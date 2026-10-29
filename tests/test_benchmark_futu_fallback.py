"""富途仅作 Service 同标的兜底，日期与时段错误必须拒绝。"""
import importlib
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

q = importlib.import_module("tools.market_benchmark_quotes")
NOW = datetime.fromisoformat("2026-10-29T22:45:00+08:00")
SESSIONS = ["2026-10-26", "2026-10-27", "2026-10-28"]


@pytest.fixture
def service():
    with patch.dict("os.environ", {q.BENCHMARK_FUTU_ENV: "1"}), patch.object(q, "futu_available", return_value=True), patch.object(q, "LOCAL_DIR", Path(tempfile.mkdtemp(prefix="ahns-etf-"))), patch.object(q, "completed_sessions", return_value=SESSIONS):
        yield


def test_nonservice_never_connects_to_opend():
    with patch.dict("os.environ", {q.BENCHMARK_FUTU_ENV: "0"}), patch.object(q, "run_source") as worker:
        with pytest.raises(q.SourceError):
            q.get_futu_etf_quote("XOP", mode="daily", target_date="2026-10-28", now=NOW)
    worker.assert_not_called()


@pytest.mark.parametrize("symbol", ["VIX", ".NDX", ".INX", ".SOX", "XAU", "US.VIX"])
def test_futu_cannot_replace_indices_or_spot_gold(service, symbol):
    with patch.object(q, "run_source") as worker:
        with pytest.raises(q.SourceError):
            q.get_futu_etf_quote(symbol, mode="daily", target_date="2026-10-28", now=NOW)
    worker.assert_not_called()


def test_xop_uses_exact_two_completed_daily_closes(service):
    payload = {"rows": [{"time_key": "2026-10-27 00:00:00", "close": 100}, {"time_key": "2026-10-28 00:00:00", "close": 102}, {"time_key": "2026-10-29 00:00:00", "close": 110}]}
    with patch.object(q, "run_source", return_value=payload) as worker:
        result = q.get_futu_etf_quote("XOP", mode="daily", target_date="2026-10-28", now=NOW)
    assert result["return_pct"] == pytest.approx(2)
    assert result["trade_date"] == "2026-10-28"
    assert worker.call_args.kwargs["start"] == "2026-10-27"


@pytest.mark.parametrize("rows", [[{"time_key": "2026-10-28", "close": 102}], [{"time_key": "2026-10-26", "close": 100}, {"time_key": "2026-10-28", "close": 102}], [{"time_key": "2026-10-27", "close": 0}, {"time_key": "2026-10-28", "close": 102}]])
def test_incomplete_daily_pair_is_rejected(service, rows):
    with patch.object(q, "run_source", return_value={"rows": rows}):
        with pytest.raises(q.SourceError):
            q.get_futu_etf_quote("XOP", mode="daily", target_date="2026-10-28", now=NOW)


def test_current_day_not_yet_complete_is_rejected(service):
    with patch.object(q, "run_source") as worker:
        with pytest.raises(q.SourceError):
            q.get_futu_etf_quote("XOP", mode="daily", target_date="2026-10-29", now=NOW)
    worker.assert_not_called()


@pytest.mark.parametrize("mode,at,stamp,fields,pct", [
    ("premarket", "2026-10-29T20:00:00+08:00", "2026-10-29 07:59:00", {"pre_price": 102,"prev_close_price": 100},2),
    ("intraday", "2026-10-29T22:45:00+08:00", "2026-10-29 10:44:00", {"last_price": 101,"prev_close_price": 100},1),
    ("afterhours", "2026-10-30T08:45:00+08:00", "2026-10-29 19:59:00", {"after_price": 103,"after_change_val": 1,"prev_close_price": 100},100/102),
])
def test_snapshot_uses_matching_phase_and_reference(service, mode, at, stamp, fields, pct):
    with patch.object(q, "run_source", return_value={"snapshot":{"update_time":stamp,**fields}}):
        result=q.get_futu_etf_quote("QQQ", mode=mode,target_date="2026-10-29",now=datetime.fromisoformat(at))
    assert result["return_pct"] == pytest.approx(pct)
    assert mode in result["source"]


@pytest.mark.parametrize("stamp", ["2026-10-28 10:44:00","2026-10-29 08:00:00","2026-10-29 16:01:00","2026-10-29 10:49:00", ""])
def test_wrong_day_phase_or_future_snapshot_is_rejected(service,stamp):
    with patch.object(q,"run_source",return_value={"snapshot":{"update_time":stamp,"last_price":101,"prev_close_price":100}}):
        with pytest.raises(q.SourceError):
            q.get_futu_etf_quote("QQQ",mode="intraday",target_date="2026-10-29",now=NOW)


def test_sdk_missing_skips_worker(service):
    with patch.object(q,"futu_available",return_value=False),patch.object(q,"run_source") as worker:
        with pytest.raises(q.SourceError):
            q.get_futu_etf_quote("XOP",mode="daily",target_date="2026-10-28",now=NOW)
    worker.assert_not_called()


def test_failure_does_not_replace_good_etf_cache(service):
    payload={"snapshot":{"update_time":"2026-10-29 10:44:00","last_price":101,"prev_close_price":100}}
    with patch.object(q,"run_source",return_value=payload):
        q.get_futu_etf_quote("QQQ",mode="intraday",target_date="2026-10-29",now=NOW)
    before=(q.LOCAL_DIR/"state.json").read_text()
    with patch.object(q,"run_source",side_effect=q.SourceError("permission denied")):
        with pytest.raises(q.SourceError):
            q.get_futu_etf_quote("QQQ",mode="intraday",target_date="2026-10-29",now=NOW+timedelta(minutes=6))
    assert (q.LOCAL_DIR/"state.json").read_text()==before


def test_existing_realtime_adapter_does_not_repeat_close_fallback():
    from tools import premarket_estimator as p
    with patch.object(p,"_fetch_realtime_vix_level",side_effect=RuntimeError("offline")),patch.object(p,"fetch_latest_complete_vix_close") as old:
        item=p.fetch_premarket_benchmark_quote("vix",today="2026-10-29",quote_cache={},disabled_sources=set(),persistent_quote_cache={},cache_now=NOW)
    old.assert_not_called()
    assert item["status"]=="missing"


def test_daily_adapter_does_not_keep_unvalidated_old_vix():
    import safe_fund
    old={"kind":"vix_level","symbol":"VIX","value":11,"trade_date":"2026-09-01","source":"CBOE"}
    with patch.object(safe_fund,"_fetch_realtime_vix_level",side_effect=RuntimeError("too old")):
        result=safe_fund.refresh_vix_footer_item_for_daily(old,valuation_date="2026-10-28")
    assert result["value"] is None
    assert result["trade_date"] == ""


@pytest.mark.parametrize("session_name", ["PREMARKET_SESSION", "AFTERHOURS_SESSION", "INTRADAY_SESSION"])
def test_observation_footer_keeps_real_vix_date_and_cache_label(session_name):
    from tools import premarket_estimator as p
    item={"value":18,"value_type":"level","trade_date":"2026-10-27","label_suffix":"收盘缓存","quote_kind":"close_cache"}
    with patch.object(p,"fetch_premarket_benchmark_quote",return_value=item):
        rows=p.build_premarket_benchmark_footer_items(today="2026-10-29",quote_cache={},disabled_sources=set(),cache_now=NOW,session=getattr(p,session_name))
    row=next(r for r in rows if r["label"].startswith("VIX"))
    assert row["trade_date"]=="2026-10-27"
    assert "收盘缓存" in row["label"]


def test_xop_daily_adapter_reaches_futu_when_primary_is_missing(service):
    from tools import get_top10_holdings as g
    with patch.object(g,"get_security_return_by_anchor_date",return_value={"status":"missing","return_pct":None}),patch.object(q,"get_futu_etf_quote",return_value={"return_pct":2,"source":"futu_benchmark_daily_unadjusted","trade_date":"2026-10-28","status":"traded"}) as fetch:
        item=g._fetch_configured_market_benchmark({"kind":"us_security","symbol":"XOP","label":"油气开采"},cache_enabled=False,valuation_anchor_date="2026-10-28")
    assert item["return_pct"]==2
    assert fetch.call_count==1


def test_night_vix_does_not_repeat_failed_close_source():
    from tools import futu_night_observation as n
    with patch.object(n,"_fetch_realtime_vix_level",side_effect=RuntimeError("offline")),patch.object(n,"fetch_latest_complete_vix_close") as old:
        from unittest.mock import Mock
        item=n._fetch_vix_footer_item(target_date="2026-10-29",generated_at=NOW,return_cache=Mock())
    old.assert_not_called()
    assert item["value"] is None
    assert item["trade_date"]==""


@pytest.mark.parametrize("bad", [
    {"trade_date":"2026-10-28"}, {"return_pct":float("inf")},
    {"quote_time_bj":"2026-10-29T20:00:00+08:00"}, {"source":"futu_benchmark_afterhours_post"},
])
def test_corrupt_persisted_etf_is_not_reused(service,bad):
    import json
    cached={"ticker":"QQQ","mode":"intraday","return_pct":1.0,"trade_date":"2026-10-29","quote_time_bj":NOW.isoformat(),
            "fetched_at_bj":NOW.isoformat(),"source":"futu_benchmark_intraday_regular","status":"traded",**bad}
    (q.LOCAL_DIR/"state.json").write_text(json.dumps({"etfs":{"QQQ:intraday:2026-10-29":cached}}),encoding="utf-8")
    with patch.object(q,"run_source",side_effect=q.SourceError("offline")) as worker:
        with pytest.raises(q.SourceError):
            q.get_futu_etf_quote("QQQ",mode="intraday",target_date="2026-10-29",now=NOW)
    worker.assert_called_once()


def test_etf_cache_rechecks_original_quote_age(service):
    payload={"snapshot":{"update_time":"2026-10-29 10:31:00","last_price":101,"prev_close_price":100}}
    with patch.object(q,"run_source",return_value=payload):
        q.get_futu_etf_quote("QQQ",mode="intraday",target_date="2026-10-29",now=NOW)
    with patch.object(q,"run_source",side_effect=q.SourceError("offline")) as worker:
        with pytest.raises(q.SourceError):
            q.get_futu_etf_quote("QQQ",mode="intraday",target_date="2026-10-29",now=NOW+timedelta(minutes=4))
    worker.assert_called_once()
