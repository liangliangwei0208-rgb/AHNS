"""主 RSI 图 VIX 极端状态带的回归测试。"""

from __future__ import annotations

import unittest

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from tools.configs.rsi_configs import RSI_ANALYSIS_CONFIGS
from tools.rsi_data import (
    VIX_STATE_BAND_NEGATIVE_COLOR,
    VIX_STATE_BAND_HEIGHT,
    VIX_STATE_BAND_BOTTOM,
    VIX_STATE_BAND_POSITIVE_COLOR,
    VIX_STATE_BAND_POSITIVE_BOTTOM,
    classify_vix_state_band,
    draw_vix_state_band,
)


class RsiVixStateBandTests(unittest.TestCase):
    def test_strict_thresholds_only_mark_extreme_states(self):
        """等于正负阈值时保持中性，不应出现状态带。"""
        self.assertEqual(classify_vix_state_band(-5.01, -5, 5), "negative")
        self.assertIsNone(classify_vix_state_band(-5.0, -5, 5))
        self.assertIsNone(classify_vix_state_band(0.0, -5, 5))
        self.assertIsNone(classify_vix_state_band(5.0, -5, 5))
        self.assertEqual(classify_vix_state_band(5.01, -5, 5), "positive")

    def test_band_uses_only_exactly_matched_extreme_dates(self):
        """没有同日 VIX 值或处于中性区间的价格日期必须保留空白。"""
        dates = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"])
        price_df = pd.DataFrame({"date": dates, "close": [100.0, 101.0, 102.0, 103.0]})
        vix_state_df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-08"]),
                "VIX_MA_SPREAD": [0.0, -5.1, 5.2],
            }
        )

        figure, axis = plt.subplots()
        draw_vix_state_band(axis, price_df, vix_state_df, -5, 5)

        self.assertEqual(len(axis.collections), 0)
        self.assertEqual(len(axis.patches), 1)
        patch = axis.patches[0]
        self.assertAlmostEqual(patch.get_y(), VIX_STATE_BAND_BOTTOM)
        self.assertAlmostEqual(patch.get_height(), VIX_STATE_BAND_HEIGHT)
        np.testing.assert_allclose(patch.get_facecolor()[:3], (0.776, 0.239, 0.239), atol=0.01)

        # 唯一色块必须覆盖 2026-01-05；01-08 没有价格日，不可错位涂色。
        # 周末会让相邻交易日中点不对称，因此不要求色块的几何中心等于该交易日。
        target_x = mdates.date2num(dates[1])
        self.assertLessEqual(patch.get_x(), target_x)
        self.assertGreaterEqual(patch.get_x() + patch.get_width(), target_x)
        plt.close(figure)

    def test_band_carries_recent_vix_state_across_short_market_calendar_gap(self):
        """DAX 类跨市场休市缺口应继承最近 VIX 状态，并合为一段色块。"""
        price_dates = pd.to_datetime(["2026-04-02", "2026-04-03", "2026-04-07"])
        price_df = pd.DataFrame({"date": price_dates, "close": [1.233, 1.241, 1.243]})
        vix_state_df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-04-02", "2026-04-07"]),
                "VIX_MA_SPREAD": [-7.97, -7.68],
            }
        )

        figure, axis = plt.subplots()
        draw_vix_state_band(axis, price_df, vix_state_df, -5, 5, max_staleness_days=5)

        # 04-03 使用 04-02 的已知状态，和 04-07 的同色状态合成一个连续区间。
        self.assertEqual(len(axis.patches), 1)
        patch = axis.patches[0]
        for date in price_dates:
            date_x = mdates.date2num(date)
            self.assertLessEqual(patch.get_x(), date_x)
            self.assertGreaterEqual(patch.get_x() + patch.get_width(), date_x)
        plt.close(figure)

    def test_band_does_not_carry_stale_or_future_vix_state(self):
        """超过上限或只有未来 VIX 数据时，状态带必须留白。"""
        price_dates = pd.to_datetime(["2026-04-08", "2026-04-09"])
        price_df = pd.DataFrame({"date": price_dates, "close": [1.30, 1.31]})
        vix_state_df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-04-02", "2026-04-10"]),
                "VIX_MA_SPREAD": [-7.97, -7.50],
            }
        )

        figure, axis = plt.subplots()
        draw_vix_state_band(axis, price_df, vix_state_df, -5, 5, max_staleness_days=5)

        # 04-08 的最近历史 VIX 已超过 5 天；04-09 不可向未来借用 04-10 的值。
        self.assertEqual(len(axis.patches), 0)
        plt.close(figure)

    def test_band_keeps_price_axis_limits_title_and_legend_unchanged(self):
        """状态带是价格轴内部的低干扰图层，不能改动原有视觉语义。"""
        dates = pd.date_range("2026-01-02", periods=3, freq="B")
        price_df = pd.DataFrame({"date": dates, "close": [100.0, 101.0, 102.0]})
        vix_state_df = pd.DataFrame({"date": dates, "VIX_MA_SPREAD": [-6.0, 0.0, 6.0]})

        figure, axis = plt.subplots()
        axis.set_title("原有标题")
        axis.set_ylim(90.0, 110.0)
        before_ylim = axis.get_ylim()

        draw_vix_state_band(axis, price_df, vix_state_df, -5, 5)

        self.assertEqual(axis.get_ylim(), before_ylim)
        self.assertEqual(axis.get_title(), "原有标题")
        self.assertIsNone(axis.get_legend())
        self.assertEqual(len(axis.collections), 0)
        self.assertEqual(len(axis.patches), 2)
        colors = {tuple(np.round(patch.get_facecolor()[:3], 3)) for patch in axis.patches}
        self.assertIn(tuple(np.round(plt.matplotlib.colors.to_rgb(VIX_STATE_BAND_NEGATIVE_COLOR), 3)), colors)
        self.assertIn(tuple(np.round(plt.matplotlib.colors.to_rgb(VIX_STATE_BAND_POSITIVE_COLOR), 3)), colors)
        plt.close(figure)

    def test_negative_band_is_below_positive_band(self):
        """红色风险状态贴底，深青色状态使用其上方独立槽位。"""
        dates = pd.date_range("2026-01-02", periods=2, freq="B")
        price_df = pd.DataFrame({"date": dates, "close": [100.0, 101.0]})
        vix_state_df = pd.DataFrame({"date": dates, "VIX_MA_SPREAD": [-6.0, 6.0]})

        figure, axis = plt.subplots()
        draw_vix_state_band(axis, price_df, vix_state_df, -5, 5)

        red_patch = next(
            patch
            for patch in axis.patches
            if np.allclose(
                patch.get_facecolor()[:3],
                plt.matplotlib.colors.to_rgb(VIX_STATE_BAND_NEGATIVE_COLOR),
            )
        )
        teal_patch = next(
            patch
            for patch in axis.patches
            if np.allclose(
                patch.get_facecolor()[:3],
                plt.matplotlib.colors.to_rgb(VIX_STATE_BAND_POSITIVE_COLOR),
            )
        )
        self.assertAlmostEqual(red_patch.get_y(), VIX_STATE_BAND_BOTTOM)
        self.assertAlmostEqual(teal_patch.get_y(), VIX_STATE_BAND_POSITIVE_BOTTOM)
        self.assertGreater(teal_patch.get_y(), red_patch.get_y())
        plt.close(figure)

    def test_all_configured_indices_and_etfs_enable_vix_state_band_by_default(self):
        """VIX 作为全球风险环境提示，所有配置标的默认启用状态带。"""
        enabled_names = {
            config["name"]
            for config in RSI_ANALYSIS_CONFIGS
            if config["kwargs"].get("show_vix_state_band")
        }
        self.assertEqual(enabled_names, {config["name"] for config in RSI_ANALYSIS_CONFIGS})

        for config in RSI_ANALYSIS_CONFIGS:
            kwargs = config["kwargs"]
            self.assertIn("show_vix_state_band", kwargs)
            self.assertEqual(kwargs["vix_state_negative_threshold"], -5)
            self.assertEqual(kwargs["vix_state_positive_threshold"], 5)

        enabled_configs = [
            config["kwargs"]
            for config in RSI_ANALYSIS_CONFIGS
            if config["kwargs"].get("show_vix_state_band")
        ]
        self.assertTrue(all(config.get("vix_state_max_staleness_days") == 5 for config in enabled_configs))


if __name__ == "__main__":
    unittest.main()
