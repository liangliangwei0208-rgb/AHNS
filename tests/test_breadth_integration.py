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
from tools.configs.rsi_configs import RSI_ANALYSIS_CONFIGS
import sync_repos
import service_runner

class IntegrationTests(unittest.TestCase):
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
        with patch("tools.breadth_engine.refresh_for_charts",side_effect=RuntimeError("OpenD unavailable")),patch.object(stock_analysis,"_run_rsi_analysis") as run,patch.object(stock_analysis,"build_change_summary_text",return_value="ok"):
            text,images=stock_analysis.build_stock_analysis()
        self.assertEqual(text,"ok")
        self.assertEqual(run.call_count,len(RSI_ANALYSIS_CONFIGS))
        self.assertIn("output/dow_jones_analysis.png",images)

    def test_five_configurations_and_dow_image(self):
        mapping={c["kwargs"]["symbol"]:c for c in RSI_ANALYSIS_CONFIGS}
        for symbol in [".IXIC",".DJI","512890","560220","159943"]:
            self.assertIn(symbol,mapping)
            self.assertTrue(mapping[symbol]["kwargs"].get("breadth_key"))
        self.assertEqual(mapping[".DJI"]["image"],"output/dow_jones_analysis.png")
        self.assertNotIn("sh000001",mapping)
    def test_breadth_overlay_shares_rsi_axis_and_keeps_gaps(self):
        days=pd.date_range("2026-01-01",periods=60)
        frame=pd.DataFrame({"date":days,"close":np.arange(60)+100.,"volume":1000.,"RSI":50.})
        breadth=pd.DataFrame({"date":days,"percent":[60.]*58+[None,70.],"kind":["close"]*59+["intraday"]})
        with tempfile.TemporaryDirectory() as tmp,patch.object(rsi_data.plt,"close") as close:
            rsi_data.plot_analysis(frame,"TEST",output_file=str(Path(tmp)/"chart.png"),show_plot=False,breadth_df=breadth,show_breadth=True)
            fig=close.call_args.args[0];axis=fig.axes[2]
            line=next(x for x in axis.lines if x.get_label()=="50D")
            self.assertTrue(np.isnan(line.get_ydata()[-2]));self.assertEqual(axis.get_ylim(),(0.,100.))
            self.assertEqual(len(fig.axes),3)
            self.assertIn("R",[t.get_text() for t in axis.get_legend().get_texts()])
            self.assertIn("50D",[t.get_text() for t in axis.get_legend().get_texts()])
    def test_nasdaq_latest_breadth_label_has_no_parenthetical_suffix(self):
        from tools.market_breadth import draw_breadth
        import matplotlib.pyplot as plt
        fig, axis = plt.subplots()
        frame = pd.DataFrame({"date": ["2026-09-25"], "percent": [34.6],
                              "kind": ["close"], "source": ["stockcharts_NAA50R"]})
        draw_breadth(axis, frame)
        self.assertEqual([label.get_text() for label in axis.texts], ["50D: 34.6%"])
        plt.close(fig)
    def test_price_breadth_band_uses_configurable_extremes_and_keeps_gaps(self):
        from tools.market_breadth import draw_breadth_state_band
        from tools.configs.market_breadth_configs import BREADTH_BAND_LOW_THRESHOLD,BREADTH_BAND_HIGH_THRESHOLD
        import matplotlib.pyplot as plt
        dates=pd.date_range("2026-01-02",periods=6,freq="B")
        prices=pd.DataFrame({"date":dates,"close":[100.]*6})
        breadth=pd.DataFrame({"date":dates,"percent":[30,30,50,70,80,None],"kind":["close"]*6})
        fig,axis=plt.subplots();axis.set_ylim(90,110)
        patches=draw_breadth_state_band(axis,prices,breadth,30,70)
        self.assertEqual((BREADTH_BAND_LOW_THRESHOLD,BREADTH_BAND_HIGH_THRESHOLD),(30,70))
        self.assertEqual(len(patches),2)
        self.assertEqual([t.get_text() for t in axis.texts],["50D","50D"])
        self.assertEqual(axis.get_ylim(),(90,110))
        from tools.rsi_data import VIX_STATE_BAND_POSITIVE_BOTTOM,VIX_STATE_BAND_HEIGHT
        low_patch,high_patch=patches
        self.assertAlmostEqual(low_patch.get_y()-(VIX_STATE_BAND_POSITIVE_BOTTOM+VIX_STATE_BAND_HEIGHT),.005,places=3)
        self.assertGreater(high_patch.get_y(),.90)
        self.assertLess(high_patch.get_y()+high_patch.get_height(),1.0)
        self.assertLess(patches[-1].get_x()+patches[-1].get_width(),__import__('matplotlib').dates.date2num(dates[-1]))
        plt.close(fig)

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
        self.assertFalse(sync_repos.is_auto_merge_cache_path("cache/market_breadth_local/refresh.lock"))
        self.assertTrue(service_runner.is_blocked_path("cache/market_breadth_local/refresh.lock"))
        self.assertFalse(service_runner.is_blocked_path("cache/market_breadth/results/dow.json"))

if __name__=="__main__":unittest.main()
