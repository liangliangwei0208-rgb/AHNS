"""运行前缓存容量自检的回归测试。"""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import check_project


class CacheGrowthCheckTests(unittest.TestCase):
    def test_cache_check_warns_without_writing_or_deleting(self):
        """过期短缓存和基金池外状态应告警，但自检不得改动任何缓存文件。"""
        self.assertTrue(hasattr(check_project, "check_cache_growth_policy"))
        with TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            short_cache = cache_dir / "premarket_quote_cache.json"
            state_cache = cache_dir / "fund_holding_change_state.json"
            stale_record = {
                "fetched_at_bj": "2026-01-01T09:00:00+08:00",
                "return_pct": 1.0,
                "value": None,
                "value_type": "return_pct",
                "status": "traded",
                "source": "test",
                "trade_date": "2026-01-01",
                "quote_time_bj": "2026-01-01T09:00:00+08:00",
                "error": "",
            }
            short_cache.write_text(
                json.dumps(
                    {
                        f"US:TEST{index}": dict(stale_record)
                        for index in range(501)
                    }
                ),
                encoding="utf-8",
            )
            state_cache.write_text(
                json.dumps({"999999:top10": {"last_checked_at": "2026-01-01T09:00:00"}}),
                encoding="utf-8",
            )
            before = {path: path.read_bytes() for path in (short_cache, state_cache)}

            items = check_project.check_cache_growth_policy(
                cache_dir=cache_dir,
                now=datetime(2026, 1, 3, 9, 0),
                active_fund_codes={"012922"},
            )

            details = "\n".join(item.detail for item in items)
            self.assertIn("过期", details)
            self.assertIn("超过配置上限", details)
            self.assertIn("基金池外", details)
            self.assertTrue(any(item.level == "WARN" for item in items))
            self.assertEqual(before, {path: path.read_bytes() for path in (short_cache, state_cache)})

    def test_cache_check_reports_directory_total_and_keyed_cache_retention(self):
        """体检应说明总容量与基金 key 缓存的 365 天回收边界，且保持只读。"""
        with TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            holdings = cache_dir / "fund_holdings_cache.json"
            holdings.write_text(
                json.dumps({"999999:top10": {"last_checked_at": "2024-01-01T00:00:00"}}),
                encoding="utf-8",
            )
            before = holdings.read_bytes()

            items = check_project.check_cache_growth_policy(
                cache_dir=cache_dir,
                now=datetime(2026, 9, 11, 12, 0),
                active_fund_codes={"012922"},
            )

            details = "\n".join(item.detail for item in items)
            self.assertIn("缓存目录总大小", details)
            self.assertIn("365 天", details)
            self.assertIn("基金池外", details)
            self.assertIn("发现 1 条基金池外且超过 365 天", details)
            self.assertEqual(holdings.read_bytes(), before)

    def test_cache_check_reports_vix_daily_row_limit_without_rewriting_it(self):
        """VIX 日线缓存也应在只读体检中暴露 1000 条上限。"""
        with TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            vix_cache = cache_dir / "vix_index_daily.csv"
            rows = ["date,close"] + [f"2020-01-{(index % 28) + 1:02d},{index}" for index in range(1_001)]
            vix_cache.write_text("\n".join(rows) + "\n", encoding="utf-8")
            before = vix_cache.read_bytes()

            items = check_project.check_cache_growth_policy(cache_dir=cache_dir)

            details = "\n".join(item.detail for item in items)
            self.assertTrue(any(item.title == "VIX 日线缓存" for item in items))
            self.assertIn("1000", details)
            self.assertTrue(any(item.level == "WARN" and item.title == "VIX 日线缓存" for item in items))
            self.assertEqual(vix_cache.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
