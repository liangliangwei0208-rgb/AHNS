"""
基金估算表格打印与图片绘制入口。

表格风格、颜色、水印和 benchmark footer 的实现集中在这里对外暴露。
safe 系列脚本继续复用同一个绘图函数，以保持图片风格一致。
"""

from tools.fund_estimator import (
    build_benchmark_rows,
    format_pct,
    print_fund_estimate_table,
    save_fund_estimate_table_image,
    setup_chinese_font,
)


__all__ = [name for name in globals() if not name.startswith("__")]
