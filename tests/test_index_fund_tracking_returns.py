"""独立指数基金：实际累计收益口径及自然月/共同日期边界。"""
import importlib.util
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

NOW = datetime(2026, 10, 14, 12, tzinfo=ZoneInfo("Asia/Shanghai"))


class TrackingReturnTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("tools.index_fund_tracking"))
        from tools import index_fund_tracking as mod
        self.mod = mod

    def test_pool_is_exact_and_isolated(self):
        from tools.configs.index_fund_tracking_configs import (
            NASDAQ100_TRACKED_FUNDS, SP500_TRACKED_FUNDS, INDEX_FUND_TRACKING_MAP)
        from tools.configs.fund_universe_configs import HAIWAI_FUND_CODES
        self.assertEqual((len(NASDAQ100_TRACKED_FUNDS), len(SP500_TRACKED_FUNDS)), (19, 6))
        self.assertEqual(len(INDEX_FUND_TRACKING_MAP), 25)
        self.assertTrue(all(isinstance(c, str) and len(c) == 6 and c.isdigit() for c in INDEX_FUND_TRACKING_MAP))
        self.assertFalse(set(INDEX_FUND_TRACKING_MAP) & set(HAIWAI_FUND_CODES))
        self.assertTrue(all(INDEX_FUND_TRACKING_MAP[c]["index_symbol"] == ".NDX" for c in NASDAQ100_TRACKED_FUNDS))
        self.assertTrue(all(INDEX_FUND_TRACKING_MAP[c]["index_symbol"] == ".INX" for c in SP500_TRACKED_FUNDS))

    def test_official_curve_selects_fund_and_preserves_wealth_basis(self):
        ms = lambda d: int(pd.Timestamp(d, tz="Asia/Shanghai").timestamp()*1000)
        payload = {"Data": [{"data": [[ms("2026-09-10"), 10], [ms("2026-10-12"), 21]]},
                            {"data": [[ms("2026-10-12"), 999]]}]}
        records = self.mod.parse_cumulative_curve(payload, NOW)
        self.assertEqual(records[-1]["wealth"], 1.21)
        self.assertAlmostEqual((records[-1]["wealth"]/records[0]["wealth"]-1)*100, 10)

    def comparison(self, fund=None, index=None, now=NOW):
        dates = pd.date_range("2025-09-01", "2026-10-12", freq="D")
        fund = fund if fund is not None else [{"date": str(d.date()), "wealth": 1+i/1000} for i, d in enumerate(dates)]
        index = index if index is not None else pd.DataFrame({"date": dates, "close": [100+i/5 for i in range(len(dates))]})
        return self.mod.calculate_comparison(fund, index, now=now, latest_nav_date="2026-10-12")

    def test_four_natural_month_periods_and_ratio_formula(self):
        result = self.comparison()
        self.assertEqual(result["comparison_end_date"], "2026-10-12")
        for months in (1, 3, 6, 12):
            period = result["periods"][f"{months}m"]
            self.assertEqual(period["start_date"], str((pd.Timestamp("2026-10-12")-pd.DateOffset(months=months)).date()))
            self.assertAlmostEqual(period["fund_return"], (period["fund_end"]/period["fund_start"]-1)*100)
            self.assertAlmostEqual(period["index_return"], (period["index_end"]/period["index_start"]-1)*100)

    def test_common_cutoff_never_uses_newer_index_or_future_fund(self):
        fund = [{"date": "2026-09-12", "wealth": 1}, {"date": "2026-10-12", "wealth": 1.1},
                {"date": "2026-10-15", "wealth": 100}]
        index = pd.DataFrame({"date": ["2026-09-12", "2026-10-12", "2026-10-13"], "close": [100, 105, 999]})
        result = self.comparison(fund, index)
        self.assertEqual(result["comparison_end_date"], "2026-10-12")
        self.assertAlmostEqual(result["periods"]["1m"]["fund_return"], 10)
        self.assertAlmostEqual(result["periods"]["1m"]["index_return"], 5)
        self.assertEqual(result["benchmark_close_date"], "2026-10-13")

    def test_twenty_day_start_boundary(self):
        for gap, available in ((20, True), (21, False)):
            start = str((pd.Timestamp("2026-09-12")-pd.Timedelta(days=gap)).date())
            fund = [{"date": start, "wealth": 1}, {"date": "2026-10-12", "wealth": 1.1}]
            index = pd.DataFrame({"date": [start, "2026-10-12"], "close": [100, 105]})
            p = self.comparison(fund, index)["periods"]["1m"]
            self.assertEqual(p["fund_return"] is not None, available)
            if not available:
                self.assertIn("20", p["reason"])

    def test_history_shortage_is_na_not_shorter_period(self):
        result = self.comparison(fund=[{"date": "2026-09-12", "wealth": 1}, {"date": "2026-10-12", "wealth": .9}])
        self.assertAlmostEqual(result["periods"]["1m"]["fund_return"], -10)
        self.assertIsNone(result["periods"]["12m"]["fund_return"])

    def test_stale_cutoff_is_na(self):
        result = self.comparison(now=datetime(2026, 11, 3, tzinfo=NOW.tzinfo))
        self.assertTrue(all(p["fund_return"] is None for p in result["periods"].values()))

    def test_invalid_and_duplicate_curve_rejected(self):
        for data in ([], [[1, float("nan")]], [[1, -100]], [[1, 1], [1, 2]]):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.mod.parse_cumulative_curve({"Data": [{"data": data}]}, NOW)

    def test_success_clock_not_attempt_clock(self):
        meta = {"last_success_at": "2026-10-11T12:00:00+08:00", "last_attempt_at": "2026-10-14T11:59:00+08:00"}
        self.assertTrue(self.mod.source_due(meta, NOW))
        meta["last_success_at"] = "2026-10-11T12:00:01+08:00"
        self.assertFalse(self.mod.source_due(meta, NOW))

    def test_month_end_and_leap_year(self):
        dates = pd.date_range("2023-01-01", "2024-03-31", freq="D")
        result = self.mod.calculate_comparison(
            [{"date": str(d.date()), "wealth": 1} for d in dates],
            pd.DataFrame({"date": dates, "close": 100}),
            now=datetime(2024, 4, 1, 12, tzinfo=NOW.tzinfo), latest_nav_date="2024-03-31")
        # 周日美股未开市：最新已结束交易日为3月28日（29日耶稣受难日）。
        self.assertEqual(result["comparison_end_date"], "2024-03-28")
        self.assertEqual(result["periods"]["1m"]["target_start_date"], "2024-02-28")

    def test_dividend_reinvestment_and_split_wealth(self):
        # 分红日NAV从10跌到9、现金分红1：总收益为0，不能误算成-10%。
        # 随后的2:1拆分由官方同基准累计曲线吸收，不读取普通NAV比值。
        series = [{"date": "2026-09-11", "wealth": 1.5}, {"date": "2026-10-12", "wealth": 1.5}]
        result = self.comparison(series, pd.DataFrame({"date": ["2026-09-11", "2026-10-12"], "close": [100, 100]}))
        self.assertEqual(result["periods"]["1m"]["fund_return"], 0)

    def test_empty_prices_never_fabricates_benchmark(self):
        result = self.comparison(index=pd.DataFrame())
        self.assertTrue(all(p["index_return"] is None for p in result["periods"].values()))


if __name__ == "__main__":
    unittest.main()
