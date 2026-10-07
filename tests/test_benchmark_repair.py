"""基准缺口修复仅写 benchmark_records，正式值和基金记录必须保留。"""

import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from tools import fund_cache_maintenance as maintenance, fund_history_io as history
from tools import get_top10_holdings as holdings


def final_record(symbol, day, value=1.0):
    return dict(symbol=symbol, label=symbol, valuation_date=day, trade_date=day,
                return_pct=value, status="traded", is_final=True, data_status="complete",
                market_group="overseas", run_time_bj="2026-10-07T07:00:00")


class BenchmarkRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache_dir = Path(self.temp.name)
        self.path = self.cache_dir / "fund_estimate_return_cache.json"
        self.initial = {"version": 2, "records": {"old-fund": {"valuation_date": "2000-01-01"}},
                        "benchmark_records": {"benchmark:.INX:2026-10-01":
                                              final_record(".INX", "2026-10-01", 2.5)}}
        self.write(self.initial)
        dates = pd.bdate_range(end="2026-10-06", periods=220)
        self.prices = pd.DataFrame({"date": dates, "close": range(1000, 1220)})
        self.prices.to_csv(self.cache_dir / "dot_NDX_index_daily.csv", index=False)

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def repair(self, **kwargs):
        return maintenance.repair_missing_us_index_benchmark_records(
            start_date="2026-10-01", end_date="2026-10-06", cache_dir=self.cache_dir,
            now=datetime(2026, 10, 7, 12, tzinfo=ZoneInfo("Asia/Shanghai")), **kwargs,
        )

    def test_missing_ndx_repaired_final_inx_and_funds_unchanged_no_network(self):
        with patch.object(holdings, "fetch_us_index_return_pct_from_rsi_module",
                          side_effect=AssertionError("network")):
            result = self.repair()
        after = self.read()
        self.assertEqual(after["records"], self.initial["records"])
        self.assertEqual(after["benchmark_records"]["benchmark:.INX:2026-10-01"],
                         self.initial["benchmark_records"]["benchmark:.INX:2026-10-01"])
        days = [r["valuation_date"] for r in result["repaired"] if r["symbol"] == ".NDX"]
        self.assertEqual(days, ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"])
        row = after["benchmark_records"]["benchmark:.NDX:2026-10-05"]
        self.assertTrue(row["is_final"])
        self.assertEqual(row["trade_date"], "2026-10-05")
        self.assertAlmostEqual(row["return_pct"], (1218 / 1217 - 1) * 100)

    def test_repeat_repair_is_byte_for_byte_noop(self):
        self.repair(symbols=[".NDX"])
        before = self.path.read_bytes()
        result = self.repair(symbols=[".NDX"])
        self.assertEqual(result["repaired"], [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_alias_final_is_preserved_without_duplicate_or_deletion(self):
        record = final_record("^NDX", "2026-10-01", 4.0)
        self.initial["benchmark_records"]["benchmark:^NDX:2026-10-01"] = record
        self.write(self.initial)
        self.repair(symbols=["NDX"])
        records = self.read()["benchmark_records"]
        self.assertEqual(records["benchmark:^NDX:2026-10-01"], record)
        self.assertNotIn("benchmark:.NDX:2026-10-01", records)

    def test_missing_previous_session_is_not_a_multiday_daily_return(self):
        self.prices[self.prices["date"] != pd.Timestamp("2026-10-02")].to_csv(
            self.cache_dir / "dot_NDX_index_daily.csv", index=False)
        # 保持完整性最低长度，单独考察尾部缺少前一真实交易日的情况。
        earlier = pd.DataFrame({"date": [pd.Timestamp("2025-01-01")], "close": [900]})
        pd.concat([earlier, self.prices[self.prices["date"] != pd.Timestamp("2026-10-02")]]).to_csv(
            self.cache_dir / "dot_NDX_index_daily.csv", index=False)
        self.repair(symbols=[".NDX"])
        records = self.read()["benchmark_records"]
        self.assertNotIn("benchmark:.NDX:2026-10-02", records)
        self.assertNotIn("benchmark:.NDX:2026-10-05", records)

    def test_complete_anchor_cache_is_used_when_csv_is_unavailable(self):
        self.prices.iloc[:10].to_csv(self.cache_dir / "dot_NDX_index_daily.csv", index=False)
        anchor = final_record(".NDX", "2026-10-01", 1.25)
        (self.cache_dir / "security_return_cache.json").write_text(json.dumps({
            "SECURITY:US:.NDX:2026-10-01": anchor,
        }), encoding="utf-8")
        self.repair(symbols=[".NDX"])
        row = self.read()["benchmark_records"]["benchmark:.NDX:2026-10-01"]
        self.assertEqual(row["return_pct"], 1.25)

    def test_failed_stale_anchor_cannot_be_repaired(self):
        row = final_record(".NDX", "2026-10-01", 4.0)
        row.update(is_final=False, status="stale", trade_date="2026-09-30")
        self.initial["benchmark_records"]["benchmark:.NDX:2026-10-01"] = row
        self.write(self.initial)
        self.repair(symbols=[".NDX"])
        repaired = self.read()["benchmark_records"]["benchmark:.NDX:2026-10-01"]
        self.assertEqual(repaired["trade_date"], "2026-10-01")
        self.assertTrue(repaired["is_final"])

    def test_incomplete_session_is_not_published(self):
        with patch.object(holdings, "_market_session_complete", return_value=False):
            result = self.repair(symbols=[".NDX"])
        self.assertEqual(result["repaired"], [])
        self.assertEqual(self.read(), self.initial)

    def test_calendar_failure_does_not_modify_cache(self):
        with patch.object(holdings, "_market_schedule", side_effect=RuntimeError("calendar unavailable")):
            with self.assertRaisesRegex(RuntimeError, "calendar unavailable"):
                self.repair(symbols=[".NDX"])
        self.assertEqual(self.read(), self.initial)

    def test_missing_and_nonfinite_prices_are_not_published(self):
        self.prices["close"] = self.prices["close"].astype(float)
        self.prices.loc[self.prices["date"] == pd.Timestamp("2026-10-01"), "close"] = float("inf")
        self.prices.to_csv(self.cache_dir / "dot_NDX_index_daily.csv", index=False)
        self.repair(symbols=[".NDX"])
        self.assertEqual(self.read(), self.initial)

    def test_default_lookback_is_fourteen_calendar_days(self):
        result = maintenance.repair_missing_us_index_benchmark_records(
            end_date="2026-10-06", cache_dir=self.cache_dir, symbols=[],
        )
        self.assertEqual(result["start_date"], "2026-09-23")

    def test_optional_network_refresh_occurs_once_per_index(self):
        from tools import rsi_data
        self.prices.iloc[:10].to_csv(self.cache_dir / "dot_NDX_index_daily.csv", index=False)

        def refresh(**kwargs):
            self.prices.to_csv(self.cache_dir / "dot_NDX_index_daily.csv", index=False)
            return self.prices

        with patch.object(rsi_data, "get_us_index_akshare", side_effect=refresh) as getter:
            result = self.repair(symbols=[".NDX"], allow_network=True)
        self.assertEqual(getter.call_count, 1)
        self.assertEqual(len(result["repaired"]), 4)

    def test_normal_benchmark_entry_runs_cache_only_healing_and_can_disable_writes(self):
        with patch.object(maintenance, "repair_missing_us_index_benchmark_records") as repair, \
             patch.object(holdings, "_enabled_market_benchmark_specs", return_value=[]):
            holdings.get_us_index_benchmark_items(valuation_anchor_date="2026-10-06")
            repair.assert_called_once_with(end_date="2026-10-06")
            repair.reset_mock()
            holdings.get_us_index_benchmark_items(cache_enabled=False, valuation_anchor_date="2026-10-06")
            repair.assert_not_called()


class BenchmarkAliasTests(unittest.TestCase):
    def test_ndx_aliases_make_one_row_and_do_not_compound_duplicates(self):
        for alias in ["NDX", "^NDX", ".NDX"]:
            rows = [final_record(alias, "2026-10-01", 1.0),
                    final_record(".NDX", "2026-10-02", 2.0)]
            rows.append(copy.deepcopy(rows[0]))
            result = history.build_benchmark_cumulative_dataframe(pd.DataFrame(rows))
            ndx = result[result["指数代码"] == ".NDX"]
            self.assertEqual(len(ndx), 1)
            self.assertEqual(ndx.iloc[0]["指数名称"], "纳斯达克100")
            self.assertAlmostEqual(ndx.iloc[0]["区间累计涨跌幅"], 3.02)
            self.assertEqual(ndx.iloc[0]["起始估值日"], "2026-10-01")

    def test_read_filters_canonical_aliases_and_preserves_raw_records(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "history.json"
            rows = {"a": final_record("^NDX", "2026-10-01"),
                    "b": final_record("NDX", "2026-10-01"),
                    "c": final_record("SPX", "2026-10-01", 2.0)}
            path.write_text(json.dumps({"benchmark_records": rows}), encoding="utf-8")
            before = path.read_bytes()
            result = history.get_benchmark_estimate_records(
                "2026-10-01", "2026-10-01", symbols=[".NDX", "^GSPC"],
                require_final=True, cache_file=path,
            )
            self.assertEqual(set(result["symbol"]), {".NDX", ".INX"})
            self.assertEqual(len(result), 2)
            self.assertEqual(before, path.read_bytes())

    def test_final_target_day_alias_wins_over_newer_stale_alias(self):
        good = final_record("^NDX", "2026-10-01", 1.0)
        stale = final_record(".NDX", "2026-10-01", 100.0)
        stale.update(trade_date="2026-09-30", status="stale", run_time_bj="2026-10-07T08:00:00")
        result = history.build_benchmark_cumulative_dataframe(pd.DataFrame([good, stale]))
        ndx = result[result["指数代码"] == ".NDX"].iloc[0]
        self.assertAlmostEqual(ndx["区间累计涨跌幅"], 1.0)


if __name__ == "__main__":
    unittest.main()
