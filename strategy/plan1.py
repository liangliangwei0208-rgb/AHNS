"""红利低波 ETF 的日线/周线 BOLL(20,2) 对照试验。"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.configs.rsi_configs import RSI_ANALYSIS_CONFIGS
from tools.rsi_data import (
    _annotate_period_rsi_signals,
    _expand_price_ylim_for_period_markers,
    _plot_segmented_by_rsi,
    _plot_volume_bar,
    _setup_chinese_font,
    add_bollinger_bands,
    rsi_analyze_index,
)


SYMBOL = "512890"
DISPLAY_NAME = "红利低波华泰ETF"
OUTPUT_FILE = PROJECT_ROOT / "output" / "honglidibo_weekly_boll_experiment.png"

# 日线颜色与正式 RSI 图一致；周线刻意使用紫/棕两色和点划线加以区分。
DAILY_BOLL_UPPER_COLOR = "#E05263"
DAILY_BOLL_LOWER_COLOR = "#25AE88"
WEEKLY_BOLL_UPPER_COLOR = "#7451B5"
WEEKLY_BOLL_LOWER_COLOR = "#9A5C1A"
DAILY_BOLL_LINESTYLE = (0, (5, 3))
WEEKLY_BOLL_LINESTYLE = (0, (8, 3, 1, 3))


def get_weekly_boll_line_specs() -> list[tuple[str, str, tuple[int, tuple[int, ...]]]]:
    """返回周线 BOLL 的绘制规则；只保留上轨和下轨。"""
    return [
        ("BOLL_UPPER", WEEKLY_BOLL_UPPER_COLOR, WEEKLY_BOLL_LINESTYLE),
        ("BOLL_LOWER", WEEKLY_BOLL_LOWER_COLOR, WEEKLY_BOLL_LINESTYLE),
    ]


def _get_hongli_rsi_kwargs() -> dict:
    """读取正式红利低波 RSI 参数，避免试验图和正式口径漂移。"""
    for config in RSI_ANALYSIS_CONFIGS:
        kwargs = config.get("kwargs", {})
        if str(kwargs.get("symbol", "")).strip() == SYMBOL:
            result = dict(kwargs)
            result.update(
                {
                    "do_plot": False,
                    "show_plot": False,
                    "return_signals": True,
                    "save_signal_table": False,
                    "include_realtime": True,
                }
            )
            return result

    raise RuntimeError(f"RSI 配置中未找到红利低波 ETF: {SYMBOL}")


def _ensure_daily_boll(df: pd.DataFrame) -> pd.DataFrame:
    """优先复用完整历史已算好的日线 BOLL；缺失时才临时补算。"""
    required_columns = {"BOLL_MID", "BOLL_UPPER", "BOLL_LOWER"}
    if required_columns.issubset(df.columns):
        return df.copy()
    return add_bollinger_bands(df, window=20, std_multiplier=2.0)


def _draw_boll_bands(
    ax,
    df: pd.DataFrame,
    line_specs,
    linewidth: float,
    zorder: float,
    drawstyle: str = "default",
) -> None:
    """按指定轨道绘制 BOLL 虚线，不生成图例。"""
    for column, color, linestyle in line_specs:
        ax.plot(
            df["date"],
            df[column],
            color=color,
            linestyle=linestyle,
            linewidth=linewidth,
            alpha=0.92,
            zorder=zorder,
            drawstyle=drawstyle,
        )


def draw_weekly_boll_bands(ax, weekly_boll_df: pd.DataFrame) -> None:
    """以右连续阶梯线叠加周 BOLL，避免在日线图上产生跨周斜线插值。"""
    _draw_boll_bands(
        ax=ax,
        df=weekly_boll_df,
        line_specs=get_weekly_boll_line_specs(),
        linewidth=1.35,
        zorder=1.2,
        drawstyle="steps-post",
    )


def render_hongli_weekly_boll_experiment(
    hist: pd.DataFrame,
    weekly_df: pd.DataFrame,
    daily_signal_df: pd.DataFrame | None,
    weekly_signal_df: pd.DataFrame | None,
    monthly_signal_df: pd.DataFrame | None,
    daily_rsi_high: float,
    daily_rsi_low: float,
    weekly_rsi_high: float,
    weekly_rsi_low: float,
    monthly_rsi_high: float,
    monthly_rsi_low: float,
    output_file: Path = OUTPUT_FILE,
) -> Path:
    """绘制日线 BOLL 加周线 BOLL 上下轨的三联试验图。"""
    if hist is None or hist.empty:
        raise ValueError("红利低波日线数据为空，无法绘图。")

    _setup_chinese_font()
    plot_df = _ensure_daily_boll(hist)
    weekly_boll_df = add_bollinger_bands(weekly_df, window=20, std_multiplier=2.0)

    display_start = pd.to_datetime(plot_df["date"]).min()
    display_end = pd.to_datetime(plot_df["date"]).max()
    weekly_boll_df = weekly_boll_df.loc[
        weekly_boll_df["date"].between(display_start, display_end)
    ].copy()

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)

    _plot_segmented_by_rsi(
        ax=axes[0],
        df=plot_df,
        y_col="close",
        rsi_col="RSI",
        date_col="date",
        rsi_high=daily_rsi_high,
        rsi_low=daily_rsi_low,
        ylabel="收盘价",
        title=f"{DISPLAY_NAME}：日线/周线 BOLL(20,2) 试验",
        show_points=True,
        color_by_rsi=True,
    )

    _draw_boll_bands(
        axes[0],
        plot_df,
        [
            ("BOLL_UPPER", DAILY_BOLL_UPPER_COLOR, DAILY_BOLL_LINESTYLE),
            ("BOLL_LOWER", DAILY_BOLL_LOWER_COLOR, DAILY_BOLL_LINESTYLE),
        ],
        linewidth=1.10,
        zorder=1.0,
    )
    draw_weekly_boll_bands(axes[0], weekly_boll_df)

    # 给日、周、月 RSI 信号标记留出上下空间。
    axes[0].margins(y=0.20)
    axes[0].autoscale_view()
    _expand_price_ylim_for_period_markers(
        axes[0],
        [
            {
                "signal_df": weekly_signal_df,
                "rsi_col": "RSI_W",
                "rsi_high": weekly_rsi_high,
                "rsi_low": weekly_rsi_low,
                "high_offset_points": 10,
                "low_offset_points": 10,
                "marker_fontsize": 13,
            },
            {
                "signal_df": monthly_signal_df,
                "rsi_col": "RSI_M",
                "rsi_high": monthly_rsi_high,
                "rsi_low": monthly_rsi_low,
                "high_offset_points": 15,
                "low_offset_points": 15,
                "marker_fontsize": 18,
            },
        ],
    )
    _annotate_period_rsi_signals(
        axes[0],
        weekly_signal_df,
        "RSI_W",
        weekly_rsi_high,
        weekly_rsi_low,
        high_marker="▲",
        low_marker="▼",
        high_offset_points=10,
        low_offset_points=10,
        marker_fontsize=13,
    )
    _annotate_period_rsi_signals(
        axes[0],
        monthly_signal_df,
        "RSI_M",
        monthly_rsi_high,
        monthly_rsi_low,
        high_marker="★",
        low_marker="★",
        high_offset_points=15,
        low_offset_points=15,
        marker_fontsize=18,
    )

    _plot_volume_bar(axes[1], plot_df)
    _plot_segmented_by_rsi(
        ax=axes[2],
        df=plot_df,
        y_col="RSI",
        rsi_col="RSI",
        date_col="date",
        rsi_high=daily_rsi_high,
        rsi_low=daily_rsi_low,
        ylabel="RSI",
        title="RSI",
        show_points=True,
        color_by_rsi=True,
    )
    axes[2].axhline(daily_rsi_high, linestyle="--", linewidth=1, color="red")
    axes[2].axhline(daily_rsi_low, linestyle="--", linewidth=1, color="black")
    axes[2].set_ylim(0, 100)

    plt.tight_layout()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_file, dpi=180)
    plt.close(fig)
    return output_file


def main() -> Path:
    """使用正式红利低波配置生成一张不覆盖正式图的试验输出。"""
    kwargs = _get_hongli_rsi_kwargs()
    (
        hist,
        daily_signal_df,
        weekly_signal_df,
        monthly_signal_df,
        _signal_table,
        weekly_df,
        _monthly_df,
    ) = rsi_analyze_index(**kwargs)

    output_path = render_hongli_weekly_boll_experiment(
        hist=hist,
        weekly_df=weekly_df,
        daily_signal_df=daily_signal_df,
        weekly_signal_df=weekly_signal_df,
        monthly_signal_df=monthly_signal_df,
        daily_rsi_high=float(kwargs["daily_rsi_high"]),
        daily_rsi_low=float(kwargs["daily_rsi_low"]),
        weekly_rsi_high=float(kwargs["weekly_rsi_high"]),
        weekly_rsi_low=float(kwargs["weekly_rsi_low"]),
        monthly_rsi_high=float(kwargs["monthly_rsi_high"]),
        monthly_rsi_low=float(kwargs["monthly_rsi_low"]),
    )
    print(f"周线 BOLL 试验图已保存: {output_path}")
    print("日线 BOLL: 红色上轨 / 绿色下轨；周线 BOLL: 紫色上轨 / 棕色下轨。")
    return output_path


if __name__ == "__main__":
    main()
