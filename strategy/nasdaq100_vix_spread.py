"""纳斯达克100与 VIX 均线差策略图。

参考图的上半部分改用纳斯达克100，下半部分展示：
VIX 200 日均线 - VIX 20 日均线。该脚本是独立试验工具，
不会被 ``stock_analysis.py`` 或日常总流程自动调用。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

import matplotlib.dates as mdates
from matplotlib.collections import LineCollection
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import vix_history
from tools.rsi_data import _setup_chinese_font, get_index_akshare
from tools.vix_history import add_vix_moving_average_spread


NASDAQ100_SYMBOL = ".NDX"
VIX_SYMBOL = "^VIX"
STRATEGY_DIR = PROJECT_ROOT / "strategy"
STRATEGY_OUTPUT_DIR = STRATEGY_DIR / "output"
STRATEGY_CACHE_DIR = STRATEGY_DIR / "cache"
# VIX 需要足够长的历史预热长均线；价格图只展示近期，避免压缩可读性。
DEFAULT_VIX_HISTORY_DAYS = 3_000
DEFAULT_DISPLAY_DAYS = 220
DEFAULT_LONG_WINDOW = 200
DEFAULT_SHORT_WINDOW = 20
# 策略试验产物单独放在 strategy 下，不干扰项目日常图片和正式行情缓存。
DEFAULT_OUTPUT_FILE = STRATEGY_OUTPUT_DIR / "nasdaq100_vix_spread.png"
VIX_CACHE_FILE = STRATEGY_CACHE_DIR / "vix_index_daily.csv"
VIX_YAHOO_CHART_URL = vix_history.VIX_YAHOO_CHART_URL
# 保留这个模块属性，兼容策略测试及手工 monkey patch。
requests = vix_history.requests

# 上图颜色由 VIX 均线差状态决定，而不是由纳斯达克100当天涨跌决定。
VIX_SPREAD_NEGATIVE_THRESHOLD = -5.0
VIX_SPREAD_POSITIVE_THRESHOLD = 5.0
PRICE_NEGATIVE_SPREAD_COLOR = "#C63D3D"
PRICE_NEUTRAL_COLOR = "#1769B0"
PRICE_POSITIVE_SPREAD_COLOR = "#00796B"
SPREAD_POSITIVE_COLOR = "#249B67"
SPREAD_NEGATIVE_COLOR = "#E35D55"


def _clean_history(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """统一日期和收盘价字段，避免异常行情行污染均线计算。"""
    if df is None or df.empty:
        raise ValueError(f"{label} 历史数据为空。")
    if not {"date", "close"}.issubset(df.columns):
        raise ValueError(f"{label} 缺少 date/close 字段。")

    out = df.loc[:, ["date", "close"]].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["date", "close"])
    out = out.drop_duplicates(subset=["date"], keep="last")
    out = out.sort_values("date").reset_index(drop=True)
    if out.empty:
        raise ValueError(f"{label} 没有可用的日期和收盘价。")
    return out


def get_vix_spread_color(spread_value: float | None) -> str:
    """按 VIX 均线差返回价格曲线颜色；阈值本身属于中性蓝色。"""
    try:
        value = float(spread_value)
    except (TypeError, ValueError):
        return PRICE_NEUTRAL_COLOR

    if not np.isfinite(value):
        return PRICE_NEUTRAL_COLOR
    if value < VIX_SPREAD_NEGATIVE_THRESHOLD:
        return PRICE_NEGATIVE_SPREAD_COLOR
    if value > VIX_SPREAD_POSITIVE_THRESHOLD:
        return PRICE_POSITIVE_SPREAD_COLOR
    return PRICE_NEUTRAL_COLOR


def build_vix_state_price_segments(
    price_df: pd.DataFrame,
    vix_spread_df: pd.DataFrame,
) -> dict[str, list[pd.DataFrame]]:
    """按终点交易日 VIX 均线差给相邻纳指价格线段分组着色。"""
    data = _clean_history(price_df, "纳斯达克100")
    if "VIX_MA_SPREAD" not in vix_spread_df.columns:
        raise ValueError("VIX 均线差数据缺少 VIX_MA_SPREAD 字段。")

    vix_state = vix_spread_df.loc[:, ["date", "VIX_MA_SPREAD"]].copy()
    vix_state["date"] = pd.to_datetime(vix_state["date"], errors="coerce")
    vix_state["VIX_MA_SPREAD"] = pd.to_numeric(vix_state["VIX_MA_SPREAD"], errors="coerce")
    vix_state = vix_state.dropna(subset=["date"]).drop_duplicates(subset=["date"], keep="last")
    data = data.merge(vix_state, on="date", how="left", validate="one_to_one")

    segments_by_color: dict[str, list[pd.DataFrame]] = {
        PRICE_NEGATIVE_SPREAD_COLOR: [],
        PRICE_NEUTRAL_COLOR: [],
        PRICE_POSITIVE_SPREAD_COLOR: [],
    }

    for index in range(1, len(data)):
        segment = data.iloc[index - 1 : index + 1].copy()
        # 使用线段终点当日的状态，连续线段在切换点仍共享同一个价格坐标。
        color = get_vix_spread_color(segment.iloc[1]["VIX_MA_SPREAD"])
        segments_by_color[color].append(segment)
    return segments_by_color


def _add_price_segments(
    axis,
    segments: Iterable[pd.DataFrame],
    *,
    color: str,
) -> None:
    """用 LineCollection 批量绘制同色线段，避免数千次 plot 调用。"""
    line_segments = []
    for segment in segments:
        dates = mdates.date2num(pd.to_datetime(segment["date"]).to_numpy())
        closes = segment["close"].to_numpy(dtype=float)
        line_segments.append(np.column_stack((dates, closes)))

    if line_segments:
        axis.add_collection(
            LineCollection(line_segments, colors=color, linewidths=1.45, zorder=3)
        )


def render_nasdaq100_vix_spread_chart(
    nasdaq100_df: pd.DataFrame,
    vix_df: pd.DataFrame,
    *,
    output_file: str | Path = DEFAULT_OUTPUT_FILE,
    long_window: int = DEFAULT_LONG_WINDOW,
    short_window: int = DEFAULT_SHORT_WINDOW,
    display_days: int = DEFAULT_DISPLAY_DAYS,
    dpi: int = 180,
) -> Path:
    """渲染双面板策略图：上为纳斯达克100，下为 VIX 均线差。"""
    _setup_chinese_font()
    if display_days <= 1:
        raise ValueError("展示天数必须大于 1。")

    price_df = _clean_history(nasdaq100_df, "纳斯达克100").tail(display_days).reset_index(drop=True)
    vix_spread_df = add_vix_moving_average_spread(
        vix_df,
        long_window=long_window,
        short_window=short_window,
    ).dropna(subset=["VIX_MA_SPREAD"])
    if vix_spread_df.empty:
        raise ValueError(f"VIX 数据不足 {long_window} 个交易日，无法计算均线差。")

    # 两个面板共用横轴，故从 VIX 长均线第一天开始展示价格。
    # VIX 的可用历史通常更长；取两份数据较晚的起点，避免共享横轴时
    # 在纳斯达克100曲线左边产生无数据空白。
    start_date = max(price_df["date"].min(), vix_spread_df["date"].min())
    end_date = min(price_df["date"].max(), vix_spread_df["date"].max())
    price_df = price_df.loc[
        price_df["date"].between(start_date, end_date)
    ].reset_index(drop=True)
    vix_spread_df = vix_spread_df.loc[
        vix_spread_df["date"].between(start_date, end_date)
    ].reset_index(drop=True)
    if len(price_df) < 2:
        raise ValueError("纳斯达克100与 VIX 可对齐的数据不足，无法绘图。")

    price_segments = build_vix_state_price_segments(price_df, vix_spread_df)
    figure, axes = plt.subplots(
        2,
        1,
        figsize=(14, 6.3),
        sharex=True,
        gridspec_kw={"height_ratios": [1, 0.9], "hspace": 0.24},
    )
    figure.patch.set_facecolor("#FFFFFF")

    price_axis, spread_axis = axes
    for color, segments in price_segments.items():
        _add_price_segments(price_axis, segments, color=color)
    price_axis.autoscale_view()
    price_axis.set_title("纳斯达克100指数", fontsize=13, fontweight="bold", pad=8)
    price_axis.set_ylabel("纳斯达克100指数", fontsize=10)

    spread = vix_spread_df["VIX_MA_SPREAD"].to_numpy(dtype=float)
    dates = vix_spread_df["date"]
    spread_axis.fill_between(
        dates,
        0,
        spread,
        where=spread >= 0,
        interpolate=True,
        color=SPREAD_POSITIVE_COLOR,
        alpha=0.88,
        zorder=2,
    )
    spread_axis.fill_between(
        dates,
        0,
        spread,
        where=spread < 0,
        interpolate=True,
        color=SPREAD_NEGATIVE_COLOR,
        alpha=0.88,
        zorder=2,
    )
    spread_axis.axhline(0, color="#8A96A3", linewidth=0.9, zorder=1)
    spread_axis.plot(dates, spread, color="#2878AF", linewidth=1.1, zorder=3)
    spread_axis.set_title(
        f"VIX 均线差（{long_window}日均线 - {short_window}日均线）",
        fontsize=13,
        fontweight="bold",
        pad=8,
    )
    spread_axis.set_ylabel("VIX 均线差", fontsize=10)

    for axis in axes:
        axis.set_facecolor("#FFFFFF")
        axis.grid(axis="both", color="#DCE3EA", linestyle="--", linewidth=0.7, alpha=0.75)
        axis.set_axisbelow(True)
        for spine in axis.spines.values():
            spine.set_color("#697785")
            spine.set_linewidth(0.85)
        axis.tick_params(labelsize=9, colors="#344252")

    spread_axis.xaxis.set_major_locator(mdates.YearLocator())
    spread_axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    figure.subplots_adjust(left=0.075, right=0.985, top=0.94, bottom=0.10)

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=dpi, facecolor=figure.get_facecolor())
    plt.close(figure)
    return output_path


def fetch_nasdaq100_history(*, days: int = DEFAULT_DISPLAY_DAYS) -> pd.DataFrame:
    """复用项目已有的新浪/缓存兜底能力获取纳斯达克100日线。"""
    return get_index_akshare(
        symbol=NASDAQ100_SYMBOL,
        days=days,
        cache_dir=str(STRATEGY_CACHE_DIR),
        retry=3,
        use_cache=True,
    )


def _fetch_daily_vix_history_from_yahoo(*, days: int) -> pd.DataFrame:
    """兼容旧入口，实际复用共享的明确日期区间 VIX 日线请求。"""
    return vix_history.fetch_daily_vix_history_from_yahoo(days=days, request_get=requests.get)


def _is_daily_vix_history(vix_df: pd.DataFrame) -> bool:
    """兼容旧入口，统一由共享 helper 判断日线有效性。"""
    return vix_history.is_daily_vix_history(vix_df)


def fetch_vix_history(*, days: int = DEFAULT_VIX_HISTORY_DAYS) -> pd.DataFrame:
    """策略试验复用共享读取逻辑，但缓存仍严格留在 strategy/cache。"""
    return vix_history.fetch_vix_daily_history(days=days, cache_file=VIX_CACHE_FILE)


def main(argv: list[str] | None = None) -> Path:
    """命令行入口：生成独立的纳斯达克100/VIX 均线差策略图。"""
    parser = argparse.ArgumentParser(description="生成纳斯达克100与 VIX 均线差策略图")
    parser.add_argument(
        "--display-days",
        type=int,
        default=DEFAULT_DISPLAY_DAYS,
        help="纳斯达克100在图片中展示的最近交易日数量",
    )
    parser.add_argument(
        "--vix-history-days",
        type=int,
        default=DEFAULT_VIX_HISTORY_DAYS,
        help="VIX 计算均线时读取的历史交易日数量",
    )
    parser.add_argument("--long-window", type=int, default=DEFAULT_LONG_WINDOW, help="VIX 长均线周期")
    parser.add_argument("--short-window", type=int, default=DEFAULT_SHORT_WINDOW, help="VIX 短均线周期")
    parser.add_argument("--dpi", type=int, default=180, help="PNG 导出 DPI")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_FILE), help="输出图片路径")
    args = parser.parse_args(argv)

    required_vix_days = max(
        args.vix_history_days,
        args.long_window + args.display_days + 30,
    )
    nasdaq100_df = fetch_nasdaq100_history(days=args.display_days)
    vix_df = fetch_vix_history(days=required_vix_days)
    output_path = render_nasdaq100_vix_spread_chart(
        nasdaq100_df,
        vix_df,
        output_file=args.output,
        long_window=args.long_window,
        short_window=args.short_window,
        display_days=args.display_days,
        dpi=args.dpi,
    )
    print(f"纳斯达克100 / VIX 均线差策略图已保存: {output_path}")
    return output_path


if __name__ == "__main__":
    main()
