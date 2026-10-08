"""只读性能验收：对照指定Git基线与当前实现，不联网、不改业务缓存。

用法：python -m tests.benchmark_breadth_refresh --baseline <commit>
计时不启用tracemalloc；另跑一次测Python分配峰值，不能视为进程RSS。
"""
import argparse
from contextlib import nullcontext
import gc
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time
import tracemalloc
import types
from unittest.mock import patch

import pandas as pd
from pandas.core.window.rolling import Rolling
from tools import breadth_engine as current_engine, market_breadth as current_math

ROOT = Path(__file__).resolve().parents[1]
NOW = pd.Timestamp('2026-10-08T04:45:00Z')
MARKETS = ('nasdaq100', 'dow', 'csi2000', 'shenzhen', 'dividend')


def baseline_modules(ref):
    modules = []
    for file in ('market_breadth', 'breadth_engine'):
        source = subprocess.check_output(['git', 'show', f'{ref}:tools/{file}.py'], cwd=ROOT).decode('utf-8')
        module = types.ModuleType(f'tools._baseline_{file}')
        module.__file__ = str(ROOT/'tools'/f'{file}.py')
        exec(compile(source, module.__file__, 'exec'), module.__dict__)
        modules.append(module)
    math, engine = modules
    for name in ('BreadthStore', 'calculate_history', 'calculate_segmented_history'):
        setattr(engine, name, getattr(math, name))
    return math, engine


def pipeline(math, engine, key, optimized):
    """复现下载预扫描、待发布历史计算、独立完成验收；不执行下载和写入。"""
    store = math.BreadthStore(ROOT/'cache'/'market_breadth')
    clock = engine.market_clock(engine.BREADTH_MARKETS[key]['market'], NOW)
    with store.read_scope() if optimized else nullcontext():
        member = store.members(key, clock['day'])
        plan = engine.price_download_plan(store, key, member, clock['day'], clock['complete_day'], False, False, True)
        rows = store.read('results', key).get('rows', [])
        published = {r['date'] for r in rows if r.get('kind') == 'close' and r.get('percent') is not None
                     and r.get('finality') != 'snapshot_provisional'}
        dates = clock['sessions'][-400:]
        targets = [day for day in dates if day not in published]
        proof = engine._reusable_close(store, key, clock, store.members(key, clock['complete_day']), plan) if optimized else None
        if proof:
            output = pd.DataFrame()
        else:
            needed = set(member['symbols'])
            requested = sorted(set(targets) | {clock['complete_day']}) if optimized else targets
            for day in requested:
                needed.update(store.members(key, day).get('symbols', []))
            if optimized:
                prices, token = store.price_snapshot(needed)
            else:
                prices = {code: store.prices(code) for code in needed}
            output = math.calculate_segmented_history(prices, store, key, requested, engine.BREADTH_MIN_COVERAGE,
                                                      calendar_sessions=dates)
            if optimized and not output.empty:
                target = output.loc[output.date == clock['complete_day']]
                complete_member = store.members(key, clock['complete_day'])
                codes = set(complete_member.get('symbols', []))
                if not target.empty:
                    proof = dict(day=clock['complete_day'], version=complete_member['version'],
                                 prices_token=tuple(item for item in token if item[0] in codes),
                                 row=target.iloc[-1].to_dict())
        complete = (engine.close_completion(store, key, clock, calculation=proof) if optimized else
                    engine.close_completion(store, key, clock))
        calculated = output.loc[output.date.isin(targets)] if not output.empty else output
        # JSON统一缺失值，仅比较金融计算字段；正式缓存原文另做哈希保护。
        columns = ['date', 'percent', 'valid', 'total', 'coverage', 'membership_version']
        values = [{field:None if isinstance(value,float) and not math.isfinite(value) else value
                   for field,value in row.items()} for row in calculated[columns].to_dict('records')] if not calculated.empty else []
        latest = max((r for r in rows if r.get('kind') == 'close' and r.get('percent') is not None
                      and r.get('finality') not in {'snapshot_provisional', 'chart_only'}), key=lambda r:r['date'])
        return dict(completion=complete, pending=len(plan['pending']), computed_rows=values, latest_formal=latest,
                    reused=bool(optimized and proof and output.empty))


def measure(math, engine, key, optimized, memory=False):
    counts = dict(price_file_reads=0, price_dataframe_builds=0, sma_passes=0, http_requests=0)
    read_text, mean = Path.read_text, Rolling.mean
    def read(path, *args, **kwargs):
        if path.parent.name == 'prices': counts['price_file_reads'] += 1
        return read_text(path, *args, **kwargs)
    def rolling(obj, *args, **kwargs):
        counts['sma_passes'] += 1
        return mean(obj, *args, **kwargs)
    if optimized:
        original = math.BreadthStore.price_state
        def price(store, code):
            cached = store._price_cache.get(code)
            if not store._scope_depth or cached is None or cached['signature'] != store._signature(store.path('prices', code)):
                counts['price_dataframe_builds'] += 1
            return original(store, code)
        method = 'price_state'
    else:
        original = math.BreadthStore.prices
        def price(store, code):
            counts['price_dataframe_builds'] += 1
            return original(store, code)
        method = 'prices'
    gc.collect()
    if memory: tracemalloc.start()
    with patch.object(Path, 'read_text', read), patch.object(Rolling, 'mean', rolling), \
         patch.object(math.BreadthStore, method, price), \
         patch('requests.sessions.Session.request', side_effect=AssertionError('offline benchmark')):
        started = time.perf_counter()
        result = pipeline(math, engine, key, optimized)
        elapsed = time.perf_counter()-started
    peak = tracemalloc.get_traced_memory()[1] if memory else None
    if memory: tracemalloc.stop()
    return dict(seconds=elapsed, python_peak_bytes=peak, **counts), result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--output', default='docs/audits/optimization_performance.json')
    args = parser.parse_args()
    old_math, old_engine = baseline_modules(args.baseline)
    # 日历构建属于两版本公共开销；先统一预热，不混入价格读取计时。
    for engine in (old_engine, current_engine):
        for market in ('US', 'CN'): engine.market_clock(market, NOW)
    report = dict(baseline=args.baseline, frozen_time=NOW.isoformat(), repetitions=3,
                  scope='local planning + unpublished close calculation + close completion; no download/write', markets={})
    for key in MARKETS:
        versions, results = {}, []
        for label, math, engine, optimized in [('before', old_math, old_engine, False),
                                              ('after', current_math, current_engine, True)]:
            samples = []
            for _ in range(3):
                metrics, result = measure(math, engine, key, optimized)
                samples.append(metrics)
            memory, result = measure(math, engine, key, optimized, memory=True)
            versions[label] = dict(median_seconds=statistics.median(r['seconds'] for r in samples),
                                   samples=samples, python_peak_bytes=memory['python_peak_bytes'])
            results.append(result)
        before, after = results
        assert before['computed_rows'] == after['computed_rows'], f'{key}: history changed'
        for field in ('expected_complete_day', 'latest_valid_close_date', 'member_count', 'valid_count', 'coverage', 'status'):
            assert before['completion'][field] == after['completion'][field], (key, field)
        report['markets'][key] = dict(**versions, completion={field:value for field,value in after['completion'].items()
                                                            if field!='remaining_missing_symbols'}, pending=after['pending'],
                                      latest_formal=after['latest_formal'], compared_history_rows=len(after['computed_rows']),
                                      exact_result_match=True, reused=after['reused'])
        print(key, json.dumps({label:versions[label]['median_seconds'] for label in versions}), flush=True)
    path = ROOT/args.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(path)


if __name__ == '__main__':
    main()
