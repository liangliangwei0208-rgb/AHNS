# -*- coding: utf-8 -*-
"""
深证成指 / 中证2000 / 上证指数 + 全A股债利差 + 滚动均值 ± kσ
================================================

核心定义：
    全A股债利差 = 全A盈利收益率 - 中国10年期国债收益率
               = 100 / 全A股PE(TTM) - CN10Y

标准差通道：
    Mean_t  = rolling_mean(Spread, WINDOW)
    Upper_t = Mean_t + STD_MULT * rolling_std(Spread, WINDOW)
    Lower_t = Mean_t - STD_MULT * rolling_std(Spread, WINDOW)

默认：
    WINDOW = 500
    STD_MULT = 1.95
    DISPLAY_YEARS = 10

可直接修改顶部参数，也可用命令行覆盖：
    python strategy/gu_zhai_xi.py --sigma 1
    python strategy/gu_zhai_xi.py --sigma 2
    python strategy/gu_zhai_xi.py --sigma 1.5 --window 250 --years 6
    python strategy/gu_zhai_xi.py --no-csi2000-index --no-shanghai-index

中证2000、上证指数与深证成指按图内首个共同交易日起点比较；
图中比较线是深成指等价点位，原始指数收盘点位仅用于读取和控制台输出。

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
from matplotlib import font_manager

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategy.gu_zhai_xi_data import MarketDataCache, required_trade_dates
from tools.configs.equity_bond_spread_configs import EBS_WINDOW, EBS_STD_MULTIPLIER
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.legend_handler import HandlerTuple
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter, MaxNLocator


# ============================================================
# 用户最常修改的参数
# ============================================================

DISPLAY_YEARS = 10          # 图中显示最近多少年
WINDOW = EBS_WINDOW        # 与ETF价格图EBS状态带共用默认参数。
STD_MULT = EBS_STD_MULTIPLIER  # 标准差倍数：1.0=±1σ, 2.0=±2σ

INDEX_SYMBOL = "sz399001"  # 深证成指

# 三条指数可以分别在这里关闭，也可以用命令行 --no-*-index 临时覆盖。
SHOW_SHENZHEN_INDEX = True
SHOW_CSI2000_INDEX = True
SHOW_SHANGHAI_INDEX = True
INDEX_NAMES = {"shenzhen": "深证成指", "csi2000": "中证2000", "shanghai": "上证指数"}

# ============================================================
# 深证成指“状态分段着色”参数 —— 可直接修改
# ============================================================
COLOR_INDEX_BY_SPREAD = True

# 正常状态：lower <= spread <= upper
INDEX_NORMAL_COLOR = "#2F4B66"      # 常态：深蓝灰

# 股债利差突破上轨：spread > upper
INDEX_ABOVE_UPPER_COLOR = "#2E8B57"  # 利差高于上轨：绿色

# 股债利差跌破下轨：spread < lower
INDEX_BELOW_LOWER_COLOR = "#C43C39"  # 利差低于下轨：砖红

INDEX_NORMAL_WIDTH = 1.05
INDEX_EXTREME_WIDTH = 1.35

# 三个市场共用同一股债利差上下轨判定；各自颜色独立，避免误认成同一条线。
INDEX_COLORS = {
    "shenzhen": {"NORMAL": INDEX_NORMAL_COLOR, "ABOVE": INDEX_ABOVE_UPPER_COLOR, "BELOW": INDEX_BELOW_LOWER_COLOR},
    "csi2000": {"NORMAL": "#79607C", "ABOVE": "#408F7A", "BELOW": "#B85468"},
    "shanghai": {"NORMAL": "#9C7A46", "ABOVE": "#6B8E23", "BELOW": "#A95634"},
}
INDEX_WIDTHS = {"shenzhen": (INDEX_NORMAL_WIDTH, INDEX_EXTREME_WIDTH), "csi2000": (1.0, 1.3), "shanghai": (1.0, 1.3)}
# 颜色表达共同估值状态，线型表达不同指数；黑白印刷时仍可区分。
INDEX_LINESTYLES = {"shenzhen": "solid", "csi2000": (0, (4, 1.6)), "shanghai": (0, (1.2, 1.4))}
SPREAD_COLOR = "#B7791F"
CHANNEL_COLOR = "#64748B"
CHART_FIGSIZE = (7.2, 4.9)
CHART_DPI = 600

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
def _load_chart_sources(years: int, window: int, *, comparison_symbols=(), cache_dir: Path | None = None, now=None):
    dates = required_trade_dates(years, window, now=now)
    location = cache_dir or Path(__file__).resolve().parents[1] / "cache"
    with MarketDataCache(location, dates, now=now, pe_columns=PE_COLUMN_CANDIDATES) as store:
        index, pe, bond = store.load_all()
        comparisons = {symbol: store.load_index(symbol) for symbol in comparison_symbols}
        return index, pe, bond, comparisons


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
    *,
    visible_indices: tuple[str, ...] = ("shenzhen", "csi2000", "shanghai"),
    cache_dir: Path | None = None,
    now=None,
) -> pd.DataFrame:
    """
    以深证成指交易日为主轴，
    将全A PE和10Y国债按最近可得历史值 backward 对齐。
    """

    comparisons_needed = tuple(key for key in ("csi2000", "shanghai") if key in visible_indices)
    # 未指定时保留原调用方式；共享加载方可显式传入缓存目录和运行时间。
    source_options = {}
    if cache_dir is not None:
        source_options["cache_dir"] = cache_dir
    if now is not None:
        source_options["now"] = now
    index_df, pe_df, bond_df, comparisons = _load_chart_sources(
        years, window, comparison_symbols=comparisons_needed, **source_options,
    )

    data = pd.merge_asof(
        index_df.sort_values("date"),
        pe_df.sort_values("date"),
        on="date",
        direction="backward",
    )

    # 比较指数只按真实同日收盘合并，不向前填充，避免跨缺口连成虚构走势。
    for key in comparisons_needed:
        extra = comparisons[key]
        if not extra.empty:
            data = data.merge(extra, on="date", how="left", validate="one_to_one")
        else:
            data[f"{key}_close"] = np.nan

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


def prepare_index_overlays(df: pd.DataFrame, visible_indices: tuple[str, ...]):
    """比较指数按首个共同交易日等比映射到深证点位；原始收盘价仍保留。"""
    out = df.copy()
    requested = [key for key in ("csi2000", "shanghai") if key in visible_indices and f"{key}_close" in out]
    for key in requested:
        out[f"{key}_display"] = np.nan
    if not requested or out.empty:
        return out, None
    keys = []
    for key in requested:
        out[f"{key}_close"] = pd.to_numeric(out[f"{key}_close"], errors="coerce")
        usable = np.isfinite(out[f"{key}_close"]) & out[f"{key}_close"].gt(0)
        if usable.sum() >= 2:
            keys.append(key)
    if not keys:
        return out, None
    valid = np.isfinite(pd.to_numeric(out["index_close"], errors="coerce")) & (out["index_close"] > 0)
    for key in keys:
        valid &= np.isfinite(out[f"{key}_close"]) & (out[f"{key}_close"] > 0)
    if not valid.any():
        return out, None
    anchor_pos = int(np.flatnonzero(valid.to_numpy())[0])
    anchor = out.iloc[anchor_pos]
    base = float(anchor["index_close"])
    for key in keys:
        values = out[f"{key}_close"]
        out[f"{key}_display"] = (values / float(anchor[f"{key}_close"]) * base).where(values.gt(0) & np.isfinite(values))
        out.loc[out.index[:anchor_pos], f"{key}_display"] = np.nan
    return out, pd.Timestamp(anchor["date"])


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
    candidates = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "DejaVu Sans",
    ]
    installed = {font.name for font in font_manager.fontManager.ttflist}
    plt.rcParams["font.sans-serif"] = [name for name in candidates if name in installed] or ["DejaVu Sans"]
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


def draw_colored_index(
    ax, df: pd.DataFrame, *, key: str, date_col: str = "date",
    price_col: str | None = None, zorder: int = 6,
):
    """
    三条指数共用真实股债利差状态，逐线段着色；缺失交易日不跨日连线。
    """
    if df is None or len(df) < 2 or key not in INDEX_COLORS:
        return None
    if price_col is None:
        price_col = "index_close" if key == "shenzhen" else f"{key}_display"
    if price_col not in df:
        return None
    work = df[[date_col, price_col, "spread", "upper", "lower"]].copy()
    dates = pd.to_datetime(work[date_col])
    x = mdates.date2num(dates.to_numpy())
    y = pd.to_numeric(work[price_col], errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(x) & np.isfinite(y) & (y > 0)
    if np.count_nonzero(valid[:-1] & valid[1:]) == 0:
        return None
    contiguous = valid[:-1] & valid[1:]
    states = classify_spread_state(work).to_numpy()
    # 每个线段采用“右端交易日”的状态。
    # 这样突破发生在某日时，从该日开始显示新颜色。
    # 同状态的相邻线段合成一次连续笔划；避免虚线每天重置成密集小点。
    ranges, segment_states = [], []
    start = None
    for i, connected in enumerate(contiguous):
        state = states[i+1]
        if not connected:
            start = None
            continue
        if start is not None and state == segment_states[-1]:
            ranges[-1] = (start, i+2)
        else:
            start = i
            ranges.append((i, i+2))
            segment_states.append(state)
    segments = [np.column_stack([x[start:end], y[start:end]]) for start, end in ranges]
    color_map = INDEX_COLORS[key]
    normal_width, extreme_width = INDEX_WIDTHS[key]
    width_map = {
        "NORMAL": normal_width,
        "ABOVE": extreme_width,
        "BELOW": extreme_width,
    }

    colors = [color_map[s] for s in segment_states]
    widths = [width_map[s] for s in segment_states]

    collection = LineCollection(
        segments,
        colors=colors,
        linewidths=widths,
        alpha=.96,
        linestyles=INDEX_LINESTYLES[key],
        zorder=zorder,
        capstyle="round",
        joinstyle="round",
    )
    ax.add_collection(collection)

    # LineCollection 不会自动参与 autoscale，所以显式更新范围。
    ax.update_datalim(np.column_stack([x[valid], y[valid]]))
    ax.autoscale_view()

    return collection


def draw_colored_shenzhen_index(
    ax, df: pd.DataFrame, *, date_col: str = "date", price_col: str = "index_close", zorder: int = 6,
):
    """保留旧入口；深证成指使用蓝灰、绿色、砖红的状态配色。"""
    return draw_colored_index(ax, df, key="shenzhen", date_col=date_col, price_col=price_col, zorder=zorder)


def current_index_state_color(df: pd.DataFrame, key: str = "shenzhen") -> str:
    """返回最新交易日指数应使用的状态颜色。"""
    if df is None or df.empty:
        return INDEX_COLORS[key]["NORMAL"]

    last = df.iloc[-1]

    if last["spread"] > last["upper"]:
        return INDEX_COLORS[key]["ABOVE"]

    if last["spread"] < last["lower"]:
        return INDEX_COLORS[key]["BELOW"]

    return INDEX_COLORS[key]["NORMAL"]


def visible_index_legend_items(visible_indices: tuple[str, ...]):
    """紧凑指数图例；名称和线型表达身份，状态含义在单独的说明行表达。"""
    handles, labels = [], []
    for key in visible_indices:
        colors = INDEX_COLORS[key]
        handles.append(Line2D([0], [0], color=colors["NORMAL"], lw=INDEX_WIDTHS[key][0],
                              linestyle=INDEX_LINESTYLES[key]))
        labels.append(INDEX_NAMES[key])
    return handles, labels


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


def _png_output_path(output_file: Path) -> Path:
    """PNG是唯一图片交付格式，拒绝把矢量文件或改名的PNG误发到邮件。"""
    path = Path(output_file)
    if path.suffix.lower() != ".png":
        raise ValueError("仅支持PNG图片，输出路径必须以 .png 结尾")
    return path


def plot_video_style(
    df: pd.DataFrame,
    years: int,
    window: int,
    sigma_mult: float,
    output_file: Path,
    visible_indices: tuple[str, ...] = ("shenzhen", "csi2000", "shanghai"),
    show_plot: bool = True,
):
    """兼容原入口；上下对齐展示指数比较和真实利差，不再混用点位与百分点。"""
    output_file = _png_output_path(output_file)
    # 只改展示：共同起点、EBS公式、阈值、缓存及传入数据均保持原状。
    data, overlay_anchor = prepare_index_overlays(df, visible_indices)
    with plt.rc_context({"font.size": 8, "axes.unicode_minus": False,
                         "text.color": "#26313D", "axes.labelcolor": "#53606C",
                         "xtick.color": "#53606C", "ytick.color": "#53606C",
                         "axes.linewidth": .6, "axes.edgecolor": "#B8C0C8"}):
        setup_font()
        # 绑定实际字体列表；局部rc退出后，图仍能正确重绘中文。
        plt.rcParams["font.family"] = plt.rcParams["font.sans-serif"]
        fig, (price_ax, spread_ax) = plt.subplots(
            2, 1, sharex=True, figsize=CHART_FIGSIZE, facecolor="white",
            gridspec_kw={"height_ratios": [1.65, 1.0]})
        try:
            # 标题、指数图例、状态说明与最新值分别预留空间，不压住曲线。
            fig.subplots_adjust(left=.115, right=.96, top=.77, bottom=.20, hspace=.17)
            drawn_keys = []
            for key in visible_indices:
                price_col = "index_close" if key == "shenzhen" else f"{key}_display"
                if price_col not in data or data[price_col].notna().sum() < 2:
                    continue
                if COLOR_INDEX_BY_SPREAD:
                    drawn = draw_colored_index(price_ax, data, key=key, price_col=price_col, zorder=4)
                    if drawn is None:
                        continue
                else:
                    price_ax.plot(data["date"], data[price_col], color=INDEX_COLORS[key]["NORMAL"],
                                  linewidth=INDEX_WIDTHS[key][0], linestyle=INDEX_LINESTYLES[key], zorder=4)
                drawn_keys.append(key)
                valid = data.loc[np.isfinite(pd.to_numeric(data[price_col], errors="coerce"))]
                point = valid.iloc[-1]
                price_ax.scatter(point["date"], point[price_col], s=10,
                                 color=current_index_state_color(valid, key) if COLOR_INDEX_BY_SPREAD else INDEX_COLORS[key]["NORMAL"],
                                 zorder=5, edgecolors="white", linewidths=.4)

            # 用真实百分点画通道，弱化上下轨，保留单独的均值与利差阅读层级。
            spread_ax.fill_between(data["date"], data["lower"], data["upper"],
                                   facecolor=CHANNEL_COLOR, alpha=.075, linewidth=0,
                                   label=f"±{sigma_mult:g}σ通道", zorder=1).set_gid("spread-channel")
            for column in ("upper", "lower"):
                spread_ax.plot(data["date"], data[column], color=CHANNEL_COLOR,
                               linewidth=.65, alpha=.6, zorder=2)
            mean_line, = spread_ax.plot(data["date"], data["mean"], color=CHANNEL_COLOR,
                                       linewidth=.85, linestyle=(0, (4, 2)), label=f"{window}D均值", zorder=3)
            spread_line, = spread_ax.plot(data["date"], data["spread"], color=SPREAD_COLOR,
                                         linewidth=1.15, label="股债利差", zorder=4)
            latest = data.iloc[-1]
            spread_ax.scatter(latest["date"], latest["spread"], s=12, color=SPREAD_COLOR,
                              zorder=5, edgecolors="white", linewidths=.4)

            price_ax.set_ylabel("深成指等价点位" if any(k != "shenzhen" for k in drawn_keys) else "深证成指点位", fontsize=8)
            spread_ax.set_ylabel("股债利差 / 百分点", fontsize=8)
            price_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
            price_ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
            spread_ax.yaxis.set_major_locator(MaxNLocator(nbins=3))
            for axis in (price_ax, spread_ax):
                axis.set_facecolor("white")
                axis.grid(axis="y", color="#DDE2E7", linewidth=.45, zorder=0)
                axis.set_axisbelow(True)
                axis.spines[["top", "right"]].set_visible(False)
                axis.tick_params(axis="both", labelsize=7.5, length=2.5, width=.5, pad=4)
                axis.margins(y=.10)
            price_ax.tick_params(axis="x", bottom=False, labelbottom=False)
            price_ax.spines["bottom"].set_visible(False)
            if not drawn_keys:
                # 三个开关都关闭或数据均不足时，不留下空白指数区域。
                price_ax.set_visible(False)
                spread_ax.set_position([.115, .20, .845, .57])
            span = (data["date"].max()-data["date"].min()).days / 365.25
            if span >= 2:
                spread_ax.xaxis.set_major_locator(mdates.YearLocator(base=2 if span >= 7 else 1))
                spread_ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
            else:
                locator = mdates.AutoDateLocator(minticks=3, maxticks=6)
                spread_ax.xaxis.set_major_locator(locator)
                spread_ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
            spread_ax.margins(x=.012)
            title = "指数走势与全A股债利差" if drawn_keys else "全A股债利差"
            if len(drawn_keys) == 3:
                title = "三指数走势与全A股债利差"
            fig.text(.115, .956, title, fontsize=12, weight="semibold", va="top")
            fig.text(.115, .892, f"近{years}年观察  |  {window}交易日滚动均值 ± {sigma_mult:g}σ  |  截至 {latest['date']:%Y-%m-%d}",
                     fontsize=7.5, color="#697582")
            handles, labels = visible_index_legend_items(tuple(drawn_keys))
            if handles:
                fig.legend(handles, labels, loc="lower left", bbox_to_anchor=(.105, .783),
                           ncol=3, frameon=False, fontsize=7.5, handlelength=2.5,
                           columnspacing=1.6, borderaxespad=0)
            # 指数身份与共同阈值分开解释，避免九个长图例项。
            spread_ax.legend([spread_line, mean_line, Patch(facecolor=CHANNEL_COLOR, alpha=.12)],
                             ["股债利差", f"{window}D均值", f"±{sigma_mult:g}σ通道"],
                             loc="upper left", ncol=3, frameon=False, fontsize=6.5,
                             handlelength=2.1, columnspacing=1, borderaxespad=.5)
            if COLOR_INDEX_BY_SPREAD and drawn_keys:
                state_handles = [Line2D([0], [0], color=INDEX_ABOVE_UPPER_COLOR, lw=1.5),
                                 Line2D([0], [0], color="#64748B", lw=1.2),
                                 Line2D([0], [0], color=INDEX_BELOW_LOWER_COLOR, lw=1.5)]
                fig.legend(state_handles, ["利差高于上轨 · 相对低估", "通道内 · 常态", "利差低于下轨 · 相对高估"],
                           loc="lower left", bbox_to_anchor=(.105, .107), ncol=3, frameon=False,
                           fontsize=6.5, handlelength=1.8, columnspacing=1.2, borderaxespad=0)
            fig.text(.115, .070, f"最新：利差 {latest['spread']:.2f} pp    全A PE(TTM) {latest['pe_ttm']:.2f}    中国10年国债 {latest['cn10y']:.2f}%",
                     fontsize=7.2, color="#53606C")
            note = (f"指数按 {overlay_anchor:%Y-%m-%d} 共同起点等比比较，比较线非各自实际点位。"
                    if overlay_anchor is not None and any(k != "shenzhen" for k in drawn_keys)
                    else "指数使用真实收盘点位。") if drawn_keys else "指数比较已隐藏；利差口径不变。"
            if COLOR_INDEX_BY_SPREAD and drawn_keys:
                note += "色段共用全A利差阈值。"
            fig.text(.115, .027, note, fontsize=6.2, color="#7B8490")
            output_file = Path(output_file)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            # 仅PNG；固定画布尺寸，避免tight裁剪改变不同日期的排版。
            fig.savefig(output_file, format="png", dpi=CHART_DPI, facecolor="white")
            if show_plot:
                plt.show()
        finally:
            plt.close(fig)


def plot_dual_axis(
    df: pd.DataFrame,
    years: int,
    window: int,
    sigma_mult: float,
    output_file: Path,
    visible_indices: tuple[str, ...] = ("shenzhen", "csi2000", "shanghai"),
    show_plot: bool = True,
):
    """
    金融含义更严格的双轴版本：
    左轴=深证成指等价点位（比较指数按共同起点等比换算）
    右轴=真实股债利差（百分点）
    """

    output_file = _png_output_path(output_file)
    setup_font()
    data, overlay_anchor = prepare_index_overlays(df, visible_indices)

    fig, ax_index = plt.subplots(
        figsize=(11.4, 7.2)
    )

    ax_spread = ax_index.twinx()

    drawn_keys, plain_handles = [], []
    for key in visible_indices:
        price_col = "index_close" if key == "shenzhen" else f"{key}_display"
        if price_col not in data or data[price_col].notna().sum() < 2:
            continue
        if COLOR_INDEX_BY_SPREAD:
            drawn = draw_colored_index(ax_index, data, key=key, price_col=price_col, zorder=6)
            if drawn is None:
                continue
        else:
            drawn = ax_index.plot(data["date"], data[price_col], color=INDEX_COLORS[key]["NORMAL"],
                                  linewidth=INDEX_WIDTHS[key][0], label=INDEX_NAMES[key], zorder=6)[0]
            plain_handles.append(drawn)
        drawn_keys.append(key)

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

    ax_index.set_ylabel("深成指等价点位" if any(key != "shenzhen" for key in drawn_keys) else "Shenzhen Component Index")
    ax_spread.set_ylabel("Equity–Bond Spread (percentage points)")

    ax_index.grid(
        True,
        axis="y",
        linestyle=(0, (3, 4)),
        linewidth=0.8,
        alpha=0.25,
    )

    # 双轴图左右均占用刻度空间，年份标签可避免长区间日期互相压住。
    ax_index.xaxis.set_major_locator(mdates.YearLocator())
    ax_index.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    metric_handles = [
        line_spread,
        line_upper,
        line_mean,
        line_lower,
    ]

    if COLOR_INDEX_BY_SPREAD:
        index_handles, index_labels = visible_index_legend_items(tuple(drawn_keys))
    else:
        index_handles, index_labels = plain_handles, [h.get_label() for h in plain_handles]
    handles = index_handles + metric_handles

    ax_index.legend(
        handles,
        index_labels + [h.get_label() for h in metric_handles],
        loc="upper left",
        bbox_to_anchor=(0, -0.105),
        ncol=4,
        frameon=False,
        fontsize=7.2,
        handlelength=2.2,
        columnspacing=1.0,
        labelspacing=0.55,
        handler_map={tuple: HandlerTuple(ndivide=None)},
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

    if overlay_anchor is not None and any(key != "shenzhen" for key in drawn_keys):
        ax_index.text(0.015, 0.947, f"指数相对走势：{overlay_anchor:%Y-%m-%d}共同起点；比较线非实际点位；色段共用股债利差上下轨",
                      transform=ax_index.transAxes, ha="left", va="top", fontsize=7.2, color="#777777")

    fig.tight_layout()
    fig.subplots_adjust(bottom=0.19)

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fig.savefig(
        output_file,
        format="png",
        dpi=CHART_DPI,
        bbox_inches="tight",
    )

    if show_plot:
        plt.show()
    plt.close(fig)


# ============================================================
# CLI
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="深证成指 / 中证2000 / 上证指数 + 全A股债利差标准差通道"
    )
    parser.add_argument("--no-show", dest="show_plot", action="store_false",
                        help="仅保存图片，不打开绘图窗口（Service使用）")

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

    parser.add_argument("--shenzhen-index", action=argparse.BooleanOptionalAction,
                        default=SHOW_SHENZHEN_INDEX, help="显示/隐藏深证成指；默认按顶部配置")
    parser.add_argument("--csi2000-index", action=argparse.BooleanOptionalAction,
                        default=SHOW_CSI2000_INDEX, help="显示/隐藏中证2000；默认按顶部配置")
    parser.add_argument("--shanghai-index", action=argparse.BooleanOptionalAction,
                        default=SHOW_SHANGHAI_INDEX, help="显示/隐藏上证指数；默认按顶部配置")

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="输出图片路径",
    )

    args = parser.parse_args()
    if args.output:
        try:
            _png_output_path(args.output)
        except ValueError as error:
            parser.error(str(error))
    return args


def main():
    args = parse_args()
    if not args.show_plot:
        # 无人值守时明确使用无界面后端，避免GUI后端等待或弹窗。
        plt.switch_backend("Agg")
    visible_indices = tuple(key for key, enabled in (
        ("shenzhen", args.shenzhen_index),
        ("csi2000", args.csi2000_index),
        ("shanghai", args.shanghai_index),
    ) if enabled)

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
        visible_indices=visible_indices,
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
    print("图中指数     : " + ("、".join(INDEX_NAMES[key] for key in visible_indices) or "均隐藏"))
    for key in ("csi2000", "shanghai"):
        column = f"{key}_close"
        if key in visible_indices and column in data:
            available = data.dropna(subset=[column])
            if not available.empty:
                item = available.iloc[-1]
                print(f"最新{INDEX_NAMES[key]} : {item[column]:.2f} ({item['date']:%Y-%m-%d})")
            else:
                print(f"最新{INDEX_NAMES[key]} : 数据暂不可用，图中跳过")
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
            visible_indices=visible_indices,
            show_plot=args.show_plot,
        )
    else:
        plot_video_style(
            data,
            years=args.years,
            window=args.window,
            sigma_mult=args.sigma,
            output_file=output,
            visible_indices=visible_indices,
            show_plot=args.show_plot,
        )


if __name__ == "__main__":
    main()
