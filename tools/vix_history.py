"""共享 VIX 日线历史读取与均线差计算。

Yahoo 的 ``range=max`` 请求可能自动降采样为月线，不能用于 200 日均线。
这里固定使用明确日期区间和 ``1d`` 间隔；调用方自行传入缓存路径，
从而让主程序与 strategy 试验分别保留各自的运行缓存。
"""

from __future__ import annotations

from pathlib import Path
import time
from typing import Callable

import numpy as np
import pandas as pd
import requests


VIX_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
VIX_DAILY_REQUEST_TIMEOUT_SECONDS = 15


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
) -> pd.DataFrame:
    """优先刷新日线，网络失败时只回退到已验证为日线的明确缓存路径。"""
    path = Path(cache_file)
    try:
        fresh = fetch_daily_vix_history_from_yahoo(days=days, request_get=request_get)
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh.to_csv(path, index=False, encoding="utf-8-sig")
        return fresh.tail(int(days)).reset_index(drop=True)
    except Exception as error:
        if path.exists():
            try:
                cached = pd.read_csv(path, encoding="utf-8-sig")
                cached = clean_vix_daily_history(cached, "VIX 缓存")
            except Exception:
                cached = pd.DataFrame()
            if not cached.empty and is_daily_vix_history(cached):
                print(f"[WARN] VIX 联网获取失败，改用本地日线缓存: {error}")
                return cached.tail(int(days)).reset_index(drop=True)
            print("[WARN] VIX 缓存不是日线，拒绝用于 200/20 日均线计算。")
        raise RuntimeError(f"VIX 联网获取失败且没有本地日线缓存: {error}") from error


__all__ = [
    "VIX_YAHOO_CHART_URL",
    "add_vix_moving_average_spread",
    "clean_vix_daily_history",
    "fetch_daily_vix_history_from_yahoo",
    "fetch_vix_daily_history",
    "is_daily_vix_history",
]
