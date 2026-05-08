"""
海外股票持仓型基金“补偿仓位”基准配置。

补偿仓位是什么：
- 海外/QDII 股票持仓型基金只能看到季报披露的前十大持仓。
- 已披露但行情失败的持仓，以及未披露的剩余仓位，需要一个方向性基准来近似估算。
- 默认使用纳斯达克100；如果某只基金主题很明确，可以在这里指定更贴近的基准。

维护方式：
- 想给某只基金指定补偿基准，只改 `FUND_RESIDUAL_BENCHMARK_MAP`。
- 左边写 6 位基金代码字符串，右边写 `RESIDUAL_BENCHMARK_SPECS` 里的 key。
- `007844` 当前按用户选择直接使用 XOP，它是跟踪美国油气勘探与生产方向指数的 ETF
  代理，不是指数本身。
"""

from __future__ import annotations


DEFAULT_RESIDUAL_BENCHMARK_KEY = "nasdaq100"

RESIDUAL_BENCHMARK_SPECS = {
    "nasdaq100": {
        "label": "纳斯达克100",
        "market": "US",
        "ticker": ".NDX",
        "aliases": {"nasdaq100", "nasdaq_100", "ndx", ".ndx", "^ndx"},
        "description": "默认海外股票持仓型基金补偿仓位基准。",
    },
    "us_oil_gas_ep_xop": {
        "label": "美国油气开采(XOP)",
        "market": "US",
        "ticker": "XOP",
        "aliases": {"xop", "us_oil_gas_ep", "us_oil_gas_ep_xop", "oil_gas_ep"},
        "description": "XOP ETF，作为美国油气勘探与生产方向补偿仓位代理。",
    },
}

FUND_RESIDUAL_BENCHMARK_MAP = {
    "007844": "us_oil_gas_ep_xop",
}


__all__ = [
    "DEFAULT_RESIDUAL_BENCHMARK_KEY",
    "RESIDUAL_BENCHMARK_SPECS",
    "FUND_RESIDUAL_BENCHMARK_MAP",
]
