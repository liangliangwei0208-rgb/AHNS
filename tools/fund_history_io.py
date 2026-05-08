"""
fund_estimate_history_overseas.py
用途
====
读取 cache/fund_estimate_return_cache.json 中的海外基金每日预估收益记录，
按日期区间计算累计预估收益率，并生成风格接近原基金收益表的图片。

支持能力
========
1. 支持读取同一缓存文件中的 benchmark_records。
2. 支持计算配置中的海外基准在同一日期区间内的累计涨跌幅。
3. 保存图片时，在主表下方单独显示基准表，再把合规提示和备注放到最底部。

说明
====
1. 该工具只读取海外基金每日预估收益缓存。
2. 基金缓存 key 为 overseas:基金代码:valuation_date。
3. 指数缓存 key 为 benchmark:指数代码:valuation_date。
4. 同一基金/指数同一 valuation_date 只计入一次，避免周末和美国节假日重复计入。
5. 累计收益率采用复利计算，而不是简单相加。
6. 保存图片时默认不展示最后一列“记录状态”，但 summary_df 内部仍保留该字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.offsetbox import AnchoredOffsetbox, HPacker, TextArea, VPacker

from tools.configs.market_benchmark_configs import MARKET_BENCHMARK_ITEMS
from tools.paths import CACHE_DIR


FUND_ESTIMATE_RETURN_CACHE_FILE = "fund_estimate_return_cache.json"
DATE_FIELD_RUN_DATE_BJ = "run_date_bj"


@dataclass(frozen=True)
class HolidayEstimateWindow:
    """自动识别出的 A 股休市、海外有新估值的累计统计窗口。"""

    should_generate: bool
    start_date: str = ""
    end_date: str = ""
    date_field: str = DATE_FIELD_RUN_DATE_BJ
    date_label: str = ""
    output_suffix: str = ""
    calendar_source: str = ""
    reason: str = ""
    overseas_run_dates: tuple[str, ...] = field(default_factory=tuple)
    overseas_valuation_dates: tuple[str, ...] = field(default_factory=tuple)


def _load_json_cache(filename: str, default=None):
    if default is None:
        default = {}

    path = CACHE_DIR / filename
    if not path.exists():
        return default

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return default


def _setup_chinese_font(force: bool = False) -> None:
    """
    设置 Matplotlib 中文字体。
    """
    candidate_font_paths = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\Deng.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]

    for font_path in candidate_font_paths:
        fp = Path(font_path)
        if fp.exists():
            try:
                font_manager.fontManager.addfont(str(fp))
            except Exception:
                pass

    candidate_font_names = [
        "Microsoft YaHei",
        "SimHei",
        "SimSun",
        "DengXian",
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "WenQuanYi Zen Hei",
        "PingFang SC",
        "Arial Unicode MS",
    ]

    available_font_names = {font.name for font in font_manager.fontManager.ttflist}

    chosen = None
    for name in candidate_font_names:
        if name in available_font_names:
            chosen = name
            break

    if chosen:
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = [chosen, *candidate_font_names, "DejaVu Sans"]
    else:
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = [*candidate_font_names, "DejaVu Sans"]

    plt.rcParams["axes.unicode_minus"] = False


def format_pct(value, digits: int = 2) -> str:
    """
    带正负号的百分数格式化。
    """
    if value is None or pd.isna(value):
        return "无有效数据"
    return f"{float(value):+.{digits}f}%"


def _normalize_date_string(value) -> str:
    """
    日期规范化为 YYYY-MM-DD。
    """
    if value is None:
        return ""

    try:
        dt = pd.to_datetime(str(value), errors="coerce")
        if pd.isna(dt):
            return ""
        return pd.Timestamp(dt).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _load_cache_root(cache_file: str | Path | None = None) -> dict:
    if cache_file is None:
        data = _load_json_cache(FUND_ESTIMATE_RETURN_CACHE_FILE, default={})
    else:
        path = Path(cache_file)
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {}

    return data if isinstance(data, dict) else {}


def _records_to_dataframe(records: dict) -> pd.DataFrame:
    if not isinstance(records, dict) or not records:
        return pd.DataFrame()

    rows = []
    for key, rec in records.items():
        if not isinstance(rec, dict):
            continue
        row = dict(rec)
        row["_cache_key"] = key
        rows.append(row)

    return pd.DataFrame(rows)


def load_fund_estimate_history(cache_file: str | Path | None = None) -> pd.DataFrame:
    """
    读取基金每日预估收益缓存，返回 records DataFrame。
    """
    data = _load_cache_root(cache_file=cache_file)
    df = _records_to_dataframe(data.get("records", {}))
    if df.empty:
        return df

    for col in ["valuation_date", "run_date_bj"]:
        if col in df.columns:
            df[col] = df[col].map(_normalize_date_string)

    if "estimate_return_pct" in df.columns:
        df["estimate_return_pct"] = pd.to_numeric(df["estimate_return_pct"], errors="coerce")

    if "is_final" in df.columns:
        df["is_final"] = df["is_final"].astype(bool)
    else:
        df["is_final"] = False

    if "run_time_bj" in df.columns:
        df["_run_time_dt"] = pd.to_datetime(df["run_time_bj"], errors="coerce")
    else:
        df["_run_time_dt"] = pd.NaT

    return df


def load_benchmark_estimate_history(cache_file: str | Path | None = None) -> pd.DataFrame:
    """
    读取指数基准每日涨跌幅缓存，返回 benchmark_records DataFrame。

    预期记录结构由 get_top10_holdings.py 写入：
        benchmark_records["benchmark:.NDX:2026-04-30"] = {...}
        benchmark_records["benchmark:.INX:2026-04-30"] = {...}
    """
    data = _load_cache_root(cache_file=cache_file)
    df = _records_to_dataframe(data.get("benchmark_records", {}))
    if df.empty:
        return df

    for col in ["valuation_date", "run_date_bj", "trade_date"]:
        if col in df.columns:
            df[col] = df[col].map(_normalize_date_string)

    if "return_pct" in df.columns:
        df["return_pct"] = pd.to_numeric(df["return_pct"], errors="coerce")

    if "is_final" in df.columns:
        df["is_final"] = df["is_final"].astype(bool)
    else:
        df["is_final"] = False

    if "run_time_bj" in df.columns:
        df["_run_time_dt"] = pd.to_datetime(df["run_time_bj"], errors="coerce")
    else:
        df["_run_time_dt"] = pd.NaT

    return df


def _parse_normalized_date(value) -> date | None:
    normalized = _normalize_date_string(value)
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").date()
    except Exception:
        return None


def _get_beijing_today(today=None) -> date:
    if today is not None:
        parsed = _parse_normalized_date(today)
        if parsed is None:
            raise ValueError("today 必须是可解析的日期")
        return parsed

    try:
        return datetime.now(ZoneInfo("Asia/Shanghai")).date()
    except Exception:
        return datetime.now().date()


def _load_a_share_trade_dates_from_akshare() -> tuple[set[str], str]:
    """
    读取 AkShare 的 A 股交易日历。

    只在自动识别节假日窗口时调用；失败会交给本地行情缓存兜底。
    """
    try:
        import akshare as ak

        calendar_df = ak.tool_trade_date_hist_sina()
    except Exception as exc:
        return set(), f"AkShare交易日历不可用: {exc}"

    if calendar_df is None or calendar_df.empty:
        return set(), "AkShare交易日历为空"

    column = "trade_date" if "trade_date" in calendar_df.columns else calendar_df.columns[0]
    trade_dates = {
        normalized
        for normalized in calendar_df[column].map(_normalize_date_string)
        if normalized
    }
    if not trade_dates:
        return set(), "AkShare交易日历未解析到有效日期"

    return trade_dates, "AkShare A股交易日历"


def _load_a_share_trade_dates_from_local_cache(
    cache_dir: str | Path | None = None,
) -> tuple[set[str], str]:
    """
    从本地国内行情 CSV 的首列日期兜底推断 A 股交易日。
    """
    root = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    if not root.exists():
        return set(), f"本地缓存目录不存在: {root}"

    trade_dates: set[str] = set()
    used_files = []
    for path in sorted(root.glob("*_index_daily.csv")):
        if not path.is_file() or path.name.startswith("dot_"):
            continue

        try:
            df = pd.read_csv(path, header=None, usecols=[0], dtype=str)
        except Exception:
            continue

        dates = {
            normalized
            for normalized in df.iloc[:, 0].map(_normalize_date_string)
            if normalized
        }
        if dates:
            trade_dates.update(dates)
            used_files.append(path.name)

    if not trade_dates:
        return set(), "本地国内行情缓存未解析到有效交易日"

    return trade_dates, f"本地国内行情缓存({len(used_files)}个文件)"


def _load_a_share_trade_dates(
    use_akshare: bool = True,
    cache_dir: str | Path | None = None,
) -> tuple[set[str], str]:
    akshare_reason = ""
    if use_akshare:
        trade_dates, source = _load_a_share_trade_dates_from_akshare()
        if trade_dates:
            return trade_dates, source
        akshare_reason = source

    trade_dates, source = _load_a_share_trade_dates_from_local_cache(cache_dir=cache_dir)
    if trade_dates:
        if akshare_reason:
            source = f"{source}; {akshare_reason}"
        return trade_dates, source

    if akshare_reason:
        source = f"{source}; {akshare_reason}"
    return set(), source


def load_a_share_trade_dates(
    use_akshare: bool = True,
    cache_dir: str | Path | None = None,
) -> tuple[set[str], str]:
    """
    读取 A 股交易日历，返回 YYYY-MM-DD 字符串集合和来源说明。

    默认优先使用 AkShare 交易日历；失败时退回本地国内行情缓存。
    这个公开入口供节假日窗口脚本复用，避免外部脚本依赖私有函数。
    """
    return _load_a_share_trade_dates(use_akshare=use_akshare, cache_dir=cache_dir)


def _format_month_day(value: str) -> str:
    parsed = _parse_normalized_date(value)
    if parsed is None:
        return str(value)
    return f"{parsed.month}.{parsed.day}"


def format_holiday_estimate_date_label(start_date: str, end_date: str) -> str:
    start_label = _format_month_day(start_date)
    end_label = _format_month_day(end_date)
    return f"{start_label}-{end_label}"


def format_holiday_estimate_output_suffix(start_date: str, end_date: str) -> str:
    start = _normalize_date_string(start_date).replace("-", "")
    end = _normalize_date_string(end_date).replace("-", "")
    return f"{start}_{end}"


def _no_holiday_window(reason: str, calendar_source: str = "") -> HolidayEstimateWindow:
    return HolidayEstimateWindow(
        should_generate=False,
        calendar_source=calendar_source,
        reason=reason,
    )


def _find_a_share_closed_window_start(
    today: date,
    trade_dates: set[str],
    max_lookback_days: int,
) -> str:
    start = today
    for _ in range(max(1, max_lookback_days)):
        previous_day = start - timedelta(days=1)
        if previous_day.isoformat() in trade_dates:
            break
        start = previous_day
    return start.isoformat()


def _filter_overseas_records_by_run_date(
    df: pd.DataFrame,
    start_date: str,
    end_date: str,
    value_column: str,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()
    if "market_group" in out.columns:
        out = out[out["market_group"].astype(str) == "overseas"]
    if DATE_FIELD_RUN_DATE_BJ not in out.columns:
        return pd.DataFrame()
    if value_column in out.columns:
        out = out.dropna(subset=[value_column])

    out = out[
        (out[DATE_FIELD_RUN_DATE_BJ] >= start_date)
        & (out[DATE_FIELD_RUN_DATE_BJ] <= end_date)
    ].copy()
    return out


def _unique_sorted_dates(values) -> tuple[str, ...]:
    dates = sorted({x for x in (_normalize_date_string(v) for v in values) if x})
    return tuple(dates)


def detect_overseas_holiday_estimate_window(
    today=None,
    cache_file: str | Path | None = None,
    use_akshare: bool = True,
    max_a_share_lookback_days: int = 20,
    max_overseas_valuation_lag_days: int = 1,
) -> HolidayEstimateWindow:
    """
    自动识别 A 股休市且海外有新估值时的累计统计窗口。

    A 股交易日优先使用 AkShare 日历；失败时用本地国内行情缓存兜底。
    海外是否有新估值只看 main.py 已写入的缓存，不重新拉行情。
    """
    today_date = _get_beijing_today(today=today)
    today_str = today_date.isoformat()

    trade_dates, calendar_source = _load_a_share_trade_dates(use_akshare=use_akshare)
    if not trade_dates:
        return _no_holiday_window(
            "无法判断A股交易日历，未生成节假日累计图。请检查网络或先运行 main.py 更新本地缓存。",
            calendar_source=calendar_source,
        )

    if today_str in trade_dates:
        return _no_holiday_window(
            f"{today_str} 是A股交易日，不属于“A股休市、海外有新估值”的累计场景，未生成图片。",
            calendar_source=calendar_source,
        )

    start_date = _find_a_share_closed_window_start(
        today=today_date,
        trade_dates=trade_dates,
        max_lookback_days=max_a_share_lookback_days,
    )
    end_date = today_str

    fund_df = load_fund_estimate_history(cache_file=cache_file)
    benchmark_df = load_benchmark_estimate_history(cache_file=cache_file)
    interval_fund_df = _filter_overseas_records_by_run_date(
        fund_df,
        start_date=start_date,
        end_date=end_date,
        value_column="estimate_return_pct",
    )
    interval_benchmark_df = _filter_overseas_records_by_run_date(
        benchmark_df,
        start_date=start_date,
        end_date=end_date,
        value_column="return_pct",
    )

    if interval_fund_df.empty:
        return _no_holiday_window(
            f"{start_date} 至 {end_date} 未发现可用海外基金缓存，未生成图片。请先运行 main.py。",
            calendar_source=calendar_source,
        )

    if interval_benchmark_df.empty:
        return _no_holiday_window(
            f"{start_date} 至 {end_date} 未发现可用海外指数基准缓存，未生成图片。请先运行 main.py。",
            calendar_source=calendar_source,
        )

    fund_run_dates = set(_unique_sorted_dates(interval_fund_df[DATE_FIELD_RUN_DATE_BJ]))
    benchmark_run_dates = set(_unique_sorted_dates(interval_benchmark_df[DATE_FIELD_RUN_DATE_BJ]))
    common_run_dates = sorted(fund_run_dates & benchmark_run_dates)
    if not common_run_dates:
        return _no_holiday_window(
            f"{start_date} 至 {end_date} 未发现同一北京时间运行日的海外基金和指数基准缓存，未生成图片。请先运行 main.py。",
            calendar_source=calendar_source,
        )

    has_today_cache = today_str in common_run_dates
    selected_run_date = today_str if has_today_cache else common_run_dates[-1]
    selected_benchmark_df = interval_benchmark_df[
        interval_benchmark_df[DATE_FIELD_RUN_DATE_BJ] == selected_run_date
    ]

    valuation_dates = _unique_sorted_dates(selected_benchmark_df.get("valuation_date", []))
    latest_valuation_date = valuation_dates[-1] if valuation_dates else ""
    latest_valuation = _parse_normalized_date(latest_valuation_date)
    if latest_valuation is None:
        return _no_holiday_window(
            f"北京时间 {selected_run_date} 的海外指数基准缓存缺少有效估值日，未生成图片。",
            calendar_source=calendar_source,
        )

    valuation_lag_days = (today_date - latest_valuation).days
    if has_today_cache and valuation_lag_days > max_overseas_valuation_lag_days:
        return _no_holiday_window(
            (
                f"海外最新估值日为 {latest_valuation_date}，与北京时间 {today_str} 间隔 "
                f"{valuation_lag_days} 天，未判断为新的海外交易估值，未生成图片。"
            ),
            calendar_source=calendar_source,
        )

    date_label = format_holiday_estimate_date_label(start_date, end_date)
    if has_today_cache:
        reason = (
            f"自动识别区间: {start_date} 至 {end_date}; "
            f"A股日历来源: {calendar_source}; 最新海外估值日: {latest_valuation_date}"
        )
    else:
        reason = (
            "今日无新增海外估值缓存，已复用当前休市区间内最近有效缓存生成区间观察图。"
            f"自动识别区间: {start_date} 至 {end_date}; "
            f"A股日历来源: {calendar_source}; 最近缓存运行日: {selected_run_date}; "
            f"最新海外估值日: {latest_valuation_date}"
        )
    return HolidayEstimateWindow(
        should_generate=True,
        start_date=start_date,
        end_date=end_date,
        date_field=DATE_FIELD_RUN_DATE_BJ,
        date_label=date_label,
        output_suffix=format_holiday_estimate_output_suffix(start_date, end_date),
        calendar_source=calendar_source,
        reason=reason,
        overseas_run_dates=_unique_sorted_dates(interval_fund_df[DATE_FIELD_RUN_DATE_BJ]),
        overseas_valuation_dates=_unique_sorted_dates(interval_fund_df.get("valuation_date", [])),
    )


def get_fund_estimate_records(
    start_date: str,
    end_date: str,
    fund_codes=None,
    market_group: str = "overseas",
    date_field: str = "valuation_date",
    include_intraday: bool = True,
    require_final: bool = False,
    cache_file: str | Path | None = None,
) -> pd.DataFrame:
    """
    按日期区间读取每日预估收益记录。

    参数
    ----
    date_field:
        "valuation_date"：按实际估值交易日筛选；
        "run_date_bj"：按北京时间运行日期筛选，适合五一假期这种场景。
    include_intraday:
        True：如果某估值日还没有 final，也允许使用 intraday；
        False：只使用 final。
    require_final:
        True：只保留 final 记录。
    """
    date_field = str(date_field).strip()
    if date_field not in {"valuation_date", "run_date_bj"}:
        raise ValueError("date_field 只能是 'valuation_date' 或 'run_date_bj'")

    start = _normalize_date_string(start_date)
    end = _normalize_date_string(end_date)
    if not start or not end:
        raise ValueError("start_date / end_date 必须是可解析的日期。")

    df = load_fund_estimate_history(cache_file=cache_file)
    if df.empty:
        return df

    if "market_group" in df.columns:
        df = df[df["market_group"].astype(str) == str(market_group)]

    if fund_codes is None and str(market_group).strip() == "overseas":
        try:
            from tools.fund_universe import HAIWAI_FUND_CODES

            fund_codes = HAIWAI_FUND_CODES
        except Exception:
            fund_codes = None

    if fund_codes is not None:
        if isinstance(fund_codes, str):
            fund_codes = [fund_codes]
        code_set = {str(x).strip().zfill(6) for x in fund_codes}
        df = df[df["fund_code"].astype(str).str.zfill(6).isin(code_set)]

    if require_final or not include_intraday:
        df = df[df["is_final"] == True]

    df = df.dropna(subset=["estimate_return_pct"]).copy()

    if date_field not in df.columns:
        return pd.DataFrame()

    df = df[(df[date_field] >= start) & (df[date_field] <= end)].copy()
    if df.empty:
        return df

    # 防御性去重：同一基金同一 valuation_date 只保留一条。
    # 优先 final，其次保留运行时间更晚的一条。
    df["_final_rank"] = df["is_final"].astype(int)
    df = df.sort_values(
        by=["fund_code", "valuation_date", "_final_rank", "_run_time_dt"],
        ascending=[True, True, True, True],
        na_position="first",
    )
    df = df.groupby(["fund_code", "valuation_date"], as_index=False).tail(1)
    df = df.sort_values(["fund_code", "valuation_date"]).reset_index(drop=True)

    return df


def get_benchmark_estimate_records(
    start_date: str,
    end_date: str,
    symbols=None,
    market_group: str = "overseas",
    date_field: str = "valuation_date",
    include_intraday: bool = True,
    require_final: bool = False,
    cache_file: str | Path | None = None,
) -> pd.DataFrame:
    """
    按日期区间读取指数每日涨跌幅记录。

    默认 symbols=None 时读取缓存中已有的全部基准；通常是 .NDX 和 .INX。
    """
    date_field = str(date_field).strip()
    if date_field not in {"valuation_date", "run_date_bj"}:
        raise ValueError("date_field 只能是 'valuation_date' 或 'run_date_bj'")

    start = _normalize_date_string(start_date)
    end = _normalize_date_string(end_date)
    if not start or not end:
        raise ValueError("start_date / end_date 必须是可解析的日期。")

    df = load_benchmark_estimate_history(cache_file=cache_file)
    if df.empty:
        return df

    if "market_group" in df.columns:
        df = df[df["market_group"].astype(str) == str(market_group)]

    if symbols is not None:
        if isinstance(symbols, str):
            symbols = [symbols]
        symbol_set = {str(x).strip().upper() for x in symbols}
        df = df[df["symbol"].astype(str).str.upper().isin(symbol_set)]

    if require_final or not include_intraday:
        df = df[df["is_final"] == True]

    df = df.dropna(subset=["return_pct"]).copy()

    if date_field not in df.columns:
        return pd.DataFrame()

    df = df[(df[date_field] >= start) & (df[date_field] <= end)].copy()
    if df.empty:
        return df

    df["_final_rank"] = df["is_final"].astype(int)
    df = df.sort_values(
        by=["symbol", "valuation_date", "_final_rank", "_run_time_dt"],
        ascending=[True, True, True, True],
        na_position="first",
    )
    df = df.groupby(["symbol", "valuation_date"], as_index=False).tail(1)
    df = df.sort_values(["symbol", "valuation_date"]).reset_index(drop=True)

    return df


def build_cumulative_dataframe(daily_df: pd.DataFrame) -> pd.DataFrame:
    """
    根据每日基金记录计算区间累计预估收益率。
    """
    if daily_df is None or daily_df.empty:
        return pd.DataFrame(
            columns=[
                "序号",
                "基金代码",
                "基金名称",
                "区间累计预估收益率",
                "有效估值日数",
                "起始估值日",
                "结束估值日",
                "记录状态",
            ]
        )

    rows = []
    for fund_code, g in daily_df.groupby("fund_code"):
        g = g.sort_values("valuation_date").copy()
        returns = pd.to_numeric(g["estimate_return_pct"], errors="coerce").dropna()

        if returns.empty:
            continue

        cumulative = (np.prod(1.0 + returns.to_numpy(dtype=float) / 100.0) - 1.0) * 100.0

        is_all_final = bool(g["is_final"].all()) if "is_final" in g.columns else False
        has_final = bool(g["is_final"].any()) if "is_final" in g.columns else False

        if is_all_final:
            status = "final"
        elif has_final:
            status = "含临时"
        else:
            status = "intraday"

        fund_name = ""
        if "fund_name" in g.columns and g["fund_name"].notna().any():
            fund_name = str(g["fund_name"].dropna().iloc[-1])

        rows.append(
            {
                "基金代码": str(fund_code).zfill(6),
                "基金名称": fund_name,
                "区间累计预估收益率": float(cumulative),
                "有效估值日数": int(g["valuation_date"].nunique()),
                "起始估值日": str(g["valuation_date"].min()),
                "结束估值日": str(g["valuation_date"].max()),
                "记录状态": status,
            }
        )

    out = pd.DataFrame(rows)

    if out.empty:
        return build_cumulative_dataframe(pd.DataFrame())

    out = out.sort_values("区间累计预估收益率", ascending=False).reset_index(drop=True)
    out.insert(0, "序号", range(1, len(out) + 1))

    return out


def _enabled_benchmark_config_rows() -> list[dict]:
    rows = []
    for order, item in enumerate(MARKET_BENCHMARK_ITEMS, start=1):
        if not isinstance(item, dict) or not bool(item.get("enabled", True)):
            continue
        label = str(item.get("label", "")).strip()
        ticker = str(item.get("ticker", "")).strip().upper()
        if not label or not ticker:
            continue
        rows.append({"order": order, "label": label, "symbol": ticker})
    return rows


def _empty_benchmark_cumulative_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "指数名称",
            "指数代码",
            "区间累计涨跌幅",
            "有效估值日数",
            "起始估值日",
            "结束估值日",
            "记录状态",
        ]
    )


def build_benchmark_cumulative_dataframe(benchmark_daily_df: pd.DataFrame) -> pd.DataFrame:
    """
    根据每日指数记录计算区间累计涨跌幅。
    """
    config_rows = _enabled_benchmark_config_rows()
    order_map = {row["symbol"]: row["order"] for row in config_rows}
    label_map = {row["symbol"]: row["label"] for row in config_rows}
    config_symbols = set(order_map)
    config_labels = {row["label"] for row in config_rows}

    if benchmark_daily_df is None or benchmark_daily_df.empty:
        out = _empty_benchmark_cumulative_dataframe()
        if not config_rows:
            return out
        rows = []
        for row in config_rows:
            rows.append({
                "指数名称": row["label"],
                "指数代码": row["symbol"],
                "区间累计涨跌幅": None,
                "有效估值日数": 0,
                "起始估值日": "",
                "结束估值日": "",
                "记录状态": "无有效数据",
            })
        return pd.DataFrame(rows, columns=out.columns)

    rows = []
    for symbol, g in benchmark_daily_df.groupby("symbol"):
        symbol_norm = str(symbol).strip().upper()
        g = g.sort_values("valuation_date").copy()
        returns = pd.to_numeric(g["return_pct"], errors="coerce").dropna()

        if returns.empty:
            continue

        cumulative = (np.prod(1.0 + returns.to_numpy(dtype=float) / 100.0) - 1.0) * 100.0

        is_all_final = bool(g["is_final"].all()) if "is_final" in g.columns else False
        has_final = bool(g["is_final"].any()) if "is_final" in g.columns else False
        if is_all_final:
            status = "final"
        elif has_final:
            status = "含临时"
        else:
            status = "intraday"

        label = label_map.get(symbol_norm, str(symbol))
        if "label" in g.columns and g["label"].notna().any():
            label = str(g["label"].dropna().iloc[-1])
        if symbol_norm not in config_symbols and label in config_labels:
            continue

        rows.append(
            {
                "指数名称": label,
                "指数代码": symbol_norm,
                "区间累计涨跌幅": float(cumulative),
                "有效估值日数": int(g["valuation_date"].nunique()),
                "起始估值日": str(g["valuation_date"].min()),
                "结束估值日": str(g["valuation_date"].max()),
                "记录状态": status,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return build_benchmark_cumulative_dataframe(pd.DataFrame())

    existing_symbols = set(out["指数代码"].astype(str).str.upper())
    missing_rows = []
    for row in config_rows:
        if row["symbol"] in existing_symbols:
            continue
        missing_rows.append({
            "指数名称": row["label"],
            "指数代码": row["symbol"],
            "区间累计涨跌幅": None,
            "有效估值日数": 0,
            "起始估值日": "",
            "结束估值日": "",
            "记录状态": "无有效数据",
        })
    if missing_rows:
        out = pd.DataFrame(
            [*out.to_dict("records"), *missing_rows],
            columns=out.columns,
        )

    out["_order"] = out["指数代码"].astype(str).str.upper().map(lambda x: order_map.get(x, 99))
    out = out.sort_values(["_order", "指数名称"]).drop(columns=["_order"]).reset_index(drop=True)
    return out


def print_cumulative_estimate_table(
    summary_df: pd.DataFrame,
    title: str | None = None,
    pct_digits: int = 2,
    benchmark_summary_df: pd.DataFrame | None = None,
    display_column_names: dict[str, str] | None = None,
):
    """
    打印累计预估收益表。
    """
    if title is None:
        title = "区间累计预估收益率"

    show_df = summary_df.copy()
    if "区间累计预估收益率" in show_df.columns:
        show_df["区间累计预估收益率"] = show_df["区间累计预估收益率"].map(
            lambda x: format_pct(x, digits=pct_digits)
        )
    if display_column_names:
        show_df = show_df.rename(columns=display_column_names)

    print("=" * 100)
    print(title)
    print("=" * 100)
    print(show_df.to_string(index=False))

    if benchmark_summary_df is not None and not benchmark_summary_df.empty:
        bench_df = benchmark_summary_df.copy()
        bench_df["区间累计涨跌幅"] = bench_df["区间累计涨跌幅"].map(
            lambda x: format_pct(x, digits=pct_digits)
        )
        print("-" * 100)
        print("指数基准区间累计涨跌幅")
        print(bench_df.to_string(index=False))

    print("=" * 100)


def _iter_benchmark_footer_items(benchmark_summary_df: pd.DataFrame | None):
    if benchmark_summary_df is None or benchmark_summary_df.empty:
        return []

    items = []
    for _, row in benchmark_summary_df.iterrows():
        label = str(row.get("指数名称", "基准")).strip() or "基准"
        symbol = str(row.get("指数代码", "")).strip()
        value = row.get("区间累计涨跌幅")
        effective_days = row.get("有效估值日数", "")
        start = str(row.get("起始估值日", "")).strip()
        end = str(row.get("结束估值日", "")).strip()
        items.append(
            {
                "label": label,
                "symbol": symbol,
                "return_pct": value,
                "effective_days": effective_days,
                "start": start,
                "end": end,
            }
        )
    return items


def _cumulative_benchmark_columns(include_symbol: bool = False) -> list[str]:
    if include_symbol:
        return ["序号", "指数代码", "指数名称", "区间模型观察", "有效估值日数", "起始估值日", "结束估值日"]
    return ["序号", "指数名称", "区间模型观察", "有效估值日数", "起始估值日", "结束估值日"]


def _build_cumulative_benchmark_table_rows(
    benchmark_summary_df: pd.DataFrame | None,
    pct_digits: int = 2,
    include_symbol: bool = False,
):
    benchmark_items = _iter_benchmark_footer_items(benchmark_summary_df)
    columns = _cumulative_benchmark_columns(include_symbol=include_symbol)
    rows = []
    raw_values = []
    for index, item in enumerate(benchmark_items, start=1):
        value = item.get("return_pct")
        try:
            value_float = float(value)
        except Exception:
            value_float = None
        if value_float is not None and pd.isna(value_float):
            value_float = None

        raw_values.append(value_float)
        effective_days = item.get("effective_days", "")
        try:
            effective_days = int(effective_days)
        except Exception:
            effective_days = 0 if value_float is None else ""
        start = str(item.get("start", "")).strip()
        end = str(item.get("end", "")).strip()

        row = {
            "序号": index,
            "指数代码": str(item.get("symbol", "")).strip(),
            "指数名称": str(item.get("label", "基准")).strip() or "基准",
            "区间模型观察": format_pct(value_float, digits=pct_digits) if value_float is not None else "无有效数据",
            "有效估值日数": effective_days,
            "起始估值日": start or "--",
            "结束估值日": end or "--",
        }
        rows.append({col: row.get(col, "") for col in columns})

    if not rows:
        return pd.DataFrame(columns=columns), []

    return pd.DataFrame(rows, columns=columns), raw_values


def _draw_benchmark_table(
    ax,
    benchmark_df: pd.DataFrame,
    raw_values,
    bbox,
    *,
    fontsize,
    header_bg,
    header_text_color,
    grid_color,
    up_color,
    down_color,
    neutral_color,
    column_widths=None,
):
    table = ax.table(
        cellText=benchmark_df.values,
        colLabels=benchmark_df.columns,
        cellLoc="center",
        colLoc="center",
        bbox=bbox,
        zorder=2,
    )
    table.auto_set_font_size(False)
    table.set_fontsize(fontsize)
    table.scale(1.0, 1.18)

    value_col_idx = None
    for candidate in ("模型观察", "区间模型观察"):
        if candidate in benchmark_df.columns:
            value_col_idx = list(benchmark_df.columns).index(candidate)
            break
    default_width_by_name = {
        "序号": 0.08,
        "指数名称": 0.34,
        "模型观察": 0.20,
        "基准日或区间": 0.38,
        "指数代码": 0.10,
        "区间模型观察": 0.18,
        "有效估值日数": 0.12,
        "起始估值日": 0.13,
        "结束估值日": 0.13,
    }
    if column_widths is not None and len(column_widths) != len(benchmark_df.columns):
        column_widths = None

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(grid_color)
        cell.set_linewidth(0.8)
        if row == 0:
            cell.set_facecolor(header_bg)
            cell.set_text_props(color=header_text_color, weight="bold")
        else:
            cell.set_facecolor("white")
            if value_col_idx is not None and col == value_col_idx:
                raw_val = raw_values[row - 1] if row - 1 < len(raw_values) else None
                if raw_val is None or pd.isna(raw_val):
                    cell.get_text().set_color(neutral_color)
                elif float(raw_val) > 0:
                    cell.get_text().set_color(up_color)
                    cell.get_text().set_weight("bold")
                elif float(raw_val) < 0:
                    cell.get_text().set_color(down_color)
                    cell.get_text().set_weight("bold")
                else:
                    cell.get_text().set_color(neutral_color)

        if col < len(benchmark_df.columns):
            name = benchmark_df.columns[col]
            if column_widths is not None:
                cell.set_width(column_widths[col])
            elif name in default_width_by_name:
                cell.set_width(default_width_by_name[name])

    return table


def save_cumulative_estimate_table_image(
    summary_df: pd.DataFrame,
    output_file: str = "output/fund_cumulative_estimate_table.png",
    title: str | None = None,
    dpi: int = 200,
    watermark_text: str = "鱼师AHNS",
    watermark_alpha: float = 0.15,
    watermark_fontsize: int = 32,
    watermark_rotation: int = 28,
    watermark_rows: int = 5,
    watermark_cols: int = 4,
    watermark_color: str = "#000000FC",
    up_color: str = "red",
    down_color: str = "green",
    neutral_color: str = "black",
    pct_digits: int = 2,
    header_bg: str = "#3f4d66",
    header_text_color: str = "white",
    grid_color: str = "#d9d9d9",
    figure_width=None,
    row_height: float = 0.55,
    footnote_text: str = "依据基金季度报告前十大持仓股及指数估算，仅供学习记录，不构成投资建议；最终以基金公司更新为准。",
    footnote_color: str = "#666666",
    footnote_fontsize: int = 14,
    compliance_notice_text: str = "个人模型，数据来源于网络公开资料，不构成任何投资建议",
    compliance_notice_color: str = "#2f3b52",
    compliance_notice_fontsize: int = 35,
    compliance_notice_fontweight: str = "bold",
    title_fontsize: int = 20,
    title_color: str = "black",
    title_fontweight: str = "bold",
    pad_inches: float = 0.14,
    hide_status_column: bool = True,
    benchmark_summary_df: pd.DataFrame | None = None,
    show_benchmark_footer: bool = True,
    benchmark_footer_fontsize: int = 14,
    display_column_names: dict[str, str] | None = None,
):
    """
    保存累计预估收益表格图片。风格尽量贴近原基金收益表。

    参数
    ----
    hide_status_column:
        True：保存图片时隐藏“记录状态”列；summary_df 本身不受影响。
        False：保存图片时保留“记录状态”列。
    benchmark_summary_df:
        build_benchmark_cumulative_dataframe() 返回的指数累计结果。
    show_benchmark_footer:
        True：在表格下方、备注上方显示指数区间累计涨跌幅。
    """
    _setup_chinese_font()

    if title is None:
        title = "区间累计预估收益率"

    output_path = Path(output_file)
    if output_path.parent and str(output_path.parent) != ".":
        output_path.parent.mkdir(parents=True, exist_ok=True)

    cumulative_col_name = "区间累计预估收益率"
    display_column_names = display_column_names or {}
    cumulative_display_col_name = display_column_names.get(
        cumulative_col_name, cumulative_col_name
    )

    table_df = summary_df.copy()

    # 只影响图片展示：删除最后一列“记录状态”。
    # 内部 summary_df 仍保留该列，用于判断 final / intraday，不破坏后续逻辑。
    if hide_status_column:
        table_df = table_df.drop(columns=["记录状态"], errors="ignore")

    if table_df.empty:
        empty_row = {
            "序号": "",
            "基金代码": "",
            "基金名称": "无有效缓存记录",
            cumulative_col_name: None,
            "有效估值日数": "",
            "起始估值日": "",
            "结束估值日": "",
        }
        if not hide_status_column:
            empty_row["记录状态"] = ""
        table_df = pd.DataFrame([empty_row])

    raw_returns = table_df.get(cumulative_col_name, pd.Series([None] * len(table_df))).copy()

    if cumulative_col_name in table_df.columns:
        table_df[cumulative_col_name] = table_df[cumulative_col_name].map(
            lambda x: format_pct(x, digits=pct_digits)
        )
    table_df = table_df.rename(columns=display_column_names)

    include_benchmark_symbol = "基金代码" in table_df.columns
    benchmark_table_df, benchmark_raw_values = (
        _build_cumulative_benchmark_table_rows(
            benchmark_summary_df,
            pct_digits=pct_digits,
            include_symbol=include_benchmark_symbol,
        )
        if show_benchmark_footer
        else (pd.DataFrame(columns=_cumulative_benchmark_columns(include_symbol=include_benchmark_symbol)), [])
    )
    has_benchmark_footer = not benchmark_table_df.empty

    nrows = len(table_df)
    ncols = len(table_df.columns)
    has_compliance_notice = bool(str(compliance_notice_text).strip()) if compliance_notice_text else False

    benchmark_height_units = row_height * (len(benchmark_table_df) + 1) if has_benchmark_footer else 0.0
    footer_height_units = 0.85 if has_compliance_notice and footnote_text else (
        0.55 if (has_compliance_notice or footnote_text) else 0.25
    )
    fig_h = max(
        1.8,
        row_height * (nrows + 1)
        + benchmark_height_units
        + footer_height_units
        + (0.35 if has_benchmark_footer else 0.0),
    )
    if figure_width is None:
        if hide_status_column:
            fig_w = 14.0 if ncols >= 7 else 13.0
        else:
            fig_w = 15.0 if ncols >= 7 else 13.5
    else:
        fig_w = figure_width

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.axis("off")
    fig.subplots_adjust(left=0.015, right=0.985, top=0.985, bottom=0.015)

    top_reserved = 0.08 if title else 0.03
    footer_reserved = 0.070 if has_compliance_notice and footnote_text else (
        0.050 if (has_compliance_notice or footnote_text) else 0.025
    )
    benchmark_reserved = min(max(benchmark_height_units / max(fig_h, 0.01), 0.16), 0.26) if has_benchmark_footer else 0.0
    benchmark_gap = 0.010 if has_benchmark_footer else 0.0
    bottom_reserved = footer_reserved + benchmark_reserved + benchmark_gap

    table_bbox = [0.02, bottom_reserved, 0.96, 1 - top_reserved - bottom_reserved]
    benchmark_bbox = [0.08, footer_reserved, 0.84, benchmark_reserved] if has_benchmark_footer else None

    table = ax.table(
        cellText=table_df.values,
        colLabels=table_df.columns,
        cellLoc="center",
        colLoc="center",
        bbox=table_bbox,
        zorder=2,
    )

    table.auto_set_font_size(False)
    table.set_fontsize(15)
    table.scale(1.0, 1.22)

    ret_col_idx = (
        list(table_df.columns).index(cumulative_display_col_name)
        if cumulative_display_col_name in table_df.columns
        else None
    )

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(grid_color)
        cell.set_linewidth(0.8)

        if row == 0:
            cell.set_facecolor(header_bg)
            cell.set_text_props(color=header_text_color, weight="bold")
        else:
            cell.set_facecolor("white")

            if ret_col_idx is not None and col == ret_col_idx:
                raw_val = raw_returns.iloc[row - 1] if row - 1 < len(raw_returns) else None
                try:
                    raw_val = float(raw_val)
                except Exception:
                    raw_val = None

                if raw_val is not None and not pd.isna(raw_val):
                    if raw_val > 0:
                        cell.get_text().set_color(up_color)
                        cell.get_text().set_weight("bold")
                    elif raw_val < 0:
                        cell.get_text().set_color(down_color)
                        cell.get_text().set_weight("bold")
                    else:
                        cell.get_text().set_color(neutral_color)
                else:
                    cell.get_text().set_color(neutral_color)

    col_width_by_name = {
        "序号": 0.06,
        "基金代码": 0.10,
        "基金名称": 0.39 if hide_status_column else 0.37,
        "区间累计预估收益率": 0.18 if hide_status_column else 0.17,
        "区间模型估算观察": 0.18 if hide_status_column else 0.17,
        "有效估值日数": 0.12,
        "起始估值日": 0.13,
        "结束估值日": 0.13,
    }

    if not hide_status_column:
        col_width_by_name["记录状态"] = 0.10

    for (row, col), cell in table.get_celld().items():
        if col < len(table_df.columns):
            name = table_df.columns[col]
            if name in col_width_by_name:
                cell.set_width(col_width_by_name[name])

    same_column_count_as_main = has_benchmark_footer and len(benchmark_table_df.columns) == len(table_df.columns)
    benchmark_column_widths = None
    if same_column_count_as_main:
        # 如果累计图的基准表和主表列数一致，则基准表按位置复用主表列宽，
        # 保持上下表格边界尽量一致。
        benchmark_bbox = list(table_bbox)
        benchmark_bbox[1] = footer_reserved
        benchmark_bbox[3] = benchmark_reserved
        benchmark_column_widths = [
            table.get_celld()[(0, col)].get_width()
            for col in range(len(table_df.columns))
        ]

    benchmark_table_artist = None
    separator_artists = []
    if has_benchmark_footer and benchmark_bbox is not None:
        benchmark_table_artist = _draw_benchmark_table(
            ax,
            benchmark_table_df,
            benchmark_raw_values,
            benchmark_bbox,
            fontsize=15,
            header_bg=header_bg,
            header_text_color=header_text_color,
            grid_color=grid_color,
            up_color=up_color,
            down_color=down_color,
            neutral_color=neutral_color,
            column_widths=benchmark_column_widths,
        )
        separator_y = benchmark_bbox[1] + benchmark_bbox[3] + max(benchmark_gap * 0.5, 0.003)
        separator_artists.extend(
            ax.plot(
                [table_bbox[0], table_bbox[0] + table_bbox[2]],
                [separator_y, separator_y],
                transform=ax.transAxes,
                color=header_bg,
                linewidth=1.2,
                alpha=0.45,
                zorder=4,
            )
        )

    # 水印
    for i in range(max(1, int(watermark_rows))):
        for j in range(max(1, int(watermark_cols))):
            x = table_bbox[0] + table_bbox[2] * (j + 0.5) / max(1, int(watermark_cols))
            y = table_bbox[1] + table_bbox[3] * (i + 0.5) / max(1, int(watermark_rows))
            ax.text(
                x,
                y,
                watermark_text,
                transform=ax.transAxes,
                ha="center",
                va="center",
                fontsize=watermark_fontsize,
                color=watermark_color,
                alpha=watermark_alpha,
                rotation=watermark_rotation,
                zorder=3,
            )

    # 标题、指数基准和备注
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    table_bbox_window = table.get_window_extent(renderer=renderer)
    table_bbox_fig = table_bbox_window.transformed(fig.transFigure.inverted())
    benchmark_bottom = table_bbox_fig.y0
    if benchmark_table_artist is not None:
        benchmark_bbox_window = benchmark_table_artist.get_window_extent(renderer=renderer)
        benchmark_bbox_fig = benchmark_bbox_window.transformed(fig.transFigure.inverted())
        benchmark_bottom = benchmark_bbox_fig.y0

    extra_artists = [table]

    if title:
        title_artist = fig.text(
            0.5,
            min(0.985, table_bbox_fig.y1 + 0.018),
            title,
            ha="center",
            va="bottom",
            fontsize=title_fontsize,
            color=title_color,
            fontweight=title_fontweight,
        )
        extra_artists.append(title_artist)

    bottom_block_artist = None
    bottom_children = []

    if has_compliance_notice:
        bottom_children.append(
            TextArea(
                str(compliance_notice_text).strip(),
                textprops={
                    "fontsize": compliance_notice_fontsize,
                    "color": compliance_notice_color,
                    "fontweight": compliance_notice_fontweight,
                },
            )
        )

    if footnote_text:
        footnote_display_text = str(footnote_text).strip()
        if footnote_display_text and not footnote_display_text.startswith("备注"):
            footnote_display_text = f"备注：{footnote_display_text}"
        bottom_children.append(
            TextArea(
                footnote_display_text,
                textprops={"fontsize": footnote_fontsize, "color": footnote_color},
            )
        )

    if bottom_children:
        bottom_pack = VPacker(children=bottom_children, align="center", pad=0, sep=4)
        table_height = max(table_bbox_fig.y1 - table_bbox_fig.y0, 0.01)
        bottom_block_y = max(
            benchmark_bottom - max(min(table_height * 0.012, 0.010), 0.006),
            0.030,
        )
        bottom_block_artist = AnchoredOffsetbox(
            loc="upper center",
            child=bottom_pack,
            pad=0.0,
            frameon=False,
            bbox_to_anchor=(0.5, bottom_block_y),
            bbox_transform=fig.transFigure,
            borderpad=0.0,
        )
        fig.add_artist(bottom_block_artist)
        extra_artists.append(bottom_block_artist)
    extra_artists.extend(separator_artists)

    fig.savefig(
        output_file,
        dpi=dpi,
        bbox_inches="tight",
        bbox_extra_artists=extra_artists,
        pad_inches=pad_inches,
    )
    plt.close(fig)

    return output_file


def build_cumulative_estimate_table(
    start_date: str,
    end_date: str,
    fund_codes=None,
    market_group: str = "overseas",
    date_field: str = "valuation_date",
    include_intraday: bool = True,
    require_final: bool = False,
    cache_file: str | Path | None = None,
    output_file: str = "output/fund_cumulative_estimate_table.png",
    title: str | None = None,
    print_table: bool = True,
    save_table: bool = True,
    pct_digits: int = 2,
    include_benchmark_footer: bool = True,
    benchmark_symbols=None,
    return_benchmark: bool = False,
    **image_kwargs,
):
    """
    一站式入口：读取缓存、计算区间累计预估收益、打印并保存图片。

    返回：
        默认返回 summary_df, daily_df；
        return_benchmark=True 时返回 summary_df, daily_df, benchmark_summary_df。
    """
    daily_df = get_fund_estimate_records(
        start_date=start_date,
        end_date=end_date,
        fund_codes=fund_codes,
        market_group=market_group,
        date_field=date_field,
        include_intraday=include_intraday,
        require_final=require_final,
        cache_file=cache_file,
    )

    summary_df = build_cumulative_dataframe(daily_df)

    benchmark_daily_df = pd.DataFrame()
    benchmark_summary_df = pd.DataFrame()
    if include_benchmark_footer:
        benchmark_daily_df = get_benchmark_estimate_records(
            start_date=start_date,
            end_date=end_date,
            symbols=benchmark_symbols,
            market_group=market_group,
            date_field=date_field,
            include_intraday=include_intraday,
            require_final=require_final,
            cache_file=cache_file,
        )
        benchmark_summary_df = build_benchmark_cumulative_dataframe(benchmark_daily_df)

    if title is None:
        title = f"{start_date} 至 {end_date} 海外基金累计预估收益率"

    if print_table:
        print_cumulative_estimate_table(
            summary_df=summary_df,
            title=title,
            pct_digits=pct_digits,
            benchmark_summary_df=benchmark_summary_df if include_benchmark_footer else None,
        )

    if save_table:
        save_cumulative_estimate_table_image(
            summary_df=summary_df,
            output_file=output_file,
            title=title,
            pct_digits=pct_digits,
            benchmark_summary_df=benchmark_summary_df if include_benchmark_footer else None,
            **image_kwargs,
        )

    if return_benchmark:
        return summary_df, daily_df, benchmark_summary_df

    return summary_df, daily_df


__all__ = [
    "HolidayEstimateWindow",
    "load_fund_estimate_history",
    "load_benchmark_estimate_history",
    "load_a_share_trade_dates",
    "detect_overseas_holiday_estimate_window",
    "format_holiday_estimate_date_label",
    "format_holiday_estimate_output_suffix",
    "get_fund_estimate_records",
    "get_benchmark_estimate_records",
    "build_cumulative_dataframe",
    "build_benchmark_cumulative_dataframe",
    "print_cumulative_estimate_table",
    "save_cumulative_estimate_table_image",
    "build_cumulative_estimate_table",
]
