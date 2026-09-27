"""限购到期、状态恢复和绘图的离线回归测试；测试产物保留在 output 下。"""
import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import git_main
from tools import get_top10_holdings as quotes
from tools.configs.workflow_configs import GITHUB_WORKFLOW_STEPS, SERVICE_WORKFLOW_STEPS


NOW = datetime(2026, 9, 27, 12, tzinfo=ZoneInfo("Asia/Shanghai"))


def record(value, age=0):
    return {"value": value, "fetched_at": (NOW - timedelta(hours=age)).isoformat()}


class LimitChangeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("tools.fund_limit_change"), "限购变化模块尚未实现")
        from tools import fund_limit_change
        self.mod = fund_limit_change
        root = Path(__file__).resolve().parents[1] / "output" / "test_limit_change"
        root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=root))
        self.cache = self.root / "limits.json"
        self.state = self.root / "state.json"
        self.cache.write_text("{}", encoding="utf-8")
        self.refreshed = []

    def write_cache(self, data):
        self.cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def run_auto(self, values=None, fail_image=False):
        def fetch(code, **kwargs):
            self.refreshed.append(code)
            value = (values or {}).get(code, "未知")
            if isinstance(value, Exception):
                raise value
            data = json.loads(self.cache.read_text(encoding="utf-8"))
            if value != "未知":
                data[code] = record(value)
                self.write_cache(data)
            return {"value": value}

        def render(code, event, output_file):
            # 图片写入前，待生成事件必须已持久化。
            state = json.loads(self.state.read_text(encoding="utf-8"))
            self.assertEqual(state[code]["event"]["id"], event["id"])
            self.assertFalse(state[code]["event"].get("generated_at"))
            if fail_image:
                raise OSError("模拟绘图失败")
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_bytes(b"image")
            return output_file

        with patch.object(self.mod, "get_fund_purchase_limit", side_effect=fetch), \
                patch.object(self.mod, "fetch_fund_purchase_limit_bulk_map", return_value={}), \
                patch.object(self.mod, "render_change", side_effect=render):
            return self.mod.run_auto(
                fund_codes=["012922", "007844"], cache_path=self.cache,
                state_path=self.state, output_dir=self.root / "images", now=NOW,
            )

    def test_due_at_72_hours_and_fresh_cache_avoids_fetch(self):
        self.write_cache({"012922": record("1000元", 71.99), "007844": record("500元", 72)})
        self.run_auto({"007844": "100元"})
        self.assertEqual(self.refreshed, ["007844"])
        self.assertFalse((self.root / "images/012922.png").exists())
        self.assertTrue((self.root / "images/007844.png").exists())

    def test_new_fund_initializes_without_image(self):
        self.run_auto({"012922": "1000元", "007844": "暂停申购"})
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["012922"]["baseline"]["value"], "1000元")
        self.assertFalse(list((self.root / "images").glob("*.png")))

    def test_manual_refresh_detected_and_deduplicated(self):
        self.write_cache({"012922": record("1000元", 2), "007844": record("500元", 2)})
        self.run_auto()
        self.write_cache({"012922": record("暂停申购"), "007844": record("500元")})
        self.run_auto()
        image = self.root / "images/012922.png"
        first_mtime = image.stat().st_mtime_ns
        self.run_auto()
        self.assertEqual(image.stat().st_mtime_ns, first_mtime)
        self.assertEqual(self.refreshed, [])

    def test_manual_change_survives_next_due_refresh_reverting_value(self):
        self.write_cache({"012922": record("1000元", 120), "007844": record("500元", 1)})
        self.run_auto({"012922": "1000元"})
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["012922"]["baseline"]["observed_at"] = (NOW - timedelta(hours=100)).isoformat()
        self.state.write_text(json.dumps(state), encoding="utf-8")
        self.write_cache({"012922": record("500元", 80), "007844": record("500元", 1)})
        self.run_auto({"012922": "1000元"}, fail_image=True)
        failed = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertIn("event", failed["012922"], "到期刷新吞掉了手动缓存变化")
        self.assertEqual(failed["012922"]["event"]["new_value"], "500元")
        self.run_auto()
        final = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(final["012922"]["event"]["old_value"], "500元")
        self.assertEqual(final["012922"]["event"]["new_value"], "1000元")
        self.assertTrue(final["012922"]["event"]["generated_at"])

    def test_equivalent_amounts_and_statuses_do_not_trigger(self):
        for old, new in [("1000元", "1,000元"), ("10000元", "1万元"), ("开放申购", "不限额度")]:
            self.assertEqual(self.mod.normalize_limit(old), self.mod.normalize_limit(new))
        self.assertNotEqual(self.mod.normalize_limit("暂停申购"), self.mod.normalize_limit("不限额度"))

    def test_failures_preserve_baseline_and_retry_due_fund(self):
        initial = {"012922": record("1000元", 80), "007844": record("500元", 80)}
        self.write_cache(initial)
        result = self.run_auto({"012922": "未知", "007844": OSError("网络失败")})
        self.assertNotEqual(result, 0)
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8")), initial)
        self.assertFalse(list((self.root / "images").glob("*.png")))
        self.run_auto({"012922": "1000元", "007844": "500元"})
        self.assertEqual(self.refreshed.count("012922"), 2)

    def test_failed_image_is_retried_even_without_new_refresh(self):
        self.write_cache({"012922": record("1000元", 80), "007844": record("500元", 1)})
        self.assertNotEqual(self.run_auto({"012922": "100元"}, fail_image=True), 0)
        self.assertEqual(self.run_auto(), 0)
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertTrue(state["012922"]["event"]["generated_at"])
        self.assertTrue((self.root / "images/012922.png").exists())

    def test_sync_keeps_newer_baseline_and_success_for_same_event(self):
        self.write_cache({"012922": record("1000元", 80), "007844": record("500元", 1)})
        self.run_auto({"012922": "100元"}, fail_image=True)
        pending = json.loads(self.state.read_text(encoding="utf-8"))
        self.run_auto()
        complete = json.loads(self.state.read_text(encoding="utf-8"))
        from sync_repos import merge_fund_limit_change_state_cache
        for a, b in [(pending, complete), (complete, pending)]:
            merged = json.loads(merge_fund_limit_change_state_cache(json.dumps(a), json.dumps(b)))
            self.assertTrue(merged["012922"]["event"]["generated_at"])
        older = copy.deepcopy(pending)
        older["012922"]["baseline"] = {"value": "1000元", "observed_at": "2026-09-20T12:00:00+08:00"}
        merged = json.loads(merge_fund_limit_change_state_cache(json.dumps(complete), json.dumps(older)))
        self.assertEqual(merged["012922"]["baseline"]["value"], "100元")

    def test_sync_newer_initial_baseline_keeps_pending_image(self):
        from sync_repos import merge_fund_limit_change_state_cache
        self.write_cache({"012922": record("1000元", 80), "007844": record("500元", 1)})
        self.run_auto({"012922": "100元"}, fail_image=True)
        pending = json.loads(self.state.read_text(encoding="utf-8"))
        initialized = {"012922": {"baseline": {"value": "100元", "observed_at": "2026-09-28T12:00:00+08:00"}}}
        for a, b in [(pending, initialized), (initialized, pending)]:
            merged = json.loads(merge_fund_limit_change_state_cache(json.dumps(a), json.dumps(b)))
            self.assertIn("event", merged["012922"], "同步丢失了待出图事件")
            self.assertFalse(merged["012922"]["event"].get("generated_at"))
            self.assertEqual(merged["012922"]["baseline"]["observed_at"], "2026-09-28T12:00:00+08:00")

    def test_sync_preserves_unprocessed_newer_observation(self):
        from sync_repos import merge_fund_limit_change_state_cache
        self.write_cache({"012922": record("1000元", 80), "007844": record("500元", 1)})
        self.run_auto({"012922": "100元"}, fail_image=True)
        pending = json.loads(self.state.read_text(encoding="utf-8"))
        # 另一台机器只建立了较新基线；合并后必须最终画出 100 -> 500，而非停在旧的 100。
        newer = {"012922": {"baseline": {"value": "500元", "observed_at": "2026-09-27T12:01:00+08:00"}}}
        combined = merge_fund_limit_change_state_cache(json.dumps(pending), json.dumps(newer))
        self.state.write_text(combined, encoding="utf-8")
        self.write_cache({"012922": {"value": "500元", "fetched_at": "2026-09-27T12:01:00+08:00"},
                          "007844": record("500元", 1)})
        self.run_auto({"012922": "500元"})
        result = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(result["012922"]["event"]["new_value"], "500元")
        # 双方均只初始化、没有事件字段时，也要保留这次跨机器发现的变化。
        first = {"012922": {"baseline": {"value": "100元", "observed_at": "2026-09-26T12:00:00+08:00"}}}
        initialized = json.loads(merge_fund_limit_change_state_cache(json.dumps(first), json.dumps(newer)))
        self.assertEqual(initialized["012922"]["observations"][0]["value"], "500元")

    def test_restored_subscription_generates_new_event(self):
        self.write_cache({"012922": record("暂停申购", 80), "007844": record("500元", 1)})
        self.run_auto({"012922": "不限额度"})
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["012922"]["event"]["old_value"], "暂停申购")
        self.assertEqual(state["012922"]["event"]["new_value"], "不限额度")

    def test_mail_snapshot_only_sees_new_or_updated_image(self):
        self.write_cache({"012922": record("1000元", 80), "007844": record("500元", 1)})
        before = git_main.snapshot_images(self.root / "images")
        self.run_auto({"012922": "100元"})
        after = git_main.snapshot_images(self.root / "images")
        self.assertEqual(git_main.changed_images(before, after), [self.root / "images/012922.png"])
        self.run_auto()
        self.assertEqual(git_main.changed_images(after, git_main.snapshot_images(self.root / "images")), [])

    def test_cached_holdings_selects_latest_period_without_network(self):
        from tools import fund_limit_change_image as drawing
        path = self.root / "holdings.json"
        rows = [{"股票代码": "AAPL", "股票名称": "苹果", "占净值比例": 9.5, "季度": "2026年2季度股票投资明细"},
                {"股票代码": "OLD", "股票名称": "旧持仓", "占净值比例": 90, "季度": "2026年1季度股票投资明细"}]
        path.write_text(json.dumps({"012922:top10": {"data_json": json.dumps(rows), "latest_quarter_key": 20262,
                                                   "latest_quarter_label": "2026年2季度股票投资明细"}}), encoding="utf-8")
        with patch.object(drawing, "FUND_HOLDINGS_CACHE", path), \
                patch.object(quotes, "fetch_fund_stock_holdings_frames", side_effect=AssertionError("禁止联网")):
            period, actual = drawing.cached_holdings("012922")
        self.assertEqual(period, "2026年2季度股票投资明细")
        self.assertEqual(len(actual), 1)
        self.assertEqual(actual[0]["占净值比例"], 9.5)

    def test_new_state_is_in_readonly_cache_check(self):
        import check_project
        path = self.root / "fund_limit_change_state.json"
        path.write_text(json.dumps({"999999": {"baseline": {"value": "100元"}}}), encoding="utf-8")
        before = path.read_bytes()
        items = check_project.check_cache_growth_policy(cache_dir=self.root, now=NOW, active_fund_codes={"012922"})
        self.assertTrue(any("fund_limit_change_state.json" in item.detail for item in items))
        self.assertTrue(any("999999" in item.detail for item in items))
        self.assertEqual(path.read_bytes(), before)


class LimitIntegrationTests(unittest.TestCase):
    def test_every_workflow_window_includes_limit_step(self):
        for config in (GITHUB_WORKFLOW_STEPS, SERVICE_WORKFLOW_STEPS):
            steps = git_main.resolve_workflow_steps(config)
            for hour in (7, 9, 12, 15, 18, 23):
                for holiday in (False, True):
                    selected = git_main.select_workflow_steps_for_time(steps, NOW.replace(hour=hour), service_holiday=holiday)
                    names = [s.script_path.name for s in selected]
                    self.assertIn("fund_limit_change.py", names)
                    self.assertTrue(next(s for s in selected if s.script_path.name == "fund_limit_change.py").collect_images)

    def test_core_unknown_response_does_not_create_success_timestamp(self):
        writes = []
        with patch.object(quotes, "_load_json_cache", return_value={}), \
                patch.object(quotes, "_save_json_cache", side_effect=lambda *args: writes.append(args)), \
                patch.object(quotes, "get_fund_purchase_limit_uncached_detail", return_value={"value": "未知"}):
            result = quotes.get_fund_purchase_limit("012922", return_detail=True)
        self.assertEqual(result["value"], "未知")
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()
