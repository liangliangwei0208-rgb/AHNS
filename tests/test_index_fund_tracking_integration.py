"""不发邮件的工作流、缓存保护及公共接口回归。"""
import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from PIL import Image

import git_main
import sync_repos
from tests.test_index_fund_tracking import NOW, CODE
from tools import get_top10_holdings as limits
from tools import index_fund_tracking as tracker
from tools.configs.workflow_configs import GITHUB_WORKFLOW_STEPS, SERVICE_WORKFLOW_STEPS
from tools.email_image_names import build_image_names


class TrackingIntegrationTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parent / "artifacts" / "index_fund_tracking"
        root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=root))

    def test_both_workflows_keep_tracker_in_realtime_windows(self):
        for workflow in (GITHUB_WORKFLOW_STEPS, SERVICE_WORKFLOW_STEPS):
            steps = git_main.resolve_workflow_steps(workflow)
            for hour in (8, 12, 19, 23):
                selected = git_main.select_workflow_steps_for_time(steps, NOW.replace(hour=hour))
                found = [s for s in selected if s.script_path.name == "index_fund_tracking.py"]
                self.assertEqual(len(found), 1)
                self.assertTrue(found[0].collect_images)

    def test_sync_registers_explicit_policies(self):
        for name in ("index_fund_tracking_returns.json", "index_fund_tracking_state.json"):
            self.assertTrue(sync_repos.is_auto_merge_cache_path(f"cache/{name}"))

    def test_rejected_detail_never_overwrites_success_cache(self):
        record = {CODE: {"value": "100元", "fetched_at": NOW.isoformat(), "source": "eastmoney_purchase_status_bulk"}}
        path = self.root / limits.FUND_PURCHASE_LIMIT_CACHE_FILE
        tracker.atomic_json(path, record)
        before = path.read_bytes()
        with patch.object(limits, "CACHE_DIR", self.root), \
                patch.object(limits, "_fetch_sale_availability_from_html", return_value={}), \
                patch.object(limits, "_FUND_LIMIT_CACHE", {}):
            result = limits.get_fund_purchase_limit(CODE, force_refresh=True, return_detail=True,
                bulk_limit_map={CODE: {"value": "限大额", "raw_purchase_status": "限大额", "raw_daily_limit_amount": "0"}},
                detail_validator=tracker.valid_limit)
        self.assertEqual(result["source"], "old_cache_after_error")
        self.assertEqual(before, path.read_bytes())

    def test_zero_amount_sale_fallback_can_confirm_unavailable(self):
        with patch.object(limits, "CACHE_DIR", self.root), \
                patch.object(limits, "_fetch_sale_availability_from_html", return_value={"raw_sale_availability": "暂不开放购买"}):
            result = limits.get_fund_purchase_limit(CODE, force_refresh=True, return_detail=True,
                bulk_limit_map={CODE: {"value": "限大额", "raw_purchase_status": "限大额", "raw_daily_limit_amount": "0"}},
                detail_validator=tracker.valid_limit)
        self.assertTrue(tracker.valid_limit(result))
        self.assertEqual(tracker.normalize_limit(result), "暂不销售")

    def test_union_cleanup_does_not_expand_holdings_pool(self):
        data = {CODE: {"value": "100元", "fetched_at": "2020-01-01"}}
        with patch.object(limits, "CACHE_DIR", self.root):
            limits._save_json_cache(limits.FUND_PURCHASE_LIMIT_CACHE_FILE, copy.deepcopy(data))
            limits._save_json_cache(limits.FUND_HOLDINGS_CACHE_FILE, copy.deepcopy(data))
        self.assertIn(CODE, tracker.read_mapping(self.root / limits.FUND_PURCHASE_LIMIT_CACHE_FILE))
        self.assertNotIn(CODE, tracker.read_mapping(self.root / limits.FUND_HOLDINGS_CACHE_FILE))

    def test_merge_preserves_whole_basis_and_success_after_failed_attempt(self):
        a = {"funds": {CODE: {"nav": {"basis": "y", "series": [{"date": "2026-10-12", "wealth": 1.2}],
             "latest_nav_date": "2026-10-12", "last_success_at": NOW.isoformat()}}}}
        b = {"funds": {CODE: {"nav": {"last_attempt_at": (NOW+timedelta(hours=1)).isoformat(), "error": "timeout"}}}}
        value = tracker.merge_returns_cache(a, b)
        self.assertEqual(value, tracker.merge_returns_cache(b, a))
        self.assertEqual(value["funds"][CODE]["nav"]["series"], a["funds"][CODE]["nav"]["series"])
        self.assertEqual(value["funds"][CODE]["nav"]["error"], "timeout")

    def test_csv_merge_preserves_longer_history(self):
        path = self.root / "dot_INX_index_daily.csv"
        pd.DataFrame({"date": ["2025-01-01", "2026-10-12"], "close": [100, 200]}).to_csv(path, index=False)
        frame = tracker.merge_index_prices(path, pd.DataFrame({"date": ["2026-10-13"], "close": [201]}), "2026-10-13")
        self.assertEqual(len(frame), 3)

    def test_pure_renderer_and_data_layer_do_not_call_holdings_or_estimator(self):
        import inspect
        from tools import index_fund_tracking_image
        text = inspect.getsource(tracker) + inspect.getsource(index_fund_tracking_image)
        for forbidden in ("get_latest_stock_holdings_df(", "estimate_fund_return(", "HAIWAI_FUND_CODES"):
            self.assertNotIn(forbidden, text)

    def test_short_dynamic_email_name(self):
        path = self.root / "index_fund_limit_change" / "latest" / "021000.png"
        self.assertEqual(build_image_names([path]), ["指数限购_021000.png"])

    def test_real_sina_decoder_import_and_completed_day_filter(self):
        rows = [{"date": "2026-10-13", "close": 100}, {"date": "2026-10-15", "close": 999}]
        with patch.object(tracker, "_get", return_value='var data="coded";'), \
                patch("py_mini_racer.MiniRacer") as decoder:
            decoder.return_value.call.return_value = rows
            result = tracker.fetch_index_prices(".NDX", NOW)
        self.assertEqual(result.date.tolist(), ["2026-10-13"])

    def test_sp500_target_accepts_full_chinese_name(self):
        self.assertTrue(tracker.matches_tracking_target(".INX", "标准普尔500指数"))
        self.assertFalse(tracker.matches_tracking_target(".NDX", "纳斯达克综合指数"))

    def test_state_merge_recovers_change_between_independent_baselines(self):
        a = {"funds": {CODE: {"baseline": {"value": "1000元", "observed_at": NOW.isoformat()}}}}
        b = {"funds": {CODE: {"baseline": {"value": "100元", "observed_at": (NOW+timedelta(hours=1)).isoformat()}}}}
        result = tracker.merge_tracking_state(a, b)
        events = result["funds"][CODE]["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(next(iter(events.values()))["old_value"], "1000元")
        self.assertEqual(result, tracker.merge_tracking_state(result, a))

    def test_merge_does_not_drop_another_computers_intermediate_baseline(self):
        later = (NOW+timedelta(hours=2)).isoformat()
        a = {"funds": {CODE: {"baseline": {"value": "500元", "observed_at": later},
             "events": {"a": {"id": "a", "old_value": "1000元", "new_value": "500元", "observed_at": later}}}}}
        b = {"funds": {CODE: {"baseline": {"value": "100元", "observed_at": (NOW+timedelta(hours=1)).isoformat()}}}}
        merged = tracker.merge_tracking_state(a, b)
        self.assertEqual(len(merged["funds"][CODE]["events"]), 2)
        self.assertEqual(merged, tracker.merge_tracking_state(b, a))
        self.assertEqual(merged, tracker.merge_tracking_state(merged, b))
        self.assertEqual(merged, tracker.merge_tracking_state(merged, a))

    def test_shared_csv_bom_mixed_encodings_merge_both_dates(self):
        a = '\ufeffdate,close\n2026-10-12,100\n'
        b = 'date,close\n2026-10-13,101\n'
        for left, right in ((a, b), (b, a)):
            merged = sync_repos.merge_index_daily_csv(left, right)
            self.assertIn('2026-10-12', merged)
            self.assertIn('2026-10-13', merged)
            self.assertEqual(merged.splitlines()[0].lstrip('\ufeff'), 'date,close')

    def test_same_second_conflicts_are_order_independent_and_not_fake_events(self):
        a = {"funds": {CODE: {"baseline": {"value": "1000元", "observed_at": NOW.isoformat()}}}}
        b = {"funds": {CODE: {"baseline": {"value": "100元", "observed_at": NOW.isoformat()}}}}
        merged = tracker.merge_tracking_state(a, b)
        self.assertEqual(merged, tracker.merge_tracking_state(b, a))
        self.assertEqual(merged, tracker.merge_tracking_state(merged, b))
        self.assertEqual(merged, tracker.merge_tracking_state(merged, a))
        entry = merged["funds"][CODE]
        self.assertFalse(entry["events"])
        values = {o["value"] for c in entry["baseline_conflicts"].values() for o in c["observations"]}
        self.assertEqual(values, {"100元", "1000元"})
        tracker.observe(entry, {"value": "100元", "observed_at": (NOW+timedelta(seconds=1)).isoformat()}, CODE, NOW.isoformat())
        self.assertFalse(entry["events"])
        self.assertTrue(all(c.get("resolved_at") for c in entry["baseline_conflicts"].values()))
        self.assertFalse(tracker.merge_tracking_state(merged, a)["funds"][CODE]["events"])

    def test_atomic_cache_retries_temporary_windows_file_occupation(self):
        from tools import fund_cache_maintenance as maintenance
        self.assertTrue(hasattr(maintenance, "replace_atomic_with_retry"))
        path = self.root / "retry.json"
        tracker.atomic_json(path, {"before": True})
        original_replace = maintenance.os.replace
        calls = []
        def replace(source, target):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError("文件短暂被占用")
            return original_replace(source, target)
        with patch.object(maintenance.os, "replace", side_effect=replace), patch.object(maintenance.time, "sleep"):
            tracker.atomic_json(path, {"after": True})
        self.assertEqual(tracker.read_mapping(path), {"after": True})
        self.assertEqual(len(calls), 2)

    def test_atomic_cache_permanent_failure_keeps_existing_data(self):
        from tools import fund_cache_maintenance as maintenance
        self.assertTrue(hasattr(maintenance, "replace_atomic_with_retry"))
        path = self.root / "permanent.json"
        tracker.atomic_json(path, {"before": True})
        with patch.object(maintenance.os, "replace", side_effect=PermissionError("拒绝")), patch.object(maintenance.time, "sleep"):
            with self.assertRaises(PermissionError):
                tracker.atomic_json(path, {"after": True})
        self.assertEqual(tracker.read_mapping(path), {"before": True})

    def test_real_script_image_collection_through_mock_smtp(self):
        configs = [{"name": "指数基金限购变化跟踪", "script": "index_fund_tracking.py", "required": False,
                    "collect_images": True, "always_run": True, "args": ["--auto"]}]
        output = self.root / "index_fund_limit_change" / "latest" / "021000.png"
        def stream(*args):
            output.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (12, 12)).save(output)
            return 1, ["其它基金收益刷新失败，已生成事件仍发送"]
        snapshot = git_main.snapshot_images
        with patch.object(git_main, "snapshot_images", side_effect=lambda: snapshot(self.root)), \
                patch.object(git_main, "stream_script_output", side_effect=stream), \
                patch.object(git_main, "send_email") as smtp:
            self.assertEqual(git_main.main([], workflow_steps=configs), 0)
        self.assertEqual(smtp.call_args.kwargs["image_paths"], [output.resolve()])
        self.assertIn("收益刷新失败", smtp.call_args.kwargs["text"])
        self.assertEqual(smtp.call_args.kwargs["timeout"], 450)

    def test_unchanged_script_does_not_send_old_event(self):
        configs = [{"name": "指数基金限购变化跟踪", "script": "index_fund_tracking.py", "required": False,
                    "collect_images": True, "always_run": True}]
        Image.new("RGB", (10, 10)).save(self.root / "021000.png")
        snapshot = git_main.snapshot_images
        with patch.object(git_main, "snapshot_images", side_effect=lambda: snapshot(self.root)), \
                patch.object(git_main, "stream_script_output", return_value=(0, ["无变化"])), \
                patch.object(git_main, "send_email") as smtp:
            self.assertEqual(git_main.main([], workflow_steps=configs), 0)
        smtp.assert_not_called()

    def test_sixteen_and_seventeen_image_policy_keeps_all_attachments(self):
        for count in (16, 17):
            images = [self.root / f"{i}.png" for i in range(count)]
            result = git_main.ScriptResult("指数基金限购变化跟踪", "index_fund_tracking.py", Path("index_fund_tracking.py"),
                                          0, 0, images, True, [])
            self.assertEqual(len(git_main.unique_images([result])), count)
            inline, attachments, _ = git_main.choose_email_image_send_options(count)
            self.assertTrue(attachments)
            self.assertEqual(inline, count <= 16)

    def test_curve_fallback_uses_one_complete_response_basis(self):
        html = '<table><tr><th>基金简称</th><td>测试纳斯达克100基金人民币A</td></tr><tr><th>基金类型</th><td>QDII</td></tr><tr><th>跟踪标的</th><td>纳斯达克100指数</td></tr></table>'
        ms = lambda d: int(pd.Timestamp(d, tz="Asia/Shanghai").timestamp()*1000)
        def response(url, **kwargs):
            if "jbgk_" in url: return html
            if "lsjz" in url: return {"Data": {"LSJZList": [{"FSRQ": "2026-10-12"}]}}
            if kwargs["params"]["type"] == "y":
                return {"Data": [{"data": [[ms("2026-01-01"), 0], [ms("2026-10-12"), 20]]}]}
            return {"Data": [{"data": [[ms("2025-09-01"), 300], [ms("2026-10-12"), 400]]}]}
        with patch.object(tracker, "_get", side_effect=response):
            result = tracker.fetch_fund_source(CODE, NOW)
        self.assertEqual(result["basis"], "try")
        self.assertEqual([r["wealth"] for r in result["series"]], [4, 5])

    def test_failed_fallback_retains_valid_one_year_response(self):
        html = '<table><tr><th>基金简称</th><td>新成立测试基金</td></tr><tr><th>基金类型</th><td>QDII</td></tr><tr><th>跟踪标的</th><td>纳斯达克100指数</td></tr></table>'
        ms = lambda d: int(pd.Timestamp(d, tz="Asia/Shanghai").timestamp()*1000)
        def response(url, **kwargs):
            if "jbgk_" in url: return html
            if "lsjz" in url: return {"Data": {"LSJZList": [{"FSRQ": "2026-10-12"}]}}
            if kwargs["params"]["type"] == "y":
                return {"Data": [{"data": [[ms("2026-01-01"), 0], [ms("2026-10-12"), 20]]}]}
            raise OSError("三年接口失败")
        with patch.object(tracker, "_get", side_effect=response):
            result = tracker.fetch_fund_source(CODE, NOW)
        self.assertEqual(result["basis"], "y")
        self.assertIn("三年接口失败", result["source_warnings"][0])


if __name__ == "__main__":
    unittest.main()
