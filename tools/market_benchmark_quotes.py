"""共享 VIX 与 Service ETF 兜底；短状态仅供本机使用，不参与三边同步。"""
from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tools.configs.market_benchmark_configs import (
    VIX_SOURCE_TIMEOUT_SECONDS, VIX_REFRESH_BUDGET_SECONDS,
    VIX_REALTIME_CACHE_SECONDS, BENCHMARK_SOURCE_COOLDOWN_SECONDS,
    BENCHMARK_FUTU_ENV, BENCHMARK_FUTU_ETFS,
)
from tools.paths import CACHE_DIR
from tools.runtime_stats import record_market_event, timed_market_call

LOCAL_DIR = CACHE_DIR / "market_benchmark_local"
BJ = ZoneInfo("Asia/Shanghai")
ET = ZoneInfo("America/New_York")


class SourceError(RuntimeError):
    def __init__(self, message, *, http_status=None, retry_after=""):
        super().__init__(message)
        self.http_status = http_status
        self.retry_after = retry_after


def run_worker(command, *, timeout):
    """subprocess.run 超时会杀死并回收子进程，不留下阻塞线程。"""
    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", timeout=max(0.01, timeout))
    except subprocess.TimeoutExpired as exc:
        raise SourceError("source timeout: worker terminated") from exc
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise SourceError(f"worker returned invalid output (exit={result.returncode})") from exc
    if "error" in payload:
        raise SourceError(payload["error"], http_status=payload.get("http_status"), retry_after=payload.get("retry_after", ""))
    return payload


def run_source(source, *, timeout, **kwargs):
    args = {"source": source, "timeout": timeout, **kwargs}
    return run_worker([sys.executable, str(Path(__file__).with_name("market_benchmark_worker.py")), json.dumps(args)], timeout=timeout)


def completed_sessions(now):
    """复用正式估算的交易日历和收盘缓冲，不用工作日猜节假日。"""
    import pandas as pd
    import pandas_market_calendars as mcal
    from tools.configs.market_calendar_configs import MARKET_CALENDAR_NAMES, MARKET_CLOSE_BUFFER_HOURS
    schedule = mcal.get_calendar(MARKET_CALENDAR_NAMES["US"]).schedule(
        start_date=(now-timedelta(days=24)).date(), end_date=now.date())
    ready = schedule.loc[schedule["market_close"]+pd.Timedelta(hours=MARKET_CLOSE_BUFFER_HOURS) <= pd.Timestamp(now)]
    if ready.empty:
        raise RuntimeError("无法确认最近完整美股交易日")
    return [str(day.date()) for day in ready.index]


def _now(value=None):
    at = value or datetime.now(BJ)
    return at.replace(tzinfo=BJ) if at.tzinfo is None else at.astimezone(BJ)


def _time(value):
    return _now(datetime.fromisoformat(str(value)))


def _positive(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("点位/价格必须为有限正数")
    return value


def _read_state():
    try:
        state = json.loads((LOCAL_DIR/"state.json").read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


@contextmanager
def locked_state():
    """OS 锁随进程退出释放；读改写整体加锁，避免多入口相互覆盖。"""
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    with (LOCAL_DIR/"state.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0"); handle.flush()
        acquired = False
        until = time.monotonic()+0.5
        while not acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() >= until:
                    raise SourceError("本机基准状态正由另一进程更新")
                time.sleep(0.025)
        state = _read_state()
        try:
            yield state
        finally:
            # 失败信息和成功点位一并原子保存；临时文件同目录，读者不会读到半截 JSON。
            try:
                temp = LOCAL_DIR/f"state.{os.getpid()}.tmp"
                temp.write_text(json.dumps(state, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                temp.replace(LOCAL_DIR/"state.json")
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def legacy_vix_closes():
    """只读已有可信收盘值；实时缓存绝不能冒充收盘记录。"""
    rows = []
    for name, key in [("security_return_cache.json", None), ("fund_estimate_return_cache.json", "benchmark_records")]:
        try:
            data = json.loads((CACHE_DIR/name).read_text(encoding="utf-8-sig"))
            data = data.get(key, {}) if key else data
            for item in data.values():
                if not isinstance(item, dict) or str(item.get("ticker", item.get("symbol", ""))).upper() != "VIX":
                    continue
                source = str(item.get("source", "")).lower()
                if "cboe" in source or "fred" in source:
                    rows.append({**item, "value": item.get("value", item.get("close"))})
        except (OSError, ValueError, AttributeError):
            continue
    try:
        with (CACHE_DIR/"vix_index_daily.csv").open(encoding="utf-8-sig") as handle:
            rows.extend({"trade_date": r["date"], "value": r["close"], "source": "local_vix_daily"} for r in csv.DictReader(handle))
    except (OSError, ValueError, KeyError):
        pass
    return rows


def _valid_close(item, sessions):
    day = str(item.get("trade_date", item.get("date", "")))[:10]
    if day not in sessions[-2:]:
        raise ValueError("收盘点位日期超出允许范围")
    return {**item, "value": _positive(item.get("value", item.get("close"))), "trade_date": day}


def _valid_live(item, now):
    value = _positive(item.get("value"))
    at = _time(item.get("quote_time_bj", ""))
    day = at.astimezone(ZoneInfo("America/Chicago")).date().isoformat()
    if str(item.get("trade_date")) != day or not -120 <= (now-at).total_seconds() <= VIX_REALTIME_CACHE_SECONDS:
        raise ValueError("实时点位日期/时间无效或已过期")
    return {**item, "value": value, "trade_date": day}


def _vix_item(item, *, kind, cached=False, errors=""):
    value = _positive(item["value"])
    return {**item, "benchmark_key": "vix", "label": "VIX恐慌指数", "ticker": "VIX", "symbol": "VIX",
            "market": "VIX_LEVEL", "kind": "vix_level", "return_pct": None, "value": value,
            "display_value": f"{value:.2f}", "value_type": "level", "status": "traded",
            "quote_kind": kind, "label_suffix": "实时" if kind == "realtime" else ("收盘缓存" if kind == "close_cache" else "收盘"),
            "cache_hit": "file" if cached else "", "error": errors}


def _event(source, *, outcome="skipped", error="", cache=False):
    record_market_event(action="benchmark_cache" if cache else "benchmark_source_skip", source=source,
                        market="VIX_LEVEL", ticker="VIX", outcome=outcome, cache_hit=cache, error=error)


def _cooldown(state, source, exc, now):
    if getattr(exc, "http_status", None) not in {403, 429}:
        return
    seconds = BENCHMARK_SOURCE_COOLDOWN_SECONDS
    if exc.http_status == 429 and exc.retry_after:
        try:
            seconds = max(0, float(exc.retry_after))
        except ValueError:
            try:
                seconds = max(0, (parsedate_to_datetime(exc.retry_after)-now).total_seconds())
            except (ValueError, TypeError):
                pass
    state.setdefault("cooldowns", {})[source] = (now+timedelta(seconds=seconds)).isoformat()


def get_vix_quote(*, realtime=True, now=None, close_candidates=()):
    now = _now(now)
    sessions = completed_sessions(now)
    errors = []
    with locked_state() as state:
        state["version"] = 1
        closes = {}
        stored = state.get("closes", {})
        for day, item in (stored.items() if isinstance(stored, dict) else []):
            try:
                checked = _valid_close(item, sessions)
                if checked["trade_date"] == day:
                    closes[day] = checked
            except (ValueError, TypeError, AttributeError):
                pass
        # 用最近两期可信收盘初始化本机短缓存，不改任何原始业务文件。
        for candidate in [*legacy_vix_closes(), *close_candidates]:
            try:
                candidate = _valid_close(candidate, sessions)
                closes.setdefault(candidate["trade_date"], candidate)
            except (ValueError, TypeError):
                pass
        state["closes"] = closes = {day: item for day, item in closes.items() if day in sessions[-2:]}
        cached = state.get("realtime") if realtime else None
        if cached:
            try:
                cached = _valid_live(cached, now)
                if 0 <= (now-_time(cached["fetched_at_bj"])).total_seconds() <= VIX_REALTIME_CACHE_SECONDS:
                    _event("vix_shared_realtime", cache=True, outcome="cache_hit")
                    return _vix_item(cached, kind="realtime", cached=True)
            except (ValueError, TypeError, KeyError):
                state.pop("realtime", None)
        latest = closes.get(sessions[-1])
        if not realtime and latest:
            _event("vix_shared_close", cache=True, outcome="cache_hit")
            return _vix_item(latest, kind="close_cache", cached=True)
        if realtime and latest and state.get("last_realtime_attempt_at"):
            try:
                if 0 <= (now-_time(state["last_realtime_attempt_at"])).total_seconds() <= VIX_REALTIME_CACHE_SECONDS:
                    _event("vix_shared_close", cache=True, outcome="cache_hit")
                    return _vix_item(latest, kind="close_cache", cached=True)
            except (ValueError, TypeError):
                pass
        deadline = time.monotonic()+VIX_REFRESH_BUDGET_SECONDS
        if realtime:
            # 收盘查询不能占用实时尝试窗口：main 后的第一张观察图仍要争取实时报价。
            state["last_realtime_attempt_at"] = now.isoformat()
        sources = ["yahoo1", "yahoo2", "cboe", "fred"] if realtime else ["cboe", "fred"]
        for source in sources:
            family = "yahoo" if source.startswith("yahoo") else source
            try:
                until = state.get("cooldowns", {}).get(family)
                if until and _time(until) > now:
                    _event(source, error="source cooldown")
                    continue
                if source == "cboe" and latest:
                    # 已有目标日完整收盘，实时失败后直接复用，不再下载整份历史。
                    _event("vix_shared_close", cache=True, outcome="cache_hit")
                    state["last_refresh_at"] = now.isoformat()
                    return _vix_item(latest, kind="close_cache", cached=True, errors=" | ".join(errors))
                left = deadline-time.monotonic()
                if left <= 0:
                    raise SourceError("VIX refresh budget exhausted")
                def fetch_valid():
                    raw = run_source(source, timeout=min(VIX_SOURCE_TIMEOUT_SECONDS, left))
                    return _valid_live(raw, now) if source.startswith("yahoo") else _valid_close(raw, sessions)
                item = timed_market_call(fetch_valid,
                    action="benchmark_network_fetch", source=source, market="VIX_LEVEL", ticker="VIX")
                if source.startswith("yahoo"):
                    item = _valid_live(item, now)
                    item.update(source=f"yahoo_chart_realtime_vix_{source}", fetched_at_bj=now.isoformat())
                    state["realtime"] = item
                    state["last_refresh_at"] = now.isoformat()
                    return _vix_item(item, kind="realtime")
                item = _valid_close(item, sessions)
                item.update(source=source.upper(), fetched_at_bj=now.isoformat())
                closes[item["trade_date"]] = item
                state["last_refresh_at"] = now.isoformat()
                # 如果源尚未发布目标日，继续尝试下一家以争取更新的有效收盘。
                if item["trade_date"] == sessions[-1]:
                    return _vix_item(item, kind="close", errors=" | ".join(errors))
            except (RuntimeError, ValueError, TypeError) as exc:
                errors.append(f"{source}: {exc}")
                _cooldown(state, family, exc, now)
        if closes:
            best = closes[max(closes)]
            state["last_refresh_at"] = now.isoformat()
            _event("vix_last_valid_close", cache=True, outcome="cache_hit")
            return _vix_item(best, kind="close_cache", cached=True, errors=" | ".join(errors))
    raise RuntimeError("VIX 获取失败: " + " | ".join(errors))


def futu_available():
    import importlib.util
    return importlib.util.find_spec("futu") is not None


def _snapshot_time(value, *, mode, target_date, now):
    """网络和短缓存共用原始行情时间校验，抓取时间不能替代行情时间。"""
    stamp = datetime.fromisoformat(str(value))
    stamp = stamp.replace(tzinfo=ET) if stamp.tzinfo is None else stamp.astimezone(ET)
    minute = stamp.hour*60+stamp.minute
    ranges = {"premarket": (240, 570), "intraday": (570, 960), "afterhours": (960, 1201)}
    start, end = ranges[mode]
    if stamp.date().isoformat() != target_date or not start <= minute < end:
        raise ValueError("快照日期/时段与请求不一致")
    if stamp > now.astimezone(ET)+timedelta(minutes=2):
        raise ValueError("拒绝未来快照")
    session_end = stamp.replace(hour=(20 if mode == "afterhours" else (16 if mode == "intraday" else 9)),
                               minute=(30 if mode == "premarket" else 0), second=0, microsecond=0)
    # 盘后结束后只接近收市的快照才可复用，不误取已进入夜盘的 last_price。
    if (min(now.astimezone(ET), session_end)-stamp).total_seconds() > 900:
        raise ValueError("快照时间过旧")
    return stamp


def _snapshot_quote(row, *, mode, target_date, now):
    stamp = _snapshot_time(row.get("update_time", ""),mode=mode,target_date=target_date,now=now)
    price = _positive(row.get({"premarket":"pre_price", "intraday":"last_price", "afterhours":"after_price"}[mode]))
    if mode == "afterhours":
        change = row.get("after_change_val")
        if change is not None:
            reference = _positive(price-float(change))
        else:
            rate = float(row["after_change_rate"])
            reference = _positive(price/(1+rate/100))
    else:
        reference = _positive(row.get("prev_close_price"))
    result = (price/reference-1)*100
    if not math.isfinite(result):
        raise ValueError("涨跌幅无效")
    return {"return_pct":result, "trade_date":target_date, "quote_time_bj":stamp.astimezone(BJ).isoformat(),
            "source":f"futu_benchmark_{mode}_{'post' if mode == 'afterhours' else ('pre' if mode == 'premarket' else 'regular')}", "status":"traded"}


def get_futu_etf_quote(ticker, *, mode, target_date, now=None):
    """只接同标的 ETF；不为真实指数或黄金现货猜测代理标的。"""
    now = _now(now)
    ticker = str(ticker).upper()
    if os.environ.get(BENCHMARK_FUTU_ENV) != "1" or ticker not in BENCHMARK_FUTU_ETFS:
        record_market_event(action="benchmark_source_skip", source="futu", market="US", ticker=ticker,
                            outcome="skipped", error="not Service or unsupported exact ETF")
        raise SourceError("富途兜底仅供 Service 配置中的同标的 ETF")
    if mode not in {"daily", "premarket", "intraday", "afterhours"} or not futu_available():
        record_market_event(action="benchmark_source_skip", source="futu", market="US", ticker=ticker,
                            outcome="skipped", error="SDK unavailable or invalid phase")
        raise SourceError("富途 SDK 不可用或时段不支持")
    start = ""
    if mode == "daily":
        sessions = completed_sessions(now)
        if target_date not in sessions[1:]:
            raise SourceError("目标日尚未完整收盘或缺少上一交易日")
        start = sessions[sessions.index(target_date)-1]
    cache_key = f"{ticker}:{mode}:{target_date}"
    with locked_state() as state:
        etfs = state.get("etfs", {})
        if not isinstance(etfs, dict):
            etfs = {}
        cached = etfs.get(cache_key)
        if cached:
            try:
                expected_source = "futu_benchmark_daily_unadjusted" if mode == "daily" else f"futu_benchmark_{mode}_{'post' if mode == 'afterhours' else ('pre' if mode == 'premarket' else 'regular')}"
                if (cached.get("ticker") != ticker or cached.get("mode") != mode
                        or cached.get("trade_date") != target_date or cached.get("source") != expected_source
                        or cached.get("status") != "traded" or not math.isfinite(float(cached["return_pct"]))):
                    raise ValueError("ETF 缓存标的/时段/日期/数值不可信")
                if mode != "daily":
                    _snapshot_time(cached["quote_time_bj"],mode=mode,target_date=target_date,now=now)
                age = (now-_time(cached["fetched_at_bj"])).total_seconds()
            except (ValueError, KeyError, TypeError, AttributeError):
                # 只失效坏记录，不让损坏值阻止重新取数，也不删除任何历史文件。
                etfs.pop(cache_key, None)
                state["etfs"] = etfs
            else:
                if 0 <= age <= VIX_REALTIME_CACHE_SECONDS:
                    record_market_event(action="benchmark_cache",source="futu_etf_cache",market="US",ticker=ticker,cache_hit=True,outcome="cache_hit")
                    return {**cached, "cache_hit":"file"}
    from tools.configs.futu_night_configs import FUTU_NIGHT_OPEND_HOST, FUTU_NIGHT_OPEND_PORT
    def fetch():
        raw = run_source("futu", timeout=VIX_SOURCE_TIMEOUT_SECONDS, ticker=ticker, mode=mode, day=target_date,
                         start=start, host=FUTU_NIGHT_OPEND_HOST, port=FUTU_NIGHT_OPEND_PORT)
        if mode == "daily":
            rows = {str(row["time_key"])[:10]:row["close"] for row in raw["rows"]}
            previous, current = _positive(rows[start]), _positive(rows[target_date])
            change = (current/previous-1)*100
            if not math.isfinite(change):
                raise ValueError("日线涨跌幅不是有限数")
            return {"return_pct":change, "trade_date":target_date,
                    "source":"futu_benchmark_daily_unadjusted", "status":"traded"}
        return _snapshot_quote(raw["snapshot"],mode=mode,target_date=target_date,now=now)
    try:
        item = timed_market_call(fetch,action="benchmark_futu_fetch",source="futu",market="US",ticker=ticker)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        raise SourceError(str(exc)) from exc
    item.update(fetched_at_bj=now.isoformat(),ticker=ticker,mode=mode)
    with locked_state() as state:
        etfs = state.setdefault("etfs", {})
        # 本机状态有界；不触碰证券历史或正式基金估算缓存。
        etfs = {key: row for key, row in etfs.items() if str(row.get("fetched_at_bj", ""))[:10] >= (now-timedelta(days=2)).date().isoformat()}
        etfs[cache_key] = item
        state["etfs"] = etfs
    return item


def write_benchmark_diagnostics(scene):
    """诊断文件不进入邮件图片扫描；不把运行信息塞入业务缓存。"""
    from tools.paths import OUTPUT_DIR
    from tools.runtime_stats import snapshot_market_events, summarize_market_events
    events = [item for item in snapshot_market_events() if item["action"].startswith("benchmark_")]
    path = OUTPUT_DIR/"diagnostics"/f"{scene}_benchmark_latest.json"
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({"generated_at_bj":_now().isoformat(), "events":events,
                                  "summary":summarize_market_events(events)},ensure_ascii=False,indent=2),encoding="utf-8")
    except OSError as exc:
        print(f"[WARN] 基准诊断写入失败: {exc}",flush=True)
