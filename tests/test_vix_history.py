"""共享 VIX 日线历史读取与均线差计算的回归测试。"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from tools.vix_history import add_vix_moving_average_spread


class VixHistoryTests(unittest.TestCase):
    def test_spread_uses_daily_long_ma_minus_short_ma(self):
        """共享 helper 继续采用 VIX 200 日均线减 20 日均线。"""
        dates = pd.date_range("2025-01-01", periods=200, freq="B")
        vix_df = pd.DataFrame({"date": dates, "close": np.arange(1, 201, dtype=float)})

        result = add_vix_moving_average_spread(vix_df, long_window=200, short_window=20)

        self.assertTrue(result.loc[:198, "VIX_MA_SPREAD"].isna().all())
        self.assertAlmostEqual(result.loc[199, "VIX_MA_SPREAD"], -90.0)


if __name__ == "__main__":
    unittest.main()
