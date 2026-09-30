"""独立十年宏观图：验证可读性、真实双轴与只读绘图，不联网。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.figure import Figure
import numpy as np
import pandas as pd
from PIL import Image
from strategy import gdp


class GdpPlotTests(unittest.TestCase):
    def setUp(self):
        self.ratio = pd.DataFrame({"date": pd.date_range("2025-01-31", periods=9, freq="ME"),
                                   "market_cap_to_gdp": [.70, .765, .7649, .6001, .60, .5501, .55, .54, .81]})
        self.index = pd.DataFrame({"date": pd.bdate_range("2025-01-01", "2025-10-31"),
                                   "close": np.linspace(9500, 13000, 218)})

    def tearDown(self):
        plt.close("all")

    def draw(self, ratio=None, index=None):
        # 大部分测试只跳过磁盘导出，真实Matplotlib构图和文字测量仍执行。
        with patch.object(Figure, "savefig"), patch.object(plt, "close") as closed:
            gdp.plot_chart(self.ratio if ratio is None else ratio, self.index if index is None else index)
        return closed.call_args.args[0]

    @staticmethod
    def curves(ax):
        return [line for line in ax.lines if line.get_gid() == "mc-gdp-curve"]

    def test_index_uses_real_points_on_overlapping_right_axis(self):
        fig = self.draw()
        self.assertEqual(len(fig.axes), 2)
        left, right = fig.axes
        self.assertEqual(left.get_position().bounds, right.get_position().bounds)
        self.assertEqual(left.get_ylabel(), "MC/GDP")
        self.assertIn("深证成指", right.get_ylabel())
        lines = [line for line in right.lines if line.get_gid() == "shenzhen-index-curve"]
        observed = {pd.Timestamp(date): value for line in lines for date, value in zip(line.get_xdata(), line.get_ydata())}
        self.assertEqual(observed, dict(zip(self.index.date, self.index.close)))
        self.assertFalse(any(line.get_visible() for line in right.get_ygridlines()))
        self.assertEqual([t.get_text() for t in fig.legends[0].get_texts()], ["MC/GDP", "深证成指"])
        self.assertEqual(len(fig.legends), 1)

    def test_threshold_boundary_colors_and_step_post_are_exact(self):
        fig = self.draw()
        curves = self.curves(fig.axes[0])
        self.assertTrue(curves)
        self.assertTrue(all(line.get_drawstyle() == "steps-post" for line in curves))
        expected = ["#6B7280", "#C43C39", "#6B7280", "#6B7280", "#2E8B57", "#2E8B57", "#14532D", "#14532D"]
        for i, color in enumerate(expected):
            midpoint = mdates.date2num(self.ratio.date.iloc[i]+(self.ratio.date.iloc[i+1]-self.ratio.date.iloc[i])/2)
            value = self.ratio.market_cap_to_gdp.iloc[i]
            hits = []
            for line in curves:
                vertices = line.get_path().vertices
                for a, b in zip(vertices[:-1], vertices[1:]):
                    if a[0] < midpoint < b[0] and a[1] == value and b[1] == value:
                        hits.append(line.get_color())
            self.assertEqual(hits, [color], i)

    def test_jumps_use_new_state_color_and_missing_values_break_lines(self):
        frame = self.ratio.copy()
        frame["market_cap_to_gdp"] = [.70, .60, np.nan, .54, np.inf, .70, .81, np.nan, .58]
        ax = self.draw(ratio=frame).axes[0]
        curves = self.curves(ax)
        x_jump = mdates.date2num(frame.date.iloc[1])
        jumps = []
        for line in curves:
            vertices = line.get_path().vertices
            for a, b in zip(vertices[:-1], vertices[1:]):
                if a[0] == b[0] == x_jump and a[1] == .70 and b[1] == .60:
                    jumps.append(line.get_color())
            for i in (2, 4, 7):
                self.assertFalse(vertices[:, 0].min() < mdates.date2num(frame.date.iloc[i]) < vertices[:, 0].max())
            self.assertTrue(np.isfinite(vertices).all())
        self.assertEqual(jumps, ["#2E8B57"])
        self.assertEqual(max(line.get_path().vertices[:, 0].max() for line in curves), mdates.date2num(frame.date.iloc[-1]))

    def test_index_color_maps_only_already_observed_monthly_valuation(self):
        macro = pd.DataFrame({"date": pd.date_range("2025-01-31", periods=7, freq="ME"),
                              "market_cap_to_gdp": [.70, .80, .58, .54, .70, np.nan, .81]})
        dates = pd.to_datetime(["2025-01-30", "2025-01-31", "2025-02-27", "2025-02-28", "2025-03-28", "2025-03-31",
                                "2025-04-29", "2025-04-30", "2025-05-30", "2025-06-02", "2025-06-30", "2025-07-15", "2025-07-31"])
        price = pd.DataFrame({"date": dates, "close": np.arange(len(dates))*100+10000})
        fig = self.draw(ratio=macro, index=price)
        lines = [line for line in fig.axes[1].lines if line.get_gid() == "shenzhen-index-curve"]
        expected = ["#C2C7CD", "#A1A8B2", "#A1A8B2", "#E27A63", "#E27A63", "#6DAD78",
                    "#6DAD78", "#347A50", "#347A50", "#A1A8B2", "#C2C7CD", "#C2C7CD"]
        for i, color in enumerate(expected):
            middle = mdates.date2num(dates[i]+(dates[i+1]-dates[i])/2)
            hits = [line.get_color() for line in lines if line.get_path().vertices[:, 0].min() < middle < line.get_path().vertices[:, 0].max()]
            self.assertEqual(hits, [color], i)
        self.assertTrue(all(line.is_dashed() for line in lines))
        self.assertTrue(all(line.get_linewidth() < 1.8 for line in lines))
        self.assertTrue(any(np.allclose(points.get_facecolors()[0][:3], matplotlib.colors.to_rgb("#E27A63")) for points in fig.axes[1].collections))

    def test_background_labels_and_axes_include_data_and_thresholds(self):
        fig = self.draw()
        left, right = fig.axes
        self.assertEqual(len(left.patches), 3)
        for state, alpha, color in (("高估", .05, "#C43C39"), ("低估", .05, "#2E8B57"), ("极端低估", .07, "#14532D")):
            self.assertEqual(sum(t.get_text() == state for t in left.texts), 1)
            self.assertTrue(any(p.get_facecolor() == matplotlib.colors.to_rgba(color, alpha) for p in left.patches))
        labels = [t.get_text() for t in left.texts]
        self.assertTrue({"0.765", "0.60", "0.55"}.issubset(labels))
        low, high = left.get_ylim()
        self.assertLessEqual(low, .54)
        self.assertGreaterEqual(high, .81)
        self.assertAlmostEqual(low/.05, round(low/.05))
        self.assertAlmostEqual(high/.05, round(high/.05))
        self.assertLess(right.get_ylim()[0], 9500)
        self.assertGreater(right.get_ylim()[1], 13000)

    def test_input_caches_and_global_style_remain_unchanged(self):
        original_ratio, original_index = self.ratio.copy(deep=True), self.index.copy(deep=True)
        keys = ["font.family", "font.sans-serif", "pdf.fonttype", "svg.fonttype", "axes.unicode_minus", "figure.figsize"]
        style = {key: matplotlib.rcParams[key] for key in keys}
        with tempfile.TemporaryDirectory() as tmp:
            cached = Path(tmp)/"unchanged.csv"
            cached.write_bytes(b"trusted cache")
            with patch.object(gdp, "CAP_CACHE", cached), patch.object(gdp, "GDP_CACHE", cached), \
                 patch.object(gdp, "INDEX_CACHE", cached), patch.object(gdp, "OUTPUT_CSV", cached), \
                 patch("requests.sessions.Session.request", side_effect=AssertionError("绘图不得联网")):
                self.draw()
            self.assertEqual(cached.read_bytes(), b"trusted cache")
        pd.testing.assert_frame_equal(self.ratio, original_ratio)
        pd.testing.assert_frame_equal(self.index, original_index)
        self.assertEqual({key: matplotlib.rcParams[key] for key in keys}, style)

    def test_mixed_chinese_and_latin_text_has_no_missing_glyphs(self):
        with warnings.catch_warnings():
            warnings.filterwarnings("error", message="Glyph .* missing from font")
            fig = self.draw()
            self.assertTrue(fig.axes[1].get_ylabel().startswith("深证成指"))

    def test_labels_fit_page_without_overlaps_at_screen_and_export_dpi(self):
        fig = self.draw()
        for dpi in (100, 600):
            fig.set_dpi(dpi)
            fig.canvas.draw()
            renderer = fig.canvas.get_renderer()
            artists = fig.texts + fig.legends + [t for ax in fig.axes for t in ax.texts]
            boxes = [artist.get_window_extent(renderer) for artist in artists if artist.get_visible()]
            for box in boxes:
                self.assertGreaterEqual(box.x0, 0)
                self.assertGreaterEqual(box.y0, 0)
                self.assertLessEqual(box.x1, fig.bbox.x1)
                self.assertLessEqual(box.y1, fig.bbox.y1)
            for i, first in enumerate(boxes):
                for second in boxes[i+1:]:
                    self.assertFalse(first.overlaps(second))

    def test_only_png_is_exported_at_fixed_publication_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"chart.png"
            with patch.object(gdp, "OUTPUT_PNG", path):
                self.assertIsNone(gdp.plot_chart(self.ratio, self.index))
            with Image.open(path) as raster:
                self.assertEqual(raster.size, (4320, 2520))
                self.assertAlmostEqual(raster.info["dpi"][0], 600, places=1)
            self.assertFalse(path.with_suffix(".pdf").exists())
            self.assertFalse(path.with_suffix(".svg").exists())


if __name__ == "__main__":
    unittest.main()
