"""绘图专用广度选择：正式自算结果始终保持独立。"""
import tempfile
import unittest
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import market_breadth
from tools.breadth_engine import chart_data, market_clock, refresh_market
from tools.configs.market_breadth_configs import BREADTH_MARKETS, BREADTH_DIRECT_INDICATOR_SOURCES
from tools.market_breadth import BreadthStore


class ChartSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = BreadthStore(Path(self.tmp.name))

    def test_ndx_backcast_uses_initial_members_without_changing_published_results(self):
        now = pd.Timestamp("2026-10-02 10:00", tz="America/New_York")
        sessions = market_clock("US", now)["sessions"][-65:]
        pre_start = sessions[-54]
        self.store.save_members("nasdaq100", ["US.A", "US.B"], "official", "2026-09-29")
        self.store.save_prices("US.A", pd.DataFrame({"date": sessions, "close": [10.] * len(sessions)}), "fixture")
        self.store.save_prices("US.B", pd.DataFrame({"date": sessions, "close": [20.] * len(sessions)}), "fixture")
        self.store.save_prices("US.C", pd.DataFrame({"date": sessions, "close": [30.] * len(sessions)}), "fixture")
        self.store.save_results("nasdaq100", [{"date": "2026-09-29", "percent": 42., "kind": "close"}], "first")
        original = self.store.read("results", "nasdaq100")
        self.store.save_members("nasdaq100", ["US.A", "US.C"], "official", "2026-10-01")
        chart_dates = pd.to_datetime([pre_start, "2026-09-28", "2026-09-29"])
        with patch("tools.breadth_engine.market_clock", return_value=market_clock("US", now)):
            frame = chart_data("nasdaq100", chart_dates, self.store.root)
        by_day = frame.set_index("date")
        self.assertEqual(by_day.loc["2026-09-28", "percent"], 0.)
        self.assertEqual(by_day.loc["2026-09-28", "membership_policy"], "current_members_backcast")
        self.assertEqual(by_day.loc["2026-09-29", "percent"], 42.)
        self.assertEqual(by_day.loc["2026-09-29", "membership_policy"], "point_in_time_membership")
        self.assertEqual(self.store.read("results", "nasdaq100"), original)
        self.assertEqual(frame.attrs["breadth_display"]["backcast_end"], "2026-09-28")

    def test_direct_indicator_has_chart_priority_for_all_five_markets(self):
        day = "2026-09-29"
        for key in ("nasdaq100", "dow", "dividend", "csi2000", "shenzhen"):
            with self.subTest(key=key), patch.dict(BREADTH_DIRECT_INDICATOR_SOURCES, {
                key: {"source_id": "licensed_fixture", "universe": BREADTH_MARKETS[key]["universe"],
                      "access_approved": True}
            }, clear=True):
                store = BreadthStore(self.store.root / key)
                store.save_results(key, [{"date": day, "percent": 40., "kind": "close"}], "v1")
                store.write("direct_indicators", key, {
                    "type": "direct_indicators", "source_id": "licensed_fixture",
                    "universe": BREADTH_MARKETS[key]["universe"],
                    "metric": "percent_members_above_sma50",
                    "rows": [{"date": day, "percent": 60., "kind": "close", "source": "licensed_fixture"}],
                })
                clock = {"day": "2026-09-30", "complete_day": day, "regular": True,
                         "now": pd.Timestamp("2026-09-30 14:30", tz="UTC"), "sessions": [day]}
                with patch("tools.breadth_engine.market_clock", return_value=clock):
                    frame = chart_data(key, pd.to_datetime([day]), store.root)
                self.assertEqual(float(frame.iloc[-1].percent), 60.)
                self.assertEqual(frame.iloc[-1].source, "licensed_fixture")
                self.assertEqual(float(store.results(key).iloc[-1].percent), 40.)
                self.assertEqual(frame.attrs["breadth_display"]["difference_pp"], 20.)

    def test_invalid_direct_rows_fall_back_per_date_without_forward_fill(self):
        key = "dow"
        self.store.save_results(key, [
            {"date": "2026-09-28", "percent": 40., "kind": "close"},
            {"date": "2026-09-29", "percent": 45., "kind": "close"},
        ], "v1")
        doc = {"type": "direct_indicators", "source_id": "licensed_fixture", "universe": "dow30",
               "metric": "percent_members_above_sma50", "rows": [
                   {"date": "2026-09-27", "percent": 75., "kind": "close", "source": "licensed_fixture"},
                   {"date": "2026-09-28", "percent": 70., "kind": "close", "source": "licensed_fixture"},
                   {"date": "2026-09-29", "percent": 110., "kind": "close", "source": "licensed_fixture"},
               ]}
        self.store.write("direct_indicators", key, doc)
        clock = {"day": "2026-09-30", "complete_day": "2026-09-29", "regular": True,
                 "now": pd.Timestamp("2026-09-30 14:30", tz="UTC"), "sessions": ["2026-09-28", "2026-09-29"]}
        with patch.dict(BREADTH_DIRECT_INDICATOR_SOURCES, {key: {"source_id": "licensed_fixture", "universe": "dow30", "access_approved": True}}, clear=True), patch("tools.breadth_engine.market_clock", return_value=clock):
            frame = chart_data(key, pd.to_datetime(["2026-09-27", "2026-09-28", "2026-09-29"]), self.store.root)
        self.assertNotIn("2026-09-27", frame.date.tolist())
        self.assertEqual(frame.set_index("date").loc["2026-09-28", "percent"], 70.)
        self.assertEqual(frame.set_index("date").loc["2026-09-29", "percent"], 45.)
        self.assertEqual(frame.attrs["breadth_display"]["source_counts"]["licensed_fixture"], 1)

    def test_unapproved_or_stale_direct_intraday_never_overrides_self(self):
        key = "dow"
        self.store.save_results(key, [{"date": "2026-09-30", "percent": 35., "kind": "intraday",
                                      "observed_at": "2026-09-30T14:00:00+00:00"}], "v1")
        doc = {"type": "direct_indicators", "source_id": "licensed_fixture", "universe": "dow30",
               "metric": "percent_members_above_sma50", "rows": [
                   {"date": "2026-09-30", "percent": 80., "kind": "intraday",
                    "observed_at": "2026-09-30T12:00:00+00:00", "source": "licensed_fixture"},
               ]}
        self.store.write("direct_indicators", key, doc)
        clock = {"day": "2026-09-30", "complete_day": "2026-09-29", "regular": True,
                 "now": pd.Timestamp("2026-09-30T14:30:00Z"), "sessions": ["2026-09-29"]}
        with patch("tools.breadth_engine.market_clock", return_value=clock):
            self.assertEqual(chart_data(key, pd.to_datetime(["2026-09-30"]), self.store.root).iloc[-1].percent, 35.)
        with patch.dict(BREADTH_DIRECT_INDICATOR_SOURCES, {key: {"source_id": "licensed_fixture", "universe": "dow30", "access_approved": True}}, clear=True), patch("tools.breadth_engine.market_clock", return_value=clock):
            self.assertEqual(chart_data(key, pd.to_datetime(["2026-09-30"]), self.store.root).iloc[-1].percent, 35.)
            doc["rows"][0]["observed_at"] = "2026-09-30T14:20:00+00:00"
            self.store.write("direct_indicators", key, doc)
            self.assertEqual(chart_data(key, pd.to_datetime(["2026-09-30"]), self.store.root).iloc[-1].percent, 80.)

    def test_source_registration_without_access_approval_is_ignored(self):
        key = "dow"
        self.store.save_results(key, [{"date": "2026-09-29", "percent": 40., "kind": "close"}], "v1")
        self.store.write("direct_indicators", key, {
            "type": "direct_indicators", "source_id": "unlicensed", "universe": "dow30",
            "metric": "percent_members_above_sma50", "rows": [
                {"date": "2026-09-29", "percent": 80., "kind": "close", "source": "unlicensed"}],
        })
        clock = {"day": "2026-09-30", "complete_day": "2026-09-29", "regular": False,
                 "now": pd.Timestamp("2026-09-30T14:30:00Z"), "sessions": ["2026-09-29"]}
        with patch.dict(BREADTH_DIRECT_INDICATOR_SOURCES, {key: {"source_id": "unlicensed", "universe": "dow30"}}, clear=True), patch("tools.breadth_engine.market_clock", return_value=clock):
            self.assertEqual(chart_data(key, pd.to_datetime(["2026-09-29"]), self.store.root).iloc[-1].percent, 40.)

    def test_malformed_optional_direct_cache_falls_back_to_self(self):
        key = "dow"
        self.store.save_results(key, [{"date": "2026-09-29", "percent": 40., "kind": "close"}], "v1")
        clock = {"day": "2026-09-30", "complete_day": "2026-09-29", "regular": False,
                 "now": pd.Timestamp("2026-09-30T14:30:00Z"), "sessions": ["2026-09-29"]}
        approved={key: {"source_id": "licensed_fixture", "universe": "dow30", "access_approved": True}}
        self.store.write("direct_indicators", key, {
            "type": "direct_indicators", "source_id": "licensed_fixture", "universe": "dow30",
            "metric": "percent_members_above_sma50", "rows": None,
        })
        with patch.dict(BREADTH_DIRECT_INDICATOR_SOURCES, approved, clear=True), patch("tools.breadth_engine.market_clock", return_value=clock):
            frame = chart_data(key, pd.to_datetime(["2026-09-29"]), self.store.root)
        self.assertEqual(frame.iloc[-1].percent, 40.)

    def test_stockcharts_is_not_called_automatically(self):
        with patch("tools.breadth_engine.fetch_stockcharts", side_effect=AssertionError("禁止自动请求")) as fetch:
            refresh_market(self.store, "nasdaq100", refresh_members=False, use_futu=False)
        fetch.assert_not_called()

    def test_backcast_leaves_gap_when_50_day_coverage_falls_below_95_percent(self):
        now = pd.Timestamp("2026-09-30 10:00", tz="America/New_York")
        sessions = market_clock("US", now)["sessions"][-65:]
        self.store.save_members("nasdaq100", ["US.A", "US.B"], "official", "2026-09-29")
        self.store.save_prices("US.A", pd.DataFrame({"date": sessions, "close": [10.] * len(sessions)}), "fixture")
        missing = sessions.index("2026-09-28")
        self.store.save_prices("US.B", pd.DataFrame({"date": sessions[:missing] + sessions[missing+1:],
                                                       "close": [20.] * (len(sessions)-1)}), "fixture")
        with patch("tools.breadth_engine.market_clock", return_value=market_clock("US", now)):
            frame = chart_data("nasdaq100", pd.to_datetime([sessions[-55], "2026-09-28"]), self.store.root)
        last = frame.set_index("date").loc["2026-09-28"]
        self.assertTrue(pd.isna(last.percent))
        self.assertEqual(last.coverage, .5)

    def test_status_reports_chart_sources_and_backcast_without_writes(self):
        now = pd.Timestamp("2026-09-30 10:00", tz="America/New_York")
        sessions = market_clock("US", now)["sessions"][-65:]
        self.store.save_members("nasdaq100", ["US.A"], "official", "2026-09-29")
        self.store.save_prices("US.A", pd.DataFrame({"date": sessions, "close": [10.] * len(sessions)}), "fixture")
        self.store.save_results("nasdaq100", [{"date": "2026-09-29", "percent": 0., "kind": "close"}], "v1")
        before = {p.relative_to(self.store.root): p.read_bytes() for p in self.store.root.rglob("*.json")}
        with patch("tools.breadth_engine.market_clock", return_value=market_clock("US", now)), redirect_stdout(io.StringIO()) as output:
            market_breadth.main(["--status", "--market", "nasdaq100", "--cache-root", str(self.store.root)])
        after = {p.relative_to(self.store.root): p.read_bytes() for p in self.store.root.rglob("*.json")}
        self.assertEqual(before, after)
        chart = json.loads(output.getvalue())["chart_breadth"]
        self.assertEqual(chart["backcast_end"], "2026-09-28")
        self.assertEqual(chart["latest_source"], "self_calculated")
        self.assertEqual(chart["backcast_latest_coverage"], 1.)


if __name__ == "__main__":
    unittest.main()
