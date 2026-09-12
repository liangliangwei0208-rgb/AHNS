"""基金 key 型缓存的保守回收与无变化写入工具。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from tools.configs.cache_policy_configs import INACTIVE_FUND_CACHE_RETENTION_DAYS


_FUND_CODE_PATTERN = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_TIMESTAMP_FIELDS = (
    "last_checked_at",
    "last_generated_at",
    "last_updated_at",
    "generated_at",
    "fetched_at",
    "updated_at",
)


def write_json_if_changed(path: str | Path, data: Any) -> bool:
    """仅在 JSON 内容变化时原子写入，避免检查时间制造同步噪声。"""
    target = Path(path)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    try:
        if target.exists() and target.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        # 无法读取旧文件时仍尝试写入，让调用方保留原有的失败语义。
        pass

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_suffix(target.suffix + ".tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(target)
    return True


def _normalize_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


def _fund_code_from_key(key: Any, record: Any) -> str:
    if isinstance(record, dict):
        from_record = str(record.get("fund_code") or "").strip()
        if re.fullmatch(r"\d{6}", from_record):
            return from_record
    matched = _FUND_CODE_PATTERN.search(str(key or ""))
    return matched.group(1) if matched else ""


def _latest_record_time(record: Any) -> datetime | None:
    if not isinstance(record, dict):
        return None
    timestamps = [_normalize_datetime(record.get(field)) for field in _TIMESTAMP_FIELDS]
    usable = [item for item in timestamps if item is not None]
    return max(usable) if usable else None


def prune_inactive_fund_records(
    records: dict[str, Any],
    *,
    active_fund_codes: Iterable[Any],
    now: datetime | None = None,
    retention_days: int = INACTIVE_FUND_CACHE_RETENTION_DAYS,
) -> tuple[dict[str, Any], list[str]]:
    """回收基金池外且明确超期的 key；时间无法判断时宁可保留。"""
    active_codes = {str(code).strip().zfill(6) for code in active_fund_codes}
    check_now = now or datetime.now()
    if check_now.tzinfo is not None:
        check_now = check_now.astimezone().replace(tzinfo=None)
    cutoff = check_now - timedelta(days=max(1, int(retention_days)))

    kept: dict[str, Any] = {}
    removed: list[str] = []
    for key, record in records.items():
        fund_code = _fund_code_from_key(key, record)
        record_time = _latest_record_time(record)
        if fund_code and fund_code not in active_codes and record_time is not None and record_time < cutoff:
            removed.append(str(key))
            continue
        kept[str(key)] = record
    return kept, sorted(removed)


__all__ = ["prune_inactive_fund_records", "write_json_if_changed"]
