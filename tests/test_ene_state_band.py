"""周线 ENE 的计算、精确周映射及 Price 专属展示（全部离线）。"""
import tempfile
from pathlib import Path
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import unittest

from tools import rsi_data
from tools.configs.rsi_configs import RSI_ANALYSIS_CONFIGS, RSI_NASDAQ100_CONFIG

def weekly_prices(closes):
    return pd.DataFrame({"date": pd.date_range("2026-01-02", periods=len(closes), freq="W-FRI"),
                         "close": closes})

def render(frame, symbol="TEST", **kwargs):
    with tempfile.TemporaryDirectory() as tmp, patch.object(plt, "close") as closed:
        rsi_data.plot_analysis(frame, symbol, output_file=str(Path(tmp)/"chart.png"),
                               show_plot=False, **kwargs)
        return closed.call_args.args[0]

class EneTests(unittest.TestCase):
    def tearDown(self):
        plt.close("all")

    def test_us_local_friday_and_daylight_saving_close(self):
        from tools.ene_state_band import build_weekly_ene_frame
        source = weekly_prices([100.]*9+[120., 80.])
        for before, after, date in (
            ("2026-03-07T04:30:00+08:00", "2026-03-07T05:01:00+08:00", "2026-03-06"),
            ("2026-03-14T03:30:00+08:00", "2026-03-14T04:01:00+08:00", "2026-03-13"),
        ):
            with self.subTest(date=date):
                early = build_weekly_ene_frame(source, market="US", as_of=before)
                final = build_weekly_ene_frame(source, market="US", as_of=after)
                self.assertEqual(early.iloc[-1].date, pd.Timestamp(date))
                self.assertTrue(early.iloc[-1].provisional)
                self.assertFalse(final.iloc[-1].provisional)

    def test_us_and_cn_holiday_short_weeks_end_at_actual_last_session(self):
        from tools.ene_state_band import build_weekly_ene_frame
        for market, latest, before, after in (
            ("US", "2026-04-02", "2026-04-02T15:59:00-04:00", "2026-04-02T16:01:00-04:00"),
            ("CN", "2026-09-30", "2026-09-30T14:59:00+08:00", "2026-09-30T15:01:00+08:00"),
        ):
            with self.subTest(market=market):
                dates = pd.date_range(end=pd.Timestamp(latest).to_period("W-FRI").end_time.normalize(), periods=10, freq="W-FRI").to_list()
                dates[-1] = pd.Timestamp(latest)
                source = pd.DataFrame({"date": dates, "close": [100.]*9+[120.]})
                early = build_weekly_ene_frame(source, market=market, as_of=before)
                final = build_weekly_ene_frame(source, market=market, as_of=after)
                self.assertTrue(early.iloc[-1].provisional)
                self.assertFalse(final.iloc[-1].provisional)
                self.assertEqual(final.iloc[-1].date, pd.Timestamp(latest))

    def test_calendar_failure_preserves_prices_and_marks_current_week_temporary(self):
        from tools.ene_state_band import build_weekly_ene_frame
        source = pd.DataFrame({"date": pd.date_range("2027-01-01", periods=10, freq="W-FRI"),
                               "close": [100.]*9+[120.]})
        with patch("pandas_market_calendars.get_calendar", side_effect=RuntimeError("calendar unavailable")):
            out = build_weekly_ene_frame(source, market="US", as_of="2027-03-05T17:00:00-05:00")
        self.assertTrue(out.iloc[-1].provisional)
        self.assertEqual(out.iloc[-1].state, "HIGH")
        self.assertEqual(out.iloc[-1].close, 120.)

    def test_us_date_filter_uses_market_day_not_beijing_day(self):
        from tools.ene_state_band import build_weekly_ene_frame
        source = weekly_prices([100.]*9+[120.])
        source = pd.concat([source, pd.DataFrame({"date": [pd.Timestamp("2026-03-07")], "close": [1.]})])
        out = build_weekly_ene_frame(source, market="US", as_of="2026-03-07T04:30:00+08:00")
        self.assertEqual(len(out), 10)
        self.assertEqual(out.iloc[-1].date, pd.Timestamp("2026-03-06"))
        self.assertEqual(out.iloc[-1].close, 120.)

    def test_three_layer_geometry_without_ebs(self):
        from tools.market_breadth import price_band_layout
        for dpi in (100, 180):
            fig, ax = plt.subplots(figsize=(12, 6.6), dpi=dpi)
            layout = price_band_layout(ax, dpi, include_ene=True)
            pixels = ax.bbox.height
            for names in (("vix_negative", "breadth_low", "ene_low"),
                          ("vix_positive", "breadth_high", "ene_high")):
                for first, second in zip(names, names[1:]):
                    self.assertAlmostEqual((layout[second]-layout[first]-layout["height"])*pixels, 2)
            self.assertAlmostEqual(layout["height"]*pixels, 16)
            self.assertAlmostEqual((1-layout["ene_high"]-layout["height"])*pixels, 5)

    def test_three_and_four_bands_export_at_fixed_pixel_spacing(self):
        from PIL import Image
        from tools.ene_state_band import draw_ene_state_band
        from tools.equity_bond_spread import draw_ebs_state_band
        from tools.market_breadth import draw_breadth_state_band
        dates = pd.bdate_range("2026-01-05", periods=20)
        price = pd.DataFrame({"date": dates})
        vix = pd.DataFrame({"date": dates, "VIX_MA_SPREAD": [-6.]*10+[6.]*10})
        breadth = pd.DataFrame({"date": dates, "percent": [10.]*10+[90.]*10})
        ebs = pd.DataFrame({"date": dates, "state": ["HIGH"]*10+["LOW"]*10})
        ene = pd.DataFrame({"date": dates, "ene_state": ["LOW"]*10+["HIGH"]*10,
                            "ene_label": ["E1.8"]*10+["E2.4"]*10})
        for dpi, include_ebs in ((100, False), (180, False), (100, True), (180, True)):
            fig, ax = plt.subplots(figsize=(12, 6.6))
            ax.set_xlim(dates[0], dates[-1])
            ax.set_ylim(0, 1)
            rsi_data.draw_vix_state_band(ax, price, vix, output_dpi=dpi, include_ebs=include_ebs, include_ene=True)
            draw_breadth_state_band(ax, price, breadth, 20, 80, output_dpi=dpi, include_ebs=include_ebs, include_ene=True)
            if include_ebs:
                draw_ebs_state_band(ax, price, ebs, output_dpi=dpi, include_ene=True)
            draw_ene_state_band(ax, price, ene, output_dpi=dpi, include_ebs=include_ebs)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/"bands.png"
                fig.savefig(path, dpi=dpi)
                pixels = np.asarray(Image.open(path).convert("RGB"))
            for date in (dates[3], dates[13]):
                x = int(ax.transData.transform((mdates.date2num(date), 0))[0]*dpi/fig.dpi)
                column = pixels[:, x]
                colored = (column.max(axis=1)-column.min(axis=1)>20) & (column.min(axis=1)<240)
                rows = np.flatnonzero(colored)
                runs = np.split(rows, np.flatnonzero(np.diff(rows)>1)+1)
                self.assertEqual(len(runs), 4 if include_ebs else 3)
                self.assertTrue(all(abs(len(run)-16)<=1 for run in runs))
                self.assertTrue(all(abs(runs[i+1][0]-runs[i][-1]-3)<=1 for i in range(len(runs)-1)))
                y_edge = (fig.bbox.height-ax.bbox.y1 if date == dates[13]
                          else fig.bbox.height-ax.bbox.y0)*dpi/fig.dpi
                gap = runs[0][0]-y_edge if date == dates[13] else y_edge-runs[-1][-1]-1
                self.assertAlmostEqual(gap, 5, delta=1)

    def test_friday_close_and_no_future_daily_prices(self):
        from tools.ene_state_band import build_weekly_ene_frame
        source = weekly_prices([100.]*9+[120., 80.])
        early = build_weekly_ene_frame(source, as_of="2026-03-06 11:00")
        self.assertEqual(len(early), 10)
        self.assertTrue(early.iloc[-1].provisional)
        final = build_weekly_ene_frame(source, as_of="2026-03-06 15:01")
        self.assertFalse(final.iloc[-1].provisional)
        self.assertEqual(final.iloc[-1].close, 120.)

    def test_identical_adjacent_weeks_merge_but_missing_week_breaks(self):
        from tools.ene_state_band import draw_ene_state_band
        dates = pd.to_datetime(["2026-01-09", "2026-01-16", "2026-01-30"])
        price = pd.DataFrame({"date": dates})
        ene = pd.DataFrame({"date": dates, "ene_state": "LOW", "ene_label": "E1.8"})
        fig, ax = plt.subplots()
        ax.set_xlim(dates[0], dates[-1])
        bands = draw_ene_state_band(ax, price, ene)
        self.assertEqual(len(bands), 2)
        for date in pd.bdate_range("2026-01-19", "2026-01-23"):
            x = mdates.date2num(date)
            self.assertFalse(any(p.get_x() <= x <= p.get_x()+p.get_width() for p in bands))

    def test_normal_warmup_and_invalid_prices_have_no_band(self):
        from tools.ene_state_band import build_weekly_ene_frame, expand_weekly_ene_to_price_dates, draw_ene_state_band
        for source in (weekly_prices([100.]*12), weekly_prices([120.]*9),
                       weekly_prices([100.]*9+[0., -1., np.nan])):
            weekly = build_weekly_ene_frame(source, as_of="2026-04-01")
            daily = expand_weekly_ene_to_price_dates(weekly, source.date)
            fig, ax = plt.subplots()
            self.assertEqual(draw_ene_state_band(ax, source, daily), [])
            self.assertFalse(ax.texts)

    def test_weekly_calculation_prewarm_and_input_preserved(self):
        from tools.ene_state_band import build_weekly_ene_frame
        source = weekly_prices([100.] * 9 + [120.])
        original = source.copy(deep=True)
        out = build_weekly_ene_frame(source, as_of="2026-04-01")
        assert out.ma.iloc[:9].isna().all()
        assert out.label.iloc[:9].isna().all()
        assert out.iloc[-1]["ma"] == 102.
        assert np.isclose(out.iloc[-1]["upper"], 113.22)
        assert np.isclose(out.iloc[-1]["lower"], 92.82)
        assert out.iloc[-1]["state"] == "HIGH"
        assert np.isclose(out.iloc[-1]["deviation"], 5.98834128246)
        assert out.iloc[-1]["label"] == "E6.0"
        pd.testing.assert_frame_equal(source, original)

    def test_deviation_and_inclusive_boundaries(self):
        for closes, state, label in [([100.] * 9 + [80.], "LOW", "E10.3"),
                  ([100.] * 10, "NORMAL", None),
                  ([100.] * 8 + [89., 111.], "HIGH", "E0.0"),
                  ([100.] * 8 + [89., 111.-1e-9], "NORMAL", None),
                  ([100.] * 8 + [109., 91.+1e-9], "NORMAL", None),
                  ([100.] * 8 + [109., 91.], "LOW", "E0.0")]:
            from tools.ene_state_band import build_weekly_ene_frame
            last = build_weekly_ene_frame(weekly_prices(closes), as_of="2026-04-01").iloc[-1]
            assert last.state == state
            assert last.label == label

    def test_partial_week_uses_latest_price_once_and_does_not_invent_holiday_week(self):
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            from tools.ene_state_band import build_weekly_ene_frame
            source = weekly_prices([100.] * 9)
            source = pd.concat([source, pd.DataFrame({"date": pd.to_datetime(["2026-03-09", "2026-03-11"]),
                                                    "close": [110., 120.]})], ignore_index=True)
            cached = tmp_path / "daily.csv"
            source.to_csv(cached, index=False)
            before = cached.read_bytes()
            out = build_weekly_ene_frame(source, as_of="2026-03-11 12:00")
            assert len(out) == 10
            assert out.iloc[-1].close == 120.
            assert out.iloc[-1].week_end_date == pd.Timestamp("2026-03-13")
            assert out.iloc[-1].date == pd.Timestamp("2026-03-11")
            assert out.iloc[-1].provisional
            updated = source.copy()
            updated.loc[updated.index[-1], "close"] = 80.
            assert build_weekly_ene_frame(updated, as_of="2026-03-11 13:00").iloc[-1].state == "LOW"
            after = build_weekly_ene_frame(source, as_of="2026-03-18")
            assert len(after) == 10
            assert not after.iloc[-1].provisional
            assert cached.read_bytes() == before

    def test_week_mapping_is_exact_and_never_carries_into_next_week(self):
        from tools.ene_state_band import build_weekly_ene_frame, expand_weekly_ene_to_price_dates
        weekly = build_weekly_ene_frame(weekly_prices([100.] * 9 + [120.]), as_of="2026-04-01")
        daily = pd.to_datetime(["2026-03-02", "2026-03-05", "2026-03-06", "2026-03-09"])
        out = expand_weekly_ene_to_price_dates(weekly, daily)
        assert out.ene_state.iloc[:3].tolist() == ["HIGH"] * 3
        assert out.ene_label.iloc[:3].tolist() == ["E6.0"] * 3
        assert pd.isna(out.ene_state.iloc[3])

    def test_invalid_latest_close_breaks_week_and_warmup(self):
        from tools.ene_state_band import build_weekly_ene_frame
        source = weekly_prices([100.] * 9 + [np.inf, 120.])
        out = build_weekly_ene_frame(source, as_of="2026-04-01")
        assert out.iloc[-2:].label.isna().all()
        assert out.iloc[-2:].state.isna().all()
        # 同周早先有效价格不能掩盖最后一笔无效价格。
        source = pd.concat([source.iloc[:9], pd.DataFrame({"date": pd.to_datetime(["2026-03-05", "2026-03-06"]),
                                                          "close": [120., np.nan]})])
        assert pd.isna(build_weekly_ene_frame(source, as_of="2026-04-01").iloc[-1].state)

    def test_four_layers_pixel_geometry_and_old_layouts(self):
        for dpi in (100, 180):
            from tools.market_breadth import price_band_layout
            fig, ax = plt.subplots(figsize=(12, 6.6), dpi=dpi)
            layout = price_band_layout(ax, dpi, include_ebs=True, include_ene=True)
            pixels = ax.bbox.height
            assert np.isclose(layout["height"] * pixels, 16)
            assert np.isclose(layout["vix_negative"] * pixels, 5)
            assert np.isclose((1-layout["ene_high"]-layout["height"]) * pixels, 5)
            for names in (("vix_negative", "breadth_low", "ebs_high", "ene_low"),
                          ("vix_positive", "breadth_high", "ebs_low", "ene_high")):
                for first, second in zip(names, names[1:]):
                    assert np.isclose((layout[second]-layout[first]-layout["height"]) * pixels, 2)
            old = price_band_layout(ax, dpi)
            three = price_band_layout(ax, dpi, include_ebs=True)
            assert np.isclose((1-old["breadth_high"]-old["height"]) * pixels, 5)
            assert np.isclose((1-three["ebs_low"]-three["height"]) * pixels, 5)
            plt.close(fig)

    def test_weekly_segments_force_labels_newest_wins_and_axes_unchanged(self):
        for dpi, include_ebs in ((100, False), (180, False), (100, True), (180, True)):
            from tools.ene_state_band import draw_ene_state_band
            dates = pd.bdate_range("2026-01-05", periods=20)
            price = pd.DataFrame({"date": dates})
            ene = pd.DataFrame({"date": dates, "ene_state": "HIGH",
                                "ene_label": np.repeat(["E1.0", "E2.0", "E3.0", "E4.0"], 5)})
            fig, ax = plt.subplots(figsize=(3, 2), dpi=dpi)
            ax.set_xlim(pd.Timestamp("2025-07-01"), pd.Timestamp("2026-02-01"))
            ax.set_ylim(90, 110)
            limits = ax.get_xlim(), ax.get_ylim()
            bands = draw_ene_state_band(ax, price, ene, output_dpi=dpi, include_ebs=include_ebs)
            fig.canvas.draw()
            assert len(bands) == 4
            assert (ax.get_xlim(), ax.get_ylim()) == limits
            labels = [t for t in ax.texts if t.get_gid() == "ene-state-label"]
            assert "E4.0" in [t.get_text() for t in labels]
            boxes = [t.get_window_extent() for t in labels]
            for box in boxes:
                assert box.x0 >= ax.bbox.x0
                assert box.x1 <= ax.bbox.x1
                assert box.y0 >= ax.bbox.y0
                assert box.y1 <= ax.bbox.y1
            assert all(not a.overlaps(b) for i, a in enumerate(boxes) for b in boxes[i+1:])
            assert len(labels) < 4
            plt.close(fig)

    def test_different_week_deviations_not_merged_and_invalid_draw_is_safe(self):
        from tools.ene_state_band import draw_ene_state_band
        dates = pd.bdate_range("2026-01-05", periods=15)
        price = pd.DataFrame({"date": dates})
        ene = pd.DataFrame({"date": dates, "ene_state": ["HIGH"]*10 + ["LOW"]*5,
                            "ene_label": ["E1.6"]*5+["E2.4"]*5+["E1.8"]*5})
        fig, ax = plt.subplots()
        ax.set_xlim(dates[0], dates[-1])
        bands = draw_ene_state_band(ax, price, ene)
        assert len(bands) == 3
        assert bands[-1].get_y() < bands[0].get_y()
        for invalid in (None, pd.DataFrame(), pd.DataFrame({"date": ["bad"], "ene_state": ["HIGH"]})):
            assert draw_ene_state_band(ax, price, invalid) == []
        plt.close(fig)

    def test_enabled_scope_price_only_and_existing_panels_are_unchanged(self):
        enabled = {x["kwargs"]["symbol"] for x in RSI_ANALYSIS_CONFIGS if x["kwargs"].get("show_ene_state_band")}
        self.assertEqual(enabled, {x["kwargs"]["symbol"] for x in RSI_ANALYSIS_CONFIGS})
        self.assertEqual(len(enabled), 6)
        self.assertTrue(RSI_NASDAQ100_CONFIG["kwargs"]["show_ene_state_band"])
        frame = weekly_prices([100.]*9+[120., 80., 120.])
        frame["RSI"] = 50.
        breadth = pd.DataFrame({"date": frame.date, "percent": 10., "kind": "close"})
        macro = pd.DataFrame({"available_at": ["2025-01-01"], "ratio": [.81], "basis": ["observed"]})
        opts = dict(show_mc_gdp=True, mc_gdp_df=macro, show_breadth=True, breadth_df=breadth)
        plain = render(frame, **opts)
        fig = render(frame, **opts, show_ene_state_band=True)
        assert len(fig.axes) == 3
        assert fig.axes[1].get_ylim() == (0, 100)
        assert fig.axes[1].get_position().bounds == fig.axes[2].get_position().bounds
        assert fig.axes[0].get_ylim() == plain.axes[0].get_ylim()
        assert fig.axes[0].get_xlim() == plain.axes[0].get_xlim()
        assert len([p for p in fig.axes[0].get_children() if p.get_gid() == "ene-state-band"]) == 3
        for old, new in zip(plain.axes, fig.axes):
            assert [line.get_label() for line in old.lines] == [line.get_label() for line in new.lines]
        for old, new in zip(plain.axes[1:], fig.axes[1:]):
            assert [t.get_text() for t in old.texts] == [t.get_text() for t in new.texts]
            assert not any(str(a.get_gid()).startswith("ene-") for a in new.get_children())
        assert [t.get_text() for t in fig.axes[1].get_legend().get_texts()] == ["R", "50D", "MC/GDP"]
        assert len(render(frame, show_rsi_panel=False, show_ene_state_band=True).axes) == 1
        for cfg in RSI_ANALYSIS_CONFIGS:
            other = render(frame, symbol=cfg["kwargs"]["symbol"], show_ene_state_band=cfg["kwargs"]["show_ene_state_band"])
            self.assertEqual(len(other.axes), 2)
            self.assertTrue(any(p.get_gid() == "ene-state-band" for p in other.axes[0].get_children()))
            self.assertFalse(any(str(p.get_gid()).startswith("ene-") for p in other.axes[1].get_children()))
            disabled = render(frame, symbol=cfg["kwargs"]["symbol"], show_ene_state_band=False)
            self.assertFalse(any(p.get_gid() == "ene-state-band" for ax in disabled.axes for p in ax.get_children()))
        plt.close("all")

    def test_standalone_rsi_entry_enables_ene(self):
        with patch.object(rsi_data, "rsi_analyze_index", return_value=pd.DataFrame()) as analyze:
            rsi_data.main()
        self.assertTrue(analyze.call_args.kwargs.get("show_ene_state_band"))

    def test_analysis_and_direct_plot_pass_correct_price_market(self):
        from tools.ene_state_band import build_weekly_ene_frame
        source = weekly_prices([100.]*9+[120., 80.])
        for symbol, market in ((".NDX", "US"), (".DJI", "US"), ("159561", "CN"),
                               ("512890", "CN"), ("159943", "CN"), ("560220", "CN")):
            with self.subTest(symbol=symbol):
                with patch.object(rsi_data, "get_index_akshare", return_value=source), \
                     patch.object(rsi_data, "plot_analysis"), \
                     patch("tools.ene_state_band.build_weekly_ene_frame", wraps=build_weekly_ene_frame) as build:
                    rsi_data.rsi_analyze_index(symbol=symbol, days=2, signal_fetch_days=100,
                                              show_ene_state_band=True, show_vix_state_band=False,
                                              do_print=False, save_signal_table=False)
                self.assertEqual(build.call_args.kwargs.get("market"), market)
                direct = source.assign(RSI=50.)
                with patch("tools.ene_state_band.build_weekly_ene_frame", wraps=build_weekly_ene_frame) as build:
                    render(direct, symbol=symbol, show_ene_state_band=True)
                self.assertEqual(build.call_args.kwargs.get("market"), market)

    def test_analyze_uses_full_history_before_visible_slice(self):
        source = weekly_prices([100.]*9+[120., 80.])
        with patch.object(rsi_data, "get_index_akshare", return_value=source), \
             patch.object(rsi_data, "plot_analysis") as draw:
            out = rsi_data.rsi_analyze_index(symbol="159943", days=2, signal_fetch_days=100,
                                            show_ene_state_band=True, show_vix_state_band=False,
                                            do_print=False, show_plot=False, save_signal_table=False,
                                            do_plot=True, return_signals=False)
        assert len(out) == 2
        ene = draw.call_args.kwargs["ene_state_df"]
        assert ene.ene_state.tolist() == ["HIGH", "LOW"]
        assert len(source) == 11
