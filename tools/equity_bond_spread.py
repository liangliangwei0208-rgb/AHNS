"""复用策略正式EBS；展示层只在Price面板绘制极端状态带。"""
from pathlib import Path

import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

from tools.configs.equity_bond_spread_configs import (
    EBS_BAND_ALPHA, EBS_HIGH_COLOR, EBS_LOW_COLOR, EBS_HISTORY_ROWS, EBS_HISTORY_YEARS,
)


def ebs_states(frame: pd.DataFrame) -> pd.Series:
    """正式策略ABOVE/BELOW对应展示HIGH/LOW；缺值不伪装成NORMAL。"""
    from strategy.gu_zhai_xi import classify_spread_state
    values = frame[["spread", "upper", "lower"]].apply(pd.to_numeric, errors="coerce")
    usable = np.isfinite(values).all(axis=1)
    states = classify_spread_state(values).map({"ABOVE": "HIGH", "BELOW": "LOW", "NORMAL": "NORMAL"})
    return states.where(usable, None)


def load_ebs_states(cache_dir="cache", *, now=None) -> pd.DataFrame:
    """只加载深证交易日主轴及PE/国债；两张ETF共用，不下载比较指数。"""
    from strategy.gu_zhai_xi import build_indicator, WINDOW, STD_MULT
    data = build_indicator(EBS_HISTORY_YEARS, WINDOW, STD_MULT, visible_indices=("shenzhen",),
                           cache_dir=Path(cache_dir), now=now)
    data = data[["date", "spread", "mean", "upper", "lower"]].copy()
    data["state"] = ebs_states(data)
    return data.tail(EBS_HISTORY_ROWS).reset_index(drop=True)


def draw_ebs_state_band(ax, price_df, state_df, *, output_dpi=None, include_ene=False):
    """严格同日匹配；缺值/盘中未有正式状态时断开，连续同状态合成一段。"""
    if (price_df is None or price_df.empty or "date" not in price_df
            or state_df is None or state_df.empty or not {"date", "state"}.issubset(state_df)):
        return []
    dates = pd.to_datetime(price_df["date"], errors="coerce").dt.normalize().dropna().drop_duplicates().sort_values()
    states = state_df[["date", "state"]].copy()
    states["date"] = pd.to_datetime(states["date"], errors="coerce").dt.normalize()
    states = states.dropna(subset=["date"]).drop_duplicates("date", keep="last")
    aligned = pd.DataFrame({"date": dates}).merge(states, on="date", how="left")
    if aligned.empty:
        return []
    x = mdates.date2num(aligned["date"].to_numpy())
    if len(x) == 1:
        left, right = x-.5, x+.5
    else:
        mid = (x[:-1]+x[1:])/2
        left = np.r_[x[0]-(mid[0]-x[0]), mid]
        right = np.r_[mid, x[-1]+(x[-1]-mid[-1])]
    from tools.market_breadth import price_band_layout, add_state_band_label
    layout = price_band_layout(ax, output_dpi, include_ebs=True, include_ene=include_ene)
    styles = {"HIGH": (EBS_HIGH_COLOR, layout["ebs_high"]), "LOW": (EBS_LOW_COLOR, layout["ebs_low"])}
    runs = [value if value in styles else None for value in aligned["state"]]
    patches, start = [], 0
    for end in range(1, len(runs)+1):
        if end < len(runs) and runs[end] == runs[start]:
            continue
        if runs[start] is not None:
            color, bottom = styles[runs[start]]
            patch = Rectangle((left[start], bottom), right[end-1]-left[start], layout["height"],
                              transform=ax.get_xaxis_transform(), facecolor=color, edgecolor="none",
                              alpha=EBS_BAND_ALPHA, zorder=2.5, clip_on=True)
            patch.set_gid("ebs-state-band")
            # add_artist不更新dataLim，价格范围和BOLL/信号保持原样。
            ax.add_artist(patch)
            patches.append(patch)
            add_state_band_label(ax, patch, "EBS", output_dpi=output_dpi)
        start = end
    return patches
