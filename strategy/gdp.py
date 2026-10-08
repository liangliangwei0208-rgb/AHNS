# -*- coding: utf-8 -*-
"""
A股总市值 / GDP + 深证成指（近10年）
口径：
1. 按估值状态分色的月度阶梯线（左轴）：
   (上海市场市价总值 + 深圳市场市价总值) / 中国名义GDP(TTM)
2. 按同一估值状态分色的细虚线指数曲线（右轴）：
   深证成指 399001 的真实点位；月度复核CSV仍保留原缩放列。
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
import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import akshare as ak
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.ticker import MultipleLocator, FormatStrFormatter, FuncFormatter


# ============================================================
# 用户可直接修改的参数
# ============================================================

LOOKBACK_YEARS = 10 #近十年

HIGH_VALUATION_THRESHOLD = 0.765 #高估阈值
LOW_VALUATION_THRESHOLD = 0.60  #低估阈值
EXTREME_LOW_THRESHOLD = 0.55    #极端低估阈值

# 仅供本独立十年图使用，不改动RSI图的阈值或全局Matplotlib样式。
VALUATION_COLORS = {"OVER": "#C43C39", "NEUTRAL": "#6B7280",
                    "LOW": "#2E8B57", "DEEP LOW": "#14532D"}
VALUATION_BACKGROUND_ALPHAS = {"OVER": .05, "LOW": .05, "DEEP LOW": .07}
# 指数同样映射估值状态，使用较浅配色与细虚线，和MC/GDP深色实线阶梯区分。
INDEX_STATE_COLORS = {"OVER": "#E27A63", "NEUTRAL": "#A1A8B2",
                      "LOW": "#6DAD78", "DEEP LOW": "#347A50", "UNKNOWN": "#C2C7CD"}
INDEX_AXIS_COLOR = "#516A7A"
INDEX_LINESTYLE = (0, (3, 1.8))
PUBLICATION_FIGSIZE = (7.2, 4.2)
PUBLICATION_DPI = 600

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

def _macro_frames():
    from tools.a_share_valuation import load_macro_data, normalize_market_cap, normalize_gdp
    cap, gdp, _ = load_macro_data(cache_dir=CACHE_DIR)
    if cap.empty or gdp.empty:
        raise RuntimeError("沪深市值/GDP共享缓存无有效数据")
    # JSON派生列有序列化舍入。复用标准化函数从原始分量重建，保持原图
    # 浮点加法、差分和滚动求和顺序；不改技术图已经记录的可用时间事件。
    cap = normalize_market_cap(cap.rename(columns={"period_date":"数据日期",
        "sse_market_cap_yi":"市价总值-上海", "szse_market_cap_yi":"市价总值-深圳"}))
    gdp = normalize_gdp(gdp.rename(columns={"period_date":"季度",
        "gdp_ytd_yi":"国内生产总值-绝对值"}))
    cap = cap.rename(columns={"period_date": "date"})[
        ["date", "sse_market_cap_yi", "szse_market_cap_yi", "market_cap_yi"]].copy()
    gdp = gdp.rename(columns={"period_date": "date"})[
        ["date", "year", "quarter", "gdp_ytd_yi", "gdp_single_quarter_yi", "gdp_ttm_yi"]]
    return cap, gdp.dropna(subset=["gdp_ttm_yi"]).copy()


def fetch_market_cap_monthly() -> pd.DataFrame:
    """保留旧入口和列结构，原始数据统一从共享缓存加载。"""
    return _macro_frames()[0]


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
    """累计转单季及连续四季 TTM 的校验复用共享实现。"""
    return _macro_frames()[1]


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
    # 一次加载市值和GDP；不把技术图的 observed 事件用作修订历史。
    cap, gdp = _macro_frames()
    out = pd.merge_asof(cap.sort_values("date"),
                        gdp[["date", "gdp_ttm_yi"]].sort_values("date"),
                        on="date", direction="backward")
    out = out.dropna(subset=["gdp_ttm_yi"]).copy()
    out["market_cap_to_gdp"] = out["market_cap_yi"] / out["gdp_ttm_yi"]
    return out


# ============================================================
# 5. 绘图
# ============================================================

def _valuation_state(value: float) -> str:
    """边界严格对应脚本阈值：高估含上界、低估及极端低估含各自上界。"""
    if value >= HIGH_VALUATION_THRESHOLD:
        return "OVER"
    if value <= EXTREME_LOW_THRESHOLD:
        return "DEEP LOW"
    if value <= LOW_VALUATION_THRESHOLD:
        return "LOW"
    return "NEUTRAL"


def _draw_ratio_steps(ax, frame: pd.DataFrame) -> None:
    """相邻观测之间持平；跳变由新状态着色，NaN处结束整段，绝不补线。"""
    xs, ys, state, previous = [], [], None, None

    def finish():
        if xs:
            line, = ax.step(xs, ys, where="post", color=VALUATION_COLORS[state],
                            linewidth=1.8, solid_capstyle="butt", zorder=4)
            line.set_gid("mc-gdp-curve")

    for date, value in frame[["date", "market_cap_to_gdp"]].itertuples(index=False, name=None):
        if not np.isfinite(value):
            finish()
            xs, ys, state, previous = [], [], None, None
            continue
        next_state = _valuation_state(value)
        if previous is None:
            xs, ys, state = [date], [value], next_state
        else:
            xs.append(date)
            ys.append(previous)
            if state != next_state:
                finish()
                xs, ys, state = [date], [previous], next_state
            xs.append(date)
            ys.append(value)
        previous = value
    finish()


def _place_endpoint_labels(fig, annotations, obstacles) -> None:
    """以实际字体边界尝试放置端点值；放不下时底部摘要仍保留完整数值。"""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    occupied = [artist.get_window_extent(renderer).expanded(1.03, 1.12) for artist in obstacles]
    for annotation in annotations:
        area = annotation.axes.bbox
        for offset, align in [((-6, 8), "right"), ((-6, -14), "right"),
                              ((-40, 8), "right"), ((-40, -14), "right"),
                              ((6, 8), "left"), ((6, -14), "left")]:
            annotation.set_position(offset)
            annotation.set_ha(align)
            box = annotation.get_window_extent(renderer)
            if (box.x0 >= area.x0+2 and box.x1 <= area.x1-2 and
                    box.y0 >= area.y0+2 and box.y1 <= area.y1-2 and
                    not any(box.overlaps(other) for other in occupied)):
                occupied.append(box.expanded(1.03, 1.12))
                break
        else:
            annotation.set_visible(False)


def _draw_index_by_valuation(ax, frame: pd.DataFrame, ratio: pd.DataFrame) -> pd.DataFrame:
    """只映射已出现的月度观测；缺少估值时为未分类灰色，不倒用未来月份。"""
    aligned = pd.merge_asof(frame, ratio[["date", "market_cap_to_gdp"]], on="date", direction="backward")
    aligned["valuation_state"] = aligned["market_cap_to_gdp"].map(
        lambda value: _valuation_state(value) if np.isfinite(value) else "UNKNOWN")

    def draw(start, end, state):
        segment = aligned.iloc[start:end]
        line, = ax.plot(segment["date"], segment["close"], color=INDEX_STATE_COLORS[state],
                        linewidth=1., alpha=.95, linestyle=INDEX_LINESTYLE, zorder=2)
        line.set_gid("shenzhen-index-curve")

    start, state = None, None
    for position, row in enumerate(aligned.itertuples()):
        if not np.isfinite(row.close):
            if start is not None:
                draw(start, position, state)
            start, state = None, None
        elif start is None:
            start, state = position, row.valuation_state
        elif row.valuation_state != state:
            # 原段连到本日真实收盘点，新状态自该观测日开始，连接处不虚构价格。
            draw(start, position+1, state)
            start, state = position, row.valuation_state
    if start is not None:
        draw(start, len(aligned), state)
    return aligned


def plot_chart(ratio: pd.DataFrame, index_df: pd.DataFrame) -> None:
    """单绘图区双纵轴；只读输入，仅输出原路径的高分辨率PNG。"""
    ratio = ratio[["date", "market_cap_to_gdp"]].copy()
    index_df = index_df[["date", "close"]].copy()
    for frame, column in ((ratio, "market_cap_to_gdp"), (index_df, "close")):
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame.loc[~np.isfinite(frame[column]), column] = np.nan
        frame.dropna(subset=["date"], inplace=True)
        frame.sort_values("date", inplace=True)
        frame.drop_duplicates("date", keep="last", inplace=True)
    latest_available = max(ratio["date"].max(), index_df["date"].max())
    start = latest_available - pd.DateOffset(years=LOOKBACK_YEARS)
    ratio_history = ratio  # 映射指数时允许读取展示起点之前的最后已知观测。
    ratio = ratio.loc[ratio["date"].between(start, latest_available)]
    index_df = index_df.loc[index_df["date"].between(start, latest_available)]
    valid_ratio = ratio.dropna(subset=["market_cap_to_gdp"])
    valid_index = index_df.dropna(subset=["close"])
    if valid_ratio.empty or valid_index.empty:
        raise RuntimeError("最近10年缺少有效的MC/GDP或深证成指数据")
    if not 0 < EXTREME_LOW_THRESHOLD < LOW_VALUATION_THRESHOLD < HIGH_VALUATION_THRESHOLD:
        raise ValueError("估值阈值须满足：0 < 极端低估 < 低估 < 高估")

    # 用rc_context隔离字体与边框，其他市场图不继承此处样式。
    with plt.rc_context({"font.family": "sans-serif", "font.size": 8.5,
                         "axes.unicode_minus": False, "axes.linewidth": .65,
                         "axes.edgecolor": "#9AA2AB", "text.color": "#26313D",
                         "axes.labelcolor": "#26313D", "xtick.color": "#53606C",
                         "ytick.color": "#53606C", "xtick.labelsize": 8,
                         "ytick.labelsize": 8, "path.simplify": False}):
        setup_chinese_font()
        chinese_font = plt.rcParams["font.sans-serif"][0]
        installed = {font.name for font in font_manager.fontManager.ttflist}
        plt.rcParams["font.sans-serif"] = (["Arial"] if "Arial" in installed else []) + [chinese_font, "DejaVu Sans"]
        # 显式字体族才会逐字回退：单写sans-serif会只选Arial，中文将变成缺字方框。
        plt.rcParams["font.family"] = plt.rcParams["font.sans-serif"]
        fig, ax = plt.subplots(figsize=PUBLICATION_FIGSIZE, facecolor="white")
        try:
            fig.subplots_adjust(left=.105, right=.895, bottom=.235, top=.795)
            index_ax = ax.twinx()
            # 价格线居后；两轴背景透明，浅色区间不会完全遮挡指数曲线。
            ax.set_zorder(2)
            index_ax.set_zorder(1)
            ax.patch.set_visible(False)
            index_ax.patch.set_visible(False)

            values = np.r_[valid_ratio["market_cap_to_gdp"].to_numpy(),
                           HIGH_VALUATION_THRESHOLD, LOW_VALUATION_THRESHOLD, EXTREME_LOW_THRESHOLD]
            padding = max(np.ptp(values)*.08, .04)
            low = max(0., np.floor((values.min()-padding)/.05)*.05)
            high = np.ceil((values.max()+padding)/.05)*.05
            ax.set_ylim(low, high)
            index_min, index_max = valid_index["close"].min(), valid_index["close"].max()
            index_padding = max((index_max-index_min)*.05, abs(index_max)*.005, 1.)
            index_ax.set_ylim(index_min-index_padding, index_max+index_padding)
            ax.set_xlim(start, latest_available)

            regimes = [(HIGH_VALUATION_THRESHOLD, high, "OVER", "高估"),
                       (EXTREME_LOW_THRESHOLD, LOW_VALUATION_THRESHOLD, "LOW", "低估"),
                       (low, EXTREME_LOW_THRESHOLD, "DEEP LOW", "极端低估")]
            for bottom, top, state, label in regimes:
                ax.axhspan(bottom, top, color=VALUATION_COLORS[state],
                           alpha=VALUATION_BACKGROUND_ALPHAS[state], linewidth=0, zorder=.1)
                ax.text(.012, (bottom+top)/2, label, transform=ax.get_yaxis_transform(),
                        color=VALUATION_COLORS[state], fontsize=7.5, va="center", zorder=5,
                        bbox=dict(facecolor="white", edgecolor="none", alpha=.65, pad=.7))
            for level, state, label in [(HIGH_VALUATION_THRESHOLD, "OVER", f"{HIGH_VALUATION_THRESHOLD:.3f}"),
                                        (LOW_VALUATION_THRESHOLD, "LOW", f"{LOW_VALUATION_THRESHOLD:.2f}"),
                                        (EXTREME_LOW_THRESHOLD, "DEEP LOW", f"{EXTREME_LOW_THRESHOLD:.2f}")]:
                ax.axhline(level, color=VALUATION_COLORS[state], linewidth=.6,
                           alpha=.45, linestyle=(0, (3, 3)), zorder=1)
                ax.annotate(label, (.993, level), xycoords=ax.get_yaxis_transform(),
                            xytext=(0, 2), textcoords="offset points", ha="right", va="bottom",
                            color=VALUATION_COLORS[state], fontsize=7, zorder=5,
                            bbox=dict(facecolor="white", edgecolor="none", alpha=.7, pad=.5))

            mapped_index = _draw_index_by_valuation(index_ax, index_df, ratio_history)
            _draw_ratio_steps(ax, ratio)
            ax.set_ylabel("MC/GDP", fontsize=9, labelpad=6)
            index_ax.set_ylabel("深证成指（点）", fontsize=9, labelpad=7, color=INDEX_AXIS_COLOR)
            index_ax.tick_params(axis="y", colors=INDEX_AXIS_COLOR, width=.65, length=3)
            ax.tick_params(width=.65, length=3)
            ax.yaxis.set_major_locator(MultipleLocator(.10))
            ax.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))
            index_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
            ax.xaxis.set_major_locator(mdates.YearLocator(2))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
            ax.grid(axis="y", color="#D8DEE5", linewidth=.5, alpha=.55, zorder=.5)
            index_ax.grid(False)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            for side in ("top", "bottom", "left"):
                index_ax.spines[side].set_visible(False)
            index_ax.spines["right"].set_color(INDEX_AXIS_COLOR)
            index_ax.spines["right"].set_alpha(.55)

            fig.text(.105, .952, "A股宏观估值与深证成指｜近10年", fontsize=12, fontweight="medium", va="top")
            fig.text(.105, .890, "MC/GDP =（沪市市价总值 + 深市市价总值）/ 中国名义 GDP(TTM)",
                     fontsize=8, color="#637080", va="top")
            index_proxy = Line2D([], [], color=INDEX_STATE_COLORS["NEUTRAL"], linewidth=1., linestyle=INDEX_LINESTYLE)
            fig.legend([Line2D([], [], color=VALUATION_COLORS["NEUTRAL"], linewidth=1.8, drawstyle="steps-post"), index_proxy],
                       ["MC/GDP", "深证成指"], loc="lower left", bbox_to_anchor=(.105, .802),
                       ncol=2, frameon=False, fontsize=8.5, handlelength=2, columnspacing=1.8, borderaxespad=0)
            ratio_last, index_last = valid_ratio.iloc[-1], valid_index.iloc[-1]
            latest_color = VALUATION_COLORS[_valuation_state(ratio_last["market_cap_to_gdp"])]
            index_state = mapped_index.loc[mapped_index["date"].eq(index_last["date"]), "valuation_state"].iloc[-1]
            annotations = []
            for axis, date, value, color, text in [
                (ax, ratio_last["date"], ratio_last["market_cap_to_gdp"], latest_color, f"{ratio_last['market_cap_to_gdp']:.3f}"),
                (index_ax, index_last["date"], index_last["close"], INDEX_STATE_COLORS[index_state], f"{index_last['close']:,.0f}")]:
                axis.scatter([date], [value], s=13, color=color, zorder=6, clip_on=True)
                annotations.append(axis.annotate(text, (date, value), xytext=(-6, 8),
                                                 textcoords="offset points", ha="right", fontsize=8,
                                                 color=color, zorder=6,
                                                 bbox=dict(facecolor="white", edgecolor="none", alpha=.8, pad=.6)))
            fig.text(.105, .112, f"最新 MC/GDP：{ratio_last['market_cap_to_gdp']:.3f}（{ratio_last['date']:%Y-%m}）"
                     f"    深证成指：{index_last['close']:,.2f}（{index_last['date']:%Y-%m-%d}）", fontsize=8)
            fig.text(.105, .073, "指数颜色按MC/GDP月度观测日期映射；实线阶梯对应左轴，细虚线对应右轴。",
                     fontsize=6.8, color="#7B8490")
            fig.text(.105, .034, "MC/GDP history: historical revised series；历史修订数据，非严格无前视回测信号。",
                     fontsize=6.8, color="#7B8490")
            obstacles = fig.texts + fig.legends + [text for text in ax.texts if text not in annotations]
            _place_endpoint_labels(fig, annotations, obstacles)
            output = Path(OUTPUT_PNG)
            output.parent.mkdir(parents=True, exist_ok=True)
            # 固定纸面尺寸，不用bbox_inches=tight改变出版物排版尺寸。
            fig.savefig(output, format="png", dpi=PUBLICATION_DPI, facecolor="white")
        finally:
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


def _run_cli(argv=None) -> None:
    """Service无界面入口；数据获取和main计算流程保持原样。"""
    parser = argparse.ArgumentParser(description="生成十年MC/GDP与深证成指出版图")
    parser.add_argument("--no-show", action="store_true", help="使用Agg后端，无界面出图，供Service调用")
    args = parser.parse_args(argv)
    if args.no_show:
        plt.switch_backend("Agg")
    main()


if __name__ == "__main__":
    _run_cli()
