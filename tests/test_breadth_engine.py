import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from tools.market_breadth import BreadthStore
from tools.breadth_engine import refresh_market, market_clock, prepare_quotes, prepare_close_quotes, chart_data

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

    def test_close_snapshot_requires_completed_day_exact_close_time_and_cached_basis(self):
        self.store.save_prices("SH.600007",pd.DataFrame({"date":["2026-09-24"],"close":[19.8]}),"tencent_qfq")
        now=pd.Timestamp("2026-09-28 15:20",tz="Asia/Shanghai")
        q={"SH.600007":{"price":19.93,"prev_close":19.8,"time":"2026-09-28 15:00:00","source":"futu_snapshot"}}
        values=prepare_close_quotes(self.store,["SH.600007"],q,now,"2026-09-28","2026-09-24")
        self.assertAlmostEqual(values["SH.600007"],19.93)
        # 次日清晨仍可核验最近已完成交易日的15:00收盘快照。
        morning=pd.Timestamp("2026-09-29 06:00",tz="Asia/Shanghai")
        self.assertAlmostEqual(prepare_close_quotes(self.store,["SH.600007"],q,morning,
                                                    "2026-09-28","2026-09-24")["SH.600007"],19.93)
        for stamp in ("2026-09-28 14:59:59","2026-09-28 15:01:00","2026-09-24 15:00:00"):
            q["SH.600007"]["time"]=stamp
            self.assertEqual(prepare_close_quotes(self.store,["SH.600007"],q,now,"2026-09-28","2026-09-24"),{})
        q["SH.600007"]["time"]="2026-09-28 15:00:00"
        self.assertEqual(prepare_close_quotes(self.store,["SH.600007"],q,pd.Timestamp("2026-09-28 15:10",tz="Asia/Shanghai"),"2026-09-28","2026-09-24"),{})

    def test_after_close_snapshot_restores_95_percent_without_history_quota(self):
        now=pd.Timestamp("2026-09-28 20:30",tz="Asia/Shanghai")
        clock=market_clock("CN",now)
        sessions=[d for d in clock["sessions"] if d<clock["day"]][-49:]
        symbols=[f"SH.{i:06d}" for i in range(20)]
        self.store.save_members("dividend",symbols,"fixture",clock["day"])
        for code in symbols:
            self.store.save_prices(code,pd.DataFrame({"date":sessions,"close":[10.]*49}),"tencent_qfq")
        class Futu:
            def history(self,*args):raise RuntimeError("history quota must not be used")
            def quotes(self,codes):
                return {code:{"price":11.,"prev_close":10.,"time":"2026-09-28 15:00:00","source":"futu_snapshot"} for code in codes if code!=symbols[-1]}
            def close(self):pass
        observed=[]
        def unavailable(*args,**kwargs):
            # 慢日线开始前必须已保存可用收盘广度，外层超时也不会丢值。
            rows=self.store.results("dividend")
            observed.append(None if rows.empty else rows.iloc[-1].percent)
            raise RuntimeError("daily source unavailable")
        with patch("tools.breadth_engine.fetch_prices",side_effect=unavailable),patch("tools.breadth_engine.FutuBreadth",return_value=Futu()):
            report=refresh_market(self.store,"dividend",now,refresh_members=False)
        latest=self.store.results("dividend").iloc[-1]
        self.assertEqual(latest.date,"2026-09-28")
        self.assertEqual(latest.kind,"close")
        self.assertEqual(latest.valid,19)
        self.assertEqual(latest.percent,100.)
        self.assertEqual(self.store.prices(symbols[0]).iloc[-1].date,"2026-09-28")
        self.assertEqual(report["close_snapshot"]["eligible"],19)
        self.assertTrue(observed)
        self.assertTrue(all(value==100. for value in observed))

    def test_next_morning_uses_last_completed_session_snapshot(self):
        now=pd.Timestamp("2026-09-29 06:00",tz="Asia/Shanghai")
        clock=market_clock("CN",now)
        self.assertEqual(clock["complete_day"],"2026-09-28")
        sessions=[d for d in clock["sessions"] if d<clock["complete_day"]][-49:]
        symbols=[f"SH.{i:06d}" for i in range(20)]
        self.store.save_members("dividend",symbols,"fixture",clock["day"])
        for code in symbols:
            self.store.save_prices(code,pd.DataFrame({"date":sessions,"close":[10.]*49}),"tencent_qfq")
        class Futu:
            def history(self,*args):raise RuntimeError("history quota must not be used")
            def quotes(self,codes):
                return {code:{"price":11.,"prev_close":10.,"time":"2026-09-28 15:00:00"}
                        for code in codes if code!=symbols[-1]}
            def close(self):pass
        with patch("tools.breadth_engine.fetch_prices",side_effect=RuntimeError("offline")):
            report=refresh_market(self.store,"dividend",now,refresh_members=False,futu=Futu())
        self.assertEqual(report["latest"]["date"],"2026-09-28")
        self.assertEqual(report["latest"]["valid"],19)
        self.assertEqual(report["latest"]["percent"],100.)

    def test_five_session_fallback_marks_date_without_reviving_stale_intraday(self):
        import matplotlib.pyplot as plt
        self.store.save_results("dow",[{"date":"2026-09-24","percent":64.8,"kind":"close"},
                                       {"date":"2026-09-28","percent":57.2,"kind":"intraday","observed_at":"2026-09-28T09:38:00+08:00"}],"fixture")
        now=pd.Timestamp("2026-09-28T12:30:00Z")
        clock={"day":"2026-09-28","complete_day":"2026-09-28","now":now,
               "sessions":["2026-09-24","2026-09-28"]}
        with patch("tools.breadth_engine.market_clock",return_value=clock):
            frame=chart_data("dow",pd.to_datetime(["2026-09-24"]),self.store.root)
        from tools.market_breadth import draw_breadth
        fig,ax=plt.subplots()
        draw_breadth(ax,frame)
        self.assertIn("50D: 64.8% · 09-24收",[t.get_text() for t in ax.texts])
        plt.close(fig)
        clock["sessions"]=["2026-09-24","2026-09-28","2026-09-29","2026-09-30","2026-10-01","2026-10-02","2026-10-05"]
        clock["day"]=clock["complete_day"]="2026-10-05"
        clock["now"]=pd.Timestamp("2026-10-05T12:30:00Z")
        with patch("tools.breadth_engine.market_clock",return_value=clock):
            frame=chart_data("dow",pd.to_datetime(["2026-09-24"]),self.store.root)
        fig,ax=plt.subplots();draw_breadth(ax,frame)
        self.assertIn("50D：数据不足",[t.get_text() for t in ax.texts])
        plt.close(fig)

if __name__=="__main__":unittest.main()
