"""宏观估值只服务走势图；隔离缓存验证计算、时间与刷新行为。"""
import tempfile
import unittest
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
        cases = { .766: "OVER", .765: "OVER", .764: "NEUTRAL", .601: "NEUTRAL",
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
