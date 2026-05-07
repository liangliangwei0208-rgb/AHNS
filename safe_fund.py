"""
safe_fund.py

从 main.py 已生成的基金级缓存中绘制公开展示版基金模型估算观察表格。

使用方式：
1. 先手动运行 main.py，让 cache/fund_estimate_return_cache.json 写入最新结果；
2. 再运行本文件，只读取缓存并绘图，不重新估算、不重新拉取行情。

输出：
- output/safe_haiwai_fund.png

表格只展示：序号、基金名称、模型估算观察、模型观察限购信息。
基金名称默认不隐藏；如需用星号隐藏后半段，修改 tools.safe_display.MASK_FUND_NAME_WITH_STAR。
不展示基金代码。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from tools.fund_table_image import save_fund_estimate_table_image
from tools.fund_universe import HAIWAI_FUND_CODES
from tools.safe_display import WATERMARK_ALPHA, add_risk_watermark, mask_fund_name



Path("output").mkdir(parents=True, exist_ok=True)

CACHE_FILE = Path("cache") / "fund_estimate_return_cache.json"
PURCHASE_LIMIT_CACHE_FILE = Path("cache") / "fund_purchase_limit_cache.json"


ESTIMATE_RETURN_COLUMN = "今日预估涨跌幅"
DISPLAY_OBSERVATION_COLUMN = "模型估算观察"
DISPLAY_PURCHASE_LIMIT_COLUMN = "模型观察限购信息"
SAFE_COLUMNS = ["序号", "基金名称", DISPLAY_OBSERVATION_COLUMN, DISPLAY_PURCHASE_LIMIT_COLUMN]
BRAND_WATERMARK_TEXT = "鱼师AHNS"
# 是否用星号隐藏 safe_fund 图片里的基金名称后半段；默认不隐藏。
MASK_FUND_NAMES_WITH_STAR = True


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def normalize_fund_code(code: Any) -> str:
    return str(code).strip().zfill(6)


def normalize_date_string(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    try:
        dt = pd.to_datetime(text, errors="coerce")
        if pd.isna(dt):
            return ""
        return pd.Timestamp(dt).strftime("%Y-%m-%d")
    except Exception:
        return ""


def safe_float_or_none(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def load_estimate_cache() -> dict[str, Any]:
    if not CACHE_FILE.exists():
        raise FileNotFoundError(
            f"未找到基金预估缓存文件: {CACHE_FILE}。请先运行 main.py 生成缓存，再运行 safe_fund.py。"
        )

    with CACHE_FILE.open("r", encoding="utf-8") as f:
        cache = json.load(f)

    if not isinstance(cache, dict):
        raise ValueError(f"基金预估缓存格式异常: {CACHE_FILE}")

    records = cache.get("records")
    if not isinstance(records, dict):
        raise ValueError(f"基金预估缓存缺少 records: {CACHE_FILE}")

    return cache


def load_purchase_limit_cache() -> dict[str, Any]:
    if not PURCHASE_LIMIT_CACHE_FILE.exists():
        log(f"[WARN] 未找到限购缓存文件: {PURCHASE_LIMIT_CACHE_FILE}")
        return {}

    try:
        with PURCHASE_LIMIT_CACHE_FILE.open("r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception as exc:
        log(f"[WARN] 限购缓存读取失败: {PURCHASE_LIMIT_CACHE_FILE}, 原因: {exc}")
        return {}

    return cache if isinstance(cache, dict) else {}


def purchase_limit_text_for_record(record: dict[str, Any], purchase_limit_cache: dict[str, Any]) -> str:
    fund_code = normalize_fund_code(record.get("fund_code", ""))
    item = purchase_limit_cache.get(fund_code)
    if isinstance(item, dict):
        value = str(item.get("value", "")).strip()
    else:
        value = str(item or "").strip()

    return value or "未知"


def record_valuation_date(record: dict[str, Any]) -> str:
    return (
        normalize_date_string(record.get("valuation_date"))
        or normalize_date_string(record.get("run_date_bj"))
        or normalize_date_string(record.get("run_time_bj"))
    )


def record_rank(record: dict[str, Any]) -> tuple[int, str]:
    is_final = 1 if bool(record.get("is_final", False)) else 0
    run_time = str(record.get("run_time_bj", ""))
    return is_final, run_time


def latest_market_records(
    cache: dict[str, Any],
    *,
    market_group: str,
    market_label: str,
    fund_codes: list[str],
) -> tuple[str, list[dict[str, Any]]]:
    records = cache.get("records", {})
    fund_code_set = {normalize_fund_code(code) for code in fund_codes}
    candidates: list[dict[str, Any]] = []

    for item in records.values():
        if not isinstance(item, dict):
            continue

        if str(item.get("market_group", "")).strip() != market_group:
            continue

        fund_code = normalize_fund_code(item.get("fund_code", ""))
        if fund_code not in fund_code_set:
            continue

        estimate_return_pct = safe_float_or_none(item.get("estimate_return_pct"))
        valuation_date = record_valuation_date(item)
        if estimate_return_pct is None or not valuation_date:
            continue

        record = dict(item)
        record["_fund_code_norm"] = fund_code
        record["_estimate_return_pct_float"] = estimate_return_pct
        record["_valuation_date_norm"] = valuation_date
        candidates.append(record)

    if not candidates:
        raise RuntimeError(
            f"未找到{market_label}基金级预估缓存。请先运行 main.py 生成缓存，再运行 safe_fund.py。"
        )

    latest_date = max(str(item["_valuation_date_norm"]) for item in candidates)
    latest_candidates = [
        item for item in candidates if str(item["_valuation_date_norm"]) == latest_date
    ]

    selected_by_code: dict[str, dict[str, Any]] = {}
    for item in latest_candidates:
        fund_code = str(item["_fund_code_norm"])
        old = selected_by_code.get(fund_code)
        if old is None or record_rank(item) > record_rank(old):
            selected_by_code[fund_code] = item

    missing_codes = [
        normalize_fund_code(code)
        for code in fund_codes
        if normalize_fund_code(code) not in selected_by_code
    ]
    if missing_codes:
        log(
            f"[WARN] {market_label}最新缓存日期 {latest_date} 缺少 {len(missing_codes)} 只基金: "
            + "、".join(missing_codes)
        )

    selected = sorted(
        selected_by_code.values(),
        key=lambda item: float(item["_estimate_return_pct_float"]),
        reverse=True,
    )

    return latest_date, selected


def build_safe_display_df(
    records: list[dict[str, Any]],
    *,
    purchase_limit_cache: dict[str, Any] | None = None,
    mask_names: bool = MASK_FUND_NAMES_WITH_STAR,
) -> pd.DataFrame:
    """
    将缓存记录转成公开展示表。

    数值仍来自 estimate_return_pct；图片展示列名弱化为“模型估算观察”。
    """
    rows = []
    purchase_limit_cache = purchase_limit_cache or {}
    for index, record in enumerate(records, start=1):
        fund_name = str(record.get("fund_name", "")).strip() or "基金名称缺失"
        rows.append(
            {
                "序号": index,
                "基金名称": mask_fund_name(fund_name, enabled=mask_names),
                DISPLAY_OBSERVATION_COLUMN: float(record["_estimate_return_pct_float"]),
                DISPLAY_PURCHASE_LIMIT_COLUMN: purchase_limit_text_for_record(record, purchase_limit_cache),
            }
        )

    return pd.DataFrame(rows, columns=SAFE_COLUMNS)


def get_benchmark_footer_items(
    cache: dict[str, Any],
    valuation_date: str,
) -> list[dict[str, Any]]:
    """
    读取海外指数基准 footer。

    只取与基金缓存同一 valuation_date 的记录，避免基金和基准日期错位。
    """
    records = cache.get("benchmark_records", {})
    if not isinstance(records, dict):
        return []

    selected_by_symbol: dict[str, dict[str, Any]] = {}
    for item in records.values():
        if not isinstance(item, dict):
            continue

        if str(item.get("market_group", "")).strip() != "overseas":
            continue

        item_valuation_date = normalize_date_string(item.get("valuation_date"))
        if item_valuation_date != valuation_date:
            continue

        symbol = str(item.get("symbol", "")).strip().upper()
        return_pct = safe_float_or_none(item.get("return_pct"))
        if not symbol or return_pct is None:
            continue

        old = selected_by_symbol.get(symbol)
        if old is None or record_rank(item) > record_rank(old):
            selected_by_symbol[symbol] = dict(item)

    footer_items = []
    for item in selected_by_symbol.values():
        footer_items.append(
            {
                "label": str(item.get("label", "")).strip() or str(item.get("symbol", "")).strip(),
                "symbol": str(item.get("symbol", "")).strip(),
                "return_pct": float(item.get("return_pct")),
                "trade_date": normalize_date_string(item.get("trade_date")) or valuation_date,
                "source": str(item.get("source", "")).strip(),
            }
        )

    if not footer_items:
        log(f"[WARN] 未找到海外指数基准缓存 footer，valuation_date={valuation_date}")

    return footer_items


def save_haiwai_safe_table(
    safe_df: pd.DataFrame,
    *,
    valuation_date: str,
    benchmark_footer_items: list[dict[str, Any]],
) -> None:
    output_file = "output/safe_haiwai_fund.png"
    # 复用原绘图函数的内部收益列名，同时在图片表头中展示为更克制的合规文案。
    image_df = safe_df.rename(columns={DISPLAY_OBSERVATION_COLUMN: ESTIMATE_RETURN_COLUMN})
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = f"海外基金模型估算观察 估值日：{valuation_date} 生成：{generated_at}"
    title_segments = [
        {"text": "海外基金模型估算观察  ", "color": "black", "fontweight": "bold"},
        {"text": f"估值日：{valuation_date}", "color": "red", "fontweight": "bold"},
        {"text": f"  生成：{generated_at}", "color": "black", "fontweight": "bold"},
    ]
    save_fund_estimate_table_image(
        result_df=image_df,
        output_file=output_file,
        title=title,
        title_segments=title_segments,
        display_column_names={ESTIMATE_RETURN_COLUMN: DISPLAY_OBSERVATION_COLUMN},
        benchmark_footer_items=benchmark_footer_items,
        benchmark_footer_fontsize=15,
        footnote_text=(
            "依据基金季度报告前十大持仓股及指数估算，仅供学习记录，"
            "不构成投资建议；最终以基金公司更新为准。"
        ),
        watermark_text=BRAND_WATERMARK_TEXT,
        watermark_alpha=WATERMARK_ALPHA,
        watermark_fontsize=32,
        up_color="red",
        down_color="green",
        pct_digits=2,
        row_height=0.55,
    )
    add_risk_watermark(output_file)
    log(f"海外基金安全版预估表生成完成: {output_file}，缓存日期: {valuation_date}")


def build_and_save_haiwai(cache: dict[str, Any]) -> bool:
    haiwai_date, haiwai_records = latest_market_records(
        cache,
        market_group="overseas",
        market_label="海外",
        fund_codes=HAIWAI_FUND_CODES,
    )
    purchase_limit_cache = load_purchase_limit_cache()
    haiwai_df = build_safe_display_df(
        haiwai_records,
        purchase_limit_cache=purchase_limit_cache,
    )
    benchmark_footer_items = get_benchmark_footer_items(cache, haiwai_date)

    save_haiwai_safe_table(
        haiwai_df,
        valuation_date=haiwai_date,
        benchmark_footer_items=benchmark_footer_items,
    )
    print("\n海外基金安全版预估表：")
    print(haiwai_df.to_string(index=False))
    return True


def main() -> None:
    cache = load_estimate_cache()
    generated_any = False

    try:
        generated_any = build_and_save_haiwai(cache) or generated_any
    except RuntimeError as exc:
        log(f"[WARN] 海外安全图未生成: {exc}")

    if not generated_any:
        raise RuntimeError("未找到可用于生成安全图的基金级预估缓存。请先运行 main.py。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}", flush=True)
        raise SystemExit(1)
