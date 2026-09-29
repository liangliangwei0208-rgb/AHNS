"""50DMA广度配置；股票数量是名单完整性校验，不是估算分母。"""
BREADTH_ENABLED = True
BREADTH_SNAPSHOT_TTL_MINUTES = 40  # 可在15-60分钟之间调整
BREADTH_MAX_QUOTE_AGE_MINUTES = 60
BREADTH_RUNTIME_BUDGET_SECONDS = 180
BREADTH_HISTORY_ROWS = 400
BREADTH_MIN_COVERAGE = .90
BREADTH_FALLBACK_MAX_SESSIONS = 5  # 最新行情失效时，右侧最多展示最近5个交易日的可信收盘值
# 价格图上50D极端状态带的阈值；可自行调整，须保持 0 <= 下限 < 上限 <= 100。
BREADTH_BAND_LOW_THRESHOLD = 30.0
BREADTH_BAND_HIGH_THRESHOLD = 78.0
BREADTH_BAND_LOW_COLOR = "#D29120"
BREADTH_BAND_HIGH_COLOR = "#7852AF"
BREADTH_FUTU_RESERVE = 10
BREADTH_FUTU_BATCH_SIZE = 200
# 纳指官方目录只是 COMP 合资格证券近似池；数量与每日变动仅用于拦截残缺目录。
BREADTH_NASDAQ_PROXY_MIN_MEMBERS = 2800
BREADTH_NASDAQ_PROXY_MAX_MEMBERS = 4000
BREADTH_NASDAQ_MAX_DAILY_CHANGE_RATIO = .03
BREADTH_MARKETS = {
    "nasdaq": {"market":"US", "name":"纳斯达克综合指数", "symbol":".IXIC", "external":"$NAA50R", "universe":"nasdaq_composite"},
    "dow": {"market":"US", "name":"道琼斯工业指数", "symbol":".DJI", "expected":30, "external":"$DOWA50R", "universe":"dow30"},
    "dividend": {"market":"CN", "name":"中证红利低波动", "symbol":"512890", "index":"H30269", "expected":50, "universe":"H30269"},
    "csi2000": {"market":"CN", "name":"中证2000", "symbol":"560220", "index":"932000", "expected":2000, "universe":"932000"},
    "shenzhen": {"market":"CN", "name":"深证成指", "symbol":"159943", "index":"399001", "expected":500, "universe":"399001"},
}
