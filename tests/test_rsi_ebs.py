"""全A EBS 只进入价格色带，测试不联网。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from tools import rsi_data
from tools.configs.rsi_configs import RSI_ANALYSIS_CONFIGS


class EbsTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=30),
                                   "close": np.linspace(100, 120, 30), "volume": 1000., "RSI": 50.})

    def tearDown(self):
        plt.close("all")

    def draw(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp, patch.object(plt, "close") as closed:
            rsi_data.plot_analysis(self.frame, "TEST", output_file=str(Path(tmp)/"chart.png"),
                                  show_plot=False, **kwargs)
            return closed.call_args.args[0]

    def test_only_two_configured_etfs_enable_ebs(self):
        enabled = {x["kwargs"]["symbol"] for x in RSI_ANALYSIS_CONFIGS if x["kwargs"].get("show_ebs_state_band")}
        self.assertEqual(enabled, {"159943", "560220"})

    def test_formula_prewarm_and_strict_boundaries_reuse_strategy(self):
        from tools.equity_bond_spread import load_ebs_states
        from strategy import gu_zhai_xi as strategy
        dates = pd.bdate_range("2022-01-01", periods=1100)
        index = pd.DataFrame({"date": dates, "index_close": 100.})
        pe = pd.DataFrame({"date": dates, "pe_ttm": np.linspace(10, 30, len(dates))})
        bond = pd.DataFrame({"date": dates, "cn10y": 2.})
        with patch.object(strategy, "_load_chart_sources", return_value=(index, pe, bond, {})):
            out = load_ebs_states()
        spread = 100/pe.pe_ttm - bond.cn10y
        expected = pd.DataFrame({"date": dates, "spread": spread,
                                 "upper": spread.rolling(500).mean()+1.95*spread.rolling(500).std(ddof=0),
                                 "lower": spread.rolling(500).mean()-1.95*spread.rolling(500).std(ddof=0)}).dropna()
        self.assertEqual(out.date.iloc[0], dates[499])
        np.testing.assert_allclose(out.spread, expected.spread)
        np.testing.assert_allclose(out.upper, expected.upper)
        np.testing.assert_allclose(out.lower, expected.lower)
        from tools.equity_bond_spread import ebs_states
        probe = pd.DataFrame({"spread": [3., 1., 2., 3.01, .99, np.nan], "upper": 3., "lower": 1.})
        self.assertEqual(ebs_states(probe).tolist(), ["NORMAL", "NORMAL", "NORMAL", "HIGH", "LOW", None])

    def test_bands_merge_missing_breaks_and_no_ebs_on_second_row(self):
        states = ["HIGH"]*8 + ["NORMAL"]*6 + ["LOW"]*8 + [None]*4 + ["HIGH"]*4
        ebs = pd.DataFrame({"date": self.frame.date, "state": states})
        plain = self.draw()
        fig = self.draw(show_ebs_state_band=True, ebs_state_df=ebs)
        self.assertEqual(len(fig.axes), 2)
        self.assertEqual(fig.axes[0].get_ylim(), plain.axes[0].get_ylim())
        self.assertEqual(fig.axes[0].get_xlim(), plain.axes[0].get_xlim())
        bands = [p for p in fig.axes[0].get_children() if p.get_gid() == "ebs-state-band"]
        self.assertEqual(len(bands), 3)
        self.assertTrue(any(t.get_text() == "EBS" for t in fig.axes[0].texts))
        self.assertFalse(any("EBS" in t.get_text() for t in fig.axes[1].texts))
        self.assertFalse(any("EBS" in line.get_label() for line in fig.axes[1].lines))

    def test_no_projection_into_intraday_or_missing_dates(self):
        from tools.equity_bond_spread import draw_ebs_state_band
        fig, ax = plt.subplots()
        price = self.frame.tail(2)
        patches = draw_ebs_state_band(ax, price, pd.DataFrame({"date": price.date.iloc[:1], "state": ["HIGH"]}))
        self.assertEqual(len(patches), 1)
        import matplotlib.dates as mdates
        self.assertLess(patches[0].get_x()+patches[0].get_width(), mdates.date2num(price.date.iloc[-1]))

    def test_three_layer_pixel_geometry_and_other_charts_keep_positions(self):
        from tools.market_breadth import price_band_layout
        for dpi in (100, 180):
            fig, ax = plt.subplots(figsize=(12, 6.6), dpi=dpi)
            layout = price_band_layout(ax, dpi, include_ebs=True)
            pixels = ax.get_position().height*fig.get_size_inches()[1]*dpi
            self.assertAlmostEqual(layout["height"]*pixels, 16)
            for outer, inner in (("vix_negative", "breadth_low"), ("breadth_low", "ebs_high"),
                                 ("vix_positive", "breadth_high"), ("breadth_high", "ebs_low")):
                self.assertAlmostEqual((layout[inner]-layout[outer]-layout["height"])*pixels, 2)
            original = price_band_layout(ax, dpi)
            self.assertAlmostEqual((1-original["breadth_high"]-original["height"])*pixels, 5)
            self.assertAlmostEqual(original["vix_negative"]*pixels, 5)

    def test_unavailable_ebs_does_not_add_axis_or_metric(self):
        for ebs in (None, pd.DataFrame(), pd.DataFrame({"date": ["bad"], "state": ["HIGH"]})):
            fig = self.draw(show_ebs_state_band=True, ebs_state_df=ebs)
            self.assertEqual(len(fig.axes), 2)
            self.assertFalse(any("EBS" in t.get_text() for ax in fig.axes for t in ax.texts))
        self.assertEqual(len(self.draw(show_rsi_panel=False, show_ebs_state_band=True).axes), 1)

    def test_three_metrics_merged_legend_and_existing_curve_are_preserved(self):
        ebs = pd.DataFrame({"date": self.frame.date, "state": "HIGH"})
        breadth = pd.DataFrame({"date": self.frame.date, "percent": 23.3, "kind": "close"})
        macro = pd.DataFrame({"available_at": ["2025-12-31"], "ratio": [.81], "basis": ["observed"]})
        kwargs = dict(show_breadth=True, breadth_df=breadth, show_mc_gdp=True, mc_gdp_df=macro)
        plain = self.draw(**kwargs)
        fig = self.draw(**kwargs, show_ebs_state_band=True, ebs_state_df=ebs)
        self.assertEqual(len(fig.axes), 3)  # 右轴与第二行重叠，没有第三行。
        self.assertEqual(fig.axes[1].get_ylim(), (0,100))
        self.assertEqual([t.get_text() for t in fig.axes[1].get_legend().get_texts()], ["R","50D","MC/GDP"])
        for old, new in zip(plain.axes[1:], fig.axes[1:]):
            self.assertEqual([t.get_text() for t in old.texts], [t.get_text() for t in new.texts])
            self.assertEqual([x.get_label() for x in old.lines], [x.get_label() for x in new.lines])
        metrics = [t for t in fig.axes[1].texts if t.get_text().startswith(("50D", "R:", "MC/GDP:"))]
        self.assertEqual(len(metrics), 3)

    def test_raster_band_height_and_two_pixel_gaps(self):
        from PIL import Image
        from tools.equity_bond_spread import draw_ebs_state_band
        from tools.market_breadth import draw_breadth_state_band
        import matplotlib.dates as mdates
        dates = pd.bdate_range("2026-01-01", periods=20)
        price = pd.DataFrame({"date": dates})
        vix = pd.DataFrame({"date": dates, "VIX_MA_SPREAD": [-6.]*10+[6.]*10})
        breadth = pd.DataFrame({"date": dates, "percent": [10.]*10+[90.]*10})
        ebs = pd.DataFrame({"date": dates, "state": ["HIGH"]*10+["LOW"]*10})
        for dpi in (100,180):
            fig, ax = plt.subplots(figsize=(12,6.6))
            ax.set_xlim(dates[0], dates[-1])
            ax.set_ylim(0,1)
            rsi_data.draw_vix_state_band(ax, price, vix, output_dpi=dpi, include_ebs=True)
            draw_breadth_state_band(ax, price, breadth, 20,80, output_dpi=dpi, include_ebs=True)
            draw_ebs_state_band(ax, price, ebs, output_dpi=dpi)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/"bands.png"
                fig.savefig(path, dpi=dpi)
                pixels = np.asarray(Image.open(path).convert("RGB"))
                for date in (dates[4], dates[14]):
                    x = int(ax.transData.transform((mdates.date2num(date),0))[0]*dpi/fig.dpi)
                    column = pixels[:,x]
                    # 色带纯色纵列；文字位于各段右端，此处不会干扰计数。
                    nonwhite = (column.max(axis=1)-column.min(axis=1)>20) & (column.min(axis=1)<240)
                    rows = np.flatnonzero(nonwhite)
                    runs = np.split(rows, np.flatnonzero(np.diff(rows)>1)+1)
                    self.assertEqual(len(runs), 3)
                    self.assertTrue(all(abs(len(run)-16)<=1 for run in runs))
                    self.assertTrue(all(abs(runs[i+1][0]-runs[i][-1]-1-2)<=1 for i in (0,1)))

    def test_shared_loading_once_and_failure_does_not_stop_charts(self):
        import stock_analysis
        configs = [{"name": s, "image": s+".png", "kwargs": {"symbol": s, "show_ebs_state_band": True}} for s in ("159943", "560220")]
        ebs = pd.DataFrame({"date": self.frame.date, "state": "HIGH"})
        for failure in (False, True):
            with patch.object(stock_analysis, "RSI_ANALYSIS_CONFIGS", configs), \
                 patch("tools.breadth_engine.refresh_for_charts"), \
                 patch("tools.equity_bond_spread.load_ebs_states", side_effect=RuntimeError("source down") if failure else None, return_value=ebs) as load, \
                 patch.object(stock_analysis, "rsi_analyze_index", return_value=(self.frame,)*7) as run, \
                 patch.object(stock_analysis, "build_change_summary_text", return_value="summary"):
                stock_analysis.build_stock_analysis()
            self.assertEqual(load.call_count, 1)
            self.assertEqual(run.call_count, 2)
            passed = [call.kwargs["ebs_state_df"] for call in run.call_args_list]
            self.assertIs(passed[0], passed[1])
            self.assertEqual(passed[0].empty, failure)


if __name__ == "__main__":
    unittest.main()
