"""
基金估算缓存工具的分组入口。

真实实现暂保留在 `tools.fund_estimator`，本模块把缓存相关函数集中导出，
方便后续维护者定位 JSON 缓存、行情缓存 key 和缓存新鲜度逻辑。
"""

from tools.fund_estimator import (
    CACHE_DIR,
    FUND_ESTIMATE_RETURN_CACHE_FILE,
    FUND_HOLDINGS_CACHE_FILE,
    FUND_PURCHASE_LIMIT_CACHE_FILE,
    SECURITY_RETURN_CACHE_FILE,
    _cache_log,
    _cached_index_tuple,
    _cached_return_tuple,
    _df_from_cache_json,
    _df_to_cache_json,
    _ensure_cache_dir,
    _is_cache_fresh,
    _load_json_cache,
    _normalize_security_cache_ticker,
    _save_json_cache,
    _security_return_cache_bucket,
    _security_return_cache_key,
)


__all__ = [name for name in globals() if not name.startswith("__")]
