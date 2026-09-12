"""共享 VIX 日线历史读取与均线差计算。

Yahoo 的 ``range=max`` 请求可能自动降采样为月线，不能用于 200 日均线。
这里固定使用明确日期区间和 ``1d`` 间隔；调用方自行传入缓存路径，
从而让主程序与 strategy 试验分别保留各自的运行缓存。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
import time
from typing import Callable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from tools.configs.cache_policy_configs import VIX_DAILY_HISTORY_MAX_ROWS, VIX_DAILY_STALE_RETRY_HOURS
from tools.configs.market_calendar_configs import MARKET_CALENDAR_NAMES, MARKET_CLOSE_BUFFER_HOURS
from tools.runtime_stats import record_market_event, timed_market_call


VIX_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
VIX_DAILY_REQUEST_TIMEOUT_SECONDS = 15


def latest_completed_us_session_date(now: datetime | None = None) -> pd.Timestamp | None:
    """返回已过收盘缓冲期的最近完整美股交易日，日历不可用时返回 None。"""
    try:
        import pandas_market_calendars as mcal

        now_bj = now or datetime.now(ZoneInfo("Asia/Shanghai"))
        if now_bj.tzinfo is None:
            now_bj = now_bj.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        else:
            now_bj = now_bj.astimezone(ZoneInfo("Asia/Shanghai"))
        calendar = mcal.get_calendar(MARKET_CALENDAR_NAMES["US"])
        schedule = calendar.schedule(
            start_date=(now_bj - timedelta(days=14)).date(),
            end_date=now_bj.date(),
        )
        if schedule is None or schedule.empty:
            return None
        close_ready_at = schedule["market_close"] + pd.Timedelta(hours=MARKET_CLOSE_BUFFER_HOURS)
        ready = schedule.loc[close_ready_at <= pd.Timestamp(now_bj)]
        if ready.empty:
            return None
        return pd.Timestamp(ready.index[-1]).normalize()
    except Exception:
        return None


def _load_valid_vix_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        cached = clean_vix_daily_history(pd.read_csv(path, encoding="utf-8-sig"), "VIX 缓存")
    except Exception:
        return pd.DataFrame()
    return cached if is_daily_vix_history(cached) else pd.DataFrame()


def _cache_attempt_is_due(path: Path, *, now: datetime, retry_hours: int) -> bool:
    try:
        attempted_at = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
    except OSError:
        return True
    return now - attempted_at >= timedelta(hours=max(1, int(retry_hours)))


def _mark_cache_attempt(path: Path, now: datetime) -> None:
    """只更新文件时间，记录失败重试窗口而不产生 Git 内容变更。"""
    try:
        timestamp = now.timestamp()
        os.utime(path, (timestamp, timestamp))
    except OSError:
        return


def _bounded_vix_history(data: pd.DataFrame) -> pd.DataFrame:
    """只保留 200/20 均线所需的有限日线预热，防止 CSV 长期增长。"""
    return data.tail(VIX_DAILY_HISTORY_MAX_ROWS).reset_index(drop=True)


def clean_vix_daily_history(vix_df: pd.DataFrame, label: str = "VIX") -> pd.DataFrame:
    """统一 VIX 日线字段，拒绝缺失日期或收盘价的异常记录。"""
    if vix_df is None or vix_df.empty:
        raise ValueError(f"{label} 历史数据为空。")
    if not {"date", "close"}.issubset(vix_df.columns):
        raise ValueError(f"{label} 缺少 date/close 字段。")

    data = vix_df.loc[:, ["date", "close"]].copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    if getattr(data["date"].dt, "tz", None) is not None:
        data["date"] = data["date"].dt.tz_convert("America/New_York").dt.tz_localize(None)
    data["date"] = data["date"].dt.normalize()
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    data = data.dropna(subset=["date", "close"])
    data = data.drop_duplicates(subset=["date"], keep="last")
    data = data.sort_values("date").reset_index(drop=True)
    if data.empty:
        raise ValueError(f"{label} 没有可用的日期和收盘价。")
    return data


def add_vix_moving_average_spread(
    vix_df: pd.DataFrame,
    *,
    long_window: int = 200,
    short_window: int = 20,
) -> pd.DataFrame:
    """计算 VIX 长均线减短均线，默认是 200 日均线减 20 日均线。"""
    if int(long_window) <= 0 or int(short_window) <= 0:
        raise ValueError("VIX 均线窗口必须是正整数。")
    if int(long_window) < int(short_window):
        raise ValueError("长均线窗口不能小于短均线窗口。")

    data = clean_vix_daily_history(vix_df)
    data["VIX_MA_LONG"] = data["close"].rolling(int(long_window), min_periods=int(long_window)).mean()
    data["VIX_MA_SHORT"] = data["close"].rolling(int(short_window), min_periods=int(short_window)).mean()
    data["VIX_MA_SPREAD"] = data["VIX_MA_LONG"] - data["VIX_MA_SHORT"]
    return data


def is_daily_vix_history(vix_df: pd.DataFrame) -> bool:
    """判断历史是否真为日线，拒绝 Yahoo 自动降采样得到的月线缓存。"""
    try:
        data = clean_vix_daily_history(vix_df)
    except (TypeError, ValueError):
        return False
    if len(data) < 2:
        return False

    median_gap_days = data["date"].diff().dropna().dt.total_seconds().median() / 86_400
    return bool(pd.notna(median_gap_days) and median_gap_days <= 7)


def fetch_daily_vix_history_from_yahoo(
    *,
    days: int,
    request_get: Callable[..., requests.Response] | None = None,
) -> pd.DataFrame:
    """以明确起止时间请求 Yahoo VIX 日线，避免 ``range=max`` 降采样。"""
    if int(days) <= 0:
        raise ValueError("VIX 历史天数必须是正整数。")

    # 美股一年约 252 个交易日；额外留出节假日与偶发休市缓冲。
    calendar_days = max(int(int(days) * 365.25 / 252) + 30, 365)
    period2 = int(time.time())
    period1 = period2 - calendar_days * 24 * 60 * 60
    request_get = request_get or requests.get
    response = request_get(
        VIX_YAHOO_CHART_URL,
        params={
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        },
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
        },
        timeout=VIX_DAILY_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    result = response.json().get("chart", {}).get("result") or []
    if not result:
        raise RuntimeError("Yahoo 未返回 VIX 日线数据。")

    payload = result[0]
    timestamps = payload.get("timestamp") or []
    quote = (payload.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    if not timestamps or not closes:
        raise RuntimeError("Yahoo VIX 日线缺少 timestamp 或 close 字段。")

    count = min(len(timestamps), len(closes))
    # Yahoo 时间戳按纽约交易日还原，防止 UTC 日期向前或向后偏移。
    dates = (
        pd.to_datetime(timestamps[:count], unit="s", utc=True)
        .tz_convert("America/New_York")
        .tz_localize(None)
        .normalize()
    )
    return clean_vix_daily_history(
        pd.DataFrame({"date": dates, "close": closes[:count]}),
        "Yahoo VIX 日线",
    ).tail(int(days)).reset_index(drop=True)


def fetch_vix_daily_history(
    *,
    days: int,
    cache_file: str | Path,
    request_get: Callable[..., requests.Response] | None = None,
    now: datetime | None = None,
    stale_retry_hours: int = VIX_DAILY_STALE_RETRY_HOURS,
) -> pd.DataFrame:
    """按最近完整美股交易日复用 VIX 日线，落后时限流刷新并保留失败兜底。"""
    path = Path(cache_file)
    # GitHub runner 使用 UTC，本机使用北京时间；统一为北京时间避免把 UTC 墙上时间误判。
    check_now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if check_now.tzinfo is None:
        check_now = check_now.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    else:
        check_now = check_now.astimezone(ZoneInfo("Asia/Shanghai"))
    requested_days = max(1, min(int(days), VIX_DAILY_HISTORY_MAX_ROWS))
    cached = _load_valid_vix_cache(path)
    if not cached.empty:
        bounded_cached = _bounded_vix_history(cached)
        if len(bounded_cached) != len(cached):
            # 这是一次真实的保留策略裁剪；之后命中缓存不再改写文件。
            bounded_cached.to_csv(path, index=False, encoding="utf-8-sig")
        cached = bounded_cached
    expected_date = latest_completed_us_session_date(check_now)
    cached_latest = pd.to_datetime(cached["date"], errors="coerce").max() if not cached.empty else pd.NaT

    if not cached.empty and expected_date is not None and pd.notna(cached_latest) and cached_latest >= expected_date:
        record_market_event(
            action="vix_daily_cache",
            source="local_csv_latest_complete_close",
            market="US",
            ticker="^VIX",
            outcome="cache_hit",
            cache_hit=True,
        )
        print(f"[CACHE] VIX 日线缓存已覆盖最近完整美股交易日: {cached_latest:%Y-%m-%d}")
        return cached.tail(requested_days).reset_index(drop=True)

    if not cached.empty and not _cache_attempt_is_due(path, now=check_now, retry_hours=stale_retry_hours):
        record_market_event(
            action="vix_daily_cache",
            source="local_csv_stale_retry_limited",
            market="US",
            ticker="^VIX",
            outcome="cache_hit",
            cache_hit=True,
        )
        print(f"[CACHE] VIX 日线缓存尚未到重试时间，继续使用 {cached_latest:%Y-%m-%d}")
        return cached.tail(requested_days).reset_index(drop=True)

    try:
        fresh = timed_market_call(
            lambda: fetch_daily_vix_history_from_yahoo(days=requested_days, request_get=request_get),
            action="vix_daily_network_fetch",
            source="yahoo_chart",
            market="US",
            ticker="^VIX",
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = _bounded_vix_history(fresh)
        fresh.to_csv(path, index=False, encoding="utf-8-sig")
        return fresh.tail(requested_days).reset_index(drop=True)
    except Exception as error:
        if not cached.empty:
            _mark_cache_attempt(path, check_now)
            record_market_event(
                action="vix_daily_cache",
                source="local_csv_after_network_failure",
                market="US",
                ticker="^VIX",
                outcome="cache_hit",
                cache_hit=True,
                error=str(error),
            )
            print(f"[WARN] VIX 联网获取失败，改用本地日线缓存: {error}")
            return cached.tail(requested_days).reset_index(drop=True)
        if path.exists():
            print("[WARN] VIX 缓存不是日线，拒绝用于 200/20 日均线计算。")
        raise RuntimeError(f"VIX 联网获取失败且没有本地日线缓存: {error}") from error


__all__ = [
    "VIX_YAHOO_CHART_URL",
    "add_vix_moving_average_spread",
    "clean_vix_daily_history",
    "fetch_daily_vix_history_from_yahoo",
    "fetch_vix_daily_history",
    "is_daily_vix_history",
    "latest_completed_us_session_date",
]
