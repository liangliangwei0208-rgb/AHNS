"""
运行期行情统计。

只保存在当前 Python 进程内，用于本轮运行结束后的控制台摘要和
output/failed_holdings_latest.txt 排查报告；不写 JSON，不影响缓存口径。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from time import perf_counter


_MARKET_EVENTS: list[dict] = []


def record_market_event(
    *,
    action: str,
    source: str,
    market: str = "",
    ticker: str = "",
    outcome: str = "ok",
    cache_hit: bool = False,
    duration_seconds: float | None = None,
    status: str = "",
    error: str = "",
) -> None:
    """记录一个轻量行情事件。异常不向外传播，避免统计影响主流程。"""
    try:
        _MARKET_EVENTS.append(
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "action": str(action or "").strip(),
                "source": str(source or "").strip(),
                "market": str(market or "").strip().upper(),
                "ticker": str(ticker or "").strip().upper(),
                "outcome": str(outcome or "").strip().lower(),
                "cache_hit": bool(cache_hit),
                "duration_seconds": None if duration_seconds is None else float(duration_seconds),
                "status": str(status or "").strip().lower(),
                "error": str(error or "").strip().replace("\n", " ")[:500],
            }
        )
    except Exception:
        return


def timed_market_call(
    callback,
    *,
    action: str,
    source: str,
    market: str = "",
    ticker: str = "",
):
    """执行 callback 并记录成功/失败耗时；失败时原样抛出。"""
    start = perf_counter()
    try:
        result = callback()
        record_market_event(
            action=action,
            source=source,
            market=market,
            ticker=ticker,
            outcome="success",
            cache_hit=False,
            duration_seconds=perf_counter() - start,
        )
        return result
    except Exception as exc:
        record_market_event(
            action=action,
            source=source,
            market=market,
            ticker=ticker,
            outcome="failed",
            cache_hit=False,
            duration_seconds=perf_counter() - start,
            error=repr(exc),
        )
        raise


def snapshot_market_events() -> list[dict]:
    return [dict(item) for item in _MARKET_EVENTS]


def summarize_market_events(events: list[dict] | None = None) -> dict:
    events = snapshot_market_events() if events is None else [dict(item) for item in events]
    cache_hits = sum(1 for item in events if item.get("cache_hit"))
    network_actions = {"daily_source_fetch", "rsi_network_fetch", "calendar_network_fetch", "vix_daily_network_fetch"}
    network_attempts = sum(
        1 for item in events
        if not item.get("cache_hit") and str(item.get("action", "")).strip() in network_actions
    )
    failures = [
        item for item in events
        if str(item.get("outcome", "")).lower() in {"failed", "error"}
    ]
    by_source = Counter(str(item.get("source", "") or "unknown") for item in events)
    by_action = Counter(str(item.get("action", "") or "unknown") for item in events)
    slowest = sorted(
        [item for item in events if item.get("duration_seconds") is not None],
        key=lambda item: float(item.get("duration_seconds") or 0),
        reverse=True,
    )[:8]
    return {
        "event_count": len(events),
        "cache_hits": cache_hits,
        "network_attempts": network_attempts,
        "failures": failures,
        "failure_count": len(failures),
        "by_source": dict(by_source),
        "by_action": dict(by_action),
        "slowest": slowest,
    }


def format_market_stats_lines(events: list[dict] | None = None) -> list[str]:
    summary = summarize_market_events(events)
    lines = [
        "行情请求统计",
        f"event_count: {summary['event_count']}",
        f"cache_hits: {summary['cache_hits']}",
        f"network_attempts: {summary['network_attempts']}",
        f"failure_count: {summary['failure_count']}",
        "",
        "by_action:",
    ]
    for key, value in sorted(summary["by_action"].items()):
        lines.append(f"- {key}: {value}")

    lines.append("")
    lines.append("by_source:")
    for key, value in sorted(summary["by_source"].items()):
        lines.append(f"- {key}: {value}")

    lines.append("")
    lines.append("slowest_events:")
    if summary["slowest"]:
        headers = ["action", "source", "market", "ticker", "duration_seconds", "outcome", "error"]
        lines.append("\t".join(headers))
        for item in summary["slowest"]:
            row = dict(item)
            row["duration_seconds"] = f"{float(row.get('duration_seconds') or 0):.3f}"
            lines.append("\t".join(str(row.get(header, "") or "") for header in headers))
    else:
        lines.append("无")

    if summary["failures"]:
        lines.append("")
        lines.append("failed_events:")
        headers = ["action", "source", "market", "ticker", "duration_seconds", "error"]
        lines.append("\t".join(headers))
        for item in summary["failures"][:20]:
            row = dict(item)
            row["duration_seconds"] = "" if row.get("duration_seconds") is None else f"{float(row.get('duration_seconds') or 0):.3f}"
            lines.append("\t".join(str(row.get(header, "") or "") for header in headers))

    return lines


__all__ = [
    "record_market_event",
    "timed_market_call",
    "snapshot_market_events",
    "summarize_market_events",
    "format_market_stats_lines",
]
