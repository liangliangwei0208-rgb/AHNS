"""
ETF/FOF/指数联接基金的代理资产配置。

`DEFAULT_FUND_PROXY_MAP` 仍由核心估算模块维护，本文件提供一个更明确的
配置入口，方便后续只查代理基金映射时不用打开完整估算实现。
"""

from tools.fund_estimator import DEFAULT_FUND_PROXY_MAP, OVERSEAS_VALID_HOLDING_BOOST


__all__ = ["DEFAULT_FUND_PROXY_MAP", "OVERSEAS_VALID_HOLDING_BOOST"]
