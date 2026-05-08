"""
sum_holidays.py

只读取现有基金估算缓存，生成节后海外基金净值补更新预估图。

运行口径：
- 普通周六、周日不属于节假日补更新场景，不生成图片；
- 节后第 1 个 A 股交易日只读取节前最后一个海外估值日，生成单日预估图；
- 节后第 2 个 A 股交易日累计节前最后交易日之后的海外估值日；
- 节后第 3 个 A 股交易日起回归 main.py / safe_fund.py 的正常日更节奏。

本脚本不拉行情、不重新估算基金、不写缓存。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from safe_holidays import (
    CUMULATIVE_DISPLAY_COLUMN,
    CUMULATIVE_INTERNAL_COLUMN,
    build_safe_summary_df,
)
from tools.fund_estimate_history_overseas import (
    build_benchmark_cumulative_dataframe,
    build_cumulative_dataframe,
    get_benchmark_estimate_records,
    get_fund_estimate_records,
    save_cumulative_estimate_table_image,
)
from tools.fund_history_io import load_a_share_trade_dates
from tools.get_top10_holdings import (
    format_pct,
    save_fund_estimate_table_image,
)
from tools.safe_display import add_brand_text_watermark, add_center_image_watermark, mask_fund_name


SAFE_OUTPUT_FILE = "output/safe_sum_holidays.png"
MAX_POST_HOLIDAY_TRADE_DAYS = 2
MAX_HOLIDAY_LOOKBACK_TRADE_DAYS = 15

FOOTNOTE_TEXT = (
    "按节后QDII净值补更新口径读取历史缓存并复利估算，仅供学习记录，"
    "不构成投资建议；最终以基金公司更新为准。"
)
DISPLAY_COLUMN_NAMES = {CUMULATIVE_INTERNAL_COLUMN: CUMULATIVE_DISPLAY_COLUMN}
DAILY_DISPLAY_COLUMN_NAMES = {"今日预估涨跌幅": "预估收益率"}
SAFE_DAILY_DISPLAY_COLUMN_NAMES = {"今日预估涨跌幅": "模型估算观察"}


@dataclass(frozen=True)
class PostHolidayContext:
    should_generate: bool
    reason: str
    today: date
    calendar_source: str = ""
    post_holiday_trade_day: int = 0
    pre_holiday_trade_date: str = ""
    first_post_holiday_trade_date: str = ""
    closed_dates: tuple[str, ...] = field(default_factory=tuple)
    weekday_closed_dates: tuple[str, ...] = field(default_factory=tuple)


def _normalize_date(value) -> date | None:
    if value is None:
        return None
    try:
        dt = pd.to_datetime(str(value), errors="coerce")
        if pd.isna(dt):
            return None
        return pd.Timestamp(dt).date()
    except Exception:
        return None


def _get_beijing_today(today=None) -> date:
    parsed = _normalize_date(today)
    if parsed is not None:
        return parsed
    if today is not None:
        raise ValueError("today 必须是可解析的日期，例如 2026-05-06。")
    try:
        return datetime.now(ZoneInfo("Asia/Shanghai")).date()
    except Exception:
        return datetime.now().date()


def _date_range_exclusive(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start + timedelta(days=1)
    while current < end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _date_label(value: str | date) -> str:
    parsed = _normalize_date(value)
    if parsed is None:
        return str(value)
    return f"{parsed.month}.{parsed.day}"


def _load_trade_dates() -> tuple[list[date], str]:
    trade_date_strings, source = load_a_share_trade_dates(use_akshare=True)
    trade_dates = sorted(
        parsed
        for parsed in (_normalize_date(x) for x in trade_date_strings)
        if parsed is not None
    )
    return trade_dates, source


def detect_post_holiday_context(today=None) -> PostHolidayContext:
    today_date = _get_beijing_today(today)
    today_str = today_date.isoformat()
    trade_dates, calendar_source = _load_trade_dates()

    if not trade_dates:
        return PostHolidayContext(
            should_generate=False,
            reason="无法读取A股交易日历，未生成节后海外基金净值补更新预估图。",
            today=today_date,
            calendar_source=calendar_source,
        )

    trade_date_set = set(trade_dates)
    if today_date not in trade_date_set:
        if today_date.weekday() >= 5:
            reason = f"{today_str} 是普通周末或A股非交易日，不属于节后开盘补更新场景。"
        else:
            reason = f"{today_str} 是A股休市日，还没有进入节后开盘补更新场景。"
        return PostHolidayContext(
            should_generate=False,
            reason=reason,
            today=today_date,
            calendar_source=calendar_source,
        )

    today_index = trade_dates.index(today_date)
    lower_bound = max(1, today_index - MAX_HOLIDAY_LOOKBACK_TRADE_DAYS + 1)
    ordinary_weekend_seen = False

    for pair_index in range(today_index, lower_bound - 1, -1):
        previous_trade_date = trade_dates[pair_index - 1]
        next_trade_date = trade_dates[pair_index]
        closed_dates = _date_range_exclusive(previous_trade_date, next_trade_date)

        if not closed_dates:
            continue

        weekday_closed_dates = [d for d in closed_dates if d.weekday() < 5]
        if not weekday_closed_dates:
            ordinary_weekend_seen = True
            continue

        post_holiday_trade_day = today_index - pair_index + 1
        pre_holiday_trade_date = previous_trade_date.isoformat()
        first_post_holiday_trade_date = next_trade_date.isoformat()
        closed_date_strings = tuple(d.isoformat() for d in closed_dates)
        weekday_closed_date_strings = tuple(d.isoformat() for d in weekday_closed_dates)

        if post_holiday_trade_day > MAX_POST_HOLIDAY_TRADE_DAYS:
            return PostHolidayContext(
                should_generate=False,
                reason=(
                    f"{today_str} 是节后第 {post_holiday_trade_day} 个A股交易日，"
                    "已回归正常节奏，请使用 main.py / safe_fund.py 的当日预估图。"
                ),
                today=today_date,
                calendar_source=calendar_source,
                post_holiday_trade_day=post_holiday_trade_day,
                pre_holiday_trade_date=pre_holiday_trade_date,
                first_post_holiday_trade_date=first_post_holiday_trade_date,
                closed_dates=closed_date_strings,
                weekday_closed_dates=weekday_closed_date_strings,
            )

        return PostHolidayContext(
            should_generate=True,
            reason=(
                f"识别为节后第 {post_holiday_trade_day} 个A股交易日；"
                f"A股日历来源: {calendar_source}; "
                f"节前最后交易日: {pre_holiday_trade_date}; "
                f"休市区间: {closed_date_strings[0]} 至 {closed_date_strings[-1]}"
            ),
            today=today_date,
            calendar_source=calendar_source,
            post_holiday_trade_day=post_holiday_trade_day,
            pre_holiday_trade_date=pre_holiday_trade_date,
            first_post_holiday_trade_date=first_post_holiday_trade_date,
            closed_dates=closed_date_strings,
            weekday_closed_dates=weekday_closed_date_strings,
        )

    if ordinary_weekend_seen:
        reason = f"{today_str} 前面只是普通周末闭市，不属于节假日后净值补更新场景。"
    else:
        reason = f"{today_str} 不是节假日后第1或第2个A股交易日，未生成图片。"

    return PostHolidayContext(
        should_generate=False,
        reason=reason,
        today=today_date,
        calendar_source=calendar_source,
    )


def _filter_records_not_after_today(df: pd.DataFrame, today_str: str) -> pd.DataFrame:
    if df is None or df.empty or "run_date_bj" not in df.columns:
        return df
    run_dates = df["run_date_bj"].astype(str)
    return df[(run_dates == "") | (run_dates <= today_str)].copy()


def _load_overseas_daily_records(
    start_date: str,
    end_date: str,
    today_str: str,
    cache_file: str | Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, tuple[str, ...]]:
    fund_daily_df = get_fund_estimate_records(
        start_date=start_date,
        end_date=end_date,
        market_group="overseas",
        date_field="valuation_date",
        include_intraday=True,
        require_final=False,
        cache_file=cache_file,
    )
    benchmark_daily_df = get_benchmark_estimate_records(
        start_date=start_date,
        end_date=end_date,
        market_group="overseas",
        date_field="valuation_date",
        include_intraday=True,
        require_final=False,
        cache_file=cache_file,
    )

    fund_daily_df = _filter_records_not_after_today(fund_daily_df, today_str)
    benchmark_daily_df = _filter_records_not_after_today(benchmark_daily_df, today_str)

    if fund_daily_df.empty or benchmark_daily_df.empty:
        return fund_daily_df, benchmark_daily_df, tuple()

    fund_dates = set(fund_daily_df["valuation_date"].dropna().astype(str))
    benchmark_dates = set(benchmark_daily_df["valuation_date"].dropna().astype(str))
    common_dates = tuple(sorted(fund_dates & benchmark_dates))

    if common_dates:
        fund_daily_df = fund_daily_df[fund_daily_df["valuation_date"].isin(common_dates)].copy()
        benchmark_daily_df = benchmark_daily_df[
            benchmark_daily_df["valuation_date"].isin(common_dates)
        ].copy()

    return fund_daily_df, benchmark_daily_df, common_dates


def _target_valuation_window(context: PostHolidayContext) -> tuple[str, str, str]:
    pre_holiday_date = _normalize_date(context.pre_holiday_trade_date)
    if pre_holiday_date is None:
        raise RuntimeError("节前最后A股交易日解析失败，无法确定海外估值日期。")

    if context.post_holiday_trade_day == 1:
        target_date = pre_holiday_date.isoformat()
        title = (
            f"{_date_label(context.today)}晚海外基金净值补更新预估"
            f"（{_date_label(target_date)}估值）"
        )
        return target_date, target_date, title

    if context.post_holiday_trade_day == 2:
        start_date = (pre_holiday_date + timedelta(days=1)).isoformat()
        end_date = context.today.isoformat()
        title = f"{_date_label(context.today)}晚海外基金节后累计补更新预估"
        return start_date, end_date, title

    raise RuntimeError("该脚本只处理节后第1天和第2天。")


def _build_title(context: PostHolidayContext, valuation_dates: tuple[str, ...], fallback: str) -> str:
    if not valuation_dates:
        return fallback

    if context.post_holiday_trade_day == 1:
        return (
            f"{_date_label(context.today)}晚海外基金净值补更新预估"
            f"（{_date_label(valuation_dates[0])}估值）"
        )

    if context.post_holiday_trade_day == 2:
        return (
            f"{_date_label(context.today)}晚海外基金节后累计补更新预估"
            f"（{_date_label(valuation_dates[0])}-{_date_label(valuation_dates[-1])}估值）"
        )

    return fallback


def _daily_result_dataframe(fund_daily_df: pd.DataFrame) -> pd.DataFrame:
    if fund_daily_df is None or fund_daily_df.empty:
        return pd.DataFrame(
            columns=["序号", "基金代码", "基金名称", "估值日", "今日预估涨跌幅"]
        )

    rows = []
    for _, row in fund_daily_df.iterrows():
        return_pct = pd.to_numeric(row.get("estimate_return_pct"), errors="coerce")
        if pd.isna(return_pct):
            continue
        rows.append(
            {
                "基金代码": str(row.get("fund_code", "")).strip().zfill(6),
                "基金名称": str(row.get("fund_name", "")).strip(),
                "估值日": str(row.get("valuation_date", "")).strip(),
                "今日预估涨跌幅": float(return_pct),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return _daily_result_dataframe(pd.DataFrame())
    out = out.sort_values("今日预估涨跌幅", ascending=False).reset_index(drop=True)
    out.insert(0, "序号", range(1, len(out) + 1))
    return out


def _safe_daily_result_dataframe(result_df: pd.DataFrame) -> pd.DataFrame:
    if result_df is None or result_df.empty:
        raise RuntimeError("目标海外基金缓存为空，无法生成安全版图片。")

    safe_df = result_df.copy()
    keep_columns = ["序号", "基金名称", "估值日", "今日预估涨跌幅"]
    for col in keep_columns:
        if col not in safe_df.columns:
            safe_df[col] = None
    safe_df = safe_df[keep_columns].copy()
    safe_df["序号"] = range(1, len(safe_df) + 1)
    safe_df["基金名称"] = safe_df["基金名称"].map(mask_fund_name)
    return safe_df


def _daily_benchmark_footer_items(benchmark_daily_df: pd.DataFrame) -> list[dict]:
    if benchmark_daily_df is None or benchmark_daily_df.empty:
        return []

    items = []
    sort_order = {".NDX": 0, "^NDX": 0, "NDX": 0, ".INX": 1, "^GSPC": 1}
    out = benchmark_daily_df.copy()
    out["_sort"] = out["symbol"].astype(str).str.upper().map(sort_order).fillna(99)
    out = out.sort_values(["_sort", "symbol", "valuation_date"])

    for _, row in out.iterrows():
        value = pd.to_numeric(row.get("return_pct"), errors="coerce")
        if pd.isna(value):
            continue
        items.append(
            {
                "label": str(row.get("label", row.get("symbol", "基准"))).strip() or "基准",
                "symbol": str(row.get("symbol", "")).strip(),
                "return_pct": float(value),
                "trade_date": str(row.get("valuation_date", row.get("trade_date", ""))).strip(),
                "source": str(row.get("source", "cache")).strip(),
            }
        )
    return items


def _print_daily_estimate_table(
    result_df: pd.DataFrame,
    title: str,
    benchmark_items: list[dict],
    pct_digits: int = 2,
) -> None:
    show_df = result_df.copy()
    if "今日预估涨跌幅" in show_df.columns:
        show_df["今日预估涨跌幅"] = show_df["今日预估涨跌幅"].map(
            lambda x: format_pct(x, digits=pct_digits)
        )
        show_df = show_df.rename(columns=DAILY_DISPLAY_COLUMN_NAMES)

    print("=" * 100)
    print(title)
    print("=" * 100)
    print(show_df.to_string(index=False))

    if benchmark_items:
        bench_rows = []
        for item in benchmark_items:
            bench_rows.append(
                {
                    "指数名称": item.get("label", ""),
                    "指数代码": item.get("symbol", ""),
                    "估值日": item.get("trade_date", ""),
                    "涨跌幅": format_pct(item.get("return_pct"), digits=pct_digits),
                }
            )
        print("-" * 100)
        print("指数基准单日涨跌幅")
        print(pd.DataFrame(bench_rows).to_string(index=False))
    print("=" * 100)


def _format_benchmark_summary_labels(
    benchmark_summary_df: pd.DataFrame,
    valuation_dates: tuple[str, ...],
) -> pd.DataFrame:
    if benchmark_summary_df is None or benchmark_summary_df.empty or not valuation_dates:
        return benchmark_summary_df

    label = f"{_date_label(valuation_dates[0])}-{_date_label(valuation_dates[-1])}"
    out = benchmark_summary_df.copy()
    if "指数名称" in out.columns:
        out["指数名称"] = out["指数名称"].map(lambda x: f"{x}（{label}）")
    return out


def _save_daily_images(
    fund_daily_df: pd.DataFrame,
    benchmark_daily_df: pd.DataFrame,
    title: str,
) -> None:
    result_df = _daily_result_dataframe(fund_daily_df)
    if result_df.empty:
        print("目标海外基金单日缓存为空，未生成图片。")
        return

    benchmark_items = _daily_benchmark_footer_items(benchmark_daily_df)
    _print_daily_estimate_table(result_df, title, benchmark_items, pct_digits=2)

    safe_df = _safe_daily_result_dataframe(result_df)
    save_fund_estimate_table_image(
        result_df=safe_df,
        output_file=SAFE_OUTPUT_FILE,
        title=title,
        pct_digits=2,
        display_column_names=SAFE_DAILY_DISPLAY_COLUMN_NAMES,
        benchmark_footer_items=benchmark_items,
        footnote_text=FOOTNOTE_TEXT,
        watermark_text="",
        watermark_alpha=0,
        watermark_fontsize=32,
        up_color="red",
        down_color="green",
        row_height=0.55,
    )
    add_center_image_watermark(SAFE_OUTPUT_FILE)
    add_brand_text_watermark(SAFE_OUTPUT_FILE)
    print(f"安全版图片已生成: {SAFE_OUTPUT_FILE}")


def _save_safe_image(
    summary_df: pd.DataFrame,
    benchmark_summary_df: pd.DataFrame,
    title: str,
) -> None:
    safe_summary_df = build_safe_summary_df(summary_df)
    image_summary_df = safe_summary_df.rename(
        columns={CUMULATIVE_DISPLAY_COLUMN: CUMULATIVE_INTERNAL_COLUMN}
    )

    save_cumulative_estimate_table_image(
        summary_df=image_summary_df,
        output_file=SAFE_OUTPUT_FILE,
        title=title,
        pct_digits=2,
        display_column_names=DISPLAY_COLUMN_NAMES,
        benchmark_summary_df=benchmark_summary_df,
        hide_status_column=True,
        footnote_text=FOOTNOTE_TEXT,
        watermark_text="",
        watermark_alpha=0,
        watermark_fontsize=32,
        up_color="red",
        down_color="green",
        row_height=0.55,
    )
    add_center_image_watermark(SAFE_OUTPUT_FILE)
    add_brand_text_watermark(SAFE_OUTPUT_FILE)
    print(f"安全版图片已生成: {SAFE_OUTPUT_FILE}")


def run(today=None, cache_file: str | Path | None = None) -> bool:
    Path("output").mkdir(parents=True, exist_ok=True)

    context = detect_post_holiday_context(today=today)
    print(context.reason)

    if context.calendar_source:
        print(f"A股日历来源: {context.calendar_source}")
    if context.closed_dates:
        print(f"A股闭市日期: {', '.join(context.closed_dates)}")
    if context.weekday_closed_dates:
        print(f"其中工作日闭市: {', '.join(context.weekday_closed_dates)}")

    if not context.should_generate:
        return False

    start_date, end_date, title = _target_valuation_window(context)
    today_str = context.today.isoformat()

    fund_daily_df, benchmark_daily_df, valuation_dates = _load_overseas_daily_records(
        start_date=start_date,
        end_date=end_date,
        today_str=today_str,
        cache_file=cache_file,
    )

    if not valuation_dates:
        print(
            "未找到目标海外估值日缓存，未生成图片。"
            f"目标区间: {start_date} 至 {end_date}；请先运行 main.py。"
        )
        return False

    if context.post_holiday_trade_day == 1 and valuation_dates != (start_date,):
        print(
            "节后第1天只使用节前最后估值日缓存；"
            f"当前可用估值日: {', '.join(valuation_dates)}"
        )

    if context.post_holiday_trade_day == 2:
        latest_valuation_date = valuation_dates[-1]
        print(
            f"节后第2天累计估值区间: {valuation_dates[0]} 至 {latest_valuation_date}; "
            f"有效估值日: {', '.join(valuation_dates)}"
        )
    else:
        print(f"节后第1天使用海外估值日: {valuation_dates[0]}")

    title = _build_title(context, valuation_dates, title)

    if context.post_holiday_trade_day == 1:
        _save_daily_images(fund_daily_df, benchmark_daily_df, title)
        return True

    summary_df = build_cumulative_dataframe(fund_daily_df)
    benchmark_summary_df = _format_benchmark_summary_labels(
        build_benchmark_cumulative_dataframe(benchmark_daily_df),
        valuation_dates,
    )

    if summary_df.empty:
        print("目标海外基金缓存为空，未生成图片。")
        return False

    _save_safe_image(summary_df, benchmark_summary_df, title)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="只读缓存生成节后海外基金净值补更新预估图。"
    )
    parser.add_argument(
        "--today",
        default=None,
        help="用于测试的北京时间日期，例如 2026-05-06；默认使用今天。",
    )
    parser.add_argument(
        "--cache-file",
        default=None,
        help="可选：指定 fund_estimate_return_cache.json 路径；默认读取 cache/ 下的正式缓存。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(today=args.today, cache_file=args.cache_file)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}", flush=True)
        raise SystemExit(1)
