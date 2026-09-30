"""两行布局与 MC/GDP 右轴阶梯线；不联网，不写生产缓存。"""
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

    def tearDown(self):
        rsi_data.plt.close("all")

    def draw(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp, patch.object(rsi_data.plt, "close") as closed:
            rsi_data.plot_analysis(self.frame, "TEST", output_file=str(Path(tmp)/"chart.png"),
                                  show_plot=False, **kwargs)
            return closed.call_args.args[0]

    def test_exact_panel_counts_and_volume_plot_never_called(self):
        with patch.object(rsi_data, "_plot_volume_bar", side_effect=AssertionError("不能画成交量面板")):
            self.assertEqual(len(self.draw().axes), 2)
            self.assertEqual(len(self.draw(show_rsi_panel=False).axes), 1)

    def test_mc_gdp_twin_axis_preserves_two_rows_and_price_limits(self):
        plain = self.draw()
        macro = pd.DataFrame({"available_at": ["2025-12-31T00:00:00+08:00"], "ratio": [.58], "basis": ["observed"]})
        styled = self.draw(show_mc_gdp=True, mc_gdp_df=macro)
        self.assertEqual(len(styled.axes), 3)
        self.assertEqual(styled.axes[0].get_ylim(), plain.axes[0].get_ylim())
        self.assertEqual(styled.axes[0].get_xlim(), plain.axes[0].get_xlim())
        self.assertFalse(styled.axes[0].patches)
        self.assertFalse(any(t.get_text() in {"OVER", "LOW", "DEEP LOW"} for t in styled.axes[0].texts))
        self.assertEqual(styled.axes[1].get_position().bounds, styled.axes[2].get_position().bounds)
        self.assertEqual(styled.axes[1].get_ylim(), (0, 100))
        self.assertEqual(styled.axes[2].get_ylabel(), "MC/GDP")
        self.assertLess(styled.axes[2].get_zorder(), styled.axes[1].get_zorder())
        self.assertFalse(styled.axes[1].spines["right"].get_visible())
        self.assertEqual(styled.axes[2].spines["right"].get_edgecolor(), matplotlib.colors.to_rgba("#8A6A3A"))
        labels = [t.get_text() for t in styled.axes[1].texts]
        self.assertIn("MC/GDP: 0.58 · LOW", labels)
        lines = styled.axes[2].lines
        self.assertTrue(lines)
        self.assertTrue(all(line.get_drawstyle() == "steps-post" for line in lines))
        self.assertTrue(all(np.allclose(line.get_ydata(), .58) for line in lines))
        self.assertEqual([t.get_text() for t in styled.axes[1].get_legend().get_texts()], ["R", "MC/GDP"])

    def test_aligned_frame_is_reused_once_and_background_helpers_are_not_called(self):
        aligned = pd.DataFrame({"date": self.frame.date, "ratio": .81, "basis": "observed"})
        with patch("tools.a_share_valuation.align_valuation", return_value=aligned) as align, \
             patch("tools.a_share_valuation.draw_valuation_background", side_effect=AssertionError("不得画背景")), \
             patch("tools.a_share_valuation.draw_regime_labels", side_effect=AssertionError("不得画价格状态")), \
             patch("tools.a_share_valuation.load_valuation", side_effect=AssertionError("不得另行请求")):
            fig = self.draw(show_mc_gdp=True, mc_gdp_df=pd.DataFrame())
        self.assertEqual(align.call_count, 1)
        self.assertEqual(fig.axes[2].lines[0].get_ydata().tolist(), [.81] * len(self.frame))
        self.assertTrue(aligned.ratio.eq(.81).all())

    def test_step_colors_transitions_missing_values_and_threshold_axis_range(self):
        from tools.configs.a_share_valuation_configs import MC_GDP_LINE_COLORS
        ratios = [.81]*7 + [.70]*7 + [.58]*7 + [.54]*7 + [np.nan, np.inf, np.nan, .70, .70, .70, .70]
        aligned = pd.DataFrame({"date": self.frame.date, "ratio": ratios, "basis": "observed"})
        with patch("tools.a_share_valuation.align_valuation", return_value=aligned):
            fig = self.draw(show_mc_gdp=True)
        axis = fig.axes[2]
        self.assertEqual(set(line.get_color() for line in axis.lines), set(MC_GDP_LINE_COLORS.values()))
        self.assertTrue(all(line.get_drawstyle() == "steps-post" for line in axis.lines))
        self.assertTrue(all(np.isfinite(line.get_ydata()).all() for line in axis.lines))
        # 缺口两侧不连线；跳变在新观测日期使用新状态色。
        for line in axis.lines:
            dates = pd.to_datetime(line.get_xdata())
            self.assertFalse(dates.min() < self.frame.date.iloc[28] < dates.max())
        low_jumps = [line for line in axis.lines if line.get_color() == MC_GDP_LINE_COLORS["LOW"]
                     and .70 in line.get_ydata() and .58 in line.get_ydata()]
        self.assertEqual(len(low_jumps), 1)
        self.assertEqual(len(set(pd.to_datetime(low_jumps[0].get_xdata()))), 1)
        np.testing.assert_allclose(axis.get_ylim(), (.51, .84))
        self.assertFalse(any(line.get_visible() for line in axis.get_ygridlines()))

    def test_single_finite_date_or_invalid_ratios_do_not_create_empty_axis(self):
        for values in ([np.nan]*34 + [.58], [np.nan]*33 + [np.inf, "bad"]):
            with self.subTest(values=values[-2:]):
                aligned = pd.DataFrame({"date": self.frame.date, "ratio": values, "basis": "observed"})
                with patch("tools.a_share_valuation.align_valuation", return_value=aligned):
                    fig = self.draw(show_mc_gdp=True)
                self.assertEqual(len(fig.axes), 2)
                self.assertIn("MC/GDP: N/A", [t.get_text() for t in fig.axes[1].texts])

    def test_empty_aligned_result_degrades_without_an_axis(self):
        aligned = pd.DataFrame(columns=["date", "ratio", "basis"])
        with patch("tools.a_share_valuation.align_valuation", return_value=aligned):
            fig = self.draw(show_mc_gdp=True)
        self.assertEqual(len(fig.axes), 2)
        self.assertIn("MC/GDP: N/A", [t.get_text() for t in fig.axes[1].texts])

    def test_right_axis_uses_graph_overrides_and_filters_outside_display(self):
        macro = pd.DataFrame({"available_at": ["2025-12-31"], "ratio": [.81], "basis": ["observed"]})
        fig = self.draw(show_mc_gdp=True, mc_gdp_df=macro, mc_gdp_over_threshold=.90,
                        mc_gdp_low_threshold=.60, mc_gdp_deep_low_threshold=.40)
        np.testing.assert_allclose(fig.axes[2].get_ylim(), (.36, .94))
        self.assertIn("MC/GDP: 0.81 · NEUTRAL", [t.get_text() for t in fig.axes[1].texts])
        outside = pd.DataFrame({"date": pd.to_datetime(["2025-01-01", "2025-01-02"]), "ratio": [2., 3.]})
        axis, _ = rsi_data._draw_mc_gdp_curve(fig.axes[1], outside)
        self.assertIsNone(axis)

    def test_narrow_state_segment_does_not_force_a_label(self):
        aligned = pd.DataFrame({"date": self.frame.date, "ratio": .70, "basis": "observed"})
        aligned.loc[17, "ratio"] = .81
        with patch("tools.a_share_valuation.align_valuation", return_value=aligned):
            fig = self.draw(show_mc_gdp=True)
        self.assertFalse(fig.axes[2].texts)

    def test_curve_labels_avoid_its_own_step_jumps(self):
        self.frame = pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=300),
                                   "close": 100., "volume": 1000., "RSI": 50.})
        aligned = pd.DataFrame({"date": self.frame.date, "ratio": [.81]*150 + [.95]*150, "basis": "observed"})
        with patch("tools.a_share_valuation.align_valuation", return_value=aligned):
            fig = self.draw(show_mc_gdp=True)
        axis = fig.axes[2]
        fig.canvas.draw()
        self.assertTrue(axis.texts)
        for text in axis.texts:
            box = text.get_window_extent(fig.canvas.get_renderer())
            self.assertFalse(any(line.get_transform().transform_path(line.get_path()).intersects_bbox(box, filled=False)
                                 for line in axis.lines))

    def test_latest_value_text_has_a_white_underlay_when_curve_passes_below_it(self):
        macro = pd.DataFrame({"available_at": ["2025-12-31"], "ratio": [.81], "basis": ["observed"]})
        fig = self.draw(show_mc_gdp=True, mc_gdp_df=macro)
        metrics = [text for text in fig.axes[1].texts if text.get_text().startswith(("R:", "MC/GDP:"))]
        for text in metrics:
            self.assertIsNotNone(text.get_bbox_patch())
            self.assertEqual(text.get_bbox_patch().get_facecolor()[:3], (1., 1., 1.))

    def test_compact_merged_legend_keeps_live_breadth_entry(self):
        macro = pd.DataFrame({"available_at": ["2025-12-31"], "ratio": [.58], "basis": ["observed"]})
        breadth = pd.DataFrame({"date": self.frame.date, "percent": 23.3, "kind": "close"})
        breadth.loc[34, "kind"] = "intraday"
        fig = self.draw(show_mc_gdp=True, mc_gdp_df=macro, show_breadth=True, breadth_df=breadth)
        self.assertEqual(sum(ax.get_legend() is not None for ax in fig.axes), 1)
        self.assertEqual([t.get_text() for t in fig.axes[1].get_legend().get_texts()],
                         ["R", "50D", "MC/GDP", "盘中估算"])

    def test_curve_state_labels_only_on_wide_non_neutral_segments(self):
        for value, state in ((.81, "OVER"), (.58, "LOW"), (.54, "DEEP LOW"), (.70, "NEUTRAL")):
            with self.subTest(state=state):
                macro = pd.DataFrame({"available_at": ["2025-12-31"], "ratio": [value], "basis": ["observed"]})
                fig = self.draw(show_mc_gdp=True, mc_gdp_df=macro)
                labels = [t.get_text() for t in fig.axes[2].texts]
                self.assertEqual(labels, [] if state == "NEUTRAL" else [state])
                if labels:
                    fig.canvas.draw()
                    box = fig.axes[2].texts[0].get_window_extent(fig.canvas.get_renderer())
                    legend_box = fig.axes[1].get_legend().get_window_extent(fig.canvas.get_renderer())
                    self.assertFalse(box.overlaps(legend_box))
                    self.assertTrue(fig.axes[2].bbox.contains(box.x0, box.y0))
                    self.assertTrue(fig.axes[2].bbox.contains(box.x1, box.y1))

    def test_unavailable_label_only_when_enabled(self):
        fig = self.draw(show_mc_gdp=True, mc_gdp_df=pd.DataFrame())
        self.assertEqual(len(fig.axes), 2)
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
            for tick in fig.axes[2].get_yticklabels():
                self.assertFalse(text.get_window_extent(fig.canvas.get_renderer()).overlaps(
                    tick.get_window_extent(fig.canvas.get_renderer())))

    def test_disabled_rsi_has_only_price_and_no_valuation_background(self):
        macro = pd.DataFrame({"available_at": ["2025-12-31"], "ratio": [.81], "basis": ["observed"]})
        fig = self.draw(show_mc_gdp=True, mc_gdp_df=macro, show_rsi_panel=False)
        self.assertEqual(len(fig.axes), 1)
        self.assertFalse(fig.axes[0].patches)

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
