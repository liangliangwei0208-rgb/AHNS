"""每日安全版和节后表复用同一 NDX 基准，别名不应制造失败行。"""

import copy
import unittest
from unittest.mock import patch

import pandas as pd

import safe_fund
import sum_holidays
from tools import fund_history_io


def benchmark(symbol, day="2026-10-06", value=0.5):
    return {"symbol": symbol, "label": "纳斯达克100", "market_group": "overseas",
            "valuation_date": day, "trade_date": day, "return_pct": value,
            "is_final": True, "status": "traded", "data_status": "complete",
            "run_time_bj": "2026-10-07T07:00:00"}


class FundBenchmarkViewTests(unittest.TestCase):
    def test_daily_safe_table_normalizes_ndx_alias_without_duplicate(self):
        for alias in ["NDX", "^NDX", ".NDX"]:
            cache = {"benchmark_records": {"old": benchmark(alias)}}
            before = copy.deepcopy(cache)
            with patch.object(safe_fund, "refresh_vix_footer_item_for_daily", side_effect=lambda item, **kw: item):
                footer = safe_fund.get_benchmark_footer_items(cache, "2026-10-06")
            ndx = [row for row in footer if row["symbol"] == ".NDX"]
            self.assertEqual(len(ndx), 1)
            self.assertEqual(ndx[0]["return_pct"], 0.5)
            self.assertEqual(cache, before)

    def test_daily_safe_table_prefers_exact_final_over_newer_stale_alias(self):
        good = benchmark("^NDX", value=1.0)
        bad = benchmark(".NDX", value=99.0)
        bad.update(trade_date="2026-10-05", status="stale", run_time_bj="2026-10-07T08:00:00")
        with patch.object(safe_fund, "refresh_vix_footer_item_for_daily", side_effect=lambda item, **kw: item):
            rows = safe_fund.get_benchmark_footer_items({"benchmark_records": {"good": good, "bad": bad}},
                                                       "2026-10-06")
        ndx = [r for r in rows if r["symbol"] == ".NDX"]
        self.assertEqual(len(ndx), 1)
        self.assertEqual(ndx[0]["return_pct"], 1.0)

    def test_post_holiday_table_reads_canonical_history(self):
        rows = fund_history_io._deduplicate_benchmark_alias_records(pd.DataFrame([
            benchmark("^NDX"), benchmark(".NDX"),
        ]))
        footer = sum_holidays._daily_benchmark_footer_items(rows)
        self.assertEqual(len(footer), 1)
        self.assertEqual(footer[0]["symbol"], ".NDX")
        self.assertEqual(footer[0]["return_pct"], 0.5)


if __name__ == "__main__":
    unittest.main()
