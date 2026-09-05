"""节假日累计图的 A 股日历覆盖范围回归测试。"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tools import fund_history_io


def _write_overseas_estimate_cache(path: Path) -> None:
    """写入足以触发节假日累计判断的最小正式缓存。"""
    payload = {
        "records": {
            "overseas:012922:2026-09-07": {
                "market_group": "overseas",
                "fund_code": "012922",
                "run_date_bj": "2026-09-07",
                "valuation_date": "2026-09-07",
                "estimate_return_pct": 1.25,
            }
        },
        "benchmark_records": {
            "benchmark:.NDX:2026-09-07": {
                "run_date_bj": "2026-09-07",
                "valuation_date": "2026-09-07",
                "return_pct": 0.75,
            }
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class HolidayCalendarCoverageTests(unittest.TestCase):
    def _detect_with_calendar(self, trade_dates: set[str]):
        with TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "fund_estimate_return_cache.json"
            _write_overseas_estimate_cache(cache_file)
            with patch.object(
                fund_history_io,
                "_load_a_share_trade_dates",
                return_value=(trade_dates, "测试日历"),
            ):
                return fund_history_io.detect_overseas_holiday_estimate_window(
                    today=date(2026, 9, 7),
                    cache_file=cache_file,
                )

    def test_calendar_ending_before_today_does_not_mark_weekday_as_holiday(self):
        """日历仅到上周五时，周一不能被误判为 A 股休市。"""
        result = self._detect_with_calendar({"2026-09-04"})

        self.assertFalse(result.should_generate)
        self.assertIn("无法判断", result.reason)

    def test_complete_calendar_with_weekday_closure_keeps_holiday_window(self):
        """覆盖当天及之后日期的日历可继续识别明确的工作日休市。"""
        result = self._detect_with_calendar({"2026-09-04", "2026-09-08"})

        self.assertTrue(result.should_generate)
        self.assertEqual(result.start_date, "2026-09-05")
        self.assertEqual(result.end_date, "2026-09-07")

    def test_calendar_without_previous_trade_day_does_not_invent_long_closure(self):
        """回看范围没有上一交易日时，不得臆造长节假日区间。"""
        result = self._detect_with_calendar({"2026-09-08"})

        self.assertFalse(result.should_generate)
        self.assertIn("无法判断", result.reason)


if __name__ == "__main__":
    unittest.main()
