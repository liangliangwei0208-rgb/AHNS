"""
海外基金累计估算表格输出入口。

包含控制台打印、Matplotlib 表格图绘制和“一键生成累计表”的外部函数。
safe 版本会在此基础上隐藏代码、脱敏名称并替换展示列名。
"""

from tools.fund_history_io import (
    build_cumulative_estimate_table,
    print_cumulative_estimate_table,
    save_cumulative_estimate_table_image,
)


__all__ = [name for name in globals() if not name.startswith("__")]
