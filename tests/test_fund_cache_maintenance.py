"""基金 key 型缓存的容量保护与无变化写入回归测试。"""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from tools import fund_region_allocation
from tools.fund_cache_maintenance import prune_inactive_fund_records, write_json_if_changed


class FundCacheMaintenanceTests(unittest.TestCase):
    def test_write_json_if_changed_keeps_identical_file_untouched(self):
        """内容相同的状态缓存不应再因一次检查而改写文件。"""
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            payload = {"012922:top10": {"fingerprint": "same", "last_checked_at": "2026-09-01T00:00:00"}}
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            before = path.read_bytes()

            changed = write_json_if_changed(path, payload)

            self.assertFalse(changed)
            self.assertEqual(path.read_bytes(), before)

    def test_prune_inactive_fund_records_keeps_active_recent_and_undated_entries(self):
        """仅回收基金池外且超过 365 天的可判定旧 key，避免误删手动缓存。"""
        cache = {
            "012922:top10": {"last_checked_at": "2024-01-01T00:00:00"},
            "999999:top10": {"last_checked_at": "2024-01-01T00:00:00"},
            "888888:top10": {"last_checked_at": "2026-09-01T00:00:00"},
            "777777:top10": {"fingerprint": "no-date"},
        }

        pruned, removed = prune_inactive_fund_records(
            cache,
            active_fund_codes={"012922"},
            now=datetime(2026, 9, 11, 12, 0),
        )

        self.assertEqual(removed, ["999999:top10"])
        self.assertIn("012922:top10", pruned)
        self.assertIn("888888:top10", pruned)
        self.assertIn("777777:top10", pruned)

    def test_region_state_reuses_prior_timestamp_when_data_is_unchanged(self):
        """地区图数据未变时，状态缓存不能仅因检查时间而制造 Git 变更。"""
        previous = {
            "fingerprint": "same",
            "report_date": "2026-06-30",
            "valid": True,
            "last_checked_at": "2026-09-01T10:00:00",
        }
        record = {
            "fingerprint": "same",
            "report_date": "2026-06-30",
            "valid": True,
        }

        item = fund_region_allocation._record_state_item(record, previous=previous)

        self.assertEqual(item, previous)

    def test_region_state_keeps_recent_manual_fund_until_retention(self):
        """自动扫描不能立即丢弃 365 天内手动生成过的基金状态。"""
        prior_funds = {
            "999999": {
                "fingerprint": "manual-fingerprint",
                "report_date": "2026-06-30",
                "valid": True,
                "last_checked_at": "2026-09-01T10:00:00",
            }
        }
        records = {
            "012922": {
                "fingerprint": "pool-fingerprint",
                "report_date": "2026-06-30",
                "valid": True,
            }
        }

        next_funds, removed = fund_region_allocation._build_next_state_funds(
            records,
            prior_funds,
            active_fund_codes={"012922"},
            now=datetime(2026, 9, 11, 12, 0),
        )

        self.assertFalse(removed)
        self.assertIn("012922", next_funds)
        self.assertIn("999999", next_funds)


if __name__ == "__main__":
    unittest.main()
