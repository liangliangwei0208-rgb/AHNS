"""
海外基金图底部“基准表”的配置。

维护什么：
- 这里决定基金估算图底部基准表显示哪些指数、ETF 或海外资产。
- 列表顺序就是图片里的显示顺序。

怎么改：
- 想临时隐藏某个基准，把 `enabled` 改成 False。
- 想新增一个基准，复制一行字典，改 `label`、`kind`、`ticker`。
- 不要在这里写复杂公式或权重；这里只维护“显示什么”和“用什么代码取行情”。

字段说明：
- enabled：True 表示显示，False 表示暂时不显示。
- label：图片里显示的名称。
- kind：行情获取类型。
    - "us_index"：美股指数，适合 .NDX、.INX、.SOX 这类新浪美股指数代码。
    - "us_security"：美股股票或 ETF，适合 XOP 这类 ETF。
    - "foreign_futures"：新浪外盘期货 / 东方财富国际期货，适合伦敦金、COMEX 黄金等。
    - "yahoo"：Yahoo Chart 代码，适合 VIX 等特殊海外资产。
- ticker：实际请求行情用的代码。
- fallback_ticker：可选。主 ticker 失败时使用的备用代码。

注意：
- 伦敦金默认优先使用新浪外盘期货 XAU；如果失败，再用东方财富国际期货 GC00Y
  作为 COMEX 黄金代理，避免国内运行强依赖 Yahoo。
- VIX 仍保留 Yahoo Chart，因为当前本地新浪 / 东方财富路径没有稳定的 VIX 完整日线。
- 基准表里的涨跌幅全部按“完整日线收盘价”计算，不使用盘中实时行情。
"""

MARKET_BENCHMARK_ITEMS = [
    {"enabled": True, "label": "纳斯达克100", "kind": "us_index", "ticker": ".NDX"},
    {"enabled": True, "label": "标普500", "kind": "us_index", "ticker": ".INX"},
    {"enabled": True, "label": "美国油气开采", "kind": "us_security", "ticker": "XOP"},
    {"enabled": True, "label": "VIX恐慌指数", "kind": "yahoo", "ticker": "^VIX"},
    {"enabled": True, "label": "费城半导体", "kind": "us_index", "ticker": ".SOX"},
    {"enabled": True, "label": "伦敦金", "kind": "foreign_futures", "ticker": "XAU", "fallback_ticker": "GC00Y"},
]


__all__ = ["MARKET_BENCHMARK_ITEMS"]
