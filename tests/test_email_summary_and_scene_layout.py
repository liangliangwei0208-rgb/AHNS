"""邮件摘要与假期观察场景字的回归测试。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image, ImageChops

import git_main
from tools.configs.safe_image_style_configs import safe_scene_text_style
from tools.safe_display import add_scene_text_watermark


class EmailSummaryTests(unittest.TestCase):
    """邮件正文只能呈现运行概况，完整诊断仍留在控制台日志。"""

    @staticmethod
    def _result(
        *,
        name: str,
        script: str,
        return_code: int = 0,
        images: list[Path] | None = None,
        output_tail: list[str] | None = None,
        error_message: str = "",
    ) -> git_main.ScriptResult:
        return git_main.ScriptResult(
            step_name=name,
            script_name=script,
            script_path=Path(script),
            return_code=return_code,
            elapsed_seconds=12.5,
            changed_images=images or [],
            collect_images=True,
            output_tail=output_tail or [],
            error_message=error_message,
        )

    def test_success_email_contains_only_overall_summary(self):
        text = git_main.build_email_text(
            started_at=datetime(2026, 9, 27, 9, 0, 0, tzinfo=git_main.BJ_TZ),
            finished_at=datetime(2026, 9, 27, 9, 2, 5, tzinfo=git_main.BJ_TZ),
            results=[
                self._result(name="RSI与市场分析", script="stock_analysis.py"),
                self._result(
                    name="海外基金正式估算",
                    script="main.py",
                    images=[Path("output/safe_haiwai_fund.png")],
                ),
            ],
            images=[Path("output/safe_haiwai_fund.png")],
        )

        self.assertIn("结果：成功", text)
        self.assertIn("总耗时：2m 05s", text)
        self.assertIn("步骤：成功 2 项，失败 0 项", text)
        self.assertIn("图片：1 张", text)
        self.assertNotIn("stock_analysis.py", text)
        self.assertNotIn("output/safe_haiwai_fund.png", text)
        self.assertNotIn("【提示】", text)

    def test_failure_email_limits_reason_to_one_short_line(self):
        long_output = "[ERROR] 行情数据源连接失败 " + "x" * 160
        text = git_main.build_email_text(
            started_at=datetime(2026, 9, 27, 9, 0, 0, tzinfo=git_main.BJ_TZ),
            finished_at=datetime(2026, 9, 27, 9, 0, 30, tzinfo=git_main.BJ_TZ),
            results=[
                self._result(
                    name="盘前海外基金观察图",
                    script="premarket_fund.py",
                    return_code=2,
                    output_tail=["准备重试", long_output, "Traceback 仅应保留在控制台"],
                ),
            ],
            images=[],
        )

        self.assertIn("结果：部分完成", text)
        self.assertIn("步骤：成功 0 项，失败 1 项", text)
        self.assertIn("- 盘前海外基金观察图：退出码 2：行情数据源连接失败", text)
        self.assertNotIn("premarket_fund.py", text)
        self.assertNotIn("Traceback 仅应保留在控制台", text)
        self.assertLess(len(text.splitlines()[-1]), 150)

    def test_failure_email_keeps_error_line_when_prefix_is_removed(self):
        text = git_main.build_email_text(
            started_at=datetime(2026, 9, 27, 9, 0, 0, tzinfo=git_main.BJ_TZ),
            finished_at=datetime(2026, 9, 27, 9, 0, 30, tzinfo=git_main.BJ_TZ),
            results=[
                self._result(
                    name="海外基金正式估算",
                    script="main.py",
                    return_code=1,
                    output_tail=[
                        "[ERROR] Connection refused",
                        "[INFO] cleanup complete",
                    ],
                ),
            ],
            images=[],
        )

        self.assertIn("Connection refused", text)
        self.assertNotIn("cleanup complete", text)

    def test_uncaught_exception_email_has_no_traceback(self):
        text = git_main.build_uncaught_exception_email_text(
            entry_name="service_main.py",
            workflow_label="小电脑服务",
            exc=RuntimeError("页面服务不可用\n内部诊断不应放入邮件"),
        )

        self.assertIn("【AHNS 运行异常摘要】", text)
        self.assertIn("异常原因：RuntimeError: 页面服务不可用", text)
        self.assertNotIn("【Traceback】", text)
        self.assertNotIn("内部诊断不应放入邮件", text)


class HolidaySceneLayoutTests(unittest.TestCase):
    """假期场景字按用户标注叠加在基金名称列右侧。"""

    def test_holiday_config_uses_marked_overlay_position(self):
        style = safe_scene_text_style()["safe_holidays.png"]

        self.assertEqual(style["placement"], "overlay")
        self.assertAlmostEqual(style["x_ratio"], 0.495)
        self.assertAlmostEqual(style["y_ratio"], 0.39)
        self.assertEqual(style["font_size"], 150)

    def test_holiday_scene_label_keeps_canvas_and_uses_marked_center(self):
        # 检查实际渲染位置，同时确保取消留白后不扩展图片宽度。
        style = safe_scene_text_style()
        source = Image.new("RGB", (3000, 2400), "#123456")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "safe_holidays.png"
            source.save(path)
            add_scene_text_watermark(path, style_by_filename=style)
            rendered = Image.open(path).convert("RGB")
        self.assertEqual(rendered.size, source.size)
        bounds = ImageChops.difference(rendered, source).getbbox()
        self.assertIsNotNone(bounds)
        assert bounds is not None
        self.assertAlmostEqual((bounds[0]+bounds[2])/2, 3000*0.495, delta=2)
        self.assertAlmostEqual((bounds[1]+bounds[3])/2, 2400*0.39, delta=2)
        self.assertGreater(bounds[1], 0)
        self.assertLess(bounds[3], rendered.height)

    def test_left_gutter_label_stays_inside_short_holiday_image(self):
        """数据行较少时，竖排标签也不能被图片顶部或底部裁切。"""
        style = safe_scene_text_style()
        style["safe_holidays.png"].update(placement="left_gutter", left_gutter_ratio=0.10)
        source = Image.new("RGB", (3000, 767), "#123456")

        with TemporaryDirectory() as directory:
            path = Path(directory) / "safe_holidays.png"
            source.save(path)
            add_scene_text_watermark(path, style_by_filename=style)
            rendered = Image.open(path).convert("RGB")

        gutter_width = 300
        gutter = rendered.crop((0, 0, gutter_width, rendered.height))
        label_bounds = ImageChops.difference(
            gutter,
            Image.new("RGB", gutter.size, "white"),
        ).getbbox()
        self.assertIsNotNone(label_bounds)
        assert label_bounds is not None
        self.assertGreater(label_bounds[1], 0)
        self.assertLess(label_bounds[3], rendered.height)
        self.assertIsNotNone(
            ImageChops.difference(
                rendered.crop((0, 0, gutter_width, 900)),
                Image.new("RGB", (gutter_width, 900), "white"),
            ).getbbox()
        )


if __name__ == "__main__":
    unittest.main()
