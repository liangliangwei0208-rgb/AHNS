"""独立十年图与技术图共用原始数据，缓存及真实可用时间不变。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from tools import a_share_valuation as valuation
from strategy import gdp


class SharedMacroTests(unittest.TestCase):
    def raw(self):
        cap = pd.DataFrame({'数据日期': ['2026年08月'], '市价总值-上海': [700000.],
                            '市价总值-深圳': [450000.]})
        data = pd.DataFrame({'季度': ['2025年第1季度', '2025年第2季度', '2025年第3季度',
                                      '2025年第4季度', '2026年第1季度', '2026年第2季度'],
                             '国内生产总值-绝对值': [300000., 620000., 960000., 1300000., 340000., 700000.]})
        return {'cap': cap, 'gdp': data}

    def test_shared_loader_returns_raw_and_events_and_reuses_fresh_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls=[]
            def fetch(due): calls.append(due); return self.raw()
            cap, data, events = valuation.load_macro_data(tmp, now='2026-10-08T12:00+08:00', fetcher=fetch)
            self.assertEqual(len(calls), 1)
            self.assertEqual(cap.iloc[-1].market_cap_yi, 1150000.)
            self.assertEqual(data.iloc[-1].gdp_ttm_yi, 1380000.)
            again = valuation.load_valuation(tmp, now='2026-10-08T13:00+08:00', fetcher=fetch)
            pd.testing.assert_frame_equal(events, again)
            self.assertEqual(len(calls), 1)

    def test_standalone_uses_one_shared_load_and_never_akshare_or_raw_csv_write(self):
        cap = valuation.normalize_market_cap(self.raw()['cap'])
        data = valuation.normalize_gdp(self.raw()['gdp'])
        with patch.object(valuation, 'load_macro_data', return_value=(cap, data, pd.DataFrame())) as load, \
             patch.object(gdp.ak, 'macro_china_stock_market_cap', side_effect=AssertionError('duplicate fetch')), \
             patch.object(gdp.ak, 'macro_china_gdp', side_effect=AssertionError('duplicate fetch')), \
             patch.object(pd.DataFrame, 'to_csv', side_effect=AssertionError('duplicate raw cache')):
            result = gdp.build_market_cap_gdp_ratio()
        self.assertEqual(load.call_count, 1)
        self.assertAlmostEqual(result.iloc[-1].market_cap_to_gdp, 1150000/1380000)
        self.assertEqual(list(result.columns), ['date', 'sse_market_cap_yi', 'szse_market_cap_yi',
                                                'market_cap_yi', 'gdp_ttm_yi', 'market_cap_to_gdp'])

    def test_unchanged_statistical_period_does_not_create_new_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            valuation.load_macro_data(tmp, now='2026-10-08T12:00+08:00', fetcher=lambda due:self.raw())
            path=Path(tmp)/'a_share_mc_gdp.json'
            before=json.loads(path.read_text(encoding='utf-8'))
            valuation.load_macro_data(tmp, now='2026-10-13T12:00+08:00', fetcher=lambda due:self.raw())
            after=json.loads(path.read_text(encoding='utf-8'))
            for key in ('historical_cutoff','history','observations'):
                self.assertEqual(before[key],after[key])
            self.assertEqual(after['sources']['cap']['rows'][-1]['period_date'], '2026-08-31T00:00:00.000')
            self.assertNotEqual(before['sources']['cap']['last_success_at'],after['sources']['cap']['last_success_at'])

    def test_json_derived_rounding_cannot_change_standalone_threshold_state(self):
        raw = self.raw()
        raw['cap']['市价总值-上海'] = 6610.9639
        raw['cap']['市价总值-深圳'] = 262482.6161
        raw['gdp'] = raw['gdp'].iloc[:4].copy()
        raw['gdp']['国内生产总值-绝对值'] = [100000., 200000., 300000., 448489.3]
        cap = valuation.normalize_market_cap(raw['cap'])
        data = valuation.normalize_gdp(raw['gdp'])
        expected = (6610.9639+262482.6161)/448489.3
        # 与生产JSON完全相同的序列化精度；派生列会舍入，原始分量仍完整。
        restored = tuple(valuation._source_frame({'rows':valuation._records(frame)}) for frame in (cap, data))
        with patch.object(valuation, 'load_macro_data', return_value=(*restored, pd.DataFrame())):
            result = gdp.build_market_cap_gdp_ratio()
        self.assertEqual(result.iloc[-1].market_cap_to_gdp, expected)
        self.assertEqual(gdp._valuation_state(result.iloc[-1].market_cap_to_gdp), 'LOW')

    def test_missing_json_uses_legacy_csv_without_fetching_or_rewriting_it(self):
        raw = self.raw()
        prefix = pd.DataFrame({'季度':[f'2024年第{q}季度' for q in range(1,5)],
                               '国内生产总值-绝对值':[250000., 520000., 810000., 1120000.]})
        data = valuation.normalize_gdp(pd.concat([prefix, raw['gdp']], ignore_index=True))
        cap = valuation.normalize_market_cap(raw['cap'])
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp)/name for name in ('a_share_market_cap_monthly.csv','china_nominal_gdp_quarterly.csv')]
            cap.rename(columns={'period_date':'date'}).to_csv(paths[0], index=False)
            data.dropna(subset=['gdp_ttm_yi']).rename(columns={'period_date':'date'}).to_csv(paths[1], index=False)
            before = [p.read_bytes() for p in paths]
            loaded = valuation.load_macro_data(tmp, now='2026-10-08T12:00+08:00', refresh=False,
                                               fetcher=lambda due:self.fail('read-only legacy migration fetched'))
            self.assertTrue(all(not frame.empty for frame in loaded))
            self.assertEqual(loaded[1].gdp_ttm_yi.dropna().iloc[-1], 1380000.)
            self.assertEqual([p.read_bytes() for p in paths], before)
            self.assertFalse((Path(tmp)/'a_share_mc_gdp.json').exists())


if __name__ == '__main__':unittest.main()
