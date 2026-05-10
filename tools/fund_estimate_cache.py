"""
基金级估算结果和 benchmark 历史缓存入口。

`safe_fund.py` 与节假日累计脚本都依赖 `cache/fund_estimate_return_cache.json`。
本模块把相关写入和估值日期解析函数集中导出，便于维护缓存字段兼容性。
"""

from tools.fund_estimator import (
    _extract_date_from_text,
    _extract_valuation_date_from_benchmark_footer_items,
    _is_after_domestic_estimate_freeze_time,
    _resolve_overseas_estimate_valuation_date,
    _write_domestic_fund_estimate_history_cache,
    _write_overseas_benchmark_history_cache,
    _write_overseas_fund_estimate_history_cache,
)


__all__ = [name for name in globals() if not name.startswith("__")]
