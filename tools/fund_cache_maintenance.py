"""基金 key 型缓存的保守回收与无变化写入工具。"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from tools.configs.cache_policy_configs import INACTIVE_FUND_CACHE_RETENTION_DAYS
from tools.configs.market_benchmark_configs import (
    BENCHMARK_REPAIR_LOOKBACK_DAYS, canonical_market_benchmark_symbol,
)


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


def is_final_benchmark_return_record(record: dict, valuation_date: str) -> bool:
    """正式收益必须属于目标交易日；旧值、盘中值和非有限值均不能充数。"""
    try:
        return (
            bool(record.get("is_final"))
            and str(record.get("status", "")).lower() == "traded"
            and str(record.get("trade_date", ""))[:10] == valuation_date
            and math.isfinite(float(record["return_pct"]))
        )
    except (KeyError, TypeError, ValueError):
        return False


def repair_missing_us_index_benchmark_records(
    start_date=None,
    end_date=None,
    *,
    lookback_days=BENCHMARK_REPAIR_LOOKBACK_DAYS,
    cache_dir=None,
    symbols=None,
    allow_network=False,
    now=None,
) -> dict:
    """只补已收盘的 us_index 基准；默认零联网，每个源 CSV 每轮只读一次。

    显式 allow_network=True 时，每个有缺口的指数最多刷新一次，随后批量修复。
    不调用基金估算；沿用质量优先 writer，保护基金 records 和其它正式历史。
    """
    # 延迟导入避免与 get_top10_holdings 的缓存工具引用形成循环依赖。
    import pandas as pd
    from tools import get_top10_holdings as holdings

    cache_dir = Path(cache_dir or holdings.CACHE_DIR).resolve()
    cache_file = cache_dir / holdings.FUND_ESTIMATE_RETURN_CACHE_FILE
    end = pd.Timestamp(end_date or holdings._beijing_now(now).date()).normalize()
    start = (pd.Timestamp(start_date).normalize() if start_date else
             end - pd.Timedelta(days=max(1, int(lookback_days)) - 1))
    if start > end:
        raise ValueError("start_date must not be after end_date")
    result = {"start_date": str(start.date()), "end_date": str(end.date()),
              "repaired": [], "skipped_final": 0, "unavailable": []}
    cache = holdings._load_json_cache(str(cache_file), default={})
    records = cache.get("benchmark_records", {})
    records = records if isinstance(records, dict) else {}
    # 日历决定真实前一交易日；不能把跨越丢失日线的多日收益误写为单日收益。
    schedule = holdings._market_schedule("US", str((start - pd.Timedelta(days=14)).date()),
                                         str(end.date()))
    sessions = [pd.Timestamp(day).strftime("%Y-%m-%d") for day in schedule.index]
    previous = dict(zip(sessions[1:], sessions[:-1]))
    anchors = [day for day in sessions if str(start.date()) <= day <= str(end.date())
               and holdings._market_session_complete("US", day, now=now)]
    wanted = ({canonical_market_benchmark_symbol(s) for s in
               ([symbols] if isinstance(symbols, str) else symbols)} if symbols is not None else None)
    specs = [spec for spec in holdings._enabled_market_benchmark_specs()
             if spec["kind"] == "us_index"
             and (wanted is None or canonical_market_benchmark_symbol(spec["symbol"]) in wanted)]
    anchor_cache = None
    pending_by_date = {}

    for spec in specs:
        symbol = canonical_market_benchmark_symbol(spec["symbol"])
        # 正式别名记录也算已存在，保留原 key 和内容，不迁移或删除旧记录。
        final_dates = {str(row.get("valuation_date", ""))[:10]
                       for row in records.values() if isinstance(row, dict)
                       and canonical_market_benchmark_symbol(row.get("symbol")) == symbol
                       and is_final_benchmark_return_record(row, str(row.get("valuation_date", ""))[:10])}
        missing = [day for day in anchors if day not in final_dates]
        result["skipped_final"] += len(anchors) - len(missing)
        for day in anchors:
            if day in final_dates:
                print(f"[BENCHMARK-REPAIR] skip final {symbol} {day}", flush=True)
        if not missing:
            continue

        daily = holdings._read_us_index_benchmark_daily_cache(symbol, cache_dir=cache_dir)

        def local_returns(frame):
            prices = ({} if frame is None else
                      {date.strftime("%Y-%m-%d"): float(close)
                       for date, close in zip(frame["date"], frame["close"])})
            return {day: (prices[day] / prices[previous[day]] - 1) * 100
                    for day in missing if day in prices and previous.get(day) in prices}

        values = local_returns(daily)
        sources = {day: "local_index_cache" for day in values}
        if anchor_cache is None:
            anchor_cache = holdings._load_json_cache(
                str(cache_dir / holdings.SECURITY_RETURN_CACHE_FILE), default={})
        for day in missing:
            if day in values:
                continue
            for key, row in anchor_cache.items():
                if not isinstance(row, dict) or not str(key).startswith("SECURITY:US:"):
                    continue
                parts = str(key).split(":")
                if (len(parts) == 4 and parts[-1] == day
                        and canonical_market_benchmark_symbol(parts[2]) == symbol
                        and is_final_benchmark_return_record({**row, "is_final": True}, day)):
                    values[day] = float(row["return_pct"])
                    sources[day] = "complete_anchor_cache"
                    break

        if allow_network and any(day not in values for day in missing):
            try:
                from tools import rsi_data

                refreshed = rsi_data.get_us_index_akshare(
                    symbol=symbol,
                    days=max(len(daily) if daily is not None else 0,
                             rsi_data.NDX_MIN_INDEX_HISTORY_ROWS if symbol == ".NDX" else 15,
                             (end - start).days + 14),
                    cache_dir=str(cache_dir), retry=1, use_cache=True,
                    allow_eastmoney=False, include_realtime=False,
                )
                # 复用同一校验器，不使用未落盘或历史不足的返回值。
                if refreshed is not None:
                    refreshed_values = local_returns(
                        holdings._read_us_index_benchmark_daily_cache(symbol, cache_dir=cache_dir))
                    for day, value in refreshed_values.items():
                        if day not in values:
                            values[day] = value
                            sources[day] = "rsi_module_index_daily"
            except Exception as exc:
                print(f"[BENCHMARK-REPAIR] refresh unavailable {symbol}: {exc}", flush=True)

        for day in missing:
            if day not in values or not math.isfinite(values[day]):
                result["unavailable"].append({"symbol": symbol, "valuation_date": day})
                continue
            item = {**spec, "symbol": symbol, "trade_date": day, "return_pct": values[day],
                    "source": sources[day], "status": "traded"}
            pending_by_date.setdefault(day, []).append(item)

    for day, items in sorted(pending_by_date.items()):
        written = holdings._write_overseas_benchmark_history_cache(
            items, day, cache_file=str(cache_file), preserve_existing_records=True,
        )
        if written["written"]:
            for item in items:
                if item["symbol"] not in written["written_symbols"]:
                    continue
                result["repaired"].append({"symbol": item["symbol"], "valuation_date": day,
                                           "return_pct": item["return_pct"], "source": item["source"]})
                print(f"[BENCHMARK-REPAIR] repaired {item['symbol']} {day}", flush=True)
    if result["unavailable"]:
        print(f"[BENCHMARK-REPAIR] local data unavailable: {len(result['unavailable'])} gaps; "
              "existing records retained", flush=True)
    return result


__all__ = ["prune_inactive_fund_records", "write_json_if_changed",
           "repair_missing_us_index_benchmark_records", "is_final_benchmark_return_record"]
