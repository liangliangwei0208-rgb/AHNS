"""共享 VIX 日线历史读取与均线差计算的回归测试。"""

from __future__ import annotations

import os
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from tools.vix_history import add_vix_moving_average_spread, fetch_vix_daily_history
from tools.runtime_stats import snapshot_market_events


class VixHistoryTests(unittest.TestCase):
    def test_spread_uses_daily_long_ma_minus_short_ma(self):
        """共享 helper 继续采用 VIX 200 日均线减 20 日均线。"""
        dates = pd.date_range("2025-01-01", periods=200, freq="B")
        vix_df = pd.DataFrame({"date": dates, "close": np.arange(1, 201, dtype=float)})

        result = add_vix_moving_average_spread(vix_df, long_window=200, short_window=20)

        self.assertTrue(result.loc[:198, "VIX_MA_SPREAD"].isna().all())
        self.assertAlmostEqual(result.loc[199, "VIX_MA_SPREAD"], -90.0)

    def test_fetch_uses_cache_when_it_covers_latest_complete_us_session(self):
        """本地日线已覆盖最近完整美股交易日时，不应重复请求 Yahoo。"""
        with TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "vix.csv"
            pd.DataFrame(
                {
                    "date": pd.to_datetime(["2026-09-08", "2026-09-09", "2026-09-10"]),
                    "close": [18.0, 19.0, 20.0],
                }
            ).to_csv(cache_file, index=False, encoding="utf-8-sig")

            with patch("tools.vix_history.latest_completed_us_session_date", return_value=pd.Timestamp("2026-09-10")):
                with patch("tools.vix_history.fetch_daily_vix_history_from_yahoo") as fetch:
                    result = fetch_vix_daily_history(
                        days=3,
                        cache_file=cache_file,
                        now=datetime(2026, 9, 11, 12, 0),
                    )

            fetch.assert_not_called()
            self.assertEqual(result["date"].max(), pd.Timestamp("2026-09-10"))

    def test_stale_cache_retries_at_most_once_within_two_hours(self):
        """缓存落后但距离上次尝试不足两小时，应继续使用旧日线而不重复联网。"""
        with TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "vix.csv"
            pd.DataFrame(
                {"date": pd.to_datetime(["2026-09-08", "2026-09-09"]), "close": [18.0, 19.0]}
            ).to_csv(cache_file, index=False, encoding="utf-8-sig")
            recent_attempt = datetime(2026, 9, 11, 11, 0).timestamp()
            os.utime(cache_file, (recent_attempt, recent_attempt))

            with patch("tools.vix_history.latest_completed_us_session_date", return_value=pd.Timestamp("2026-09-10")):
                with patch("tools.vix_history.fetch_daily_vix_history_from_yahoo") as fetch:
                    result = fetch_vix_daily_history(
                        days=2,
                        cache_file=cache_file,
                        now=datetime(2026, 9, 11, 12, 0),
                    )

            fetch.assert_not_called()
            self.assertEqual(result["date"].max(), pd.Timestamp("2026-09-09"))

    def test_stale_cache_falls_back_after_failed_retry(self):
        """到达重试时间后联网失败，仍必须返回经过日线校验的旧缓存。"""
        with TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "vix.csv"
            pd.DataFrame(
                {"date": pd.to_datetime(["2026-09-08", "2026-09-09"]), "close": [18.0, 19.0]}
            ).to_csv(cache_file, index=False, encoding="utf-8-sig")
            old_attempt = datetime(2026, 9, 11, 8, 0).timestamp()
            os.utime(cache_file, (old_attempt, old_attempt))
            before_event_count = len(snapshot_market_events())

            with patch("tools.vix_history.latest_completed_us_session_date", return_value=pd.Timestamp("2026-09-10")):
                with patch(
                    "tools.vix_history.fetch_daily_vix_history_from_yahoo",
                    side_effect=RuntimeError("Yahoo unavailable"),
                ) as fetch:
                    result = fetch_vix_daily_history(
                        days=2,
                        cache_file=cache_file,
                        now=datetime(2026, 9, 11, 12, 0),
                    )

            fetch.assert_called_once()
            self.assertEqual(result["date"].max(), pd.Timestamp("2026-09-09"))
            new_events = snapshot_market_events()[before_event_count:]
            self.assertTrue(any(item["action"] == "vix_daily_network_fetch" for item in new_events))
            self.assertTrue(any(item["action"] == "vix_daily_cache" and item["cache_hit"] for item in new_events))

    def test_aware_utc_clock_is_converted_to_beijing_before_session_check(self):
        """GitHub 的 UTC 时钟不能被误当成北京时间判断美股完整收盘。"""
        with TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "vix.csv"
            pd.DataFrame(
                {"date": pd.to_datetime(["2026-09-10", "2026-09-11"]), "close": [18.0, 19.0]}
            ).to_csv(cache_file, index=False, encoding="utf-8-sig")

            with patch(
                "tools.vix_history.latest_completed_us_session_date",
                return_value=pd.Timestamp("2026-09-11"),
            ) as latest_session:
                fetch_vix_daily_history(
                    days=2,
                    cache_file=cache_file,
                    now=datetime(2026, 9, 11, 4, 0, tzinfo=ZoneInfo("UTC")),
                )

            check_now = latest_session.call_args.args[0]
            self.assertEqual(check_now.hour, 12)
            self.assertEqual(str(check_now.tzinfo), "Asia/Shanghai")

    def test_vix_cache_is_capped_at_one_thousand_daily_rows(self):
        """VIX 缓存最多保留约 1000 个交易日，避免策略缓存长期膨胀。"""
        with TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "vix.csv"
            dates = pd.date_range("2021-01-01", periods=1_005, freq="B")
            pd.DataFrame({"date": dates, "close": np.arange(len(dates), dtype=float)}).to_csv(
                cache_file,
                index=False,
                encoding="utf-8-sig",
            )

            with patch(
                "tools.vix_history.latest_completed_us_session_date",
                return_value=dates[-1],
            ) as latest_session:
                with patch("tools.vix_history.fetch_daily_vix_history_from_yahoo") as fetch:
                    result = fetch_vix_daily_history(days=1_500, cache_file=cache_file)

            latest_session.assert_called_once()
            fetch.assert_not_called()
            self.assertEqual(len(result), 1_000)
            self.assertEqual(len(pd.read_csv(cache_file, encoding="utf-8-sig")), 1_000)


if __name__ == "__main__":
    unittest.main()
