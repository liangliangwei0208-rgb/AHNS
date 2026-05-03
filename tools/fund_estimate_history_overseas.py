"""
海外基金历史估算兼容入口。

旧脚本从本模块读取节假日窗口、历史缓存、区间复利和累计表格绘图函数。
为降低迁移风险，完整实现保留在 ``tools.fund_history_io``，本文件只重新
导出旧接口；新增维护可以优先查看 ``holiday_window.py``、``fund_cumulative.py``
和 ``fund_cumulative_plot.py`` 中的分组入口说明。
"""

from tools import fund_history_io as _impl


for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)


__all__ = [_name for _name in globals() if not _name.startswith("_")]
