"""最新季度持仓、季度累计编号及邮件收集的离线回归测试。"""
import copy
import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import git_main
import sync_repos
from tools import fund_holding_change as holding
from tools.email_image_names import build_image_names


def period(key, weight=8):
    label = f"{key // 10}年{key % 10}季度股票投资明细"
    frame = pd.DataFrame([{"序号": 1, "股票代码": "AAPL", "股票名称": "苹果", "市场": "US",
                           "ticker": "AAPL", "占净值比例": weight, "持股数": 100,
                           "持仓市值": 1000, "季度": label, "_quarter_key": key}])
    return holding.HoldingPeriod(key, label, frame, "test")


class LatestHoldingTests(unittest.TestCase):
    def setUp(self):
        # 测试目录保留，不执行递归/批量清理。
        self.root = Path(tempfile.mkdtemp(prefix="ahns-holding-"))
        self.pool = ["017437", "012922", "016702"]
        self.periods = {}
        self.online = {}
        self.state = {}
        self.batch = {}
        self.rendered = []
        self.render_failures = set()
        self.analysed = []

    def run_auto(self, force=""):
        def analyse(*, fund_code, **kwargs):
            self.analysed.append(fund_code)
            value = self.online.get(fund_code, self.periods.get(fund_code))
            if isinstance(value, Exception):
                raise value
            return SimpleNamespace(fund_code=fund_code, fund_name=fund_code, latest=value)

        def render(result, output_file):
            if result.fund_code in self.render_failures:
                raise RuntimeError("模拟绘图失败")
            path = Path(output_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"new-image")
            self.rendered.append((result.fund_code, result.latest.quarter_key, path.name))
            return path

        cache = {f"{code}:top10": {"period": p} for code, p in self.periods.items()}
        stream = io.StringIO()
        with ExitStack() as stack:
            for name, value in {
                "HAIWAI_FUND_CODES": self.pool,
                "HOLDING_CHANGE_LATEST_DIR": self.root / "latest",
                "HOLDING_CHANGE_MANUAL_DIR": self.root / "manual",
            }.items():
                stack.enter_context(patch.object(holding, name, value))
            stack.enter_context(patch.object(holding, "ensure_runtime_dirs"))
            stack.enter_context(patch.object(holding, "_load_json", return_value=cache))
            stack.enter_context(patch.object(holding, "_load_change_state", side_effect=lambda: copy.deepcopy(self.state)))
            stack.enter_context(patch.object(holding, "_load_batch_state", side_effect=lambda: copy.deepcopy(self.batch)))
            stack.enter_context(patch.object(holding, "_save_change_state", side_effect=lambda s: setattr(self, "state", copy.deepcopy(s))))
            stack.enter_context(patch.object(holding, "_save_batch_state", side_effect=lambda s: setattr(self, "batch", copy.deepcopy(s))))
            stack.enter_context(patch.object(holding, "_cached_period_from_item", side_effect=lambda c, n, item: item["period"]))
            stack.enter_context(patch.object(holding, "build_holding_change", side_effect=analyse))
            stack.enter_context(patch.object(holding, "save_holding_change_image", side_effect=render))
            stack.enter_context(patch.object(holding, "relative_path_str", side_effect=str))
            stack.enter_context(redirect_stdout(stream))
            code = holding.run_auto_holding_change(top_n=10, force_fund_code=force)
        markers = [line for line in stream.getvalue().splitlines() if line.startswith("AHNS_HOLDING_CHANGE_RESULT=")]
        self.assertTrue(markers, "必须报告本轮实际成功生成的图片清单")
        return code, json.loads(markers[-1].split("=", 1)[1])

    def test_migration_only_rebuilds_latest_and_does_not_repeat(self):
        self.periods = dict(zip(self.pool, [period(20262), period(20263), period(20263)]))
        self.assertEqual(self.run_auto()[0], 0)
        self.assertEqual(self.rendered, [("012922", 20263, "1_三季度_012922.png"),
                                        ("016702", 20263, "2_三季度_016702.png")])
        self.rendered.clear()
        self.run_auto()
        self.assertEqual(self.rendered, [])
        self.periods["017437"] = period(20263)
        self.run_auto()
        self.assertEqual(self.rendered, [("017437", 20263, "3_三季度_017437.png")])

    def test_failed_rebuild_retries_only_pending_and_keeps_index(self):
        self.periods = {code: period(20263) for code in self.pool}
        self.render_failures = {"017437"}
        code, report = self.run_auto()
        self.assertEqual(code, 1)
        self.assertEqual(len(report["images"]), 2)
        self.render_failures.clear()
        self.rendered.clear()
        self.run_auto()
        self.assertEqual(self.rendered, [("017437", 20263, "1_三季度_017437.png")])

    def test_quarter_switch_and_cross_year_never_regress(self):
        self.periods = {"017437": period(20263), "012922": period(20264)}
        self.run_auto()
        self.assertEqual(self.rendered, [("012922", 20264, "1_四季度_012922.png")])
        self.periods = {"017437": period(20271), "012922": period(20264)}
        self.rendered.clear()
        self.run_auto()
        self.assertEqual(self.rendered, [("017437", 20271, "1_一季度_017437.png")])
        self.periods = {"017437": period(20263)}
        self.rendered.clear()
        self.run_auto()
        self.assertEqual(self.rendered, [])
        self.assertEqual(self.batch["current"]["quarter_key"], 20271)

    def test_online_newer_quarter_is_selected_before_any_rendering(self):
        self.periods = {code: period(20263) for code in self.pool}
        self.online["012922"] = period(20264)
        self.run_auto()
        self.assertEqual(self.rendered, [("012922", 20264, "1_四季度_012922.png")])

    def test_missing_previous_period_fails_without_old_image_fallback(self):
        self.periods = {"017437": period(20263), "012922": period(20262)}
        self.online["017437"] = RuntimeError("缺少相邻上一期")
        code, report = self.run_auto()
        self.assertEqual(code, 1)
        self.assertEqual(report["images"], [])
        self.assertEqual(self.rendered, [])

    def test_same_quarter_change_reuses_index_and_stale_cache_is_not_resent(self):
        self.periods = {"017437": period(20263)}
        self.run_auto()
        self.periods["017437"] = period(20263, weight=9)
        self.rendered.clear()
        self.run_auto()
        self.assertEqual(self.rendered, [("017437", 20263, "1_三季度_017437.png")])
        self.periods["017437"] = period(20262)
        self.rendered.clear()
        self.run_auto()
        self.assertEqual(self.rendered, [])

    def test_forced_old_quarter_is_not_published(self):
        self.periods = {"017437": period(20262), "012922": period(20263)}
        _, report = self.run_auto(force="017437")
        self.assertEqual(report["images"], [])

    def test_pending_analysis_with_only_saved_latest_period_is_retried(self):
        self.periods = {"017437": period(20262)}
        self.state["017437:top10"] = holding._state_entry_from_period(
            fund_code="017437", top_n=10, period=period(20263))
        self.online["017437"] = RuntimeError("数据暂时不可用")
        self.run_auto()
        self.online["017437"] = period(20263)
        self.run_auto()
        self.assertEqual(self.rendered, [("017437", 20263, "1_三季度_017437.png")])

    def test_cache_rows_determine_latest_period_not_stale_metadata(self):
        rows = pd.concat([period(20262).df, period(20263, 9).df]).to_dict("records")
        item = {"data_json": rows, "latest_quarter_key": 20262,
                "latest_quarter_label": "2026年2季度股票投资明细"}
        actual = holding._cached_period_from_item("017437", 10, item)
        self.assertEqual(actual.quarter_key, 20263)
        self.assertEqual(actual.df["占净值比例"].tolist(), [9])

    def test_legacy_completed_state_is_rebuilt_once_and_old_files_are_untouched(self):
        self.periods = {code: period(20263) for code in self.pool}
        self.state = {f"{code}:top10": holding._state_entry_from_period(fund_code=code, top_n=10, period=p)
                      for code, p in self.periods.items()}
        self.batch = {"current": {"quarter_key": 20263, "top_n": 10, "funds": {
            "017437": {"quarter_key": 20263, "index": 9, "image": "output/fund_holding_change/latest/9_017437.png"}}}}
        old = self.root / "latest" / "9_017437.png"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"old-quarter-image")
        self.run_auto()
        self.assertEqual(self.rendered[0][2], "1_三季度_017437.png")
        self.assertEqual(old.read_bytes(), b"old-quarter-image")
        self.rendered.clear()
        self.run_auto()
        self.assertEqual(self.rendered, [])

    def test_all_changed_candidates_are_checked_even_after_newer_online_discovery(self):
        self.periods = {code: period(20262) for code in self.pool}
        self.run_auto()
        self.periods = {code: period(20262, weight=9) for code in self.pool}
        self.online = {"017437": period(20263), "012922": period(20264)}
        self.rendered.clear()
        self.run_auto()
        self.assertEqual(self.rendered, [("012922", 20264, "1_四季度_012922.png")])

    def test_forced_first_upgrade_does_not_skip_other_saved_latest_funds(self):
        self.pool = self.pool[:2]
        self.periods = {code: period(20262) for code in self.pool}
        self.online = {code: period(20263) for code in self.pool}
        self.state = {f"{code}:top10": holding._state_entry_from_period(
            fund_code=code, top_n=10, period=period(20263)) for code in self.pool}
        self.run_auto(force="017437")
        self.rendered.clear()
        self.run_auto()
        self.assertEqual(self.rendered, [("012922", 20263, "2_三季度_012922.png")])


class HoldingMailTests(unittest.TestCase):
    def test_number_and_quarter_survive_mail_renaming(self):
        path = Path("output/fund_holding_change/latest/10_三季度_012922.png")
        self.assertEqual(build_image_names([path]), [path.name])

    def test_mime_attachment_and_caption_keep_number_and_quarter(self):
        from tools.email_send import build_message
        path = Path(tempfile.mkdtemp(prefix="ahns-holding-mime-")) / "1_三季度_017437.png"
        path.write_bytes(b"sample-image")
        msg = build_message("test", "test", [path], sender_email="test@example.com",
                            image_names=[path.name])
        images = [part for part in msg.walk() if part.get_content_maintype() == "image"]
        self.assertEqual([part.get_filename() for part in images], [path.name, path.name])
        self.assertTrue(all(part.get_payload(decode=True) == b"sample-image" for part in images))

    def test_collection_uses_manifest_not_archive_or_mtime(self):
        root = Path(tempfile.mkdtemp(prefix="ahns-holding-mail-"))
        output = root / "output"
        latest = output / "fund_holding_change" / "latest"
        archive = output / "fund_holding_change" / "previous" / "1_017654.png"
        selected = [latest / f"{n}_三季度_{n:06d}.png" for n in (10, 2, 1)]
        old = latest / "1_二季度_012922.png"
        manual = output / "fund_holding_change" / "manual" / "017654.png"
        paths = selected + [archive, old, manual]
        after = {p.resolve(): git_main.ImageState(1, 20) for p in paths}
        marker = "AHNS_HOLDING_CHANGE_RESULT=" + json.dumps({"quarter_key": 20263, "images": [
            {"path": str(p), "quarter_key": 20263, "index": n}
            for p, n in zip(selected, (10, 2, 1))]})
        step = git_main.WorkflowStep("基金持仓变化图", root / "fund_holding_change.py", False, True, ("--auto",))
        with patch.object(git_main, "OUTPUT_DIR", output), patch.object(git_main, "snapshot_images", side_effect=[{}, after]), \
             patch.object(git_main, "stream_script_output", return_value=(0, [marker])):
            result = git_main.run_script(step)
        self.assertEqual([p.name for p in result.changed_images], [selected[2].name, selected[1].name, selected[0].name])


class HoldingSyncTests(unittest.TestCase):
    def batch(self, quarter, funds, version=1):
        return {"rules_version": version, "current": {"quarter_key": quarter, "top_n": 10,
                "batch_key": f"top10:{quarter}", "funds": funds}}

    def item(self, code, index, generated="2026-10-26T12:00:00", **kw):
        return {"fund_code": code, "quarter_key": 20263, "index": index,
                "image": f"output/fund_holding_change/latest/{index}_三季度_{code}.png",
                "generated_at": generated, "fingerprint": "valid", **kw}

    def merge(self, a, b):
        return json.loads(sync_repos.merge_fund_holding_change_batch_state_cache(json.dumps(a), json.dumps(b)))

    def test_merge_repairs_duplicate_indexes_and_is_direction_independent(self):
        a = self.batch(20263, {"017437": self.item("017437", 1)})
        b = self.batch(20263, {"012922": self.item("012922", 1, "2026-10-26T13:00:00")})
        merged = self.merge(a, b)
        self.assertEqual(merged, self.merge(b, a))
        funds = merged["current"]["funds"]
        self.assertEqual(funds["017437"]["index"], 1)
        self.assertEqual(funds["012922"]["index"], 2)
        self.assertTrue(funds["012922"]["needs_rebuild"])

    def test_completed_upgrade_is_not_replaced_by_legacy_or_pending_copy(self):
        done = self.batch(20263, {"017437": self.item("017437", 1)})
        pending = self.batch(20263, {"017437": self.item("017437", 1, needs_rebuild=True)})
        legacy = self.batch(20263, {"017437": self.item("017437", 8)}, version=0)
        for other in (pending, legacy):
            merged = self.merge(done, other)
            self.assertFalse(merged["current"]["funds"]["017437"].get("needs_rebuild", False))
            self.assertEqual(merged["current"]["funds"]["017437"]["index"], 1)

    def test_newest_quarter_survives_previous_and_current_slot_mismatch(self):
        old = self.batch(20262, {})
        old["previous"] = self.batch(20263, {"017437": self.item("017437", 1)})["current"]
        merged = self.merge(old, self.batch(20262, {}))
        self.assertEqual(merged["current"]["quarter_key"], 20263)


class HoldingDiskIntegrationTests(unittest.TestCase):
    def test_newer_online_quarter_blocks_old_publication_even_if_comparison_fails(self):
        root = Path(tempfile.mkdtemp(prefix="ahns-holding-incomplete-"))
        cache = root / "holdings.json"
        cache.write_text(json.dumps({f"{code}:top10": {"data_json": period(20262).df.to_json(orient="records")}
                                     for code in ("017437", "012922")}), encoding="utf-8")
        def online(code, **kwargs):
            if code == "017437":
                return period(20263).df
            return pd.concat([period(20261).df, period(20262).df], ignore_index=True)
        stream = io.StringIO()
        with ExitStack() as stack:
            for name, value in {"HAIWAI_FUND_CODES": ["017437", "012922"], "FUND_HOLDINGS_CACHE": cache,
                                "FUND_HOLDING_CHANGE_STATE_CACHE": root / "state.json",
                                "FUND_HOLDING_CHANGE_BATCH_STATE_CACHE": root / "batches.json",
                                "HOLDING_CHANGE_LATEST_DIR": root / "latest"}.items():
                stack.enter_context(patch.object(holding, name, value))
            stack.enter_context(patch.object(holding, "ensure_runtime_dirs"))
            stack.enter_context(patch.object(holding, "_fetch_recent_holdings", side_effect=online))
            stack.enter_context(patch.object(holding, "_fetch_specific_year_holdings", side_effect=online))
            stack.enter_context(patch.object(holding, "_fund_name_from_cache_or_fallback", return_value="测试基金"))
            render = stack.enter_context(patch.object(holding, "save_holding_change_image", side_effect=lambda result, path: path))
            stack.enter_context(redirect_stdout(stream))
            self.assertEqual(holding.run_auto_holding_change(top_n=10), 1)
        report = json.loads(stream.getvalue().split("AHNS_HOLDING_CHANGE_RESULT=")[-1])
        self.assertEqual(report["quarter_key"], 20263)
        self.assertEqual(report["images"], [])
        render.assert_not_called()
        batch = json.loads((root / "batches.json").read_text(encoding="utf-8"))
        self.assertTrue(batch["current"]["funds"]["017437"]["needs_rebuild"])

    def test_real_cache_comparison_png_and_restart_are_latest_only(self):
        """只替换网络边界；真实解析、比较、PNG 和状态写入都在临时目录执行。"""
        from PIL import Image
        root = Path(tempfile.mkdtemp(prefix="ahns-holding-disk-"))
        cache = root / "holdings.json"
        state, batches = root / "state.json", root / "batches.json"
        current = period(20263, 9)
        cache.write_text(json.dumps({"017437:top10": {"data_json": current.df.to_json(orient="records"),
                         "latest_quarter_key": 20263}}), encoding="utf-8")
        frames = pd.concat([period(20262).df, current.df], ignore_index=True)
        with ExitStack() as stack:
            for name, value in {"HAIWAI_FUND_CODES": ["017437"], "FUND_HOLDINGS_CACHE": cache,
                                "FUND_HOLDING_CHANGE_STATE_CACHE": state,
                                "FUND_HOLDING_CHANGE_BATCH_STATE_CACHE": batches,
                                "HOLDING_CHANGE_LATEST_DIR": root / "latest",
                                "MARK_IMAGE": root / "absent-logo.jpg"}.items():
                stack.enter_context(patch.object(holding, name, value))
            stack.enter_context(patch.object(holding, "ensure_runtime_dirs"))
            stack.enter_context(patch.object(holding, "_fetch_recent_holdings", return_value=frames))
            stack.enter_context(patch.object(holding, "_fund_name_from_cache_or_fallback", return_value="测试基金"))
            stream = stack.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(holding.run_auto_holding_change(top_n=10), 0)
            image_path = root / "latest" / "1_三季度_017437.png"
            with Image.open(image_path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.width, holding.HOLDING_CHANGE_IMAGE_STYLE["canvas_width_px"])
            image_time = image_path.stat().st_mtime_ns
            state_before = state.read_bytes()
            stream.seek(0)
            stream.truncate(0)
            self.assertEqual(holding.run_auto_holding_change(top_n=10), 0)
            report = json.loads(stream.getvalue().split("AHNS_HOLDING_CHANGE_RESULT=")[-1])
            self.assertEqual(report["images"], [])
            self.assertEqual(image_path.stat().st_mtime_ns, image_time)
            self.assertEqual(state.read_bytes(), state_before)


if __name__ == "__main__":
    unittest.main()
