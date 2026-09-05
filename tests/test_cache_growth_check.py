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


if __name__ == "__main__":
    unittest.main()
