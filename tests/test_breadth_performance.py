"""广度本轮复用与独立完成验收；所有数据均在隔离缓存中。"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from tools.market_breadth import BreadthStore, calculate_history, calculate_segmented_history
from tools.breadth_engine import market_clock, price_download_plan, close_completion, refresh_market

NOW = pd.Timestamp('2026-10-08T08:00:00-04:00')


class BreadthPerformanceTests(unittest.TestCase):
    def fixture(self, count=30):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = BreadthStore(Path(tmp.name)/'breadth')
        clock = market_clock('US', NOW)
        dates = clock['sessions'][-65:]
        codes = [f'US.T{i:03d}' for i in range(count)]
        member = store.save_members('dow', codes, 'verified', '2026-09-29')
        frame = pd.DataFrame({'date': dates, 'close': np.arange(65, dtype=float)+100})
        for code in codes:
            store.save_prices(code, frame, 'sina_us_qfq')
        rows = calculate_history({c: frame for c in codes}, codes, sessions=dates)
        store.save_results('dow', rows.loc[rows.date >= '2026-09-29'].to_dict('records'), member['version'])
        return store, clock, member, frame

    def test_planning_and_completion_parse_each_price_file_once(self):
        store, clock, member, _ = self.fixture()
        reads = []
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path.parent.name == 'prices': reads.append(path)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read), store.read_scope():
            price_download_plan(store, 'dow', member, clock['day'], clock['complete_day'], False, False, True)
            result = close_completion(store, 'dow', clock)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(reads), len(member['symbols']))

    def test_scope_invalidates_after_write_external_change_and_exit(self):
        store, _, member, frame = self.fixture(1)
        code = member['symbols'][0]
        with store.read_scope():
            self.assertEqual(store.prices(code).iloc[-1].close, 164)
            store.save_prices(code, frame.assign(close=frame.close*2), 'sina_us_qfq')
            self.assertEqual(store.prices(code).iloc[-1].close, 328)
            path = store.path('prices', code)
            doc = json.loads(path.read_text(encoding='utf-8'))
            doc['rows'][-1]['close'] = 329
            path.write_text(json.dumps(doc), encoding='utf-8')
            self.assertEqual(store.prices(code).iloc[-1].close, 329)
        self.assertFalse(store._price_cache)

    def test_complete_no_maintenance_skips_sma_and_network(self):
        store, _, _, _ = self.fixture()
        with patch('tools.breadth_engine.fetch_prices', side_effect=AssertionError('network')), \
             patch('tools.breadth_engine.calculate_history', side_effect=AssertionError('duplicate SMA')), \
             patch('tools.breadth_engine.calculate_segmented_history', side_effect=AssertionError('unnecessary history')):
            result = refresh_market(store, 'dow', NOW, refresh_members=False, use_futu=False,
                                    deadline=time.monotonic()+30)
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(result['maintenance_complete'])

    def test_completion_rejects_stale_calculation_after_price_change(self):
        store, clock, member, frame = self.fixture(1)
        with store.read_scope():
            calculation = {'day': clock['complete_day'], 'version': member['version'],
                           'prices_token': store.price_token(member['symbols']),
                           'row': {'valid': 1, 'coverage': 1}}
            store.save_prices(member['symbols'][0], frame.iloc[:-1], 'different_basis')
            result = close_completion(store, 'dow', clock, calculation=calculation)
        self.assertEqual(result['valid_count'], 0)
        self.assertEqual(result['missing_count'], 1)

    def test_member_versions_share_one_sma_pass_and_keep_exact_results(self):
        store, clock, member, frame = self.fixture(2)
        code = member['symbols'][0]
        store.save_members('dow', [code], 'verified', '2026-10-06')
        dates = clock['sessions'][-65:]
        prices = {c: frame for c in member['symbols']}
        requested = [day for day in dates[-8:] if day >= '2026-09-29']
        expected = []
        for day in requested:
            current = store.members('dow', day)
            row = calculate_history(prices, current['symbols'], sessions=dates).set_index('date').loc[day]
            expected.append(row.to_dict())
        with patch('pandas.core.window.rolling.Rolling.mean', autospec=True,
                   side_effect=pd.core.window.rolling.Rolling.mean) as means:
            result = calculate_segmented_history(prices, store, 'dow', requested, calendar_sessions=dates)
        self.assertEqual(means.call_count, 1)
        for actual, old in zip(result.to_dict('records'), expected):
            for field in ('percent', 'valid', 'total', 'coverage', 'kind'):
                self.assertEqual(actual[field], old[field])

    def test_member_change_invalidates_completion_proof(self):
        store, clock, member, _ = self.fixture(2)
        proof = {'day': clock['complete_day'], 'version': member['version'],
                 'prices_token': store.price_token(member['symbols']),
                 'row': {'valid': 2, 'coverage': 1}}
        store.save_members('dow', member['symbols'][:1], 'verified', clock['complete_day'])
        result = close_completion(store, 'dow', clock, calculation=proof)
        self.assertEqual(result['valid_count'], 1)
        self.assertEqual(result['status'], 'stale')

    def test_snapshot_cannot_certify_data_changed_after_read(self):
        store, clock, member, frame = self.fixture(1)
        with store.read_scope():
            prices, token = store.price_snapshot(member['symbols'])
            path = store.path('prices', member['symbols'][0])
            document = json.loads(path.read_text(encoding='utf-8'))
            document['rows'] = document['rows'][:-1]
            path.write_text(json.dumps(document), encoding='utf-8')
            # 老表仍可计算，但其文件证据不能再用于当前完成验收。
            self.assertEqual(len(prices[member['symbols'][0]]), len(frame))
            result = close_completion(store, 'dow', clock, calculation={
                'day': clock['complete_day'], 'version': member['version'],
                'prices_token': token, 'row': {'valid': 1, 'coverage': 1}})
        self.assertEqual(result['valid_count'], 0)

    def test_private_scope_does_not_change_public_document_mutability(self):
        store, _, member, _ = self.fixture(1)
        code = member['symbols'][0]
        with store.read_scope():
            document = store.read('prices', code)
            document['rows'][0]['close'] = -1
            document['basis'] = 'changed by caller'
            self.assertGreater(store.prices(code).iloc[0].close, 0)
            self.assertEqual(store.price_metadata(code)['basis'], 'sina_us_qfq')

    def test_successful_future_member_prewarm_finishes_maintenance(self):
        store, clock, member, frame = self.fixture(1)
        store.save_members('dow', member['symbols']+['US.FUTURE'], 'verified', clock['day'],
                           effective_date='2026-10-09')
        with patch('tools.breadth_engine.fetch_prices', return_value=(frame, 'sina_us_qfq')):
            result = refresh_market(store, 'dow', NOW, refresh_members=False, use_futu=False,
                                    deadline=time.monotonic()+30)
        self.assertEqual(result['prewarm']['missing'], 0)
        self.assertEqual(result['remaining_missing_symbols'], [])
        self.assertTrue(result['maintenance_complete'])


if __name__ == '__main__':
    unittest.main()
