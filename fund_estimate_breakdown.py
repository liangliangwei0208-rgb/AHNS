"""
Print a cached overseas fund estimate breakdown.

Usage:
    python fund_estimate_breakdown.py
    python fund_estimate_breakdown.py 022184 2026-05-06
    python fund_estimate_breakdown.py 022184
    python fund_estimate_breakdown.py 022184 --latest
    python fund_estimate_breakdown.py 022184 2026-05-06 --save-txt

The script only reads existing cache files. It does not fetch market data,
write cache, or regenerate images.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
FUND_ESTIMATE_CACHE = ROOT / "cache" / "fund_estimate_return_cache.json"
FUND_HOLDINGS_CACHE = ROOT / "cache" / "fund_holdings_cache.json"
SECURITY_RETURN_CACHE = ROOT / "cache" / "security_return_cache.json"

GOOD_STATUSES = {"traded", "closed"}
BAD_STATUSES = {"pending", "missing", "stale", "failed"}


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:10]


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
    print(f"基金代码: {fund_code}")
    print(f"基金名称: {fund_name}")
    print("可用估值日期：")
    print("估值日期\t表格显示\t数据状态\tis_final\t运行时间")
    for item in items:
        anchor_date = normalize_date(item.get("valuation_anchor_date") or item.get("valuation_date"))
        print(
            f"{anchor_date}\t"
            f"{fmt_pct(item.get('estimate_return_pct'), signed=True)}\t"
            f"{item.get('data_status') or item.get('stage', '')}\t"
            f"{item.get('is_final')}\t"
            f"{item.get('run_time_bj', '')}"
        )
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
) -> tuple[list[dict[str, Any]], float, float, float]:
    rows = []
    raw_sum = 0.0
    valid_raw_sum = 0.0
    holding_contribution_sum = 0.0

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
        effective_weight = raw_weight * boost if is_resolved else 0.0
        contribution = effective_weight * return_pct / 100.0 if is_resolved else 0.0

        if is_resolved:
            valid_raw_sum += raw_weight
        holding_contribution_sum += contribution

        rows.append({
            "name": holding_name(item),
            "market": market,
            "ticker": normalize_ticker(market, ticker),
            "raw_weight": raw_weight,
            "status": status,
            "trade_date": trade_date,
            "return_pct": return_pct if is_resolved else 0.0,
            "effective_weight": effective_weight,
            "contribution": contribution,
            "source": source,
            "error": error,
        })

    return rows, raw_sum, valid_raw_sum, holding_contribution_sum


def print_breakdown(fund_code: str, anchor_date: str) -> None:
    fund_cache = load_json(FUND_ESTIMATE_CACHE, {})
    records = fund_cache.get("records", {}) if isinstance(fund_cache, dict) else {}
    security_cache = load_json(SECURITY_RETURN_CACHE, {})

    record = find_record(records, fund_code, anchor_date)
    holdings = load_holdings(fund_code)

    fund_name = record.get("fund_name", "")
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
    )

    raw_sum = safe_float(record.get("raw_holding_weight_sum_pct"), raw_sum_calc)
    valid_sum = safe_float(record.get("valid_holding_weight_pct"), valid_sum_calc)
    failed_sum = safe_float(record.get("failed_raw_weight_sum_pct"), max(0.0, raw_sum - valid_sum))
    boosted_valid = safe_float(record.get("boosted_valid_holding_weight_pct"), valid_sum * boost)
    residual_contribution = residual_weight * residual_return / 100.0 if residual_status in GOOD_STATUSES else 0.0
    total_calc = holding_contribution_sum + residual_contribution
    total_cache = safe_float(record.get("estimate_return_pct"))

    print(f"基金代码: {fund_code}")
    print(f"基金名称: {fund_name}")
    print(f"估值锚点: {anchor_date}")
    print(f"运行时间: {record.get('run_time_bj', '')}")
    print(f"数据状态: {record.get('data_status') or record.get('stage', '')}, is_final={record.get('is_final')}")
    print()
    print(f"前十大披露持仓合计: {fmt_pct(raw_sum)}")
    print(f"行情有效持仓: {fmt_pct(valid_sum)}")
    print(f"行情失败持仓: {fmt_pct(failed_sum)}")
    print(f"有效持仓放大系数: {boost:.2f}")
    print(f"放大后有效持仓权重: {fmt_pct(valid_sum)} * {boost:.2f} = {fmt_pct(boosted_valid)}")
    print(f"补偿仓位: 100% - {fmt_pct(boosted_valid)} = {fmt_pct(residual_weight)}")
    print(
        f"补偿基准: {residual_label}，交易日 {residual_trade_date or '-'}，"
        f"状态 {residual_status}，涨幅 {fmt_pct(residual_return, digits=4, signed=True)}"
    )
    print()
    print("逐项贡献如下：")
    print("持仓\t市场\t代码\t原始权重\t状态\t行情交易日\t股票自身涨跌幅\t估算权重\t对基金贡献\t数据源")

    for row in rows:
        print(
            f"{row['name']}\t"
            f"{row['market']}\t"
            f"{row['ticker']}\t"
            f"{fmt_pct(row['raw_weight'])}\t"
            f"{row['status']}\t"
            f"{row['trade_date'] or '-'}\t"
            f"{fmt_pct(row['return_pct'], digits=4, signed=True)}\t"
            f"{fmt_pct(row['effective_weight'], digits=3)}\t"
            f"{fmt_pct(row['contribution'], digits=4, signed=True)}\t"
            f"{row['source'] or '-'}"
        )

    print(
        f"{residual_label}补偿仓位\t"
        f"{residual_market}\t"
        f"{residual_ticker}\t"
        f"{fmt_pct(residual_weight)}\t"
        f"{residual_status}\t"
        f"{residual_trade_date or '-'}\t"
        f"{fmt_pct(residual_return, digits=4, signed=True)}\t"
        f"{fmt_pct(residual_weight, digits=3)}\t"
        f"{fmt_pct(residual_contribution, digits=4, signed=True)}\t"
        "residual_benchmark"
    )

    print()
    print(f"持仓贡献合计: {fmt_pct(holding_contribution_sum, digits=6, signed=True)}")
    print(f"补偿仓位贡献: {fmt_pct(residual_contribution, digits=6, signed=True)}")
    print(f"复算合计: {fmt_pct(total_calc, digits=6, signed=True)}")
    print(f"缓存估算值: {fmt_pct(total_cache, digits=6, signed=True)}")
    print(f"表格显示: {fmt_pct(total_cache, digits=2, signed=True)}")

    failed_rows = [row for row in rows if row["status"] in BAD_STATUSES]
    if failed_rows:
        print()
        print("失败/未完成持仓：")
        for row in failed_rows:
            msg = row["error"] or row["source"] or "无错误详情"
            print(f"- {row['name']} {row['market']}:{row['ticker']} {row['status']} {msg}")


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

    date_text = input(
        "请输入估值日期，例如 2026-05-06；留空=列出可用估值日期；输入 latest=查看最新："
    ).strip()

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
        description="打印某只海外/全球基金在指定估值锚点的收益预估拆解。"
    )
    parser.add_argument("fund_code", nargs="?", help="基金代码，例如 022184")
    parser.add_argument("valuation_anchor_date", nargs="?", help="估值锚点日期，例如 2026-05-06")
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
