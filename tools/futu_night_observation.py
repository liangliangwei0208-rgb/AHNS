"""
Futu based overnight overseas fund observation.

This module is deliberately separate from the legacy night branch in
``premarket_estimator``.  It reuses shared table, masking and holding helpers,
but US overnight quotes come only from Futu OpenAPI.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from tools.configs.futu_night_configs import (
    FUTU_NIGHT_BENCHMARK_SPECS,
    FUTU_NIGHT_DEFAULT_RESIDUAL_BENCHMARK_KEY,
    FUTU_NIGHT_END_HOUR_BJ,
    FUTU_NIGHT_END_MINUTE_BJ,
    FUTU_NIGHT_FOOTER_BENCHMARK_KEYS,
    FUTU_NIGHT_FOOTER_LABELS,
    FUTU_NIGHT_FUND_RESIDUAL_BENCHMARK_MAP,
    FUTU_NIGHT_OUTPUT_FILE,
    FUTU_NIGHT_REPORT_FILE,
    FUTU_NIGHT_RETURN_CACHE,
    FUTU_NIGHT_RETURN_CACHE_MAX_ITEMS,
    FUTU_NIGHT_RETURN_CACHE_RETENTION_DAYS,
    FUTU_NIGHT_RETURN_CACHE_TTL_MINUTES,
    FUTU_NIGHT_START_HOUR_BJ,
    FUTU_NIGHT_START_MINUTE_BJ,
)
from tools.configs.premarket_configs import PREMARKET_FUND_ESTIMATION_METHOD_MAP
from tools.console_display import fund_progress, print_dataframe_table, print_stage
from tools.fund_universe import HAIWAI_FUND_CODES
from tools.futu_night_quotes import (
    FutuNightQuoteProvider,
    FutuNightReturnCache,
    futu_night_session_status,
    futu_night_valuation_date,
)
from tools.get_top10_holdings import (
    fetch_latest_complete_vix_close,
    get_fund_name,
    get_latest_stock_holdings_df,
)
from tools.paths import ensure_runtime_dirs, relative_path_str
from tools.premarket_estimator import (
    BJ_TZ,
    ObservationSessionConfig,
    PremarketRunResult,
    _fetch_anchor_daily_return,
    _fetch_cn_realtime_return_with_date,
    _fetch_hk_realtime_return_with_date,
    _fetch_kr_realtime_return_with_date,
    _fetch_realtime_vix_level,
    _load_cached_fund_names,
    _load_purchase_limit_cache,
    _premarket_benchmark_spec,
    _purchase_limit_text,
    _safe_float,
    coerce_bj_datetime,
    estimate_top10_available_normalized_return,
    estimate_boosted_valid_holding_with_residual,
    get_observation_estimation_method,
    get_observation_residual_benchmark_key,
    is_top10_available_normalized_method,
    save_premarket_image,
)
from tools.safe_display import mask_fund_name


FUTU_NIGHT_START_BJ = time(FUTU_NIGHT_START_HOUR_BJ, FUTU_NIGHT_START_MINUTE_BJ)
FUTU_NIGHT_END_BJ = time(FUTU_NIGHT_END_HOUR_BJ, FUTU_NIGHT_END_MINUTE_BJ)

FUTU_NIGHT_SESSION = ObservationSessionConfig(
    mode="futu_night",
    title_word="夜盘",
    window_word="夜盘",
    start_time_bj=FUTU_NIGHT_START_BJ,
    end_time_bj=FUTU_NIGHT_END_BJ,
    output_file=FUTU_NIGHT_OUTPUT_FILE,
    report_file=FUTU_NIGHT_REPORT_FILE,
    quote_cache_file=FUTU_NIGHT_RETURN_CACHE,
    quote_cache_ttl_minutes=FUTU_NIGHT_RETURN_CACHE_TTL_MINUTES,
    quote_cache_retention_days=FUTU_NIGHT_RETURN_CACHE_RETENTION_DAYS,
    quote_cache_max_items=FUTU_NIGHT_RETURN_CACHE_MAX_ITEMS,
    benchmark_specs=FUTU_NIGHT_BENCHMARK_SPECS,
    default_residual_benchmark_key=FUTU_NIGHT_DEFAULT_RESIDUAL_BENCHMARK_KEY,
    fund_residual_benchmark_map=FUTU_NIGHT_FUND_RESIDUAL_BENCHMARK_MAP,
    fund_estimation_method_map=PREMARKET_FUND_ESTIMATION_METHOD_MAP,
    footer_benchmark_keys=FUTU_NIGHT_FOOTER_BENCHMARK_KEYS,
    footer_labels=FUTU_NIGHT_FOOTER_LABELS,
    display_return_column="夜盘模型观察",
    us_quote_mode="night",
    complete_data_status="night",
)

PURCHASE_LIMIT_COLUMN = "模型观察基金信息"


def _progress_status(progress, message: str) -> None:
    if progress is None:
        return
    try:
        progress.set_status(str(message))
    except Exception:
        return


def _format_progress_return_pct(value: Any) -> str:
    try:
        if value is None or pd.isna(value):
            return "无涨跌幅"
        return f"{float(value):+.4f}%"
    except Exception:
        return "无涨跌幅"


def _print_night_estimate_table(display_df: pd.DataFrame, *, generated_at: datetime) -> None:
    if display_df is None:
        return
    show_df = display_df.copy()
    return_col = "今日预估涨跌幅"
    if return_col in show_df.columns:
        show_df[return_col] = show_df[return_col].map(_format_progress_return_pct)
        show_df = show_df.rename(columns={return_col: FUTU_NIGHT_SESSION.display_return_column})
    title_date = futu_night_valuation_date(generated_at)
    print_dataframe_table(
        show_df,
        title=f"夜盘基金模型观察汇总 估值日: {title_date}",
    )


def in_futu_night_window(check_time: datetime | str | None = None) -> bool:
    dt = coerce_bj_datetime(check_time)
    current = dt.time().replace(second=0, microsecond=0)
    return FUTU_NIGHT_START_BJ <= current <= FUTU_NIGHT_END_BJ


def _quote_item_has_value(item: dict[str, Any]) -> bool:
    if _safe_float(item.get("return_pct")) is not None:
        return True
    return str(item.get("value_type", "")).strip().lower() == "level" and _safe_float(item.get("value")) is not None


def _quote_key(market: Any, ticker: Any) -> tuple[str, str]:
    return str(market or "").strip().upper(), str(ticker or "").strip().upper()


def _zero_return(market: str, ticker: str, *, target_date: str, error: str) -> dict[str, Any]:
    return {
        "return_pct": 0.0,
        "source": "futu_night_zero",
        "status": "zeroed",
        "trade_date": str(target_date),
        "quote_time_bj": "",
        "market": str(market or "").strip().upper(),
        "ticker": str(ticker or "").strip().upper(),
        "error": str(error or "富途夜盘无有效实时数据，单项贡献置零"),
    }


def _config_missing_benchmark(benchmark_key: Any, *, target_date: str) -> dict[str, Any]:
    key_norm = str(benchmark_key or "").strip().lower()
    return {
        "benchmark_key": key_norm,
        "label": key_norm or "未知基准",
        "ticker": "",
        "market": "",
        "kind": "",
        "return_pct": None,
        "source": "config_missing",
        "status": "missing",
        "trade_date": target_date,
        "error": f"富途夜盘基准配置不存在: {benchmark_key}",
        "value_type": "return_pct",
    }


def _fetch_vix_footer_item(
    *,
    target_date: str,
    generated_at: datetime,
    return_cache: FutuNightReturnCache,
) -> dict[str, Any]:
    cached = return_cache.get("VIX_LEVEL", "VIX", target_date, generated_at)
    if cached is not None:
        return {
            **cached,
            "benchmark_key": "vix",
            "label": "VIX恐慌指数",
            "ticker": "VIX",
            "market": "VIX_LEVEL",
            "kind": "vix_level",
        }
    try:
        item = _fetch_realtime_vix_level(target_date)
    except Exception as realtime_exc:
        try:
            vix = fetch_latest_complete_vix_close()
            item = {
                "benchmark_key": "vix",
                "label": "VIX恐慌指数",
                "ticker": "VIX",
                "market": "VIX_LEVEL",
                "kind": "vix_level",
                "return_pct": None,
                "value": _safe_float(vix.get("close")),
                "display_value": f"{float(vix['close']):.2f}",
                "trade_date": str(vix.get("date") or target_date),
                "source": f"vix_latest_close_fallback:{vix.get('source', '')}",
                "status": "traded",
                "value_type": "level",
                "error": str(realtime_exc),
            }
        except Exception as fallback_exc:
            item = {
                "benchmark_key": "vix",
                "label": "VIX恐慌指数",
                "ticker": "VIX",
                "market": "VIX_LEVEL",
                "kind": "vix_level",
                "return_pct": None,
                "value": None,
                "display_value": "",
                "trade_date": target_date,
                "source": "failed",
                "status": "missing",
                "value_type": "level",
                "error": f"{realtime_exc!r} | {fallback_exc!r}",
            }
    return_cache.remember("VIX_LEVEL", "VIX", target_date, item, fetched_at_bj=generated_at)
    return item


def _fetch_benchmark_quote(
    benchmark_key: Any,
    *,
    target_date: str,
    provider: FutuNightQuoteProvider,
    quote_cache: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    spec = _premarket_benchmark_spec(benchmark_key, session=FUTU_NIGHT_SESSION)
    if spec is None:
        return _config_missing_benchmark(benchmark_key, target_date=target_date)

    if spec["kind"] == "vix_level":
        item = _fetch_vix_footer_item(
            target_date=target_date,
            generated_at=provider.as_of_bj,
            return_cache=provider.return_cache,
        )
    elif spec["market"] == "US":
        try:
            quote = provider.get_us_return(spec["ticker"], target_us_date=target_date)
            item = {
                "benchmark_key": spec["key"],
                "label": spec["label"],
                "ticker": spec["ticker"],
                "market": spec["market"],
                "kind": spec["kind"],
                "return_pct": _safe_float(quote.get("return_pct")),
                "trade_date": str(quote.get("trade_date") or target_date),
                "source": str(quote.get("source", "")),
                "status": str(quote.get("status", "traded")),
                "value_type": "return_pct",
                "quote_time_bj": str(quote.get("quote_time_bj", "")),
                "cache_hit": str(quote.get("cache_hit", "")),
            }
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
                "trade_date": target_date,
                "source": "failed",
                "status": "missing",
                "error": str(exc),
                "value_type": "return_pct",
            }
    else:
        item = {
            "benchmark_key": spec["key"],
            "label": spec["label"],
            "ticker": spec["ticker"],
            "market": spec["market"],
            "kind": spec["kind"],
            "return_pct": None,
            "value": None,
            "display_value": "",
            "trade_date": target_date,
            "source": "failed",
            "status": "missing",
            "error": f"富途夜盘基准暂不支持 market={spec['market']}",
            "value_type": "return_pct",
        }

    key = _quote_key(item.get("market"), item.get("ticker"))
    if key[0] and key[1]:
        quote_cache[key] = dict(item)
    return item


def _fetch_non_us_return(
    market: str,
    ticker: str,
    *,
    target_date: str,
    provider: FutuNightQuoteProvider,
    disabled_sources: set[str],
) -> dict[str, Any]:
    cached = provider.return_cache.get(market, ticker, target_date, provider.as_of_bj)
    if cached is not None:
        return cached

    errors: list[str] = []
    try:
        if market == "CN":
            item = _fetch_cn_realtime_return_with_date(ticker, target_date=target_date)
        elif market == "HK":
            item = _fetch_hk_realtime_return_with_date(
                ticker,
                target_date=target_date,
                disabled_sources=disabled_sources,
            )
        elif market == "KR":
            item = _fetch_kr_realtime_return_with_date(ticker, target_date=target_date)
        else:
            raise RuntimeError(f"不支持的持仓市场: market={market}, ticker={ticker}")
        provider.return_cache.remember(market, ticker, target_date, item, fetched_at_bj=provider.as_of_bj)
        return item
    except Exception as exc:
        errors.append(f"realtime: {repr(exc)}")

    try:
        item = _fetch_anchor_daily_return(
            market,
            ticker,
            target_date=target_date,
            as_of_bj=provider.as_of_bj,
        )
        if str(item.get("trade_date") or "").strip() != str(target_date):
            raise RuntimeError(f"日涨跌幅日期不匹配: trade_date={item.get('trade_date')}, target={target_date}")
        provider.return_cache.remember(market, ticker, target_date, item, fetched_at_bj=provider.as_of_bj)
        return item
    except Exception as exc:
        errors.append(f"daily: {repr(exc)}")

    raise RuntimeError(" | ".join(errors))


def _fetch_holding_return(
    market: str,
    ticker: str,
    *,
    target_date: str,
    provider: FutuNightQuoteProvider,
    disabled_sources: set[str],
) -> dict[str, Any]:
    market_norm = str(market or "").strip().upper()
    ticker_norm = str(ticker or "").strip().upper()
    if market_norm == "US":
        return provider.get_us_return(ticker_norm, target_us_date=target_date)
    if market_norm in {"CN", "HK", "KR"}:
        return _fetch_non_us_return(
            market_norm,
            ticker_norm,
            target_date=target_date,
            provider=provider,
            disabled_sources=disabled_sources,
        )
    raise RuntimeError(f"不支持的持仓市场: market={market_norm or '空'}, ticker={ticker_norm}")


def _estimate_fund_holdings(
    holdings_df: pd.DataFrame,
    *,
    target_date: str,
    residual_benchmark: dict[str, Any],
    provider: FutuNightQuoteProvider,
    quote_cache: dict[tuple[str, str], dict[str, Any]],
    affected_funds: dict[tuple[str, str], list[str]],
    fund_code: str,
    disabled_sources: set[str],
    progress=None,
    estimation_method: str = "",
) -> dict[str, Any]:
    return_pairs = []
    valid_count = 0
    zeroed_count = 0
    missing_count = 0

    total_holdings = len(holdings_df)

    for holding_index, (_, row) in enumerate(holdings_df.iterrows(), start=1):
        market = str(row.get("市场", "")).strip().upper()
        ticker = str(row.get("ticker", "")).strip().upper()
        name = str(row.get("股票名称", "") or row.get("name", "") or "").strip()
        weight = _safe_float(row.get("占净值比例"))
        key = _quote_key(market, ticker)
        item_label = f"{fund_code} 夜盘持仓 {holding_index}/{total_holdings}: {market}:{ticker} {name}".strip()
        _progress_status(progress, f"{item_label} 获取行情")
        if market and ticker:
            affected_funds[key].append(fund_code)
        try:
            item = _fetch_holding_return(
                market,
                ticker,
                target_date=target_date,
                provider=provider,
                disabled_sources=disabled_sources,
            )
            quote_cache[key] = dict(item)
            return_pct = _safe_float(item.get("return_pct"))
            status = str(item.get("status") or "traded").strip().lower()
            if return_pct is None or status not in {"traded", "closed"}:
                raise RuntimeError(str(item.get("error") or "行情无有效涨跌幅"))
            if weight is not None and weight > 0:
                return_pairs.append((weight, return_pct))
                valid_count += 1
            _progress_status(
                progress,
                (
                    f"{item_label} -> {status} "
                    f"{_format_progress_return_pct(return_pct)} "
                    f"{item.get('source', '')} {item.get('trade_date', '')}"
                ).strip(),
            )
        except Exception as exc:
            item = _zero_return(market, ticker, target_date=target_date, error=str(exc))
            quote_cache[key] = dict(item)
            zeroed_count += 1
            _progress_status(progress, f"{item_label} 获取失败，夜盘置零: {exc}")

    top10_available_normalized = is_top10_available_normalized_method(estimation_method)
    if top10_available_normalized:
        residual_benchmark = {}
        calc = estimate_top10_available_normalized_return(return_pairs)
    else:
        calc = estimate_boosted_valid_holding_with_residual(
            return_pairs,
            residual_return_pct=residual_benchmark.get("return_pct"),
        )
    estimate = calc["estimated_return_pct"]
    residual_weight_pct = float(calc["residual_weight_pct"] or 0.0)
    residual_return_pct = calc["residual_return_pct"]
    residual_failed = bool(residual_weight_pct > 0 and residual_return_pct is None)

    return {
        "method": "top10_available_normalized" if top10_available_normalized else "boosted_valid_holding_with_residual",
        "estimate_return_pct": estimate,
        "known_contribution_pct": calc["known_contribution_pct"],
        "valid_raw_weight_sum_pct": float(calc["raw_valid_weight_pct"] or 0.0),
        "boosted_valid_weight_sum_pct": float(calc["boosted_weight_pct"] or 0.0),
        "actual_boost": float(calc["actual_boost"] or 0.0),
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
        "zeroed_holding_count": zeroed_count,
        "data_status": "failed" if estimate is None else ("partial" if missing_count or residual_failed else "night"),
    }


def _collect_us_tickers(
    fund_payloads: list[dict[str, Any]],
) -> list[str]:
    tickers: set[str] = set()
    for payload in fund_payloads:
        holdings_df = payload.get("holdings_df")
        if isinstance(holdings_df, pd.DataFrame):
            for _, row in holdings_df.iterrows():
                market = str(row.get("市场", "")).strip().upper()
                ticker = str(row.get("ticker", "")).strip().upper()
                if market == "US" and ticker:
                    tickers.add(ticker)
        spec = payload.get("residual_spec")
        if isinstance(spec, dict) and str(spec.get("market", "")).strip().upper() == "US":
            ticker = str(spec.get("ticker", "")).strip().upper()
            if ticker:
                tickers.add(ticker)

    for benchmark_key in FUTU_NIGHT_SESSION.footer_benchmark_keys:
        spec = _premarket_benchmark_spec(benchmark_key, session=FUTU_NIGHT_SESSION)
        if isinstance(spec, dict) and str(spec.get("market", "")).strip().upper() == "US":
            ticker = str(spec.get("ticker", "")).strip().upper()
            if ticker:
                tickers.add(ticker)
    return sorted(tickers)


def build_futu_night_table(
    *,
    fund_codes: Iterable[str] = HAIWAI_FUND_CODES,
    top_n: int = 10,
    current_time: datetime | str | None = None,
    provider: FutuNightQuoteProvider,
) -> tuple[
    pd.DataFrame,
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[tuple[str, str], dict[str, Any]],
    dict[tuple[str, str], list[str]],
]:
    fund_codes = list(fund_codes)
    generated_at = coerce_bj_datetime(current_time)
    target_date = futu_night_valuation_date(generated_at)
    purchase_limit_cache = _load_purchase_limit_cache()
    cached_fund_names = _load_cached_fund_names()
    quote_cache: dict[tuple[str, str], dict[str, Any]] = {}
    affected_funds: dict[tuple[str, str], list[str]] = defaultdict(list)
    disabled_sources: set[str] = set()
    fund_payloads: list[dict[str, Any]] = []

    with fund_progress("夜盘持仓加载", len(fund_codes)) as progress:
        for index, fund_code_raw in enumerate(fund_codes, start=1):
            fund_code = str(fund_code_raw).strip().zfill(6)
            fund_name = cached_fund_names.get(fund_code) or get_fund_name(fund_code)
            progress.start_item(f"{fund_code} {fund_name}")
            try:
                holding_fetch_top_n = 10 if int(top_n or 10) <= 10 else int(top_n)
                progress.set_status(f"{fund_code} {fund_name} 加载前{holding_fetch_top_n}大持仓")
                holdings_df = get_latest_stock_holdings_df(
                    fund_code=fund_code,
                    top_n=holding_fetch_top_n,
                    cache_enabled=True,
                )
                if int(top_n or 0) > 0 and len(holdings_df) > int(top_n):
                    holdings_df = holdings_df.head(int(top_n)).copy()
                progress.set_status(f"{fund_code} {fund_name} 持仓加载完成: {len(holdings_df)} 条")
                estimation_method = get_observation_estimation_method(fund_code, session=FUTU_NIGHT_SESSION)
                if is_top10_available_normalized_method(estimation_method):
                    residual_key = ""
                    residual_spec = {}
                else:
                    residual_key = get_observation_residual_benchmark_key(fund_code, session=FUTU_NIGHT_SESSION)
                    residual_spec = _premarket_benchmark_spec(residual_key, session=FUTU_NIGHT_SESSION)
                fund_payloads.append(
                    {
                        "_input_order": index,
                        "fund_code": fund_code,
                        "fund_name": fund_name,
                        "holdings_df": holdings_df,
                        "estimation_method": estimation_method,
                        "residual_key": residual_key,
                        "residual_spec": residual_spec,
                    }
                )
                progress.advance(success=True)
            except Exception as exc:
                progress.set_status(f"{fund_code} {fund_name} 持仓加载失败: {exc}")
                fund_payloads.append(
                    {
                        "_input_order": index,
                        "fund_code": fund_code,
                        "fund_name": fund_name,
                        "load_error": str(exc),
                    }
                )
                progress.advance(success=False, status=f"{fund_code} 持仓加载失败")

    prefetch_tickers = _collect_us_tickers(fund_payloads)
    print_stage(f"夜盘批量预取美股报价: {len(prefetch_tickers)} 个标的")
    with fund_progress("夜盘报价预取", len(prefetch_tickers)) as progress:
        provider.prefetch_us_returns(prefetch_tickers, target_us_date=target_date, progress=progress)

    rows: list[dict[str, Any]] = []
    with fund_progress("夜盘基金估算", len(fund_payloads)) as progress:
        for payload in fund_payloads:
            fund_code = str(payload.get("fund_code", "")).zfill(6)
            fund_name = str(payload.get("fund_name", ""))
            progress.start_item(f"{fund_code} {fund_name}")
            if payload.get("load_error"):
                rows.append(
                    {
                        "_input_order": payload.get("_input_order", 0),
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
                        "error": str(payload.get("load_error", "")),
                    }
                )
                progress.advance(success=False, status=f"{fund_code} 持仓加载失败")
                continue

            try:
                estimation_method = str(payload.get("estimation_method", "") or "")
                if is_top10_available_normalized_method(estimation_method):
                    residual_benchmark = {}
                    progress.set_status(f"{fund_code} 使用前十大可用持仓归一化估算，跳过夜盘补偿基准")
                else:
                    progress.set_status(f"{fund_code} 获取夜盘补偿基准: {payload.get('residual_key')}")
                    residual_benchmark = _fetch_benchmark_quote(
                        payload.get("residual_key"),
                        target_date=target_date,
                        provider=provider,
                        quote_cache=quote_cache,
                    )
                    progress.set_status(
                        (
                            f"{fund_code} 补偿基准 -> {residual_benchmark.get('label', '')} "
                            f"{_format_progress_return_pct(residual_benchmark.get('return_pct'))} "
                            f"{residual_benchmark.get('source', '')} "
                            f"{residual_benchmark.get('trade_date', '')}"
                        ).strip()
                    )
                    residual_key = _quote_key(residual_benchmark.get("market"), residual_benchmark.get("ticker"))
                    if residual_key[0] and residual_key[1]:
                        affected_funds[residual_key].append(fund_code)

                summary = _estimate_fund_holdings(
                    payload["holdings_df"],
                    target_date=target_date,
                    residual_benchmark=residual_benchmark,
                    provider=provider,
                    quote_cache=quote_cache,
                    affected_funds=affected_funds,
                    fund_code=fund_code,
                    disabled_sources=disabled_sources,
                    progress=progress,
                    estimation_method=estimation_method,
                )
                rows.append(
                    {
                        "_input_order": payload.get("_input_order", 0),
                        "fund_code": fund_code,
                        "fund_name": fund_name,
                        "estimate_return_pct": summary["estimate_return_pct"],
                        **summary,
                    }
                )
                progress.set_status(
                    (
                        f"{fund_code} 夜盘估算完成: "
                        f"{_format_progress_return_pct(summary.get('estimate_return_pct'))} "
                        f"status={summary.get('data_status', '')}"
                    ).strip()
                )
                progress.advance(success=True)
            except Exception as exc:
                rows.append(
                    {
                        "_input_order": payload.get("_input_order", 0),
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
                progress.set_status(f"{fund_code} 夜盘估算失败: {exc}")
                progress.advance(success=False, status=f"{fund_code} 估算失败")

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

    benchmark_footer_items: list[dict[str, Any]] = []
    for order, benchmark_key in enumerate(FUTU_NIGHT_SESSION.footer_benchmark_keys, start=1):
        item = _fetch_benchmark_quote(
            benchmark_key,
            target_date=target_date,
            provider=provider,
            quote_cache=quote_cache,
        )
        out = dict(item)
        out["order"] = order
        out["label"] = FUTU_NIGHT_SESSION.footer_labels.get(benchmark_key, str(out.get("label", "") or benchmark_key))
        if benchmark_key == "vix":
            out["value_type"] = "level"
            out["return_pct"] = None
        benchmark_footer_items.append(out)

    return display_df, rows, benchmark_footer_items, quote_cache, affected_funds


def _write_futu_night_report(
    report_file: str | Path,
    *,
    generated_at: datetime,
    rows: list[dict[str, Any]],
    quote_cache: dict[tuple[str, str], dict[str, Any]],
    affected_funds: dict[tuple[str, str], list[str]],
    provider: FutuNightQuoteProvider,
) -> None:
    path = Path(report_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    zeroed_items = [
        item for item in quote_cache.values()
        if str(item.get("status", "")).strip().lower() == "zeroed"
    ]
    valid_items = [
        item for item in quote_cache.values()
        if _quote_item_has_value(item) and str(item.get("status", "")).strip().lower() != "zeroed"
    ]
    missing_items = [
        item for item in quote_cache.values()
        if not _quote_item_has_value(item) and str(item.get("status", "")).strip().lower() != "zeroed"
    ]
    file_cache_hits = [item for item in quote_cache.values() if str(item.get("cache_hit", "")).lower() == "file"]
    target_date = futu_night_valuation_date(generated_at)
    stats = provider.stats
    lines = [
        f"generated_at_bj: {generated_at.isoformat(timespec='seconds')}",
        f"valuation_date: {target_date}",
        f"fund_count: {len(rows)}",
        f"unique_security_count: {len(quote_cache)}",
        f"valid_unique_security_count: {len(valid_items)}",
        f"missing_unique_security_count: {len(missing_items)}",
        f"zeroed_unique_security_count: {len(zeroed_items)}",
        f"file_cache_hit_unique_count: {len(file_cache_hits)}",
        f"night_valuation_date: {target_date}",
        f"futu_us_requested_count: {stats.us_requested_count}",
        f"futu_us_cache_hit_count: {stats.us_cache_hit_count}",
        f"futu_us_subscribed_count: {stats.us_subscribed_count}",
        f"futu_us_unsubscribed_count: {stats.us_unsubscribed_count}",
        f"futu_us_subscribe_batch_count: {stats.us_subscribe_batch_count}",
        f"futu_us_fetch_error_count: {stats.us_fetch_error_count}",
        "",
        "基金汇总",
        (
            "fund_code\tfund_name\testimate_return_pct\tknown_contribution_pct\t"
            "valid_raw_weight_pct\tboosted_valid_weight_pct\tresidual_benchmark_key\t"
            "residual_benchmark_label\tresidual_ticker\tresidual_weight_pct\t"
            "residual_return_pct\tresidual_contribution_pct\tvalid_holding_count\t"
            "missing_holding_count\tzeroed_holding_count\tdata_status\terror"
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
                    str(row.get("zeroed_holding_count", 0)),
                    str(row.get("data_status", "")),
                    str(row.get("error", "")),
                ]
            )
        )

    lines.extend(["", "夜盘置零持仓", "market\tticker\taffected_funds\tnote"])
    for key, item in sorted(quote_cache.items()):
        if str(item.get("status", "")).strip().lower() != "zeroed":
            continue
        market, ticker = key
        funds = ",".join(sorted(set(affected_funds.get(key, []))))
        lines.append("\t".join([market, ticker, funds, str(item.get("error", ""))]))

    lines.extend(["", "失败/未取到证券", "market\tticker\taffected_funds\terror"])
    for key, item in sorted(quote_cache.items()):
        if _quote_item_has_value(item):
            continue
        if str(item.get("status", "")).strip().lower() == "zeroed":
            continue
        market, ticker = key
        funds = ",".join(sorted(set(affected_funds.get(key, []))))
        lines.append("\t".join([market, ticker, funds, str(item.get("error", ""))]))

    lines.extend(["", "富途源错误", "ticker\terror"])
    for ticker, error in sorted(stats.errors.items()):
        lines.append("\t".join([ticker, error]))

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_futu_night_observation(
    *,
    force: bool = False,
    current_time: datetime | str | None = None,
    fund_codes: Iterable[str] = HAIWAI_FUND_CODES,
    output_file: str | Path = FUTU_NIGHT_OUTPUT_FILE,
    report_file: str | Path = FUTU_NIGHT_REPORT_FILE,
    top_n: int = 10,
) -> PremarketRunResult:
    ensure_runtime_dirs()
    generated_at = coerce_bj_datetime(current_time)
    output_path = Path(output_file)
    report_path = Path(report_file)
    if not force and not in_futu_night_window(generated_at):
        window_text = f"{FUTU_NIGHT_START_BJ.strftime('%H:%M')}-{FUTU_NIGHT_END_BJ.strftime('%H:%M')}"
        reason = f"当前北京时间不在 {window_text} 夜盘观察窗口，未生成夜盘图；如需测试请使用 --force。"
        print(reason, flush=True)
        return PremarketRunResult(
            generated=False,
            reason=reason,
            output_file=output_path,
            report_file=report_path,
        )

    session_status = futu_night_session_status(generated_at)
    if not session_status.is_open:
        reason = session_status.reason
        print(reason, flush=True)
        return PremarketRunResult(
            generated=False,
            reason=reason,
            output_file=output_path,
            report_file=report_path,
        )

    return_cache = FutuNightReturnCache(cache_now=generated_at)
    provider = FutuNightQuoteProvider(as_of_bj=generated_at, return_cache=return_cache)
    try:
        display_df, rows, benchmark_footer_items, quote_cache, affected_funds = build_futu_night_table(
            fund_codes=fund_codes,
            top_n=top_n,
            current_time=generated_at,
            provider=provider,
        )
        _print_night_estimate_table(display_df, generated_at=generated_at)
        save_premarket_image(
            display_df,
            generated_at=generated_at,
            output_file=output_path,
            benchmark_footer_items=benchmark_footer_items,
            session=FUTU_NIGHT_SESSION,
        )
        _write_futu_night_report(
            report_path,
            generated_at=generated_at,
            rows=rows,
            quote_cache=quote_cache,
            affected_funds=affected_funds,
            provider=provider,
        )
        valid_count = len([item for item in quote_cache.values() if _quote_item_has_value(item)])
        missing_count = len(quote_cache) - valid_count
        reason = f"夜盘观察图生成完成: {relative_path_str(output_path)}"
        print(reason, flush=True)
        return PremarketRunResult(
            generated=True,
            reason=reason,
            output_file=output_path,
            report_file=report_path,
            fund_count=len(rows),
            valid_security_count=valid_count,
            missing_security_count=missing_count,
        )
    finally:
        try:
            provider.return_cache.save()
        finally:
            provider.close()


__all__ = [
    "FUTU_NIGHT_SESSION",
    "FUTU_NIGHT_START_BJ",
    "FUTU_NIGHT_END_BJ",
    "build_futu_night_table",
    "in_futu_night_window",
    "run_futu_night_observation",
]
