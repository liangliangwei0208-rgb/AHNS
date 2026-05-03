"""
RSI 分析兼容入口。

原来的 ``tools.rsi_module`` 同时包含行情获取、RSI 计算、信号整理和绘图。
本次保守拆分后，完整实现先迁移到 ``tools.rsi_data``，并通过
``rsi_calculation.py``、``rsi_plotting.py`` 暴露更清晰的维护入口。

旧调用方式保持不变：

    from tools.rsi_module import rsi_analyze_index
"""

from tools import rsi_data as _impl


for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)


__all__ = [_name for _name in globals() if not _name.startswith("_")]
