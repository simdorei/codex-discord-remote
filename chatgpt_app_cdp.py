from __future__ import annotations

from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from chatgpt_app_mirror_models import (
    ChatGptConversation,
    ChatGptRole,
    ChatGptSnapshot,
    ChatGptTurn,
)


type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class CdpContractError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ChatGptCdpTarget:
    websocket_url: str


RENDERER_SNAPSHOT_FUNCTION = r"""function () {
  const conversationIdFromHref = (href) => {
    try {
      const url = new URL(href, window.location.href);
      const match = url.pathname.match(/\/c\/([A-Za-z0-9_-]+)/);
      return match ? match[1] : null;
    } catch (_) {
      return null;
    }
  };
  const quickChatContents = Array.from(document.querySelectorAll(
    '[data-quick-chat-thread-scroll-content="true"]'
  ));
  const visibleQuickChatContent = quickChatContents.find((node) => (
    !node.closest('[aria-hidden="true"], [inert]')
  ));
  const transcript = visibleQuickChatContent || quickChatContents[0] || document;
  const quickChatRoot = transcript instanceof Element
    ? transcript.closest('[data-state="open"]')
    : null;
  const appRoot = quickChatRoot || document;

  const conversationLists = Array.from(appRoot.querySelectorAll('ul')).map((list) => (
    Array.from(list.children).flatMap((item) => {
      if (!(item instanceof HTMLElement) || item.tagName !== 'LI') return [];
      const button = item.firstElementChild;
      if (!(button instanceof HTMLButtonElement) || button.type !== 'button') return [];
      const title = (button.getAttribute('aria-label') || '').trim();
      return title ? [{title}] : [];
    })
  ));
  const recentItems = (conversationLists.sort((left, right) => right.length - left.length)[0] || [])
    .slice(0, 5);
  const titleCounts = new Map();
  for (const item of recentItems) {
    titleCounts.set(item.title, (titleCounts.get(item.title) || 0) + 1);
  }
  const conversationIdFromTitle = (title) => `title:${encodeURIComponent(title)}`;
  const recentConversations = recentItems.map((item, index) => ({
    id: titleCounts.get(item.title) === 1
      ? conversationIdFromTitle(item.title)
      : `ambiguous:${index}:${conversationIdFromTitle(item.title)}`,
    title: item.title,
  }));

  const activeTitle = (
    appRoot.querySelector('header h2 button span.truncate')?.innerText || ''
  ).trim();
  const activeConversationAmbiguous = Boolean(
    activeTitle && titleCounts.get(activeTitle) > 1
  );
  const recentActive = recentConversations.find((item) => (
    item.title === activeTitle && titleCounts.get(item.title) === 1
  ));
  const activeConversationId = recentActive?.id
    || conversationIdFromHref(window.location.href);
  const turns = [];
  const turnNodes = transcript.querySelectorAll(
    '[data-chatgpt-conversation-turn="true"], [data-content-search-turn-key]'
  );
  for (const [turnIndex, turnNode] of Array.from(turnNodes).entries()) {
    const turnId = turnNode.getAttribute('data-chatgpt-conversation-turn-id')
      || turnNode.getAttribute('data-content-search-turn-key')
      || `turn-${turnIndex}`;
    const assistantComplete = Boolean(
      turnNode.querySelector('[data-assistant-message-sent-time]')
    );
    const units = turnNode.querySelectorAll('[data-content-search-unit-key]');
    for (const [unitIndex, unit] of Array.from(units).entries()) {
      const searchKey = unit.getAttribute('data-content-search-unit-key') || '';
      const role = searchKey.endsWith(':user')
        ? 'user'
        : searchKey.endsWith(':assistant') ? 'assistant' : null;
      if (!role) continue;
      const text = (unit.innerText || '').trim();
      if (!text) continue;
      turns.push({
        id: searchKey || `${turnId}:${unitIndex}:${role}`,
        role,
        text,
        complete: role === 'user' || assistantComplete,
      });
    }
  }
  const isStreaming = Boolean(
    appRoot.querySelector('[aria-busy="true"][role="status"]')
  ) || turns.some((turn) => turn.role === 'assistant' && !turn.complete);
  return {
    recentConversations,
    activeConversationId,
    activeConversationAmbiguous,
    turns,
    isStreaming,
  };
}"""


def select_chatgpt_cdp_target(targets: JsonValue) -> ChatGptCdpTarget:
    if not isinstance(targets, list):
        raise CdpContractError("CDP target list must be an array")
    rejected_external = False
    for raw_target in targets:
        if not isinstance(raw_target, dict):
            continue
        target_type = raw_target.get("type")
        title = raw_target.get("title")
        url = raw_target.get("url")
        websocket_url = raw_target.get("webSocketDebuggerUrl")
        if target_type != "page" or not isinstance(websocket_url, str):
            continue
        identity = f"{title if isinstance(title, str) else ''} {url if isinstance(url, str) else ''}".lower()
        if "codex" not in identity:
            continue
        parsed = urlsplit(websocket_url)
        if parsed.scheme not in {"ws", "wss"} or parsed.hostname not in _LOOPBACK_HOSTS:
            rejected_external = True
            continue
        return ChatGptCdpTarget(websocket_url=websocket_url)
    if rejected_external:
        raise CdpContractError("ChatGPT CDP websocket must use a loopback host")
    raise CdpContractError("Codex desktop CDP page target was not found")


def parse_renderer_snapshot(value: JsonValue) -> ChatGptSnapshot:
    if not isinstance(value, dict):
        raise CdpContractError("renderer snapshot must be an object")
    raw_recent = value.get("recentConversations")
    raw_active = value.get("activeConversationId")
    raw_turns = value.get("turns")
    is_streaming = value.get("isStreaming") is True
    if value.get("activeConversationAmbiguous") is True:
        raise CdpContractError(
            "selected ChatGPT conversation title is duplicated in the recent list"
        )
    if not isinstance(raw_recent, list) or not isinstance(raw_turns, list):
        raise CdpContractError("renderer snapshot is missing conversation or turn arrays")

    conversations: list[ChatGptConversation] = []
    seen_conversations: set[str] = set()
    for raw_conversation in raw_recent:
        if not isinstance(raw_conversation, dict):
            continue
        conversation_id = raw_conversation.get("id")
        title = raw_conversation.get("title")
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            continue
        normalized_id = conversation_id.strip()
        if normalized_id in seen_conversations:
            continue
        seen_conversations.add(normalized_id)
        conversations.append(
            ChatGptConversation(
                conversation_id=normalized_id,
                title=title.strip() if isinstance(title, str) else "",
            )
        )
        if len(conversations) == 5:
            break

    turns: list[ChatGptTurn] = []
    seen_messages: set[str] = set()
    for raw_turn in raw_turns:
        if not isinstance(raw_turn, dict):
            continue
        message_id = raw_turn.get("id")
        raw_role = raw_turn.get("role")
        text = raw_turn.get("text")
        raw_complete = raw_turn.get("complete")
        if (
            not isinstance(message_id, str)
            or not message_id.strip()
            or not isinstance(text, str)
            or not text.strip()
            or raw_role not in {ChatGptRole.USER.value, ChatGptRole.ASSISTANT.value}
        ):
            continue
        normalized_message_id = message_id.strip()
        if normalized_message_id in seen_messages:
            continue
        seen_messages.add(normalized_message_id)
        turns.append(
            ChatGptTurn(
                message_id=normalized_message_id,
                role=ChatGptRole(raw_role),
                text=text.strip(),
                complete=raw_complete is not False,
            )
        )
    if is_streaming:
        for index in range(len(turns) - 1, -1, -1):
            if turns[index].role is ChatGptRole.ASSISTANT:
                turns[index] = replace(turns[index], complete=False)
                break
    return ChatGptSnapshot(
        recent_conversations=tuple(conversations),
        active_conversation_id=raw_active.strip() if isinstance(raw_active, str) and raw_active.strip() else None,
        turns=tuple(turns),
    )
