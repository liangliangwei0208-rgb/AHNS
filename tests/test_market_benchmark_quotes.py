"""基准源离线验收：失败有界、日期可信、富途不替换真实指数。"""
import importlib
import json
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 29, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
SESSIONS = ["2026-10-26", "2026-10-27", "2026-10-28"]


def module():
    return importlib.import_module("tools.market_benchmark_quotes")


@pytest.fixture
def quotes():
    q = module()
    folder = Path(tempfile.mkdtemp(prefix="ahns-benchmark-"))
    with patch.object(q, "LOCAL_DIR", folder), patch.object(q, "completed_sessions", return_value=SESSIONS), patch.object(q, "legacy_vix_closes", return_value=[]):
        yield q


def live(**changes):
    return {"value": 18.25, "trade_date": "2026-10-29", "quote_time_bj": NOW.isoformat(), **changes}


def close(day="2026-10-28", **changes):
    return {"value": 18.0, "trade_date": day, **changes}


def test_shared_module_is_available():
    assert callable(module().get_vix_quote)


def test_live_success_is_reused_across_callers(quotes):
    with patch.object(quotes, "run_source", return_value=live()) as fetch:
        first = quotes.get_vix_quote(now=NOW)
        second = quotes.get_vix_quote(now=NOW + timedelta(minutes=4))
    assert first["value"] == second["value"] == 18.25
    assert second["cache_hit"] == "file"
    assert fetch.call_count == 1
    assert first["return_pct"] is None


@pytest.mark.parametrize("status", [403, 429])
def test_yahoo_rejection_skips_same_family_and_cools_down(quotes, status):
    calls = []
    def fetch(source, **kwargs):
        calls.append(source)
        if source.startswith("yahoo"):
            raise quotes.SourceError("blocked", http_status=status, retry_after="1800")
        return close()
    with patch.object(quotes, "run_source", side_effect=fetch):
        item = quotes.get_vix_quote(now=NOW)
        quotes.get_vix_quote(now=NOW + timedelta(minutes=6))
    assert calls == ["yahoo1", "cboe"]
    assert item["quote_kind"] == "close"
    assert "收盘" in item["label_suffix"]


def test_all_failed_sources_are_not_repeated(quotes):
    calls = []
    def fetch(source, **kwargs):
        calls.append(source)
        raise quotes.SourceError("timeout")
    with patch.object(quotes, "run_source", side_effect=fetch):
        with pytest.raises(RuntimeError):
            quotes.get_vix_quote(now=NOW)
    assert calls == ["yahoo1", "yahoo2", "cboe", "fred"]


@pytest.mark.parametrize("day,allowed", [("2026-10-28", True), ("2026-10-27", True), ("2026-10-26", False), ("2026-10-29", False)])
def test_close_cache_has_one_session_limit(quotes, day, allowed):
    with patch.object(quotes, "legacy_vix_closes", return_value=[close(day)]), patch.object(quotes, "run_source", side_effect=quotes.SourceError("offline")):
        if allowed:
            item = quotes.get_vix_quote(now=NOW)
            assert item["trade_date"] == day
            assert item["quote_kind"] == "close_cache"
            assert item["label_suffix"] == "收盘缓存"
        else:
            with pytest.raises(RuntimeError):
                quotes.get_vix_quote(now=NOW)


@pytest.mark.parametrize("bad", [live(value=0), live(value=float("nan")), live(value=float("inf")), live(quote_time_bj=""), live(trade_date="2026-10-28"), live(quote_time_bj=(NOW+timedelta(minutes=3)).isoformat()), live(quote_time_bj=(NOW-timedelta(minutes=6)).isoformat())])
def test_invalid_realtime_data_uses_close_without_fake_realtime(quotes, bad):
    def fetch(source, **kwargs):
        return bad if source.startswith("yahoo") else close()
    with patch.object(quotes, "run_source", side_effect=fetch):
        item = quotes.get_vix_quote(now=NOW)
    assert item["value"] == 18.0
    assert item["quote_kind"] == "close"
    assert item["trade_date"] == "2026-10-28"


def test_failed_refresh_preserves_last_valid_close(quotes):
    with patch.object(quotes, "run_source", return_value=close()):
        quotes.get_vix_quote(realtime=False, now=NOW)
    with patch.object(quotes, "run_source", side_effect=quotes.SourceError("offline")):
        item = quotes.get_vix_quote(now=NOW+timedelta(minutes=6))
    assert item["value"] == 18.0
    assert json.loads((quotes.LOCAL_DIR/"state.json").read_text(encoding="utf-8"))["closes"]["2026-10-28"]["value"] == 18.0


@pytest.mark.parametrize("at,expected", [("2026-10-26T08:00:00+08:00", "2026-10-23"), ("2027-01-04T08:00:00+08:00", "2026-12-31"), ("2026-12-26T08:00:00+08:00", "2026-12-24")])
def test_completed_sessions_respect_weekends_holidays_year(at, expected):
    assert module().completed_sessions(datetime.fromisoformat(at))[-1] == expected


def test_hanging_worker_is_terminated():
    import sys
    q = module()
    started = time.monotonic()
    with pytest.raises(q.SourceError, match="timeout"):
        q.run_worker([sys.executable, "-c", "import time;time.sleep(10)"], timeout=0.2)
    assert time.monotonic()-started < 2


def test_http_worker_does_not_import_business_estimator():
    import sys
    q = module()
    payload = q.run_worker([sys.executable, "-c", "import json;print(json.dumps({'value':18}))"], timeout=2)
    assert payload["value"] == 18


def test_diagnostics_record_failures_and_cache_hits(quotes):
    from tools.runtime_stats import snapshot_market_events, summarize_market_events
    before = len(snapshot_market_events())
    def fetch(source, **kwargs):
        if source.startswith("yahoo"):
            raise quotes.SourceError("blocked", http_status=403)
        return close()
    with patch.object(quotes, "run_source", side_effect=fetch):
        quotes.get_vix_quote(now=NOW)
        quotes.get_vix_quote(now=NOW+timedelta(minutes=1))
    stats = summarize_market_events(snapshot_market_events()[before:])
    assert stats["failure_count"] == 1
    assert stats["network_attempts"] == 2
    assert stats["cache_hits"] >= 1


def test_persisted_close_cannot_lie_about_its_date(quotes):
    (quotes.LOCAL_DIR/"state.json").write_text(json.dumps({"closes":{"2026-10-28":close("2026-09-01")}}),encoding="utf-8")
    with patch.object(quotes,"run_source",side_effect=quotes.SourceError("offline")):
        with pytest.raises(RuntimeError):
            quotes.get_vix_quote(realtime=False,now=NOW)


def test_entire_failed_refresh_obeys_budget_and_no_extra_worker(quotes):
    import sys
    calls=[]
    def blocked(source,timeout,**kwargs):
        calls.append(source)
        return quotes.run_worker([sys.executable,"-c","import time;time.sleep(10)"],timeout=timeout)
    start=time.monotonic()
    with patch.object(quotes,"VIX_REFRESH_BUDGET_SECONDS",0.25),patch.object(quotes,"run_source",side_effect=blocked):
        with pytest.raises(RuntimeError):
            quotes.get_vix_quote(now=NOW)
    assert time.monotonic()-start<2
    assert len(calls)==1


def test_close_only_refresh_cannot_suppress_first_realtime_lookup(quotes):
    calls=[]
    def fetch(source,**kwargs):
        calls.append(source)
        return live() if source.startswith("yahoo") else close()
    with patch.object(quotes,"run_source",side_effect=fetch):
        quotes.get_vix_quote(realtime=False,now=NOW)
        item=quotes.get_vix_quote(now=NOW+timedelta(seconds=30))
    assert calls==["cboe","yahoo1"]
    assert item["quote_kind"]=="realtime"


def test_invalid_persisted_live_does_not_block_valid_close_fallback(quotes):
    (quotes.LOCAL_DIR/"state.json").write_text(json.dumps({"realtime":live(value=float("nan")),"closes":{"2026-10-28":close()}}),encoding="utf-8")
    with patch.object(quotes,"run_source",side_effect=quotes.SourceError("offline")):
        item=quotes.get_vix_quote(now=NOW)
    assert item["value"]==18
