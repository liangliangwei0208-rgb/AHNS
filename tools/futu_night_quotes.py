"""
Futu OpenAPI quote helpers for the overnight overseas fund observation.

The module keeps Futu-specific logic out of the legacy premarket estimator:
it subscribes only the current run's required US tickers, stores only computed
return results, and validates every cached result against the target night
valuation date before reuse.
"""

from __future__ import annotations

import json
import math
import time as time_module
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from tools.configs.futu_night_configs import (
    FUTU_NIGHT_FUTURE_QUOTE_TOLERANCE_MINUTES,
    FUTU_NIGHT_KLINE_BARS,
    FUTU_NIGHT_MAX_QUOTE_STALENESS_MINUTES,
    FUTU_NIGHT_MIN_SUBSCRIBE_SECONDS,
    FUTU_NIGHT_OPEND_HOST,
    FUTU_NIGHT_OPEND_PORT,
    FUTU_NIGHT_RETURN_CACHE,
    FUTU_NIGHT_RETURN_CACHE_MAX_ITEMS,
    FUTU_NIGHT_RETURN_CACHE_RETENTION_DAYS,
    FUTU_NIGHT_RETURN_CACHE_TTL_MINUTES,
    FUTU_NIGHT_SUBSCRIBE_BATCH_SIZE,
    FUTU_NIGHT_SUBSCRIBE_LIMIT,
)
from tools.get_top10_holdings import _market_schedule
from tools.paths import ensure_runtime_dirs
from tools.premarket_estimator import BJ_TZ, US_EASTERN_TZ, coerce_bj_datetime


FUTU_NIGHT_CACHE_SCOPE = "futu_night"


@dataclass
class FutuNightQuoteStats:
    us_requested_count: int = 0
    us_cache_hit_count: int = 0
    us_subscribed_count: int = 0
    us_unsubscribed_count: int = 0
    us_unsubscribe_error_count: int = 0
    us_subscribe_batch_count: int = 0
    us_fetch_error_count: int = 0
    cache_write_count: int = 0
    errors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FutuNightSessionStatus:
    is_open: bool
    reason: str
    target_us_date: str
    window_start_bj: datetime
    window_end_bj: datetime
    window_start_et: datetime
    window_end_et: datetime


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        text = str(value).strip().replace("%", "").replace(",", "")
        if not text or text in {"-", "--", "nan", "None"}:
            return None
        out = float(text)
        if not math.isfinite(out):
            return None
        return out
    except Exception:
        return None


def _cache_key(market: Any, ticker: Any, target_date: Any) -> str:
    market_norm = str(market or "").strip().upper()
    ticker_norm = str(ticker or "").strip().upper()
    return f"{market_norm}:{ticker_norm}:{str(target_date or '').strip()}"


def normalize_us_ticker(ticker: Any) -> str:
    return str(ticker or "").strip().upper()


def futu_us_code(ticker: Any) -> str:
    ticker_norm = normalize_us_ticker(ticker)
    if not ticker_norm:
        raise RuntimeError("富途美股 ticker 为空")
    if ticker_norm.startswith("US."):
        return ticker_norm
    return f"US.{ticker_norm}"


def _parse_bj_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BJ_TZ)
    return dt.astimezone(BJ_TZ)


def _parse_futu_time_key(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = pd.to_datetime(text, errors="raise")
    except Exception:
        return None
    dt = parsed.to_pydatetime()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=US_EASTERN_TZ)
    return dt.astimezone(US_EASTERN_TZ)


def _previous_us_trading_date(day: str) -> str:
    target = datetime.strptime(str(day), "%Y-%m-%d").date()
    start = target - timedelta(days=20)
    end = target - timedelta(days=1)
    try:
        schedule = _market_schedule("US", start, end)
        if schedule is not None and not schedule.empty:
            return pd.Timestamp(schedule.index[-1]).strftime("%Y-%m-%d")
    except Exception:
        pass

    candidate = end
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate.isoformat()


def _next_us_trading_date_after(day: str) -> str:
    base = datetime.strptime(str(day), "%Y-%m-%d").date()
    start = base + timedelta(days=1)
    end = base + timedelta(days=20)
    try:
        schedule = _market_schedule("US", start, end)
        if schedule is not None and not schedule.empty:
            return pd.Timestamp(schedule.index[0]).strftime("%Y-%m-%d")
    except Exception:
        pass

    candidate = start
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def futu_night_valuation_date(as_of_bj: datetime | str | None = None) -> str:
    dt_us = coerce_bj_datetime(as_of_bj).astimezone(US_EASTERN_TZ)
    local_time = dt_us.time().replace(second=0, microsecond=0)
    target_date = dt_us.date()
    if local_time < time(16, 0):
        target_date -= timedelta(days=1)
    while target_date.weekday() >= 5:
        target_date -= timedelta(days=1)
    return _next_us_trading_date_after(target_date.isoformat())


def _is_us_trading_date(day: str) -> bool:
    target = datetime.strptime(str(day), "%Y-%m-%d").date()
    try:
        schedule = _market_schedule("US", target, target)
        return bool(schedule is not None and not schedule.empty)
    except Exception:
        return target.weekday() < 5


def _night_window_et(target_us_date: str) -> tuple[datetime, datetime]:
    # Futu overnight for a US valuation date is the previous calendar evening,
    # not the previous exchange trading date. Otherwise a Monday target would
    # incorrectly include the whole weekend after Friday 20:00 ET.
    target_date = datetime.strptime(str(target_us_date), "%Y-%m-%d").date()
    previous_date = target_date - timedelta(days=1)
    start = datetime.combine(
        previous_date,
        time(20, 0),
        tzinfo=US_EASTERN_TZ,
    )
    end = datetime.combine(
        target_date,
        time(4, 0),
        tzinfo=US_EASTERN_TZ,
    )
    return start, end


def futu_night_session_status(as_of_bj: datetime | str | None = None) -> FutuNightSessionStatus:
    as_of = coerce_bj_datetime(as_of_bj)
    as_of_et = as_of.astimezone(US_EASTERN_TZ)
    target_us_date = futu_night_valuation_date(as_of)
    start_et, end_et = _night_window_et(target_us_date)
    start_bj = start_et.astimezone(BJ_TZ)
    end_bj = end_et.astimezone(BJ_TZ)

    window_text = (
        f"{start_bj.isoformat(timespec='seconds')} - "
        f"{end_bj.isoformat(timespec='seconds')}"
    )
    if not _is_us_trading_date(target_us_date):
        return FutuNightSessionStatus(
            is_open=False,
            reason=f"目标夜盘不可用：{target_us_date} 不是 NYSE 交易日，未运行富途夜盘。",
            target_us_date=target_us_date,
            window_start_bj=start_bj,
            window_end_bj=end_bj,
            window_start_et=start_et,
            window_end_et=end_et,
        )
    if as_of_et < start_et or as_of_et > end_et:
        return FutuNightSessionStatus(
            is_open=False,
            reason=(
                "当前不在美股夜盘开市窗口，未运行富途夜盘；"
                f"目标估值日 {target_us_date}，夜盘窗口北京时间 {window_text}。"
            ),
            target_us_date=target_us_date,
            window_start_bj=start_bj,
            window_end_bj=end_bj,
            window_start_et=start_et,
            window_end_et=end_et,
        )
    return FutuNightSessionStatus(
        is_open=True,
        reason=f"美股夜盘开市中，目标估值日 {target_us_date}。",
        target_us_date=target_us_date,
        window_start_bj=start_bj,
        window_end_bj=end_bj,
        window_start_et=start_et,
        window_end_et=end_et,
    )


def _quote_time_bj_text(dt_et: datetime) -> str:
    return dt_et.astimezone(BJ_TZ).isoformat(timespec="seconds")


def _validate_quote_time(
    quote_dt_bj: datetime,
    *,
    target_us_date: str,
    as_of_bj: datetime,
    check_staleness: bool,
) -> None:
    quote_dt_et = quote_dt_bj.astimezone(US_EASTERN_TZ)
    start_et, end_et = _night_window_et(target_us_date)
    if not (start_et <= quote_dt_et <= end_et):
        raise RuntimeError(
            "报价时间不属于目标美股夜盘窗口: "
            f"quote={quote_dt_et.isoformat()}, target={target_us_date}"
        )

    future_tolerance = timedelta(minutes=max(0, int(FUTU_NIGHT_FUTURE_QUOTE_TOLERANCE_MINUTES)))
    if quote_dt_bj > as_of_bj + future_tolerance:
        raise RuntimeError(
            f"报价时间晚于本次运行时间: quote={quote_dt_bj.isoformat()}, as_of={as_of_bj.isoformat()}"
        )

    if check_staleness:
        max_age = timedelta(minutes=max(1, int(FUTU_NIGHT_MAX_QUOTE_STALENESS_MINUTES)))
        if quote_dt_bj < as_of_bj - max_age:
            raise RuntimeError(
                f"报价时间过旧: quote={quote_dt_bj.isoformat()}, as_of={as_of_bj.isoformat()}"
            )


def _item_has_value(item: dict[str, Any]) -> bool:
    if _safe_float(item.get("return_pct")) is not None:
        return True
    return str(item.get("value_type", "")).strip().lower() == "level" and _safe_float(item.get("value")) is not None


def validate_cached_return_item(
    item: dict[str, Any],
    *,
    market: str,
    ticker: str,
    target_us_date: str,
    as_of_bj: datetime,
) -> dict[str, Any]:
    if not isinstance(item, dict) or not _item_has_value(item):
        raise RuntimeError("缓存没有有效涨跌幅")
    if str(item.get("cache_scope", "")).strip() != FUTU_NIGHT_CACHE_SCOPE:
        raise RuntimeError("缓存 scope 不匹配")
    if str(item.get("trade_date") or "").strip() != str(target_us_date):
        raise RuntimeError(
            f"缓存交易日不匹配: trade_date={item.get('trade_date')}, target={target_us_date}"
        )

    fetched_at = _parse_bj_datetime(item.get("fetched_at_bj"))
    if fetched_at is None:
        raise RuntimeError("缓存缺少 fetched_at_bj")
    ttl = timedelta(minutes=max(1, int(FUTU_NIGHT_RETURN_CACHE_TTL_MINUTES)))
    if fetched_at > as_of_bj + timedelta(minutes=1) or as_of_bj - fetched_at > ttl:
        raise RuntimeError(
            f"缓存已过期: fetched_at={fetched_at.isoformat()}, as_of={as_of_bj.isoformat()}"
        )

    if str(market or "").strip().upper() == "US":
        source = str(item.get("source") or "")
        if not source.startswith("futu_night"):
            raise RuntimeError(f"美股缓存来源不是富途夜盘: source={source}")
        quote_dt_bj = _parse_bj_datetime(item.get("quote_time_bj"))
        if quote_dt_bj is None:
            raise RuntimeError(f"缓存缺少可校验报价时间: {ticker}")
        _validate_quote_time(
            quote_dt_bj,
            target_us_date=target_us_date,
            as_of_bj=as_of_bj,
            check_staleness=True,
        )

    out = dict(item)
    out["cache_hit"] = "file"
    return out


def _prune_return_cache(
    cache: dict[str, dict[str, Any]],
    *,
    cache_now: datetime,
) -> dict[str, dict[str, Any]]:
    retention = timedelta(days=max(1, int(FUTU_NIGHT_RETURN_CACHE_RETENTION_DAYS)))
    kept: dict[str, dict[str, Any]] = {}
    for key, item in (cache or {}).items():
        if not isinstance(item, dict):
            continue
        fetched_at = _parse_bj_datetime(item.get("fetched_at_bj"))
        if fetched_at is None or fetched_at > cache_now + timedelta(minutes=1):
            continue
        if cache_now - fetched_at > retention:
            continue
        kept[str(key)] = dict(item)

    def sort_key(pair: tuple[str, dict[str, Any]]) -> str:
        fetched_at = _parse_bj_datetime(pair[1].get("fetched_at_bj"))
        return "" if fetched_at is None else fetched_at.isoformat()

    newest = sorted(kept.items(), key=sort_key, reverse=True)[: max(1, int(FUTU_NIGHT_RETURN_CACHE_MAX_ITEMS))]
    return dict(newest)


class FutuNightReturnCache:
    def __init__(self, *, cache_file: str | Path = FUTU_NIGHT_RETURN_CACHE, cache_now: datetime):
        self.cache_file = Path(cache_file)
        self.cache_now = coerce_bj_datetime(cache_now)
        self.data: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            with self.cache_file.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            print(f"[WARN] 富途夜盘涨跌幅缓存读取失败，将忽略旧缓存: {exc}", flush=True)
            return {}
        if not isinstance(loaded, dict):
            return {}
        return _prune_return_cache(loaded, cache_now=self.cache_now)

    def get(self, market: str, ticker: str, target_us_date: str, as_of_bj: datetime) -> dict[str, Any] | None:
        item = self.data.get(_cache_key(market, ticker, target_us_date))
        if not isinstance(item, dict):
            return None
        try:
            return validate_cached_return_item(
                item,
                market=market,
                ticker=ticker,
                target_us_date=target_us_date,
                as_of_bj=as_of_bj,
            )
        except Exception:
            return None

    def remember(
        self,
        market: str,
        ticker: str,
        target_us_date: str,
        item: dict[str, Any],
        *,
        fetched_at_bj: datetime,
    ) -> None:
        if not isinstance(item, dict) or not _item_has_value(item):
            return
        record = dict(item)
        record["cache_scope"] = FUTU_NIGHT_CACHE_SCOPE
        record["fetched_at_bj"] = coerce_bj_datetime(fetched_at_bj).isoformat(timespec="seconds")
        record.setdefault("market", str(market or "").strip().upper())
        record.setdefault("ticker", str(ticker or "").strip().upper())
        record.setdefault("trade_date", str(target_us_date))
        record.pop("cache_hit", None)
        self.data[_cache_key(market, ticker, target_us_date)] = record

    def save(self) -> None:
        ensure_runtime_dirs()
        pruned = _prune_return_cache(self.data, cache_now=self.cache_now)
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(
            json.dumps(pruned, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.data.clear()
        self.data.update(pruned)


class FutuNightQuoteProvider:
    def __init__(
        self,
        *,
        as_of_bj: datetime | str | None = None,
        return_cache: FutuNightReturnCache | None = None,
        host: str = FUTU_NIGHT_OPEND_HOST,
        port: int = FUTU_NIGHT_OPEND_PORT,
    ):
        self.as_of_bj = coerce_bj_datetime(as_of_bj)
        self.return_cache = return_cache or FutuNightReturnCache(cache_now=self.as_of_bj)
        self.host = str(host)
        self.port = int(port)
        self.ctx = None
        self.runtime: dict[tuple[str, str], dict[str, Any]] = {}
        self.failures: dict[str, str] = {}
        self.subscribed_codes: set[str] = set()
        self.subscribed_at: dict[str, datetime] = {}
        self.stats = FutuNightQuoteStats()

    def close(self) -> None:
        if self.ctx is not None and self.subscribed_codes:
            try:
                self._unsubscribe_codes(sorted(self.subscribed_codes))
            except Exception:
                pass
        if self.ctx is not None:
            try:
                self.ctx.close()
            except Exception:
                pass
            self.ctx = None

    def _ensure_ctx(self):
        if self.ctx is not None:
            return self.ctx
        try:
            from futu import OpenQuoteContext
        except Exception as exc:
            raise RuntimeError("未安装 futu-api，请先安装或确认当前 Python 环境") from exc
        self.ctx = OpenQuoteContext(host=self.host, port=self.port)
        return self.ctx

    def _unsubscribe_codes(self, codes: list[str]) -> None:
        if not codes or self.ctx is None:
            return
        try:
            from futu import SubType

            min_seconds = max(0, int(FUTU_NIGHT_MIN_SUBSCRIBE_SECONDS))
            if min_seconds:
                now = datetime.now(tz=BJ_TZ)
                waits = []
                for code in codes:
                    started_at = self.subscribed_at.get(code)
                    if started_at is None:
                        continue
                    elapsed = (now - started_at).total_seconds()
                    waits.append(max(0.0, float(min_seconds) - elapsed))
                wait_seconds = max(waits) if waits else 0.0
                if wait_seconds > 0:
                    time_module.sleep(wait_seconds)

            ret, message = self.ctx.unsubscribe(codes, [SubType.K_1M])
            if ret == 0:
                self.stats.us_unsubscribed_count += len(codes)
                self.subscribed_codes.difference_update(codes)
                for code in codes:
                    self.subscribed_at.pop(code, None)
            else:
                self.stats.us_unsubscribe_error_count += len(codes)
                self.stats.errors["unsubscribe"] = str(message)
        except Exception as exc:
            self.stats.us_unsubscribe_error_count += len(codes)
            self.stats.errors["unsubscribe"] = repr(exc)

    def _get_from_cache(self, ticker: str, target_us_date: str) -> dict[str, Any] | None:
        item = self.return_cache.get("US", ticker, target_us_date, self.as_of_bj)
        if item is not None:
            self.runtime[("US", normalize_us_ticker(ticker))] = dict(item)
            self.stats.us_cache_hit_count += 1
            return item
        return None

    def prefetch_us_returns(
        self,
        tickers: Iterable[str],
        *,
        target_us_date: str,
        progress: Any | None = None,
    ) -> None:
        unique = []
        seen = set()
        for ticker in tickers:
            ticker_norm = normalize_us_ticker(ticker)
            if not ticker_norm or ticker_norm in seen:
                continue
            seen.add(ticker_norm)
            unique.append(ticker_norm)
        if not unique:
            return

        self.stats.us_requested_count += len(unique)
        missing: list[str] = []
        ready_count = 0
        failed_skip_count = 0
        if progress is not None:
            progress.start_item(f"检查夜盘缓存: {len(unique)} 个标的")
        for ticker in unique:
            if ("US", ticker) in self.runtime:
                ready_count += 1
                continue
            if ticker in self.failures:
                failed_skip_count += 1
                continue
            if self._get_from_cache(ticker, target_us_date) is not None:
                ready_count += 1
                continue
            missing.append(ticker)

        if progress is not None:
            progress.advance_units(ready_count, success=True, status=f"夜盘缓存命中/本轮已取: {ready_count} 个")
            progress.advance_units(failed_skip_count, success=False, status=f"本轮已记录失败: {failed_skip_count} 个")
        if not missing:
            if progress is not None:
                progress.set_status("夜盘报价预取完成，全部来自缓存或本轮结果")
            return

        if progress is not None:
            progress.start_item(
                f"连接 Futu OpenD {self.host}:{self.port}，批量读取夜盘快照: {len(missing)} 个"
            )
        snapshot_done = self._fetch_us_snapshot_batch(missing, target_us_date=target_us_date)
        if progress is not None:
            progress.advance_units(
                len(snapshot_done),
                success=True,
                status=f"富途快照命中: {len(snapshot_done)} 个",
            )
        missing = [ticker for ticker in missing if ticker not in snapshot_done and ticker not in self.failures]

        batch_size = min(
            max(1, int(FUTU_NIGHT_SUBSCRIBE_BATCH_SIZE)),
            max(1, int(FUTU_NIGHT_SUBSCRIBE_LIMIT)),
        )
        batch_total = (len(missing) + batch_size - 1) // batch_size if missing else 0
        for batch_index, start in enumerate(range(0, len(missing), batch_size), start=1):
            batch = missing[start : start + batch_size]
            if progress is not None:
                progress.start_item(
                    f"订阅 Futu 1分钟K线第 {batch_index}/{batch_total} 批: {len(batch)} 个"
                )
            done = self._fetch_us_batch(batch, target_us_date=target_us_date)
            failed_count = len([ticker for ticker in batch if ticker not in done and ticker in self.failures])
            unresolved_count = max(0, len(batch) - len(done) - failed_count)
            if progress is not None:
                progress.advance_units(len(done), success=True, status=f"富途K线成功: {len(done)} 个")
                progress.advance_units(failed_count, success=False, status=f"富途K线失败: {failed_count} 个")
                progress.advance_units(unresolved_count, success=False, status=f"富途K线未返回: {unresolved_count} 个")
        if progress is not None:
            progress.set_status(
                f"夜盘报价预取完成: 成功 {len(self.runtime)} 个，失败 {len(self.failures)} 个"
            )

    def _fetch_us_snapshot_batch(self, tickers: list[str], *, target_us_date: str) -> set[str]:
        if not tickers:
            return set()
        ctx = self._ensure_ctx()
        futu_codes = [futu_us_code(ticker) for ticker in tickers]
        try:
            from futu import RET_OK

            ret, data = ctx.get_market_snapshot(futu_codes)
            if ret != RET_OK:
                return set()
            if data is None or getattr(data, "empty", True):
                return set()
        except Exception:
            return set()

        rows_by_code: dict[str, Any] = {}
        for _, row in data.iterrows():
            rows_by_code[str(row.get("code") or "").strip().upper()] = row

        done: set[str] = set()
        for ticker, code in zip(tickers, futu_codes):
            row = rows_by_code.get(code.upper())
            if row is None:
                continue
            try:
                item = self._quote_from_snapshot(
                    ticker,
                    code,
                    row,
                    target_us_date=target_us_date,
                )
                self.runtime[("US", ticker)] = item
                self.return_cache.remember(
                    "US",
                    ticker,
                    target_us_date,
                    item,
                    fetched_at_bj=self.as_of_bj,
                )
                self.stats.cache_write_count += 1
                done.add(ticker)
            except Exception as exc:
                message = repr(exc)
                self.failures[ticker] = message
                self.stats.errors[ticker] = message
                self.stats.us_fetch_error_count += 1
        return done

    def _quote_from_snapshot(
        self,
        ticker: str,
        code: str,
        row: Any,
        *,
        target_us_date: str,
    ) -> dict[str, Any]:
        update_dt_et = _parse_futu_time_key(row.get("update_time"))
        if update_dt_et is None:
            raise RuntimeError(f"富途快照缺少可校验更新时间: {code}")

        start_et, end_et = _night_window_et(target_us_date)
        if update_dt_et < start_et:
            raise RuntimeError(
                f"富途快照更新时间早于目标夜盘窗口: {code}, update={update_dt_et.isoformat()}, target={target_us_date}"
            )
        update_dt_bj = update_dt_et.astimezone(BJ_TZ)
        if update_dt_bj > self.as_of_bj + timedelta(
            minutes=max(0, int(FUTU_NIGHT_FUTURE_QUOTE_TOLERANCE_MINUTES))
        ):
            raise RuntimeError(
                f"富途快照更新时间晚于本次运行时间: {code}, update={update_dt_bj.isoformat()}, as_of={self.as_of_bj.isoformat()}"
            )
        if update_dt_et <= end_et:
            quote_dt_et = update_dt_et
        else:
            if update_dt_et.date().isoformat() != str(target_us_date):
                raise RuntimeError(
                    f"富途快照更新时间不属于目标估值日: {code}, update={update_dt_et.isoformat()}, target={target_us_date}"
                )
            quote_dt_et = end_et

        quote_dt_bj = quote_dt_et.astimezone(BJ_TZ)
        _validate_quote_time(
            quote_dt_bj,
            target_us_date=target_us_date,
            as_of_bj=self.as_of_bj,
            check_staleness=True,
        )

        overnight_price = _safe_float(row.get("overnight_price"))
        overnight_change_rate = _safe_float(row.get("overnight_change_rate"))
        overnight_change_val = _safe_float(row.get("overnight_change_val"))
        if overnight_price is None or overnight_price <= 0:
            raise RuntimeError(f"富途快照缺少有效 overnight_price: {code}")
        if overnight_change_rate is None and overnight_change_val is not None:
            previous = float(overnight_price) - float(overnight_change_val)
            if previous:
                overnight_change_rate = float(overnight_change_val) / previous * 100.0
        if overnight_change_rate is None:
            raise RuntimeError(f"富途快照缺少有效 overnight_change_rate: {code}")

        return {
            "return_pct": float(overnight_change_rate),
            "source": "futu_night_snapshot",
            "status": "traded",
            "trade_date": str(target_us_date),
            "quote_time_bj": quote_dt_bj.isoformat(timespec="seconds"),
            "market": "US",
            "ticker": ticker,
            "futu_code": code,
            "latest_price": float(overnight_price),
            "cache_scope": FUTU_NIGHT_CACHE_SCOPE,
            "fetched_at_bj": self.as_of_bj.isoformat(timespec="seconds"),
        }

    def _fetch_us_batch(self, tickers: list[str], *, target_us_date: str) -> set[str]:
        if not tickers:
            return set()
        ctx = self._ensure_ctx()
        futu_codes = [futu_us_code(ticker) for ticker in tickers]
        self.stats.us_subscribe_batch_count += 1
        subscribed_this_batch: list[str] = []
        done: set[str] = set()
        try:
            from futu import AuType, RET_OK, Session, SubType

            ret, message = ctx.subscribe(
                futu_codes,
                [SubType.K_1M],
                is_first_push=False,
                subscribe_push=False,
                session=Session.ALL,
            )
            if ret != RET_OK:
                error = f"富途订阅失败: {message}"
                for ticker in tickers:
                    self.failures[ticker] = error
                    self.stats.errors[ticker] = error
                self.stats.us_fetch_error_count += len(tickers)
                return done

            subscribed_this_batch = futu_codes
            self.subscribed_codes.update(futu_codes)
            subscribed_at = datetime.now(tz=BJ_TZ)
            for code in futu_codes:
                self.subscribed_at[code] = subscribed_at
            self.stats.us_subscribed_count += len(futu_codes)

            for ticker, code in zip(tickers, futu_codes):
                try:
                    ret_kline, data = ctx.get_cur_kline(
                        code,
                        int(FUTU_NIGHT_KLINE_BARS),
                        SubType.K_1M,
                        AuType.QFQ,
                    )
                    if ret_kline != RET_OK:
                        raise RuntimeError(f"富途 get_cur_kline 失败: {data}")
                    item = self._quote_from_kline(
                        ticker,
                        code,
                        data,
                        target_us_date=target_us_date,
                    )
                    self.runtime[("US", ticker)] = item
                    self.return_cache.remember(
                        "US",
                        ticker,
                        target_us_date,
                        item,
                        fetched_at_bj=self.as_of_bj,
                    )
                    self.stats.cache_write_count += 1
                    done.add(ticker)
                except Exception as exc:
                    message = repr(exc)
                    self.failures[ticker] = message
                    self.stats.errors[ticker] = message
                    self.stats.us_fetch_error_count += 1
        finally:
            self._unsubscribe_codes(subscribed_this_batch)
        return done

    def _quote_from_kline(
        self,
        ticker: str,
        code: str,
        data: Any,
        *,
        target_us_date: str,
    ) -> dict[str, Any]:
        if data is None or getattr(data, "empty", True):
            raise RuntimeError(f"富途夜盘 K 线返回空数据: {code}")

        df = data.copy()
        if "time_key" not in df.columns:
            raise RuntimeError(f"富途夜盘 K 线缺少 time_key: {code}")
        if "close" not in df.columns:
            raise RuntimeError(f"富途夜盘 K 线缺少 close: {code}")
        if "change_rate" not in df.columns:
            raise RuntimeError(f"富途夜盘 K 线缺少可用夜盘涨跌幅字段: {code}")

        start_et, end_et = _night_window_et(target_us_date)
        candidates: list[tuple[datetime, Any]] = []
        for _, row in df.iterrows():
            dt_et = _parse_futu_time_key(row.get("time_key"))
            if dt_et is None:
                continue
            if start_et <= dt_et <= end_et:
                quote_dt_bj = dt_et.astimezone(BJ_TZ)
                if quote_dt_bj <= self.as_of_bj + timedelta(
                    minutes=max(0, int(FUTU_NIGHT_FUTURE_QUOTE_TOLERANCE_MINUTES))
                ):
                    candidates.append((dt_et, row))

        if not candidates:
            raise RuntimeError(f"富途没有目标美股日期夜盘 K 线: {code}, target={target_us_date}")

        latest_dt_et, latest_row = sorted(candidates, key=lambda item: item[0])[-1]
        quote_dt_bj = latest_dt_et.astimezone(BJ_TZ)
        _validate_quote_time(
            quote_dt_bj,
            target_us_date=target_us_date,
            as_of_bj=self.as_of_bj,
            check_staleness=True,
        )

        latest_price = _safe_float(latest_row.get("close"))
        return_pct = _safe_float(latest_row.get("change_rate"))
        if latest_price is None or latest_price <= 0:
            raise RuntimeError(f"富途夜盘最新价格无效: {code}, close={latest_row.get('close')}")
        if return_pct is None:
            raise RuntimeError(f"富途夜盘 K 线缺少有效涨跌幅: {code}")

        return {
            "return_pct": float(return_pct),
            "source": "futu_night_1m",
            "status": "traded",
            "trade_date": str(target_us_date),
            "quote_time_bj": _quote_time_bj_text(latest_dt_et),
            "market": "US",
            "ticker": ticker,
            "futu_code": code,
            "latest_price": float(latest_price),
            "cache_scope": FUTU_NIGHT_CACHE_SCOPE,
            "fetched_at_bj": self.as_of_bj.isoformat(timespec="seconds"),
        }

    def get_us_return(self, ticker: str, *, target_us_date: str) -> dict[str, Any]:
        ticker_norm = normalize_us_ticker(ticker)
        if not ticker_norm:
            raise RuntimeError("富途夜盘 ticker 为空")
        existing = self.runtime.get(("US", ticker_norm))
        if existing is not None:
            return dict(existing)
        cached = self._get_from_cache(ticker_norm, target_us_date)
        if cached is not None:
            return dict(cached)
        if ticker_norm in self.failures:
            raise RuntimeError(self.failures[ticker_norm])
        self.prefetch_us_returns([ticker_norm], target_us_date=target_us_date)
        existing = self.runtime.get(("US", ticker_norm))
        if existing is not None:
            return dict(existing)
        error = self.failures.get(ticker_norm) or "富途夜盘未取到有效数据"
        raise RuntimeError(error)


__all__ = [
    "FUTU_NIGHT_CACHE_SCOPE",
    "FutuNightQuoteProvider",
    "FutuNightQuoteStats",
    "FutuNightReturnCache",
    "FutuNightSessionStatus",
    "futu_night_session_status",
    "futu_night_valuation_date",
    "futu_us_code",
    "normalize_us_ticker",
    "validate_cached_return_item",
]
