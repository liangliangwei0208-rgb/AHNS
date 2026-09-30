# -*- coding: utf-8 -*-
"""
深证成指 + 全A股债利差 + 250日滚动均值 ± kσ
================================================

核心定义：
    全A股债利差 = 全A盈利收益率 - 中国10年期国债收益率
               = 100 / 全A股PE(TTM) - CN10Y

标准差通道：
    Mean_t  = rolling_mean(Spread, WINDOW)
    Upper_t = Mean_t + STD_MULT * rolling_std(Spread, WINDOW)
    Lower_t = Mean_t - STD_MULT * rolling_std(Spread, WINDOW)

默认：
    WINDOW = 250
    STD_MULT = 1.25
    DISPLAY_YEARS = 6

可直接修改顶部参数，也可用命令行覆盖：
    python strategy/gu_zhai_xi.py --sigma 1
    python strategy/gu_zhai_xi.py --sigma 2
    python strategy/gu_zhai_xi.py --sigma 1.5 --window 250 --years 6

依赖：
    pip install akshare pandas numpy matplotlib
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategy.gu_zhai_xi_data import MarketDataCache, required_trade_dates
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D


# ============================================================
# 用户最常修改的参数
# ============================================================

DISPLAY_YEARS = 10          # 图中显示最近多少年
WINDOW = 600               # 滚动窗口：当前用户参数
STD_MULT = 1.95            # 标准差倍数：1.0=±1σ, 2.0=±2σ

INDEX_SYMBOL = "sz399001"  # 深证成指

# ============================================================
# 深证成指“状态分段着色”参数 —— 可直接修改
# ============================================================
COLOR_INDEX_BY_SPREAD = True

# 正常状态：lower <= spread <= upper
INDEX_NORMAL_COLOR = "#030303"      # red

# 股债利差突破上轨：spread > upper
INDEX_ABOVE_UPPER_COLOR = "#049E0C" # magenta / rose

# 股债利差跌破下轨：spread < lower
INDEX_BELOW_LOWER_COLOR = "#FA3205" # deep teal

INDEX_NORMAL_WIDTH = 2.15
INDEX_EXTREME_WIDTH = 2.70

# 是否把股债利差线性映射到指数点位坐标，复刻视频风格
VIDEO_STYLE_SCALE = True

# 图片输出
OUTPUT_DIR = Path("output")
OUTPUT_FILE = "equity_bond_spread_std_channel.png"

# 全A PE字段优先级
# averagePETTM：全A股等权TTM PE
# middlePETTM：全A股TTM PE中位数
PE_COLUMN_CANDIDATES = [
    "averagePETTM",
    "middlePETTM",
]


# ============================================================
# 数据获取
# ============================================================

# 同一次 build_indicator 共享缓存检查和富途连接。独立 fetch_* 入口保留原返回字段。
def _load_chart_sources(years: int, window: int, *, cache_dir: Path | None = None, now=None):
    dates = required_trade_dates(years, window, now=now)
    location = cache_dir or Path(__file__).resolve().parents[1] / "cache"
    with MarketDataCache(location, dates, now=now, pe_columns=PE_COLUMN_CANDIDATES) as store:
        return store.load_all()


def fetch_all_a_pe() -> pd.DataFrame:
    dates = required_trade_dates(DISPLAY_YEARS, WINDOW)
    with MarketDataCache(Path(__file__).resolve().parents[1] / "cache", dates, pe_columns=PE_COLUMN_CANDIDATES) as store:
        return store.load_pe()


def fetch_cn_10y_yield() -> pd.DataFrame:
    dates = required_trade_dates(DISPLAY_YEARS, WINDOW)
    with MarketDataCache(Path(__file__).resolve().parents[1] / "cache", dates) as store:
        return store.load_bond()


def fetch_shenzhen_index() -> pd.DataFrame:
    dates = required_trade_dates(DISPLAY_YEARS, WINDOW)
    with MarketDataCache(Path(__file__).resolve().parents[1] / "cache", dates) as store:
        return store.load_index()


# ============================================================
# 指标构造
# ============================================================

def build_indicator(
    years: int,
    window: int,
    sigma_mult: float,
) -> pd.DataFrame:
    """
    以深证成指交易日为主轴，
    将全A PE和10Y国债按最近可得历史值 backward 对齐。
    """

    index_df, pe_df, bond_df = _load_chart_sources(years, window)

    data = pd.merge_asof(
        index_df.sort_values("date"),
        pe_df.sort_values("date"),
        on="date",
        direction="backward",
    )

    data = pd.merge_asof(
        data.sort_values("date"),
        bond_df.sort_values("date"),
        on="date",
        direction="backward",
    )

    # 全A盈利收益率，单位 %
    data["earnings_yield"] = 100.0 / data["pe_ttm"]

    # 股债利差，单位百分点
    data["spread"] = data["earnings_yield"] - data["cn10y"]

    # 长周期标准差通道
    rolling = data["spread"].rolling(
        window=window,
        min_periods=window,
    )

    data["mean"] = rolling.mean()
    rolling_std = rolling.std(ddof=0)

    data["upper"] = data["mean"] + sigma_mult * rolling_std
    data["lower"] = data["mean"] - sigma_mult * rolling_std

    data = data.dropna(
        subset=[
            "index_close",
            "spread",
            "mean",
            "upper",
            "lower",
        ]
    ).copy()

    if data.empty:
        raise RuntimeError("有效数据不足，无法绘图。")

    # 仅显示最近 N 年
    end_date = data["date"].max()
    start_date = end_date - pd.DateOffset(years=years)

    return (
        data.loc[data["date"] >= start_date]
        .reset_index(drop=True)
    )


# ============================================================
# 视频式同轴缩放
# ============================================================

def affine_scale_to_index(df: pd.DataFrame) -> pd.DataFrame:
    """
    将 spread / mean / upper / lower 用同一个线性变换映射到指数坐标。

    y_scaled = a * y + b

    注意：
    这只改变展示坐标，不改变四条股债利差相关曲线之间的相对结构。
    """

    out = df.copy()

    spread_std = float(out["spread"].std(ddof=0))
    index_std = float(out["index_close"].std(ddof=0))

    if (
        not np.isfinite(spread_std)
        or spread_std <= 0
        or not np.isfinite(index_std)
        or index_std <= 0
    ):
        raise RuntimeError("标准差无效，无法执行同轴缩放。")

    scale = index_std / spread_std

    offset = (
        float(out["index_close"].mean())
        - scale * float(out["spread"].mean())
    )

    for col in ["spread", "mean", "upper", "lower"]:
        out[f"{col}_scaled"] = scale * out[col] + offset

    return out


# ============================================================
# 绘图
# ============================================================

def setup_font():
    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def classify_spread_state(df: pd.DataFrame) -> pd.Series:
    """
    按真实、未缩放的股债利差判断状态：

        ABOVE : spread > upper
        BELOW : spread < lower
        NORMAL: lower <= spread <= upper

    注意：
    - 判断使用真实 spread / upper / lower；
    - 与视频模式里的视觉缩放无关；
    - 不使用未来值。
    """
    state = pd.Series("NORMAL", index=df.index, dtype="object")
    state.loc[df["spread"] > df["upper"]] = "ABOVE"
    state.loc[df["spread"] < df["lower"]] = "BELOW"
    return state


def draw_colored_shenzhen_index(
    ax,
    df: pd.DataFrame,
    *,
    date_col: str = "date",
    price_col: str = "index_close",
    zorder: int = 6,
):
    """
    将深证成指按股债利差所处标准差通道状态进行“线段级”着色。

    NORMAL:
        INDEX_NORMAL_COLOR
    spread > upper:
        INDEX_ABOVE_UPPER_COLOR
    spread < lower:
        INDEX_BELOW_LOWER_COLOR

    使用 LineCollection，而不是三条 NaN mask 曲线：
    - 状态切换处更连续；
    - 不会留下视觉断点；
    - 一条指数轨迹即可表达三种状态。
    """
    if df is None or len(df) < 2:
        return None

    work = df[[date_col, price_col, "spread", "upper", "lower"]].copy()
    work = work.dropna().reset_index(drop=True)

    if len(work) < 2:
        return None

    dates = pd.to_datetime(work[date_col])
    x = mdates.date2num(dates.to_numpy())
    y = pd.to_numeric(work[price_col], errors="coerce").to_numpy(dtype=float)

    points = np.column_stack([x, y]).reshape(-1, 1, 2)
    segments = np.concatenate([points[:-1], points[1:]], axis=1)

    states = classify_spread_state(work).to_numpy()

    # 每个线段采用“右端交易日”的状态。
    # 这样突破发生在某日时，从该日开始显示新颜色。
    segment_states = states[1:]

    color_map = {
        "NORMAL": INDEX_NORMAL_COLOR,
        "ABOVE": INDEX_ABOVE_UPPER_COLOR,
        "BELOW": INDEX_BELOW_LOWER_COLOR,
    }
    width_map = {
        "NORMAL": INDEX_NORMAL_WIDTH,
        "ABOVE": INDEX_EXTREME_WIDTH,
        "BELOW": INDEX_EXTREME_WIDTH,
    }

    colors = [color_map[s] for s in segment_states]
    widths = [width_map[s] for s in segment_states]

    collection = LineCollection(
        segments,
        colors=colors,
        linewidths=widths,
        zorder=zorder,
        capstyle="round",
        joinstyle="round",
    )
    ax.add_collection(collection)

    # LineCollection 不会自动参与 autoscale，所以显式更新范围。
    ax.update_datalim(np.column_stack([x, y]))
    ax.autoscale_view()

    return collection


def current_index_state_color(df: pd.DataFrame) -> str:
    """返回最新交易日深证成指应使用的状态颜色。"""
    if df is None or df.empty:
        return INDEX_NORMAL_COLOR

    last = df.iloc[-1]

    if last["spread"] > last["upper"]:
        return INDEX_ABOVE_UPPER_COLOR

    if last["spread"] < last["lower"]:
        return INDEX_BELOW_LOWER_COLOR

    return INDEX_NORMAL_COLOR


def index_state_legend_handles():
    """用于图例的三个深证成指状态说明。"""
    return [
        Line2D(
            [0], [0],
            color=INDEX_NORMAL_COLOR,
            lw=INDEX_NORMAL_WIDTH,
            label="Shenzhen Index · Normal",
        ),
        Line2D(
            [0], [0],
            color=INDEX_ABOVE_UPPER_COLOR,
            lw=INDEX_EXTREME_WIDTH,
            label="Shenzhen Index · Spread > Upper",
        ),
        Line2D(
            [0], [0],
            color=INDEX_BELOW_LOWER_COLOR,
            lw=INDEX_EXTREME_WIDTH,
            label="Shenzhen Index · Spread < Lower",
        ),
    ]


def plot_video_style(
    df: pd.DataFrame,
    years: int,
    window: int,
    sigma_mult: float,
    output_file: Path,
):
    """复刻视频风格：所有曲线映射到同一个指数坐标。"""

    setup_font()

    data = affine_scale_to_index(df)

    fig, ax = plt.subplots(figsize=(10.8, 7.2))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    # 深证成指：按股债利差是否突破标准差通道分段着色。
    # 判断基于真实 spread / upper / lower，而不是 scaled 数据。
    if COLOR_INDEX_BY_SPREAD:
        draw_colored_shenzhen_index(
            ax,
            data,
            date_col="date",
            price_col="index_close",
            zorder=6,
        )
    else:
        ax.plot(
            data["date"],
            data["index_close"],
            color=INDEX_NORMAL_COLOR,
            linewidth=INDEX_NORMAL_WIDTH,
            label="Shenzhen Index",
            zorder=6,
        )

    ax.plot(
        data["date"],
        data["spread_scaled"],
        color="#4A82CF",
        linewidth=1.85,
        label="Equity–Bond Spread",
        zorder=7,
    )

    ax.plot(
        data["date"],
        data["upper_scaled"],
        color="#77A66B",
        linewidth=1.65,
        label=f"+{sigma_mult:g}σ",
        zorder=4,
    )

    ax.plot(
        data["date"],
        data["mean_scaled"],
        color="#E0A04A",
        linewidth=1.65,
        label="Mean",
        zorder=4,
    )

    ax.plot(
        data["date"],
        data["lower_scaled"],
        color="#7B5EA7",
        linewidth=1.65,
        label=f"-{sigma_mult:g}σ",
        zorder=4,
    )

    # 视频风格：右侧纵轴
    ax.yaxis.tick_right()

    ax.tick_params(
        axis="y",
        left=False,
        labelleft=False,
        right=True,
        labelright=True,
        length=0,
        labelsize=9,
    )

    ax.tick_params(
        axis="x",
        labelsize=8.5,
    )

    # 只保留横向虚线网格
    ax.grid(False)
    ax.yaxis.grid(
        True,
        linestyle=(0, (3, 4)),
        linewidth=0.8,
        alpha=0.30,
    )

    # 极简边框
    ax.spines["top"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax.spines["bottom"].set_color("#707070")
    ax.spines["bottom"].set_linewidth(0.8)

    # 近6年用约9个月一个刻度
    ax.xaxis.set_major_locator(
        mdates.MonthLocator(interval=9)
    )
    ax.xaxis.set_major_formatter(
        mdates.DateFormatter("%Y/%m/%d")
    )

    # 视频右上角文字
    ax.text(
        0.995,
        0.985,
        "指数",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=12,
        color="#555555",
    )

    # 左上角参数提示
    ax.text(
        0.015,
        0.975,
        f"{window}D rolling mean ± {sigma_mult:g}σ",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=9.5,
        color="#666666",
    )

    # 底部图例：
    # 既保留 Spread / Mean / ±σ，也明确解释深证成指三种颜色，
    # 避免把状态着色误读成另一条指标曲线。
    handles, labels = ax.get_legend_handles_labels()

    if COLOR_INDEX_BY_SPREAD:
        state_handles = index_state_legend_handles()
        handles = state_handles + handles
        labels = [h.get_label() for h in state_handles] + labels

    ax.legend(
        handles,
        labels,
        loc="lower left",
        ncol=3,
        frameon=False,
        fontsize=7.2,
        handlelength=2.2,
        columnspacing=1.0,
        labelspacing=0.55,
    )

    # 最新值
    latest = data.iloc[-1]

    ax.scatter(
        latest["date"],
        latest["index_close"],
        s=26,
        color=current_index_state_color(data),
        zorder=10,
    )

    ax.scatter(
        latest["date"],
        latest["spread_scaled"],
        s=22,
        color="#4A82CF",
        zorder=10,
    )

    # 左下角显示真实金融指标，而不是缩放值
    ax.text(
        0.015,
        0.075,
        (
            f"Spread {latest['spread']:.2f} pp   ·   "
            f"All-A PE(TTM) {latest['pe_ttm']:.2f}   ·   "
            f"CN10Y {latest['cn10y']:.2f}%"
        ),
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=7.8,
        color="#666666",
    )

    ax.margins(x=0.012)

    fig.tight_layout(pad=0.9)

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fig.savefig(
        output_file,
        dpi=220,
        bbox_inches="tight",
    )

    plt.show()
    plt.close(fig)


def plot_dual_axis(
    df: pd.DataFrame,
    years: int,
    window: int,
    sigma_mult: float,
    output_file: Path,
):
    """
    金融含义更严格的双轴版本：
    左轴=深证成指
    右轴=真实股债利差（百分点）
    """

    setup_font()

    fig, ax_index = plt.subplots(
        figsize=(11.4, 7.2)
    )

    ax_spread = ax_index.twinx()

    # 深证成指在严格双轴模式下也采用相同的状态分段着色。
    if COLOR_INDEX_BY_SPREAD:
        draw_colored_shenzhen_index(
            ax_index,
            df,
            date_col="date",
            price_col="index_close",
            zorder=6,
        )
        line_index = None
    else:
        line_index = ax_index.plot(
            df["date"],
            df["index_close"],
            color=INDEX_NORMAL_COLOR,
            linewidth=INDEX_NORMAL_WIDTH,
            label="Shenzhen Index",
        )[0]

    line_spread = ax_spread.plot(
        df["date"],
        df["spread"],
        color="#4A82CF",
        linewidth=1.85,
        label="Equity–Bond Spread",
    )[0]

    line_upper = ax_spread.plot(
        df["date"],
        df["upper"],
        color="#77A66B",
        linewidth=1.6,
        label=f"+{sigma_mult:g}σ",
    )[0]

    line_mean = ax_spread.plot(
        df["date"],
        df["mean"],
        color="#E0A04A",
        linewidth=1.6,
        label="Mean",
    )[0]

    line_lower = ax_spread.plot(
        df["date"],
        df["lower"],
        color="#7B5EA7",
        linewidth=1.6,
        label=f"-{sigma_mult:g}σ",
    )[0]

    ax_index.set_ylabel("Shenzhen Component Index")
    ax_spread.set_ylabel("Equity–Bond Spread (percentage points)")

    ax_index.grid(
        True,
        axis="y",
        linestyle=(0, (3, 4)),
        linewidth=0.8,
        alpha=0.25,
    )

    ax_index.xaxis.set_major_locator(
        mdates.MonthLocator(interval=9)
    )
    ax_index.xaxis.set_major_formatter(
        mdates.DateFormatter("%Y/%m/%d")
    )

    metric_handles = [
        line_spread,
        line_upper,
        line_mean,
        line_lower,
    ]

    if COLOR_INDEX_BY_SPREAD:
        handles = index_state_legend_handles() + metric_handles
    else:
        handles = [line_index] + metric_handles

    ax_index.legend(
        handles,
        [h.get_label() for h in handles],
        loc="lower left",
        ncol=3,
        frameon=False,
        fontsize=7.2,
        handlelength=2.2,
        columnspacing=1.0,
        labelspacing=0.55,
    )

    ax_index.text(
        0.015,
        0.975,
        f"{window}D rolling mean ± {sigma_mult:g}σ",
        transform=ax_index.transAxes,
        ha="left",
        va="top",
        fontsize=9.5,
        color="#666666",
    )

    fig.tight_layout()

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fig.savefig(
        output_file,
        dpi=220,
        bbox_inches="tight",
    )

    plt.show()
    plt.close(fig)


# ============================================================
# CLI
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="深证成指 + 全A股债利差 + 滚动标准差通道"
    )

    parser.add_argument(
        "--sigma",
        type=float,
        default=STD_MULT,
        help="标准差倍数，例如 1 / 1.5 / 2；默认 %(default)s",
    )

    parser.add_argument(
        "--window",
        type=int,
        default=WINDOW,
        help="滚动窗口，例如 120 / 250 / 500；默认 %(default)s",
    )

    parser.add_argument(
        "--years",
        type=int,
        default=DISPLAY_YEARS,
        help="显示最近多少年；默认 %(default)s",
    )

    parser.add_argument(
        "--dual-axis",
        action="store_true",
        help="使用严格双轴模式，不做视频式同轴缩放",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="输出图片路径",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.sigma <= 0:
        raise ValueError("--sigma 必须 > 0")

    if args.window < 20:
        raise ValueError("--window 建议至少 >= 20")

    if args.years <= 0:
        raise ValueError("--years 必须 > 0")

    data = build_indicator(
        years=args.years,
        window=args.window,
        sigma_mult=args.sigma,
    )

    if args.output:
        output = Path(args.output)
    else:
        mode = "dual" if args.dual_axis else "video"
        output = OUTPUT_DIR / (
            f"equity_bond_spread_"
            f"{args.years}y_"
            f"{args.window}d_"
            f"{args.sigma:g}sigma_"
            f"{mode}.png"
        )

    latest = data.iloc[-1]

    print("=" * 72)
    print("深证成指 + 全A股债利差标准差通道")
    print("=" * 72)
    print(f"显示区间     : {data['date'].min():%Y-%m-%d} ~ {data['date'].max():%Y-%m-%d}")
    print(f"滚动窗口     : {args.window} 个交易日")
    print(f"标准差倍数   : ±{args.sigma:g}σ")
    print(f"最新深证成指 : {latest['index_close']:.2f}")
    print(f"最新全A PE   : {latest['pe_ttm']:.2f}")
    print(f"最新CN10Y    : {latest['cn10y']:.2f}%")
    print(f"最新股债利差 : {latest['spread']:.2f} pct-pts")
    print(f"Rolling Mean : {latest['mean']:.2f}")
    print(f"Upper        : {latest['upper']:.2f}")
    print(f"Lower        : {latest['lower']:.2f}")

    states = classify_spread_state(data)
    above_days = int((states == "ABOVE").sum())
    below_days = int((states == "BELOW").sum())
    normal_days = int((states == "NORMAL").sum())

    latest_state = states.iloc[-1]
    print(f"最新通道状态 : {latest_state}")
    print(f"Above天数    : {above_days}")
    print(f"Below天数    : {below_days}")
    print(f"Normal天数   : {normal_days}")
    print(f"输出         : {output}")
    print("=" * 72)

    if args.dual_axis:
        plot_dual_axis(
            data,
            years=args.years,
            window=args.window,
            sigma_mult=args.sigma,
            output_file=output,
        )
    else:
        plot_video_style(
            data,
            years=args.years,
            window=args.window,
            sigma_mult=args.sigma,
            output_file=output,
        )


if __name__ == "__main__":
    main()
