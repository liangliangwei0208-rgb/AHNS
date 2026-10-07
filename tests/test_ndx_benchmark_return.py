"""NDX 基准默认短窗口必须复用完整指数缓存，不能误判历史不足。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from tools import get_top10_holdings as holdings, rsi_data, rsi_module


class NdxBenchmarkReturnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache_dir = Path(self.temp.name)
        dates = pd.bdate_range(end="2026-10-06", periods=220)
        self.prices = pd.DataFrame({"date": dates, "close": range(1000, 1220)})
        self.prices.to_csv(self.cache_dir / "dot_NDX_index_daily.csv", index=False)

    def fetch(self, end_date):
        original = rsi_data.get_us_index_akshare

        def isolated_getter(**kwargs):
            kwargs.update(cache_dir=str(self.cache_dir), retry=1)
            return original(**kwargs)

        with patch.object(holdings, "CACHE_DIR", self.cache_dir), \
             patch.object(rsi_module, "get_us_index_akshare", side_effect=isolated_getter), \
             patch.object(rsi_data, "_latest_complete_rsi_trade_date", return_value="2026-10-06"), \
             patch.object(rsi_data.ak, "index_us_stock_sina", side_effect=AssertionError("unexpected network")):
            # 特意不传 days：复现正式基准调用的默认 15 行路径。
            return holdings.fetch_us_index_return_pct_from_rsi_module(
                symbol=".NDX", display_name="纳斯达克100", end_date=end_date,
            )

    def test_default_days_reads_complete_ndx_cache(self):
        result, trade_date, source = self.fetch("2026-10-06")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, (1219 / 1218 - 1) * 100)
        self.assertEqual(trade_date, "2026-10-06")
        self.assertEqual(source, "rsi_module_index_daily")

    def test_historical_anchor_excludes_future_closes(self):
        result, trade_date, _ = self.fetch("2026-10-02")
        self.assertEqual(trade_date, "2026-10-02")
        self.assertAlmostEqual(result, (1217 / 1216 - 1) * 100)

    def test_old_anchor_uses_local_history_without_refreshing_latest(self):
        with patch.object(holdings, "CACHE_DIR", self.cache_dir), \
             patch.object(rsi_module, "get_us_index_akshare", side_effect=AssertionError("network")):
            result, trade_date, _ = holdings.fetch_us_index_return_pct_from_rsi_module(
                ".NDX", end_date="2026-10-01",
            )
        self.assertEqual(trade_date, "2026-10-01")
        self.assertAlmostEqual(result, (1216 / 1215 - 1) * 100)

    def test_network_request_uses_shared_chart_minimum(self):
        with patch.object(holdings, "CACHE_DIR", self.cache_dir / "empty"), \
             patch.object(rsi_module, "get_us_index_akshare", return_value=self.prices) as getter:
            holdings.fetch_us_index_return_pct_from_rsi_module(".NDX", end_date="2026-10-06")
        self.assertEqual(getter.call_args.kwargs["days"], rsi_data.NDX_MIN_INDEX_HISTORY_ROWS)

    def test_strict_anchor_rejects_stale_trade_date(self):
        with patch.object(holdings, "_fetch_daily_return_for_anchor",
                          return_value=(1.0, "2026-10-05", "fixture")):
            row = holdings.get_security_return_by_anchor_date(
                "US", ".NDX", "2026-10-06", security_return_cache_enabled=False,
            )
        self.assertEqual(row["status"], "stale")

    def test_us_weekend_stays_closed_without_fetching(self):
        with patch.object(holdings, "_fetch_daily_return_for_anchor",
                          side_effect=AssertionError("closed market fetched")):
            row = holdings.get_security_return_by_anchor_date(
                "US", ".NDX", "2026-10-03", security_return_cache_enabled=False,
            )
        self.assertEqual(row["status"], "closed")
        self.assertEqual(row["return_pct"], 0.0)

    def test_missing_previous_us_session_is_not_a_daily_return(self):
        missing_previous = pd.concat([
            pd.DataFrame({"date": [pd.Timestamp("2025-01-01")], "close": [900]}),
            self.prices[self.prices["date"] != pd.Timestamp("2026-10-02")],
        ])
        missing_previous.to_csv(self.cache_dir / "dot_NDX_index_daily.csv", index=False)
        with patch.object(holdings, "CACHE_DIR", self.cache_dir), \
             patch.object(rsi_module, "get_us_index_akshare", return_value=missing_previous):
            with self.assertRaisesRegex(RuntimeError, "前一.*交易日"):
                holdings.fetch_us_index_return_pct_from_rsi_module(".NDX", end_date="2026-10-05")


if __name__ == "__main__":
    unittest.main()
