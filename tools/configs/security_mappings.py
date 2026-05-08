"""
证券代码识别映射。

维护影响：
- `US_TICKER_MAP` 用于持仓名称兜底识别美股 ticker。
- `KR_TICKER_MAP` 用于韩国股票识别。韩国六位数字代码可能和 A 股代码冲突，
  所以核心逻辑会要求“代码 + 名称别名”同时命中，避免把 A 股误判成韩国股票。
"""

US_TICKER_MAP = {
    "奈飞": "NFLX",
    "Netflix": "NFLX",
    "英伟达": "NVDA",
    "NVIDIA": "NVDA",
    "苹果": "AAPL",
    "Apple": "AAPL",
    "微软": "MSFT",
    "Microsoft": "MSFT",
    "博通": "AVGO",
    "Broadcom": "AVGO",
    "特斯拉": "TSLA",
    "Tesla": "TSLA",
    "谷歌-C": "GOOG",
    "谷歌C": "GOOG",
    "Alphabet Inc Class C": "GOOG",
    "谷歌-A": "GOOGL",
    "谷歌A": "GOOGL",
    "Alphabet Inc Class A": "GOOGL",
    "亚马逊": "AMZN",
    "Amazon": "AMZN",
    "Meta Platforms Inc-A": "META",
    "Meta Platforms": "META",
    "Meta": "META",
    "迈威尔科技": "MRVL",
    "迈威尔": "MRVL",
    "Marvell": "MRVL",
    "台积电": "TSM",
    "TSMC": "TSM",
    "康宁": "GLW",
    "Corning": "GLW",
    "Coterra Energy Inc": "CTRA",
    "Coterra Energy": "CTRA",
    "英美烟草": "BTI",
    "British American Tobacco": "BTI",
    "再生元制药": "REGN",
    "Regeneron": "REGN",
}


KR_TICKER_MAP = {
    "000660": {
        "ticker": "000660",
        "name": "SK海力士",
        "aliases": ["SK海力士", "海力士", "SK Hynix", "Hynix", "SK hynix"],
    },
    "005930": {
        "ticker": "005930",
        "name": "三星电子",
        "aliases": ["三星电子", "三星", "Samsung Electronics", "Samsung"],
    },
}


__all__ = ["US_TICKER_MAP", "KR_TICKER_MAP"]
