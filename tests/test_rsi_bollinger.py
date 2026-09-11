"""RSI 收盘价图的 BOLL(20,2) 计算回归测试。"""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd

from tools import rsi_data


class BollingerBandsTests(unittest.TestCase):
    def test_bollinger_bands_use_20_closes_and_two_standard_deviations(self):
        """BOLL(20,2) 在第 20 个收盘价处开始生成三条轨道。"""
        self.assertTrue(hasattr(rsi_data, "add_bollinger_bands"))

        closes = pd.Series(range(1, 21), dtype=float)
        result = rsi_data.add_bollinger_bands(pd.DataFrame({"close": closes}))

        self.assertTrue(result.loc[:18, "BOLL_MID"].isna().all())
        self.assertAlmostEqual(result.loc[19, "BOLL_MID"], 10.5)

        expected_std = float(np.std(closes.to_numpy(), ddof=0))
        self.assertAlmostEqual(result.loc[19, "BOLL_UPPER"], 10.5 + expected_std * 2)
        self.assertAlmostEqual(result.loc[19, "BOLL_LOWER"], 10.5 - expected_std * 2)

    def test_price_chart_hides_bollinger_legend(self):
        """BOLL 虚线只做辅助参考，不占用价格图的图例区域。"""
        dates = pd.date_range("2026-01-01", periods=30, freq="D")
        frame = pd.DataFrame(
            {
                "date": dates,
                "close": np.linspace(100, 130, 30),
                "volume": np.full(30, 1_000_000),
                "RSI": np.full(30, 50.0),
            }
        )

        with TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "boll.png"
            with patch.object(rsi_data.plt, "close") as close_figure:
                rsi_data.plot_analysis(
                    df=frame,
                    symbol="TEST",
                    output_file=str(output_file),
                    show_plot=False,
                    show_points=False,
                    show_daily_signals=False,
                    show_weekly_signals=False,
                    show_monthly_signals=False,
                    show_boll=True,
                )

            price_axis = close_figure.call_args.args[0].axes[0]
            self.assertIsNone(price_axis.get_legend())

    def test_price_chart_draws_only_bollinger_upper_and_lower_bands(self):
        """BOLL 中轨只参与计算，不在价格图中单独绘制。"""
        dates = pd.date_range("2026-01-01", periods=30, freq="D")
        frame = pd.DataFrame(
            {
                "date": dates,
                "close": np.linspace(100, 130, 30),
                "volume": np.full(30, 1_000_000),
                "RSI": np.full(30, 50.0),
            }
        )

        with TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "boll.png"
            with patch.object(rsi_data.plt, "close") as close_figure:
                rsi_data.plot_analysis(
                    df=frame,
                    symbol="TEST",
                    output_file=str(output_file),
                    show_plot=False,
                    show_points=False,
                    show_daily_signals=False,
                    show_weekly_signals=False,
                    show_monthly_signals=False,
                    show_boll=True,
                )

            price_axis = close_figure.call_args.args[0].axes[0]
            self.assertEqual([line.get_color() for line in price_axis.lines], ["#E05263", "#25AE88"])

    def test_price_chart_draws_weekly_boll_as_right_continuous_steps(self):
        """正式收盘价图的周 BOLL 必须用阶梯线，不能在周点之间斜线插值。"""
        self.assertIn("weekly_boll_df", inspect.signature(rsi_data.plot_analysis).parameters)

        dates = pd.date_range("2026-01-01", periods=30, freq="D")
        daily_frame = pd.DataFrame(
            {
                "date": dates,
                "close": np.linspace(100, 130, 30),
                "volume": np.full(30, 1_000_000),
                "RSI": np.full(30, 50.0),
            }
        )
        weekly_boll_frame = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-01-02", "2026-01-09", "2026-01-16", "2026-01-23", "2026-01-30"]),
                "close": [101, 104, 107, 110, 113],
                "BOLL_MID": [100, 102, 104, 106, 108],
                "BOLL_UPPER": [105, 107, 109, 111, 113],
                "BOLL_LOWER": [95, 97, 99, 101, 103],
            }
        )

        with TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "weekly_boll.png"
            with patch.object(rsi_data.plt, "close") as close_figure:
                rsi_data.plot_analysis(
                    df=daily_frame,
                    symbol="TEST",
                    output_file=str(output_file),
                    show_plot=False,
                    show_points=False,
                    show_daily_signals=False,
                    show_weekly_signals=False,
                    show_monthly_signals=False,
                    show_boll=True,
                    weekly_boll_df=weekly_boll_frame,
                    show_weekly_boll=True,
                )

            price_axis = close_figure.call_args.args[0].axes[0]
            weekly_lines = price_axis.lines[-2:]
            self.assertEqual([line.get_color() for line in weekly_lines], ["#7451B5", "#9A5C1A"])
            self.assertEqual([line.get_drawstyle() for line in weekly_lines], ["steps-post", "steps-post"])

    def test_price_chart_hides_rsi_panel_when_disabled(self):
        """关闭开关后，公开输出图只保留收盘价和成交量两行。"""
        dates = pd.date_range("2026-01-01", periods=30, freq="D")
        frame = pd.DataFrame(
            {
                "date": dates,
                "close": np.linspace(100, 130, 30),
                "volume": np.full(30, 1_000_000),
                "RSI": np.full(30, 50.0),
            }
        )

        with TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "two_panels.png"
            with patch.object(rsi_data.plt, "close") as close_figure:
                rsi_data.plot_analysis(
                    df=frame,
                    symbol="TEST",
                    output_file=str(output_file),
                    show_plot=False,
                    show_points=False,
                    show_daily_signals=False,
                    show_weekly_signals=False,
                    show_monthly_signals=False,
                    show_rsi_panel=False,
                )

            self.assertEqual(len(close_figure.call_args.args[0].axes), 2)


if __name__ == "__main__":
    unittest.main()
