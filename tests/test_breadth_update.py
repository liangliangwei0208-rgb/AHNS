"""美股增量修复：隔离缓存，不访问真实行情接口。"""
import io
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch, Mock

import pandas as pd
import market_breadth
from tools.breadth_engine import market_clock, refresh_market, price_download_plan
from tools.breadth_sources import SourceHealth, FutuBreadth, fetch_prices, fetch_sina_us_prices
from tools.market_breadth import BreadthStore

NOW = pd.Timestamp('2026-10-08 08:00', tz='America/New_York')


class FakeFutu:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []
        self.quote_errors = []
    def quotes(self, codes): return {}
    def history(self, code, day, **kwargs):
        self.calls.append((code, kwargs))
        return self.frame, 'sina_us_qfq'
    def close(self): pass


class UpdateTests(unittest.TestCase):
    def fixture(self, count=30, key='dow'):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = BreadthStore(Path(tmp.name)/'breadth')
        dates = market_clock('US', NOW)['sessions'][-65:]
        frame = pd.DataFrame({'date': dates, 'close': range(100, 165)})
        symbols = [f'US.T{i:03d}' for i in range(count)]
        member = store.save_members(key, symbols, 'verified', '2026-09-29')
        for code in symbols:
            store.save_prices(code, frame.iloc[:-2], 'sina_us_qfq')
        store.save_results(key, [dict(date='2026-10-05', percent=50., valid=count,
                                     total=count, coverage=1., kind='close')], member['version'])
        return store, frame, symbols

    def run_update(self, store, key='dow', **kwargs):
        return refresh_market(store, key, NOW, refresh_members=False,
                              deadline=time.monotonic()+60, **kwargs)

    def test_all_unsubmitted_symbols_reach_futu_after_circuit_break(self):
        store, frame, symbols = self.fixture()
        health = SourceHealth()
        health.unreachable.update(['yahoo_adjclose','sina_us_qfq','eastmoney_us_qfq'])
        futu = FakeFutu(frame)
        report = self.run_update(store, futu=futu, source_health=health)
        self.assertEqual({c for c,_ in futu.calls}, set(symbols))
        self.assertEqual(report['futu_history_success_count'], 30)
        self.assertEqual(report['status'], 'complete')

    def test_stale_ordinary_response_is_not_success_and_falls_back(self):
        store, frame, symbols = self.fixture()
        futu = FakeFutu(frame)
        with patch('tools.breadth_engine.fetch_prices', return_value=(frame.iloc[:-2], 'sina_us_qfq')):
            report = self.run_update(store, futu=futu)
        self.assertEqual(len(futu.calls), 30)
        self.assertEqual(report['ordinary_source_success_count'], 0)
        self.assertEqual(report['status'], 'complete')

    def test_normal_101_and_30_incremental_updates_preserve_published(self):
        for key,count in [('nasdaq100',101),('dow',30)]:
            with self.subTest(key=key):
                store, frame, symbols = self.fixture(count,key)
                before = next(r for r in store.read('results',key)['rows'] if r['date']=='2026-10-05')
                with patch('tools.breadth_engine.fetch_prices',return_value=(frame,'sina_us_qfq')):
                    report = self.run_update(store,key,use_futu=False)
                self.assertEqual(report['status'],'complete')
                self.assertEqual(report['ordinary_source_success_count'],count)
                self.assertEqual(report['coverage'],1.)
                self.assertEqual(next(r for r in store.read('results',key)['rows'] if r['date']=='2026-10-05'),before)
                self.assertEqual(store.results(key).iloc[-1].date,'2026-10-07')

    def test_internal_gap_is_queued_even_when_latest_date_is_current(self):
        store, frame, symbols = self.fixture()
        for code in symbols: store.save_prices(code,frame,'sina_us_qfq')
        doc=store.read('prices',symbols[0])
        doc['rows']=[r for r in doc['rows'] if r['date']!='2026-10-06']
        store.write('prices',symbols[0],doc)
        with patch('tools.breadth_engine.fetch_prices',return_value=(frame,'sina_us_qfq')) as fetch:
            report=self.run_update(store,use_futu=False)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(report['status'],'complete')

    def test_stale_result_and_insufficient_coverage_cannot_complete(self):
        store, frame, symbols = self.fixture()
        with patch('tools.breadth_engine.fetch_prices',side_effect=RuntimeError('Source timeout')):
            report=self.run_update(store,use_futu=False)
        self.assertEqual(report['status'],'stale')
        self.assertEqual(report['latest_valid_close_date'],'2026-10-05')
        self.assertEqual(report['missing_sessions'],2)
        self.assertEqual(report['coverage'],0.)

    def test_cli_rejects_obsolete_nonnull_percent(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        report={'latest':{'percent':50.,'date':'2026-10-05'},'status':'stale','errors':[]}
        with patch('tools.breadth_engine.refresh_market',return_value=report), redirect_stdout(io.StringIO()):
            rc=market_breadth.main(['--update','--market','dow','--worker','--no-futu',
                                  '--cache-root',tmp.name,'--report-path',str(Path(tmp.name)/'report.json')])
        self.assertNotEqual(rc,0)

    def test_95_percent_boundary_and_restart_only_requests_remaining(self):
        store, frame, symbols = self.fixture(20)
        for code in symbols[:19]:store.save_prices(code,frame,'sina_us_qfq')
        with patch('tools.breadth_engine.fetch_prices',side_effect=RuntimeError('offline')):
            report=self.run_update(store,use_futu=False)
        self.assertEqual(report['coverage'],.95)
        self.assertEqual(report['status'],'complete')
        with patch('tools.breadth_engine.fetch_prices',return_value=(frame,'sina_us_qfq')) as fetch:
            self.run_update(store,use_futu=False)
        self.assertEqual([c.args[0] for c in fetch.call_args_list],symbols[19:])
        store, frame, symbols = self.fixture(20)
        for code in symbols[:18]:store.save_prices(code,frame,'sina_us_qfq')
        with patch('tools.breadth_engine.fetch_prices',side_effect=RuntimeError('offline')):
            report=self.run_update(store,use_futu=False)
        self.assertEqual(report['coverage'],.90)
        self.assertEqual(report['status'],'stale')

    def test_partial_response_saves_progress_without_reporting_success(self):
        store, frame, symbols=self.fixture(1)
        with patch('tools.breadth_engine.fetch_prices',return_value=(frame.iloc[:-1],'sina_us_qfq')):
            report=self.run_update(store,use_futu=False)
        self.assertEqual(store.prices(symbols[0]).iloc[-1].date,'2026-10-06')
        self.assertEqual(report['ordinary_source_success_count'],0)
        self.assertEqual(report['remaining_missing_symbols'],symbols)

    def test_provisional_close_is_never_official_completion(self):
        store,frame,symbols=self.fixture(1)
        store.save_prices(symbols[0],frame,'sina_us_qfq',provisional=True)
        with patch('tools.breadth_engine.fetch_prices',side_effect=RuntimeError('offline')):
            report=self.run_update(store,use_futu=False)
        self.assertEqual(report['status'],'stale')
        self.assertEqual(report['latest_valid_close_date'],'2026-10-05')

    def test_opend_unavailable_is_explicit_and_not_reconnected_for_every_symbol(self):
        futu=FutuBreadth()
        with patch('tools.breadth_sources.socket.create_connection',side_effect=ConnectionRefusedError('refused')) as connect:
            for _ in range(2):
                with self.assertRaisesRegex(ConnectionError,'OpenD not running'):futu.connect()
        self.assertEqual(connect.call_count,1)
        self.assertEqual(futu.connection_status,'OpenD not running')

    def test_futu_quota_is_lazy_and_reserve_still_allows_used_symbol(self):
        frame=pd.DataFrame({'time_key':['2026-10-06','2026-10-07'],'close':[100.,101.]})
        ctx=Mock()
        ctx.get_history_kl_quota.return_value=(0,(0,12,[]))
        ctx.request_history_kline.return_value=(0,frame,None)
        futu=FutuBreadth()
        with patch.object(futu,'connect',return_value=ctx),patch.object(futu,'throttle'):
            futu.history('US.A','2026-10-07',start='2026-10-01')
            futu.history('US.B','2026-10-07')
            with self.assertRaisesRegex(RuntimeError,'quota exhausted'):futu.history('US.C','2026-10-07')
            futu.history('US.A','2026-10-07')
        self.assertEqual(ctx.get_history_kl_quota.call_count,1)
        self.assertEqual(futu.remaining,10)
        self.assertEqual(ctx.request_history_kline.call_args_list[0].kwargs['start'],'2026-10-01')

    def test_futu_permission_and_budget_failures_are_distinct(self):
        futu=FutuBreadth();futu.quota_checked_at='checked';futu.remaining=50
        ctx=Mock();ctx.request_history_kline.return_value=(-1,'No permission for US history',None)
        with patch.object(futu,'connect',return_value=ctx),patch.object(futu,'throttle'):
            with self.assertRaisesRegex(RuntimeError,'Futu permission denied'):futu.history('US.A','2026-10-07')
            with self.assertRaisesRegex(TimeoutError,'Runtime budget exhausted'):
                futu.history('US.A','2026-10-07',deadline=time.monotonic()+1)

    def test_stale_source_tries_next_without_circuit_breaking(self):
        stale=pd.DataFrame({'date':['2026-10-05'],'close':[100.]})
        current=pd.DataFrame({'date':['2026-10-07'],'close':[101.]})
        health=SourceHealth()
        with patch('tools.breadth_sources.fetch_yahoo_prices',return_value=(stale,'yahoo_adjclose')), \
             patch('tools.breadth_sources.fetch_sina_us_prices',return_value=(current,'sina_us_qfq')):
            result=fetch_prices('US.A','2026-10-07',health=health)
        self.assertEqual(result[1],'sina_us_qfq');self.assertFalse(health.unreachable)

    def test_sina_timeout_terminates_its_request_and_is_classified(self):
        import subprocess,requests
        with patch('tools.breadth_sources.subprocess.run',side_effect=subprocess.TimeoutExpired('child',1)) as run:
            with self.assertRaisesRegex(requests.Timeout,'process terminated'):
                fetch_sina_us_prices('US.A','2026-10-07',deadline=time.monotonic()+2)
        self.assertLessEqual(run.call_args.kwargs['timeout'],2)

    def test_progress_callback_retains_unfinished_tasks_on_budget_exhaustion(self):
        store,frame,symbols=self.fixture()
        progress=[]
        with patch('tools.breadth_engine.fetch_prices') as fetch:
            report=refresh_market(store,'dow',NOW,refresh_members=False,use_futu=False,
                                  deadline=time.monotonic()-1,progress_callback=progress.append)
        fetch.assert_not_called()
        self.assertEqual(report['remaining_missing_symbols'],symbols)
        self.assertTrue(progress);self.assertIn('Runtime budget exhausted',report['errors'])

    def test_complete_local_cache_does_not_connect_or_fetch_prices(self):
        store,frame,symbols=self.fixture(1)
        store.save_prices(symbols[0],frame,'sina_us_qfq')
        futu=FutuBreadth()
        with patch('tools.breadth_engine.fetch_prices') as fetch,patch.object(futu,'connect') as connect:
            report=self.run_update(store,futu=futu)
        fetch.assert_not_called();connect.assert_not_called()
        self.assertEqual(report['status'],'complete')

    def test_changed_adjustment_requires_full_futu_window(self):
        store,frame,symbols=self.fixture(1)
        futu=FakeFutu(frame)
        short=frame.tail(2).copy();full=frame.copy();full['close']*=2
        with patch('tools.breadth_engine.fetch_prices',side_effect=RuntimeError('offline')), \
             patch.object(futu,'history',side_effect=[(short,'futu_qfq'),(full,'futu_qfq')]) as history:
            report=self.run_update(store,futu=futu)
        self.assertEqual(history.call_count,2)
        self.assertNotIn('start',history.call_args.kwargs)
        self.assertEqual(store.read('prices',symbols[0])['basis'],'futu_qfq')
        self.assertEqual(report['status'],'complete')

    def test_older_futu_basis_does_not_discard_newer_partial_progress(self):
        store,frame,symbols=self.fixture(1)
        futu=FakeFutu(frame)
        with patch('tools.breadth_engine.fetch_prices',return_value=(frame.iloc[:-1],'yahoo_adjclose')), \
             patch.object(futu,'history',return_value=(frame.iloc[:-2],'futu_qfq')):
            self.run_update(store,futu=futu)
        self.assertEqual(store.prices(symbols[0]).iloc[-1].date,'2026-10-06')
        self.assertEqual(store.read('prices',symbols[0])['basis'],'yahoo_adjclose')

    def test_futu_rechecks_budget_after_slow_initial_quota_query(self):
        futu=FutuBreadth();ctx=Mock()
        ctx.get_history_kl_quota.return_value=(0,(0,50,[]))
        ctx.request_history_kline.return_value=(0,pd.DataFrame({'time_key':['2026-10-07'],'close':[100.]}),None)
        with patch.object(futu,'connect',return_value=ctx),patch.object(futu,'throttle'), \
             patch('tools.breadth_sources.time.monotonic',side_effect=[0,20]):
            with self.assertRaisesRegex(TimeoutError,'Runtime budget exhausted'):
                futu.history('US.A','2026-10-07',deadline=30)
        ctx.request_history_kline.assert_not_called()

    def test_long_tail_fetch_starts_at_last_cached_session(self):
        store,frame,symbols=self.fixture(1)
        dates=market_clock('US',NOW)['sessions'][-200:]
        all_prices=pd.DataFrame({'date':dates,'close':range(100,300)})
        store.write('prices',symbols[0],{})
        store.save_prices(symbols[0],all_prices.iloc[:80],'sina_us_qfq')
        def fetch(code,day,start,*args,**kwargs):
            return all_prices.loc[all_prices.date>=start],'sina_us_qfq'
        with patch('tools.breadth_engine.fetch_prices',side_effect=fetch):
            self.run_update(store,use_futu=False)
        self.assertEqual(list(store.prices(symbols[0]).date),dates)

    def test_initialized_us_market_queues_all_empty_price_shards(self):
        store,frame,symbols=self.fixture()
        for code in symbols:store.write('prices',code,{})
        plan=price_download_plan(store,'dow',store.members('dow'),'2026-10-08','2026-10-07',False,False,True)
        self.assertEqual(set(plan['pending']),set(symbols))

    def test_yahoo_and_eastmoney_have_terminable_wall_clock_boundary(self):
        import subprocess,requests
        from tools.breadth_sources import fetch_yahoo_prices,fetch_eastmoney_us_prices
        for fetch in [fetch_yahoo_prices,fetch_eastmoney_us_prices]:
            with self.subTest(source=fetch.__name__), \
                 patch('tools.breadth_sources.subprocess.run',side_effect=subprocess.TimeoutExpired('child',1)), \
                 patch('tools.breadth_sources.get',side_effect=AssertionError('unbounded HTTP')), \
                 patch('tools.breadth_sources.requests.Session',side_effect=AssertionError('unbounded HTTP')):
                with self.assertRaisesRegex(requests.Timeout,'process terminated'):
                    fetch('US.A','2026-10-07',deadline=time.monotonic()+2)

    def test_each_futu_checkpoint_contains_connection_and_quota_evidence(self):
        store,frame,symbols=self.fixture(1)
        futu=FakeFutu(frame);futu.connection_status='connected';futu.quota_checked_at='checked';futu.remaining=90;futu.used=set();futu.history_calls=0
        progress=[]
        with patch('tools.breadth_engine.fetch_prices',side_effect=RuntimeError('offline')):
            self.run_update(store,futu=futu,progress_callback=progress.append)
        batch=next(r for r in progress if r['futu_history_success_count']==1)
        self.assertEqual(batch['futu_connection_status'],'connected')
        self.assertEqual(batch['futu_quota_remaining'],90)
        self.assertIn('futu_history_request_count',batch)

    def test_chart_wrapper_reserves_time_for_cli_parent_to_stop_worker(self):
        from tools.breadth_engine import refresh_for_charts
        result=Mock(returncode=0,stdout='',stderr='')
        with patch('tools.breadth_engine.subprocess.run',return_value=result) as run:
            refresh_for_charts()
        args=run.call_args.args[0]
        inner=int(args[args.index('--budget')+1])
        self.assertGreaterEqual(run.call_args.kwargs['timeout']-inner,5)

    def test_futu_same_basis_repairs_long_tail_without_skipping_middle(self):
        store,frame,symbols=self.fixture(1)
        dates=market_clock('US',NOW)['sessions'][-200:]
        prices=pd.DataFrame({'date':dates,'close':range(100,300)})
        store.write('prices',symbols[0],{});store.save_prices(symbols[0],prices.iloc[:80],'futu_qfq')
        futu=FakeFutu(prices)
        def history(code,day,start=None,**kwargs):return prices.loc[prices.date>=start],'futu_qfq'
        with patch('tools.breadth_engine.fetch_prices',side_effect=RuntimeError('offline')),patch.object(futu,'history',side_effect=history):
            self.run_update(store,futu=futu)
        self.assertEqual(list(store.prices(symbols[0]).date),dates)


if __name__=='__main__': unittest.main()
