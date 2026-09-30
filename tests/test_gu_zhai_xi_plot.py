"""股债利差展示层验收：真实单位、分层阅读、文本避让和只读出图。"""
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stderr
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure
import numpy as np
import pandas as pd
from PIL import Image
from strategy import gu_zhai_xi as chart


class SpreadPlotTests(unittest.TestCase):
    def setUp(self):
        dates = pd.bdate_range("2016-09-30", "2026-09-30")
        t = np.linspace(0, 24, len(dates))
        self.data = pd.DataFrame({"date": dates, "index_close": 10000+2500*np.sin(t)+t*70,
                                  "csi2000_close": 2000+650*np.sin(t+.4),
                                  "shanghai_close": 3000+450*np.sin(t+.9),
                                  "spread": .8+1.4*np.sin(t/2), "mean": .8,
                                  "upper": 1.8, "lower": -.2, "pe_ttm": 25., "cn10y": 2.})

    def tearDown(self):
        plt.close("all")

    def draw(self, visible=("shenzhen", "csi2000", "shanghai")):
        with patch.object(Figure, "savefig"), patch.object(plt, "close") as closed:
            chart.plot_video_style(self.data, 10, 500, 1.95, Path("output/test.png"),
                                   visible_indices=visible, show_plot=False)
        return closed.call_args.args[0]

    def test_comparison_and_spread_have_separate_true_units_and_shared_dates(self):
        fig = self.draw()
        self.assertEqual(len(fig.axes), 2)
        price, spread = fig.axes
        self.assertTrue(price.get_shared_x_axes().joined(price, spread))
        self.assertGreater(price.get_position().y0, spread.get_position().y1)
        self.assertIn("等价点位", price.get_ylabel())
        self.assertIn("百分点", spread.get_ylabel())
        line = next(line for line in spread.lines if line.get_label() == "股债利差")
        np.testing.assert_allclose(line.get_ydata(), self.data.spread)
        self.assertEqual(sum(isinstance(c, LineCollection) for c in price.collections), 3)
        self.assertEqual(sum(isinstance(c, LineCollection) for c in spread.collections), 0)
        self.assertTrue(any(c.get_gid() == "spread-channel" for c in spread.collections))

    def test_consecutive_same_state_is_one_stroke_and_market_styles_differ(self):
        frame = self.data.iloc[:8].copy()
        frame["spread"] = 0.
        frame["upper"], frame["lower"] = 1., -1.
        frame.loc[3, "index_close"] = np.nan
        fig, ax = plt.subplots()
        item = chart.draw_colored_index(ax, frame, key="shenzhen")
        self.assertEqual([len(s) for s in item.get_segments()], [3, 4])
        styles = []
        for key in ("shenzhen", "csi2000", "shanghai"):
            frame[f"{key}_display"] = frame.index_close
            item = chart.draw_colored_index(ax, frame, key=key)
            styles.append(repr(item.get_linestyles()))
        self.assertEqual(len(set(styles)), 3)

    def test_headers_legends_and_metrics_stay_outside_curves_without_overlaps(self):
        fig = self.draw()
        for dpi in (100, 600):
            fig.set_dpi(dpi)
            fig.canvas.draw()
            renderer = fig.canvas.get_renderer()
            artists = fig.texts + fig.legends
            boxes = [a.get_window_extent(renderer) for a in artists if a.get_visible()]
            for box in boxes:
                self.assertGreaterEqual(box.x0, 0)
                self.assertGreaterEqual(box.y0, 0)
                self.assertLessEqual(box.x1, fig.bbox.x1)
                self.assertLessEqual(box.y1, fig.bbox.y1)
                self.assertFalse(any(box.overlaps(ax.get_window_extent(renderer)) for ax in fig.axes))
            for i, box in enumerate(boxes):
                self.assertFalse(any(box.overlaps(other) for other in boxes[i+1:]))
            ticks = [t for t in fig.axes[1].get_xticklabels() if t.get_visible()]
            self.assertLessEqual(len(ticks), 8)
            self.assertTrue(all(len(t.get_text()) == 4 for t in ticks))

    def test_chinese_text_can_be_redrawn_after_local_style_context_exits(self):
        fig = self.draw()
        with warnings.catch_warnings():
            warnings.filterwarnings("error", message="Glyph .* missing from font")
            fig.canvas.draw()

    def test_hiding_all_indices_leaves_one_visible_spread_panel(self):
        fig = self.draw(visible=())
        axes = [axis for axis in fig.axes if axis.get_visible()]
        self.assertEqual(len(axes), 1)
        self.assertIn("百分点", axes[0].get_ylabel())
        self.assertGreater(axes[0].get_position().height, .5)

    def test_toggles_inputs_and_global_style_are_preserved_without_network(self):
        original = self.data.copy(deep=True)
        style = dict(matplotlib.rcParams)
        with patch("requests.sessions.Session.request", side_effect=AssertionError("绘图不得联网")):
            fig = self.draw(visible=("shenzhen",))
        self.assertEqual(sum(isinstance(c, LineCollection) for c in fig.axes[0].collections), 1)
        self.assertNotIn("中证2000", [t.get_text() for legend in fig.legends for t in legend.get_texts()])
        pd.testing.assert_frame_equal(self.data, original)
        self.assertEqual(dict(matplotlib.rcParams), style)

    def test_only_png_is_saved_without_gui(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(plt, "show") as show:
            output = Path(tmp)/"spread.png"
            chart.plot_video_style(self.data, 10, 500, 1.95, output, show_plot=False)
            with Image.open(output) as raster:
                self.assertEqual(raster.size, (4320, 2940))
                self.assertAlmostEqual(raster.info["dpi"][0], 600, places=1)
            self.assertFalse(output.with_suffix(".pdf").exists())
            self.assertFalse(output.with_suffix(".svg").exists())
            show.assert_not_called()

    def test_vector_output_paths_are_rejected_in_both_modes_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            for suffix in (".pdf", ".svg"):
                output = Path(tmp)/("spread"+suffix)
                for plot in (chart.plot_video_style, chart.plot_dual_axis):
                    with self.assertRaises(ValueError):
                        plot(self.data, 10, 500, 1.95, output, show_plot=False)
                self.assertFalse(output.exists())
                with patch("sys.argv", ["gu_zhai_xi.py", "--output", str(output)]), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        chart.parse_args()
                self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
