"""
Lightweight pre-market overseas fund observation.

This module deliberately stays outside the official overseas estimate cache.
It is intended for manual Beijing-time 17:00-20:30 runs, when US pre-market
quotes may be available but the final US daily bars are not confirmed yet.
"""

from __future__ import annotations

import json
import math
import requests
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from tools.configs.fund_proxy_configs import OVERSEAS_VALID_HOLDING_BOOST
from tools.configs.premarket_configs import (
    PREMARKET_BENCHMARK_SPECS,
    PREMARKET_DEFAULT_RESIDUAL_BENCHMARK_KEY,
    PREMARKET_END_HOUR_BJ,
    PREMARKET_END_MINUTE_BJ,
    PREMARKET_FUND_RESIDUAL_BENCHMARK_MAP,
    PREMARKET_START_HOUR_BJ,
    PREMARKET_START_MINUTE_BJ,
)
from tools.configs.safe_image_style_configs import SAFE_TITLE_STYLE, safe_daily_table_kwargs
from tools.fund_table_image import save_fund_estimate_table_image
from tools.fund_universe import HAIWAI_FUND_CODES
from tools.get_top10_holdings import (
    fetch_cn_security_return_pct,
    fetch_cn_security_return_pct_daily_with_date,
    fetch_hk_return_pct_akshare_spot_em,
    fetch_hk_return_pct_akshare_daily_with_date,
    fetch_hk_return_pct_sina,
    fetch_kr_return_pct_daily_with_date,
    fetch_latest_complete_vix_close,
    fetch_us_return_pct_akshare_spot_em,
    fetch_us_return_pct_akshare_spot_sina,
    get_fund_name,
    get_latest_stock_holdings_df,
)
from tools.paths import (
    FUND_ESTIMATE_CACHE,
    FUND_PURCHASE_LIMIT_CACHE,
    PREMARKET_FAILED_HOLDINGS_REPORT,
    SAFE_HAIWAI_PREMARKET_IMAGE,
    ensure_runtime_dirs,
    relative_path_str,
)
from tools.safe_display import apply_safe_public_watermarks, mask_fund_name


BJ_TZ = ZoneInfo("Asia/Shanghai")
PREMARKET_START_BJ = time(PREMARKET_START_HOUR_BJ, PREMARKET_START_MINUTE_BJ)
PREMARKET_END_BJ = time(PREMARKET_END_HOUR_BJ, PREMARKET_END_MINUTE_BJ)
DISPLAY_RETURN_COLUMN = "盘前模型观察"
PURCHASE_LIMIT_COLUMN = "模型观察基金信息"


@dataclass
class PremarketRunResult:
    generated: bool
    reason: str
    output_file: Path
    report_file: Path
    fund_count: int = 0
    valid_security_count: int = 0
    missing_security_count: int = 0


def now_bj() -> datetime:
    return datetime.now(BJ_TZ)


def coerce_bj_datetime(value: Any | None = None) -> datetime:
    if value is None:
        return now_bj()
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return now_bj()
        dt = datetime.fromisoformat(text.replace(" ", "T"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BJ_TZ)
    return dt.astimezone(BJ_TZ)


def in_premarket_window(check_time: datetime | None = None) -> bool:
    dt = coerce_bj_datetime(check_time)
    current = dt.time().replace(second=0, microsecond=0)
    return PREMARKET_START_BJ <= current <= PREMARKET_END_BJ


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        out = float(value)
        if not math.isfinite(out):
            return None
        return out
    except Exception:
        return None


def estimate_boosted_valid_holding_return(
    weight_return_pairs: Iterable[tuple[Any, Any]],
    *,
    boost: float = OVERSEAS_VALID_HOLDING_BOOST,
) -> tuple[float | None, float, float, float]:
    """
    Estimate by raw valid holding weight times boost, capped at 100%.

    Returns:
        estimated_return_pct, raw_valid_weight_pct, boosted_weight_pct, actual_boost
    """
    pairs: list[tuple[float, float]] = []
    for weight, return_pct in weight_return_pairs:
        weight_f = _safe_float(weight)
        return_f = _safe_float(return_pct)
        if weight_f is None or return_f is None or weight_f <= 0:
            continue
        pairs.append((weight_f, return_f))

    raw_valid_weight_pct = float(sum(weight for weight, _ in pairs))
    if raw_valid_weight_pct <= 0:
        return None, 0.0, 0.0, 0.0

    try:
        boost_f = float(boost)
    except Exception:
        boost_f = 1.0
    if not math.isfinite(boost_f) or boost_f < 0:
        boost_f = 1.0

    boosted_weight_pct = min(100.0, raw_valid_weight_pct * boost_f)
    actual_boost = boosted_weight_pct / raw_valid_weight_pct
    estimated_return_pct = float(
        sum(weight * actual_boost * return_pct / 100.0 for weight, return_pct in pairs)
    )
    return estimated_return_pct, raw_valid_weight_pct, boosted_weight_pct, actual_boost


def estimate_boosted_valid_holding_with_residual(
    weight_return_pairs: Iterable[tuple[Any, Any]],
    *,
    residual_return_pct: Any = None,
    boost: float = OVERSEAS_VALID_HOLDING_BOOST,
) -> dict[str, float | None]:
    """
    Use the same shape as the official overseas stock-holding estimate:
    valid disclosed holdings are boosted first, then the remaining weight is
    estimated by the configured residual benchmark.
    """
    known_return, raw_valid_weight, boosted_weight, actual_boost = estimate_boosted_valid_holding_return(
        weight_return_pairs,
        boost=boost,
    )
    residual_weight_pct = max(0.0, 100.0 - float(boosted_weight or 0.0))
    residual_return = _safe_float(residual_return_pct)
    known_contribution = float(known_return or 0.0)
    residual_contribution = (
        residual_weight_pct * residual_return / 100.0
        if residual_return is not None and residual_weight_pct > 0
        else 0.0
    )

    if known_return is None and residual_return is None:
        estimated_return = None
    else:
        estimated_return = known_contribution + residual_contribution

    return {
        "estimated_return_pct": estimated_return,
        "known_contribution_pct": known_contribution if known_return is not None else None,
        "raw_valid_weight_pct": raw_valid_weight,
        "boosted_weight_pct": boosted_weight,
        "actual_boost": actual_boost,
        "residual_weight_pct": residual_weight_pct,
        "residual_return_pct": residual_return,
        "residual_contribution_pct": residual_contribution,
    }


def normalize_premarket_benchmark_key(value: Any) -> str:
    return str(value or "").strip().lower()


def get_premarket_residual_benchmark_key(fund_code: Any) -> str:
    code = str(fund_code or "").strip().zfill(6)
    key = PREMARKET_FUND_RESIDUAL_BENCHMARK_MAP.get(
        code,
        PREMARKET_DEFAULT_RESIDUAL_BENCHMARK_KEY,
    )
    key = normalize_premarket_benchmark_key(key)
    if key not in PREMARKET_BENCHMARK_SPECS:
        return normalize_premarket_benchmark_key(PREMARKET_DEFAULT_RESIDUAL_BENCHMARK_KEY)
    return key


def _premarket_benchmark_spec(key: Any) -> dict[str, Any] | None:
    key_norm = normalize_premarket_benchmark_key(key)
    spec = PREMARKET_BENCHMARK_SPECS.get(key_norm)
    if not isinstance(spec, dict):
        return None
    out = dict(spec)
    out["key"] = key_norm
    out["label"] = str(out.get("label") or key_norm).strip() or key_norm
    out["ticker"] = str(out.get("ticker") or "").strip().upper()
    out["market"] = str(out.get("market") or "US").strip().upper()
    out["kind"] = str(out.get("kind") or "us_security").strip().lower()
    return out


def _yahoo_realtime_return_pct(symbol: str, *, timeout: int = 12) -> dict[str, Any]:
    """
    Use Yahoo intraday chart with pre/post data enabled.

    This is intentionally not a daily-bar fallback. It only accepts quote points
    from a live/pre/post trading session and calculates against the previous
    regular close supplied by Yahoo metadata.
    """
    symbol_norm = str(symbol or "").strip().upper()
    if not symbol_norm:
        raise RuntimeError("Yahoo symbol 为空")

    encoded = requests.utils.quote(symbol_norm, safe="=")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
    params = {
        "range": "1d",
        "interval": "1m",
        "includePrePost": "true",
        "events": "history",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://finance.yahoo.com/quote/{symbol_norm}",
    }

    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    result = data.get("chart", {}).get("result", [None])[0]
    if not result:
        raise RuntimeError(f"Yahoo 返回结构异常: {symbol_norm}")

    meta = result.get("meta") or {}
    market_state = str(meta.get("marketState") or "").upper()
    previous_close = _safe_float(
        meta.get("regularMarketPreviousClose")
        or meta.get("chartPreviousClose")
        or meta.get("previousClose")
    )
    if previous_close is None or previous_close <= 0:
        raise RuntimeError(f"Yahoo 缺少有效昨收价: {symbol_norm}")

    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []

    latest_ts = None
    latest_price = None
    for ts, price in zip(timestamps, closes):
        price_f = _safe_float(price)
        if price_f is None or price_f <= 0:
            continue
        latest_ts = int(ts)
        latest_price = price_f

    if latest_ts is None or latest_price is None:
        raise RuntimeError(f"Yahoo 没有可用盘前/实时价格点: {symbol_norm}")

    allowed_by_period = False
    periods = meta.get("currentTradingPeriod") or {}
    for name in ("pre", "regular", "post"):
        period = periods.get(name) or {}
        try:
            start = int(period.get("start"))
            end = int(period.get("end"))
        except Exception:
            continue
        if start <= latest_ts <= end:
            allowed_by_period = True
            break

    allowed_states = {"PRE", "REGULAR", "POST", "PREPRE", "POSTPOST"}
    if not allowed_by_period and market_state not in allowed_states:
        raise RuntimeError(
            f"Yahoo 当前不是盘前/实时状态: {symbol_norm}, marketState={market_state or '空'}"
        )

    return_pct = (latest_price / previous_close - 1.0) * 100.0
    quote_time_bj = datetime.fromtimestamp(latest_ts, tz=BJ_TZ).strftime("%Y-%m-%d %H:%M")
    return {
        "return_pct": float(return_pct),
        "source": f"yahoo_chart_intraday_{market_state.lower() or 'session'}",
        "quote_time_bj": quote_time_bj,
        "trade_date": quote_time_bj[:10],
        "status": "traded",
    }


def fetch_us_premarket_return_pct(symbol: str, *, disabled_sources: set[str] | None = None) -> dict[str, Any]:
    disabled_sources = disabled_sources if disabled_sources is not None else set()
    symbol_norm = str(symbol or "").strip().upper()
    errors = []

    if "ak_us_sina" not in disabled_sources:
        try:
            return_pct, source = fetch_us_return_pct_akshare_spot_sina(symbol_norm)
            return {
                "return_pct": float(return_pct),
                "source": source,
                "status": "traded",
                "trade_date": now_bj().date().isoformat(),
                "quote_time_bj": "",
            }
        except Exception as exc:
            message = repr(exc)
            errors.append(f"ak_us_sina: {message}")
            if any(token in message for token in ("JSONDecodeError", "SSLError", "ProxyError", "返回空数据")):
                disabled_sources.add("ak_us_sina")

    if "ak_us_em" not in disabled_sources:
        try:
            return_pct, source = fetch_us_return_pct_akshare_spot_em(symbol_norm)
            return {
                "return_pct": float(return_pct),
                "source": source,
                "status": "traded",
                "trade_date": now_bj().date().isoformat(),
                "quote_time_bj": "",
            }
        except Exception as exc:
            message = repr(exc)
            errors.append(f"ak_us_em: {message}")
            if any(token in message for token in ("JSONDecodeError", "SSLError", "ProxyError", "返回空数据")):
                disabled_sources.add("ak_us_em")

    try:
        return _yahoo_realtime_return_pct(symbol_norm)
    except Exception as exc:
        errors.append(f"yahoo_intraday: {repr(exc)}")

    raise RuntimeError(" | ".join(errors))


def fetch_premarket_benchmark_quote(
    benchmark_key: Any,
    *,
    today: str,
    quote_cache: dict[tuple[str, str], dict[str, Any]],
    disabled_sources: set[str],
) -> dict[str, Any]:
    spec = _premarket_benchmark_spec(benchmark_key)
    if spec is None:
        key_norm = normalize_premarket_benchmark_key(benchmark_key)
        return {
            "benchmark_key": key_norm,
            "label": key_norm or "未知基准",
            "ticker": "",
            "market": "",
            "kind": "",
            "return_pct": None,
            "source": "config_missing",
            "status": "missing",
            "trade_date": today,
            "error": f"盘前基准配置不存在: {benchmark_key}",
        }

    cache_key = (spec["market"], spec["ticker"])
    cached = quote_cache.get(cache_key)
    if isinstance(cached, dict) and (
        cached.get("return_pct") is not None or cached.get("value") is not None or cached.get("status") == "missing"
    ):
        out = dict(cached)
        out.update(
            {
                "benchmark_key": spec["key"],
                "label": spec["label"],
                "ticker": spec["ticker"],
                "market": spec["market"],
                "kind": spec["kind"],
            }
        )
        return out

    try:
        if spec["kind"] == "vix_level":
            vix = fetch_latest_complete_vix_close()
            item = {
                "benchmark_key": spec["key"],
                "label": spec["label"],
                "ticker": spec["ticker"],
                "market": spec["market"],
                "kind": spec["kind"],
                "return_pct": None,
                "value": _safe_float(vix.get("close")),
                "display_value": f"{float(vix['close']):.2f}",
                "trade_date": str(vix.get("date") or today),
                "source": str(vix.get("source", "")),
                "status": "traded",
                "value_type": "level",
            }
        elif spec["market"] == "US":
            quote = fetch_us_premarket_return_pct(spec["ticker"], disabled_sources=disabled_sources)
            item = {
                "benchmark_key": spec["key"],
                "label": spec["label"],
                "ticker": spec["ticker"],
                "market": spec["market"],
                "kind": spec["kind"],
                "return_pct": _safe_float(quote.get("return_pct")),
                "trade_date": str(quote.get("trade_date") or today),
                "source": str(quote.get("source", "")),
                "status": str(quote.get("status", "traded")),
                "value_type": "return_pct",
            }
        else:
            raise RuntimeError(f"盘前基准暂不支持 market={spec['market']}")
    except Exception as exc:
        item = {
            "benchmark_key": spec["key"],
            "label": spec["label"],
            "ticker": spec["ticker"],
            "market": spec["market"],
            "kind": spec["kind"],
            "return_pct": None,
            "value": None,
            "display_value": "",
            "trade_date": today,
            "source": "failed",
            "status": "missing",
            "error": str(exc),
            "value_type": "level" if spec["kind"] == "vix_level" else "return_pct",
        }

    quote_cache[cache_key] = dict(item)
    return item


def _fetch_cn_current_return(code: str, today: str) -> dict[str, Any]:
    errors = []
    try:
        return_pct, trade_date, source = fetch_cn_security_return_pct_daily_with_date(
            str(code).zfill(6),
            end_date=today,
        )
        if str(trade_date) == today:
            return {
                "return_pct": float(return_pct),
                "source": source,
                "status": "traded",
                "trade_date": str(trade_date),
                "quote_time_bj": "",
            }
        errors.append(f"cn_daily_close: trade_date={trade_date}, today={today}")
    except Exception as exc:
        errors.append(f"cn_daily_close: {repr(exc)}")

    try:
        return_pct, source = fetch_cn_security_return_pct(str(code).zfill(6))
    except Exception as exc:
        errors.append(f"cn_realtime: {repr(exc)}")
        raise RuntimeError(" | ".join(errors))

    return {
        "return_pct": float(return_pct),
        "source": source,
        "status": "traded",
        "trade_date": today,
        "quote_time_bj": "",
    }


def _fetch_hk_current_return(code: str, *, today: str, disabled_sources: set[str]) -> dict[str, Any]:
    errors = []
    try:
        return_pct, trade_date, source = fetch_hk_return_pct_akshare_daily_with_date(
            code,
            end_date=today,
        )
        if str(trade_date) == today:
            return {
                "return_pct": float(return_pct),
                "source": source,
                "status": "traded",
                "trade_date": str(trade_date),
                "quote_time_bj": "",
            }
        errors.append(f"hk_daily_close: trade_date={trade_date}, today={today}")
    except Exception as exc:
        errors.append(f"hk_daily_close: {repr(exc)}")

    if "hk_sina" not in disabled_sources:
        try:
            return_pct, source = fetch_hk_return_pct_sina(code)
            return {
                "return_pct": float(return_pct),
                "source": source,
                "status": "traded",
                "trade_date": today,
                "quote_time_bj": "",
            }
        except Exception as exc:
            message = repr(exc)
            errors.append(f"hk_sina: {message}")
            if any(token in message for token in ("SSLError", "ProxyError", "返回空内容")):
                disabled_sources.add("hk_sina")

    if "hk_em" not in disabled_sources:
        try:
            return_pct, source = fetch_hk_return_pct_akshare_spot_em(code)
            return {
                "return_pct": float(return_pct),
                "source": source,
                "status": "traded",
                "trade_date": today,
                "quote_time_bj": "",
            }
        except Exception as exc:
            message = repr(exc)
            errors.append(f"hk_em: {message}")
            if any(token in message for token in ("SSLError", "ProxyError", "返回空数据")):
                disabled_sources.add("hk_em")

    raise RuntimeError(" | ".join(errors))


def _fetch_kr_current_return(code: str, today: str) -> dict[str, Any]:
    return_pct, trade_date, source = fetch_kr_return_pct_daily_with_date(code, target_date=today)
    if str(trade_date) != today:
        raise RuntimeError(f"韩国行情日期不是今日: trade_date={trade_date}, today={today}")
    return {
        "return_pct": float(return_pct),
        "source": source,
        "status": "traded",
        "trade_date": trade_date,
        "quote_time_bj": "",
    }


def fetch_holding_current_return(
    market: str,
    ticker: str,
    *,
    today: str,
    disabled_sources: set[str],
) -> dict[str, Any]:
    market_norm = str(market or "").strip().upper()
    ticker_norm = str(ticker or "").strip().upper()
    if market_norm == "US":
        return fetch_us_premarket_return_pct(ticker_norm, disabled_sources=disabled_sources)
    if market_norm == "CN":
        return _fetch_cn_current_return(ticker_norm, today=today)
    if market_norm == "HK":
        return _fetch_hk_current_return(ticker_norm, today=today, disabled_sources=disabled_sources)
    if market_norm == "KR":
        return _fetch_kr_current_return(ticker_norm, today)
    raise RuntimeError(f"不支持的持仓市场: market={market_norm or '空'}, ticker={ticker_norm}")


def estimate_premarket_holdings(
    holdings_df: pd.DataFrame,
    *,
    today: str,
    quote_cache: dict[tuple[str, str], dict[str, Any]],
    disabled_sources: set[str],
    residual_benchmark: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    df = holdings_df.copy()
    returns = []
    sources = []
    statuses = []
    errors = []
    trade_dates = []

    for _, row in df.iterrows():
        market = str(row.get("市场", "")).strip().upper()
        ticker = str(row.get("ticker", "")).strip().upper()
        key = (market, ticker)
        try:
            item = quote_cache.get(key)
            if item is None:
                item = fetch_holding_current_return(
                    market,
                    ticker,
                    today=today,
                    disabled_sources=disabled_sources,
                )
                quote_cache[key] = dict(item)
            return_pct = _safe_float(item.get("return_pct"))
            if return_pct is None:
                raise RuntimeError(str(item.get("error") or "行情无有效涨跌幅"))
            returns.append(return_pct)
            sources.append(str(item.get("source", "")))
            statuses.append("traded")
            trade_dates.append(str(item.get("trade_date", "")))
            errors.append("")
        except Exception as exc:
            error_text = str(exc)
            returns.append(None)
            sources.append("failed")
            statuses.append("missing")
            trade_dates.append("")
            errors.append(error_text)
            existing = quote_cache.get(key)
            if not isinstance(existing, dict) or existing.get("return_pct") is not None:
                quote_cache[key] = {
                    "return_pct": None,
                    "source": "failed",
                    "status": "missing",
                    "trade_date": "",
                    "error": error_text,
                }

    df["盘前涨跌幅"] = returns
    df["盘前数据源"] = sources
    df["盘前状态"] = statuses
    df["盘前交易日"] = trade_dates
    df["盘前错误"] = errors
    df["占净值比例"] = pd.to_numeric(df["占净值比例"], errors="coerce")

    valid_mask = df["盘前状态"].eq("traded") & df["盘前涨跌幅"].notna() & df["占净值比例"].gt(0)
    residual_benchmark = residual_benchmark or {}
    calc = estimate_boosted_valid_holding_with_residual(
        zip(df.loc[valid_mask, "占净值比例"], df.loc[valid_mask, "盘前涨跌幅"]),
        residual_return_pct=residual_benchmark.get("return_pct"),
    )
    estimate = calc["estimated_return_pct"]
    raw_valid_weight = float(calc["raw_valid_weight_pct"] or 0.0)
    boosted_weight = float(calc["boosted_weight_pct"] or 0.0)
    actual_boost = float(calc["actual_boost"] or 0.0)
    df["盘前有效估算权重"] = pd.NA
    df["盘前收益贡献"] = pd.NA
    if raw_valid_weight > 0:
        df.loc[valid_mask, "盘前有效估算权重"] = df.loc[valid_mask, "占净值比例"] * actual_boost
        df.loc[valid_mask, "盘前收益贡献"] = (
            df.loc[valid_mask, "盘前有效估算权重"] * df.loc[valid_mask, "盘前涨跌幅"] / 100.0
        )

    raw_weight_sum = float(pd.to_numeric(df["占净值比例"], errors="coerce").fillna(0).sum())
    valid_count = int(valid_mask.sum())
    missing_count = int((~valid_mask).sum())
    residual_weight_pct = float(calc["residual_weight_pct"] or 0.0)
    residual_return_pct = calc["residual_return_pct"]
    residual_failed = bool(residual_weight_pct > 0 and residual_return_pct is None)
    summary = {
        "estimate_return_pct": estimate,
        "known_contribution_pct": calc["known_contribution_pct"],
        "raw_weight_sum_pct": raw_weight_sum,
        "valid_raw_weight_sum_pct": raw_valid_weight,
        "boosted_valid_weight_sum_pct": boosted_weight,
        "actual_boost": actual_boost,
        "residual_benchmark_key": str(residual_benchmark.get("benchmark_key", "")),
        "residual_benchmark_label": str(residual_benchmark.get("label", "")),
        "residual_ticker": str(residual_benchmark.get("ticker", "")),
        "residual_source": str(residual_benchmark.get("source", "")),
        "residual_status": str(residual_benchmark.get("status", "")),
        "residual_error": str(residual_benchmark.get("error", "")),
        "residual_weight_pct": residual_weight_pct,
        "residual_return_pct": residual_return_pct,
        "residual_contribution_pct": calc["residual_contribution_pct"],
        "valid_holding_count": valid_count,
        "missing_holding_count": missing_count,
        "data_status": "failed" if estimate is None else ("partial" if missing_count or residual_failed else "intraday"),
    }
    return df, summary


def _load_purchase_limit_cache() -> dict[str, Any]:
    try:
        with FUND_PURCHASE_LIMIT_CACHE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_cached_fund_names() -> dict[str, str]:
    try:
        with FUND_ESTIMATE_CACHE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}

    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, dict):
        return {}

    names: dict[str, tuple[str, str]] = {}
    for item in records.values():
        if not isinstance(item, dict):
            continue
        code = str(item.get("fund_code", "")).strip().zfill(6)
        name = str(item.get("fund_name", "")).strip()
        run_time = str(item.get("run_time_bj", ""))
        if not code or not name:
            continue
        old = names.get(code)
        if old is None or run_time >= old[0]:
            names[code] = (run_time, name)

    return {code: name for code, (_run_time, name) in names.items()}


def _purchase_limit_text(fund_code: str, cache: dict[str, Any]) -> str:
    code = str(fund_code).strip().zfill(6)
    item = cache.get(code)
    if isinstance(item, dict):
        value = str(item.get("value", "")).strip()
    else:
        value = str(item or "").strip()
    return value or "未知"


def _write_report(
    report_file: str | Path,
    *,
    generated_at: datetime,
    rows: list[dict[str, Any]],
    quote_cache: dict[tuple[str, str], dict[str, Any]],
    affected_funds: dict[tuple[str, str], list[str]],
) -> None:
    path = Path(report_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    valid_items = [item for item in quote_cache.values() if item.get("return_pct") is not None]
    missing_items = [item for item in quote_cache.values() if item.get("return_pct") is None]
    lines = [
        f"generated_at_bj: {generated_at.isoformat(timespec='seconds')}",
        f"fund_count: {len(rows)}",
        f"unique_security_count: {len(quote_cache)}",
        f"valid_unique_security_count: {len(valid_items)}",
        f"missing_unique_security_count: {len(missing_items)}",
        "",
        "基金汇总",
        (
            "fund_code\tfund_name\testimate_return_pct\tknown_contribution_pct\t"
            "valid_raw_weight_pct\tboosted_valid_weight_pct\tresidual_benchmark_key\t"
            "residual_benchmark_label\tresidual_ticker\tresidual_weight_pct\t"
            "residual_return_pct\tresidual_contribution_pct\tvalid_holding_count\t"
            "missing_holding_count\tdata_status"
        ),
    ]
    for row in rows:
        lines.append(
            "\t".join(
                [
                    str(row.get("fund_code", "")),
                    str(row.get("fund_name", "")),
                    "" if row.get("estimate_return_pct") is None else f"{float(row['estimate_return_pct']):+.4f}",
                    "" if row.get("known_contribution_pct") is None else f"{float(row['known_contribution_pct']):+.4f}",
                    f"{float(row.get('valid_raw_weight_sum_pct') or 0):.2f}",
                    f"{float(row.get('boosted_valid_weight_sum_pct') or 0):.2f}",
                    str(row.get("residual_benchmark_key", "")),
                    str(row.get("residual_benchmark_label", "")),
                    str(row.get("residual_ticker", "")),
                    f"{float(row.get('residual_weight_pct') or 0):.2f}",
                    "" if row.get("residual_return_pct") is None else f"{float(row['residual_return_pct']):+.4f}",
                    f"{float(row.get('residual_contribution_pct') or 0):+.4f}",
                    str(row.get("valid_holding_count", 0)),
                    str(row.get("missing_holding_count", 0)),
                    str(row.get("data_status", "")),
                ]
            )
        )

    lines.extend(["", "失败/未取到证券", "market\tticker\taffected_funds\terror"])
    for key, item in sorted(quote_cache.items()):
        if item.get("return_pct") is not None:
            continue
        market, ticker = key
        funds = ",".join(sorted(set(affected_funds.get(key, []))))
        lines.append(
            "\t".join(
                [
                    market,
                    ticker,
                    funds,
                    str(item.get("error", "")),
                ]
            )
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_premarket_table(
    *,
    fund_codes: Iterable[str] = HAIWAI_FUND_CODES,
    top_n: int = 10,
    current_time: datetime | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], list[str]]]:
    generated_at = coerce_bj_datetime(current_time)
    today = generated_at.date().isoformat()
    quote_cache: dict[tuple[str, str], dict[str, Any]] = {}
    disabled_sources: set[str] = set()
    purchase_limit_cache = _load_purchase_limit_cache()
    cached_fund_names = _load_cached_fund_names()
    rows = []
    affected_funds: dict[tuple[str, str], list[str]] = defaultdict(list)

    for index, fund_code_raw in enumerate(fund_codes, start=1):
        fund_code = str(fund_code_raw).strip().zfill(6)
        fund_name = cached_fund_names.get(fund_code) or get_fund_name(fund_code)
        try:
            holdings_df = get_latest_stock_holdings_df(
                fund_code=fund_code,
                top_n=top_n,
                cache_enabled=True,
            )
            residual_key = get_premarket_residual_benchmark_key(fund_code)
            residual_benchmark = fetch_premarket_benchmark_quote(
                residual_key,
                today=today,
                quote_cache=quote_cache,
                disabled_sources=disabled_sources,
            )
            residual_market = str(residual_benchmark.get("market", "")).strip().upper()
            residual_ticker = str(residual_benchmark.get("ticker", "")).strip().upper()
            if residual_market and residual_ticker:
                affected_funds[(residual_market, residual_ticker)].append(fund_code)
            detail_df, summary = estimate_premarket_holdings(
                holdings_df,
                today=today,
                quote_cache=quote_cache,
                disabled_sources=disabled_sources,
                residual_benchmark=residual_benchmark,
            )
            for _, item in detail_df.iterrows():
                market = str(item.get("市场", "")).strip().upper()
                ticker = str(item.get("ticker", "")).strip().upper()
                if market and ticker:
                    affected_funds[(market, ticker)].append(fund_code)
            estimate = summary["estimate_return_pct"]
            rows.append(
                {
                    "_input_order": index,
                    "fund_code": fund_code,
                    "fund_name": fund_name,
                    "estimate_return_pct": estimate,
                    **summary,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "_input_order": index,
                    "fund_code": fund_code,
                    "fund_name": fund_name,
                    "estimate_return_pct": None,
                    "known_contribution_pct": None,
                    "valid_raw_weight_sum_pct": 0.0,
                    "boosted_valid_weight_sum_pct": 0.0,
                    "residual_benchmark_key": "",
                    "residual_benchmark_label": "",
                    "residual_ticker": "",
                    "residual_weight_pct": 0.0,
                    "residual_return_pct": None,
                    "residual_contribution_pct": 0.0,
                    "valid_holding_count": 0,
                    "missing_holding_count": top_n,
                    "data_status": "failed",
                    "error": str(exc),
                }
            )

    rows.sort(
        key=lambda row: (
            row.get("estimate_return_pct") is not None,
            float(row.get("estimate_return_pct") or -9999),
        ),
        reverse=True,
    )
    display_rows = []
    for order, row in enumerate(rows, start=1):
        display_rows.append(
            {
                "序号": order,
                "基金名称": mask_fund_name(row.get("fund_name", ""), enabled=True),
                "今日预估涨跌幅": row.get("estimate_return_pct"),
                PURCHASE_LIMIT_COLUMN: _purchase_limit_text(row.get("fund_code", ""), purchase_limit_cache),
            }
        )

    display_df = pd.DataFrame(
        display_rows,
        columns=["序号", "基金名称", "今日预估涨跌幅", PURCHASE_LIMIT_COLUMN],
    )
    return display_df, rows, quote_cache, affected_funds


def save_premarket_image(
    display_df: pd.DataFrame,
    *,
    generated_at: datetime,
    output_file: str | Path = SAFE_HAIWAI_PREMARKET_IMAGE,
) -> None:
    output_path = Path(output_file)
    title_date = generated_at.date().isoformat()
    generated_text = generated_at.strftime("%Y-%m-%d %H:%M:%S")
    title = f"海外基金盘前模型观察 观察日：{title_date} 生成：{generated_text}"
    title_segments = [
        {
            "text": "海外基金盘前模型观察  ",
            "color": SAFE_TITLE_STYLE["color"],
            "fontweight": SAFE_TITLE_STYLE["fontweight"],
            "fontsize": SAFE_TITLE_STYLE["fontsize"],
        },
        {
            "text": f"观察日：{title_date}",
            "color": SAFE_TITLE_STYLE["highlight_color"],
            "fontweight": SAFE_TITLE_STYLE["fontweight"],
            "fontsize": SAFE_TITLE_STYLE["fontsize"],
        },
        {
            "text": f"  生成：{generated_text}",
            "color": SAFE_TITLE_STYLE["color"],
            "fontweight": SAFE_TITLE_STYLE["fontweight"],
            "fontsize": SAFE_TITLE_STYLE["fontsize"],
        },
    ]
    image_kwargs = safe_daily_table_kwargs()
    column_widths = dict(image_kwargs.get("column_width_by_name") or {})
    column_widths[DISPLAY_RETURN_COLUMN] = column_widths.get("模型估算观察", 0.15)
    image_kwargs["column_width_by_name"] = column_widths
    image_kwargs.update(
        {
            "footnote_text": (
                "盘前观察使用当前可取到的持仓股涨跌幅，并按配置的盘前基准补仓；"
                "美股不回退旧日线，最终以基金公司更新为准。鱼师AHNS出品"
            ),
            "watermark_text": "",
            "watermark_alpha": 0,
            "watermark_fontsize": 32,
        }
    )
    save_fund_estimate_table_image(
        result_df=display_df,
        output_file=relative_path_str(output_path),
        title=title,
        title_segments=title_segments,
        display_column_names={"今日预估涨跌幅": DISPLAY_RETURN_COLUMN},
        benchmark_footer_items=[],
        pct_digits=2,
        **image_kwargs,
    )
    apply_safe_public_watermarks(output_path)


def run_premarket_observation(
    *,
    force: bool = False,
    current_time: datetime | str | None = None,
    fund_codes: Iterable[str] = HAIWAI_FUND_CODES,
    output_file: str | Path = SAFE_HAIWAI_PREMARKET_IMAGE,
    report_file: str | Path = PREMARKET_FAILED_HOLDINGS_REPORT,
    top_n: int = 10,
) -> PremarketRunResult:
    ensure_runtime_dirs()
    generated_at = coerce_bj_datetime(current_time)
    if not force and not in_premarket_window(generated_at):
        window_text = f"{PREMARKET_START_BJ.strftime('%H:%M')}-{PREMARKET_END_BJ.strftime('%H:%M')}"
        reason = (
            f"当前北京时间不在 {window_text} 盘前观察窗口，未生成盘前图；"
            "如需测试请使用 --force。"
        )
        print(reason, flush=True)
        return PremarketRunResult(
            generated=False,
            reason=reason,
            output_file=Path(output_file),
            report_file=Path(report_file),
        )

    display_df, rows, quote_cache, affected_funds = build_premarket_table(
        fund_codes=fund_codes,
        top_n=top_n,
        current_time=generated_at,
    )
    save_premarket_image(display_df, generated_at=generated_at, output_file=output_file)
    _write_report(
        report_file,
        generated_at=generated_at,
        rows=rows,
        quote_cache=quote_cache,
        affected_funds=affected_funds,
    )

    valid_count = len([item for item in quote_cache.values() if item.get("return_pct") is not None])
    missing_count = len(quote_cache) - valid_count
    reason = f"盘前观察图生成完成: {relative_path_str(output_file)}"
    print(reason, flush=True)
    return PremarketRunResult(
        generated=True,
        reason=reason,
        output_file=Path(output_file),
        report_file=Path(report_file),
        fund_count=len(rows),
        valid_security_count=valid_count,
        missing_security_count=missing_count,
    )


__all__ = [
    "DISPLAY_RETURN_COLUMN",
    "PREMARKET_END_BJ",
    "PREMARKET_START_BJ",
    "PremarketRunResult",
    "build_premarket_table",
    "coerce_bj_datetime",
    "estimate_boosted_valid_holding_return",
    "estimate_boosted_valid_holding_with_residual",
    "estimate_premarket_holdings",
    "fetch_premarket_benchmark_quote",
    "fetch_us_premarket_return_pct",
    "get_premarket_residual_benchmark_key",
    "in_premarket_window",
    "normalize_premarket_benchmark_key",
    "run_premarket_observation",
    "save_premarket_image",
]
