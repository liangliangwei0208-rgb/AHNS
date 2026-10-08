"""公共显示组件保持原有文本、基准表列宽和色带几何。"""
import unittest
from unittest.mock import Mock

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from tools import console_display as console
from tools import get_top10_holdings as holdings
from tools import fund_history_io as history
from tools import premarket_estimator as premarket
from tools import futu_night_observation as night
from tools import market_breadth as breadth


class SharedDrawingTests(unittest.TestCase):
    def tearDown(self):plt.close('all')

    def test_progress_aliases_keep_missing_and_error_behavior(self):
        for module in (holdings,premarket,night):
            self.assertIs(module._progress_status,console.progress_status)
            self.assertIs(module._format_progress_return_pct,console.format_progress_return_pct)
            self.assertEqual(module._format_progress_return_pct(None),'无涨跌幅')
            self.assertEqual(module._format_progress_return_pct(np.nan),'无涨跌幅')
            self.assertEqual(module._format_progress_return_pct('bad'),'无涨跌幅')
            self.assertEqual(module._format_progress_return_pct(-1.25),'-1.2500%')
        broken=Mock();broken.set_status.side_effect=RuntimeError('optional display')
        console.progress_status(broken,'text')
        console.progress_status(None,'text')

    def table(self,caller,frame,values):
        fig,ax=plt.subplots()
        return caller(ax,frame,values,[0,0,1,1],fontsize=8,header_bg='#eeeeee',
                      header_text_color='black',grid_color='gray',up_color='red',
                      down_color='green',neutral_color='gray')

    def test_benchmark_table_daily_and_cumulative_styles_are_preserved(self):
        from tools.benchmark_table import draw_benchmark_table
        self.assertIs(holdings._draw_benchmark_table,draw_benchmark_table)
        daily=pd.DataFrame({'序号':[1,2],'指数名称':['NDX','VIX'],
                            '模型观察':['+1.00%','20.00'],'基准日或区间':['date','date']})
        table=self.table(holdings._draw_benchmark_table,daily,[1.,{'value_type':'level','value':20}])
        self.assertEqual(table[1,2].get_text().get_color(),'red')
        self.assertEqual(table[2,2].get_text().get_color(),'gray')
        self.assertEqual(table[0,1].get_width(),.34)
        cumulative=pd.DataFrame({'指数代码':['NDX'],'区间模型观察':['-1%'],
                                  '有效估值日数':[4],'起始估值日':['start'],'结束估值日':['end']})
        table=self.table(history._draw_benchmark_table,cumulative,[-1.])
        self.assertEqual(table[1,1].get_text().get_color(),'green')
        self.assertEqual([table[0,i].get_width() for i in range(5)],[.10,.18,.12,.13,.13])

    def test_geometry_preserves_single_date_midpoints_clip_and_price_limits(self):
        left,right=breadth.state_band_edges(pd.to_datetime(['2026-01-02']))
        self.assertEqual(float(right[0]-left[0]),1.)
        left,right=breadth.state_band_edges(pd.to_datetime(['2026-01-02','2026-01-05']))
        np.testing.assert_equal(right[:-1],left[1:])
        fig,ax=plt.subplots();ax.plot([1,2],[100,200]);before=(ax.get_xlim(),ax.get_ylim())
        patch=breadth.add_price_state_band(ax,left[0],right[-1],.1,.05,'red',gid='test-band')
        self.assertEqual(before,(ax.get_xlim(),ax.get_ylim()))
        self.assertTrue(patch.get_clip_on())
        self.assertEqual(patch.get_gid(),'test-band')


if __name__=='__main__':unittest.main()
