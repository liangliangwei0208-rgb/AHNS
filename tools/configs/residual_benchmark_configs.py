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

# 正式日线海外股票持仓型基金的有效持仓增强系数。
# 仅作用于“有效持仓增强 + 补偿基准”口径：
# 有效披露持仓占比先乘以该系数，封顶到 100%，剩余权重再走补偿基准。
# `top10_available_normalized` 基金不使用该系数，也不走补偿基准。
OVERSEAS_VALID_HOLDING_BOOST = 1.15

RESIDUAL_BENCHMARK_SPECS = {
    "nasdaq100": {
        "label": "纳斯达克100",
        "market": "US",
        "ticker": ".NDX",
        "aliases": {"nasdaq100", "nasdaq_100", "ndx", ".ndx", "^ndx"},
        "description": "默认海外股票持仓型基金补偿仓位基准。",
    },
    "sp500": {
        "label": "标普500",
        "market": "US",
        "ticker": ".INX",
        "aliases": {"sp500", "s&p500", "s_and_p_500", "inx", ".inx", "^gspc", "gspc", "spx"},
        "description": "标普500指数，作为更偏美股宽基方向基金的补偿仓位基准。",
    },
    "biotech_ibb": {
        "label": "生物科技",
        "market": "US",
        "ticker": "IBB",
        "aliases": {"biotech", "biotech_ibb", "ibb", "us.ibb"},
        "description": "IBB ETF，作为生物科技方向补偿仓位代理。",
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
    "006679": "us_oil_gas_ep_xop",  # 006679 油气勘探与生产主题基金
    "018852": "us_oil_gas_ep_xop",  # 018852 油气勘探与生产主题基金
    "012868": "sp500",
    "008401": "sp500",
    "519981": "sp500",
    "001092": "biotech_ibb",
}

FUND_ESTIMATION_METHOD_MAP = {
    # 只使用前十大持仓，可取行情的持仓重新归一化到 100%，不走补偿仓位。
    "015205": "top10_available_normalized",
    "017654": "top10_available_normalized",
    "019231": "top10_available_normalized",
    #"164212": "top10_available_normalized",
    "118001": "top10_available_normalized",
}


__all__ = [
    "DEFAULT_RESIDUAL_BENCHMARK_KEY",
    "OVERSEAS_VALID_HOLDING_BOOST",
    "RESIDUAL_BENCHMARK_SPECS",
    "FUND_RESIDUAL_BENCHMARK_MAP",
    "FUND_ESTIMATION_METHOD_MAP",
]
