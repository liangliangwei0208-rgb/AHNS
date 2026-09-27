"""50DMA广度配置；股票数量是名单完整性校验，不是估算分母。"""
BREADTH_ENABLED = True
BREADTH_SNAPSHOT_TTL_MINUTES = 30  # 可在15-60分钟之间调整
BREADTH_MAX_QUOTE_AGE_MINUTES = 60
BREADTH_RUNTIME_BUDGET_SECONDS = 180
BREADTH_HISTORY_ROWS = 400
BREADTH_MIN_COVERAGE = .95
BREADTH_FUTU_RESERVE = 10
BREADTH_FUTU_BATCH_SIZE = 200
BREADTH_MARKETS = {
    "nasdaq": {"market":"US", "name":"纳斯达克综合指数", "symbol":".IXIC", "external":"$NAA50R", "universe":"nasdaq_composite"},
    "dow": {"market":"US", "name":"道琼斯工业指数", "symbol":".DJI", "expected":30, "external":"$DOWA50R", "universe":"dow30"},
    "dividend": {"market":"CN", "name":"中证红利低波动", "symbol":"512890", "index":"H30269", "expected":50, "universe":"H30269"},
    "csi2000": {"market":"CN", "name":"中证2000", "symbol":"560220", "index":"932000", "expected":2000, "universe":"932000"},
    "shenzhen": {"market":"CN", "name":"深证成指", "symbol":"159943", "index":"399001", "expected":500, "universe":"399001"},
}
