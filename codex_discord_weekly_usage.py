from __future__ import annotations

from datetime import datetime, timedelta, timezone

from codex_discord_weekly_usage_format import (
    FormatPercentFunc,
    format_rate_limit_line,
    format_rate_limit_reset,
    format_window_minutes,
    parse_event_timestamp,
)
from codex_discord_weekly_usage_scan import (
    WeeklyUsageBridge,
    WeeklyUsageScanResult,
    scan_weekly_usage_events,
)

__all__ = [
    "FormatPercentFunc",
    "WeeklyUsageBridge",
    "WeeklyUsageScanResult",
    "build_weekly_usage_message",
    "format_rate_limit_line",
    "format_rate_limit_reset",
    "format_window_minutes",
    "parse_event_timestamp",
    "scan_weekly_usage_events",
]


def build_weekly_usage_message(
    days: int = 7,
    *,
    bridge_module: WeeklyUsageBridge,
    format_percent_func: FormatPercentFunc,
) -> str:
    days = max(1, min(30, days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sessions_dir = bridge_module.CODEX_HOME / "sessions"
    if not sessions_dir.exists():
        return f"Local usage estimate unavailable: sessions directory not found at {sessions_dir}"

    scan = scan_weekly_usage_events(sessions_dir, cutoff, bridge_module=bridge_module)
    lines = [f"Codex usage ({days}d local scan)"]
    if scan.latest_rate_limits:
        seen_at = (
            scan.latest_rate_limits_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            if scan.latest_rate_limits_at
            else "-"
        )
        lines.extend(
            [
                "Latest rate limits",
                f"seen_at: {seen_at}",
                f"plan: {scan.latest_rate_limits.get('plan_type') or '-'}",
                f"limit_id: {scan.latest_rate_limits.get('limit_id') or '-'}",
                format_rate_limit_line(
                    "primary",
                    scan.latest_rate_limits.get("primary"),
                    bridge_module=bridge_module,
                    format_percent_func=format_percent_func,
                ),
                format_rate_limit_line(
                    "secondary",
                    scan.latest_rate_limits.get("secondary"),
                    bridge_module=bridge_module,
                    format_percent_func=format_percent_func,
                ),
                f"credits: {scan.latest_rate_limits.get('credits') or '-'}",
                f"reached: {scan.latest_rate_limits.get('rate_limit_reached_type') or '-'}",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "Latest rate limits",
                "not found in recent local token_count events",
                "",
            ]
        )

    lines.extend(
        [
            "Local token estimate",
            f"turns: {scan.turns}",
            f"token_events: {scan.token_events}",
            f"total_tokens: {bridge_module.format_token_k(scan.total_tokens)}",
            f"input_tokens: {bridge_module.format_token_k(scan.input_tokens)}",
            f"output_tokens_est: {bridge_module.format_token_k(scan.output_tokens)}",
            f"recent_threads_seen: {len(scan.recent_threads)}",
            f"session_files_scanned: {scan.files_scanned}",
        ]
    )
    return "\n".join(lines)
