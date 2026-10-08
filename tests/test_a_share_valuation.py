"""宏观估值只服务走势图；隔离缓存验证计算、时间与刷新行为。"""
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pandas as pd

try:
    from tools import a_share_valuation as valuation
except ImportError:
    valuation = None


def cap_frame(value=80.):
    return pd.DataFrame({"数据日期": ["2026年01月份", "2026年02月份"],
                         "市价总值-上海": [30., value - 20.], "市价总值-深圳": [20., 20.]})


def gdp_frame():
    return pd.DataFrame({"季度": ["2024年第1-4季度", "2025年第1季度", "2025年第1-2季度",
                                  "2025年第1-3季度", "2025年第1-4季度"],
                         "国内生产总值-绝对值": [80., 20., 45., 70., 100.]})


class ValuationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(valuation, "需要独立 MC/GDP 模块")

    def test_market_cap_sum_and_gdp_ttm_have_no_scale_factor(self):
        cap = valuation.normalize_market_cap(cap_frame())
        gdp = valuation.normalize_gdp(gdp_frame())
        self.assertEqual(cap.iloc[-1].market_cap_yi, 80.)
        self.assertEqual(gdp.iloc[-1].gdp_ttm_yi, 100.)
        self.assertEqual(gdp.gdp_single_quarter_yi.dropna().tolist(), [20., 25., 25., 30.])
        history = valuation.build_revised_history(cap, gdp)
        self.assertEqual(history.iloc[-1].ratio, .8)

    def test_missing_quarter_does_not_form_a_false_ttm(self):
        raw = gdp_frame().drop(index=2)
        result = valuation.normalize_gdp(raw)
        self.assertTrue(result.gdp_ttm_yi.isna().all())

    def test_invalid_schema_conflicting_duplicates_and_nonpositive_rejected(self):
        for raw in [cap_frame().drop(columns="市价总值-深圳"),
                    pd.concat([cap_frame(), cap_frame(90.).tail(1)]),
                    cap_frame().assign(**{"市价总值-上海": -1.})]:
            with self.subTest(columns=list(raw.columns)), self.assertRaises(ValueError):
                valuation.normalize_market_cap(raw)

    def test_incomplete_current_month_is_not_zero(self):
        raw = cap_frame()
        raw.loc[2] = ["2026年03月份", float("nan"), float("nan")]
        result = valuation.normalize_market_cap(raw)
        self.assertEqual(len(result), 2)

    def test_state_boundaries_and_overrides(self):
        cases = { .776: "OVER", .775: "OVER", .774: "NEUTRAL", .601: "NEUTRAL",
                  .60: "LOW", .599: "LOW", .551: "LOW", .55: "DEEP LOW", .549: "DEEP LOW" }
        for value, expected in cases.items():
            self.assertEqual(valuation.classify_ratio(value), expected)
        self.assertEqual(valuation.classify_ratio(.8, over=.9), "NEUTRAL")
        self.assertIsNone(valuation.classify_ratio(float("nan")))
        with self.assertRaises(ValueError):
            valuation.classify_ratio(.8, over=.5)

    def test_alignment_uses_available_time_and_never_future_value(self):
        frame = pd.DataFrame({"available_at": ["2026-01-31T00:00:00+08:00", "2026-02-28T00:00:00+08:00"],
                              "ratio": [.55, .65], "basis": ["observed", "observed"]})
        result = valuation.align_valuation(frame, pd.to_datetime(["2026-01-15", "2026-02-15", "2026-03-01"]))
        self.assertTrue(pd.isna(result.ratio.iloc[0]))
        self.assertEqual(result.ratio.iloc[1:].tolist(), [.55, .65])
        late = pd.DataFrame({"available_at": ["2026-02-15T16:00:00+08:00"], "ratio": [.7], "basis": ["observed"]})
        self.assertTrue(pd.isna(valuation.align_valuation(late, ["2026-02-15"], now="2026-02-15T14:00:00+08:00").ratio.iloc[0]))

    def test_cache_iso_precision_keeps_observation_and_historical_cutoff(self):
        # 生产缓存的原始格式：历史无小数秒，首次观测为 UTC 微秒。
        latest = 0.8051410473348942
        frame = pd.DataFrame({
            "available_at": ["2026-08-31T00:00:00+08:00", "2026-09-30T10:55:59.345933+00:00"],
            "ratio": [latest, latest], "basis": ["historical revised series", "observed"],
        })
        frame.attrs["historical_cutoff"] = "2026-09-30T10:55:59.345933+00:00"
        original = frame.copy(deep=True)
        result = valuation.align_valuation(frame, ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-08"],
                                           now="2026-10-08T16:00:00+08:00")
        self.assertEqual(result.ratio.iloc[0], latest)
        # 9月30日15:00早于18:55首次观测，不能为了补线倒填。
        self.assertTrue(pd.isna(result.ratio.iloc[1]))
        self.assertEqual(result.ratio.iloc[2:].tolist(), [latest, latest])
        self.assertEqual(result.basis.iloc[0], "historical revised series")
        self.assertEqual(result.basis.iloc[2:].tolist(), ["observed", "observed"])
        pd.testing.assert_frame_equal(frame, original)
        self.assertEqual(frame.attrs, original.attrs)

    def test_intraday_mixed_timezones_preserve_fractional_seconds(self):
        frame = pd.DataFrame({
            "available_at": ["2026-10-07T15:00:00+08:00", "2026-10-08T12:39:30.123+08:00",
                             "2026-10-08T04:39:59.123456Z", "2026-10-08T04:40:00.123456789+00:00"],
            "ratio": [.6, .75, .8051410473348942, .9], "basis": "observed",
        })
        result = valuation.align_valuation(frame, ["2026-10-07", "2026-10-08"],
                                           now="2026-10-08T12:40:00.123456+08:00")
        self.assertEqual(result.ratio.tolist(), [.6, .8051410473348942])

    def test_after_close_and_historical_queries_keep_1500_boundary(self):
        frame = pd.DataFrame({
            "available_at": ["2026-10-07T15:00:00+08:00", "2026-10-07T16:00:00+08:00",
                             "2026-10-08T14:59:59.999999+08:00", "2026-10-08T15:00:00.000001+08:00"],
            "ratio": [.7, .75, .8, .9], "basis": "observed",
        })
        result = valuation.align_valuation(frame, ["2026-10-07", "2026-10-08"],
                                           now="2026-10-08T16:00:00.000001+08:00")
        self.assertEqual(result.ratio.tolist(), [.7, .8])

    def test_beijing_day_switch_does_not_use_utc_calendar_day(self):
        frame = pd.DataFrame({
            "available_at": ["2026-10-08T14:59:00+08:00", "2026-10-08T16:10:00.123456+00:00",
                             "2026-10-08T16:31:00+00:00"],
            "ratio": [.7, .8, .9], "basis": "observed",
        })
        result = valuation.align_valuation(frame, ["2026-10-08", "2026-10-09"],
                                           now="2026-10-09T00:30:00.123456+08:00")
        self.assertEqual(result.ratio.tolist(), [.7, .8])

    def test_bad_event_and_query_dates_only_skip_the_affected_rows(self):
        frame = pd.DataFrame({"available_at": ["2026-09-30T10:55:59.345933+00:00", "bad", None],
                              "ratio": [.8051410473348942, .9, .95], "basis": "observed"})
        output = io.StringIO()
        with redirect_stdout(output):
            result = valuation.align_valuation(frame, ["2026-10-02", "bad", None, "2026-10-01"],
                                               now="2026-10-08T16:00:00+08:00")
        self.assertEqual(len(result), 4)
        self.assertEqual(result.ratio.iloc[[0, 3]].tolist(), [.8051410473348942] * 2)
        self.assertTrue(result.iloc[1:3].date.isna().all())
        self.assertTrue(result.iloc[1:3].ratio.isna().all())
        self.assertIn("invalid_event_dates=2", output.getvalue())
        self.assertIn("invalid_query_dates=2", output.getvalue())

    def test_all_invalid_query_dates_do_not_reach_asof(self):
        frame = pd.DataFrame({"available_at": ["2026-09-30T10:55:59.345933+00:00"],
                              "ratio": [.8051410473348942], "basis": "observed"})
        with patch.object(valuation.pd, "merge_asof", side_effect=AssertionError("无有效日期不能合并")):
            result = valuation.align_valuation(frame, [None, "bad"], now="2026-10-08T16:00:00+08:00")
        self.assertEqual(len(result), 2)
        self.assertTrue(result.ratio.isna().all())

    def test_query_resolution_is_compatible_with_event_resolution(self):
        frame = pd.DataFrame({"available_at": ["2026-09-30T10:55:59.345933+00:00"],
                              "ratio": [.8051410473348942], "basis": "observed"})
        for unit in ("s", "us", "ns"):
            with self.subTest(unit=unit):
                dates = pd.DatetimeIndex(["2026-10-01", "2026-10-08"]).as_unit(unit)
                result = valuation.align_valuation(frame, dates, now="2026-10-08T12:40:00.123456+08:00")
                self.assertEqual(result.ratio.tolist(), [.8051410473348942] * 2)

    def test_contiguous_regimes_are_merged_and_gaps_break_them(self):
        aligned = pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=7),
                                "ratio": [.58, .58, .58, None, .58, .7, .8]})
        segments = valuation.regime_segments(aligned)
        self.assertEqual([s[2] for s in segments], ["LOW", "LOW", "OVER"])

    def test_source_ttl_and_shared_fetch(self):
        calls = []
        def fetch(due):
            calls.append(set(due))
            return {key: cap_frame() if key == "cap" else gdp_frame() for key in due}
        with tempfile.TemporaryDirectory() as tmp:
            for now in ["2026-03-01T10:00:00+08:00", "2026-03-02T10:00:00+08:00",
                        "2026-03-03T10:00:00+08:00", "2026-03-05T10:00:00+08:00"]:
                valuation.load_valuation(tmp, now=now, fetcher=fetch)
        self.assertEqual(calls, [{"cap", "gdp"}, {"cap"}, {"cap", "gdp"}])

    def test_failed_refresh_backoff_retains_data_and_success_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = valuation.load_valuation(tmp, now="2026-03-01T10:00:00+08:00",
                          fetcher=lambda due: {"cap": cap_frame(), "gdp": gdp_frame()})
            failed = lambda due: {key: "offline" for key in due}
            stale = valuation.load_valuation(tmp, now="2026-03-05T10:00:00+08:00", fetcher=failed)
            pd.testing.assert_frame_equal(original, stale)
            with patch.object(valuation, "fetch_sources", side_effect=AssertionError("不应在退避期间请求")):
                valuation.load_valuation(tmp, now="2026-03-05T20:00:00+08:00")
            import json
            doc = json.loads((Path(tmp)/"a_share_mc_gdp.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["sources"]["cap"]["last_success_at"], "2026-03-01T02:00:00+00:00")

    def test_revisions_preserve_baseline_and_previous_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = valuation.load_valuation(tmp, now="2026-03-01T10:00:00+08:00",
                            fetcher=lambda due: {"cap": cap_frame(), "gdp": gdp_frame()})
            revised = valuation.load_valuation(tmp, now="2026-03-03T10:00:00+08:00",
                            fetcher=lambda due: {"cap": cap_frame(90.)})
            dates = ["2026-02-15", "2026-03-01", "2026-03-02", "2026-03-04"]
            aligned = valuation.align_valuation(revised, dates)
            self.assertEqual(aligned.ratio.tolist(), [.5, .8, .8, .9])
            pd.testing.assert_frame_equal(first[first.basis == "historical revised series"].reset_index(drop=True),
                                          revised[revised.basis == "historical revised series"].reset_index(drop=True))

    def test_unavailable_or_timeout_is_optional(self):
        with tempfile.TemporaryDirectory() as tmp:
            import subprocess
            with patch.object(valuation.subprocess, "run", side_effect=subprocess.TimeoutExpired("macro", 15)):
                result = valuation.load_valuation(tmp, now="2026-03-01T10:00:00+08:00")
            self.assertTrue(result.empty)

    def test_unexpected_fetch_exception_still_uses_old_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = valuation.load_valuation(tmp, now="2026-03-01T10:00:00+08:00",
                             fetcher=lambda due: {"cap": cap_frame(), "gdp": gdp_frame()})
            def broken(due):
                raise RuntimeError("unexpected transport failure")
            result = valuation.load_valuation(tmp, now="2026-03-05T10:00:00+08:00", fetcher=broken)
            pd.testing.assert_frame_equal(old, result)

    def test_newer_gdp_label_cannot_replace_more_recent_valid_ttm_with_older_one(self):
        old_gdp = pd.concat([gdp_frame(), pd.DataFrame({"季度": ["2026年第1季度", "2026年第1-2季度"],
                                           "国内生产总值-绝对值": [30., 70.]})], ignore_index=True)
        broken = pd.concat([old_gdp.iloc[:-1], pd.DataFrame({"季度": ["2026年第1-3季度"],
                                           "国内生产总值-绝对值": [100.]})], ignore_index=True)
        with tempfile.TemporaryDirectory() as tmp:
            first = valuation.load_valuation(tmp, now="2026-10-01T10:00:00+08:00",
                            fetcher=lambda due: {"cap": cap_frame(), "gdp": old_gdp})
            result = valuation.load_valuation(tmp, now="2026-10-05T10:00:00+08:00",
                            fetcher=lambda due: {"cap": cap_frame(), "gdp": broken})
            self.assertEqual(result.iloc[-1].ratio, first.iloc[-1].ratio)
            self.assertEqual(result.iloc[-1].gdp_period, "2026-06-30")
            self.assertTrue(result.attrs["sources"]["gdp"]["error"])


if __name__ == "__main__":
    unittest.main()
