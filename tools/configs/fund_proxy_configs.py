"""
基金代理资产配置。

维护影响：
- `DEFAULT_FUND_PROXY_MAP` 决定哪些基金不读取前十大持仓，而是用 ETF/指数代理资产估算。
- 新增代理基金时，每个 component 至少维护 `name`、`code`、`type`、`weight_pct`。
- `OVERSEAS_VALID_HOLDING_BOOST` 这里只保留兼容导出；正式维护位置在
  `tools/configs/residual_benchmark_configs.py`。
"""
from tools.configs.residual_benchmark_configs import OVERSEAS_VALID_HOLDING_BOOST

# 兼容旧导入路径：有效持仓增强系数的主维护位置已移动到
# `tools/configs/residual_benchmark_configs.py`。

DEFAULT_FUND_PROXY_MAP = {
    # 华泰柏瑞中证红利低波动 ETF 联接
    # 使用场内红利低波 ETF 作为联接基金代理资产。
    "007467": {
        "description": "华泰柏瑞中证红利低波动ETF联接：用底层 512890 ETF 代理",
        "components": [
            {
                "name": "华泰柏瑞中证红利低波动ETF",
                "code": "512890",
                "type": "cn_etf",
                "weight_pct": 99.31,
            }
        ],
    },

    # 华安国际龙头(DAX)ETF 联接(QDII)
    # 常见场内 DAX ETF 代码可能为 513030；如与你软件显示不一致，请改这里。
    "015016": {
        "description": "华安国际龙头(DAX)ETF联接(QDII)：用底层 DAX ETF 代理",
        "components": [
            {
                "name": "华安德国(DAX)ETF",
                "code": "513030",
                "type": "cn_etf",
                "weight_pct": 99.89,
            }
        ],
    },

    # 天弘标普500(QDII-FOF)C
    # 使用 SPY 作为标普500代理，统一走美股 ETF 行情。
    "007722": {
        "description": "天弘标普500(QDII-FOF)C：用 SPY 代理标普500基金仓位",
        "components": [
            {
                "name": "SPDR标普500ETF",
                "code": "SPY",
                "type": "us_etf",
                "weight_pct": 99.77,
            }
        ],
    },
    # 如果你有其他 ETF 联接 / FOF 需要代理，可以在这里添加，格式同上。
    "015311": {
        "description": "华泰柏瑞南方东英恒生科技指数：用 015311 代理恒生科技指数基金仓位",
        "components": [
            {
                "name": "恒生科技指数基金",
                "code": "513130",
                "type": "cn_etf",
                "weight_pct": 99.17,
            }
        ],
    },
    "005125": {
        "description": "华宝标普中国A股红利指数：用 005125 代理标普中国A股红利指数基金仓位",
        "components": [
            {
                "name": "标普中国A股红利指数基金",
                "code": "562060",
                "type": "cn_etf",
                "weight_pct": 99.17,
            }
        ],
    },
    "020713": {
        "description": "华安三菱日联日经225ETF：用 020713 代理日经225指数基金仓位",
        "components": [
            {
                "name": "日经225指数基金",
                "code": "513880",
                "type": "cn_etf",
                "weight_pct": 99.17,
            }
        ],
    },
    "023918": {
        "description": "华夏国证自由现金流：用 023918 代理自由现金流指数基金仓位",
        "components": [
            {
                "name": "自由现金流指数基金",
                "code": "159201",
                "type": "cn_etf",
                "weight_pct": 99.17,
            }
        ],
    },
    "008987": {
        "description": "广发上海金ETF：用 008987 代理上海金指数基金仓位",
        "components": [
            {
                "name": "上海金指数基金",
                "code": "518600",
                "type": "cn_etf",
                "weight_pct": 99.17,
            }
        ],
    },
    "016020": {
        "description": "招商中证电池主题ETF：用 016020 代理电池主题指数基金仓位",
        "components": [
            {
                "name": "电池主题指数基金",
                "code": "561910",
                "type": "cn_etf",
                "weight_pct": 99.17,
            }
        ],
    },
    "012725": {
        "description": "国泰中证畜牧养殖：用 012725 代理畜牧养殖指数基金仓位",
        "components": [
            {
                "name": "畜牧养殖指数基金",
                "code": "159865",
                "type": "cn_etf",
                "weight_pct": 99.17,
            }
        ],
    },
    "023145": {
        "description": "汇添富中证油气资源：用 023145 代理油气资源指数基金仓位",
        "components": [
            {
                "name": "油气资源指数基金",
                "code": "159309",
                "type": "cn_etf",
                "weight_pct": 99.17,
            }
        ],
    },
}


__all__ = ["DEFAULT_FUND_PROXY_MAP", "OVERSEAS_VALID_HOLDING_BOOST"]
