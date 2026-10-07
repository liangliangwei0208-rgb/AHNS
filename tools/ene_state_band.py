"""周线 ENE 回顾展示：复用已取得的行情，不请求网络、不写缓存。"""
from functools import lru_cache

import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

from tools.configs.ene_configs import (
    ENE_WINDOW, ENE_UPPER_PCT, ENE_LOWER_PCT,
    ENE_HIGH_COLOR, ENE_LOW_COLOR, ENE_BAND_ALPHA, ENE_LABEL_FONTSIZE,
)
from tools.configs.market_calendar_configs import MARKET_CALENDAR_NAMES

WEEKLY_COLUMNS = ["week_end_date", "date", "close", "ma", "upper", "lower",
                  "state", "deviation", "label", "provisional"]
DAILY_COLUMNS = ["date", "week_end_date", "ene_state", "ene_label"]


@lru_cache(maxsize=16)
def _week_market_close(market, week_end_date):
    """复用本地日历规则；同市场/周在内存中复用，不新增磁盘缓存或请求。"""
    import pandas_market_calendars as mcal
    calendar = mcal.get_calendar(MARKET_CALENDAR_NAMES[market])
    schedule = calendar.schedule(start_date=week_end_date-pd.Timedelta(days=4), end_date=week_end_date)
    if schedule is None or schedule.empty or "market_close" not in schedule:
        raise ValueError("无法确认该周最后交易时段")
    closes = pd.to_datetime(schedule.market_close, errors="coerce", utc=True)
    if closes.isna().any():
        raise ValueError("交易日历包含无效收盘时间")
    return closes.max()


def build_weekly_ene_frame(price_df, *, as_of=None, market="CN"):
    """完整行情先预热10周；历史周用最终收盘，当前周只用已观测价格。

    历史周内的色带使用该周最终收盘，是回顾展示，不是无前视日频信号。
    无效的最后一笔收盘使该周及其后未完成预热的周无状态。
    market 决定交易日所属时区；无时区的 as_of 按该市场当地时间解释。
    """
    if price_df is None or price_df.empty or not {"date", "close"}.issubset(price_df):
        return pd.DataFrame(columns=WEEKLY_COLUMNS)
    market = str(market).upper()
    timezone = {"CN": "Asia/Shanghai", "US": "America/New_York"}[market]
    observed_at = pd.Timestamp.now(tz=timezone) if as_of is None else pd.Timestamp(as_of)
    observed_at = (observed_at.tz_localize(timezone) if observed_at.tzinfo is None
                   else observed_at.tz_convert(timezone))
    local_day = observed_at.tz_localize(None).normalize()
    work = price_df.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    work = work.loc[work.date.notna() & (work.date <= local_day)]
    work = work.sort_values("date").drop_duplicates("date", keep="last")
    if work.empty:
        return pd.DataFrame(columns=WEEKLY_COLUMNS)
    valid = np.isfinite(work.close) & work.close.gt(0)
    original_close = work.set_index("date")["close"].where(valid.to_numpy())
    # 通用 resampler 会丢弃 NaN；占位只用于保留周和真实日期，聚合后立即还原缺值。
    work["close"] = work.close.where(valid, 0.)
    from tools.rsi_data import resample_ohlcv
    weekly = resample_ohlcv(work, period="W")[["date", "close"]].copy()
    weekly["close"] = weekly.date.map(original_close)
    weekly["week_end_date"] = weekly.date.dt.to_period("W-FRI").dt.end_time.dt.normalize()
    weekly["ma"] = weekly.close.rolling(ENE_WINDOW, min_periods=ENE_WINDOW).mean()
    weekly["upper"] = weekly.ma * (1 + ENE_UPPER_PCT / 100.)
    weekly["lower"] = weekly.ma * (1 - ENE_LOWER_PCT / 100.)
    usable = np.isfinite(weekly[["close", "ma", "upper", "lower"]]).all(axis=1)
    weekly["state"] = pd.Series(None, index=weekly.index, dtype=object)
    weekly.loc[usable, "state"] = "NORMAL"
    # 只吸收乘法的机器精度误差（例如100×1.11略大于111），不扩大业务阈值。
    roundoff = 4 * np.finfo(float).eps
    high = usable & (weekly.close.ge(weekly.upper) | np.isclose(weekly.close, weekly.upper, rtol=roundoff, atol=0))
    low = usable & (weekly.close.le(weekly.lower) | np.isclose(weekly.close, weekly.lower, rtol=roundoff, atol=0))
    weekly.loc[high, "state"] = "HIGH"
    weekly.loc[low, "state"] = "LOW"
    weekly["deviation"] = np.nan
    weekly.loc[high, "deviation"] = ((weekly.close[high] - weekly.upper[high]) / weekly.upper[high] * 100).clip(lower=0)
    weekly.loc[low, "deviation"] = ((weekly.lower[low] - weekly.close[low]) / weekly.lower[low] * 100).clip(lower=0)
    weekly["label"] = weekly.deviation.map(lambda v: f"E{v:.1f}" if np.isfinite(v) else None)
    this_week = local_day.to_period("W-FRI").end_time.normalize()
    current_week = weekly.week_end_date.eq(this_week)
    weekly["provisional"] = False
    if current_week.any():
        try:
            # 按实际最后交易日收盘判断，覆盖短周、提前收盘、夏令时和北京时间跨日。
            week_in_progress = observed_at < _week_market_close(market, this_week)
        except Exception:
            # 日历不可核实时保守保留临时标识，仍可显示已有价格计算的状态。
            week_in_progress = True
        weekly.loc[current_week, "provisional"] = week_in_progress
    return weekly[WEEKLY_COLUMNS].reset_index(drop=True)


def expand_weekly_ene_to_price_dates(weekly_frame, price_dates):
    """按同一周精确关联；没有该周观测时不沿用上一周状态。"""
    dates = pd.to_datetime(pd.Series(price_dates), errors="coerce").dt.normalize()
    daily = pd.DataFrame({"date": dates.dropna().drop_duplicates().sort_values()})
    daily["week_end_date"] = daily.date.dt.to_period("W-FRI").dt.end_time.dt.normalize()
    if (weekly_frame is None or weekly_frame.empty
            or not {"week_end_date", "state", "label"}.issubset(weekly_frame)):
        return daily.assign(ene_state=None, ene_label=None)[DAILY_COLUMNS].reset_index(drop=True)
    states = weekly_frame[["week_end_date", "state", "label"]].copy()
    states["week_end_date"] = pd.to_datetime(states.week_end_date, errors="coerce").dt.normalize()
    states = states.dropna(subset=["week_end_date"]).drop_duplicates("week_end_date", keep="last")
    states = states.rename(columns={"state": "ene_state", "label": "ene_label"})
    return daily.merge(states, on="week_end_date", how="left")[DAILY_COLUMNS].reset_index(drop=True)


def draw_ene_state_band(ax, price_df, ene_frame, *, output_dpi=None, include_ebs=False):
    """仅 Price 的周状态带；标签较新周优先，可略超出短段但不超出绘图区。"""
    if (price_df is None or price_df.empty or "date" not in price_df
            or ene_frame is None or ene_frame.empty
            or not {"date", "ene_state", "ene_label"}.issubset(ene_frame)):
        return []
    dates = pd.to_datetime(price_df.date, errors="coerce").dt.normalize().dropna().drop_duplicates().sort_values()
    states = ene_frame[["date", "ene_state", "ene_label"]].copy()
    states["date"] = pd.to_datetime(states.date, errors="coerce").dt.normalize()
    states = states.dropna(subset=["date"]).drop_duplicates("date", keep="last")
    daily = pd.DataFrame({"date": dates}).merge(states, on="date", how="left")
    if daily.empty:
        return []
    x = mdates.date2num(daily.date.to_numpy())
    if len(x) == 1:
        left, right = x - .5, x + .5
    else:
        middle = (x[:-1] + x[1:]) / 2
        left = np.r_[x[0] - (middle[0]-x[0]), middle]
        right = np.r_[middle, x[-1] + (x[-1]-middle[-1])]
    from tools.market_breadth import price_band_layout, add_state_band_label
    layout = price_band_layout(ax, output_dpi, include_ebs=include_ebs, include_ene=True)
    styles = {"HIGH": (ENE_HIGH_COLOR, layout["ene_high"]), "LOW": (ENE_LOW_COLOR, layout["ene_low"])}
    weeks = daily.date.dt.to_period("W-FRI").dt.end_time.dt.normalize()
    runs = [(state, label) if state in styles and isinstance(label, str) and label else None
            for state, label in zip(daily.ene_state, daily.ene_label)]
    patches, start = [], 0
    for end in range(1, len(runs)+1):
        adjacent = end < len(runs) and (weeks.iloc[end] - weeks.iloc[end-1]).days <= 7
        if adjacent and runs[end] == runs[start]:
            continue
        if runs[start] is not None:
            state, label = runs[start]
            color, bottom = styles[state]
            # 交易日中点遇到整周缺口会延伸过远，限定为首末周的周一至周五日期格。
            # 即使两个缺口两侧的状态相同，也不能把没有行情的整周涂满。
            band_left = max(left[start], mdates.date2num(weeks.iloc[start]-pd.Timedelta(days=4.5)))
            band_right = min(right[end-1], mdates.date2num(weeks.iloc[end-1]+pd.Timedelta(days=.5)))
            band = Rectangle((band_left, bottom), band_right-band_left, layout["height"],
                             transform=ax.get_xaxis_transform(), facecolor=color, edgecolor="none",
                             alpha=ENE_BAND_ALPHA, zorder=2.5, clip_on=True)
            band.set_gid("ene-state-band")
            # add_artist 不更新 dataLim，绝不改变价格/BOLL/信号的坐标范围。
            ax.add_artist(band)
            patches.append((band, label))
        start = end
    occupied = []
    renderer = ax.figure.canvas.get_renderer()
    for band, label in reversed(patches):
        text = add_state_band_label(ax, band, label, output_dpi=output_dpi,
                                    fontsize=ENE_LABEL_FONTSIZE, force=True)
        if text is None:
            continue
        bounds = text.get_window_extent(renderer).expanded(1.05, 1.0)
        if any(bounds.overlaps(other) for other in occupied):
            text.remove()
        else:
            text.set_gid("ene-state-label")
            occupied.append(bounds)
    return [band for band, _ in patches]
