"""广度计算及同步边界：使用手算样本，避免依赖网络。"""
import json
import tempfile
import unittest
from pathlib import Path
import pandas as pd
from tools.market_breadth import calculate_history, calculate_intraday, BreadthStore, merge_document, eligible_quote


def price(values, start="2026-01-01"):
    return pd.DataFrame({"date": pd.bdate_range(start, periods=len(values)).strftime("%Y-%m-%d"), "close": values})


class BreadthCalculationTests(unittest.TestCase):
    def test_direct_indicator_merge_never_combines_different_sources(self):
        old={"type":"direct_indicators","source_id":"one","universe":"dow30",
             "metric":"percent_members_above_sma50","updated_at":"2026-09-29T00:00:00Z",
             "rows":[{"date":"2026-09-28","percent":40,"kind":"close"}]}
        new={"type":"direct_indicators","source_id":"two","universe":"dow30",
             "metric":"percent_members_above_sma50","updated_at":"2026-09-30T00:00:00Z",
             "rows":[{"date":"2026-09-29","percent":50,"kind":"close"}]}
        self.assertEqual(merge_document(old,new),new)

    def test_strict_above_and_50_day_warmup(self):
        data={"A":price([10]*49+[11]), "B":price([10]*50)}
        out=calculate_history(data, ["A","B"], min_coverage=.95)
        self.assertTrue(out.iloc[:49].percent.isna().all())
        self.assertEqual(out.iloc[-1].percent,50)
        self.assertEqual(out.iloc[-1].valid,2)

    def test_recent_ipo_without_50_days_remains_in_coverage_denominator(self):
        dates=pd.bdate_range("2026-06-01",periods=50).strftime("%Y-%m-%d")
        prices={"US.OLD":pd.DataFrame({"date":dates,"close":[10.]*49+[11.]}),
                "US.NEW":pd.DataFrame({"date":dates[-10:],"close":[20.]*10})}
        out=calculate_history(prices,["US.OLD","US.NEW"],min_coverage=.95,sessions=dates)
        self.assertEqual(out.iloc[-1].valid,1)
        self.assertEqual(out.iloc[-1].total,2)
        self.assertEqual(out.iloc[-1].coverage,.5)
        self.assertTrue(pd.isna(out.iloc[-1].percent))

    def test_missing_not_below_and_coverage_gate(self):
        data={"A":price([10]*49+[11])}
        out=calculate_history(data,["A","B"])
        self.assertTrue(pd.isna(out.iloc[-1].percent))
        self.assertEqual(out.iloc[-1].coverage,.5)

    def test_exact_95_percent_coverage_publishes_but_90_does_not(self):
        symbols=[f"US.T{i:02d}" for i in range(20)]
        rows={code:price([10.]*49+[11.]) for code in symbols[:19]}
        exact=calculate_history(rows,symbols,min_coverage=.95).iloc[-1]
        self.assertEqual(exact.coverage,.95)
        self.assertEqual(exact.percent,100.)
        below=calculate_history({code:rows[code] for code in symbols[:18]},symbols,
                                min_coverage=.95).iloc[-1]
        self.assertEqual(below.coverage,.9)
        self.assertTrue(pd.isna(below.percent))

    def test_missing_session_breaks_window_and_duplicates_do_not_count(self):
        df=price([10]*49+[11]); df=pd.concat([df.iloc[:20],df.iloc[21:],df.iloc[[-1]]])
        out=calculate_history({"A":df,"B":price([10]*49+[11])},["A","B"])
        self.assertEqual(out.iloc[-1].valid,1)
        self.assertTrue(pd.isna(out.iloc[-1].percent))

    def test_intraday_uses_49_days_and_excludes_today(self):
        df=price([10]*49+[999])
        day=df.iloc[-1].date
        out=calculate_intraday({"A":df,"B":df},["A","B"],day,{"A":11,"B":10})
        self.assertEqual(out["percent"],50)
        self.assertEqual(out["kind"],"intraday")

    def test_intraday_does_not_bridge_missing_previous_session(self):
        a=price([10]*51); b=a.iloc[:-1]
        day=str((pd.Timestamp(a.iloc[-1].date)+pd.offsets.BDay()).date())
        out=calculate_intraday({"A":a,"B":b},["A","B"],day,{"A":11,"B":11})
        self.assertEqual(out["valid"],1)
        self.assertIsNone(out["percent"])

    def test_quote_date_future_stale_and_regular_session(self):
        now=pd.Timestamp("2026-09-25 15:00",tz="America/New_York")
        self.assertTrue(eligible_quote("2026-09-25 14:45",now,"US",60))
        for stamp in ["2026-09-24 15:00","2026-09-25 08:00","2026-09-25 13:00","2026-09-25 15:05"]:
            self.assertFalse(eligible_quote(stamp,now,"US",60))
        lunch=pd.Timestamp("2026-09-25 12:00",tz="Asia/Shanghai")
        self.assertFalse(eligible_quote("2026-09-25 11:59",lunch,"CN",60))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=BreadthStore(Path(self.tmp.name))

    def test_price_source_switch_replaces_window_not_splices(self):
        self.store.save_prices("US.A",price([10]*50),"one","2026-04-01T00:00:00+00:00")
        self.store.save_prices("US.A",price([20]*50),"two","2026-04-02T00:00:00+00:00")
        self.assertEqual(self.store.prices("US.A").close.tolist(),[20]*50)
        with self.assertRaises(ValueError):
            self.store.save_prices("US.A",price([30]*2),"three","2026-04-03T00:00:00+00:00")

    def test_membership_versions_and_old_results_are_preserved(self):
        self.store.save_members("dow",["US.A","US.B"],"official","2026-04-01")
        self.store.save_members("dow",["US.A","US.C"],"official","2026-04-02")
        self.assertEqual(self.store.members("dow","2026-04-01")["symbols"],["US.A","US.B"])
        self.store.save_results("dow",[{"date":"2026-04-01","percent":50,"kind":"close","valid":2,"total":2,"coverage":1}],"v1")
        self.store.save_results("dow",[{"date":"2026-04-01","percent":100,"kind":"close","valid":2,"total":2,"coverage":1}],"v2")
        self.assertEqual(self.store.results("dow").iloc[0].percent,50)

    def test_intraday_cannot_replace_close_and_new_close_replaces_preview(self):
        base={"date":"2026-04-01","percent":50,"valid":2,"total":2,"coverage":1}
        self.store.save_results("dow",[dict(base,kind="intraday")],"v1")
        self.store.save_results("dow",[dict(base,kind="close",percent=100)],"v1")
        self.store.save_results("dow",[dict(base,kind="intraday",percent=0)],"v1")
        self.assertEqual(self.store.results("dow").iloc[0].percent,100)

    def test_new_low_coverage_preview_leaves_gap_instead_of_old_value(self):
        self.store.save_results("dow",[{"date":"2026-04-01","percent":60,"kind":"intraday"}],"v1")
        self.store.save_results("dow",[{"date":"2026-04-01","percent":None,"kind":"intraday"}],"v1")
        self.assertTrue(pd.isna(self.store.results("dow").iloc[0].percent))

    def test_sync_different_price_basis_selects_whole_newer_snapshot(self):
        a={"type":"prices","basis":"old","updated_at":"2026-01-01","rows":[{"date":"2025-01-01","close":10}]}
        b={"type":"prices","basis":"new","updated_at":"2026-01-02","rows":[{"date":"2025-01-02","close":20}]}
        self.assertEqual(merge_document(a,b)["rows"],b["rows"])
        self.assertEqual(merge_document(a,b),merge_document(b,a))

    def test_sync_same_price_basis_merges_missing_dates_only_when_overlap_matches(self):
        a={"type":"prices","basis":"same","updated_at":"2026-01-01","rows":[{"date":"2025-01-01","close":10},{"date":"2025-01-02","close":11}]}
        b={"type":"prices","basis":"same","updated_at":"2026-01-02","rows":[{"date":"2025-01-02","close":11},{"date":"2025-01-03","close":12}]}
        self.assertEqual(len(merge_document(a,b)["rows"]),3)
        b["rows"][0]["close"]=5.5
        self.assertEqual(merge_document(a,b)["rows"],b["rows"])
    def test_sync_is_deterministic_and_close_wins(self):
        a={"type":"results","rows":[{"date":"2026-01-01","kind":"close","percent":60,"updated_at":"2026-01-01"}]}
        b={"type":"results","rows":[{"date":"2026-01-01","kind":"intraday","percent":90,"updated_at":"2026-01-02"}]}
        self.assertEqual(merge_document(a,b)["rows"][0]["percent"],60)
        self.assertEqual(merge_document(a,b),merge_document(b,a))

if __name__=="__main__": unittest.main()
