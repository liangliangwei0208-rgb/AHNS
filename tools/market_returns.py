"""
证券、ETF 和指数涨跌幅获取入口。

这里集中暴露 A 股、港股、美股、指数和代理资产的行情获取函数。节假日
估值日置零、收盘/盘中模式、小时级缓存等细节仍在核心实现中保持不变。
"""

from tools.fund_estimator import (
    fetch_cn_security_return_pct,
    fetch_cn_security_return_pct_daily,
    fetch_cn_security_return_pct_daily_with_date,
    fetch_hk_return_pct,
    fetch_hk_return_pct_akshare_daily,
    fetch_hk_return_pct_akshare_daily_with_date,
    fetch_hk_return_pct_akshare_spot_em,
    fetch_hk_return_pct_last_close_with_fallback,
    fetch_hk_return_pct_last_close_with_fallback_with_date,
    fetch_hk_return_pct_sina,
    fetch_us_index_return_pct_from_rsi_module,
    fetch_us_index_return_pct_yahoo,
    fetch_us_return_pct,
    fetch_us_return_pct_akshare_daily,
    fetch_us_return_pct_akshare_daily_with_date,
    fetch_us_return_pct_akshare_spot_em,
    fetch_us_return_pct_akshare_spot_sina,
    get_proxy_return_pct,
    get_stock_return_pct,
    get_us_index_benchmark_items,
    get_us_index_return_pct_cached,
    infer_sina_cn_symbol,
)


__all__ = [name for name in globals() if not name.startswith("__")]
