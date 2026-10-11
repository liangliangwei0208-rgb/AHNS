"""90%门槛、富途常规收盘价和跨主机合并回归；不联网。"""
import copy
import itertools
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from tools.breadth_engine import (market_clock, prepare_market_close_quotes,
                                  refresh_market, close_completion, chart_data)
from tools.breadth_sources import FutuBreadth, SourceHealth
from tools.configs.market_breadth_configs import BREADTH_MIN_COVERAGE
from tools.market_breadth import (BreadthStore, calculate_history,
                                  calculate_segmented_history, calculate_intraday, merge_document)


class CoverageAndCloseFallbackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = BreadthStore(Path(tmp.name)/'market_breadth')
        self.now = pd.Timestamp('2026-10-10 08:00', tz='America/New_York')
        self.clock = market_clock('US', self.now)
        self.dates = self.clock['sessions'][-60:]
        self.frame = pd.DataFrame({'date': self.dates, 'close': range(100, 160)})

    def quote(self, **changes):
        return dict(dict(price=110., prev_close=100., time='2026-10-09 20:02:00',
                         source='futu_snapshot', after_price=109.5, after_change_val=-.5), **changes)

    def prepare(self, quote, now=None, day='2026-10-09', previous='2026-10-08'):
        return prepare_market_close_quotes(self.store, ['US.A'], {'US.A': quote},
                                           now or self.now, day, previous, 'US')

    def test_shared_default_is_90_percent_for_history_segments_and_intraday(self):
        self.assertEqual(BREADTH_MIN_COVERAGE, .90)
        for key,market,prefix in (('dow','US','US.'),('dividend','CN','SH.')):
            dates = market_clock(market, self.now)['sessions'][-60:]
            symbols = [prefix+f'T{i}' for i in range(10)]
            frame = pd.DataFrame({'date':dates, 'close':range(100,160)})
            self.store.save_members(key, symbols, 'verified', dates[-1])
            for valid in (9, 8):
                with self.subTest(market=market, valid=valid):
                    prices = {c: frame for c in symbols[:valid]}
                    daily = calculate_history(prices, symbols, sessions=dates).iloc[-1]
                    segmented = calculate_segmented_history(prices, self.store, key,
                                [dates[-1]], calendar_sessions=dates).iloc[-1]
                    live = calculate_intraday(prices, symbols, dates[-1],
                           {c: 159. for c in symbols[:valid]}, sessions=dates)
                    for result in (daily, segmented, live):
                        self.assertEqual(result['valid'], valid)
                        self.assertEqual(result['coverage'], valid/10)
                        self.assertEqual(pd.notna(result['percent']), valid == 9)
                        if valid == 9: self.assertEqual(result['percent'], 100.)

    def test_90_percent_official_completion_for_us_and_cn(self):
        for key, market, prefix in (('dow', 'US', 'US.'), ('dividend', 'CN', 'SH.')):
            with self.subTest(market=market):
                clock = market_clock(market, self.now)
                dates = clock['sessions'][-60:]
                symbols = [prefix+f'T{i}' for i in range(10)]
                member = self.store.save_members(key, symbols, 'verified', dates[-1])
                for c in symbols[:9]:
                    self.store.save_prices(c, pd.DataFrame({'date': dates, 'close': range(100, 160)}), 'test')
                row = calculate_history({c:self.store.prices(c) for c in symbols}, symbols, sessions=dates).iloc[-1].to_dict()
                self.store.save_results(key, [row], member['version'])
                self.assertEqual(close_completion(self.store, key, clock)['status'], 'complete')
                self.assertIn('min_coverage_90pct', self.store.read('results', key)['calculation_policy'])

    def test_futu_adapter_preserves_raw_time_and_regular_price_fields(self):
        futu = FutuBreadth()
        futu.ctx = Mock()
        futu.ctx.get_market_snapshot.return_value = (0, pd.DataFrame([dict(
            code='US.A', last_price=110., prev_close_price=100., update_time='2026-10-09 20:02:00',
            after_price=109.5, after_change_val=-.5)]))
        q = futu.quotes(['US.A'])['US.A']
        self.assertEqual(q, self.quote())

    def test_postmarket_timestamp_can_validate_regular_close(self):
        self.store.save_prices('US.A', self.frame.iloc[:-1], 'test')
        value = self.prepare(self.quote())['US.A']
        self.assertAlmostEqual(value, 158.*1.1)
        # 原有收盘前最后五分钟路径保留，不依赖盘后字段。
        q = self.quote(time='2026-10-09 15:59:00')
        q.pop('after_price'); q.pop('after_change_val')
        self.assertAlmostEqual(self.prepare(q)['US.A'], value)

    def test_rejects_wrong_after_price_missing_fields_and_nonfinite_values(self):
        self.store.save_prices('US.A', self.frame.iloc[:-1], 'test')
        cases = [self.quote(price=109.5), self.quote(after_price=None), self.quote(after_change_val=None),
                 self.quote(after_price=float('nan')), self.quote(after_change_val=float('inf')),
                 self.quote(price=0), self.quote(prev_close=0), self.quote(source='unverified')]
        for q in cases:
            with self.subTest(quote=q): self.assertEqual(self.prepare(q), {})

    def test_rejects_old_future_unclosed_quotes_and_missing_prior_session(self):
        self.store.save_prices('US.A', self.frame.iloc[:-1], 'test')
        for stamp in ('2026-10-08 20:00', '2026-10-10 20:00', '2026-10-09 15:30'):
            with self.subTest(stamp=stamp): self.assertEqual(self.prepare(self.quote(time=stamp)), {})
        self.assertEqual(self.prepare(self.quote(time='2026-10-09 17:00'),
                         now=pd.Timestamp('2026-10-09 16:30', tz='America/New_York')), {})
        self.assertEqual(self.prepare(self.quote(time='2026-10-09 16:00'),
                         now=pd.Timestamp('2026-10-09 16:14', tz='America/New_York')), {})
        self.assertEqual(self.prepare(self.quote(), previous='2026-10-07'), {})

    def test_early_close_uses_exchange_schedule_and_15_minute_buffer(self):
        self.store.save_prices('US.A', pd.DataFrame([{'date':'2026-11-25','close':100.}]), 'test')
        q = self.quote(time='2026-11-27 13:20')
        self.assertAlmostEqual(self.prepare(q, pd.Timestamp('2026-11-27 13:30', tz='America/New_York'),
                               '2026-11-27', '2026-11-25')['US.A'], 110.)
        self.assertEqual(self.prepare(q, pd.Timestamp('2026-11-27 13:14', tz='America/New_York'),
                         '2026-11-27', '2026-11-25'), {})

    def docs(self, official_time, snapshot_time, official=None):
        base = dict(date='2026-10-09', kind='close', valid=100, total=101, coverage=100/101)
        a = dict(type='results', updated_at=official_time, rows=[dict(base, percent=official,
                 finality='official', updated_at=official_time)])
        b = dict(type='results', updated_at=snapshot_time, rows=[dict(base, percent=49.,
                 finality='snapshot_provisional', updated_at=snapshot_time)])
        return a, b

    def test_new_valid_snapshot_recovers_older_official_null_in_both_orders(self):
        # 使用不同UTC偏移，不能按时间字符串字典序判断先后。
        a, b = self.docs('2026-10-10T10:00:00+08:00', '2026-10-10T03:00:00Z')
        for left, right in ((a,b), (b,a)):
            self.assertEqual(merge_document(left, right)['rows'], b['rows'])

    def test_new_official_null_rejects_old_snapshot_and_ties_prefer_official(self):
        for official_time in ('2026-10-10T04:00:00Z', '2026-10-10T03:00:00Z'):
            a, b = self.docs(official_time, '2026-10-10T03:00:00Z')
            for left, right in ((a,b), (b,a)):
                self.assertEqual(merge_document(left, right)['rows'], a['rows'])

    def test_valid_official_always_protected_from_newer_snapshot(self):
        a,b = self.docs('2026-10-10T02:00:00Z', '2026-10-11T03:00:00Z', official=47.)
        for left,right in ((a,b), (b,a)):
            self.assertEqual(merge_document(left, right)['rows'], a['rows'])

    def test_three_document_null_snapshot_merge_is_order_independent_across_timezones(self):
        a,snapshot = self.docs('2026-10-10T10:00:00+08:00', '2026-10-10T02:30:00Z')
        b,_ = self.docs('2026-10-10T03:00:00Z', '2026-10-10T02:30:00Z')
        for left,middle,right in itertools.permutations((a,b,snapshot)):
            self.assertEqual(merge_document(merge_document(left,middle),right)['rows'], b['rows'])
            self.assertEqual(merge_document(left,merge_document(middle,right))['rows'], b['rows'])

    def test_save_results_recalculation_cannot_invent_fresh_snapshot_evidence(self):
        row = dict(date='2026-10-09',kind='close',percent=49.,valid=100,total=101,coverage=100/101)
        with patch('tools.market_breadth.utc_now', return_value='2026-10-10T01:00:00Z'):
            self.store.save_results('nasdaq100',[row], 'test', finality='snapshot_provisional')
        with patch('tools.market_breadth.utc_now', return_value='2026-10-10T02:00:00Z'):
            self.store.save_results('nasdaq100',[dict(row,percent=None)], 'test')
        with patch('tools.market_breadth.utc_now', return_value='2026-10-10T03:00:00Z'):
            self.store.save_results('nasdaq100',[row], 'test', finality='snapshot_provisional')
        self.assertIsNone(self.store.read('results','nasdaq100')['rows'][0]['percent'])
        with patch('tools.market_breadth.utc_now', return_value='2026-10-10T04:00:00Z'):
            self.store.save_results('nasdaq100',[dict(row,observed_at='2026-10-10T04:00:00Z')],
                                    'test',finality='snapshot_provisional')
        self.assertEqual(self.store.results('nasdaq100').iloc[-1].percent,49.)

    def test_refresh_preserves_official_rejection_until_new_price_evidence(self):
        member = self.store.save_members('dow', ['US.A'], 'verified', '2026-10-09')
        self.store.save_prices('US.A',self.frame,'test',updated_at='2026-10-10T01:00:00Z',provisional=True)
        row = dict(date='2026-10-09',kind='close',percent=100.,valid=1,total=1,coverage=1.)
        with patch('tools.market_breadth.utc_now', return_value='2026-10-10T01:00:00Z'):
            self.store.save_results('dow',[row],member['version'],finality='snapshot_provisional')
        with patch('tools.market_breadth.utc_now', return_value='2026-10-10T02:00:00Z'):
            self.store.save_results('dow',[dict(row,percent=None)],member['version'])
        health = SourceHealth()
        health.unreachable.update(['yahoo_adjclose','sina_us_qfq','eastmoney_us_qfq'])
        def refresh():
            with patch('tools.market_breadth.utc_now', return_value='2026-10-10T04:00:00Z'):
                return refresh_market(self.store,'dow',self.now,refresh_members=False,use_futu=False,
                       source_health=health,deadline=time.monotonic()+30)
        refresh()
        self.assertIsNone(self.store.read('results','dow')['rows'][0]['percent'])
        # 新行情证据到达才允许恢复；重复计算本身不能更新证据时间。
        self.store.save_prices('US.A',self.frame,'test',updated_at='2026-10-10T03:00:00Z',provisional=True)
        refresh()
        latest=self.store.results('dow').iloc[-1]
        self.assertEqual(latest.percent,100.)
        self.assertEqual(pd.Timestamp(latest.observed_at),pd.Timestamp('2026-10-10T03:00:00Z'))

    def test_stale_unchanged_daily_response_does_not_refresh_provisional_evidence(self):
        member=self.store.save_members('dow',['US.A'],'verified','2026-10-09')
        self.store.save_prices('US.A',self.frame.iloc[:-1],'test',updated_at='2026-10-10T01:00:00Z')
        self.store.save_prices('US.A',self.frame.iloc[-1:],'test',updated_at='2026-10-10T01:00:00Z',provisional=True)
        row=dict(date='2026-10-09',kind='close',percent=None,valid=1,total=1,coverage=1.)
        with patch('tools.market_breadth.utc_now',return_value='2026-10-10T02:00:00Z'):
            self.store.save_results('dow',[row],member['version'])
        price_doc=self.store.read('prices','US.A')
        with patch('tools.market_breadth.utc_now',return_value='2026-10-10T04:00:00Z'), \
             patch('tools.breadth_engine.fetch_prices',return_value=(self.frame.iloc[:-1],'test')):
            refresh_market(self.store,'dow',self.now,refresh_members=False,use_futu=False,deadline=time.monotonic()+30)
        self.assertEqual(self.store.read('prices','US.A'),price_doc)
        self.assertIsNone(self.store.read('results','dow')['rows'][0]['percent'])

    def test_recalculated_old_snapshot_does_not_replace_newer_snapshot_evidence(self):
        _,a=self.docs('2026-10-10T01:00:00Z','2026-10-10T05:00:00Z')
        a['rows'][0].update(observed_at='2026-10-10T01:00:00Z',percent=48.)
        _,b=self.docs('2026-10-10T01:00:00Z','2026-10-10T04:00:00Z')
        b['rows'][0]['observed_at']='2026-10-10T04:00:00Z'
        for left,right in ((a,b),(b,a)):
            self.assertEqual(merge_document(left,right)['rows'],b['rows'])

    def test_future_price_file_update_cannot_refresh_older_session_evidence(self):
        member=self.store.save_members('dow',['US.A'],'verified','2026-10-09')
        self.store.save_prices('US.A',self.frame.iloc[:-1],'test',updated_at='2026-10-10T01:00:00Z')
        self.store.save_prices('US.A',self.frame.iloc[-1:],'test',updated_at='2026-10-10T01:00:00Z',provisional=True)
        with patch('tools.market_breadth.utc_now',return_value='2026-10-10T02:00:00Z'):
            self.store.save_results('dow',[dict(date='2026-10-09',kind='close',percent=None,
                valid=1,total=1,coverage=1.)],member['version'])
        self.store.save_prices('US.A',pd.DataFrame([dict(date='2026-10-12',close=1000.)]),
                               'test',updated_at='2026-10-12T20:30:00Z')
        health=SourceHealth();health.unreachable.update(['yahoo_adjclose','sina_us_qfq','eastmoney_us_qfq'])
        refresh_market(self.store,'dow',self.now,refresh_members=False,use_futu=False,
                       source_health=health,deadline=time.monotonic()+30)
        self.assertIsNone(self.store.read('results','dow')['rows'][0]['percent'])

    def test_replay_88_of_101_snapshot_restores_current_value_idempotently_then_official(self):
        symbols = [f'US.T{i:03d}' for i in range(101)]
        member = self.store.save_members('nasdaq100', symbols, 'verified', '2026-10-08')
        frame = self.frame.assign(close=100.)
        for i,c in enumerate(symbols):
            if i < 88:
                f = frame.copy(); f.loc[f.index[-1], 'close'] = 110. if i < 44 else 90.
            else: f = frame.iloc[:(-2 if i == 100 else -1)]
            self.store.save_prices(c, f, 'test')
        self.store.save_results('nasdaq100', [dict(date='2026-10-08', kind='close', percent=47.,
                               valid=100, total=101, coverage=100/101)], member['version'])
        protected = copy.deepcopy(self.store.read('results', 'nasdaq100')['rows'][0])
        with patch('tools.market_breadth.utc_now', return_value='2026-10-10T06:38:20Z'):
            self.store.save_results('nasdaq100', [dict(date='2026-10-09', kind='close', percent=None,
                                   valid=88, total=101, coverage=88/101)], member['version'])
        futu = Mock(quote_errors=[], history_calls=0, connection_status='connected',
                    quota_checked_at='2026-10-11', remaining=10, used=set())
        futu.quotes.side_effect = lambda codes: {c:self.quote(price=110. if symbols.index(c)<93 else 90.,
            after_price=109.5 if symbols.index(c)<93 else 89.5) for c in codes}
        futu.history.side_effect = RuntimeError('Futu quota exhausted: reserve 10')
        health = SourceHealth()
        health.unreachable.update(['yahoo_adjclose','sina_us_qfq','eastmoney_us_qfq'])
        for _ in range(2):
            report = refresh_market(self.store, 'nasdaq100', self.now, refresh_members=False,
                     deadline=time.monotonic()+60, futu=futu, source_health=health)
            row = self.store.results('nasdaq100').iloc[-1]
            self.assertEqual((row.date, row.valid, row.total, row.percent), ('2026-10-09',100,101,49.))
            self.assertEqual(row.finality, 'snapshot_provisional')
            self.assertEqual(report['latest_usable_close_date'], '2026-10-09')
            self.assertEqual(report['latest_usable_close_finality'], 'snapshot_provisional')
            self.assertEqual(report['min_coverage'], .90)
            self.assertEqual(report['status'], 'stale')  # 临时收盘不冒称正式核验完成。
        self.assertEqual(len(self.store.results('nasdaq100')), 2)
        self.assertEqual(self.store.read('results','nasdaq100')['rows'][0], protected)
        with patch('tools.breadth_engine.market_clock', return_value=self.clock):
            self.assertTrue(chart_data('nasdaq100', pd.to_datetime(self.dates),
                            root=self.store.root).attrs['breadth_display']['current'])
        for i,c in enumerate(symbols[88:100], 88):
            f = frame.copy(); f.loc[f.index[-1], 'close'] = 110. if i<93 else 90.
            self.store.save_prices(c, f, 'test')
        report = refresh_market(self.store, 'nasdaq100', self.now, refresh_members=False,
                 use_futu=False, deadline=time.monotonic()+60, source_health=health)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(self.store.results('nasdaq100').iloc[-1].finality, 'official')
        self.assertEqual(self.store.read('results','nasdaq100')['rows'][0], protected)


if __name__ == '__main__': unittest.main()
