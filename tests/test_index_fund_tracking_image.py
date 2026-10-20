"""纯绘图验收：真实Logo、中文标的、文字边界、负向条形和N/A。"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.paths import MARK_IMAGE


class TrackingImageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("tools.index_fund_tracking_image"))
        from tools.index_fund_tracking_image import render_index_fund_limit_change
        self.render = render_index_fund_limit_change
        self.root = Path(__file__).parent / "artifacts" / "index_fund_tracking"
        self.root.mkdir(parents=True, exist_ok=True)
        self.profile = {"fund_code": "021000", "fund_name": "南方纳斯达克100指数发起(QDII)I", "currency": "CNY",
                        "index_name_cn": "纳斯达克100指数", "index_name_en": "NASDAQ-100", "index_symbol": ".NDX"}
        self.event = {"id": "fixed-test-event", "old_value": "1000元", "new_value": "100元", "detected_at": "2026-10-14T12:00:00+08:00"}
        self.comparison = {"comparison_end_date": "2026-10-12", "fund_nav_date": "2026-10-12", "benchmark_close_date": "2026-10-13",
                           "periods": {f"{m}m": {"fund_return": f, "index_return": i, "start_date": "2025-10-10", "end_date": "2026-10-12"}
                                       for m, f, i in ((1, -3.5, 4), (3, 0, None), (6, 14, -2), (12, None, None))}}

    def image(self, name="fixed", logo=MARK_IMAGE, profile=None):
        report = {}
        path = self.root / f"{name}.png"
        self.render(profile or self.profile, self.event, self.comparison, path, logo_path=logo, layout_report=report)
        return path, report

    def test_logo_aspect_and_output_width(self):
        path, report = self.image()
        with Image.open(path) as image:
            self.assertEqual(image.width, 1080)
            self.assertEqual(image.info["index_fund_event_id"], self.event["id"])
        with Image.open(MARK_IMAGE) as logo:
            box = report["logo"]
            self.assertAlmostEqual((box[2]-box[0])/(box[3]-box[1]), logo.width/logo.height, delta=.01)

    def test_all_text_geometrically_centered_and_inside_boxes(self):
        _, report = self.image("centering")
        for item in report["texts"]:
            with self.subTest(text=item["text"]):
                x0, y0, x1, y1 = item["box"]
                a, b, c, d = item["bounds"]
                self.assertGreaterEqual(a, x0-1); self.assertLessEqual(c, x1+1)
                self.assertGreaterEqual(b, y0-1); self.assertLessEqual(d, y1+1)
                self.assertAlmostEqual((b+d)/2, (y0+y1)/2, delta=1)

    def test_negative_zero_and_na_share_axis(self):
        _, report = self.image("negative")
        self.assertEqual(len(report["bars"]), 8)
        scales = {tuple(b["scale"]) for b in report["bars"]}
        self.assertEqual(len(scales), 1)
        for bar in report["bars"]:
            if bar["value"] is None:
                self.assertIsNone(bar["end_x"])
            elif bar["value"] < 0:
                self.assertLess(bar["end_x"], bar["zero_x"])
            elif bar["value"] > 0:
                self.assertGreater(bar["end_x"], bar["zero_x"])
            else:
                self.assertEqual(bar["end_x"], bar["zero_x"])

    def test_chinese_index_four_periods_no_holdings_or_demo(self):
        _, report = self.image("labels")
        text = "\n".join(t["text"] for t in report["texts"])
        self.assertIn("纳斯达克100指数", text)
        self.assertIn("2026-10-12", text)
        self.assertNotIn("来源：", text)
        self.assertLessEqual(next(t for t in report["texts"] if "NASDAQ-100" in t["text"])["bounds"][3], report["header_bottom"]-12)
        for m in (1, 3, 6, 12):
            self.assertIn(f"近{m}月", text)
        for banned in ("十大持仓", "股票代码", "示例数据", "LAYOUT PREVIEW"):
            self.assertNotIn(banned, text)

    def test_long_name_wraps_without_overflow(self):
        _, report = self.image("long-name", profile={**self.profile, "fund_name": "超长基金名称用于自动换行几何验证"*8})
        for item in report["texts"]:
            self.assertLessEqual(item["bounds"][2], item["box"][2]+1)
            self.assertLessEqual(item["bounds"][3], item["box"][3]+1)

    def test_missing_logo_fails_without_damaging_existing_image(self):
        path, _ = self.image("protected")
        before = path.read_bytes()
        with self.assertRaises(FileNotFoundError):
            self.render(self.profile, self.event, self.comparison, path, logo_path=self.root / "no-logo.jpg")
        self.assertEqual(before, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
