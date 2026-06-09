"""
Print a cached overseas fund estimate breakdown.

Usage:
    python fund_estimate_breakdown.py
    python fund_estimate_breakdown.py 022184 2026-05-06
    python fund_estimate_breakdown.py 022184
    python fund_estimate_breakdown.py 022184 --latest
    python fund_estimate_breakdown.py 022184 2026-05-06 --save-txt
    python fund_estimate_breakdown.py 012922 --observation 盘中

The script only reads existing cache files. It does not fetch market data,
write cache, or regenerate images.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.console_display import print_key_values, print_records_table


ROOT = Path(__file__).resolve().parent
FUND_ESTIMATE_CACHE = ROOT / "cache" / "fund_estimate_return_cache.json"
FUND_HOLDINGS_CACHE = ROOT / "cache" / "fund_holdings_cache.json"
SECURITY_RETURN_CACHE = ROOT / "cache" / "security_return_cache.json"
PREMARKET_QUOTE_CACHE = ROOT / "cache" / "premarket_quote_cache.json"
INTRADAY_QUOTE_CACHE = ROOT / "cache" / "intraday_quote_cache.json"
AFTERHOURS_QUOTE_CACHE = ROOT / "cache" / "afterhours_quote_cache.json"
FUTU_NIGHT_RETURN_CACHE = ROOT / "cache" / "futu_night_return_cache.json"
PREMARKET_REPORT = ROOT / "output" / "premarket_failed_holdings_latest.txt"
INTRADAY_REPORT = ROOT / "output" / "intraday_failed_holdings_latest.txt"
AFTERHOURS_REPORT = ROOT / "output" / "afterhours_failed_holdings_latest.txt"
NIGHT_REPORT = ROOT / "output" / "night_failed_holdings_latest.txt"

GOOD_STATUSES = {"traded", "closed"}
BAD_STATUSES = {"pending", "missing", "stale", "failed"}
OBSERVATION_GOOD_STATUSES = {"traded", "closed"}
OVERSEAS_VALID_HOLDING_BOOST = 1.15
TOP10_AVAILABLE_NORMALIZED_METHOD = "top10_available_normalized"
STOCK_TOPN_AVAILABLE_NORMALIZED_METHOD = "stock_topn_available_normalized"


@dataclass(frozen=True)
class ObservationMode:
    key: str
    label: str
    quote_cache: Path
    report_file: Path
    futu_night: bool = False


OBSERVATION_MODES = {
    "premarket": ObservationMode("premarket", "盘前", PREMARKET_QUOTE_CACHE, PREMARKET_REPORT),
    "intraday": ObservationMode("intraday", "盘中", INTRADAY_QUOTE_CACHE, INTRADAY_REPORT),
    "afterhours": ObservationMode("afterhours", "盘后", AFTERHOURS_QUOTE_CACHE, AFTERHOURS_REPORT),
    "night": ObservationMode("night", "富途夜盘", FUTU_NIGHT_RETURN_CACHE, NIGHT_REPORT, futu_night=True),
}

MODE_ALIASES = {
    "盘前": "premarket",
    "pre": "premarket",
    "premarket": "premarket",
    "盘中": "intraday",
    "盘中实时": "intraday",
    "intraday": "intraday",
    "realtime": "intraday",
    "盘后": "afterhours",
    "afterhours": "afterhours",
    "post": "afterhours",
    "夜盘": "night",
    "富途夜盘": "night",
    "futu": "night",
    "futu_night": "night",
    "night": "night",
}


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def relative_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def safe_float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip().replace("%", "").replace(",", "")
            if not value:
                return None
        out = float(value)
        if not math.isfinite(out):
            return None
        return out
    except Exception:
        return None


def is_top10_available_normalized_method(value: Any) -> bool:
    """判断缓存记录是否使用“前十大可用持仓归一化到 100%”口径。"""
    text = str(value or "").strip().lower()
    if not text:
        return False
    return (
        TOP10_AVAILABLE_NORMALIZED_METHOD in text
        or text.startswith(STOCK_TOPN_AVAILABLE_NORMALIZED_METHOD)
    )


def normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:10]


def normalize_observation_mode(value: Any) -> ObservationMode | None:
    text = str(value or "").strip()
    if not text:
        return None
    key = MODE_ALIASES.get(text) or MODE_ALIASES.get(text.lower())
    if key is None:
        return None
    return OBSERVATION_MODES[key]


def normalize_fund_code(value: Any) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else text


def normalize_ticker(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip().upper()
    market = str(market or "").strip().upper()
    if market in {"CN", "KR"}:
        digits = "".join(ch for ch in text if ch.isdigit())
        return digits.zfill(6) if digits else text
    if market == "HK":
        text = text.replace(".HK", "").replace("HK", "")
        digits = "".join(ch for ch in text if ch.isdigit())
        return digits[-5:].zfill(5) if digits else text
    return text


def fmt_pct(value: Any, digits: int = 2, signed: bool = False) -> str:
    number = safe_float(value)
    sign = "+" if signed and number >= 0 else ""
    return f"{sign}{number:.{digits}f}%"


def fmt_optional_pct(value: Any, digits: int = 4, signed: bool = True) -> str:
    number = safe_float_or_none(value)
    if number is None:
        return ""
    sign = "+" if signed and number >= 0 else ""
    return f"{sign}{number:.{digits}f}%"


def find_record(records: dict[str, Any], fund_code: str, anchor_date: str) -> dict[str, Any]:
    key = f"overseas:{fund_code}:{anchor_date}"
    record = records.get(key)
    if isinstance(record, dict):
        return record

    matches = []
    for item in records.values():
        if not isinstance(item, dict):
            continue
        if normalize_fund_code(item.get("fund_code")) != fund_code:
            continue
        item_date = normalize_date(item.get("valuation_anchor_date") or item.get("valuation_date"))
        matches.append(item_date)

    available = ", ".join(sorted(set(matches))[-10:])
    suffix = f"；可用估值日: {available}" if available else ""
    raise SystemExit(f"未找到缓存记录: overseas:{fund_code}:{anchor_date}{suffix}")


def available_records(records: dict[str, Any], fund_code: str) -> list[dict[str, Any]]:
    items = []
    for item in records.values():
        if not isinstance(item, dict):
            continue
        if normalize_fund_code(item.get("fund_code")) != fund_code:
            continue
        anchor_date = normalize_date(item.get("valuation_anchor_date") or item.get("valuation_date"))
        if not anchor_date:
            continue
        items.append(item)
    return sorted(items, key=lambda x: (normalize_date(x.get("valuation_anchor_date") or x.get("valuation_date")), str(x.get("run_time_bj") or "")))


def print_available_dates(fund_code: str) -> None:
    fund_cache = load_json(FUND_ESTIMATE_CACHE, {})
    records = fund_cache.get("records", {}) if isinstance(fund_cache, dict) else {}
    items = available_records(records, fund_code)

    if not items:
        raise SystemExit(f"未找到基金 {fund_code} 的海外估算缓存。请先运行 main.py。")

    fund_name = items[-1].get("fund_name", "")
    print_key_values("基金缓存记录", [("基金代码", fund_code), ("基金名称", fund_name)])
    rows = []
    for item in items:
        anchor_date = normalize_date(item.get("valuation_anchor_date") or item.get("valuation_date"))
        rows.append(
            {
                "估值日期": anchor_date,
                "表格显示": fmt_pct(item.get("estimate_return_pct"), signed=True),
                "数据状态": item.get("data_status") or item.get("stage", ""),
                "is_final": item.get("is_final"),
                "运行时间": item.get("run_time_bj", ""),
            }
        )
    print_records_table(rows, title="可用估值日期")
    print()
    print(f"查看某日完整表: python fund_estimate_breakdown.py {fund_code} <估值日期>")
    print(f"查看最新完整表: python fund_estimate_breakdown.py {fund_code} --latest")


def latest_anchor_date_for_fund(fund_code: str) -> str:
    fund_cache = load_json(FUND_ESTIMATE_CACHE, {})
    records = fund_cache.get("records", {}) if isinstance(fund_cache, dict) else {}
    items = available_records(records, fund_code)
    if not items:
        raise SystemExit(f"未找到基金 {fund_code} 的海外估算缓存。请先运行 main.py。")
    return normalize_date(items[-1].get("valuation_anchor_date") or items[-1].get("valuation_date"))


def load_holdings(fund_code: str) -> list[dict[str, Any]]:
    cache = load_json(FUND_HOLDINGS_CACHE, {})
    item = cache.get(f"{fund_code}:top10")
    if not isinstance(item, dict):
        raise SystemExit(f"未找到持仓缓存: {fund_code}:top10。请先运行 main.py 生成持仓缓存。")

    raw = item.get("data_json", "[]")
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, list) or not data:
        raise SystemExit(f"持仓缓存为空: {fund_code}:top10")
    return [x for x in data if isinstance(x, dict)]


def load_holdings_record(fund_code: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cache = load_json(FUND_HOLDINGS_CACHE, {})
    item = cache.get(f"{fund_code}:top10")
    if not isinstance(item, dict):
        raise SystemExit(f"未找到持仓缓存: {fund_code}:top10。请先运行 main.py 或对应观察脚本。")

    raw = item.get("data_json", "[]")
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, list) or not data:
        raise SystemExit(f"持仓缓存为空: {fund_code}:top10")
    return item, [x for x in data if isinstance(x, dict)]


def get_security_record(cache: dict[str, Any], market: str, ticker: Any, anchor_date: str) -> dict[str, Any]:
    ticker_norm = normalize_ticker(market, ticker)
    key = f"SECURITY:{str(market).strip().upper()}:{ticker_norm}:{anchor_date}"
    value = cache.get(key)
    return value if isinstance(value, dict) else {}


def holding_name(row: dict[str, Any]) -> str:
    name = str(row.get("股票名称") or row.get("name") or "").strip()
    code = str(row.get("股票代码") or row.get("ticker") or "").strip()
    return f"{name} {code}".strip()


def build_rows(
    holdings: list[dict[str, Any]],
    security_cache: dict[str, Any],
    anchor_date: str,
    boost: float,
    top10_available_normalized: bool = False,
) -> tuple[list[dict[str, Any]], float, float, float]:
    rows = []
    raw_sum = 0.0
    valid_raw_sum = 0.0

    for item in holdings:
        market = str(item.get("市场") or item.get("market") or "").strip().upper()
        ticker = item.get("ticker") or item.get("股票代码")
        raw_weight = safe_float(item.get("占净值比例"))
        raw_sum += raw_weight

        sec = get_security_record(security_cache, market, ticker, anchor_date)
        status = str(sec.get("status") or "missing").strip().lower()
        trade_date = normalize_date(sec.get("trade_date"))
        return_pct = safe_float(sec.get("return_pct"))
        source = str(sec.get("source") or "").strip()
        error = str(sec.get("error") or "").strip()

        is_resolved = status in GOOD_STATUSES
        if is_resolved:
            valid_raw_sum += raw_weight

        rows.append({
            "name": holding_name(item),
            "market": market,
            "ticker": normalize_ticker(market, ticker),
            "raw_weight": raw_weight,
            "status": status,
            "trade_date": trade_date,
            "return_pct": return_pct if is_resolved else 0.0,
            "effective_weight": 0.0,
            "contribution": 0.0,
            "source": source,
            "error": error,
        })

    holding_contribution_sum = 0.0
    for row in rows:
        is_resolved = row["status"] in GOOD_STATUSES
        if not is_resolved:
            continue

        if top10_available_normalized:
            effective_weight = row["raw_weight"] / valid_raw_sum * 100.0 if valid_raw_sum > 0 else 0.0
        else:
            effective_weight = row["raw_weight"] * boost

        contribution = effective_weight * row["return_pct"] / 100.0
        row["effective_weight"] = effective_weight
        row["contribution"] = contribution
        holding_contribution_sum += contribution

    return rows, raw_sum, valid_raw_sum, holding_contribution_sum


def print_breakdown(fund_code: str, anchor_date: str) -> None:
    fund_cache = load_json(FUND_ESTIMATE_CACHE, {})
    records = fund_cache.get("records", {}) if isinstance(fund_cache, dict) else {}
    security_cache = load_json(SECURITY_RETURN_CACHE, {})

    record = find_record(records, fund_code, anchor_date)
    holdings = load_holdings(fund_code)

    fund_name = record.get("fund_name", "")
    method = str(record.get("method") or record.get("estimation_method") or "").strip()
    top10_available_normalized = is_top10_available_normalized_method(method)
    boost = safe_float(record.get("holding_boost"), 1.0)
    residual_weight = safe_float(
        record.get("residual_benchmark_weight_pct", record.get("residual_weight_pct"))
    )
    residual_return = safe_float(record.get("residual_benchmark_return_pct"))
    residual_status = str(record.get("residual_benchmark_status") or "").strip().lower() or "missing"
    residual_trade_date = normalize_date(record.get("residual_benchmark_trade_date"))
    residual_label = str(record.get("residual_benchmark_label") or "补偿基准").strip()
    residual_market = str(record.get("residual_benchmark_market") or "US").strip().upper()
    residual_ticker = normalize_ticker(
        residual_market,
        record.get("residual_benchmark_ticker") or ".NDX",
    )

    rows, raw_sum_calc, valid_sum_calc, holding_contribution_sum = build_rows(
        holdings=holdings,
        security_cache=security_cache,
        anchor_date=anchor_date,
        boost=boost,
        top10_available_normalized=top10_available_normalized,
    )

    raw_sum = safe_float(record.get("raw_holding_weight_sum_pct"), raw_sum_calc)
    valid_sum = safe_float(record.get("valid_holding_weight_pct"), valid_sum_calc)
    failed_sum = safe_float(record.get("failed_raw_weight_sum_pct"), max(0.0, raw_sum - valid_sum))
    boosted_valid = (
        sum(safe_float(row.get("effective_weight")) for row in rows if row["status"] in GOOD_STATUSES)
        if top10_available_normalized
        else safe_float(record.get("boosted_valid_holding_weight_pct"), valid_sum * boost)
    )
    if top10_available_normalized:
        residual_contribution = 0.0
    elif residual_status in GOOD_STATUSES:
        residual_contribution = residual_weight * residual_return / 100.0
    else:
        residual_contribution = 0.0
    total_calc = holding_contribution_sum + residual_contribution
    total_cache = safe_float(record.get("estimate_return_pct"))

    info_items = [
        ("基金代码", fund_code),
        ("基金名称", fund_name),
        ("估值锚点", anchor_date),
        ("运行时间", record.get("run_time_bj", "")),
        ("数据状态", f"{record.get('data_status') or record.get('stage', '')}, is_final={record.get('is_final')}"),
        ("估算口径", "前十大可用持仓归一化" if top10_available_normalized else "有效持仓增强 + 补偿基准"),
        ("前十大披露持仓合计", fmt_pct(raw_sum)),
        ("行情有效持仓", fmt_pct(valid_sum)),
        ("行情失败持仓", fmt_pct(failed_sum)),
    ]
    if top10_available_normalized:
        info_items.extend(
            [
                ("参与归一化持仓", fmt_pct(valid_sum)),
                ("归一化后估算权重", fmt_pct(boosted_valid)),
            ]
        )
    else:
        info_items.extend(
            [
                ("有效持仓放大系数", f"{boost:.2f}"),
                ("放大后有效持仓权重", f"{fmt_pct(valid_sum)} * {boost:.2f} = {fmt_pct(boosted_valid)}"),
                ("补偿仓位", f"100% - {fmt_pct(boosted_valid)} = {fmt_pct(residual_weight)}"),
                (
                    "补偿基准",
                    f"{residual_label}，交易日 {residual_trade_date or '-'}，状态 {residual_status}，涨幅 {fmt_pct(residual_return, digits=4, signed=True)}",
                ),
            ]
        )
    print_key_values("基金估算拆解", info_items)

    table_rows = []
    for row in rows:
        table_rows.append(
            {
                "持仓": row["name"],
                "市场": row["market"],
                "代码": row["ticker"],
                "原始权重": fmt_pct(row["raw_weight"]),
                "状态": row["status"],
                "行情交易日": row["trade_date"] or "-",
                "股票自身涨跌幅": fmt_pct(row["return_pct"], digits=4, signed=True),
                "估算权重": fmt_pct(row["effective_weight"], digits=3),
                "对基金贡献": fmt_pct(row["contribution"], digits=4, signed=True),
                "数据源": row["source"] or "-",
            }
        )

    if not top10_available_normalized:
        table_rows.append(
            {
                "持仓": f"{residual_label}补偿仓位",
                "市场": residual_market,
                "代码": residual_ticker,
                "原始权重": fmt_pct(residual_weight),
                "状态": residual_status,
                "行情交易日": residual_trade_date or "-",
                "股票自身涨跌幅": fmt_pct(residual_return, digits=4, signed=True),
                "估算权重": fmt_pct(residual_weight, digits=3),
                "对基金贡献": fmt_pct(residual_contribution, digits=4, signed=True),
                "数据源": "residual_benchmark",
            }
        )
    print_records_table(table_rows, title="逐项贡献")

    total_items = [("持仓贡献合计", fmt_pct(holding_contribution_sum, digits=6, signed=True))]
    if not top10_available_normalized:
        total_items.append(("补偿仓位贡献", fmt_pct(residual_contribution, digits=6, signed=True)))
    total_items.extend(
        [
            ("复算合计", fmt_pct(total_calc, digits=6, signed=True)),
            ("缓存估算值", fmt_pct(total_cache, digits=6, signed=True)),
            ("表格显示", fmt_pct(total_cache, digits=2, signed=True)),
        ]
    )
    print_key_values("合计", total_items)

    failed_rows = [row for row in rows if row["status"] in BAD_STATUSES]
    if failed_rows:
        print_records_table(
            [
                {
                    "持仓": row["name"],
                    "市场": row["market"],
                    "代码": row["ticker"],
                    "状态": row["status"],
                    "错误": row["error"] or row["source"] or "无错误详情",
                }
                for row in failed_rows
            ],
            title="失败/未完成持仓",
        )


def parse_observation_report(report_file: Path) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    if not report_file.exists():
        return {}, {}
    lines = report_file.read_text(encoding="utf-8", errors="replace").splitlines()
    meta: dict[str, str] = {}
    summaries: dict[str, dict[str, str]] = {}
    headers: list[str] = []
    in_summary = False
    for line in lines:
        if line.strip() == "基金汇总":
            in_summary = True
            continue
        if not in_summary:
            if ":" in line and "\t" not in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
            continue
        if not headers:
            if line.startswith("fund_code"):
                headers = line.split("\t")
            continue
        if not line.strip() or not line[0].isdigit():
            break
        values = line.split("\t")
        row = dict(zip(headers, values))
        code = row.get("fund_code", "")
        if code:
            summaries[code] = row
    return meta, summaries


def observation_quote_for_holding(
    quote_cache: dict[str, Any],
    *,
    mode: ObservationMode,
    market: str,
    ticker: str,
    valuation_date: str,
) -> dict[str, Any]:
    if mode.futu_night:
        keys = [f"{market}:{ticker}:{valuation_date}", f"{market}:{ticker}"]
    else:
        keys = [f"{market}:{ticker}"]

    for key in keys:
        item = quote_cache.get(key)
        if isinstance(item, dict):
            return item

    if mode.key == "afterhours" and market in {"CN", "HK", "KR"}:
        return {
            "return_pct": 0.0,
            "source": "afterhours_non_us_zero",
            "status": "zeroed",
            "trade_date": valuation_date,
            "quote_time_bj": "",
            "fetched_at_bj": "",
            "error": "盘后非美持仓置零，不计入有效持仓权重",
        }

    return {
        "return_pct": None,
        "source": "cache_missing",
        "status": "missing",
        "trade_date": "",
        "quote_time_bj": "",
        "fetched_at_bj": "",
        "error": f"短缓存未找到 {market}:{ticker}",
    }


def observation_residual_quote(
    quote_cache: dict[str, Any],
    *,
    mode: ObservationMode,
    summary: dict[str, str],
    valuation_date: str,
) -> dict[str, Any]:
    ticker = str(summary.get("residual_ticker") or "QQQ").strip().upper()
    keys = [f"US:{ticker}:{valuation_date}", f"US:{ticker}"] if mode.futu_night else [f"US:{ticker}"]
    for key in keys:
        item = quote_cache.get(key)
        if isinstance(item, dict):
            return item
    return {
        "return_pct": safe_float_or_none(summary.get("residual_return_pct")),
        "source": "report_summary",
        "status": "summary",
        "trade_date": valuation_date,
        "quote_time_bj": "",
        "fetched_at_bj": "",
        "error": "",
    }


def print_observation_breakdown(fund_code: str, mode: ObservationMode) -> None:
    holdings_record, holdings = load_holdings_record(fund_code)
    quote_cache = load_json(mode.quote_cache, {})
    meta, summaries = parse_observation_report(mode.report_file)
    summary = summaries.get(fund_code, {})
    valuation_date = str(meta.get("valuation_date") or "").strip()

    if mode.futu_night and not quote_cache:
        print(f"提示：未找到富途夜盘短缓存 {relative_path(mode.quote_cache)}，请先运行 futu_night_fund.py。")

    info_items = [
        ("基金代码", fund_code),
        ("基金", summary.get("fund_name") or fund_code),
        ("观察类型", mode.label),
        ("报告文件", relative_path(mode.report_file)),
        ("短缓存", relative_path(mode.quote_cache)),
        ("报告生成时间", meta.get("generated_at_bj", "")),
        ("估值/目标日期", valuation_date),
    ]
    if meta.get("afterhours_quote_date"):
        info_items.append(("盘后报价日", meta.get("afterhours_quote_date")))
    info_items.append(
        (
            "持仓缓存",
            f"{holdings_record.get('latest_quarter_label', '')}，"
            f"抓取时间 {holdings_record.get('fetched_at', '')}，"
            f"确认={holdings_record.get('target_quarter_confirmed', '')}",
        )
    )

    if summary:
        info_items.append(
            (
                "汇总",
                (
            f"持仓贡献 {fmt_optional_pct(summary.get('known_contribution_pct'))}，"
            f"剩余仓位贡献 {fmt_optional_pct(summary.get('residual_contribution_pct'))}，"
            f"{mode.label}估算 {fmt_optional_pct(summary.get('estimate_return_pct'))}"
                ),
            )
        )
    else:
        info_items.append(("汇总", "最新报告中没有找到该基金，将仅按短缓存复算。"))
    print_key_values("实时观察拆解", info_items)

    row_items: list[dict[str, Any]] = []
    valid_pairs: list[tuple[float, float]] = []
    for holding in holdings:
        market = str(holding.get("市场") or "").strip().upper()
        ticker = normalize_ticker(market, holding.get("ticker") or holding.get("股票代码"))
        raw_weight = safe_float(holding.get("占净值比例"))
        quote = observation_quote_for_holding(
            quote_cache,
            mode=mode,
            market=market,
            ticker=ticker,
            valuation_date=valuation_date,
        )
        return_pct = safe_float_or_none(quote.get("return_pct"))
        status = str(quote.get("status") or "missing").strip().lower()
        is_valid = status in OBSERVATION_GOOD_STATUSES and return_pct is not None and raw_weight > 0
        if is_valid:
            valid_pairs.append((raw_weight, return_pct))
        row_items.append({
            "holding": holding,
            "market": market,
            "ticker": ticker,
            "raw_weight": raw_weight,
            "quote": quote,
            "return_pct": return_pct,
            "status": status,
            "is_valid": is_valid,
        })

    raw_valid_weight = sum(weight for weight, _ in valid_pairs)
    boosted_valid_weight = min(100.0, raw_valid_weight * OVERSEAS_VALID_HOLDING_BOOST) if raw_valid_weight > 0 else 0.0
    actual_boost = boosted_valid_weight / raw_valid_weight if raw_valid_weight > 0 else 0.0

    known_contribution = 0.0
    for item in row_items:
        if item["is_valid"]:
            boosted_weight = item["raw_weight"] * actual_boost
            contribution = boosted_weight * item["return_pct"] / 100.0
            known_contribution += contribution
        elif item["status"] == "zeroed":
            boosted_weight = 0.0
            contribution = 0.0
        else:
            boosted_weight = None
            contribution = None
        item["boosted_weight"] = boosted_weight
        item["contribution"] = contribution

    residual = observation_residual_quote(
        quote_cache,
        mode=mode,
        summary=summary,
        valuation_date=valuation_date,
    )
    residual_return = safe_float_or_none(residual.get("return_pct"))
    residual_weight = max(0.0, 100.0 - boosted_valid_weight)
    residual_contribution = residual_weight * residual_return / 100.0 if residual_return is not None else 0.0
    estimate = known_contribution + residual_contribution

    table_rows = []
    for item in row_items:
        holding = item["holding"]
        quote = item["quote"]
        name = str(holding.get("股票名称") or item["ticker"]).strip()
        label = f"{item['ticker']} {name}".strip()
        table_rows.append(
            {
                "持仓": label,
                "市场": item["market"],
                "原始权重": fmt_optional_pct(item["raw_weight"], digits=2, signed=False),
                "增强后权重": fmt_optional_pct(item.get("boosted_weight"), digits=4, signed=False),
                "涨跌幅": fmt_optional_pct(item.get("return_pct"), digits=4, signed=True),
                "贡献": fmt_optional_pct(item.get("contribution"), digits=4, signed=True),
                "交易日": str(quote.get("trade_date", "")),
                "报价时间": str(quote.get("quote_time_bj", "")),
                "缓存时间": str(quote.get("fetched_at_bj", "")),
                "状态": str(quote.get("status", "")),
                "来源": str(quote.get("source", "")),
                "错误": str(quote.get("error", "")),
            }
        )
    print_records_table(table_rows, title="持仓明细")

    residual_ticker = str(summary.get("residual_ticker") or "QQQ").strip().upper()
    print_key_values(
        "合计",
        [
            (
                "计算过程",
                f"{fund_code}：原始有效权重 {fmt_optional_pct(raw_valid_weight, digits=2, signed=False)}，"
                f"增强后 {fmt_optional_pct(boosted_valid_weight, digits=4, signed=False)}，"
                f"剩余 {fmt_optional_pct(residual_weight, digits=4, signed=False)} × "
                f"{residual_ticker} {fmt_optional_pct(residual_return, digits=4, signed=True)} = "
                f"{fmt_optional_pct(residual_contribution, digits=4, signed=True)}，"
                f"最终 {fmt_optional_pct(known_contribution, digits=4, signed=True)} "
                f"{'+' if residual_contribution >= 0 else '-'} "
                f"{fmt_optional_pct(abs(residual_contribution), digits=4, signed=False)} = "
                f"{fmt_optional_pct(estimate, digits=4, signed=True)}。",
            ),
            (
                "补偿基准",
                f"{residual_ticker}，trade_date={residual.get('trade_date', '')}，"
                f"quote_time_bj={residual.get('quote_time_bj', '')}，"
                f"fetched_at_bj={residual.get('fetched_at_bj', '')}，"
                f"source={residual.get('source', '')}",
            ),
        ],
    )

    report_estimate = safe_float_or_none(summary.get("estimate_return_pct"))
    if report_estimate is not None and abs(report_estimate - estimate) > 0.005:
        print(
            "提示：复算值与报告汇总略有差异，可能是短缓存已被后续运行覆盖，"
            f"报告={fmt_optional_pct(report_estimate)}，复算={fmt_optional_pct(estimate)}。"
        )


def print_or_save_breakdown(fund_code: str, anchor_date: str, save_txt=None) -> None:
    if save_txt:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            print_breakdown(fund_code=fund_code, anchor_date=anchor_date)
        text = buffer.getvalue()
        print(text, end="")

        if save_txt == "auto":
            out_path = ROOT / "output" / f"fund_estimate_breakdown_{fund_code}_{anchor_date}.txt"
        else:
            out_path = Path(save_txt)
            if not out_path.is_absolute():
                out_path = ROOT / out_path

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"\n完整表已保存: {out_path}")
    else:
        print_breakdown(fund_code=fund_code, anchor_date=anchor_date)


def interactive_main() -> None:
    print("海外/全球基金收益预估完整拆解")
    print("说明：本工具只读取缓存，不联网、不改缓存。")
    print()

    fund_code = normalize_fund_code(input("请输入基金代码，例如 022184：").strip())
    if not fund_code:
        raise SystemExit("基金代码不能为空。")

    mode_text = input(
        "请输入查询类型：正式/盘前/盘中/盘中实时/盘后/夜盘；留空=正式："
    ).strip()
    observation_mode = normalize_observation_mode(mode_text)
    if observation_mode is not None:
        print()
        print_observation_breakdown(fund_code=fund_code, mode=observation_mode)
        return

    if mode_text and mode_text not in {"正式", "日线", "完整日线", "main"}:
        raise SystemExit(f"不支持的查询类型: {mode_text}")

    date_text = input("请输入估值日期，例如 2026-05-06；留空=列出可用估值日期；输入 latest=查看最新：").strip()

    if not date_text:
        print()
        print_available_dates(fund_code)
        return

    if date_text.lower() in {"latest", "newest", "最新"}:
        anchor_date = latest_anchor_date_for_fund(fund_code)
    else:
        anchor_date = normalize_date(date_text)

    if not anchor_date:
        raise SystemExit("估值日期不能为空。")

    save_text = input("是否同时保存为 txt？输入 y 保存，直接回车不保存：").strip().lower()
    save_txt = "auto" if save_text in {"y", "yes", "是", "保存"} else None
    print()
    print_or_save_breakdown(fund_code=fund_code, anchor_date=anchor_date, save_txt=save_txt)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="打印某只海外/全球基金在正式锚点或实时观察中的收益预估拆解。"
    )
    parser.add_argument("fund_code", nargs="?", help="基金代码，例如 022184")
    parser.add_argument("valuation_anchor_date", nargs="?", help="估值锚点日期，或观察类型：盘前/盘中/盘后/夜盘")
    parser.add_argument("--observation", "--mode", default=None, help="实时观察类型：盘前、盘中、盘中实时、盘后、夜盘")
    parser.add_argument("--latest", action="store_true", help="使用该基金缓存中的最新估值日期")
    parser.add_argument("--list-dates", action="store_true", help="列出该基金缓存中已有的估值日期")
    parser.add_argument(
        "--save-txt",
        nargs="?",
        const="auto",
        default=None,
        help="把完整表保存为 txt；可选指定路径，不指定则保存到 output/fund_estimate_breakdown_<基金代码>_<估值日期>.txt",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.fund_code:
        interactive_main()
        return

    fund_code = normalize_fund_code(args.fund_code)
    observation_mode = normalize_observation_mode(args.observation)
    positional_mode = normalize_observation_mode(args.valuation_anchor_date)
    if observation_mode is not None or positional_mode is not None:
        print_observation_breakdown(fund_code=fund_code, mode=observation_mode or positional_mode)
        return

    if args.list_dates or (not args.valuation_anchor_date and not args.latest):
        print_available_dates(fund_code)
        return

    anchor_date = latest_anchor_date_for_fund(fund_code) if args.latest else normalize_date(args.valuation_anchor_date)
    if not anchor_date:
        raise SystemExit("请提供估值日期，例如 2026-05-06，或使用 --latest。")

    print_or_save_breakdown(fund_code=fund_code, anchor_date=anchor_date, save_txt=args.save_txt)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        interactive_main()
    else:
        main()
