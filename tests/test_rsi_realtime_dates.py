"""中国场内 ETF 实时补点日期校验的回归测试。"""

from __future__ import annotations

import inspect
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from tools import rsi_data


BJ_TZ = ZoneInfo("Asia/Shanghai")
AS_OF_BJ = datetime(2026, 9, 4, 10, 0, tzinfo=BJ_TZ)


def _history_until_previous_trade_day() -> pd.DataFrame:
    """构造截至 2026-09-03 的历史日线，避免依赖真实行情。"""
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-02", "2026-09-03"]),
            "open": [1.00, 1.01],
            "high": [1.02, 1.03],
            "low": [0.99, 1.00],
            "close": [1.01, 1.02],
            "volume": [100, 120],
        }
    )


class CnEtfRealtimeDateTests(unittest.TestCase):
    def test_full_market_snapshot_without_verified_date_is_rejected(self):
        """全市场快照缺少日期时，不能把价格伪装成当天实时日线。"""
        snapshot = pd.DataFrame(
            {
                "代码": ["512890"],
                "最新价": [1.23],
                "今开": [1.20],
                "最高": [1.24],
                "最低": [1.19],
                "成交量": [12345],
                "成交额": [15000],
            }
        )
        with patch.object(rsi_data.ak, "fund_etf_spot_em", return_value=snapshot):
            result = rsi_data._fetch_cn_etf_realtime_spot_row("512890", retry=1)

        self.assertIsNone(result)

    def test_verified_today_quote_is_merged(self):
        """已验证为当日 A 股交易日的新浪报价仍应作为临时日线合并。"""
        self.assertIn("as_of_bj", inspect.signature(rsi_data._merge_cn_etf_realtime_today).parameters)
        quote = {
            "date": pd.Timestamp("2026-09-04"),
            "open": 1.03,
            "high": 1.05,
            "low": 1.02,
            "close": 1.04,
            "volume": 160,
            "amount": 166,
            "source": "sina_realtime",
        }
        with patch.object(rsi_data, "_get_cn_etf_realtime_row", return_value=quote):
            result = rsi_data._merge_cn_etf_realtime_today(
                _history_until_previous_trade_day(),
                symbol="512890",
                as_of_bj=AS_OF_BJ,
            )

        self.assertEqual([str(value.date()) for value in result["date"]], ["2026-09-02", "2026-09-03", "2026-09-04"])
        self.assertEqual(float(result.iloc[-1]["close"]), 1.04)
        self.assertTrue(bool(result.iloc[-1]["is_realtime"]))

    def test_previous_trade_date_quote_does_not_replace_history(self):
        """旧报价即使有价格，也不能覆盖当前运行日的历史序列。"""
        self.assertIn("as_of_bj", inspect.signature(rsi_data._merge_cn_etf_realtime_today).parameters)
        quote = {
            "date": pd.Timestamp("2026-09-03"),
            "open": 2.00,
            "high": 2.00,
            "low": 2.00,
            "close": 2.00,
            "volume": 999,
            "amount": 999,
            "source": "sina_realtime",
        }
        with patch.object(rsi_data, "_get_cn_etf_realtime_row", return_value=quote):
            result = rsi_data._merge_cn_etf_realtime_today(
                _history_until_previous_trade_day(),
                symbol="512890",
                as_of_bj=AS_OF_BJ,
            )

        self.assertEqual([str(value.date()) for value in result["date"]], ["2026-09-02", "2026-09-03"])
        self.assertEqual(float(result.iloc[-1]["close"]), 1.02)

    def test_weekend_quote_is_not_appended_even_when_its_date_matches_today(self):
        """周末报价即使标注当天，也必须经 SSE 日历拦截。"""
        quote = {
            "date": pd.Timestamp("2026-09-05"),
            "open": 1.03,
            "high": 1.05,
            "low": 1.02,
            "close": 1.04,
            "volume": 160,
            "amount": 166,
            "source": "sina_realtime",
        }
        with patch.object(rsi_data, "_get_cn_etf_realtime_row", return_value=quote):
            result = rsi_data._merge_cn_etf_realtime_today(
                _history_until_previous_trade_day(),
                symbol="512890",
                as_of_bj=datetime(2026, 9, 5, 10, 0, tzinfo=BJ_TZ),
            )

        self.assertEqual([str(value.date()) for value in result["date"]], ["2026-09-02", "2026-09-03"])


if __name__ == "__main__":
    unittest.main()
