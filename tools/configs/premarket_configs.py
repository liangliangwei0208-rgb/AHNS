"""
盘前海外基金观察配置。

这里维护两类信息：
1. 盘前附表显示哪些实时观察项，以及每个观察项实际使用哪个美股 ticker 取数；
2. 不同基金的“补仓仓位”使用哪个盘前实时观察项估算。

维护提示：
- `PREMARKET_BENCHMARK_SPECS` 的 key 是内部配置名，图片只显示 `label`。
- `PREMARKET_FUND_RESIDUAL_BENCHMARK_MAP` 左边写 6 位基金代码，右边写
  `PREMARKET_BENCHMARK_SPECS` 里的 key。
- 普通海外股票持仓基金默认使用纳指100方向补仓。
- 油气主题基金应显式配置为 `oil_gas_ep`，避免误用纳指100补仓。
"""

from __future__ import annotations


PREMARKET_START_HOUR_BJ = 17
PREMARKET_START_MINUTE_BJ = 0
PREMARKET_END_HOUR_BJ = 20
PREMARKET_END_MINUTE_BJ = 30


PREMARKET_BENCHMARK_SPECS = {
    "nasdaq100": {
        "order": 1,
        "label": "纳指100",
        "market": "US",
        "ticker": "QQQ",
        "kind": "us_security",
        "display_in_footer": True,
        "usable_as_residual": True,
        "description": "用 QQQ 作为纳斯达克100方向盘前实时观察项。",
    },
    "sp500": {
        "order": 2,
        "label": "标普500",
        "market": "US",
        "ticker": "SPY",
        "kind": "us_security",
        "display_in_footer": True,
        "usable_as_residual": True,
        "description": "用 SPY 作为标普500方向盘前实时观察项。",
    },
    "oil_gas_ep": {
        "order": 3,
        "label": "油气开采",
        "market": "US",
        "ticker": "XOP",
        "kind": "us_security",
        "display_in_footer": True,
        "usable_as_residual": True,
        "description": "用 XOP 作为美国油气勘探与生产方向盘前实时观察项。",
    },
    "semiconductor": {
        "order": 4,
        "label": "费城半导体",
        "market": "US",
        "ticker": "SMH",
        "kind": "us_security",
        "display_in_footer": True,
        "usable_as_residual": True,
        "description": "用 SMH 作为半导体方向盘前实时观察项。",
    },
    "gold": {
        "order": 5,
        "label": "黄金",
        "market": "US",
        "ticker": "GLD",
        "kind": "us_security",
        "display_in_footer": True,
        "usable_as_residual": True,
        "description": "用 GLD 作为黄金方向盘前实时观察项。",
    },
    "vix": {
        "order": 6,
        "label": "VIX恐慌指数",
        "market": "VIX_LEVEL",
        "ticker": "VIX",
        "kind": "vix_level",
        "display_in_footer": True,
        "usable_as_residual": False,
        "description": "VIX 是点位观察项，不作为补仓收益率基准。",
    },
}


PREMARKET_DEFAULT_RESIDUAL_BENCHMARK_KEY = "nasdaq100"


PREMARKET_FUND_RESIDUAL_BENCHMARK_MAP = {
    "007844": "oil_gas_ep",
    "006679": "oil_gas_ep",
    "018852": "oil_gas_ep",
}


__all__ = [
    "PREMARKET_BENCHMARK_SPECS",
    "PREMARKET_DEFAULT_RESIDUAL_BENCHMARK_KEY",
    "PREMARKET_END_HOUR_BJ",
    "PREMARKET_END_MINUTE_BJ",
    "PREMARKET_FUND_RESIDUAL_BENCHMARK_MAP",
    "PREMARKET_START_HOUR_BJ",
    "PREMARKET_START_MINUTE_BJ",
]
