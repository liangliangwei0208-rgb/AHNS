import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from tools.market_breadth import BreadthStore
from tools.breadth_engine import refresh_market, market_clock, prepare_quotes, chart_data

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=BreadthStore(Path(self.tmp.name))
        self.now=pd.Timestamp("2026-09-26 12:00",tz="UTC")
        self.clock=market_clock("US",self.now)
        self.dates=self.clock["sessions"][-80:]
        self.prices=pd.DataFrame({"date":self.dates,"close":list(range(100,180))})
        self.store.save_members("dow",["US.A"],"fixture",self.clock["day"])
    def test_restart_skips_completed_symbols_and_reuses_history(self):
        with patch("tools.breadth_engine.fetch_prices",return_value=(self.prices,"fixture")) as fetch:
            one=refresh_market(self.store,"dow",self.now,bootstrap=True,refresh_members=False)
            two=refresh_market(self.store,"dow",self.now,bootstrap=True,refresh_members=False)
        self.assertEqual(one["downloaded"],1);self.assertEqual(two["downloaded"],0)
        self.assertEqual(self.store.results("dow").iloc[-1].percent,100)
    def test_no_initial_download_during_daily_run(self):
        with patch("tools.breadth_engine.fetch_prices",side_effect=AssertionError("不应建库")):
            result=refresh_market(self.store,"dow",self.now,bootstrap=False,refresh_members=False)
            result=refresh_market(self.store,"dow",self.now,bootstrap=False,refresh_members=False)
        self.assertEqual(result["needs_bootstrap"],1)
        self.assertEqual(len(self.store.prices("US.A")),0)
    def test_new_component_gets_bounded_daily_backfill_after_initialization(self):
        self.store.save_prices("US.A",self.prices,"fixture")
        self.store.save_results("dow",[{"date":self.dates[-1],"percent":100,"kind":"close"}],"old")
        self.store.save_members("dow",["US.A","US.B"],"fixture",self.clock["day"])
        with patch("tools.breadth_engine.fetch_prices",return_value=(self.prices,"fixture")) as fetch:
            result=refresh_market(self.store,"dow",self.now,refresh_members=False)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(result["needs_bootstrap"],0)

    def test_quote_conversion_uses_cached_adjusted_previous_close(self):
        self.store.save_prices("US.A",self.prices,"fixture")
        now=pd.Timestamp("2026-09-28 10:00",tz="America/New_York")
        quote={"US.A":{"price":220,"prev_close":200,"time":"2026-09-28 09:50","source":"fixture"}}
        out=prepare_quotes(self.store,["US.A"],quote,now,"US",self.dates[-1])
        self.assertAlmostEqual(out["US.A"],196.9)
    def test_chart_retains_current_session_beyond_previous_daily_price_and_hides_stale(self):
        row=dict(date="2026-09-28",percent=60.,kind="intraday",valid=30,total=30,coverage=1,observed_at="2026-09-28T14:00:00+00:00")
        self.store.save_results("dow",[row],"fixture")
        now=pd.Timestamp("2026-09-28T14:30:00Z")
        with patch("tools.breadth_engine.market_clock",return_value={"day":"2026-09-28","now":now}):
            out=chart_data("dow",pd.to_datetime(["2026-09-25"]),self.store.root)
        self.assertEqual(out.iloc[-1].percent,60.)
        with patch("tools.breadth_engine.market_clock",return_value={"day":"2026-09-28","now":now+pd.Timedelta(hours=2)}):
            out=chart_data("dow",pd.to_datetime(["2026-09-25"]),self.store.root)
        self.assertTrue(pd.isna(out.iloc[-1].percent))
    def test_holiday_weekend_lunch_never_marked_regular(self):
        for stamp in ["2026-09-26 10:00","2026-09-07 10:00"]:
            self.assertFalse(market_clock("US",pd.Timestamp(stamp,tz="America/New_York"))["regular"])
        self.assertFalse(market_clock("CN",pd.Timestamp("2026-09-28 12:00",tz="Asia/Shanghai"))["regular"])

if __name__=="__main__":unittest.main()
