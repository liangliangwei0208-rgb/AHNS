"""Service十年估值图：北京时间窗口、每天一次成功出图和失败重试。"""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import git_main
from tools.configs.workflow_configs import GITHUB_WORKFLOW_STEPS, SERVICE_WORKFLOW_STEPS


class GdpServiceTests(unittest.TestCase):
    def step(self):
        matches = [s for s in git_main.resolve_workflow_steps(SERVICE_WORKFLOW_STEPS) if s.script_path.name == "gdp.py"]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_only_service_selects_gdp_in_same_window_and_special_branches(self):
        self.assertFalse(any(s["script"] == "strategy/gdp.py" for s in GITHUB_WORKFLOW_STEPS))
        step = self.step()
        self.assertTrue(step.once_per_day)
        self.assertTrue(step.collect_images)
        self.assertIn("--no-show", step.args)
        steps = git_main.resolve_workflow_steps(SERVICE_WORKFLOW_STEPS)
        for h, m, s, expected in ((11,29,59,False), (11,30,0,True), (12,0,0,True), (18,0,0,True), (22,45,0,True), (23,50,59,True), (23,51,0,False)):
            for options in ({}, {"service_holiday": True}, {"service_first_reopen": True}):
                selected = git_main.select_workflow_steps_for_time(steps, datetime(2026,10,1,h,m,s), **options)
                self.assertEqual(any(s.script_path.name == "gdp.py" for s in selected), expected)

    def test_success_is_persistent_once_per_beijing_day(self):
        from tools import service_daily_step
        step = self.step()
        output = git_main.OUTPUT_DIR / "a_share_market_cap_gdp_10y.png"
        with tempfile.TemporaryDirectory() as tmp, patch.object(service_daily_step, "STATE_DIR", Path(tmp)), \
             patch.object(git_main, "datetime", wraps=datetime) as clock, \
             patch.object(git_main, "stream_script_output", return_value=(0, [])) as launch, \
             patch.object(git_main, "snapshot_images", side_effect=[{}, {output: git_main.ImageState(1,100)}, {}, {output: git_main.ImageState(2,100)}]):
            clock.now.return_value = datetime(2026,10,1,12,0,tzinfo=git_main.BJ_TZ)
            first = git_main.run_script(step)
            second = git_main.run_script(step)
            self.assertEqual(first.changed_images, [output])
            self.assertTrue(second.success)
            self.assertFalse(second.changed_images)
            self.assertEqual(launch.call_count, 1)
            self.assertTrue(list(Path(tmp).glob("*.json")))
            clock.now.return_value = datetime(2026,10,2,12,0,tzinfo=git_main.BJ_TZ)
            third = git_main.run_script(step)
            self.assertEqual(third.changed_images, [output])
            self.assertEqual(launch.call_count, 2)
        self.assertEqual(git_main.unique_images([first,second]), [output])

    def test_failure_or_no_updated_image_does_not_consume_day(self):
        from tools import service_daily_step
        step = self.step()
        output = git_main.OUTPUT_DIR / "a_share_market_cap_gdp_10y.png"
        with tempfile.TemporaryDirectory() as tmp, patch.object(service_daily_step, "STATE_DIR", Path(tmp)), \
             patch.object(git_main, "datetime", wraps=datetime) as clock, \
             patch.object(git_main, "stream_script_output", side_effect=[(1,["failed"]),(0,[]),(0,[])]) as launch, \
             patch.object(git_main, "snapshot_images", side_effect=[{}, {}, {}, {}, {}, {output:git_main.ImageState(1,100)}]):
            clock.now.return_value = datetime(2026,10,1,12,0,tzinfo=git_main.BJ_TZ)
            self.assertFalse(git_main.run_script(step).success)
            self.assertFalse(list(Path(tmp).glob("*.json")))
            git_main.run_script(step)
            self.assertFalse(list(Path(tmp).glob("*.json")))
            self.assertTrue(git_main.run_script(step).success)
            self.assertTrue(list(Path(tmp).glob("*.json")))
            self.assertEqual(launch.call_count, 3)

    def test_overlap_lock_prevents_duplicate_and_recovers_after_release(self):
        from tools import service_daily_step
        with tempfile.TemporaryDirectory() as tmp, patch.object(service_daily_step,"STATE_DIR",Path(tmp)):
            with service_daily_step.DailyStepGuard("strategy/gdp.py") as first:
                self.assertTrue(first.acquired)
                with service_daily_step.DailyStepGuard("strategy/gdp.py") as second:
                    self.assertFalse(second.acquired)
            with service_daily_step.DailyStepGuard("strategy/gdp.py") as after:
                self.assertTrue(after.acquired)

    def test_service_cli_uses_agg_without_loading_data(self):
        from strategy import gdp
        with patch.object(gdp, "main") as calculate:
            gdp._run_cli(["--no-show"])
        self.assertEqual(gdp.plt.get_backend().lower(), "agg")
        calculate.assert_called_once()

    def test_unwritable_local_state_is_a_step_failure_not_workflow_exception(self):
        from tools import service_daily_step
        step = self.step()
        with patch.object(git_main, "step_can_start", return_value=True), \
             patch.object(service_daily_step.DailyStepGuard, "__enter__", side_effect=PermissionError("state directory denied")), \
             patch.object(git_main, "stream_script_output") as launch:
            result = git_main.run_script(step)
        self.assertFalse(result.success)
        self.assertIn("state directory denied", result.error_message)
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
