"""
A 股休市且海外有新估值时的自动窗口识别入口。

窗口判断只读取交易日历、本地行情缓存和 `main.py` 已写入的海外估算缓存；
不重新拉海外行情，不重新计算基金收益。
"""

from tools.fund_history_io import (
    HolidayEstimateWindow,
    detect_overseas_holiday_estimate_window,
    format_holiday_estimate_date_label,
    format_holiday_estimate_output_suffix,
)


__all__ = [name for name in globals() if not name.startswith("__")]
