"""Service 窗口与邮件短名：不运行外部脚本，不发送邮件。"""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import git_main
from tools.configs.workflow_configs import GITHUB_WORKFLOW_STEPS, SERVICE_WORKFLOW_STEPS


class ServiceTests(unittest.TestCase):
    def test_service_only_inclusive_minute_and_all_observation_branches(self):
        self.assertFalse(any(x["script"] == "strategy/gu_zhai_xi.py" for x in GITHUB_WORKFLOW_STEPS))
        steps = git_main.resolve_workflow_steps(SERVICE_WORKFLOW_STEPS)
        for h, m, s, expected in ((11,29,59,False), (11,30,0,True), (12,0,0,True),
                                   (18,0,0,True), (22,45,0,True), (23,50,59,True), (23,51,0,False)):
            for special in ({}, {"service_holiday": True}, {"service_first_reopen": True}):
                chosen = git_main.select_workflow_steps_for_time(steps, datetime(2026,10,1,h,m,s), **special)
                target = [x for x in chosen if x.script_path.name == "gu_zhai_xi.py"]
                self.assertEqual(bool(target), expected, (h,m,s,special))
                if target:
                    self.assertTrue(target[0].collect_images)
                    self.assertIn("--no-show", target[0].args)

    def test_actual_start_outside_window_skips_subprocess(self):
        step = next(x for x in git_main.resolve_workflow_steps(SERVICE_WORKFLOW_STEPS) if x.script_path.name == "gu_zhai_xi.py")
        with patch.object(git_main, "step_can_start", return_value=False), \
             patch.object(git_main, "stream_script_output") as launch:
            result = git_main.run_script(step)
        launch.assert_not_called()
        self.assertTrue(result.success)
        self.assertFalse(result.changed_images)

    def test_slow_image_scan_crossing_window_does_not_launch(self):
        step = next(x for x in git_main.resolve_workflow_steps(SERVICE_WORKFLOW_STEPS) if x.script_path.name == "gu_zhai_xi.py")
        with patch.object(git_main, "step_can_start", side_effect=[True, False]), \
             patch.object(git_main, "snapshot_images", return_value={}), \
             patch.object(git_main, "stream_script_output", return_value=(0,[])) as launch:
            result = git_main.run_script(step)
        launch.assert_not_called()
        self.assertTrue(result.success)

    def test_no_show_cli_keeps_manual_default(self):
        from strategy import gu_zhai_xi
        with patch("sys.argv", ["gu_zhai_xi.py", "--no-show"]):
            self.assertFalse(gu_zhai_xi.parse_args().show_plot)
        with patch("sys.argv", ["gu_zhai_xi.py"]):
            self.assertTrue(gu_zhai_xi.parse_args().show_plot)

    def test_no_show_both_plot_modes_save_without_gui(self):
        import matplotlib.pyplot as plt
        import numpy as np
        import pandas as pd
        from strategy import gu_zhai_xi
        dates = pd.bdate_range("2026-01-01", periods=35)
        data = pd.DataFrame({"date": dates, "index_close": np.linspace(100,130,35),
                             "spread": np.sin(np.arange(35)), "mean": 0., "upper": 1., "lower": -1.,
                             "pe_ttm": 20., "cn10y": 2.})
        with tempfile.TemporaryDirectory() as tmp, patch.object(plt, "show") as show:
            for plot in (gu_zhai_xi.plot_video_style, gu_zhai_xi.plot_dual_axis):
                output = Path(tmp)/(plot.__name__+".png")
                plot(data, 1, 500, 1.95, output, visible_indices=("shenzhen",), show_plot=False)
                self.assertGreater(output.stat().st_size, 1000)
            show.assert_not_called()

    def test_actual_start_window_rechecks_clock(self):
        step = next(x for x in git_main.resolve_workflow_steps(SERVICE_WORKFLOW_STEPS) if x.script_path.name == "gu_zhai_xi.py")
        self.assertTrue(git_main.step_can_start(step, datetime(2026,10,1,23,50,59)))
        self.assertFalse(git_main.step_can_start(step, datetime(2026,10,1,23,51)))


class EmailNameTests(unittest.TestCase):
    def test_fixed_dynamic_unknown_and_duplicate_names(self):
        from tools.email_image_names import build_image_names
        paths = [Path("output/shenzhen_component_analysis.png"), Path("output/csi_2000_analysis.png"),
                 Path("output/equity_bond_spread_10y_500d_1.95sigma_video.png"),
                 Path("output/fund_holding_change/latest/1_012922.png"),
                 Path("output/fund_holding_change/manual/012922.png"),
                 Path("output/fund_region_allocation/latest/3_海外基金地区分布.png"),
                 Path("output/fund_limit_change/latest/007844.png"), Path("output/very_long_unknown_filename.png")]
        names = build_image_names(paths, {paths[-1].resolve(): "基金持仓变化图"})
        self.assertEqual(names[:7], ["深成指ETF.png", "中证2000ETF.png", "股债利差.png", "持仓_012922.png",
                                    "持仓_012922_2.png", "地区分布_3.png", "限购_007844.png"])
        self.assertLess(len(names[-1]), 22)
        self.assertEqual(len(set(names)), len(names))

    def test_mime_names_captions_and_unchanged_image_bytes(self):
        from tools.email_send import build_message
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp)/"long_original_name.png"
            raw = b"sample image bytes"
            image.write_bytes(raw)
            msg = build_message("subject", "body", [image], sender_email="test@example.com", to_email="to@example.com",
                                image_names=["深成指ETF.png"])
            parts = [x for x in msg.walk() if x.get_content_maintype() == "image"]
            self.assertEqual([x.get_filename() for x in parts], ["深成指ETF.png"]*2)
            self.assertTrue(all(x.get_payload(decode=True) == raw for x in parts))
            html = next(x for x in msg.walk() if x.get_content_type() == "text/html").get_content()
            self.assertIn("深成指ETF", html)
            self.assertTrue(image.exists())
            with self.assertRaises(ValueError):
                build_message("s", "t", [image], sender_email="test@example.com", image_names=[])

    def test_attachments_only_and_caption_escaping(self):
        from tools.email_send import build_message
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp)/"original.png"
            image.write_bytes(b"image")
            msg = build_message("s", "t", [image], sender_email="test@example.com", embed_images=False,
                                image_names=["股债利差.png"])
            self.assertFalse(any(p.get_content_type()=="text/html" for p in msg.walk()))
            self.assertEqual([p.get_filename() for p in msg.iter_attachments()], ["股债利差.png"])
            escaped = build_message("s", "t", [image], sender_email="test@example.com", image_names=["<test>.png"])
            html = next(p for p in escaped.walk() if p.get_content_type()=="text/html").get_content()
            self.assertIn("&lt;test&gt;", html)
            self.assertNotIn("<test>", html)


if __name__ == "__main__":
    unittest.main()
