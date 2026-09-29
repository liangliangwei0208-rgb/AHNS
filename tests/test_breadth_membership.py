"""广度成分版本和逐日口径的回归测试。"""
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tools.market_breadth import BreadthStore, calculate_segmented_history, merge_document


class MembershipVersionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = BreadthStore(Path(tmp.name))

    def test_same_list_only_updates_verification_time(self):
        first = self.store.save_members("dow", ["US.A", "US.B"], "official", "2026-09-25")
        second = self.store.save_members("dow", ["US.B", "US.A"], "official-new-url", "2026-09-28")
        self.assertEqual(first["version"], second["version"])
        self.assertEqual(len(self.store.read("members", "dow")["rows"]), 1)
        self.assertEqual(second["verified_date"], "2026-09-28")
        self.assertEqual(self.store.read("members","dow")["pit_start"],"2026-09-25")

    def test_future_version_waits_and_event_keeps_removed_price(self):
        self.store.save_members("dow", ["US.A", "US.B"], "official", "2026-09-25")
        self.store.save_members("dow", ["US.B", "US.C"], "official", "2026-09-28",
                                effective_date="2026-10-01")
        self.assertEqual(self.store.members("dow", "2026-09-30")["symbols"], ["US.A", "US.B"])
        self.assertEqual(self.store.members("dow", "2026-10-01")["symbols"], ["US.B", "US.C"])
        event = self.store.read("membership_events", "dow")["rows"][-1]
        self.assertEqual(event["added"], ["US.C"])
        self.assertEqual(event["removed"], ["US.A"])

    def test_unknown_effective_date_stays_pending(self):
        self.store.save_members("dividend", ["SH.600001"], "official", "2026-09-25")
        self.store.save_members("dividend", ["SH.600002"], "official", "2026-09-28",
                                effective_date=None)
        self.assertEqual(self.store.members("dividend", "2026-10-02")["symbols"], ["SH.600001"])
        self.assertEqual(self.store.read("members", "dividend")["pending_membership"]["symbols"], ["SH.600002"])
        self.store.activate_pending("dividend","2026-10-05","https://www.csindex.com.cn/official-announcement")
        self.assertEqual(self.store.members("dividend","2026-10-04")["symbols"],["SH.600001"])
        self.assertEqual(self.store.members("dividend","2026-10-05")["symbols"],["SH.600002"])

    def test_legacy_membership_never_labels_old_backcast_as_point_in_time(self):
        self.store.write("members","dow",{"type":"members","rows":[{"date":"2026-01-01",
                            "version":"legacy","symbols":["US.A"],"source":"old"}]})
        self.store.establish_pit_start("dow","2026-09-29")
        days=pd.bdate_range("2026-07-01",periods=60).strftime("%Y-%m-%d").tolist()
        prices={"US.A":pd.DataFrame({"date":days,"close":[10.]*59+[11.]})}
        out=calculate_segmented_history(prices,self.store,"dow",days[-2:])
        self.assertTrue(out.empty)

    def test_pit_activation_marks_legacy_results_without_recalculating(self):
        self.store.write("members","dow",{"type":"members","rows":[{"date":"2026-09-25",
                         "version":"legacy","symbols":["US.A"],"source":"old"}]})
        self.store.write("results","dow",{"type":"results","rows":[{"date":"2026-09-25",
                         "kind":"close","percent":60.,"membership_version":"legacy"}]})
        self.store.establish_pit_start("dow","2026-09-29")
        result=self.store.read("results","dow")["rows"][0]
        self.assertEqual(result["percent"],60.)
        self.assertEqual(result["membership_policy"],"current_members_backcast")

    def test_sync_keeps_legacy_policy_annotation_when_value_unchanged(self):
        plain={"type":"results","rows":[{"date":"2026-09-25","kind":"close","percent":60.,
                  "membership_version":"v1","updated_at":"2026-09-25T20:00:00Z"}]}
        tagged={"type":"results","rows":[dict(plain["rows"][0],
                  membership_policy="current_members_backcast",updated_at="2026-09-29T20:00:00Z")]}
        self.assertEqual(merge_document(plain,tagged)["rows"][0]["membership_policy"],"current_members_backcast")

    def test_segmented_history_uses_pre_entry_prices_without_backcast(self):
        days = pd.bdate_range("2026-01-01", periods=52).strftime("%Y-%m-%d").tolist()
        self.store.save_members("dow", ["US.A"], "official", days[0])
        self.store.save_members("dow", ["US.B"], "official", days[51], effective_date=days[51])
        prices = {
            "US.A": pd.DataFrame({"date": days, "close": [10.] * 49 + [11.] * 3}),
            "US.B": pd.DataFrame({"date": days, "close": [20.] * 49 + [19.] * 3}),
        }
        result = calculate_segmented_history(prices, self.store, "dow", days[-3:])
        self.assertEqual(result.iloc[0].percent, 100.)
        self.assertEqual(result.iloc[-1].percent, 0.)
        self.assertNotEqual(result.iloc[0].membership_version, result.iloc[-1].membership_version)

    def test_calendar_sessions_exclude_nontrading_price_from_50_day_window(self):
        days=pd.bdate_range("2026-01-01",periods=50).strftime("%Y-%m-%d").tolist()
        self.store.save_members("dow",["US.A"],"official",days[0])
        frame=pd.DataFrame({"date":days[1:]+["2026-02-01"],"close":[10.]*49+[10.]})
        out=calculate_segmented_history({"US.A":frame},self.store,"dow",[days[-1]],
                                        calendar_sessions=days)
        self.assertEqual(out.iloc[-1].valid,0)
        self.assertTrue(pd.isna(out.iloc[-1].percent))

    def test_published_close_is_immutable_even_same_member_version(self):
        old = {"type": "results", "rows": [{"date": "2026-09-25", "kind": "close", "percent": 40,
                                             "membership_version": "v1", "updated_at": "2026-09-26"}]}
        new = {"type": "results", "rows": [{"date": "2026-09-25", "kind": "close", "percent": 80,
                                             "membership_version": "v1", "updated_at": "2026-09-29"}]}
        self.assertEqual(merge_document(old, new)["rows"][0]["percent"], 40)
        self.assertEqual(merge_document(new, old)["rows"][0]["percent"], 40)

    def test_official_close_can_reject_provisional_snapshot(self):
        provisional={"type":"results","rows":[{"date":"2026-09-28","kind":"close","percent":60,
                  "finality":"snapshot_provisional","updated_at":"2026-09-28T21:00:00Z"}]}
        official={"type":"results","rows":[{"date":"2026-09-28","kind":"close","percent":None,
                  "finality":"official","updated_at":"2026-09-29T01:00:00Z"}]}
        self.assertIsNone(merge_document(provisional,official)["rows"][0]["percent"])
        self.assertEqual(merge_document(provisional,official),merge_document(official,provisional))

    def test_only_explicit_repair_can_change_published_close(self):
        first={"date":"2026-09-28","kind":"close","percent":40.,"valid":30,"total":30,"coverage":1.}
        second=dict(first,percent=60.)
        self.store.save_results("dow",[first],"v1")
        self.store.save_results("dow",[second],"v1")
        self.assertEqual(self.store.results("dow").iloc[-1].percent,40.)
        self.store.save_results("dow",[second],"v1",repair=True)
        self.assertEqual(self.store.results("dow").iloc[-1].percent,60.)

    def test_explicit_repair_survives_two_machine_merge(self):
        original={"type":"results","rows":[{"date":"2026-09-28","kind":"close","percent":40.,
                    "finality":"official","updated_at":"2026-09-28T23:00:00Z"}]}
        corrected={"type":"results","rows":[{"date":"2026-09-28","kind":"close","percent":60.,
                    "finality":"official","repair_revision":1,"updated_at":"2026-09-29T01:00:00Z"}]}
        self.assertEqual(merge_document(original,corrected)["rows"][0]["percent"],60.)
        self.assertEqual(merge_document(corrected,original)["rows"][0]["percent"],60.)

    def test_activated_membership_wins_over_stale_pending_on_sync(self):
        self.store.save_members("dow",["US.A"],"official","2026-09-25")
        self.store.save_members("dow",["US.B"],"official","2026-09-28",effective_date=None)
        before=self.store.read("members","dow")
        self.store.activate_pending("dow","2026-10-01","https://www.spglobal.com/notice")
        after=self.store.read("members","dow")
        merged=merge_document(before,after)
        self.assertNotIn("pending_membership",merged)

    def test_snapshot_price_stays_provisional_until_official_daily_replaces_it(self):
        dates=pd.bdate_range("2026-06-01",periods=51).strftime("%Y-%m-%d").tolist()
        frame=pd.DataFrame({"date":dates[:-1],"close":[10.]*50})
        self.store.save_prices("US.A",frame,"yahoo_adjclose")
        self.store.save_prices("US.A",pd.DataFrame({"date":[dates[-1]],"close":[11.]}),
                               "yahoo_adjclose",provisional=True)
        self.assertEqual(self.store.read("prices","US.A")["provisional_dates"],[dates[-1]])
        self.store.save_prices("US.A",pd.DataFrame({"date":[dates[-1]],"close":[12.]}),
                               "yahoo_adjclose")
        doc=self.store.read("prices","US.A")
        self.assertEqual(doc.get("provisional_dates"),[])
        self.assertEqual(self.store.prices("US.A").iloc[-1].close,12.)

    def test_sync_official_price_beats_newer_snapshot(self):
        official={"type":"prices","basis":"adjusted","updated_at":"2026-09-29T01:00:00Z",
                  "rows":[{"date":"2026-09-25","close":10.},{"date":"2026-09-28","close":12.}],
                  "provisional_dates":[]}
        snapshot={"type":"prices","basis":"adjusted","updated_at":"2026-09-29T02:00:00Z",
                  "rows":[{"date":"2026-09-25","close":10.},{"date":"2026-09-28","close":11.}],
                  "provisional_dates":["2026-09-28"]}
        for left,right in ((official,snapshot),(snapshot,official)):
            merged=merge_document(left,right)
            self.assertEqual(merged["rows"][-1]["close"],12.)
            self.assertEqual(merged.get("provisional_dates"),[])


if __name__ == "__main__":
    unittest.main()
