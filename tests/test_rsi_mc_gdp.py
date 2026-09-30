"""两行布局与估值背景集成；不联网，不写生产缓存。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd
from tools import rsi_data
from tools.configs.rsi_configs import RSI_ANALYSIS_CONFIGS


class ChartTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=35),
                                   "close": np.linspace(100, 130, 35), "volume": 1000., "RSI": 50.})

    def draw(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp, patch.object(rsi_data.plt, "close") as closed:
            rsi_data.plot_analysis(self.frame, "TEST", output_file=str(Path(tmp)/"chart.png"),
                                  show_plot=False, **kwargs)
            return closed.call_args.args[0]

    def test_exact_panel_counts_and_volume_plot_never_called(self):
        with patch.object(rsi_data, "_plot_volume_bar", side_effect=AssertionError("不能画成交量面板")):
            self.assertEqual(len(self.draw().axes), 2)
            self.assertEqual(len(self.draw(show_rsi_panel=False).axes), 1)

    def test_mc_gdp_does_not_add_an_axis_or_legend_and_preserves_limits(self):
        plain = self.draw()
        macro = pd.DataFrame({"available_at": ["2025-12-31T00:00:00+08:00"], "ratio": [.58], "basis": ["observed"]})
        styled = self.draw(show_mc_gdp=True, mc_gdp_df=macro)
        self.assertEqual(len(styled.axes), 2)
        self.assertEqual(styled.axes[0].get_ylim(), plain.axes[0].get_ylim())
        self.assertEqual(styled.axes[0].get_xlim(), plain.axes[0].get_xlim())
        self.assertEqual(len(styled.axes[0].patches), 1)
        labels = [t.get_text() for t in styled.axes[1].texts]
        self.assertIn("MC/GDP: 0.58 · LOW", labels)
        self.assertFalse(any("MC/GDP" in line.get_label() for line in styled.axes[1].lines))

    def test_unavailable_label_only_when_enabled(self):
        fig = self.draw(show_mc_gdp=True, mc_gdp_df=pd.DataFrame())
        self.assertIn("MC/GDP: N/A", [t.get_text() for t in fig.axes[1].texts])
        self.assertFalse(fig.axes[0].patches)
        self.assertFalse(any("MC/GDP" in t.get_text() for a in self.draw().axes for t in a.texts))

    def test_invalid_macro_metadata_degrades_without_stopping_chart(self):
        macro = pd.DataFrame({"available_at": ["2026-01-01"], "ratio": [.58], "basis": ["observed"]})
        macro.attrs["historical_cutoff"] = "invalid date"
        fig = self.draw(show_mc_gdp=True, mc_gdp_df=macro)
        self.assertIn("MC/GDP: N/A", [t.get_text() for t in fig.axes[1].texts])

    def test_three_metric_rows_are_left_aligned_inside_the_rsi_axis(self):
        macro = pd.DataFrame({"available_at": ["2025-12-31T00:00:00+08:00"], "ratio": [.54], "basis": ["observed"]})
        breadth = pd.DataFrame({"date": self.frame.date, "percent": 23.3, "kind": "close"})
        breadth.attrs["breadth_display"] = {"current": False, "age_sessions": 1, "max_age_sessions": 5}
        fig = self.draw(show_mc_gdp=True, mc_gdp_df=macro, show_breadth=True, breadth_df=breadth)
        fig.canvas.draw()
        axis = fig.axes[1]
        labels = [t for t in axis.texts if t.get_text().startswith(("50D", "R:", "MC/GDP:"))]
        self.assertEqual(len(labels), 3)
        self.assertEqual(len({t.get_position()[0] for t in labels}), 1)
        renderer = fig.canvas.get_renderer()
        for text in labels:
            self.assertLess(text.get_window_extent(renderer).x1, axis.bbox.x1 - 3)
            self.assertEqual(text.get_ha(), "left")
        # 导出180 DPI时文字栅格化宽度稍有变化，也应保留轴内边距。
        fig.set_dpi(180)
        fig.canvas.draw()
        for text in labels:
            self.assertLess(text.get_window_extent(fig.canvas.get_renderer()).x1, axis.bbox.x1 - 3)

    def test_only_two_etfs_are_enabled_and_keep_relationships(self):
        enabled = {c["kwargs"]["symbol"]: c["kwargs"] for c in RSI_ANALYSIS_CONFIGS if c["kwargs"].get("show_mc_gdp")}
        self.assertEqual(set(enabled), {"159943", "560220"})
        self.assertEqual(enabled["159943"]["breadth_key"], "shenzhen")
        self.assertEqual(enabled["560220"]["breadth_key"], "csi2000")
        self.assertTrue(all(c["days"] == 300 for c in enabled.values()))

    def test_builder_loads_macro_once_and_passes_same_frame(self):
        import stock_analysis
        macro = pd.DataFrame()
        with patch("tools.a_share_valuation.load_valuation", return_value=macro) as load, \
             patch("tools.breadth_engine.refresh_for_charts"), \
             patch.object(stock_analysis, "rsi_analyze_index", return_value=(None,)*7) as run, \
             patch.object(stock_analysis, "build_change_summary_text", return_value="ok"):
            stock_analysis.build_stock_analysis()
        self.assertEqual(load.call_count, 1)
        enabled = [call.kwargs for call in run.call_args_list if call.kwargs.get("show_mc_gdp")]
        self.assertEqual(len(enabled), 2)
        self.assertTrue(all(call["mc_gdp_df"] is macro for call in enabled))

    def test_volume_factor_and_email_summary_are_retained(self):
        from stock_analysis import add_quant_factors, format_stock_factor_text, StockAnalysisResult
        hist = self.frame.copy()
        hist.loc[34, "volume"] = 2000.
        factored = add_quant_factors(hist)
        self.assertAlmostEqual(factored.volume_ratio_20.iloc[-1], 2000./1050.)
        result = StockAnalysisResult("TEST", hist, None, None, None, None, None, None)
        self.assertIn("成交量", format_stock_factor_text(result))


if __name__ == "__main__":
    unittest.main()
