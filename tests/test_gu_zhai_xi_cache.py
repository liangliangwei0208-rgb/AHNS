"""股债利差的缓存优先与行情源回退测试。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest
import json
import os
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from strategy import gu_zhai_xi


NOW = datetime(2026, 9, 30, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
DATES = pd.to_datetime(["2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29"])


def _write_csv(path, dates, column, values):
    pd.DataFrame({"date": dates, column: values}).to_csv(path, index=False)


def _store(tmp_path, dates=DATES, now=NOW):
    return gu_zhai_xi.MarketDataCache(tmp_path, dates, now=now)


class GuZhaiXiCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ahns-gu-zhai-xi-")
        self.addCleanup(self.temp.cleanup)
        self.cache_dir = Path(self.temp.name)

    def test_complete_cache_skips_every_source_and_intraday_row(self):
        _write_csv(self.cache_dir / "sz399001_daily.csv", list(DATES) + [pd.Timestamp("2026-09-30")], "close", [10, 11, 12, 13, 999])
        _write_csv(self.cache_dir / "gu_zhai_xi_pe.csv", DATES, "pe_ttm", [20, 20, 21, 21])
        _write_csv(self.cache_dir / "gu_zhai_xi_cn10y.csv", DATES, "cn10y", [2.0, 2.0, 2.1, 2.1])
        cls = gu_zhai_xi.MarketDataCache
        with patch.object(cls, "_fetch_index_futu", side_effect=AssertionError("Futu called")), patch.object(cls, "_fetch_pe_full", side_effect=AssertionError("PE called")), patch.object(cls, "_fetch_bond_range", side_effect=AssertionError("bond called")):
            with _store(self.cache_dir) as store:
                index, pe, bond = store.load_all()
        self.assertEqual(index["date"].max(), pd.Timestamp("2026-09-29"))
        self.assertEqual(index.iloc[-1]["index_close"], 13)
        self.assertEqual((len(pe), len(bond)), (4, 4))

    def test_futu_first_then_other_sources_fill_only_missing_dates(self):
        _write_csv(self.cache_dir / "sz399001_daily.csv", DATES[:2], "close", [10, 11])
        calls = []
        def source(name, result):
            def fetch(_self, start, end):
                calls.append((name, start, end))
                return result
            return fetch
        empty = pd.DataFrame(columns=["date", "index_close"])
        futu = pd.DataFrame({"date": [DATES[2]], "index_close": [12.0]})
        sina = pd.DataFrame({"date": [DATES[3]], "index_close": [13.0]})
        cls = gu_zhai_xi.MarketDataCache
        with patch.object(cls, "_fetch_index_futu", source("futu", futu)), patch.object(cls, "_fetch_index_tencent", source("tencent", empty)), patch.object(cls, "_fetch_index_eastmoney", source("eastmoney", empty)), patch.object(cls, "_fetch_index_sina", source("sina", sina)):
            with _store(self.cache_dir) as store:
                result = store.load_index()
        self.assertEqual(result["index_close"].tolist(), [10, 11, 12, 13])
        self.assertEqual([row[0] for row in calls], ["futu", "tencent", "eastmoney", "sina"])
        self.assertTrue(all(start >= pd.Timestamp("2026-09-28") for _, start, _ in calls))
        self.assertEqual(pd.read_csv(self.cache_dir / "sz399001_daily.csv")["close"].tolist(), [10, 11, 12, 13])

    def test_pe_full_response_only_on_missing_date_and_preserves_field(self):
        calls = []
        full = pd.DataFrame({"date": DATES, "averagePETTM": [20, 21, 22, 23], "middlePETTM": [30, 31, 32, 33]})
        def get_full(_self):
            calls.append(1)
            return full
        with patch.object(gu_zhai_xi.MarketDataCache, "_fetch_pe_full", get_full):
            with _store(self.cache_dir) as store:
                first = store.load_pe()
            with _store(self.cache_dir) as store:
                second = store.load_pe()
        self.assertEqual(calls, [1])
        self.assertEqual(first["pe_ttm"].tolist(), second["pe_ttm"].tolist())
        self.assertEqual(first["pe_ttm"].tolist(), [20, 21, 22, 23])

    def test_bond_range_backfills_and_failed_range_resumes(self):
        calls = []
        def bond(_self, start, end):
            calls.append((start, end))
            if start <= DATES[1] <= end and len(calls) == 2:
                raise TimeoutError("temporary")
            included = [date for date in DATES if start <= date <= end]
            return pd.DataFrame({"date": included, "cn10y": [2.0] * len(included)})
        cls = gu_zhai_xi.MarketDataCache
        with patch.object(cls, "_fetch_bond_range", bond), patch.object(cls, "MAX_BATCH_DAYS", 2):
            with _store(self.cache_dir) as store:
                first = store.load_bond()
            self.assertEqual(first["date"].tolist(), list(DATES[2:]))
            self.assertEqual(len(pd.read_csv(self.cache_dir / "gu_zhai_xi_cn10y.csv")), 2)
            after_backoff = datetime(2026, 9, 30, 15, 2, tzinfo=ZoneInfo("Asia/Shanghai"))
            with _store(self.cache_dir, now=after_backoff) as store:
                second = store.load_bond()
        self.assertEqual(second["date"].tolist(), list(DATES))
        self.assertEqual(calls[0], (DATES[2], DATES[3]))
        self.assertEqual(calls[-1], (DATES[0], DATES[1]))

    def test_missing_intraday_components_never_publish_live_spread(self):
        with _store(self.cache_dir) as store:
            self.assertIsNone(store.live_spread_if_verified(index_quote={"date": "2026-09-30", "price": 12000, "observed_at": NOW}))

    def test_pe_source_with_no_new_date_waits_one_hour(self):
        calls = []
        old = pd.DataFrame({"date": DATES[:3], "averagePETTM": [20, 21, 22]})
        def get_old(_self):
            calls.append(1)
            return old
        with patch.object(gu_zhai_xi.MarketDataCache, "_fetch_pe_full", get_old):
            with _store(self.cache_dir) as store:
                store.load_pe()
            with _store(self.cache_dir) as store:
                store.load_pe()
        self.assertEqual(calls, [1])

    def test_out_of_range_index_source_is_backed_off(self):
        _write_csv(self.cache_dir / "sz399001_daily.csv", DATES[:3], "close", [10, 11, 12])
        calls = []
        def old_futu(_self, _start, _end):
            calls.append(1)
            return pd.DataFrame({"date": [DATES[0]], "index_close": [10]})
        cls = gu_zhai_xi.MarketDataCache
        with patch.object(cls, "_fetch_index_futu", old_futu), patch.object(cls, "_fetch_index_tencent", return_value=pd.DataFrame()), patch.object(cls, "_fetch_index_eastmoney", return_value=pd.DataFrame()), patch.object(cls, "_fetch_index_sina", return_value=pd.DataFrame()):
            with _store(self.cache_dir) as store:
                store.load_index()
            with _store(self.cache_dir) as store:
                store.load_index()
        self.assertEqual(calls, [1])

    def test_sina_full_history_is_reused_across_missing_batches(self):
        calls = []
        def fetch_sina(_self, start, end):
            calls.append((start, end))
            return pd.DataFrame({"date": DATES, "index_close": [10, 11, 12, 13]})
        cls = gu_zhai_xi.MarketDataCache
        with patch.object(cls, "_fetch_index_futu", return_value=pd.DataFrame()), patch.object(cls, "_fetch_index_tencent", return_value=pd.DataFrame()), patch.object(cls, "_fetch_index_eastmoney", return_value=pd.DataFrame()), patch.object(cls, "_fetch_index_sina", fetch_sina), patch.object(cls, "MAX_BATCH_DAYS", 2):
            with _store(self.cache_dir) as store:
                result = store.load_index()
        self.assertEqual(len(result), 4)
        self.assertEqual(len(calls), 1)

    def test_sina_is_not_requested_when_futu_fills_the_gap(self):
        _write_csv(self.cache_dir / "sz399001_daily.csv", DATES[:3], "close", [10, 11, 12])
        fresh = pd.DataFrame({"date": [DATES[3]], "index_close": [13.0]})
        cls = gu_zhai_xi.MarketDataCache
        with patch.object(cls, "_fetch_index_futu", return_value=fresh), patch.object(cls, "_fetch_index_sina", side_effect=AssertionError("Sina called")):
            with _store(self.cache_dir) as store:
                result = store.load_index()
        self.assertEqual(result.iloc[-1]["index_close"], 13.0)

    def test_sina_adapter_uses_akshare_decoder_and_index_symbol(self):
        calls = []
        class Response:
            text = 'var hq_str="encoded-history";'
            def raise_for_status(self):
                pass
        def get(url, *, params, timeout):
            calls.append((url, params, timeout))
            return Response()
        class Decoder:
            def eval(self, code):
                calls.append(("script", isinstance(code, str)))
            def call(self, name, payload):
                calls.append((name, payload))
                return [{"date": "2026-09-29", "close": "12888.25"}]
        decoder = Decoder()
        with patch("strategy.gu_zhai_xi_data.requests.get", get), patch("py_mini_racer.MiniRacer", return_value=decoder):
            with _store(self.cache_dir) as store:
                result = store._fetch_index_sina(DATES[3], DATES[3])
        self.assertEqual(calls[0][0], "https://finance.sina.com.cn/realstock/company/sz399001/hisdata/klc_kl.js")
        self.assertEqual(calls[0][1], {"d": "2020_2_4"})
        self.assertEqual(calls[0][2], 8)
        self.assertEqual(calls[1], ("script", True))
        self.assertEqual(calls[2], ("d", "encoded-history"))
        self.assertEqual(result.iloc[0]["index_close"], "12888.25")

    def test_live_spread_requires_matching_fresh_sources(self):
        quotes = {
            "index_quote": {"value": 12000.0, "observed_at": NOW, "basis": "sz399001"},
            "pe_quote": {"value": 20.0, "observed_at": NOW, "basis": "all_a_equal_weight_pe_ttm"},
            "bond_quote": {"value": 2.0, "observed_at": NOW, "basis": "cn10y_yield_pct"},
        }
        with _store(self.cache_dir) as store:
            live = store.live_spread_if_verified(**quotes)
            self.assertEqual(live["spread"], 3.0)
            self.assertEqual(live["status"], "盘中估算")
            quotes["pe_quote"]["observed_at"] = datetime(2026, 9, 30, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
            self.assertIsNone(store.live_spread_if_verified(**quotes))

    def test_market_close_buffer_excludes_today_until_ready(self):
        early = gu_zhai_xi.required_trade_dates(1, 20, now=NOW)
        late = gu_zhai_xi.required_trade_dates(1, 20, now=datetime(2026, 9, 30, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
        self.assertEqual(early[-1], pd.Timestamp("2026-09-29"))
        self.assertEqual(late[-1], pd.Timestamp("2026-09-30"))

    def test_individual_pe_reader_does_not_fetch_other_series(self):
        class FakeStore:
            def __init__(self, *_args, **_kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                pass
            def load_pe(self):
                return pd.DataFrame({"date": DATES, "pe_ttm": [20, 21, 22, 23]})
            def load_index(self):
                raise AssertionError("index requested")
            def load_bond(self):
                raise AssertionError("bond requested")
        with patch.object(gu_zhai_xi, "required_trade_dates", return_value=DATES), patch.object(gu_zhai_xi, "MarketDataCache", FakeStore):
            self.assertEqual(len(gu_zhai_xi.fetch_all_a_pe()), 4)

    def test_invalid_cache_can_refresh_from_valid_source(self):
        (self.cache_dir / "sz399001_daily.csv").write_text("broken_column\nwrong\n", encoding="utf-8")
        frame = pd.DataFrame({"date": DATES, "index_close": [10, 11, 12, 13]})
        with patch.object(gu_zhai_xi.MarketDataCache, "_fetch_index_futu", return_value=frame):
            with _store(self.cache_dir) as store:
                result = store.load_index()
        self.assertEqual(result["index_close"].tolist(), [10, 11, 12, 13])

    def test_same_day_cached_price_is_verified_once_after_close_buffer(self):
        today = pd.Timestamp("2026-09-30")
        dates = DATES.append(pd.DatetimeIndex([today]))
        after_close = datetime(2026, 9, 30, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        index_file = self.cache_dir / "sz399001_daily.csv"
        _write_csv(index_file, dates, "close", [10, 11, 12, 13, 999])
        intraday = datetime(2026, 9, 30, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        os.utime(index_file, (intraday, intraday))
        calls = []
        def final_day(_self, start, end):
            calls.append((start, end))
            return pd.DataFrame({"date": [today], "index_close": [14.0]})
        with patch.object(gu_zhai_xi.MarketDataCache, "_fetch_index_futu", final_day):
            with _store(self.cache_dir, dates=dates, now=after_close) as store:
                first = store.load_index()
            with _store(self.cache_dir, dates=dates, now=after_close) as store:
                second = store.load_index()
        self.assertEqual(first.iloc[-1]["index_close"], 14)
        self.assertEqual(second.iloc[-1]["index_close"], 14)
        self.assertEqual(calls, [(today, today)])

    def test_unverified_same_day_price_is_not_exposed_when_sources_fail(self):
        today = pd.Timestamp("2026-09-30")
        dates = DATES.append(pd.DatetimeIndex([today]))
        after_close = datetime(2026, 9, 30, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        index_file = self.cache_dir / "sz399001_daily.csv"
        _write_csv(index_file, dates, "close", [10, 11, 12, 13, 999])
        intraday = datetime(2026, 9, 30, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        os.utime(index_file, (intraday, intraday))
        cls = gu_zhai_xi.MarketDataCache
        with patch.object(cls, "_fetch_index_futu", return_value=pd.DataFrame()), patch.object(cls, "_fetch_index_tencent", return_value=pd.DataFrame()), patch.object(cls, "_fetch_index_eastmoney", return_value=pd.DataFrame()), patch.object(cls, "_fetch_index_sina", return_value=pd.DataFrame()):
            with _store(self.cache_dir, dates=dates, now=after_close) as store:
                result = store.load_index()
        self.assertEqual(result["date"].max(), pd.Timestamp("2026-09-29"))
        self.assertEqual(pd.read_csv(self.cache_dir / "sz399001_daily.csv")["close"].iloc[-1], 999)

    def test_same_day_cache_written_after_close_buffer_stays_cache_first(self):
        today = pd.Timestamp("2026-09-30")
        dates = DATES.append(pd.DatetimeIndex([today]))
        late = datetime(2026, 9, 30, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        index_file = self.cache_dir / "sz399001_daily.csv"
        _write_csv(index_file, dates, "close", [10, 11, 12, 13, 14])
        postclose = datetime(2026, 9, 30, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        os.utime(index_file, (postclose, postclose))
        cls = gu_zhai_xi.MarketDataCache
        with patch.object(cls, "_fetch_index_futu", side_effect=AssertionError("unexpected request")), patch.object(cls, "_fetch_index_tencent", side_effect=AssertionError("unexpected request")), patch.object(cls, "_fetch_index_eastmoney", side_effect=AssertionError("unexpected request")), patch.object(cls, "_fetch_index_sina", side_effect=AssertionError("unexpected request")):
            with _store(self.cache_dir, dates=dates, now=late) as store:
                result = store.load_index()
        self.assertEqual(result.iloc[-1]["index_close"], 14)

    def test_intraday_seen_row_remains_pending_after_later_file_touch(self):
        today = pd.Timestamp("2026-09-30")
        dates = DATES.append(pd.DatetimeIndex([today]))
        index_file = self.cache_dir / "sz399001_daily.csv"
        _write_csv(index_file, dates, "close", [10, 11, 12, 13, 999])
        early = datetime(2026, 9, 30, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        late = datetime(2026, 9, 30, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with _store(self.cache_dir, dates=DATES, now=early) as store:
            store.load_index()
        postclose = datetime(2026, 9, 30, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        os.utime(index_file, (postclose, postclose))
        calls = []
        def final_day(_self, start, end):
            calls.append((start, end))
            return pd.DataFrame({"date": [today], "index_close": [14.0]})
        with patch.object(gu_zhai_xi.MarketDataCache, "_fetch_index_futu", final_day):
            with _store(self.cache_dir, dates=dates, now=late) as store:
                result = store.load_index()
        self.assertEqual(result.iloc[-1]["index_close"], 14)
        self.assertEqual(calls, [(today, today)])

    def test_pending_row_is_verified_on_following_holiday(self):
        today = pd.Timestamp("2026-09-30")
        dates = DATES.append(pd.DatetimeIndex([today]))
        index_file = self.cache_dir / "sz399001_daily.csv"
        _write_csv(index_file, dates, "close", [10, 11, 12, 13, 999])
        early = datetime(2026, 9, 30, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        holiday = datetime(2026, 10, 1, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with _store(self.cache_dir, dates=DATES, now=early) as store:
            store.load_index()
        calls = []
        def final_day(_self, start, end):
            calls.append((start, end))
            return pd.DataFrame({"date": [today], "index_close": [14.0]})
        with patch.object(gu_zhai_xi.MarketDataCache, "_fetch_index_futu", final_day):
            with _store(self.cache_dir, dates=dates, now=holiday) as store:
                first = store.load_index()
            with _store(self.cache_dir, dates=dates, now=holiday) as store:
                second = store.load_index()
        self.assertEqual(first.iloc[-1]["index_close"], 14)
        self.assertEqual(second.iloc[-1]["index_close"], 14)
        self.assertEqual(calls, [(today, today)])

    def test_tencent_adapter_requests_only_recent_count(self):
        calls = []
        class Response:
            text = 'kline_dayqfq=' + json.dumps({"data": {"sz399001": {"day": [["2026-09-28", "10", "12", "13", "9", "100"], ["2026-09-29", "12", "13", "14", "11", "200"]]}}}) + ';'
            def raise_for_status(self):
                pass
        def get(_url, *, params, timeout):
            calls.append((params, timeout))
            return Response()
        with patch("strategy.gu_zhai_xi_data.requests.get", get):
            with _store(self.cache_dir) as store:
                result = store._fetch_index_tencent(DATES[2], DATES[3])
        self.assertEqual(result["index_close"].tolist(), ["12", "13"])
        self.assertEqual(calls[0][0]["param"], "sz399001,day,,2026-09-29,12,qfq")
        self.assertEqual(calls[0][1], 8)

    def test_bond_adapter_uses_date_filter_and_pagination(self):
        calls = []
        class Response:
            def __init__(self, page):
                self.page = page
            def raise_for_status(self):
                pass
            def json(self):
                return {"success": True, "result": {"pages": 2, "data": [{"SOLAR_DATE": f"2026-09-{28 + self.page}", "EMM00166466": 1.6 + self.page / 100}]}}
        def get(_url, *, params, timeout):
            calls.append((params, timeout))
            return Response(params["p"])
        with patch("strategy.gu_zhai_xi_data.requests.get", get):
            with _store(self.cache_dir) as store:
                result = store._fetch_bond_range(DATES[2], pd.Timestamp("2026-09-30"))
        self.assertEqual(len(result), 2)
        self.assertEqual([item[0]["p"] for item in calls], [1, 2])
        self.assertIn("SOLAR_DATE>='2026-09-28'", calls[0][0]["filter"])

    def test_pe_column_priority_in_chart_config_remains_effective(self):
        full = pd.DataFrame({"date": DATES, "averagePETTM": [20, 21, 22, 23], "middlePETTM": [30, 31, 32, 33]})
        with patch.object(gu_zhai_xi, "PE_COLUMN_CANDIDATES", ["middlePETTM", "averagePETTM"]), patch.object(gu_zhai_xi, "required_trade_dates", return_value=DATES), patch.object(gu_zhai_xi.MarketDataCache, "_fetch_pe_full", return_value=full), patch.object(gu_zhai_xi.Path, "resolve", return_value=self.cache_dir / "strategy" / "gu_zhai_xi.py"):
            result = gu_zhai_xi.fetch_all_a_pe()
        self.assertEqual(result["pe_ttm"].tolist(), [30, 31, 32, 33])

    def test_spread_and_rolling_channel_formula_unchanged(self):
        dates = pd.bdate_range("2025-01-01", periods=300)
        index = pd.DataFrame({"date": dates, "index_close": range(100, 400)})
        pe = pd.DataFrame({"date": dates, "pe_ttm": [20.0] * 300})
        bond = pd.DataFrame({"date": dates, "cn10y": [2.0] * 300})
        with patch.object(gu_zhai_xi, "_load_chart_sources", return_value=(index, pe, bond)):
            result = gu_zhai_xi.build_indicator(years=1, window=250, sigma_mult=1.25)
        self.assertAlmostEqual(result.iloc[-1]["spread"], 3.0)
        self.assertAlmostEqual(result.iloc[-1]["mean"], 3.0)
        self.assertAlmostEqual(result.iloc[-1]["upper"], 3.0)
        self.assertAlmostEqual(result.iloc[-1]["lower"], 3.0)


if __name__ == "__main__":
    unittest.main()
