"""基金前十大持仓变化图的竖屏发布样式配置。

该配置只影响 ``fund_holding_change.py`` 生成的持仓变化图，不影响收盘、
盘前、盘中、盘后、夜盘或 RSI 图片。边距单位均为像素，适合直接按短视频
平台的遮挡区域进行微调。
"""

from __future__ import annotations


HOLDING_CHANGE_IMAGE_STYLE = {
    # 1080px 是抖音/手机竖图常用宽度；内部文字和表格会随宽度等比例放大。
    "canvas_width_px": 1080,
    # iPhone 灵动岛、抖音顶部控件的安全区。标题会从该位置之后开始绘制。
    "top_margin_px": 120,
    # 其余三边独立控制，避免调整顶部安全区时影响表格和风险提示的位置。
    "bottom_margin_px": 48,
    "left_margin_px": 42,
    "right_margin_px": 42,
    # PNG 是无损格式；DPI 是导出元数据，实际清晰度主要由上面的像素宽度决定。
    "export_dpi": 300,
}


__all__ = ["HOLDING_CHANGE_IMAGE_STYLE"]
