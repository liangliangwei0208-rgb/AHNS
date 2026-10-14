"""指数跟踪状态、共享限购校验、成功时钟和跨机事件合并。"""
import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
from PIL import Image

NOW = datetime(2026, 10, 14, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
CODE = "021000"


class TrackerTests(unittest.TestCase):
    def setUp(self):
        from tools import index_fund_tracking as mod
        self.mod = mod
        root = Path(__file__).parent / "artifacts" / "index_fund_tracking"
        root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=root))
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.limit = self.cache / "fund_purchase_limit_cache.json"
        self.limit.write_text("{}", encoding="utf-8")
        self.source_calls, self.index_calls, self.limit_calls, self.renders = [], [], [], []
        self.values, self.fail_render = {CODE: "1000元"}, False
        dates = pd.date_range("2025-09-01", "2026-10-12", freq="D")
        self.prices = pd.DataFrame({"date": dates, "close": range(100, 100+len(dates))})
        self.source = {"profile": {"fund_name": "真实来源测试基金", "currency": "CNY", "fund_type": "QDII"},
                       "series": [{"date": str(d.date()), "wealth": 1+i/1000} for i, d in enumerate(dates)],
                       "latest_nav_date": "2026-10-12", "source": "eastmoney_official_cumulative", "basis": "y"}

    def fetch(self, code, **kwargs):
        self.limit_calls.append(code)
        data = self.mod.read_mapping(self.limit)
        value = self.values.get(code, "1000元")
        detail = {"value": value, "source": "eastmoney_purchase_status_bulk"}
        if not self.mod.valid_limit(detail):
            return {"value": "未知", "source": "failed", "error": "无有效限购"}
        data[code] = {**detail, "fetched_at": self.now.isoformat()}
        self.mod.atomic_json(self.limit, data)
        return detail

    def source_fetch(self, code, now):
        self.source_calls.append(code)
        return copy.deepcopy(self.source)

    def index_fetch(self, symbol, now):
        self.index_calls.append(symbol)
        return self.prices.copy()

    def render(self, profile, event, comparison, output_file, **kwargs):
        state = self.mod.read_mapping(self.cache / "index_fund_tracking_state.json")
        self.assertIn(event["id"], state["funds"][profile["fund_code"]]["events"])
        if self.fail_render:
            raise OSError("绘图失败")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (10, 10)).save(output_file)
        self.renders.append((profile, event, comparison))
        return output_file

    def run_tracker(self, now=NOW, codes=None, force=False, source_error=False, index_error=False):
        self.now = now
        with patch.object(self.mod, "get_fund_purchase_limit", side_effect=self.fetch), \
                patch.object(self.mod, "fetch_fund_purchase_limit_bulk_map", return_value={}) as bulk, \
                patch.object(self.mod, "fetch_fund_source", side_effect=OSError("收益接口失败") if source_error else self.source_fetch), \
                patch.object(self.mod, "fetch_index_prices", side_effect=OSError("指数接口失败") if index_error else self.index_fetch), \
                patch("tools.index_fund_tracking_image.render_index_fund_limit_change", side_effect=self.render):
            result = self.mod.run_tracking(fund_codes=codes or [CODE], cache_dir=self.cache,
                         output_dir=self.root / "images", now=now, force_refresh=force)
            self.bulk_calls = bulk.call_count
            return result

    def test_first_baseline_no_picture(self):
        self.assertEqual(self.run_tracker(), 0)
        state = self.mod.read_mapping(self.cache / "index_fund_tracking_state.json")
        self.assertEqual(state["funds"][CODE]["baseline"]["value"], "1000元")
        self.assertFalse(self.renders)

    def test_fresh_cache_performs_zero_network(self):
        self.run_tracker()
        self.source_calls.clear(); self.index_calls.clear(); self.limit_calls.clear()
        self.run_tracker(now=NOW+timedelta(hours=71, minutes=59))
        self.assertEqual((self.source_calls, self.index_calls, self.limit_calls, self.bulk_calls), ([], [], [], 0))

    def test_exact_72_hours_refresh(self):
        self.run_tracker()
        self.run_tracker(now=NOW+timedelta(hours=72))
        self.assertEqual(len(self.source_calls), 2)
        self.assertEqual(len(self.limit_calls), 2)

    def test_manual_force_resets_success_clock(self):
        self.run_tracker(); self.run_tracker(now=NOW+timedelta(hours=1), force=True)
        self.run_tracker(now=NOW+timedelta(hours=72))
        self.assertEqual(len(self.source_calls), 2)

    def test_limit_change_generates_once(self):
        self.run_tracker(); self.values[CODE] = "100元"
        self.run_tracker(now=NOW+timedelta(hours=72))
        self.assertEqual(len(self.renders), 1)
        path = self.root / "images" / f"{CODE}.png"
        before = path.stat().st_mtime_ns
        self.run_tracker(now=NOW+timedelta(hours=73))
        self.assertEqual(len(self.renders), 1)
        self.assertEqual(before, path.stat().st_mtime_ns)

    def test_pause_and_resume_are_events(self):
        self.run_tracker()
        for age, value in ((72, "暂停申购"), (144, "不限额度")):
            self.values[CODE] = value
            self.run_tracker(now=NOW+timedelta(hours=age))
        self.assertEqual(len(self.renders), 2)
        self.assertEqual(self.renders[0][1]["new_value"], "暂停申购")

    def test_equivalent_amount_does_not_generate(self):
        self.values[CODE] = "10000元"; self.run_tracker()
        for age, value in ((72, "1万元"), (144, "10,000元")):
            self.values[CODE] = value; self.run_tracker(now=NOW+timedelta(hours=age))
        self.assertFalse(self.renders)

    def test_invalid_limit_keeps_trusted_baseline_and_success(self):
        self.run_tracker(); self.values[CODE] = "限大额"
        self.assertEqual(self.run_tracker(now=NOW+timedelta(hours=72)), 1)
        state = self.mod.read_mapping(self.cache / "index_fund_tracking_state.json")
        self.assertEqual(state["funds"][CODE]["baseline"]["value"], "1000元")
        self.assertFalse(self.renders)

    def test_failed_picture_is_pending_then_retried(self):
        self.run_tracker(); self.values[CODE] = "100元"; self.fail_render = True
        self.assertEqual(self.run_tracker(now=NOW+timedelta(hours=72)), 1)
        self.fail_render = False
        self.assertEqual(self.run_tracker(now=NOW+timedelta(hours=73)), 0)
        self.assertEqual(len(self.renders), 1)

    def test_returns_failure_does_not_lose_event(self):
        self.run_tracker(); self.values[CODE] = "100元"
        self.assertEqual(self.run_tracker(now=NOW+timedelta(hours=72), source_error=True), 1)
        self.assertEqual(len(self.renders), 1)
        cache = self.mod.read_mapping(self.cache / "index_fund_tracking_returns.json")
        self.assertEqual(cache["funds"][CODE]["nav"]["last_success_at"], NOW.isoformat(timespec="seconds"))

    def test_no_redraw_when_returns_recover(self):
        self.run_tracker(source_error=True)
        self.values[CODE] = "100元"
        self.run_tracker(now=NOW+timedelta(hours=72), source_error=True)
        self.assertEqual(len(self.renders), 1)
        self.run_tracker(now=NOW+timedelta(hours=73))
        self.assertEqual(len(self.renders), 1)

    def test_two_funds_share_index_fetch(self):
        self.run_tracker(codes=[CODE, "019441", "017641"])
        self.assertCountEqual(self.index_calls, [".NDX", ".INX"])
        self.assertEqual(len(self.source_calls), 3)

    def test_multiple_funds_generate_separate_images(self):
        self.run_tracker(codes=[CODE, "017641"])
        self.values.update({CODE: "100元", "017641": "暂停申购"})
        self.run_tracker(codes=[CODE, "017641"], now=NOW+timedelta(hours=72))
        self.assertEqual(len(self.renders), 2)

    def test_old_overseas_state_is_untouched(self):
        old = self.cache / "fund_limit_change_state.json"
        old.write_text('{"012922":{"baseline":"keep"}}', encoding="utf-8")
        before = old.read_bytes(); self.run_tracker()
        self.assertEqual(before, old.read_bytes())

    def test_corrupt_state_fails_closed(self):
        state = self.cache / "index_fund_tracking_state.json"
        state.write_text("{invalid", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_tracker()
        self.assertEqual(state.read_text(), "{invalid")

    def test_unknown_code_rejected_before_network(self):
        with self.assertRaises(ValueError):
            self.run_tracker(codes=["999999"])
        self.assertFalse(self.limit_calls)

    def test_shortened_csv_does_not_invalidate_fresh_return_proofs(self):
        self.run_tracker()
        self.prices.tail(15).to_csv(self.cache / "dot_NDX_index_daily.csv", index=False)
        self.index_calls.clear(); self.source_calls.clear()
        self.run_tracker(now=NOW+timedelta(hours=1))
        self.assertEqual((self.index_calls, self.source_calls), ([], []))
        data = self.mod.read_mapping(self.cache / "index_fund_tracking_returns.json")
        self.assertIsNotNone(data["funds"][CODE]["comparison"]["periods"]["12m"]["fund_return"])

    def test_nav_refresh_can_reuse_verified_index_endpoints_if_csv_shortened(self):
        self.run_tracker()
        self.prices.tail(15).to_csv(self.cache / "dot_NDX_index_daily.csv", index=False)
        path = self.cache / "index_fund_tracking_returns.json"
        data = self.mod.read_mapping(path)
        data["funds"][CODE]["nav"]["last_success_at"] = (NOW-timedelta(hours=72)).isoformat()
        self.mod.atomic_json(path, data)
        self.index_calls.clear()
        self.run_tracker(now=NOW+timedelta(hours=1))
        result = self.mod.read_mapping(path)
        self.assertFalse(self.index_calls)
        self.assertIsNotNone(result["funds"][CODE]["comparison"]["periods"]["12m"]["fund_return"])

    def test_merge_unions_pending_preserves_completed_bidirectionally(self):
        a = {"funds": {CODE: {"baseline": {"value": "100元", "observed_at": NOW.isoformat()},
             "events": {"a": {"id": "a", "old_value": "1000元", "new_value": "100元", "observed_at": NOW.isoformat()},
                        "b": {"id": "b", "generated_at": NOW.isoformat()}}}}}
        b = {"funds": {CODE: {"baseline": {"value": "1000元", "observed_at": (NOW-timedelta(days=1)).isoformat()},
             "events": {"c": {"id": "c"}, "b": {"id": "b"}}}}}
        merged = self.mod.merge_tracking_state(a, b)
        self.assertEqual(merged, self.mod.merge_tracking_state(b, a))
        self.assertEqual(set(merged["funds"][CODE]["events"]), {"a", "b", "c"})
        self.assertEqual(merged["funds"][CODE]["events"]["b"]["generated_at"], NOW.isoformat())
        self.assertEqual(merged["funds"][CODE]["baseline"]["value"], "100元")

    def test_invalid_limit_validator(self):
        for value in ("未知", "限大额", "", "0元", None):
            self.assertFalse(self.mod.valid_limit({"value": value}))
        self.assertFalse(self.mod.valid_limit({"value": "100元", "source": "old_cache_after_error"}))
        self.assertTrue(self.mod.valid_limit({"value": "暂不销售"}))

    def test_one_pending_event_per_fund_each_run(self):
        self.run_tracker(); self.fail_render = True
        self.values[CODE] = "100元"; self.run_tracker(now=NOW+timedelta(hours=72))
        self.values[CODE] = "500元"; self.run_tracker(now=NOW+timedelta(hours=144))
        self.fail_render = False
        self.run_tracker(now=NOW+timedelta(hours=145))
        self.assertEqual(len(self.renders), 1)
        self.run_tracker(now=NOW+timedelta(hours=146))
        self.assertEqual(len(self.renders), 2)
        self.assertEqual([x[1]["new_value"] for x in self.renders], ["100元", "500元"])

    def test_completed_event_png_recovers_without_redraw(self):
        self.run_tracker(); self.values[CODE] = "100元"; self.fail_render = True
        self.run_tracker(now=NOW+timedelta(hours=72))
        state = self.mod.read_mapping(self.cache / "index_fund_tracking_state.json")
        event = next(iter(state["funds"][CODE]["events"].values()))
        from PIL.PngImagePlugin import PngInfo
        metadata = PngInfo(); metadata.add_text("index_fund_event_id", event["id"])
        path = self.root / "images" / f"{CODE}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (10, 10)).save(path, pnginfo=metadata)
        before = path.stat().st_mtime_ns
        self.run_tracker(now=NOW+timedelta(hours=73))
        self.assertFalse(self.renders)
        self.assertEqual(before, path.stat().st_mtime_ns)

    def test_corrupt_old_png_does_not_block_new_event_forever(self):
        self.run_tracker()
        path = self.root / "images" / f"{CODE}.png"
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"broken-old-png")
        self.values[CODE] = "100元"
        self.assertEqual(self.run_tracker(now=NOW+timedelta(hours=72)), 0)
        self.assertEqual(len(self.renders), 1)

    def test_png_crc_corruption_is_repaired_on_pending_event(self):
        self.run_tracker()
        path = self.root / "images" / f"{CODE}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (10, 10)).save(path)
        raw = bytearray(path.read_bytes())
        offset = raw.index(b"IDAT")
        length = int.from_bytes(raw[offset-4:offset], "big")
        raw[offset+4+length] ^= 1
        path.write_bytes(raw)
        self.values[CODE] = "100元"
        self.assertEqual(self.run_tracker(now=NOW+timedelta(hours=72)), 0)
        self.assertEqual(len(self.renders), 1)

    def test_semantically_corrupt_state_fails_closed(self):
        state = self.cache / "index_fund_tracking_state.json"
        state.write_text('{"schema_version":1,"funds":{"021000":{"events":[]}}}', encoding="utf-8")
        before = state.read_bytes()
        with self.assertRaises(ValueError):
            self.run_tracker()
        self.assertEqual(before, state.read_bytes())

    def test_stale_cached_returns_become_na_even_when_both_sources_fail(self):
        self.run_tracker(); self.values[CODE] = "100元"
        self.run_tracker(now=NOW+timedelta(days=23), source_error=True, index_error=True)
        self.assertEqual(len(self.renders), 1)
        comparison = self.renders[0][2]
        self.assertTrue(all(p["fund_return"] is None and p["index_return"] is None for p in comparison["periods"].values()))
        self.assertTrue(all("20" in p["reason"] for p in comparison["periods"].values()))


if __name__ == "__main__":
    unittest.main()
