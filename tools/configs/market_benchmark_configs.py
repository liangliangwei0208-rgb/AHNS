"""
海外基金图底部“基准表”的配置。

维护什么：
- 这里决定基金估算图底部基准表显示哪些指数、ETF 或海外资产。
- 列表顺序就是图片里的显示顺序。

怎么改：
- 想临时隐藏某个基准，把 `enabled` 改成 False；隐藏后即使缓存里还有旧记录，
  safe 图也不会继续展示它。
- 想新增一个基准，复制一行字典，改 `label`、`kind`、`ticker`。
- 不要在这里写复杂公式或权重；这里只维护“显示什么”和“用什么代码取行情”。

字段说明：
- enabled：True 表示显示，False 表示暂时不显示。False 只是不显示/不再主动拉取，
  不会删除历史缓存。
- label：图片里显示的名称。
- kind：行情获取类型。
    - "us_index"：美股指数，适合 .NDX、.INX、.SOX 这类新浪美股指数代码。
    - "us_security"：美股股票或 ETF，适合 XOP 这类 ETF。
    - "foreign_futures"：新浪外盘期货 / 东方财富国际期货，适合伦敦金、COMEX 黄金等。
    - "yahoo"：Yahoo Chart 代码，适合 VIX 等特殊海外资产。
    - "vix_level"：VIX 恐慌指数点位。它不是涨跌幅，不参与累计收益计算。
- ticker：实际请求行情用的代码。
- fallback_ticker：可选。主 ticker 失败时使用的备用代码。
- display_in_daily_fund：可选。是否在每日 safe 海外基金图底部基准表展示，默认 True。
- display_in_holidays：可选。是否在节假日 / 节后单日观察图展示，默认 True。
- include_in_cumulative：可选。是否作为收益率参与节假日 / 节后累计表复利，默认 True。
  VIX 这类点位指标必须设为 False；否则会被误当成涨跌幅。
- final_confirm_hour_bj / final_confirm_minute_bj：可选。只建议给 "foreign_futures"
  这类接近 24 小时交易的海外期货/贵金属使用。
  例如估值日是 2026-05-08，配置 5:30 表示北京时间 2026-05-09 05:30
  之后才把这条 2026-05-08 日线视为“最终完整日线”。在此之前，即使接口已经
  返回 2026-05-08 这一行，也只当作未确认数据，不展示临时涨跌幅。

注意：
- 伦敦金默认优先使用新浪外盘期货 XAU；如果失败，再用东方财富国际期货 GC00Y
  作为 COMEX 黄金代理，避免国内运行强依赖 Yahoo。
- 伦敦金/现货黄金的日线确认时间默认设为估值日次日北京时间 05:30。原因是
  黄金接近全天交易，晚上 23 点左右接口可能已经返回当天日期，但 close 仍会继续变化。
- VIX 恐慌指数使用 CBOE 官方历史 CSV，失败后回退 FRED。它展示的是“最新完整有效
  交易日收盘点位”，例如 18.42，不带百分号，不表示涨跌幅。
- VIX 只用于每日海外基金图观察市场波动水平，不作为基金补偿基准，也不进入
  safe_holidays.py / sum_holidays.py 的累计基准表。
- 基准表里的涨跌幅全部按“完整日线收盘价”计算，不使用盘中实时行情。
"""

# 正常估算入口仅用本地缓存自愈最近两周的指数基准缺口，不增加行情请求。
BENCHMARK_REPAIR_LOOKBACK_DAYS = 14


def canonical_market_benchmark_symbol(symbol) -> str:
    """统一历史基准别名；不改证券持仓、补偿基准或其它市场的代码口径。"""
    symbol = str(symbol or "").strip().upper()
    return {
        "NDX": ".NDX", "^NDX": ".NDX", ".NDX": ".NDX",
        "SPX": ".INX", "GSPC": ".INX", "^GSPC": ".INX", ".INX": ".INX",
    }.get(symbol, symbol)


MARKET_BENCHMARK_ITEMS = [
    {"enabled": True, "label": "纳斯达克100", "kind": "us_index", "ticker": ".NDX"},
    {"enabled": True, "label": "标普500", "kind": "us_index", "ticker": ".INX"},
    {
        "enabled": False,
        "label": "生物科技",
        "kind": "us_security",
        "ticker": "IBB",
        "display_in_daily_fund": True,
        "display_in_holidays": True,
        "include_in_cumulative": True,
    },
    {"enabled": True, "label": "油气开采指数", "kind": "us_security", "ticker": "XOP"},
    {"enabled": True, "label": "费城半导体", "kind": "us_index", "ticker": ".SOX"},
    {
        "enabled": True,
        "label": "现货黄金",
        "kind": "foreign_futures",
        "ticker": "XAU",
        "fallback_ticker": "GC00Y",
        "final_confirm_hour_bj": 5,
        "final_confirm_minute_bj": 30,
    },
    {
        "enabled": True,
        "label": "VIX恐慌指数",
        "kind": "vix_level",
        "ticker": "VIX",
        "display_in_daily_fund": True,
        "display_in_holidays": False,
        "include_in_cumulative": False,
    },
]


__all__ = ["MARKET_BENCHMARK_ITEMS", "BENCHMARK_REPAIR_LOOKBACK_DAYS",
           "canonical_market_benchmark_symbol"]
