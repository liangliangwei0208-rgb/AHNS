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
import json
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from PIL import Image, ImageChops

from safe_holidays import (
    CUMULATIVE_DISPLAY_COLUMN,
    CUMULATIVE_INTERNAL_COLUMN,
    build_safe_summary_df,
)
from tools.configs.market_benchmark_configs import MARKET_BENCHMARK_ITEMS
from tools.configs.safe_image_style_configs import (
    safe_cumulative_table_kwargs,
    safe_daily_table_kwargs,
)
from tools.fund_estimate_history_overseas import (
    build_benchmark_cumulative_dataframe,
    build_cumulative_dataframe,
    get_benchmark_estimate_records,
    get_fund_estimate_records,
    save_cumulative_estimate_table_image,
)
from tools.fund_history_io import detect_a_share_holiday_context, load_fund_estimate_history
from tools.fund_universe import HAIWAI_FUND_CODES
from tools.get_top10_holdings import (
    format_pct,
    save_fund_estimate_table_image,
)
from tools.paths import FUND_ESTIMATE_CACHE, SAFE_SUM_HOLIDAYS_IMAGE, ensure_runtime_dirs, relative_path_str
from tools.safe_display import apply_safe_public_watermarks, mask_fund_name


SAFE_OUTPUT_FILE = relative_path_str(SAFE_SUM_HOLIDAYS_IMAGE)
MAX_POST_HOLIDAY_TRADE_DAYS = 2
RESULT_PREFIX = "AHNS_SUM_HOLIDAYS_RESULT="

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
    verified: bool = True


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


def detect_post_holiday_context(today=None) -> PostHolidayContext:
    """兼容旧调用的数据类型，假期检测统一复用已验证的共享交易日历。"""
    today_date = _get_beijing_today(today)
    shared = detect_a_share_holiday_context(today_date)
    previous = _normalize_date(shared.previous_trade_date)
    reopen = _normalize_date(shared.first_post_holiday_trade_date)
    closed = _date_range_exclusive(previous, reopen) if previous and reopen else []
    ordinal = shared.post_holiday_trade_day
    reason = shared.reason
    if ordinal:
        reason = f"识别为节后第 {ordinal} 个A股交易日；{shared.reason}"
        if ordinal > MAX_POST_HOLIDAY_TRADE_DAYS:
            reason += "；已回归正常每日估算流程"
    return PostHolidayContext(
        should_generate=shared.verified and ordinal in (1, 2),
        reason=reason,
        today=today_date,
        calendar_source=shared.calendar_source,
        post_holiday_trade_day=ordinal,
        pre_holiday_trade_date=shared.previous_trade_date,
        first_post_holiday_trade_date=shared.first_post_holiday_trade_date,
        closed_dates=tuple(item.isoformat() for item in closed),
        weekday_closed_dates=tuple(item.isoformat() for item in closed if item.weekday() < 5),
        verified=shared.verified,
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
        include_intraday=False,
        require_final=False,
        include_partial_close=True,
        valid_close_only=True,
        as_of_run_date_bj=today_str,
        cache_file=cache_file,
    )
    benchmark_daily_df = get_benchmark_estimate_records(
        start_date=start_date,
        end_date=end_date,
        market_group="overseas",
        date_field="valuation_date",
        include_intraday=False,
        require_final=True,
        valid_close_only=True,
        as_of_run_date_bj=today_str,
        cache_file=cache_file,
    )

    fund_daily_df = _filter_records_not_after_today(fund_daily_df, today_str)
    benchmark_daily_df = _filter_records_not_after_today(benchmark_daily_df, today_str)

    # 基金与基准分别使用自身有效日期，基准缺失不能删掉基金真实收益。
    fund_dates = tuple(sorted(set(fund_daily_df.get("valuation_date", pd.Series(dtype=str)).dropna().astype(str))))
    return fund_daily_df, benchmark_daily_df, fund_dates


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
        first_reopen = _normalize_date(context.first_post_holiday_trade_date)
        if first_reopen is None or not pre_holiday_date < first_reopen < context.today:
            raise RuntimeError("第一复市交易日无效，无法确定节后累计截止日。")
        end_date = first_reopen.isoformat()
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
            f"（{_date_label(valuation_dates[0])}–{_date_label(valuation_dates[-1])}估值）"
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
    safe_df["基金名称"] = safe_df["基金名称"].map(lambda name: mask_fund_name(name, enabled=True))
    return safe_df


def _daily_benchmark_footer_items(benchmark_daily_df: pd.DataFrame) -> list[dict]:
    if benchmark_daily_df is None or benchmark_daily_df.empty:
        return []

    items = []
    sort_order = {
        str(item.get("ticker", "")).strip().upper(): order
        for order, item in enumerate(MARKET_BENCHMARK_ITEMS, start=1)
        if (
            isinstance(item, dict)
            and bool(item.get("enabled", True))
            and bool(item.get("display_in_holidays", True))
        )
    }
    disabled_symbols = {
        str(item.get("ticker", "")).strip().upper()
        for item in MARKET_BENCHMARK_ITEMS
        if (
            isinstance(item, dict)
            and (
                not bool(item.get("enabled", True))
                or not bool(item.get("display_in_holidays", True))
            )
        )
    }
    disabled_labels = {
        str(item.get("label", "")).strip()
        for item in MARKET_BENCHMARK_ITEMS
        if (
            isinstance(item, dict)
            and (
                not bool(item.get("enabled", True))
                or not bool(item.get("display_in_holidays", True))
            )
        )
    }
    config_labels = {
        str(item.get("label", "")).strip()
        for item in MARKET_BENCHMARK_ITEMS
        if (
            isinstance(item, dict)
            and bool(item.get("enabled", True))
            and bool(item.get("display_in_holidays", True))
        )
    }
    out = benchmark_daily_df.copy()
    out["_sort"] = out["symbol"].astype(str).str.upper().map(sort_order).fillna(99)
    out = out.sort_values(["_sort", "symbol", "valuation_date"])

    for _, row in out.iterrows():
        value = pd.to_numeric(row.get("return_pct"), errors="coerce")
        if pd.isna(value):
            continue
        label = str(row.get("label", row.get("symbol", "基准"))).strip() or "基准"
        symbol = str(row.get("symbol", "")).strip()
        if symbol.upper() in disabled_symbols or label in disabled_labels:
            continue
        if symbol.upper() not in sort_order and label in config_labels:
            continue
        items.append(
            {
                "label": label,
                "symbol": symbol,
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

    out = benchmark_summary_df.copy()
    if "指数名称" in out.columns:
        # 每个基准标自己的实际区间，不能把基金的有效日期范围借给缺日基准。
        out["指数名称"] = out.apply(lambda row: (
            f"{row['指数名称']}（{_date_label(row['起始估值日'])}-{_date_label(row['结束估值日'])}）"
            if row.get("起始估值日") and row.get("结束估值日") else row["指数名称"]
        ), axis=1)
    return out


def _save_daily_images(
    fund_daily_df: pd.DataFrame,
    benchmark_daily_df: pd.DataFrame,
    title: str,
    output_file: str | Path | None = None,
) -> None:
    output_file = output_file or SAFE_OUTPUT_FILE
    result_df = _daily_result_dataframe(fund_daily_df)
    if result_df.empty:
        raise RuntimeError("目标海外基金单日缓存为空，无法生成补更新图片。")

    benchmark_items = _daily_benchmark_footer_items(benchmark_daily_df)
    _print_daily_estimate_table(result_df, title, benchmark_items, pct_digits=2)

    safe_df = _safe_daily_result_dataframe(result_df)
    image_kwargs = safe_daily_table_kwargs()
    image_kwargs.update(
        {
            "footnote_text": FOOTNOTE_TEXT,
            # safe 系列统一由 tools.safe_display.apply_safe_public_watermarks()
            # 叠加居中 logo 和斜向文字水印，这里关闭表格函数内置平铺水印。
            "watermark_text": "",
            "watermark_alpha": 0,
            "watermark_fontsize": 32,
        }
    )
    save_fund_estimate_table_image(
        result_df=safe_df,
        output_file=str(output_file),
        title=title,
        pct_digits=2,
        display_column_names=SAFE_DAILY_DISPLAY_COLUMN_NAMES,
        benchmark_footer_items=benchmark_items,
        **image_kwargs,
    )
    apply_safe_public_watermarks(output_file)


def _save_safe_image(
    summary_df: pd.DataFrame,
    benchmark_summary_df: pd.DataFrame,
    title: str,
    output_file: str | Path | None = None,
) -> None:
    output_file = output_file or SAFE_OUTPUT_FILE
    safe_summary_df = build_safe_summary_df(summary_df)
    image_summary_df = safe_summary_df.rename(
        columns={CUMULATIVE_DISPLAY_COLUMN: CUMULATIVE_INTERNAL_COLUMN}
    )

    image_kwargs = safe_cumulative_table_kwargs()
    image_kwargs.update(
        {
            "footnote_text": FOOTNOTE_TEXT,
            # safe 系列统一由 tools.safe_display.apply_safe_public_watermarks()
            # 叠加居中 logo 和斜向文字水印，这里关闭表格函数内置平铺水印。
            "watermark_text": "",
            "watermark_alpha": 0,
            "watermark_fontsize": 32,
        }
    )
    save_cumulative_estimate_table_image(
        summary_df=image_summary_df,
        output_file=str(output_file),
        title=title,
        pct_digits=2,
        display_column_names=DISPLAY_COLUMN_NAMES,
        benchmark_summary_df=benchmark_summary_df,
        hide_status_column=True,
        # 上下表统一为五列，复用绘图函数已有的同列数边界/列宽对齐逻辑。
        hide_effective_days_column=True,
        **image_kwargs,
    )
    apply_safe_public_watermarks(output_file)


def _publish_image(render) -> str:
    """完整绘制、水印和校验成功后才替换正式图；相同像素不重复改写。"""
    target = Path(SAFE_OUTPUT_FILE).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".sum_holidays_", suffix=".png", dir=target.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        render(temporary)
        with Image.open(temporary) as image:
            image.verify()
        with Image.open(temporary) as image:
            current = image.convert("RGB")
        if target.is_file():
            try:
                with Image.open(target) as image:
                    previous = image.convert("RGB")
                if current.size == previous.size and ImageChops.difference(current, previous).getbbox() is None:
                    return "unchanged"
            except (OSError, ValueError):
                pass  # 旧图损坏时使用本次已校验成功的图片替换。
        temporary.replace(target)
        return "generated"
    finally:
        # 仅清理本次明确创建的单个临时文件，不批量删除目录或历史产物。
        if temporary.exists():
            temporary.unlink()


def _update_data_diagnostics(report, funds, benchmarks, cache_file):
    """缺口仅用于诊断，不把缺失收益当 0，也不强制各市场拥有相同交易日。"""
    fund_days = sorted(set(funds.get("valuation_date", [])))
    benchmark_days = sorted(set(benchmarks.get("valuation_date", [])))
    by_fund = {str(code).zfill(6): sorted(set(group["valuation_date"]))
               for code, group in funds.groupby("fund_code")} if not funds.empty else {}
    codes = sorted({str(code).zfill(6) for code in HAIWAI_FUND_CODES})
    reference_days = fund_days or [item.date().isoformat() for item in pd.date_range(report["start_date"], report["end_date"])]
    missing = {code: sorted(set(reference_days) - set(by_fund.get(code, []))) for code in codes}
    missing = {code: days for code, days in missing.items() if days}
    raw = load_fund_estimate_history(cache_file)
    if not raw.empty and "valuation_date" in raw:
        raw = raw[raw["valuation_date"].between(report["start_date"], report["end_date"])]
        raw = raw[raw["fund_code"].astype(str).str.zfill(6).isin(codes)]
    report.update(
        fund_valid_dates=fund_days, benchmark_valid_dates=benchmark_days,
        fund_record_count=len(funds), benchmark_record_count=len(benchmarks),
        fund_count=len(by_fund), expected_fund_count=len(codes), fund_dates_by_code=by_fund,
        missing_fund_count=len(set(codes) - set(by_fund)),
        missing_dates_by_fund=missing,
        missing_dates_basis="其它基金已有的有效估值日期（不同市场可能正常休市）" if fund_days else "目标自然日期；暂无有效基金日可核对",
        missing_reason="目标区间无基金记录" if raw.empty else "未缓存目标日，或记录未通过收盘/有限收益/可用运行日期校验；不同市场休市不补零",
        raw_fund_record_count=len(raw),
    )
    if not raw.empty:
        report["raw_record_states"] = raw.groupby(["stage", "data_status"], dropna=False).size().to_dict() if {"stage", "data_status"}.issubset(raw.columns) else {}
        report["raw_record_states"] = {str(key): int(value) for key, value in report["raw_record_states"].items()}


def run(today=None, cache_file: str | Path | None = None) -> bool:
    report = dict(status="failed", should_generate=False,
                  now_bj=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                  today=_get_beijing_today(today).isoformat(), post_holiday_trade_day=0,
                  pre_holiday_trade_date="", first_post_holiday_trade_date="", start_date="", end_date="",
                  fund_valid_dates=[], benchmark_valid_dates=[], fund_record_count=0, benchmark_record_count=0,
                  fund_count=0, missing_reason="", output_file=str(Path(SAFE_OUTPUT_FILE).resolve()),
                  image_status="not_generated", mail_collection_status="standalone_not_collected")
    try:
        context = detect_post_holiday_context(today=today)
        report.update(should_generate=context.should_generate, post_holiday_trade_day=context.post_holiday_trade_day,
                      pre_holiday_trade_date=context.pre_holiday_trade_date,
                      first_post_holiday_trade_date=context.first_post_holiday_trade_date,
                      calendar_source=context.calendar_source, reason=context.reason)
        print(context.reason)
        if not context.verified:
            raise RuntimeError(f"A股日历未核实：{context.reason}")
        if not context.should_generate:
            report.update(status="skipped", image_status="not_required")
            return False
        start_date, end_date, title = _target_valuation_window(context)
        report.update(start_date=start_date, end_date=end_date)
        print(f"目标估值区间: {start_date} 至 {end_date}；A股日历来源: {context.calendar_source}")
        # 显式暴露缺失/损坏缓存，不让历史读取的兼容性空表掩盖应出图失败。
        payload = json.loads(Path(cache_file or FUND_ESTIMATE_CACHE).read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or not isinstance(payload.get("records", {}), dict):
            raise RuntimeError("基金估算缓存结构无效")
        funds, benchmarks, valuation_dates = _load_overseas_daily_records(start_date, end_date, context.today.isoformat(), cache_file)
        _update_data_diagnostics(report, funds, benchmarks, cache_file)
        print(f"基金有效日期: {', '.join(valuation_dates) or '无'}；基金记录 {len(funds)} 条 / {report['fund_count']} 只")
        print(f"基准有效日期: {', '.join(report['benchmark_valid_dates']) or '无有效数据'}；基准记录 {len(benchmarks)} 条")
        if not valuation_dates:
            raise RuntimeError(f"目标海外基金无有效收盘估算；{report['missing_reason']}")
        title = _build_title(context, valuation_dates, title)
        ensure_runtime_dirs()
        if context.post_holiday_trade_day == 1:
            status = _publish_image(lambda path: _save_daily_images(funds, benchmarks, title, path))
        else:
            summary = build_cumulative_dataframe(funds)
            if summary.empty:
                raise RuntimeError("目标海外基金累计结果为空")
            benchmark_summary = _format_benchmark_summary_labels(build_benchmark_cumulative_dataframe(benchmarks), valuation_dates)
            status = _publish_image(lambda path: _save_safe_image(summary, benchmark_summary, title, path))
        report.update(status=status, image_status=status)
        print(f"安全版图片{'已生成/更新' if status == 'generated' else '已生成但未变化'}: {SAFE_OUTPUT_FILE}")
        return True
    except Exception as exc:
        report.update(status="failed", error=str(exc), missing_reason=report["missing_reason"] or str(exc))
        raise
    finally:
        # 放在输出末尾，确保总入口保留的日志尾部能读取完整状态和缺口诊断。
        print(RESULT_PREFIX + json.dumps(report, ensure_ascii=False), flush=True)


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
