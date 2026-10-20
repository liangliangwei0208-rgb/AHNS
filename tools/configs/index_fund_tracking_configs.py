"""独立观察池：只用于真实历史收益与限购事件，严禁加入模型估算池。"""
NASDAQ100_TRACKED_FUNDS = [
    "021000", "019441", "000834", "539001", "016452", "019736", "019547",
    "019172", "040046", "019524", "022525", "015299", "016055", "016532",
    "160213", "018043", "270042", "161130", "018966",
]
SP500_TRACKED_FUNDS = ["017641", "018064", "050025", "017028", "007721", "161125"]
INDEX_FUND_TRACKING_MAP = {
    **{code: {"index_symbol": ".NDX", "index_name_cn": "纳斯达克100指数", "index_name_en": "NASDAQ-100"}
       for code in NASDAQ100_TRACKED_FUNDS},
    **{code: {"index_symbol": ".INX", "index_name_cn": "标普500指数", "index_name_en": "S&P 500"}
       for code in SP500_TRACKED_FUNDS},
}
INDEX_FUND_RETURN_PERIODS = (1, 3, 6, 12)
DATE_TOLERANCE_DAYS = 20
FUND_RETURN_COLOR = "#17858A"
INDEX_RETURN_COLOR = "#E3A149"
# 400个真实日线点足够覆盖12自然月+20天容忍；合并时保留共享CSV原有更长历史。
INDEX_HISTORY_FETCH_ROWS = 400
