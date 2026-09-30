# -*- coding: utf-8 -*-
"""
A股总市值 / GDP + 深证成指（近10年）
口径：
1. 蓝线：
   (上海市场市价总值 + 深圳市场市价总值) / 中国名义GDP(TTM)
2. 上方指数曲线：
   深证成指 399001 / 10000
3. 估值阈值：
   高估、低估、极度低估均可在脚本顶部直接修改。

数据源：
- AKShare macro_china_stock_market_cap()
  东方财富“全国股票交易统计表”，月度上海/深圳市价总值，单位亿元。
- AKShare macro_china_gdp()
  中国季度GDP绝对值，单位亿元；转换为单季GDP后计算最近4季度GDP(TTM)。
- AKShare stock_zh_index_daily_em(symbol="sz399001")
  深证成指日线；失败时尝试 stock_zh_index_daily_tx()。

说明：
- 只使用上海 + 深圳市场市价总值，不纳入北交所。
- 图上只显示最近10年。
- GDP历史数据采用当前数据库中的修订值，不是实时 vintage 回测口径。
"""

from __future__ import annotations

import re
from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager
from matplotlib.ticker import MultipleLocator, FormatStrFormatter


# ============================================================
# 用户可直接修改的参数
# ============================================================

LOOKBACK_YEARS = 10 #近十年

HIGH_VALUATION_THRESHOLD = 0.765 #高估阈值
LOW_VALUATION_THRESHOLD = 0.60  #低估阈值
EXTREME_LOW_THRESHOLD = 0.55    #极端低估阈值

SHENZHEN_COMPONENT_SYMBOL = "sz399001"
SHENZHEN_COMPONENT_SCALE = 10000.0

OUTPUT_DIR = Path("output")
CACHE_DIR = Path("cache")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_PNG = OUTPUT_DIR / "a_share_market_cap_gdp_10y.png"
OUTPUT_CSV = CACHE_DIR / "a_share_market_cap_gdp_10y.csv"

CAP_CACHE = CACHE_DIR / "a_share_market_cap_monthly.csv"
GDP_CACHE = CACHE_DIR / "china_nominal_gdp_quarterly.csv"
INDEX_CACHE = CACHE_DIR / "sz399001_daily.csv"

# ============================================================
# 基础工具
# ============================================================

def setup_chinese_font() -> None:
    """优先寻找常见中文字体，不依赖固定字体路径。"""
    candidates = [
        "Microsoft YaHei",
        "Microsoft YaHei UI",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "SimHei",
        "PingFang SC",
        "Arial Unicode MS",
    ]
    installed = {f.name for f in font_manager.fontManager.ttflist}
    chosen = next((name for name in candidates if name in installed), None)

    if chosen:
        plt.rcParams["font.sans-serif"] = [chosen, "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("--", "", regex=False)
        .str.strip(),
        errors="coerce",
    )


def parse_month(value) -> pd.Timestamp:
    """兼容 '2026年08月'、'2026-08-01' 等格式，并统一到月末。"""
    text = str(value).strip()

    match = re.search(r"(\d{4})\D+(\d{1,2})", text)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        return pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)

    stamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(stamp):
        return pd.NaT
    return pd.Timestamp(stamp) + pd.offsets.MonthEnd(0)


def read_cache(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        frame = pd.read_csv(path)
        return frame if not frame.empty else None
    except Exception:
        return None


# ============================================================
# 1. 上海 + 深圳市场总市值
# ============================================================

def fetch_market_cap_monthly() -> pd.DataFrame:
    try:
        raw = ak.macro_china_stock_market_cap().copy()

        required = {"数据日期", "市价总值-上海", "市价总值-深圳"}
        missing = required - set(raw.columns)
        if missing:
            raise RuntimeError(
                f"市值接口字段发生变化，缺少 {sorted(missing)}；"
                f"实际字段={raw.columns.tolist()}"
            )

        out = pd.DataFrame()
        out["date"] = raw["数据日期"].map(parse_month)
        out["sse_market_cap_yi"] = to_numeric(raw["市价总值-上海"])
        out["szse_market_cap_yi"] = to_numeric(raw["市价总值-深圳"])
        out["market_cap_yi"] = (
            out["sse_market_cap_yi"] + out["szse_market_cap_yi"]
        )

        out = (
            out.dropna(subset=["date", "market_cap_yi"])
            .sort_values("date")
            .drop_duplicates("date", keep="last")
            .reset_index(drop=True)
        )

        out.to_csv(CAP_CACHE, index=False, encoding="utf-8-sig")
        return out

    except Exception as error:
        cached = read_cache(CAP_CACHE)
        if cached is None:
            raise RuntimeError(f"沪深总市值获取失败且没有缓存：{error}") from error

        cached["date"] = pd.to_datetime(cached["date"], errors="coerce")
        print(f"[WARN] 市值接口失败，使用缓存：{error}")
        return cached.dropna(subset=["date"]).sort_values("date")


# ============================================================
# 2. 中国名义GDP -> 单季度 -> TTM
# ============================================================

def parse_quarter(value) -> tuple[int, int] | None:
    """
    兼容 AKShare 常见形式：
    - 2026-06-01
    - 2026年第2季度
    - 2026Q2
    """
    stamp = pd.to_datetime(value, errors="coerce")
    if pd.notna(stamp):
        stamp = pd.Timestamp(stamp)
        quarter = (stamp.month - 1) // 3 + 1
        return int(stamp.year), int(quarter)

    text = str(value).strip()
    patterns = [
        r"(?P<year>20\d{2}).*?第?(?P<quarter>[1-4])季度",
        r"(?P<year>20\d{2})\s*[Qq](?P<quarter>[1-4])",
        r"(?P<year>20\d{2})[-/](?P<quarter>[1-4])$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group("year")), int(match.group("quarter"))
    return None


def fetch_gdp_ttm() -> pd.DataFrame:
    try:
        raw = ak.macro_china_gdp().copy()

        required = {"季度", "国内生产总值-绝对值"}
        missing = required - set(raw.columns)
        if missing:
            raise RuntimeError(
                f"GDP接口字段发生变化，缺少 {sorted(missing)}；"
                f"实际字段={raw.columns.tolist()}"
            )

        parsed = raw["季度"].map(parse_quarter)

        out = pd.DataFrame()
        out["year"] = parsed.map(lambda x: x[0] if x else np.nan)
        out["quarter"] = parsed.map(lambda x: x[1] if x else np.nan)
        out["gdp_ytd_yi"] = to_numeric(raw["国内生产总值-绝对值"])

        out = (
            out.dropna(subset=["year", "quarter", "gdp_ytd_yi"])
            .astype({"year": int, "quarter": int})
            .sort_values(["year", "quarter"])
            .drop_duplicates(["year", "quarter"], keep="last")
            .reset_index(drop=True)
        )

        # AKShare此字段为年内累计GDP：
        # Q1 = Q1
        # Q2单季 = Q1-Q2累计差
        # Q3单季 = Q1-Q3累计差
        # Q4单季 = 全年累计 - Q1-Q3累计
        out["previous_ytd"] = out.groupby("year")["gdp_ytd_yi"].shift(1)
        out["gdp_single_quarter_yi"] = np.where(
            out["quarter"].eq(1),
            out["gdp_ytd_yi"],
            out["gdp_ytd_yi"] - out["previous_ytd"],
        )

        bad = out["gdp_single_quarter_yi"] <= 0
        if bad.any():
            raise RuntimeError(
                "GDP单季度值出现非正数，可能是接口字段或口径变化：\n"
                + out.loc[
                    bad,
                    ["year", "quarter", "gdp_ytd_yi", "gdp_single_quarter_yi"],
                ].to_string(index=False)
            )

        out["gdp_ttm_yi"] = (
            out["gdp_single_quarter_yi"]
            .rolling(4, min_periods=4)
            .sum()
        )

        period = pd.PeriodIndex(
            year=out["year"],
            quarter=out["quarter"],
            freq="Q",
        )
        out["date"] = period.to_timestamp(how="end").normalize()

        out = out[
            [
                "date",
                "year",
                "quarter",
                "gdp_ytd_yi",
                "gdp_single_quarter_yi",
                "gdp_ttm_yi",
            ]
        ].dropna(subset=["gdp_ttm_yi"])

        out.to_csv(GDP_CACHE, index=False, encoding="utf-8-sig")
        return out

    except Exception as error:
        cached = read_cache(GDP_CACHE)
        if cached is None:
            raise RuntimeError(f"GDP获取失败且没有缓存：{error}") from error

        cached["date"] = pd.to_datetime(cached["date"], errors="coerce")
        print(f"[WARN] GDP接口失败，使用缓存：{error}")
        return cached.dropna(subset=["date"]).sort_values("date")


# ============================================================
# 3. 深证成指399001
# ============================================================

def standardize_index_frame(raw: pd.DataFrame) -> pd.DataFrame:
    frame = raw.copy()

    rename_map = {
        "日期": "date",
        "date": "date",
        "收盘": "close",
        "close": "close",
    }
    frame = frame.rename(columns=rename_map)

    if "date" not in frame.columns or "close" not in frame.columns:
        raise RuntimeError(
            f"深证成指返回字段不完整：{frame.columns.tolist()}"
        )

    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = to_numeric(frame["close"])

    return (
        frame[["date", "close"]]
        .dropna()
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )


def fetch_shenzhen_component(start_date: pd.Timestamp, end_date: pd.Timestamp) -> pd.DataFrame:
    start_text = start_date.strftime("%Y%m%d")
    end_text = end_date.strftime("%Y%m%d")

    errors = []

    try:
        raw = ak.stock_zh_index_daily_em(
            symbol=SHENZHEN_COMPONENT_SYMBOL,
            start_date=start_text,
            end_date=end_text,
        )
        out = standardize_index_frame(raw)
        if not out.empty:
            out.to_csv(INDEX_CACHE, index=False, encoding="utf-8-sig")
            return out
    except Exception as error:
        errors.append(f"东方财富: {error}")

    try:
        raw = ak.stock_zh_index_daily_tx(symbol=SHENZHEN_COMPONENT_SYMBOL)
        out = standardize_index_frame(raw)
        out = out.loc[
            (out["date"] >= start_date) & (out["date"] <= end_date)
        ].copy()

        if not out.empty:
            out.to_csv(INDEX_CACHE, index=False, encoding="utf-8-sig")
            return out
    except Exception as error:
        errors.append(f"腾讯: {error}")

    cached = read_cache(INDEX_CACHE)
    if cached is not None:
        cached["date"] = pd.to_datetime(cached["date"], errors="coerce")
        cached["close"] = to_numeric(cached["close"])
        cached = cached.dropna(subset=["date", "close"]).sort_values("date")
        cached = cached.loc[
            (cached["date"] >= start_date) & (cached["date"] <= end_date)
        ].copy()

        if not cached.empty:
            print("[WARN] 深证成指在线行情失败，使用本地缓存")
            return cached

    raise RuntimeError("深证成指获取失败：" + " | ".join(errors))


# ============================================================
# 4. 构造A股总市值/GDP
# ============================================================

def build_market_cap_gdp_ratio() -> pd.DataFrame:
    cap = fetch_market_cap_monthly()
    gdp = fetch_gdp_ttm()

    out = pd.merge_asof(
        cap.sort_values("date"),
        gdp[["date", "gdp_ttm_yi"]].sort_values("date"),
        on="date",
        direction="backward",
    )

    out = out.dropna(subset=["gdp_ttm_yi"]).copy()
    out["market_cap_to_gdp"] = out["market_cap_yi"] / out["gdp_ttm_yi"]

    return out


# ============================================================
# 5. 绘图
# ============================================================

def plot_chart(ratio: pd.DataFrame, index_df: pd.DataFrame) -> None:
    setup_chinese_font()

    latest_available = max(
        ratio["date"].max(),
        index_df["date"].max(),
    )
    start = latest_available - pd.DateOffset(years=LOOKBACK_YEARS)

    ratio = ratio.loc[ratio["date"] >= start].copy()
    index_df = index_df.loc[index_df["date"] >= start].copy()

    index_df["scaled_close"] = (
        index_df["close"] / SHENZHEN_COMPONENT_SCALE
    )

    if ratio.empty:
        raise RuntimeError("最近10年没有可用的总市值/GDP数据")
    if index_df.empty:
        raise RuntimeError("最近10年没有可用的深证成指数据")

    fig, ax = plt.subplots(figsize=(13.2, 6.5))

    # 先画估值线，再画深证成指；不强制指定颜色，
    # 保持 Matplotlib 默认配色体系，避免硬编码主题。
    ax.plot(
        ratio["date"],
        ratio["market_cap_to_gdp"],
        linewidth=2.15,
        label="沪深总市值 / GDP(TTM)",
        zorder=3,
    )
    ax.plot(
        index_df["date"],
        index_df["scaled_close"],
        linewidth=1.55,
        alpha=0.88,
        label="深证成指 ÷ 10000",
        zorder=2,
    )

    thresholds = [
        (HIGH_VALUATION_THRESHOLD, "高估"),
        (LOW_VALUATION_THRESHOLD, "低估"),
        (EXTREME_LOW_THRESHOLD, "极度低估"),
    ]

    for level, label in thresholds:
        ax.axhline(
            y=level,
            linewidth=1.0,
            alpha=0.52,
            zorder=1,
        )
        ax.text(
            0.985,
            level,
            f"{label}  {level:.2f}",
            transform=ax.get_yaxis_transform(),
            ha="right",
            va="bottom",
            fontsize=9,
            alpha=0.82,
        )

    # 最新值标记
    ratio_last = ratio.iloc[-1]
    index_last = index_df.iloc[-1]

    ax.scatter(
        [ratio_last["date"]],
        [ratio_last["market_cap_to_gdp"]],
        s=30,
        zorder=5,
    )
    ax.annotate(
        f'{ratio_last["market_cap_to_gdp"]:.2f}',
        xy=(ratio_last["date"], ratio_last["market_cap_to_gdp"]),
        xytext=(-9, 11),
        textcoords="offset points",
        ha="right",
        fontsize=9.5,
    )

    ax.scatter(
        [index_last["date"]],
        [index_last["scaled_close"]],
        s=26,
        zorder=5,
    )
    ax.annotate(
        f'{index_last["scaled_close"]:.2f}',
        xy=(index_last["date"], index_last["scaled_close"]),
        xytext=(-9, -15),
        textcoords="offset points",
        ha="right",
        fontsize=9.5,
    )

    ax.set_title(
        "A股总市值 / GDP 与深证成指｜近10年",
        fontsize=16.5,
        pad=20,
    )

    ax.text(
        0.0,
        1.015,
        "沪市市价总值 + 深市市价总值 ÷ 中国名义GDP(TTM)   ｜   深证成指 399001 ÷ 10000",
        transform=ax.transAxes,
        fontsize=9.5,
        alpha=0.72,
        va="bottom",
    )

    ax.legend(
        loc="upper left",
        ncol=2,
        frameon=False,
        fontsize=10,
    )

    ax.set_ylabel("总市值/GDP ｜ 深证成指÷10000")
    ax.set_xlabel("")

    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    ax.yaxis.set_major_locator(MultipleLocator(0.2))
    ax.yaxis.set_major_formatter(FormatStrFormatter("%.1f"))

    ax.grid(
        axis="y",
        alpha=0.16,
        linewidth=0.8,
    )

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    all_values = pd.concat(
        [
            ratio["market_cap_to_gdp"],
            index_df["scaled_close"],
            pd.Series(
                [
                    HIGH_VALUATION_THRESHOLD,
                    LOW_VALUATION_THRESHOLD,
                    EXTREME_LOW_THRESHOLD,
                ]
            ),
        ],
        ignore_index=True,
    ).dropna()

    y_min = max(0.0, float(all_values.min()) - 0.12)
    y_max = float(all_values.max()) + 0.15

    ax.set_ylim(y_min, y_max)
    ax.set_xlim(start, latest_available)

    ax.text(
        0.995,
        0.015,
        (
            f"最新：总市值/GDP {ratio_last['market_cap_to_gdp']:.2f}"
            f"（{ratio_last['date']:%Y-%m}）"
            f"   ｜   深证成指÷10000 {index_last['scaled_close']:.2f}"
            f"（{index_last['date']:%Y-%m-%d}）"
        ),
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=8.8,
        alpha=0.68,
    )

    fig.tight_layout()
    fig.savefig(
        OUTPUT_PNG,
        dpi=220,
        bbox_inches="tight",
    )
    plt.close(fig)


# ============================================================
# 6. 导出用于复核的月度CSV
# ============================================================

def export_monthly_review_data(
    ratio: pd.DataFrame,
    index_df: pd.DataFrame,
) -> pd.DataFrame:
    monthly_index = (
        index_df.set_index("date")[["close"]]
        .resample("ME")
        .last()
        .dropna()
        .reset_index()
    )
    monthly_index["shenzhen_component_scaled"] = (
        monthly_index["close"] / SHENZHEN_COMPONENT_SCALE
    )

    out = pd.merge_asof(
        ratio.sort_values("date"),
        monthly_index[
            ["date", "close", "shenzhen_component_scaled"]
        ].sort_values("date"),
        on="date",
        direction="backward",
    )

    latest = max(out["date"].max(), index_df["date"].max())
    start = latest - pd.DateOffset(years=LOOKBACK_YEARS)

    out = out.loc[out["date"] >= start].copy()
    out.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    return out


def main() -> None:
    ratio = build_market_cap_gdp_ratio()

    end_date = pd.Timestamp.today().normalize()
    start_date = end_date - pd.DateOffset(
        years=LOOKBACK_YEARS,
        months=2,
    )

    index_df = fetch_shenzhen_component(
        start_date=start_date,
        end_date=end_date,
    )

    review = export_monthly_review_data(ratio, index_df)
    plot_chart(ratio, index_df)

    ratio_last = ratio.iloc[-1]
    index_last = index_df.iloc[-1]

    print("=" * 72)
    print("A股总市值/GDP + 深证成指 图表生成完成")
    print(f"显示区间        : 最近 {LOOKBACK_YEARS} 年")
    print(f"高估阈值        : {HIGH_VALUATION_THRESHOLD:.2f}")
    print(f"低估阈值        : {LOW_VALUATION_THRESHOLD:.2f}")
    print(f"极度低估阈值    : {EXTREME_LOW_THRESHOLD:.2f}")
    print("-" * 72)
    print(f"最新市值月份     : {ratio_last['date']:%Y-%m}")
    print(f"沪市总市值       : {ratio_last['sse_market_cap_yi'] / 10000:.2f} 万亿元")
    print(f"深市总市值       : {ratio_last['szse_market_cap_yi'] / 10000:.2f} 万亿元")
    print(f"沪深总市值       : {ratio_last['market_cap_yi'] / 10000:.2f} 万亿元")
    print(f"GDP(TTM)         : {ratio_last['gdp_ttm_yi'] / 10000:.2f} 万亿元")
    print(f"总市值/GDP       : {ratio_last['market_cap_to_gdp']:.4f}")
    print("-" * 72)
    print(f"深证成指日期     : {index_last['date']:%Y-%m-%d}")
    print(f"深证成指         : {index_last['close']:.2f}")
    print(f"深证成指/10000   : {index_last['close'] / SHENZHEN_COMPONENT_SCALE:.4f}")
    print("-" * 72)
    print(f"月度复核数据     : {OUTPUT_CSV}")
    print(f"输出图片         : {OUTPUT_PNG}")
    print(f"月度记录数       : {len(review)}")
    print("=" * 72)


if __name__ == "__main__":
    main()
