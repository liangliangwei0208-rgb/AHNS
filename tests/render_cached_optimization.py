"""使用真实本地缓存做图片验收；不刷新、联网或写入正式业务缓存。"""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

from strategy import gdp, gu_zhai_xi
from tools import a_share_valuation, rsi_data
from tools.configs.rsi_configs import RSI_ANALYSIS_CONFIGS
from tools.equity_bond_spread import load_ebs_states


def main():
    cache = Path('cache')
    # 保护正式结果、宏观事件、旧CSV与共享行情分片；图片是本次唯一业务输出。
    protected = list((cache/'market_breadth').rglob('*.json')) + list(cache.glob('*.csv')) + [cache/'a_share_mc_gdp.json']
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    before = {p: digest(p) for p in protected}
    shared = a_share_valuation.load_macro_data(refresh=False)
    index = pd.read_csv(cache/'sz399001_daily.csv', parse_dates=['date'])
    pe = pd.read_csv(cache/'gu_zhai_xi_pe.csv', parse_dates=['date'])
    bond = pd.read_csv(cache/'gu_zhai_xi_cn10y.csv', parse_dates=['date'])
    vix = pd.read_csv(cache/'vix_index_daily.csv', parse_dates=['date'])
    summary = {}
    def cached_price(symbol, days=1200, cache_dir='cache', **kwargs):
        # 离线验收不尝试刷新落后的CN行情；日期保持缓存真实日期。
        path = Path(cache_dir)/(symbol.replace('.', 'dot_')+'_index_daily.csv')
        return rsi_data._read_cache(path, days)
    original_close = plt.close
    def inspect_close(fig=None):
        if hasattr(fig, 'axes'):
            summary[current] = dict(
                axes=len(fig.axes), price_ylim=list(fig.axes[0].get_ylim()),
                rsi_ylim=list(fig.axes[1].get_ylim()) if len(fig.axes)>1 else None,
                right_ylim=list(fig.axes[2].get_ylim()) if len(fig.axes)>2 else None,
                labels=[text.get_text() for axis in fig.axes for text in axis.texts],
                legends=[[t.get_text() for t in axis.get_legend().get_texts()] for axis in fig.axes if axis.get_legend()],
                band_counts={name:sum(a.get_gid()==name for a in fig.axes[0].get_children())
                             for name in ('ene-state-band','ebs-state-band')})
        original_close(fig)
    # 使用已有source返回结构，仅替换数据获取边界；EBS公式、周线和所有绘图仍走正式入口。
    with patch('requests.sessions.Session.request', side_effect=AssertionError('visual acceptance is offline')), \
         patch.object(gu_zhai_xi, '_load_chart_sources', return_value=(index.rename(columns={'close':'index_close'}), pe, bond, {})), \
         patch.object(a_share_valuation, 'load_macro_data', return_value=shared), \
         patch.object(rsi_data, 'get_index_akshare', side_effect=cached_price), \
         patch.object(rsi_data, 'fetch_vix_daily_history', return_value=vix), \
         patch.object(plt, 'close', side_effect=inspect_close):
        ebs = load_ebs_states()
        for config in RSI_ANALYSIS_CONFIGS:
            kwargs = dict(config['kwargs'], include_realtime=False, do_print=False, show_plot=False)
            if kwargs.get('show_mc_gdp'): kwargs['mc_gdp_df'] = shared[2]
            if kwargs.get('show_ebs_state_band'): kwargs['ebs_state_df'] = ebs
            current = kwargs['output_file']
            rsi_data.rsi_analyze_index(**kwargs)
            print(current, json.dumps({k:v for k,v in summary[current].items() if k!='labels'}, ensure_ascii=False), flush=True)
        ratio = gdp.build_market_cap_gdp_ratio()
        current = str(gdp.OUTPUT_PNG)
        gdp.plot_chart(ratio, index)
    assert all(digest(p)==sha for p,sha in before.items()), 'read-only acceptance changed a business cache'
    summary['macro'] = dict(latest_ratio=float(shared[2].iloc[-1].ratio), event_count=len(shared[2]),
                            historical_cutoff=shared[2].attrs.get('historical_cutoff'), protected_files=len(before),
                            http_requests=0, business_cache_changes=0)
    output=Path('docs/audits/optimization_visual_acceptance.json')
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
