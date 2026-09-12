"""纳斯达克100与 VIX 均线差策略图的计算与绘图回归测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

from strategy import nasdaq100_vix_spread


class Nasdaq100VixSpreadTests(unittest.TestCase):
    def test_vix_spread_is_long_ma_minus_short_ma(self):
        """VIX 差值严格采用 200 日均线减去 20 日均线。"""
        dates = pd.date_range("2025-01-01", periods=200, freq="B")
        vix_df = pd.DataFrame({"date": dates, "close": np.arange(1, 201, dtype=float)})

        result = nasdaq100_vix_spread.add_vix_moving_average_spread(
            vix_df,
            long_window=200,
            short_window=20,
        )

        self.assertTrue(result.loc[:198, "VIX_MA_SPREAD"].isna().all())
        self.assertAlmostEqual(result.loc[199, "VIX_MA_LONG"], 100.5)
        self.assertAlmostEqual(result.loc[199, "VIX_MA_SHORT"], 190.5)
        self.assertAlmostEqual(result.loc[199, "VIX_MA_SPREAD"], -90.0)

    def test_vix_spread_color_keeps_exact_thresholds_neutral(self):
        """VIX 差值只在严格越过正负 5 时改变价格线颜色。"""
        self.assertEqual(
            nasdaq100_vix_spread.get_vix_spread_color(-5.01),
            nasdaq100_vix_spread.PRICE_NEGATIVE_SPREAD_COLOR,
        )
        self.assertEqual(
            nasdaq100_vix_spread.get_vix_spread_color(-5.0),
            nasdaq100_vix_spread.PRICE_NEUTRAL_COLOR,
        )
        self.assertEqual(
            nasdaq100_vix_spread.get_vix_spread_color(0.0),
            nasdaq100_vix_spread.PRICE_NEUTRAL_COLOR,
        )
        self.assertEqual(
            nasdaq100_vix_spread.get_vix_spread_color(5.0),
            nasdaq100_vix_spread.PRICE_NEUTRAL_COLOR,
        )
        self.assertEqual(
            nasdaq100_vix_spread.get_vix_spread_color(5.01),
            nasdaq100_vix_spread.PRICE_POSITIVE_SPREAD_COLOR,
        )

    def test_price_segments_use_same_day_vix_spread_not_price_direction(self):
        """价格线段使用终点交易日 VIX 差值着色，涨跌方向不参与判断。"""
        dates = pd.date_range("2026-01-01", periods=6, freq="D")
        prices = pd.DataFrame({"date": dates, "close": [100.0, 102.0, 101.0, 103.0, 99.0, 104.0]})
        vix_spread = pd.DataFrame(
            {"date": dates, "VIX_MA_SPREAD": [0.0, -5.1, -5.0, 5.0, 5.1, 0.0]}
        )

        segments = nasdaq100_vix_spread.build_vix_state_price_segments(prices, vix_spread)

        self.assertEqual(
            [segment["close"].tolist() for segment in segments[nasdaq100_vix_spread.PRICE_NEGATIVE_SPREAD_COLOR]],
            [[100.0, 102.0]],
        )
        self.assertEqual(
            [segment["close"].tolist() for segment in segments[nasdaq100_vix_spread.PRICE_POSITIVE_SPREAD_COLOR]],
            [[103.0, 99.0]],
        )
        self.assertEqual(
            [segment["close"].tolist() for segment in segments[nasdaq100_vix_spread.PRICE_NEUTRAL_COLOR]],
            [[102.0, 101.0], [101.0, 103.0], [99.0, 104.0]],
        )

    def test_render_creates_two_panel_nasdaq100_vix_figure(self):
        """输出图包含纳斯达克100价格图和 VIX 均线差图。"""
        dates = pd.date_range("2025-01-01", periods=220, freq="B")
        price_df = pd.DataFrame({"date": dates, "close": np.linspace(18_000, 21_000, len(dates))})
        vix_df = pd.DataFrame({"date": dates, "close": np.linspace(24, 14, len(dates))})

        with TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "nasdaq100_vix_spread.png"
            with patch.object(nasdaq100_vix_spread.plt, "close") as close_figure:
                nasdaq100_vix_spread.render_nasdaq100_vix_spread_chart(
                    price_df,
                    vix_df,
                    output_file=output_file,
                )

            figure = close_figure.call_args.args[0]
            self.assertTrue(output_file.exists())
            self.assertEqual(len(figure.axes), 2)
            self.assertEqual(figure.axes[0].get_title(), "纳斯达克100指数")
            self.assertEqual(figure.axes[1].get_title(), "VIX 均线差（200日均线 - 20日均线）")
            self.assertEqual(len(figure.axes[1].collections), 2)

        plt.close("all")

    def test_render_uses_latest_common_start_date_for_shared_x_axis(self):
        """VIX 历史更早时，共享横轴不能在纳斯达克100图左侧留下空白。"""
        vix_dates = pd.date_range("2019-01-01", periods=340, freq="B")
        price_dates = pd.date_range("2020-01-02", periods=120, freq="B")
        price_df = pd.DataFrame({"date": price_dates, "close": np.linspace(9_000, 10_000, len(price_dates))})
        vix_df = pd.DataFrame({"date": vix_dates, "close": np.linspace(15, 25, len(vix_dates))})

        with TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "aligned.png"
            with patch.object(nasdaq100_vix_spread.plt, "close") as close_figure:
                nasdaq100_vix_spread.render_nasdaq100_vix_spread_chart(
                    price_df,
                    vix_df,
                    output_file=output_file,
                    long_window=200,
                    short_window=20,
                )

            price_axis = close_figure.call_args.args[0].axes[0]
            visible_start = pd.Timestamp(mdates.num2date(price_axis.get_xlim()[0])).tz_localize(None)
            self.assertGreaterEqual(visible_start, price_dates.min() - pd.Timedelta(days=15))

        plt.close("all")

    def test_render_limits_visible_price_history_to_display_days(self):
        """VIX 可保留长历史，但图中纳斯达克100只展示指定的最近交易日。"""
        dates = pd.date_range("2024-01-01", periods=520, freq="B")
        price_df = pd.DataFrame({"date": dates, "close": np.linspace(15_000, 22_000, len(dates))})
        vix_df = pd.DataFrame({"date": dates, "close": np.linspace(12, 28, len(dates))})

        with TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "latest_220.png"
            with patch.object(nasdaq100_vix_spread.plt, "close") as close_figure:
                nasdaq100_vix_spread.render_nasdaq100_vix_spread_chart(
                    price_df,
                    vix_df,
                    output_file=output_file,
                    long_window=200,
                    short_window=20,
                    display_days=220,
                )

            price_axis = close_figure.call_args.args[0].axes[0]
            visible_start = pd.Timestamp(mdates.num2date(price_axis.get_xlim()[0])).tz_localize(None)
            expected_start = dates[-220]
            # Matplotlib 的浮点日期会按 UTC 还原，允许一个自然日的时区差。
            self.assertGreaterEqual(visible_start.date(), (expected_start - pd.Timedelta(days=16)).date())

        plt.close("all")

    def test_strategy_chart_uses_its_own_output_and_cache_directories(self):
        """策略图的图片与两类行情缓存均不能写入项目根目录。"""
        strategy_dir = nasdaq100_vix_spread.PROJECT_ROOT / "strategy"
        self.assertEqual(
            nasdaq100_vix_spread.DEFAULT_OUTPUT_FILE,
            strategy_dir / "output" / "nasdaq100_vix_spread.png",
        )
        self.assertEqual(
            nasdaq100_vix_spread.VIX_CACHE_FILE,
            strategy_dir / "cache" / "vix_index_daily.csv",
        )
        self.assertEqual(nasdaq100_vix_spread.DEFAULT_VIX_HISTORY_DAYS, 1_000)

        with patch.object(nasdaq100_vix_spread, "get_index_akshare") as get_index:
            get_index.return_value = pd.DataFrame(
                {"date": pd.to_datetime(["2026-01-01"]), "close": [20_000.0]}
            )
            nasdaq100_vix_spread.fetch_nasdaq100_history(days=220)

        self.assertEqual(
            get_index.call_args.kwargs["cache_dir"],
            str(strategy_dir / "cache"),
        )

    def test_vix_fetch_requests_daily_yahoo_history_with_explicit_dates(self):
        """VIX 长历史必须请求日线，不能让 Yahoo 把 max 区间自动降采样为月线。"""
        timestamps = [1_704_067_200, 1_704_153_600, 1_704_240_000]
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": timestamps,
                        "indicators": {"quote": [{"close": [14.0, 15.0, 16.0]}]},
                    }
                ]
            }
        }
        with patch.object(nasdaq100_vix_spread.requests, "get") as request_get:
            request_get.return_value.json.return_value = payload
            result = nasdaq100_vix_spread._fetch_daily_vix_history_from_yahoo(days=300)

        self.assertEqual(len(result), 3)
        self.assertEqual(result["close"].tolist(), [14.0, 15.0, 16.0])
        params = request_get.call_args.kwargs["params"]
        self.assertEqual(params["interval"], "1d")
        self.assertIn("period1", params)
        self.assertIn("period2", params)
        self.assertNotIn("range", params)


if __name__ == "__main__":
    unittest.main()
