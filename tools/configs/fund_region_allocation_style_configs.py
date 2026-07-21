"""海外基金股票地区分布图的竖版研究报告样式配置。

只影响 ``fund_region_allocation.py`` 输出的地区分布图，不会改变晨星原始数据、
地区分类、权重计算或基金分页顺序。边距和尺寸单位均为像素，适合直接按发布
平台的安全区要求调整。
"""

from __future__ import annotations


FUND_REGION_ALLOCATION_IMAGE_STYLE = {
    # 图片基础尺寸与抖音/iPhone 顶部安全区。
    "canvas_width_px": 1080,
    "top_margin_px": 120,
    "bottom_margin_px": 56,
    "left_margin_px": 42,
    "right_margin_px": 42,
    "export_dpi": 300,
    # 固定按基金池原顺序分页；7 只可兼顾图表密度与小字号可读性。
    "funds_per_page": 7,
    # 卡片布局。基金名称自动换行时会按实际行数增加卡片高度。
    "card_height_px": 198,
    # 卡片之间的留白；收紧外部间距，但不压缩卡片内文字与条形图行距。
    "card_gap_px": 10,
    "card_padding_px": 18,
    "card_corner_radius_px": 14,
    "long_name_line_height_px": 29,
    # 卡片内部的纵向间距。长名称会自动增加卡片高度，不会压缩正文。
    "title_to_bar_gap_px": 8,
    "bar_to_primary_gap_px": 8,
    "primary_to_detail_gap_px": 4,
    "primary_line_spacing_px": 5,
    "detail_line_spacing_px": 4,
    "bar_height_px": 32,
    "bar_label_min_pct": 8.0,
    # 居中鱼师图像水印：绘制在卡片背景之上、数据文字和条形图之下。
    "logo_width_ratio": 0.36,
    "logo_opacity": 0.10,
    # 所有文字字号集中在这里，便于后续按手机端效果微调。
    "font_sizes": {
        "title": 40,
        "subtitle": 22,
        "legend": 18,
        "page": 19,
        "code": 25,
        "fund_name": 26,
        "fund_name_min": 18,
        "report_date": 18,
        "bar_label": 18,
        "primary": 19,
        "primary_min": 16,
        "subregion": 16,
        "subregion_min": 14,
        "footer": 22,
        "author": 18,
    },
    # 页面保持白底、弱边框和无阴影的研究报告风格。
    "colors": {
        "background": "#FFFFFF",
        "title": "#0F172A",
        "secondary_text": "#475569",
        "muted_text": "#64748B",
        "body_text": "#1E293B",
        "fund_code": "#1D4ED8",
        "card_background": "#FFFFFF",
        "card_alternate_background": "#FBFCFE",
        "card_border": "#E2E8F0",
        "bar_background": "#E9EEF4",
        "footer": "#475569",
        "author": "#94A3B8",
    },
}


__all__ = ["FUND_REGION_ALLOCATION_IMAGE_STYLE"]
