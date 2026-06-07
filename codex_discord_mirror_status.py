"""Mirror status/list message builders for the Discord bridge."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def build_mirror_list(
    limit: int,
    *,
    visible_thread_ids: list[str] | None = None,
    db_path: Path,
    init_mirror_db_func,
    bridge_module: object,
) -> str:
    init_mirror_db_func()
    with sqlite3.connect(db_path) as conn:
        if visible_thread_ids is None:
            rows = conn.execute(
                """
                SELECT mt.thread_title, mt.codex_thread_id, mp.project_name, mt.discord_channel_id, mt.discord_thread_id
                FROM mirror_threads mt
                LEFT JOIN mirror_projects mp ON mp.project_key = mt.project_key
                ORDER BY mt.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            ordered_ids = list(dict.fromkeys(str(thread_id) for thread_id in visible_thread_ids if str(thread_id)))
            if ordered_ids:
                rows = conn.execute(
                    """
                    SELECT mt.thread_title, mt.codex_thread_id, mp.project_name, mt.discord_channel_id, mt.discord_thread_id
                    FROM mirror_threads mt
                    LEFT JOIN mirror_projects mp ON mp.project_key = mt.project_key
                    WHERE mt.codex_thread_id IN ({})
                    ORDER BY mt.updated_at DESC
                    """.format(",".join("?" for _ in ordered_ids)),
                    tuple(ordered_ids),
                ).fetchall()
                visible_order = {thread_id: index for index, thread_id in enumerate(ordered_ids)}
                rows.sort(key=lambda row: visible_order.get(str(row[1]), len(visible_order)))
            else:
                rows = []
    if not rows:
        return "No mirrored threads yet. Run `!mirror sync`."
    lines = ["Mirrored Codex threads"]
    for title, codex_thread_id, project_name, channel_id, thread_id in rows:
        context_suffix = ""
        try:
            thread = bridge_module.choose_thread(str(codex_thread_id), None)
            context_usage = bridge_module.get_thread_context_usage(thread)
            if context_usage is not None:
                status = bridge_module.describe_thread_context_usage(context_usage)
                archive_hint = " archive" if bridge_module.should_recommend_archive(thread, context_usage) else ""
                context_suffix = f" ctx={context_usage.usage_ratio * 100:.1f}%/{status}{archive_hint}"
        except Exception:
            context_suffix = ""
        lines.append(
            f"- {project_name or '-'} / {title or codex_thread_id[:8]} "
            f"=> <#{thread_id}> ({codex_thread_id[:8]}){context_suffix}"
        )
    return "\n".join(lines)


def build_mirror_check(
    *,
    threads=None,
    limit: int | None = None,
    db_path: Path,
    init_mirror_db_func,
    bridge_module: object,
    filter_mirrorable_threads_func,
    get_project_key_func,
    get_project_name_func,
) -> str:
    init_mirror_db_func()
    if threads is None:
        if limit is None:
            raise RuntimeError("Mirror check requires explicit threads or an explicit limit.")
        threads = bridge_module.load_recent_threads(limit=limit)
    threads = filter_mirrorable_threads_func(threads)
    expected: dict[str, tuple[str, str, str]] = {}
    for thread in threads:
        expected[thread.id] = (
            get_project_key_func(thread),
            get_project_name_func(thread),
            bridge_module.get_thread_ui_name(thread.id, thread) or thread.title or thread.id[:8],
        )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT codex_thread_id, project_key, thread_title, discord_channel_id, discord_thread_id
            FROM mirror_threads
            ORDER BY updated_at DESC
            """
        ).fetchall()

    missing = [item for item in expected if item not in {str(row[0]) for row in rows}]
    stale = [str(row[0]) for row in rows if str(row[0]) not in expected]
    wrong_project = [
        (str(row[0]), str(row[1]), expected[str(row[0])][0])
        for row in rows
        if str(row[0]) in expected and str(row[1]) != expected[str(row[0])][0]
    ]

    lines = [
        "Mirror check",
        f"codex_threads: {len(expected)}",
        f"mirrored_threads: {len(rows)}",
        f"missing: {len(missing)}",
        f"stale: {len(stale)}",
        f"wrong_project: {len(wrong_project)}",
    ]
    if missing:
        lines.append("")
        lines.append("Missing:")
        for thread_id in missing[:10]:
            project_key, project_name, title = expected[thread_id]
            lines.append(f"- {project_name} / {title} ({thread_id[:8]})")
    if wrong_project:
        lines.append("")
        lines.append("Wrong project:")
        for thread_id, current, expected_key in wrong_project[:10]:
            lines.append(f"- {thread_id[:8]} current={current} expected={expected_key}")
    if stale:
        lines.append("")
        lines.append("Stale:")
        for thread_id in stale[:10]:
            lines.append(f"- {thread_id[:8]}")
    if missing or wrong_project:
        lines.append("")
        lines.append("Run `!mirror sync` to repair.")
    return "\n".join(lines)
