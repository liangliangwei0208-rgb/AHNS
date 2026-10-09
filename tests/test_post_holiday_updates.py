"""所有 A 股假期节后第 1、2 个交易日的补更新回归。"""
from __future__ import annotations

import contextlib
import io
import json
import runpy
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd
from PIL import Image

import git_main
import sum_holidays as updates
from tools import fund_history_io as history
from tools.configs.workflow_configs import GITHUB_WORKFLOW_STEPS, SERVICE_WORKFLOW_STEPS


TRADE_DATES = {"2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"}
VALUATION_DATES = ("2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08")


def fund_record(day, value=1.0, code="012922", **extra):
    row = dict(market_group="overseas", fund_code=code, fund_name="测试海外基金",
               valuation_date=day, valuation_anchor_date=day, run_date_bj="2026-10-09",
               run_time_bj="2026-10-09T08:00:00", stage="final", data_status="complete",
               completeness_score=100.0, is_final=True, estimate_return_pct=value,
               valuation_mode="last_close", effective_valuation_mode="last_close",
               market_status={"US": "traded"}, market_trade_dates={"US": day})
    row.update(extra)
    return row


def benchmark_record(day, value=1.0, **extra):
    row = dict(market_group="overseas", symbol=".NDX", label="纳斯达克100",
               valuation_date=day, trade_date=day, return_pct=value, is_final=True,
               stage="final", status="traded", data_status="complete",
               run_date_bj="2026-10-09", run_time_bj="2026-10-09T08:00:00")
    row.update(extra)
    return row


class PostHolidayTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name) / "estimates.json"
        self.image = Path(self.temp.name) / "safe_sum_holidays.png"
        self.calendar = patch.object(history, "_load_a_share_trade_dates", return_value=(TRADE_DATES, "test-calendar"))
        self.calendar.start()
        self.addCleanup(self.calendar.stop)

    def write_cache(self, funds, benchmarks=()):
        self.cache.write_text(json.dumps({"records": {str(i): r for i, r in enumerate(funds)},
                                         "benchmark_records": {str(i): r for i, r in enumerate(benchmarks)}},
                                        ensure_ascii=False), encoding="utf-8")

    def run_update(self, today="2026-10-09"):
        output = io.StringIO()
        with patch.object(updates, "SAFE_OUTPUT_FILE", str(self.image)), \
                patch.object(updates, "ensure_runtime_dirs"), contextlib.redirect_stdout(output):
            result = updates.run(today, cache_file=self.cache)
        return result, output.getvalue()

    def test_first_day_only_pre_holiday_valuation(self):
        context = updates.detect_post_holiday_context("2026-10-08")
        self.assertEqual(context.post_holiday_trade_day, 1)
        self.assertEqual(updates._target_valuation_window(context)[:2], ("2026-09-30", "2026-09-30"))

    def test_second_day_ends_at_first_reopen(self):
        context = updates.detect_post_holiday_context("2026-10-09")
        self.assertEqual(context.post_holiday_trade_day, 2)
        self.assertEqual(updates._target_valuation_window(context)[:2], ("2026-10-01", "2026-10-08"))

    def test_shared_context_identifies_second_reopen(self):
        context = history.detect_a_share_holiday_context("2026-10-09")
        self.assertEqual(getattr(context, "post_holiday_trade_day", 0), 2)
        self.assertEqual(getattr(context, "first_post_holiday_trade_date", ""), "2026-10-08")
        self.assertFalse(context.is_first_reopen)
        self.assertFalse(context.is_holiday)

    def test_other_holidays_cross_weekend_and_cross_year(self):
        cases = [
            ({"2026-04-30", "2026-05-06", "2026-05-07"}, "2026-05-07", "2026-05-01", "2026-05-06"),
            ({"2026-06-18", "2026-06-22", "2026-06-23"}, "2026-06-23", "2026-06-19", "2026-06-22"),
            ({"2026-09-24", "2026-09-28", "2026-09-29"}, "2026-09-29", "2026-09-25", "2026-09-28"),
            ({"2026-04-29", "2026-05-08", "2026-05-11"}, "2026-05-11", "2026-04-30", "2026-05-08"),
            ({"2026-12-31", "2027-01-04", "2027-01-05"}, "2027-01-05", "2027-01-01", "2027-01-04"),
        ]
        for dates, today, start, end in cases:
            with self.subTest(today=today), patch.object(history, "_load_a_share_trade_dates", return_value=(dates, "fixture")):
                context = updates.detect_post_holiday_context(today)
                self.assertEqual(context.post_holiday_trade_day, 2)
                self.assertEqual(updates._target_valuation_window(context)[:2], (start, end))

    def test_weekend_third_day_and_plain_monday_skip(self):
        for day in ("2026-10-10", "2026-10-12", "2026-10-13"):
            self.assertFalse(updates.detect_post_holiday_context(day).should_generate)
        with patch.object(history, "_load_a_share_trade_dates", return_value=({"2026-09-18", "2026-09-21", "2026-09-22"}, "fixture")):
            self.assertFalse(updates.detect_post_holiday_context("2026-09-21").should_generate)

    def test_missing_benchmarks_do_not_remove_fund_dates(self):
        self.write_cache([fund_record(day) for day in VALUATION_DATES])
        funds, benchmarks, days = updates._load_overseas_daily_records("2026-10-01", "2026-10-08", "2026-10-09", self.cache)
        self.assertEqual(days, VALUATION_DATES)
        self.assertEqual(len(funds), 6)
        self.assertTrue(benchmarks.empty)

    def test_one_missing_benchmark_day_does_not_align_funds(self):
        self.write_cache([fund_record(day) for day in VALUATION_DATES], [benchmark_record("2026-10-08")])
        funds, _, days = updates._load_overseas_daily_records("2026-10-01", "2026-10-08", "2026-10-09", self.cache)
        self.assertEqual(len(funds), 6)
        self.assertEqual(days, VALUATION_DATES)

    def test_intraday_future_stale_closed_and_nonfinite_are_not_compounded(self):
        rows = [fund_record("2026-10-01", 1.0),
                fund_record("2026-10-02", 99.0, is_final=False, stage="intraday", valuation_mode="intraday", effective_valuation_mode="intraday"),
                fund_record("2026-10-05", 99.0, is_final=False, stage="partial", data_status="stale", market_trade_dates={"US": "2026-10-02"}, market_status={"US": "stale"}),
                fund_record("2026-10-06", 0.0, market_status={"US": "closed"}, market_trade_dates={}),
                fund_record("2026-10-07", float("inf")),
                fund_record("2026-10-08", 2.0, is_final=False, stage="partial", data_status="partial"),
                fund_record("2026-10-08", 99.0, run_date_bj="2026-10-10", run_time_bj="2026-10-10T08:00:00"),
                fund_record("2026-10-09", 99.0)]
        self.write_cache(rows, [benchmark_record(day) for day in VALUATION_DATES])
        funds, _, days = updates._load_overseas_daily_records("2026-10-01", "2026-10-08", "2026-10-09", self.cache)
        self.assertEqual(days, ("2026-10-01", "2026-10-08"))
        self.assertEqual(funds.estimate_return_pct.tolist(), [1.0, 2.0])

    def test_duplicate_quality_then_time_and_defensive_compounding(self):
        rows = [fund_record("2026-10-01", 1.0, is_final=False, stage="partial", data_status="partial", completeness_score=90),
                fund_record("2026-10-01", 9.0, is_final=False, stage="partial", data_status="stale", completeness_score=5, run_time_bj="2026-10-09T09:00:00"),
                fund_record("2026-10-02", -1.0)]
        self.write_cache(rows, [benchmark_record("2026-10-01"), benchmark_record("2026-10-02")])
        funds, _, _ = updates._load_overseas_daily_records("2026-10-01", "2026-10-08", "2026-10-09", self.cache)
        self.assertEqual(funds.estimate_return_pct.tolist(), [1.0, -1.0])
        summary = history.build_cumulative_dataframe(pd.concat([funds, funds], ignore_index=True))
        self.assertAlmostEqual(summary.iloc[0]["区间累计预估收益率"], -0.01, places=10)

    def test_pending_only_records_fail_instead_of_becoming_zero_return(self):
        for status in ("pending", "missing", "stale"):
            with self.subTest(status=status):
                self.write_cache([fund_record("2026-10-08", 0.0, is_final=False,
                    stage="partial", data_status="partial", completeness_score=0,
                    market_status={"US": status}, market_trade_dates={"US": None},
                    residual_benchmark_status="pending", residual_benchmark_trade_date=None)])
                with self.assertRaisesRegex(RuntimeError, "基金"):
                    self.run_update()
                self.assertFalse(self.image.exists())

    def test_closed_market_date_cannot_validate_old_quotes(self):
        self.write_cache([fund_record("2026-10-08", 1.0, is_final=False,
            stage="partial", data_status="stale",
            market_status={"CN": "closed", "US": "stale"},
            market_trade_dates={"CN": "2026-10-08", "US": "2026-10-07"}),
            fund_record("2026-10-08", 2.0, code="007844", is_final=False,
            stage="partial", data_status="partial", market_status={"US": "missing"},
            market_trade_dates={"US": None}, residual_benchmark_status="traded",
            residual_benchmark_trade_date="2026-10-08")])
        funds, _, _ = updates._load_overseas_daily_records("2026-10-01", "2026-10-08", "2026-10-09", self.cache)
        self.assertEqual(funds.fund_code.tolist(), ["007844"])

    def test_aggregate_bad_market_status_keeps_actual_same_day_contribution(self):
        # 写入方取同市场最差状态；missing/pending/stale 不代表该市场所有持仓都无有效行情。
        for status in ("missing", "pending", "stale"):
            with self.subTest(status=status):
                self.write_cache([fund_record("2026-10-01", 0.463938, is_final=False,
                    stage="partial", data_status="partial", completeness_score=41.9385,
                    market_status={"CN": "closed", "UNKNOWN": "missing", "US": status},
                    market_trade_dates={"CN": None, "UNKNOWN": None, "US": "2026-10-01"},
                    residual_benchmark_status="missing", residual_benchmark_trade_date="")])
                funds, _, dates = updates._load_overseas_daily_records("2026-10-01", "2026-10-08", "2026-10-09", self.cache)
                self.assertEqual(dates, ("2026-10-01",))
                self.assertEqual(funds.estimate_return_pct.tolist(), [0.463938])

    def test_mixed_timezone_timestamps_choose_latest_beijing_run(self):
        self.write_cache([fund_record("2026-10-08", 1.0, run_time_bj="2026-10-09T08:00:00"),
                          fund_record("2026-10-08", 2.0, run_time_bj="2026-10-09T09:00:00+08:00")])
        funds, _, _ = updates._load_overseas_daily_records("2026-10-01", "2026-10-08", "2026-10-09", self.cache)
        self.assertEqual(funds.estimate_return_pct.tolist(), [2.0])

    def test_independent_compound_and_individual_date_sets(self):
        rows = [fund_record("2026-10-01", 10.0), fund_record("2026-10-02", -10.0),
                fund_record("2026-10-08", 2.0, code="007844")]
        summary = history.build_cumulative_dataframe(pd.DataFrame(rows)).set_index("基金代码")
        self.assertAlmostEqual(summary.loc["012922", "区间累计预估收益率"], -1.0)
        self.assertEqual(summary.loc["012922", "有效估值日数"], 2)
        self.assertEqual(summary.loc["007844", "有效估值日数"], 1)

    def test_missing_funds_are_failure_not_successful_skip(self):
        self.write_cache([], [benchmark_record("2026-10-08")])
        with self.assertRaisesRegex(RuntimeError, "基金"):
            self.run_update()
        self.assertFalse(self.image.exists())

    def test_fund_only_png_and_repeat_does_not_modify_cache_or_image(self):
        self.write_cache([fund_record("2026-10-01"), fund_record("2026-10-08", -0.5)])
        before_cache = self.cache.read_bytes()
        result, output = self.run_update()
        self.assertTrue(result)
        self.assertTrue(self.image.is_file())
        with Image.open(self.image) as image:
            self.assertGreater(image.width, 100)
            image.verify()
        stat = self.image.stat()
        result, output = self.run_update()
        self.assertTrue(result)
        self.assertEqual(self.image.stat().st_mtime_ns, stat.st_mtime_ns)
        self.assertEqual(self.cache.read_bytes(), before_cache)
        self.assertIn('"status": "unchanged"', output)

    def test_unverified_calendar_is_reported_as_failure(self):
        self.write_cache([fund_record("2026-10-08")])
        with patch.object(history, "_load_a_share_trade_dates", return_value=(set(), "unavailable")):
            with self.assertRaisesRegex(RuntimeError, "日历"):
                self.run_update()

    def test_first_day_renders_only_pre_holiday_daily_return(self):
        self.write_cache([fund_record("2026-09-30", 1.25, run_date_bj="2026-10-08"),
                          fund_record("2026-10-01", 99.0, run_date_bj="2026-10-08")])
        result, output = self.run_update("2026-10-08")
        self.assertTrue(result)
        self.assertTrue(self.image.is_file())
        report = json.loads(next(line.split("=", 1)[1] for line in output.splitlines() if line.startswith(updates.RESULT_PREFIX)))
        self.assertEqual(report["fund_valid_dates"], ["2026-09-30"])
        self.assertEqual(report["fund_record_count"], 1)

    def test_missing_fund_cli_exits_nonzero_and_reports_diagnostics(self):
        self.write_cache([], [benchmark_record("2026-10-08")])
        output = io.StringIO()
        with patch.object(sys, "argv", ["sum_holidays.py", "--today", "2026-10-09", "--cache-file", str(self.cache)]), \
                contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as failure:
            runpy.run_path(str(Path(updates.__file__)), run_name="__main__")
        self.assertEqual(failure.exception.code, 1)
        report = json.loads(next(line.split("=", 1)[1] for line in output.getvalue().splitlines() if line.startswith(updates.RESULT_PREFIX)))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["fund_count"], 0)
        self.assertEqual(report["benchmark_valid_dates"], ["2026-10-08"])
        self.assertTrue(report["missing_dates_by_fund"])

    def test_partial_stale_with_current_contribution_and_legacy_final_remain_legal(self):
        legacy = fund_record("2026-10-01", 1.0)
        for key in ("stage", "data_status", "valuation_mode", "effective_valuation_mode", "market_status", "market_trade_dates", "valuation_anchor_date"):
            legacy.pop(key)
        self.write_cache([legacy, fund_record("2026-10-08", -0.5, is_final=False, stage="partial", data_status="stale")])
        funds, _, _ = updates._load_overseas_daily_records("2026-10-01", "2026-10-08", "2026-10-09", self.cache)
        self.assertEqual(funds.estimate_return_pct.tolist(), [1.0, -0.5])

    def test_bad_benchmark_cannot_impersonate_target_day(self):
        self.write_cache([fund_record("2026-10-01"), fund_record("2026-10-08")],
                         [benchmark_record("2026-10-01", 99.0, trade_date="2026-09-30", status="stale"),
                          benchmark_record("2026-10-08", 2.0)])
        funds, benchmarks, days = updates._load_overseas_daily_records("2026-10-01", "2026-10-08", "2026-10-09", self.cache)
        self.assertEqual(len(funds), 2)
        self.assertEqual(benchmarks.return_pct.tolist(), [2.0])
        self.assertEqual(days, ("2026-10-01", "2026-10-08"))

    def test_duplicate_non_index_benchmark_is_compounded_once(self):
        rows = [benchmark_record("2026-10-01", 10.0, symbol="XOP", label="油气开采指数"),
                benchmark_record("2026-10-01", 10.0, symbol="XOP", label="油气开采指数"),
                benchmark_record("2026-10-02", -10.0, symbol="XOP", label="油气开采指数")]
        summary = history.build_benchmark_cumulative_dataframe(pd.DataFrame(rows)).set_index("指数代码")
        self.assertAlmostEqual(summary.loc["XOP", "区间累计涨跌幅"], -1.0)
        self.assertEqual(summary.loc["XOP", "有效估值日数"], 2)

    def test_render_failure_preserves_previous_image_and_cache(self):
        self.write_cache([fund_record("2026-10-08")])
        self.image.write_bytes(b"previous-image")
        before = self.cache.read_bytes()
        with patch.object(updates, "_save_safe_image", side_effect=RuntimeError("drawing-failed")):
            with self.assertRaisesRegex(RuntimeError, "drawing-failed"):
                self.run_update()
        self.assertEqual(self.image.read_bytes(), b"previous-image")
        self.assertEqual(self.cache.read_bytes(), before)
        self.assertFalse(list(self.image.parent.glob(".sum_holidays_*.png")))


class WorkflowPostHolidayTests(unittest.TestCase):
    def test_both_workflows_keep_update_during_realtime_and_daily_windows(self):
        # 使用真实总入口控制流；仅拦截外部业务脚本及邮件发送。
        for config, service_mode in ((GITHUB_WORKFLOW_STEPS, False), (SERVICE_WORKFLOW_STEPS, True)):
            for stamp in ("2026-10-08T19:00:00", "2026-10-09T19:00:00", "2026-10-09T21:30:00"):
                observed = []
                def runner(step, **kwargs):
                    observed.append(step.script_path.name)
                    return git_main.ScriptResult(step.name, step.script_path.name, step.script_path, 0, 0., [], step.collect_images, [])
                with self.subTest(service=service_mode, time=stamp), \
                        patch.object(history, "_load_a_share_trade_dates", return_value=(TRADE_DATES, "fixture")), \
                        patch.object(git_main, "datetime", wraps=datetime) as clock, \
                        patch.object(git_main, "run_script", side_effect=runner), \
                        contextlib.redirect_stdout(io.StringIO()):
                    clock.now.return_value = datetime.fromisoformat(stamp).replace(tzinfo=git_main.BJ_TZ)
                    self.assertEqual(git_main.main(["--no-send"], workflow_steps=config, service_mode=service_mode), 0)
                self.assertIn("sum_holidays.py", observed)
                self.assertEqual(observed.count("sum_holidays.py"), 1)

    def test_ordinary_day_does_not_select_post_holiday_task(self):
        observed = []
        def runner(step, **kwargs):
            observed.append(step.script_path.name)
            return git_main.ScriptResult(step.name, step.script_path.name, step.script_path, 0, 0., [], step.collect_images, [])
        with patch.object(history, "_load_a_share_trade_dates", return_value=(TRADE_DATES, "fixture")), \
                patch.object(git_main, "datetime", wraps=datetime) as clock, \
                patch.object(git_main, "run_script", side_effect=runner), contextlib.redirect_stdout(io.StringIO()):
            clock.now.return_value = datetime(2026, 10, 12, 21, 30, tzinfo=git_main.BJ_TZ)
            git_main.main(["--no-send"])
        self.assertNotIn("sum_holidays.py", observed)

    def test_generated_status_without_output_is_failure(self):
        step = next(s for s in git_main.resolve_workflow_steps() if s.script_path.name == "sum_holidays.py")
        marker = 'AHNS_SUM_HOLIDAYS_RESULT=' + json.dumps(dict(status="generated", should_generate=True, output_file="output/safe_sum_holidays.png"))
        with patch.object(git_main, "snapshot_images", return_value={}), \
                patch.object(git_main, "stream_script_output", return_value=(0, [marker])), \
                contextlib.redirect_stdout(io.StringIO()):
            result = git_main.run_script(step)
        self.assertFalse(result.success)

    def test_mail_collects_png_above_and_below_inline_limit(self):
        image = (git_main.OUTPUT_DIR / "safe_sum_holidays.png").resolve()
        step = next(s for s in git_main.resolve_workflow_steps() if s.script_path.name == "sum_holidays.py")
        with patch.object(git_main, "snapshot_images", side_effect=[{}, {image: git_main.ImageState(2, 100)}]), \
                patch.object(git_main, "stream_script_output", return_value=(0, [])), contextlib.redirect_stdout(io.StringIO()):
            result = git_main.run_script(step)
        self.assertEqual(git_main.unique_images([result, result]), [image])
        for count in (16, 17):
            embed, attach, _ = git_main.choose_email_image_send_options(count)
            self.assertTrue(attach)
            self.assertEqual(embed, count == 16)

    def test_main_passes_all_candidates_to_mail_and_preserves_failure_report(self):
        with TemporaryDirectory() as directory:
            images = [Path(directory) / ("safe_sum_holidays.png" if i == 0 else f"other_{i}.png") for i in range(17)]
            for image in images:
                image.write_bytes(b"image-data")
            for count in (16, 17):
                selected = images[:count]
                observed = {}
                def runner(step, **kwargs):
                    return git_main.ScriptResult(step.name, step.script_path.name, step.script_path, 1, 0., selected, True, ["example-task-failure"])
                def mail(**kwargs):
                    observed.update(kwargs)
                with patch.object(history, "_load_a_share_trade_dates", return_value=(TRADE_DATES, "fixture")), \
                        patch.object(git_main, "datetime", wraps=datetime) as clock, \
                        patch.object(git_main, "run_script", side_effect=runner), \
                        patch.object(git_main, "send_email", side_effect=mail), contextlib.redirect_stdout(io.StringIO()):
                    clock.now.return_value = datetime(2026, 10, 9, 19, 0, tzinfo=git_main.BJ_TZ)
                    code = git_main.main([], workflow_steps=[{"name": "补更新", "script": "sum_holidays.py", "post_holiday_update_group": True}])
                self.assertEqual(code, 0)  # 邮件成功发出后保留原有总入口退出语义。
                self.assertEqual(observed["image_paths"], [path.resolve() for path in selected])
                self.assertTrue(observed["attach_images"])
                self.assertEqual(observed["embed_images"], count == 16)
                self.assertIn("example-task-failure", observed["text"])


if __name__ == "__main__":
    unittest.main()
