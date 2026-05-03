"""
海外基金区间复利累计计算入口。

累计收益率按每天模型估算值复合计算，并按基金与估值日期去重，避免同一天
多次运行缓存导致重复计入。
"""

from tools.fund_history_io import (
    build_benchmark_cumulative_dataframe,
    build_cumulative_dataframe,
    get_benchmark_estimate_records,
    get_fund_estimate_records,
    load_benchmark_estimate_history,
    load_fund_estimate_history,
)


__all__ = [name for name in globals() if not name.startswith("__")]
