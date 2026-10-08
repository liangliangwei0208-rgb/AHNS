"""独立广度入口的只读状态与有限修复语义。"""
import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import market_breadth
from tools.market_breadth import BreadthStore


class BreadthCliTests(unittest.TestCase):
    def test_default_update_uses_ndx_and_excludes_legacy_composite(self):
        from tools.configs.market_breadth_configs import BREADTH_DEFAULT_MARKETS
        self.assertIn("nasdaq100", BREADTH_DEFAULT_MARKETS)
        self.assertNotIn("nasdaq", BREADTH_DEFAULT_MARKETS)
        with patch("tools.breadth_engine.refresh_market", return_value={"latest": {"percent": 50}, "errors": []}) as refresh:
            with redirect_stdout(io.StringIO()):
                market_breadth.main(["--update", "--budget", "100", "--cache-root", str(self.root),
                                     "--report-path", str(self.root / "report.json"), "--worker"])
        self.assertEqual([call.args[1] for call in refresh.call_args_list], list(BREADTH_DEFAULT_MARKETS))

    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)

    def test_status_is_read_only_and_reports_pending(self):
        store=BreadthStore(self.root)
        store.save_members("dow",["US.A"],"official","2026-09-25")
        store.save_members("dow",["US.B"],"official","2026-09-28",effective_date=None)
        before={p.relative_to(self.root):p.read_bytes() for p in self.root.rglob("*.json")}
        with redirect_stdout(io.StringIO()) as output:
            rc=market_breadth.main(["--status","--market","dow","--cache-root",str(self.root)])
        after={p.relative_to(self.root):p.read_bytes() for p in self.root.rglob("*.json")}
        self.assertEqual(rc,0)
        self.assertEqual(before,after)
        self.assertIn("pending_membership",output.getvalue())

    def test_status_lists_future_version_event_and_prewarm_progress(self):
        store=BreadthStore(self.root)
        store.save_members("dow",["US.A","US.B"],"https://www.spglobal.com/old","2026-09-25")
        store.save_members("dow",["US.B","US.C"],"https://www.spglobal.com/new","2026-09-29",
                           effective_date="2026-10-01",evidence_url="https://www.spglobal.com/notice")
        with patch("tools.breadth_engine.market_clock",return_value={"day":"2026-09-30","complete_day":"2026-09-29"}), \
             redirect_stdout(io.StringIO()) as output:
            market_breadth.main(["--status","--market","dow","--cache-root",str(self.root)])
        status=json.loads(output.getvalue())
        self.assertEqual(status["members"],2)
        self.assertEqual(status["future_memberships"][0]["effective_date"],"2026-10-01")
        self.assertEqual(status["latest_event"]["added"],["US.C"])
        self.assertEqual(status["prewarm"]["missing"],1)
        self.assertEqual(status["source"],"https://www.spglobal.com/old")

    def test_repair_flag_reaches_engine_without_bootstrap(self):
        with patch("tools.breadth_engine.refresh_market",return_value={"latest":{"percent":50},"status":"complete","errors":[]}) as refresh:
            with redirect_stdout(io.StringIO()):
                rc=market_breadth.main(["--repair","--market","dow","--cache-root",str(self.root),
                                         "--report-path",str(self.root/"diagnostics.json"),"--worker"])
        self.assertEqual(rc,0)
        self.assertTrue(refresh.call_args.kwargs["repair"])
        self.assertFalse(refresh.call_args.kwargs["bootstrap"])

    def test_shared_budget_is_divided_so_later_markets_get_a_turn(self):
        with patch("tools.breadth_engine.refresh_market",return_value={"latest":{"percent":50},"errors":[]}) as refresh:
            started=time.monotonic()
            with redirect_stdout(io.StringIO()):
                market_breadth.main(["--update","--market","nasdaq","shenzhen","--budget","100",
                                     "--cache-root",str(self.root),"--report-path",str(self.root/"report.json"),"--worker"])
        self.assertEqual(refresh.call_count,2)
        self.assertLess(refresh.call_args_list[0].kwargs["deadline"]-started,65)

    def test_confirm_pending_needs_official_evidence(self):
        store=BreadthStore(self.root)
        store.save_members("shenzhen",["SZ.000001"],"official","2026-09-25")
        store.save_members("shenzhen",["SZ.000002"],"official","2026-09-28",effective_date=None)
        with self.assertRaises(ValueError):
            market_breadth.main(["--activate-pending","--market","shenzhen","--effective-date","2026-10-08",
                                 "--evidence-url","https://example.com/notice","--cache-root",str(self.root)])
        with redirect_stdout(io.StringIO()):
            rc=market_breadth.main(["--activate-pending","--market","shenzhen","--effective-date","2026-10-08",
                                     "--evidence-url","https://www.cnindex.com.cn/notice","--cache-root",str(self.root)])
        self.assertEqual(rc,0)
        self.assertEqual(store.members("shenzhen","2026-10-08")["symbols"],["SZ.000002"])


if __name__=="__main__":unittest.main()
