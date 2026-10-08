import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
from tools import rsi_data
from tools.configs.rsi_configs import RSI_ANALYSIS_CONFIGS, RSI_NASDAQ100_CONFIG
import sync_repos
import service_runner

class IntegrationTests(unittest.TestCase):
    def test_nasdaq_chart_uses_ndx_price_and_independent_breadth(self):
        config = RSI_NASDAQ100_CONFIG
        self.assertEqual(config["name"], "纳斯达克100指数")
        self.assertEqual(config["kwargs"]["symbol"], ".NDX")
        self.assertEqual(config["kwargs"]["display_name"], "纳斯达克100指数")
        self.assertEqual(config["kwargs"]["breadth_key"], "nasdaq100")
        self.assertEqual(config["kwargs"]["output_file"], "output/nasdaq_analysis.png")

    def test_legacy_analysis_entry_accepts_breadth_and_keeps_seven_return_values(self):
        from tools.rsi_module import rsi_analyze_index
        days=pd.bdate_range("2024-01-01",periods=500)
        frame=pd.DataFrame({"date":days,"open":100.,"close":np.arange(500)+100.,"high":601.,"low":90.,"volume":1000.})
        with patch.object(rsi_data,"get_index_akshare",return_value=frame):
            result=rsi_analyze_index(symbol=".DJI",breadth_key="dow",do_plot=False,do_print=False,save_signal_table=False,return_signals=True)
        self.assertEqual(len(result),7)
        self.assertEqual(len(result[0]),180)
    def test_collection_failure_does_not_stop_existing_price_charts(self):
        import stock_analysis
        with patch("tools.breadth_engine.refresh_for_charts",side_effect=RuntimeError("OpenD unavailable")),patch("tools.a_share_valuation.load_valuation",return_value=pd.DataFrame()),patch("tools.equity_bond_spread.load_ebs_states",return_value=pd.DataFrame()),patch.object(stock_analysis,"_run_rsi_analysis") as run,patch.object(stock_analysis,"build_change_summary_text",return_value="ok"):
            text,images=stock_analysis.build_stock_analysis()
        self.assertEqual(text,"ok")
        self.assertEqual(run.call_count,len(RSI_ANALYSIS_CONFIGS))
        self.assertIn("output/dow_jones_analysis.png",images)

    def test_five_configurations_and_dow_image(self):
        mapping={c["kwargs"]["symbol"]:c for c in RSI_ANALYSIS_CONFIGS}
        for symbol in [".NDX",".DJI","512890","560220","159943"]:
            self.assertIn(symbol,mapping)
            self.assertTrue(mapping[symbol]["kwargs"].get("breadth_key"))
        self.assertEqual(mapping[".DJI"]["image"],"output/dow_jones_analysis.png")
        self.assertNotIn("sh000001",mapping)
        self.assertNotIn(".IXIC",mapping)
        for symbol in (".NDX",".DJI","512890","560220","159943"):
            self.assertIn("breadth_band_low_threshold",mapping[symbol]["kwargs"])
            self.assertIn("breadth_band_high_threshold",mapping[symbol]["kwargs"])
            self.assertIsNone(mapping[symbol]["kwargs"]["breadth_band_low_threshold"])
            self.assertIsNone(mapping[symbol]["kwargs"]["breadth_band_high_threshold"])
    def test_breadth_overlay_shares_rsi_axis_and_keeps_gaps(self):
        days=pd.date_range("2026-01-01",periods=60)
        frame=pd.DataFrame({"date":days,"close":np.arange(60)+100.,"volume":1000.,"RSI":50.})
        breadth=pd.DataFrame({"date":days,"percent":[60.]*58+[None,70.],"kind":["close"]*59+["intraday"]})
        with tempfile.TemporaryDirectory() as tmp,patch.object(rsi_data.plt,"close") as close:
            rsi_data.plot_analysis(frame,"TEST",output_file=str(Path(tmp)/"chart.png"),show_plot=False,breadth_df=breadth,show_breadth=True)
            fig=close.call_args.args[0];axis=fig.axes[1]
            line=next(x for x in axis.lines if x.get_label()=="50D")
            self.assertTrue(np.isnan(line.get_ydata()[-2]));self.assertEqual(axis.get_ylim(),(0.,100.))
            self.assertEqual(len(fig.axes),2)
            self.assertIn("R",[t.get_text() for t in axis.get_legend().get_texts()])
            self.assertIn("50D",[t.get_text() for t in axis.get_legend().get_texts()])
            self.assertEqual(axis.get_legend()._ncols,3)
            self.assertLess(axis.get_legend().borderpad,.4)
            labels={t.get_text():t for t in axis.texts}
            self.assertIn("R: 50.0",labels)
            self.assertIn("50D: 70.0%",labels)
            self.assertLess(labels["R: 50.0"].get_position()[1],labels["50D: 70.0%"].get_position()[1])
            self.assertEqual(labels["R: 50.0"].get_ha(),"left")
            self.assertEqual(labels["50D: 70.0%"].get_ha(),"left")
            self.assertEqual(labels["R: 50.0"].get_position()[0],labels["50D: 70.0%"].get_position()[0])
    def test_latest_rsi_value_is_shown_without_breadth(self):
        days=pd.date_range("2026-01-01",periods=12)
        frame=pd.DataFrame({"date":days,"close":[100.]*12,"volume":[1000.]*12,
                            "RSI":[42.]*11+[44.6]})
        with tempfile.TemporaryDirectory() as tmp,patch.object(rsi_data.plt,"close") as close:
            rsi_data.plot_analysis(frame,"TEST",output_file=str(Path(tmp)/"chart.png"),show_plot=False)
            axis=close.call_args.args[0].axes[1]
            self.assertIn("R: 44.6",[t.get_text() for t in axis.texts])
    def test_nasdaq_latest_breadth_label_has_no_parenthetical_suffix(self):
        from tools.market_breadth import draw_breadth
        import matplotlib.pyplot as plt
        fig, axis = plt.subplots()
        frame = pd.DataFrame({"date": ["2026-09-25"], "percent": [34.6],
                              "kind": ["close"], "source": ["stockcharts_NAA50R"]})
        draw_breadth(axis, frame)
        self.assertEqual([label.get_text() for label in axis.texts], ["50D: 34.6%"])
        plt.close(fig)

    def test_ndx_backcast_is_labeled_without_changing_latest_formal_label(self):
        from tools.market_breadth import draw_breadth
        import matplotlib.pyplot as plt
        fig, axis = plt.subplots()
        frame = pd.DataFrame({"date": ["2026-09-28", "2026-09-29"],
                              "percent": [40., 45.], "kind": ["close", "close"]})
        frame.attrs["breadth_display"] = {"current": True, "backcast_end": "2026-09-28",
                                          "backcast_basis_date": "2026-09-29"}
        draw_breadth(axis, frame)
        labels = [item.get_text() for item in axis.texts]
        self.assertIn("50D: 45.0%", labels)
        self.assertIn("历史50D按2026-09-29成分回算", labels)
        plt.close(fig)

    def test_latest_completed_close_remains_visible_when_intraday_expired(self):
        from tools.market_breadth import draw_breadth
        import matplotlib.pyplot as plt
        fig,axis=plt.subplots()
        frame=pd.DataFrame({"date":["2026-09-28","2026-09-29"],"percent":[36.7,None],
                            "kind":["close","intraday"]})
        frame.attrs["breadth_display"]={"current":True,"age_sessions":0,"max_age_sessions":5}
        draw_breadth(axis,frame)
        self.assertIn("50D: 36.7%",[text.get_text() for text in axis.texts])
        self.assertEqual([text.get_text() for text in axis.get_legend().get_texts()],["50D"])
        plt.close(fig)
    def test_price_breadth_band_uses_configurable_extremes_and_keeps_gaps(self):
        from tools.market_breadth import draw_breadth_state_band
        import matplotlib.pyplot as plt
        dates=pd.date_range("2026-01-02",periods=6,freq="B")
        prices=pd.DataFrame({"date":dates,"close":[100.]*6})
        breadth=pd.DataFrame({"date":dates,"percent":[30,30,50,70,80,None],"kind":["close"]*6})
        fig,axis=plt.subplots();axis.set_ylim(90,110);axis.set_xlim(dates[0]-pd.Timedelta(days=1),dates[-1]+pd.Timedelta(days=1))
        patches=draw_breadth_state_band(axis,prices,breadth,30,70)
        self.assertEqual(len(patches),2)
        self.assertEqual([t.get_text() for t in axis.texts],["50D","50D"])
        self.assertEqual(axis.get_ylim(),(90,110))
        low_patch,high_patch=patches
        self.assertLess(low_patch.get_y(),.2)
        self.assertGreater(high_patch.get_y(),.90)
        self.assertLess(high_patch.get_y()+high_patch.get_height(),1.0)
        self.assertLess(patches[-1].get_x()+patches[-1].get_width(),__import__('matplotlib').dates.date2num(dates[-1]))
        plt.close(fig)

    def test_each_separate_50d_band_has_its_own_label(self):
        from tools.market_breadth import draw_breadth_state_band
        import matplotlib.pyplot as plt
        dates=pd.date_range("2026-01-02",periods=7,freq="B")
        prices=pd.DataFrame({"date":dates,"close":[100.]*7})
        breadth=pd.DataFrame({"date":dates,"percent":[75,75,50,80,50,20,20]})
        fig,axis=plt.subplots()
        axis.set_xlim(dates[0]-pd.Timedelta(days=1),dates[-1]+pd.Timedelta(days=1))
        patches=draw_breadth_state_band(axis,prices,breadth,30,70)
        labels=[t for t in axis.texts if t.get_text()=="50D"]
        self.assertEqual(len(patches),3)
        self.assertEqual(len(labels),3)
        for label, band in zip(labels,patches):
            self.assertEqual(label.get_ha(),"right")
            fig.canvas.draw()
            text_box=label.get_window_extent(fig.canvas.get_renderer())
            band_box=band.get_window_extent(fig.canvas.get_renderer())
            self.assertLessEqual(text_box.x1,band_box.x1)
            self.assertGreaterEqual(text_box.x0,band_box.x0)
        plt.close(fig)

    def test_narrow_50d_band_stays_colored_without_letter(self):
        from tools.market_breadth import draw_breadth_state_band
        import matplotlib.pyplot as plt
        dates=pd.date_range("2026-01-02",periods=200,freq="B")
        prices=pd.DataFrame({"date":dates,"close":[100.]*200})
        breadth=pd.DataFrame({"date":dates,"percent":[50.]*100+[80]+[50.]*99})
        fig,axis=plt.subplots()
        axis.set_xlim(dates[0],dates[-1])
        patches=draw_breadth_state_band(axis,prices,breadth,30,70)
        self.assertEqual(len(patches),1)
        self.assertEqual(list(axis.texts),[])
        plt.close(fig)

    def test_four_state_bands_use_top_and_bottom_pairs_with_two_pixel_gaps(self):
        from tools.market_breadth import draw_breadth_state_band
        from tools.rsi_data import draw_vix_state_band
        import matplotlib.pyplot as plt
        dates=pd.date_range("2026-01-02",periods=4,freq="B")
        prices=pd.DataFrame({"date":dates,"close":[100.]*4})
        vix=pd.DataFrame({"date":dates,"VIX_MA_SPREAD":[-6,-6,6,6]})
        breadth=pd.DataFrame({"date":dates,"percent":[20,20,90,90]})
        fig,axis=plt.subplots(figsize=(12,8),dpi=180)
        axis.set_xlim(dates[0],dates[-1]);fig.tight_layout()
        vix_patches=draw_vix_state_band(axis,prices,vix,-5,5,output_dpi=180)
        breadth_patches=draw_breadth_state_band(axis,prices,breadth,30,80,output_dpi=180)
        red,teal=vix_patches
        amber,purple=breadth_patches
        self.assertLess(red.get_y(),amber.get_y())
        self.assertLess(teal.get_y(),purple.get_y())
        self.assertGreater(teal.get_y(),.8)
        self.assertAlmostEqual((amber.get_y()-red.get_y()-red.get_height())*axis.bbox.height,2,delta=.2)
        self.assertAlmostEqual((purple.get_y()-teal.get_y()-teal.get_height())*axis.bbox.height,2,delta=.2)
        self.assertGreaterEqual(amber.get_facecolor()[-1],.8)
        self.assertGreaterEqual(purple.get_facecolor()[-1],.8)
        plt.close(fig)

    def test_breadth_threshold_overrides_are_per_chart_with_global_fallback(self):
        days=pd.date_range("2026-01-01",periods=60)
        frame=pd.DataFrame({"date":days,"close":[100.]*60,"volume":[1000.]*60,"RSI":[50.]*60})
        breadth=pd.DataFrame({"date":days,"percent":[50.]*60,"kind":["close"]*60})
        from tools.configs import market_breadth_configs as config
        with tempfile.TemporaryDirectory() as tmp,patch.object(config,"BREADTH_BAND_LOW_THRESHOLD",25),patch.object(config,"BREADTH_BAND_HIGH_THRESHOLD",80):
            with patch("tools.market_breadth.draw_breadth_state_band") as band:
                rsi_data.plot_analysis(frame,"TEST",output_file=str(Path(tmp)/"default.png"),show_plot=False,breadth_df=breadth,show_breadth=True)
                self.assertEqual(band.call_args.args[3:5],(25,80))
                rsi_data.plot_analysis(frame,"TEST",output_file=str(Path(tmp)/"high.png"),show_plot=False,breadth_df=breadth,show_breadth=True,breadth_band_high_threshold=70)
                self.assertEqual(band.call_args.args[3:5],(25,70))
                rsi_data.plot_analysis(frame,"TEST",output_file=str(Path(tmp)/"both.png"),show_plot=False,breadth_df=breadth,show_breadth=True,breadth_band_low_threshold=35,breadth_band_high_threshold=75)
                self.assertEqual(band.call_args.args[3:5],(35,75))

    def test_rsi_entry_passes_single_chart_breadth_thresholds_to_plot(self):
        days=pd.bdate_range("2024-01-01",periods=500)
        frame=pd.DataFrame({"date":days,"open":100.,"close":np.arange(500)+100.,
                            "high":601.,"low":90.,"volume":1000.})
        with patch.object(rsi_data,"get_index_akshare",return_value=frame),patch.object(rsi_data,"plot_analysis") as plot:
            rsi_data.rsi_analyze_index(symbol=".DJI",do_print=False,save_signal_table=False,
                                       breadth_band_low_threshold=22,breadth_band_high_threshold=83)
        self.assertEqual(plot.call_args.kwargs["breadth_band_low_threshold"],22)
        self.assertEqual(plot.call_args.kwargs["breadth_band_high_threshold"],83)

    def test_threshold_override_changes_band_only_not_breadth_curve(self):
        days=pd.bdate_range("2026-01-02",periods=60)
        frame=pd.DataFrame({"date":days,"close":[100.]*60,"volume":[1000.]*60,"RSI":[50.]*60})
        breadth=pd.DataFrame({"date":days,"percent":[50.]*58+[75.,85.],"kind":["close"]*60})
        from tools.configs import market_breadth_configs as config
        with tempfile.TemporaryDirectory() as tmp,patch.object(config,"BREADTH_BAND_HIGH_THRESHOLD",80),patch.object(rsi_data.plt,"close") as close:
            rsi_data.plot_analysis(frame,"TEST",output_file=str(Path(tmp)/"default.png"),show_plot=False,breadth_df=breadth,show_breadth=True,show_boll=False)
            default_fig=close.call_args.args[0]
            rsi_data.plot_analysis(frame,"TEST",output_file=str(Path(tmp)/"override.png"),show_plot=False,breadth_df=breadth,show_breadth=True,show_boll=False,breadth_band_high_threshold=70)
            override_fig=close.call_args.args[0]
        default_band=default_fig.axes[0].patches[0]
        override_band=override_fig.axes[0].patches[0]
        self.assertGreater(override_band.get_width(),default_band.get_width())
        default_line=next(line for line in default_fig.axes[1].lines if line.get_label()=="50D")
        override_line=next(line for line in override_fig.axes[1].lines if line.get_label()=="50D")
        np.testing.assert_allclose(default_line.get_ydata(),override_line.get_ydata())

    def test_actual_conflict_handler_merges_complete_and_intraday_without_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/"cache/market_breadth/results/dow.json";target.parent.mkdir(parents=True)
            left=json.dumps({"type":"results","rows":[{"date":"2026-09-25","percent":40,"kind":"close"}]})
            right=json.dumps({"type":"results","rows":[{"date":"2026-09-25","percent":60,"kind":"intraday"}]})
            with patch.object(sync_repos,"git_stage_text",side_effect=[left,right]),patch.object(sync_repos,"run_git"):
                sync_repos.merge_cache_conflict_file(root,"cache/market_breadth/results/dow.json")
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["rows"][0]["percent"],40)
    def test_breadth_audit_is_read_only_and_reports_oversize(self):
        from tools.market_breadth import audit_store
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/"prices/US.A.json";p.parent.mkdir()
            p.write_text(json.dumps({"type":"prices","rows":[{"date":"2026-01-01","close":10}]*401}),encoding="utf-8")
            before=p.read_bytes();report=audit_store(root)
            self.assertTrue(report["warnings"]);self.assertEqual(p.read_bytes(),before)
    def test_sync_whitelist_and_local_state_exclusion(self):
        self.assertTrue(sync_repos.is_auto_merge_cache_path("cache/market_breadth/prices/US.AAPL.json"))
        self.assertTrue(sync_repos.is_auto_merge_cache_path("cache/market_breadth/membership_events/dow.json"))
        self.assertTrue(sync_repos.is_auto_merge_cache_path("cache/market_breadth/benchmarks/nasdaq_stockcharts.json"))
        self.assertTrue(sync_repos.is_auto_merge_cache_path("cache/market_breadth/direct_indicators/dow.json"))
        self.assertFalse(sync_repos.is_auto_merge_cache_path("cache/market_breadth_local/refresh.lock"))
        self.assertTrue(service_runner.is_blocked_path("cache/market_breadth_local/refresh.lock"))
        self.assertFalse(service_runner.is_blocked_path("cache/market_breadth/results/dow.json"))

if __name__=="__main__":unittest.main()
