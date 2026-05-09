"""
缓存有效期配置。

这个文件只放“多久认为缓存仍可用”的策略常量，不读写缓存、不联网、不出图。
后续如果想调整请求频率，优先改这里，避免在多个业务脚本里到处找数字。
"""

# 普通小时桶行情缓存保留天数。主要用于盘中或短周期行情缓存裁剪。
SECURITY_HOURLY_CACHE_RETENTION_DAYS = 15

# 普通证券日线缓存保留天数。用于非指数类证券的历史缓存裁剪。
SECURITY_DAILY_CACHE_RETENTION_DAYS = 30

# 指数、基准、基金估算历史保留天数。默认保留较长，便于节假日累计和回溯。
SECURITY_INDEX_CACHE_RETENTION_DAYS = 300
FUND_ESTIMATE_HISTORY_RETENTION_DAYS = 300
ANCHOR_CACHE_STABLE_RETENTION_DAYS = 300

# 锚点行情的短期重试窗口。pending 更快重试，missing/stale 稍慢重试。
# 如果某个证券的锚点行情缓存是 pending，脚本在 1 小时内会先复用这个结果，避免反复请求接口；超过 1 小时后，下次运行就会重新拉取。
# 如果状态是 missing 或 stale，脚本会等 2 小时后再重试。因为这类情况更像接口失败、源站延迟或当天数据没同步，太频繁重试意义不大。
ANCHOR_PENDING_CACHE_HOURS = 1
ANCHOR_TRANSIENT_CACHE_HOURS = 2

# 基金限购缓存必须严格 7 天有效，不要改成固定每周刷新。
FUND_PURCHASE_LIMIT_CACHE_DAYS = 7

# 基金持仓缓存兼容旧调用参数；实际目标季度确认逻辑仍以持仓披露窗口为准。
FUND_HOLDINGS_CACHE_DAYS = 75

# A 股交易日历文件缓存有效期。过期后才主动刷新 AkShare 日历。
A_SHARE_TRADE_CALENDAR_CACHE_DAYS = 7

# 国内 ETF RSI 只补实时点的历史缓存新鲜度阈值。
# 含义：本地 CSV 最新日期距离今天不超过这个自然日数时，可以复用历史数据并补今天实时点；
# 更旧则重拉整段历史，避免拿很旧的历史数据硬拼实时点导致 RSI 失真。
RSI_CN_ETF_REALTIME_CACHE_MAX_AGE_DAYS = 10


__all__ = [
    "SECURITY_HOURLY_CACHE_RETENTION_DAYS",
    "SECURITY_DAILY_CACHE_RETENTION_DAYS",
    "SECURITY_INDEX_CACHE_RETENTION_DAYS",
    "FUND_ESTIMATE_HISTORY_RETENTION_DAYS",
    "ANCHOR_CACHE_STABLE_RETENTION_DAYS",
    "ANCHOR_PENDING_CACHE_HOURS",
    "ANCHOR_TRANSIENT_CACHE_HOURS",
    "FUND_PURCHASE_LIMIT_CACHE_DAYS",
    "FUND_HOLDINGS_CACHE_DAYS",
    "A_SHARE_TRADE_CALENDAR_CACHE_DAYS",
    "RSI_CN_ETF_REALTIME_CACHE_MAX_AGE_DAYS",
]
