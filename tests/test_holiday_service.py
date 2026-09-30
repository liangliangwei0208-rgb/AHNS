"""Holiday Service flow and valuation window regressions."""

from __future__ import annotations

import json
import io
import os
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

import git_main
from git_main import resolve_workflow_steps, select_workflow_steps_for_time
from tools import fund_history_io
from tools import get_top10_holdings
from tools.configs.workflow_configs import SERVICE_WORKFLOW_STEPS
import safe_holidays


class HolidayServiceTests(unittest.TestCase):
    def test_child_runner_does_not_inherit_holiday_anchor_flag(self):
        with patch.dict(os.environ, {"AHNS_HOLIDAY_COMPLETE_SESSION": "1"}), \
                patch.object(git_main.subprocess, "Popen") as popen:
            popen.return_value.stdout = io.StringIO("")
            popen.return_value.wait.return_value = 0
            git_main.stream_script_output(Path("main.py"))
        self.assertNotIn("AHNS_HOLIDAY_COMPLETE_SESSION", popen.call_args.kwargs["env"])

    def test_anchor_waits_until_every_open_market_has_closed(self):
        def schedule(market, start, end):
            if str(start) != "2026-09-25":
                return pd.DataFrame({"market_close": [pd.Timestamp("2026-09-24 20:00:00+00:00")]},
                                    index=pd.DatetimeIndex(["2026-09-24"])) if str(start) == "2026-09-24" else pd.DataFrame()
            close = "2026-09-25 20:00:00+00:00" if market == "US" else "2026-09-25 08:00:00+00:00"
            return pd.DataFrame({"market_close": [pd.Timestamp(close)]},
                                index=pd.DatetimeIndex(["2026-09-25"]))

        with patch.object(get_top10_holdings, "_market_schedule", side_effect=schedule):
            result = get_top10_holdings.determine_latest_complete_cross_market_session(
                markets=("US", "HK"), now=datetime(2026, 9, 25, 10, tzinfo=timezone.utc),
            )
            next_morning = get_top10_holdings.determine_latest_complete_cross_market_session(
                markets=("US", "HK"), now=datetime(2026, 9, 26, 2, tzinfo=timezone.utc),
            )
        self.assertEqual(result, "2026-09-24")
        self.assertEqual(next_morning, "2026-09-25")

    def test_calendar_error_is_not_treated_as_market_closure(self):
        with patch.object(get_top10_holdings, "_market_schedule", side_effect=RuntimeError("calendar unavailable")):
            with self.assertRaisesRegex(RuntimeError, "交易日历读取失败"):
                get_top10_holdings.determine_latest_complete_cross_market_session(
                    markets=("US",), now=datetime(2026, 9, 25, 10, tzinfo=timezone.utc),
                )

    def test_us_closed_hk_open_can_be_completed_anchor(self):
        def schedule(market, start, end):
            if str(start) == "2026-10-02" and market == "HK":
                return pd.DataFrame({"market_close": [pd.Timestamp("2026-10-02 08:00:00+00:00")]},
                                    index=pd.DatetimeIndex(["2026-10-02"]))
            return pd.DataFrame()

        with patch.object(get_top10_holdings, "_market_schedule", side_effect=schedule):
            result = get_top10_holdings.determine_latest_complete_cross_market_session(
                markets=("US", "HK"), now=datetime(2026, 10, 2, 10, tzinfo=timezone.utc),
            )
        self.assertEqual(result, "2026-10-02")

    def test_adjacent_weekend_and_first_reopen_share_holiday(self):
        dates = {"2026-09-24", "2026-09-28", "2026-09-29"}
        with patch.object(fund_history_io, "_load_a_share_trade_dates", return_value=(dates, "test")):
            saturday = fund_history_io.detect_a_share_holiday_context(date(2026, 9, 26))
            reopen = fund_history_io.detect_a_share_holiday_context(date(2026, 9, 28))
        self.assertTrue(saturday.is_holiday)
        self.assertEqual(saturday.start_date, "2026-09-25")
        self.assertTrue(reopen.is_first_reopen)
        self.assertEqual(reopen.end_date, "2026-09-27")

    def test_plain_weekend_does_not_trigger(self):
        dates = {"2026-09-18", "2026-09-21"}
        with patch.object(fund_history_io, "_load_a_share_trade_dates", return_value=(dates, "test")):
            result = fund_history_io.detect_a_share_holiday_context(date(2026, 9, 19))
        self.assertFalse(result.is_holiday)

    def test_cross_year_holiday_weekend_is_included(self):
        dates = {"2026-12-31", "2027-01-04"}
        with patch.object(fund_history_io, "_load_a_share_trade_dates", return_value=(dates, "test")):
            result = fund_history_io.detect_a_share_holiday_context(date(2027, 1, 2))
        self.assertTrue(result.is_holiday)
        self.assertEqual(result.previous_trade_date, "2026-12-31")
        self.assertEqual(result.start_date, "2027-01-01")

    def test_calendar_refreshes_when_cached_dates_do_not_cover_next_session(self):
        with patch.object(fund_history_io, "_load_a_share_trade_dates_from_file_cache",
                          return_value=({"2026-09-24"}, "fresh-cache")), \
                patch.object(fund_history_io, "_load_a_share_trade_dates_from_akshare",
                             return_value=({"2026-09-24", "2026-09-28"}, "network")) as fetch, \
                patch.object(fund_history_io, "_save_a_share_trade_dates_to_file_cache"):
            dates, _ = fund_history_io._load_a_share_trade_dates(
                strict=True, required_date=date(2026, 9, 25),
            )
        self.assertIn("2026-09-28", dates)
        fetch.assert_called_once()

    def test_holiday_window_uses_valuation_date_not_run_date(self):
        with TemporaryDirectory() as directory:
            cache_file = Path(directory) / "estimates.json"
            cache_file.write_text(json.dumps({"records": {
                "overseas:012922:2026-09-24": {
                    "market_group": "overseas", "fund_code": "012922",
                    "run_date_bj": "2026-09-25", "valuation_date": "2026-09-24",
                    "estimate_return_pct": 1.0,
                },
            }, "benchmark_records": {}}), encoding="utf-8")
            with patch.object(fund_history_io, "_load_a_share_trade_dates", return_value=(
                {"2026-09-24", "2026-09-28"}, "test",
            )):
                window = fund_history_io.detect_overseas_holiday_estimate_window(
                    today=date(2026, 9, 25), cache_file=cache_file,
                )
            self.assertTrue(window.should_generate)
            self.assertEqual(window.date_field, "valuation_date")
            self.assertEqual(window.overseas_valuation_dates, ())

    def test_next_morning_includes_previous_complete_holiday_session(self):
        with TemporaryDirectory() as directory:
            cache_file = Path(directory) / "estimates.json"
            cache_file.write_text(json.dumps({"records": {
                "overseas:012922:2026-09-25": {
                    "market_group": "overseas", "fund_code": "012922",
                    "run_date_bj": "2026-09-26", "valuation_date": "2026-09-25",
                    "is_final": True, "estimate_return_pct": 1.0,
                },
            }, "benchmark_records": {}}), encoding="utf-8")
            with patch.object(fund_history_io, "_load_a_share_trade_dates", return_value=(
                {"2026-09-24", "2026-09-28"}, "test",
            )):
                window = fund_history_io.detect_overseas_holiday_estimate_window(
                    today=date(2026, 9, 26), cache_file=cache_file,
                )
        self.assertTrue(window.should_generate)
        self.assertEqual(window.overseas_valuation_dates, ("2026-09-25",))

    def test_closed_only_fund_day_is_not_counted_as_effective(self):
        daily = pd.DataFrame([
            {"fund_code": "012922", "valuation_date": "2026-10-02", "market_status": {"US": "closed"}},
            {"fund_code": "015205", "valuation_date": "2026-10-02", "market_status": {"HK": "traded"}},
        ])
        filtered = fund_history_io.filter_effective_holiday_fund_days(daily)
        self.assertEqual(filtered["fund_code"].tolist(), ["015205"])

    def test_holiday_reader_accepts_partial_close_but_not_intraday_or_missing_value(self):
        day = "2026-09-25"
        records = {}
        for code, stage, status, value in [
            ("000001", "final", "complete", 1.0),
            ("000002", "partial", "partial", 2.0),
            ("000003", "partial", "stale", -0.5),
            ("000004", "intraday", "partial", 3.0),
            ("000005", "partial", "partial", None),
        ]:
            records[f"overseas:{code}:{day}"] = {
                "market_group": "overseas", "fund_code": code,
                "valuation_date": day, "run_date_bj": day,
                "stage": stage, "data_status": status,
                "valuation_mode": "intraday" if stage == "intraday" else "last_close",
                "is_final": stage == "final", "estimate_return_pct": value,
            }
        with TemporaryDirectory() as directory:
            cache_file = Path(directory) / "estimates.json"
            cache_file.write_text(json.dumps({"records": records}), encoding="utf-8")
            selected = fund_history_io.get_fund_estimate_records(
                start_date=day, end_date=day, cache_file=cache_file,
                fund_codes=list(code for code in ("000001", "000002", "000003", "000004", "000005")),
                include_intraday=False, include_partial_close=True,
            )
            complete_only = fund_history_io.get_fund_estimate_records(
                start_date=day, end_date=day, cache_file=cache_file,
                fund_codes=["000001", "000002", "000003"], include_intraday=False,
            )
        self.assertEqual(selected["fund_code"].tolist(), ["000001", "000002", "000003"])
        self.assertEqual(complete_only["fund_code"].tolist(), ["000001"])

    def test_partial_market_error_does_not_hide_other_traded_holdings(self):
        day = "2026-09-25"
        daily = pd.DataFrame([
            {"fund_code": "000001", "valuation_date": day, "market_status": {"US": "missing"},
             "market_trade_dates": {"US": day}},
            {"fund_code": "000002", "valuation_date": day, "market_status": {"US": "stale"},
             "market_trade_dates": {"US": day}},
            {"fund_code": "000003", "valuation_date": day, "market_status": {"US": "missing"},
             "market_trade_dates": {}, "residual_benchmark_status": "traded",
             "residual_benchmark_trade_date": day},
            {"fund_code": "000004", "valuation_date": day, "market_status": {"US": "closed"},
             "market_trade_dates": {}},
            {"fund_code": "000005", "valuation_date": day, "market_status": {"US": "missing"},
             "market_trade_dates": {}},
            {"fund_code": "000006", "valuation_date": day, "market_status": {"US": "stale"},
             "market_trade_dates": {"US": "2026-09-24"}, "stage": "partial",
             "data_status": "stale", "valuation_mode": "last_close", "estimate_return_pct": -0.2},
            {"fund_code": "000007", "valuation_date": day, "market_status": {"US": "missing"},
             "market_trade_dates": {}, "stage": "partial",
             "data_status": "partial", "valuation_mode": "last_close", "estimate_return_pct": 0.3},
            {"fund_code": "000008", "valuation_date": day, "market_status": {"US": "missing"},
             "market_trade_dates": {}, "stage": "partial",
             "data_status": "partial", "effective_valuation_mode": "last_close",
             "estimate_return_pct": 0.1},
        ])
        filtered = fund_history_io.filter_effective_holiday_fund_days(daily)
        self.assertEqual(filtered["fund_code"].tolist(),
                         ["000001", "000002", "000003", "000006", "000007", "000008"])

    def test_holiday_image_uses_all_close_estimates_without_status_marks(self):
        day = "2026-09-25"
        records = {}
        for code, name, stage, status, value in [
            ("002891", "基金甲", "final", "complete", 1.0),
            ("006555", "基金乙", "partial", "partial", 2.0),
            ("160140", "基金丙", "partial", "stale", -0.5),
            ("012922", "盘中基金", "intraday", "partial", 4.0),
        ]:
            records[f"overseas:{code}:{day}"] = {
                "market_group": "overseas", "fund_code": code, "fund_name": name,
                "valuation_date": day, "run_date_bj": day,
                "stage": stage, "data_status": status,
                "valuation_mode": "intraday" if stage == "intraday" else "last_close",
                "is_final": stage == "final", "estimate_return_pct": value,
                "market_status": {"US": "traded" if stage == "final" else status},
                "market_trade_dates": {"US": day},
            }
        window = fund_history_io.HolidayEstimateWindow(
            should_generate=True, start_date=day, end_date=day,
            date_field="valuation_date", date_label="9.25-9.25",
        )
        with TemporaryDirectory() as directory:
            cache_file = Path(directory) / "fund_estimate_return_cache.json"
            cache_file.write_text(json.dumps({"records": records, "benchmark_records": {}}), encoding="utf-8")
            with patch.object(fund_history_io, "CACHE_DIR", Path(directory)), \
                    patch.object(safe_holidays, "detect_overseas_holiday_estimate_window", return_value=window), \
                    patch.object(safe_holidays, "save_cumulative_estimate_table_image") as save_image, \
                    patch.object(safe_holidays, "apply_safe_public_watermarks"):
                safe_holidays.main()
        image_rows = save_image.call_args.kwargs["summary_df"]
        self.assertEqual(image_rows["基金名称"].tolist(), ["基金乙***", "基金甲***", "基金丙***"])
        self.assertEqual(image_rows["区间累计预估收益率"].round(2).tolist(), [2.0, 1.0, -0.5])
        self.assertEqual(save_image.call_args.kwargs["footnote_text"],
                         "鱼师AHNS，依据季报持仓及指数估算，最终以基金公司公告为准。")

    def test_holiday_window_reports_partial_close_date(self):
        day = "2026-09-25"
        with TemporaryDirectory() as directory:
            cache_file = Path(directory) / "estimates.json"
            cache_file.write_text(json.dumps({"records": {
                f"overseas:012922:{day}": {
                    "market_group": "overseas", "fund_code": "012922", "fund_name": "测试基金",
                    "valuation_date": day, "run_date_bj": "2026-09-26",
                    "stage": "partial", "data_status": "partial", "is_final": False,
                    "valuation_mode": "last_close", "estimate_return_pct": 0.4,
                    "market_status": {"US": "missing"}, "market_trade_dates": {"US": day},
                },
            }}), encoding="utf-8")
            with patch.object(fund_history_io, "_load_a_share_trade_dates", return_value=(
                {"2026-09-24", "2026-09-28"}, "test",
            )):
                window = fund_history_io.detect_overseas_holiday_estimate_window(
                    today=date(2026, 9, 26), cache_file=cache_file,
                )
        self.assertEqual(window.overseas_valuation_dates, (day,))

    def test_placeholder_png_is_actually_rendered(self):
        window = fund_history_io.HolidayEstimateWindow(
            should_generate=True, start_date="2026-09-25", end_date="2026-09-25",
            date_field="valuation_date", date_label="9.25-9.25",
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "safe_holidays.png"
            with patch.object(safe_holidays, "OUTPUT_FILE", str(path)), \
                    patch.object(safe_holidays, "detect_overseas_holiday_estimate_window", return_value=window), \
                    patch.object(safe_holidays, "get_fund_estimate_records", return_value=pd.DataFrame()), \
                    patch.object(safe_holidays, "get_benchmark_estimate_records", return_value=pd.DataFrame()):
                safe_holidays.main()
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 1000)

    def test_service_holiday_keeps_formal_and_realtime_steps(self):
        steps = resolve_workflow_steps(SERVICE_WORKFLOW_STEPS)
        selected = select_workflow_steps_for_time(
            steps, current_time=datetime(2026, 9, 25, 22, 45),
            service_holiday=True,
        )
        names = {step.script_path.name for step in selected}
        self.assertEqual(names, {
            "stock_analysis.py", "fund_limit_change.py", "main.py", "safe_fund.py",
            "safe_holidays.py", "intraday_fund.py", "gu_zhai_xi.py", "gdp.py",
        })

    def test_first_reopen_keeps_both_cumulative_reports(self):
        steps = resolve_workflow_steps(SERVICE_WORKFLOW_STEPS)
        selected = select_workflow_steps_for_time(
            steps, current_time=datetime(2026, 9, 28, 18, 0),
            service_first_reopen=True,
        )
        names = {step.script_path.name for step in selected}
        self.assertIn("sum_holidays.py", names)
        self.assertIn("safe_holidays.py", names)
        self.assertIn("safe_fund.py", names)
        self.assertIn("premarket_fund.py", names)
        safe_step = next(step for step in selected if step.script_path.name == "safe_holidays.py")
        self.assertIn("--first-reopen", safe_step.args)

    def test_service_entry_scopes_complete_session_to_formal_estimate(self):
        context = fund_history_io.AShareHolidayContext(is_holiday=True, verified=True, reason="test")
        observed = []

        def run_step(step, *, extra_env=None):
            observed.append((step.script_path.name, extra_env))
            return git_main.ScriptResult(step.name, step.script_path.name, step.script_path,
                                         0, 0.0, [], step.collect_images, [])

        with patch.object(fund_history_io, "detect_a_share_holiday_context", return_value=context), \
                patch.object(git_main, "run_script", side_effect=run_step):
            code = git_main.main(["--no-send"], entry_name="service_main.py",
                                 workflow_steps=SERVICE_WORKFLOW_STEPS, service_mode=True)
        self.assertEqual(code, 0)
        self.assertIn(("main.py", {"AHNS_HOLIDAY_COMPLETE_SESSION": "1"}), observed)
        self.assertIn(("safe_holidays.py", None), observed)

    def test_unverified_calendar_is_reported_without_stopping_normal_steps(self):
        context = fund_history_io.AShareHolidayContext(reason="无法核实A股交易日历")
        observed = []

        def run_step(step):
            observed.append(step.script_path.name)
            return git_main.ScriptResult(step.name, step.script_path.name, step.script_path,
                                         0, 0.0, [], step.collect_images, [])

        with patch.object(fund_history_io, "detect_a_share_holiday_context", return_value=context), \
                patch.object(git_main, "run_script", side_effect=run_step):
            code = git_main.main(["--no-send"], workflow_steps=SERVICE_WORKFLOW_STEPS,
                                 service_mode=True)
        self.assertEqual(code, 1)
        self.assertIn("stock_analysis.py", observed)

    def test_empty_holiday_data_still_saves_placeholder(self):
        window = fund_history_io.HolidayEstimateWindow(
            should_generate=True, start_date="2026-09-25", end_date="2026-09-25",
            date_field="valuation_date", date_label="9.25-9.25",
        )
        with patch.object(safe_holidays, "detect_overseas_holiday_estimate_window", return_value=window), \
                patch.object(safe_holidays, "get_fund_estimate_records", return_value=pd.DataFrame()), \
                patch.object(safe_holidays, "get_benchmark_estimate_records", return_value=pd.DataFrame()), \
                patch.object(safe_holidays, "save_cumulative_estimate_table_image") as save_image, \
                patch.object(safe_holidays, "apply_safe_public_watermarks"), \
                patch.object(safe_holidays, "print_cumulative_estimate_table"):
            safe_holidays.main()
        summary = save_image.call_args.kwargs["summary_df"]
        self.assertIn("暂无可累计的完整交易日", summary.to_string())
        # 品牌前缀可以调整；核心估算口径和公告说明必须保留。
        self.assertIn("依据季报持仓及指数估算，最终以基金公司公告为准。", save_image.call_args.kwargs["footnote_text"])


if __name__ == "__main__":
    unittest.main()
