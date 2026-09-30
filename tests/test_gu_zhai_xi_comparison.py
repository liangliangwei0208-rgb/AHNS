"""股债利差图的三指数叠加、共用状态及开关测试。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from strategy import gu_zhai_xi
from strategy.gu_zhai_xi_data import MarketDataCache


DATES = pd.to_datetime(["2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29"])
NOW = datetime(2026, 9, 30, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


class ComparisonIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ahns-gu-zhai-overlay-")
        self.addCleanup(self.temp.cleanup)
        self.cache_dir = Path(self.temp.name)

    def test_comparison_cache_first_and_separate_files(self):
        for name, values in (("csi2000", [100, 101, 102, 103]), ("shanghai", [200, 201, 202, 203])):
            pd.DataFrame({"date": DATES, "close": values}).to_csv(
                self.cache_dir / f"gu_zhai_xi_{name}_daily.csv", index=False
            )
        with patch.object(MarketDataCache, "_fetch_index_futu", side_effect=AssertionError("网络不应调用")):
            with MarketDataCache(self.cache_dir, DATES, now=NOW) as store:
                csi = store.load_index("csi2000")
                sh = store.load_index("shanghai")
        self.assertEqual(csi["csi2000_close"].tolist(), [100, 101, 102, 103])
        self.assertEqual(sh["shanghai_close"].tolist(), [200, 201, 202, 203])

    def test_comparison_fetch_uses_futu_before_fallback_and_only_missing_dates(self):
        pd.DataFrame({"date": DATES[:3], "close": [100, 101, 102]}).to_csv(
            self.cache_dir / "gu_zhai_xi_csi2000_daily.csv", index=False
        )
        calls = []

        def futu(_self, start, end, *, symbol="shenzhen"):
            calls.append((symbol, start, end))
            return pd.DataFrame({"date": [DATES[-1]], "index_close": [103]})

        with patch.object(MarketDataCache, "_fetch_index_futu", futu), patch.object(
            MarketDataCache, "_fetch_index_tencent", side_effect=AssertionError("fallback 不应调用")
        ):
            with MarketDataCache(self.cache_dir, DATES, now=NOW) as store:
                result = store.load_index("csi2000")
        self.assertEqual(calls, [("csi2000", DATES[-1], DATES[-1])])
        self.assertEqual(result["csi2000_close"].tolist(), [100, 101, 102, 103])

    def test_overlay_uses_one_common_anchor_and_preserves_missing_dates(self):
        frame = pd.DataFrame({
            "date": DATES,
            "index_close": [1000, 1100, 1200, 1300],
            "csi2000_close": [np.nan, 200, 220, np.nan],
            "shanghai_close": [3000, 3300, 3630, 3960],
        })
        plotted, anchor = gu_zhai_xi.prepare_index_overlays(frame, ("shenzhen", "csi2000", "shanghai"))
        self.assertEqual(anchor, DATES[1])
        self.assertTrue(np.isnan(plotted.loc[0, "csi2000_display"]))
        self.assertEqual(plotted.loc[1, "csi2000_display"], 1100)
        self.assertEqual(plotted.loc[2, "csi2000_display"], 1210)
        self.assertEqual(plotted.loc[2, "shanghai_display"], 1210)
        self.assertTrue(np.isnan(plotted.loc[3, "csi2000_display"]))

    def test_one_unavailable_index_does_not_hide_another(self):
        frame = pd.DataFrame({
            "date": DATES, "index_close": [1000, 1100, 1200, 1300],
            "csi2000_close": [np.nan] * 4,
            "shanghai_close": [3000, 3300, 3600, 3900],
        })
        plotted, anchor = gu_zhai_xi.prepare_index_overlays(frame, ("shenzhen", "csi2000", "shanghai"))
        self.assertEqual(anchor, DATES[0])
        self.assertTrue(plotted["csi2000_display"].isna().all())
        self.assertEqual(plotted["shanghai_display"].tolist(), [1000, 1100, 1200, 1300])

    def test_three_indices_use_same_spread_states_but_distinct_colors(self):
        frame = pd.DataFrame({
            "date": DATES,
            "index_close": [1000, 1010, 1020, 1030],
            "csi2000_display": [1000, 990, 1020, 1010],
            "shanghai_display": [1000, 1005, 1010, 1015],
            "spread": [0, 2, -2, 0],
            "upper": [1] * 4,
            "lower": [-1] * 4,
        })
        fig, ax = plt.subplots()
        try:
            collections = [
                gu_zhai_xi.draw_colored_index(ax, frame, key=key)
                for key in ("shenzhen", "csi2000", "shanghai")
            ]
            self.assertEqual([len(item.get_segments()) for item in collections], [3, 3, 3])
            for key, collection in zip(("shenzhen", "csi2000", "shanghai"), collections):
                wanted = gu_zhai_xi.INDEX_COLORS[key]
                self.assertEqual(
                    [tuple(row[:3]) for row in collection.get_colors()],
                    [tuple(plt.matplotlib.colors.to_rgb(wanted[state])) for state in ("ABOVE", "BELOW", "NORMAL")],
                )
            self.assertEqual(len({tuple(item.get_colors()[0]) for item in collections}), 3)
        finally:
            plt.close(fig)

    def test_price_gap_does_not_draw_a_false_bridge(self):
        frame = pd.DataFrame({
            "date": DATES, "csi2000_display": [1000, np.nan, 1020, 1030],
            "spread": [0, 0, 0, 0], "upper": [1] * 4, "lower": [-1] * 4,
        })
        fig, ax = plt.subplots()
        try:
            collection = gu_zhai_xi.draw_colored_index(ax, frame, key="csi2000")
            self.assertEqual(len(collection.get_segments()), 1)
        finally:
            plt.close(fig)

    def test_hiding_comparisons_skips_their_data_requests(self):
        dates = pd.bdate_range("2025-01-01", periods=300)
        index = pd.DataFrame({"date": dates, "index_close": range(100, 400)})
        pe = pd.DataFrame({"date": dates, "pe_ttm": [20.0] * 300})
        bond = pd.DataFrame({"date": dates, "cn10y": [2.0] * 300})

        def sources(_years, _window, *, comparison_symbols):
            self.assertEqual(comparison_symbols, ())
            return index, pe, bond, {}

        with patch.object(gu_zhai_xi, "_load_chart_sources", side_effect=sources):
            result = gu_zhai_xi.build_indicator(1, 250, 1.95, visible_indices=("shenzhen",))
        self.assertNotIn("csi2000_close", result)
        self.assertNotIn("shanghai_close", result)
        self.assertAlmostEqual(result.iloc[-1]["spread"], 3.0)

    def test_cli_can_hide_each_index_independently(self):
        with patch("sys.argv", ["gu_zhai_xi.py", "--no-shenzhen-index", "--no-csi2000-index"]):
            args = gu_zhai_xi.parse_args()
        self.assertFalse(args.shenzhen_index)
        self.assertFalse(args.csi2000_index)
        self.assertTrue(args.shanghai_index)


if __name__ == "__main__":
    unittest.main()
