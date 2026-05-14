"""
safe 系列公开图样式配置。

这个文件只放“怎么画图”的参数，不拉行情、不读缓存、不出图。
后续想微调 safe_haiwai_fund.png、safe_holidays.png、safe_sum_holidays.png
的标题、表格、水印和颜色，优先改这里，不需要再翻绘图函数。

重要说明
--------
1. 配置默认只影响 safe 公开图，不影响详细版调试图。
2. 颜色可以写常见英文色名（如 "red"、"green"、"white"），也可以写
   十六进制色值（如 "#3f4d66"）。
3. Matplotlib 表格里的“行间距”主要由 row_height 和 table_scale_y 控制：
   - row_height 会影响整张图高度，调大后每行更松，图也更高。
   - table_scale_y 只拉伸表格单元格，调大后行更高，但过大可能挤压标题/备注。
4. “列间距”用 column_width_by_name 控制。它不是像素，而是表格内部相对宽度；
   同一张表所有列宽会被 Matplotlib 再归一化，所以建议每次只小幅调整。
"""

from __future__ import annotations

from copy import deepcopy


# 标题样式。
# 影响范围：
# - safe_haiwai_fund.png 的顶部标题
# - safe_holidays.png 的顶部标题
# - safe_sum_holidays.png 的顶部标题
#
# 调参建议：
# - fontsize：标题字号。手机端常用 18-25，太大容易把表格往下挤。
# - color：普通标题文字颜色。
# - highlight_color：safe_haiwai_fund 分段标题里的估值日高亮颜色。
# - fontweight：标题粗细，常用 "normal" / "bold"。
# - daily_gap_ratio/min/max：每日表标题与主表上边缘的距离，按表格高度自适应。
# - cumulative_gap：累计表标题与主表上边缘的固定距离；你反馈间距偏大，
#   这里从原来的约 0.018 收紧到 0.010。
SAFE_TITLE_STYLE = {
    "fontsize": 25,
    "color": "black",
    "highlight_color": "red",
    "fontweight": "bold",
    "daily_gap_ratio": 0.05,
    "daily_gap_min": 0.003,
    "daily_gap_max": 0.008,
    "cumulative_gap": 0.010,
}


# 画布外边距。
# 影响范围：
# - safe_haiwai_fund.png 的图片四周留白
# - safe_sum_holidays.png 的节后第 1 天单日观察图
#
# 这些参数控制的是“图片边缘到内容”的外部留白，不是标题到表格的距离。
# 如果你觉得最上方空白太高，优先调小 daily_top_pad_inches。
# 如果你觉得底部免责声明下面空白太高，优先调小 daily_bottom_pad_inches。
#
# 调参建议：
# - daily_pad_inches：每日图默认外边距。单位是英寸，不是像素；调大会让四周更宽。
# - daily_top_pad_inches：每日图顶部外边距。调小后，图片最上方到标题更近。
# - daily_bottom_pad_inches：每日图底部外边距。调小后，图片最下方到备注更近。
# - daily_left_pad_inches / daily_right_pad_inches：左右外边距。调小后，内容更接近图片左右边。
# - 留空 None 表示沿用 daily_pad_inches；写具体数值可以单独控制某一侧。
# - 推荐范围：顶部 0.02-0.12；底部/左右 0.08-0.18。过小可能让文字贴边。
# - 想恢复某一侧默认观感，可以把对应项改回 daily_pad_inches 的值，或直接写 None。
SAFE_CANVAS_STYLE = {
    "daily_pad_inches": 0.15,
    "daily_top_pad_inches": 0.55,
    "daily_bottom_pad_inches": 1.35,
    "daily_left_pad_inches": 0.15,
    "daily_right_pad_inches": 0.15,
}


# 表格主体样式。
# 影响范围：
# - safe 系列主表
# - safe 系列底部基准表
#
# 调参建议：
# - body_fontsize：主表正文和表头字号。每日图原先约 17，累计图原先约 15。
#   为了保留原观感，下面拆成 daily/cumulative 两套字号。
# - benchmark_fontsize：底部指数/基准表字号。通常与主表一致或小 1 号。
# - header_bg：第一行表头底色。当前已按你的要求从深蓝调浅为 #3f4d66。
# - header_text_color：表头文字色，深色表头建议保持 white。
# - body_bg：普通单元格底色。想要淡米色/淡灰底，可以改这里。
# - figure_bg：整张图画布底色。当前保持白色，适合贴到浅色页面。
# - grid_color：表格线颜色。越浅越克制，过浅会降低可读性。
# - row_height：每行占用的基础高度；调大行距更松、整图更高。
# - table_scale_y：单元格纵向缩放；1.15-1.30 比较稳，过大可能导致裁切。
# - benchmark_table_scale_y：底部基准表的纵向缩放，通常略小于主表。
SAFE_DAILY_TABLE_STYLE = {
    "body_fontsize": 20,
    "benchmark_fontsize": 20,
    "header_bg": "#3f4d66",
    "header_text_color": "white",
    "body_bg": "white",
    "figure_bg": "white",
    "grid_color": "#c9c9c9",
    "row_height": 0.48,
    "table_scale_x": 1.0,
    "table_scale_y": 1.22,
    "benchmark_table_scale_x": 1.0,
    "benchmark_table_scale_y": 1.18,
}

SAFE_CUMULATIVE_TABLE_STYLE = {
    "body_fontsize": 20,
    "benchmark_fontsize": 20,
    "header_bg": "#3f4d66",
    "header_text_color": "white",
    "body_bg": "white",
    "figure_bg": "white",
    "grid_color": "#c9c9c9",
    "row_height": 0.48,
    "table_scale_x": 1.0,
    "table_scale_y": 1.22,
    "benchmark_table_scale_x": 1.0,
    "benchmark_table_scale_y": 1.18,
}


# 涨跌幅颜色。
# 影响范围：
# - 主表的收益/模型观察列
# - 底部基准表的收益/模型观察列
#
# 调参建议：
# - up：正收益颜色。国内习惯红涨，所以默认 red。
# - down：负收益颜色。国内习惯绿跌，所以默认 green。
# - neutral：0、无效数据、无法获取时的颜色。
SAFE_RETURN_COLORS = {
    "up": "red",
    "down": "green",
    "neutral": "black",
}


# 底部合规提示和备注样式。
# 影响范围：
# - “个人模型，数据来源于网络公开资料，不构成任何投资建议”
# - “备注：……”
#
# 调参建议：
# - compliance_fontsize：合规提示字号。累计图原先较大，继续保持 35。
# - footnote_fontsize：备注字号。过小手机端会糊，建议 12-15。
# - color：颜色越浅越不抢主表，但不能浅到看不清。
SAFE_FOOTER_STYLE = {
    "compliance_color": "#2f3b52",
    "compliance_fontsize": 35,
    "compliance_fontweight": "bold",
    "footnote_color": "#666666",
    "daily_footnote_fontsize": 20,
    "cumulative_footnote_fontsize": 20,
}


# 主表列宽。
# 影响范围：
# - safe_haiwai_fund.png
# - safe_sum_holidays.png 的节后第 1 天“单日观察图”
#
# 调参建议：
# - 基金名称太挤：调大“基金名称”，同时适当调小其他列。
# - 收益列太窄：调大“模型估算观察”或“今日预估涨跌幅”。
# - 列宽是相对值，不是像素；每次建议调整 0.01-0.03。
SAFE_DAILY_COLUMN_WIDTHS = {
    "序号": 0.06,
    "基金代码": 0.10,
    "基金名称": 0.37,
    "今日预估涨跌幅": 0.15,
    "模型估算观察": 0.15,
    "模型观察基金信息": 0.18,
    "限购金额": 0.15,
    "估算方式": 0.16,
    "估值日": 0.14,
}


# 累计表列宽。
# 影响范围：
# - safe_holidays.png
# - safe_sum_holidays.png 的节后第 2 天“累计观察图”
#
# 调参建议：
# - safe_holidays.py 已隐藏“有效估值日数”，把空间让给基金名称、收益列和日期列。
# - safe_sum_holidays.py 如果仍保留“有效估值日数”，绘图函数会按存在的列名取宽度。
# - 基金名称太挤：调大“基金名称”，同时适当调小收益列或日期列。
# - 如果标题或列名换成长文案，先调大对应列，再考虑降低 body_fontsize。
SAFE_CUMULATIVE_COLUMN_WIDTHS = {
    "序号": 0.06,
    "基金代码": 0.10,
    "基金名称": 0.47,
    "区间累计预估收益率": 0.19,
    "区间模型估算观察": 0.19,
    "有效估值日数": 0.12,
    "起始估值日": 0.16,
    "结束估值日": 0.16,
    "记录状态": 0.10,
}


# 底部基准表列宽。
# 影响范围：
# - 当基准表列数与主表不同的时候使用。
# - 如果基准表列数与主表相同，绘图函数会优先复用主表列宽，让上下边界对齐。
SAFE_BENCHMARK_COLUMN_WIDTHS = {
    "序号": 0.08,
    "指数名称": 0.42,
    "模型观察": 0.20,
    "基准日或区间": 0.38,
    "指数代码": 0.10,
    "区间模型观察": 0.20,
    "有效估值日数": 0.12,
    "起始估值日": 0.16,
    "结束估值日": 0.16,
}


# safe 图水印样式。
# 影响范围：
# - 居中的 cache/mark.jpg 图片水印
# - 斜向平铺的“鱼师AHNS”文字水印
#
# 调参建议：
# - center_image.alpha：居中 logo 透明度，0 完全不可见，1 完全不透明。
#   常用 0.08-0.16，当前 0.12。
# - center_image.width_ratio / height_ratio：logo 最大宽高占整图比例。
#   0.42 表示最多占图宽/图高的 42%。
# - brand_text.text：斜向文字水印内容。
# - brand_text.font_size：斜向文字水印字号，当前 85。
# - brand_text.alpha：文字透明度，常用 0.06-0.14。
# - brand_text.rotation：文字倾斜角度，当前 28 度。
# - brand_text.color：文字颜色。建议用深灰或黑色配低透明度。
SAFE_WATERMARK_STYLE = {
    "center_image": {
        "alpha": 0.10,
        "width_ratio": 0.60,
        "height_ratio": 0.60,
    },
    "brand_text": {
        "text": "鱼师AHNS",
        "font_size": 105,
        "color": "#000000",
        "alpha": 0.14,
        "rotation": 28,
    },
}


# safe 图中间“场景大字”样式。
# 影响范围：
# - safe_haiwai_afterhours.png：盘后观察图
# - safe_haiwai_fund.png：收盘观察图
# - safe_haiwai_premarket.png：盘前观察图
# - safe_haiwai_intraday.png：盘中观察图
# - safe_haiwai_night.png：夜盘观察图
# - safe_holidays.png：节假日观察图
# - safe_sum_holidays.png：节后补更新观察图
#
# 调参说明：
# - enabled：是否显示这层大字。False 表示这张图不叠加场景字。
# - text：中间展示的文字，可以改成任意中文或空字符串。
# - layout：文字排列方式。
#   "vertical" 表示竖排，例如“收盘观察”会显示为“收/盘/观/察”一列；
#   "horizontal" 表示横排，配合 rotation 可以做斜向大字。
# - font_size：字号，单位是像素。常用 90-160；图越高可以适当越大。
# - fill_color：文字填充颜色，支持英文色名或十六进制，如 "orange"、"#ff7a00"。
# - stroke_color：文字描边颜色。想要纯色字，可以设成和 fill_color 一样，或把 stroke_width 设为 0。
# - stroke_width：描边粗细，单位是像素。常用 0-8；数字越大边越粗。
# - alpha：整层透明度，0 完全不可见，1 完全不透明。竖排场景字常用 0.84-0.92。
# - rotation：旋转角度，单位是度。竖排通常写 0；正数为逆时针，负数为顺时针。
# - char_spacing_px：竖排时每个字之间的额外间距，单位是像素。常用 0-12。
# - x_ratio / y_ratio：按整张图片宽高比例定位“大字中心点”。
#   0.5 表示正中；x_ratio 变大向右，变小向左；y_ratio 变大向下，变小向上。
# - x_offset_px / y_offset_px：在比例定位基础上的像素微调。
#   x_offset_px 正数向右、负数向左；y_offset_px 正数向下、负数向上。
# - 如果不同图高度差异较大，优先用 x_ratio/y_ratio 做大方向定位，再用 offset 微调。
SAFE_SCENE_TEXT_STYLE = {
    "safe_haiwai_afterhours.png": {
        "enabled": True,
        "text": "盘后观察",
        "layout": "vertical",
        "font_size": 200,
        "fill_color": "#1f1f1f",
        "stroke_color": "#dc2626",
        "stroke_width": 7,
        "alpha": 0.90,
        "rotation": 0,
        "char_spacing_px": 2,
        "x_ratio": 0.55,
        "y_ratio": 0.40,
        "x_offset_px": 0,
        "y_offset_px": 0,
    },
    "safe_haiwai_fund.png": {
        "enabled": True,
        "text": "收盘观察",
        "layout": "vertical",
        "font_size": 200,
        "fill_color": "#c2410c",
        "stroke_color": "#fde68a",
        "stroke_width": 7,
        "alpha": 0.90,
        "rotation": 0,
        "char_spacing_px": 2,
        "x_ratio": 0.55,
        "y_ratio": 0.40,
        "x_offset_px": 0,
        "y_offset_px": 0,
    },
    "safe_haiwai_premarket.png": {
        "enabled": True,
        "text": "盘前观察",
        "layout": "vertical",
        "font_size": 200,
        "fill_color": "#6d28d9",
        "stroke_color": "#34d399",
        "stroke_width": 7,
        "alpha": 0.90,
        "rotation": 0,
        "char_spacing_px": 2,
        "x_ratio": 0.55,
        "y_ratio": 0.40,
        "x_offset_px": 0,
        "y_offset_px": 0,
    },
    "safe_haiwai_intraday.png": {
        "enabled": True,
        "text": "盘中观察",
        "layout": "vertical",
        "font_size": 200,
        "fill_color": "#1d4ed8",
        "stroke_color": "#fbbf24",
        "stroke_width": 7,
        "alpha": 0.90,
        "rotation": 0,
        "char_spacing_px": 2,
        "x_ratio": 0.55,
        "y_ratio": 0.40,
        "x_offset_px": 0,
        "y_offset_px": 0,
    },
    "safe_haiwai_night.png": {
        "enabled": True,
        "text": "夜盘观察",
        "layout": "vertical",
        "font_size": 200,
        "fill_color": "#4c1d95",
        "stroke_color": "#a7f3d0",
        "stroke_width": 7,
        "alpha": 0.90,
        "rotation": 0,
        "char_spacing_px": 2,
        "x_ratio": 0.55,
        "y_ratio": 0.40,
        "x_offset_px": 0,
        "y_offset_px": 0,
    },
    "safe_holidays.png": {
        "enabled": True,
        "text": "假期观察",
        "layout": "vertical",
        "font_size": 200,
        "fill_color": "#111827",
        "stroke_color": "#f8fafc",
        "stroke_width": 7,
        "alpha": 0.85,
        "rotation": 0,
        "char_spacing_px": 2,
        "x_ratio": 0.55,
        "y_ratio": 0.40,
        "x_offset_px": 0,
        "y_offset_px": 0,
    },
    "safe_sum_holidays.png": {
        "enabled": True,
        "text": "节后更新",
        "layout": "vertical",
        "font_size": 200,
        "fill_color": "#9f1239",
        "stroke_color": "#fde68a",
        "stroke_width": 7,
        "alpha": 0.90,
        "rotation": 0,
        "char_spacing_px": 2,
        "x_ratio": 0.55,
        "y_ratio": 0.40,
        "x_offset_px": 0,
        "y_offset_px": 0,
    },
}


def safe_daily_table_kwargs() -> dict:
    """返回 safe 每日/单日表格绘图参数；调用方可以继续单独覆盖个别参数。"""
    style = SAFE_DAILY_TABLE_STYLE
    return {
        "title_fontsize": SAFE_TITLE_STYLE["fontsize"],
        "title_color": SAFE_TITLE_STYLE["color"],
        "title_fontweight": SAFE_TITLE_STYLE["fontweight"],
        "title_gap_ratio": SAFE_TITLE_STYLE["daily_gap_ratio"],
        "title_gap_min": SAFE_TITLE_STYLE["daily_gap_min"],
        "title_gap_max": SAFE_TITLE_STYLE["daily_gap_max"],
        "pad_inches": SAFE_CANVAS_STYLE["daily_pad_inches"],
        "top_pad_inches": SAFE_CANVAS_STYLE["daily_top_pad_inches"],
        "bottom_pad_inches": SAFE_CANVAS_STYLE["daily_bottom_pad_inches"],
        "left_pad_inches": SAFE_CANVAS_STYLE["daily_left_pad_inches"],
        "right_pad_inches": SAFE_CANVAS_STYLE["daily_right_pad_inches"],
        "header_bg": style["header_bg"],
        "header_text_color": style["header_text_color"],
        "body_bg": style["body_bg"],
        "figure_bg": style["figure_bg"],
        "grid_color": style["grid_color"],
        "table_fontsize": style["body_fontsize"],
        "benchmark_footer_fontsize": style["benchmark_fontsize"],
        "row_height": style["row_height"],
        "table_scale_x": style["table_scale_x"],
        "table_scale_y": style["table_scale_y"],
        "benchmark_table_scale_x": style["benchmark_table_scale_x"],
        "benchmark_table_scale_y": style["benchmark_table_scale_y"],
        "column_width_by_name": deepcopy(SAFE_DAILY_COLUMN_WIDTHS),
        "benchmark_column_width_by_name": deepcopy(SAFE_BENCHMARK_COLUMN_WIDTHS),
        "up_color": SAFE_RETURN_COLORS["up"],
        "down_color": SAFE_RETURN_COLORS["down"],
        "neutral_color": SAFE_RETURN_COLORS["neutral"],
        "compliance_notice_color": SAFE_FOOTER_STYLE["compliance_color"],
        "compliance_notice_fontsize": SAFE_FOOTER_STYLE["compliance_fontsize"],
        "compliance_notice_fontweight": SAFE_FOOTER_STYLE["compliance_fontweight"],
        "footnote_color": SAFE_FOOTER_STYLE["footnote_color"],
        "footnote_fontsize": SAFE_FOOTER_STYLE["daily_footnote_fontsize"],
    }


def safe_cumulative_table_kwargs() -> dict:
    """返回 safe 节假日/累计表格绘图参数；调用方可以继续单独覆盖个别参数。"""
    style = SAFE_CUMULATIVE_TABLE_STYLE
    return {
        "title_fontsize": SAFE_TITLE_STYLE["fontsize"],
        "title_color": SAFE_TITLE_STYLE["color"],
        "title_fontweight": SAFE_TITLE_STYLE["fontweight"],
        "title_gap": SAFE_TITLE_STYLE["cumulative_gap"],
        "header_bg": style["header_bg"],
        "header_text_color": style["header_text_color"],
        "body_bg": style["body_bg"],
        "figure_bg": style["figure_bg"],
        "grid_color": style["grid_color"],
        "table_fontsize": style["body_fontsize"],
        "benchmark_footer_fontsize": style["benchmark_fontsize"],
        "row_height": style["row_height"],
        "table_scale_x": style["table_scale_x"],
        "table_scale_y": style["table_scale_y"],
        "benchmark_table_scale_x": style["benchmark_table_scale_x"],
        "benchmark_table_scale_y": style["benchmark_table_scale_y"],
        "column_width_by_name": deepcopy(SAFE_CUMULATIVE_COLUMN_WIDTHS),
        "benchmark_column_width_by_name": deepcopy(SAFE_BENCHMARK_COLUMN_WIDTHS),
        "up_color": SAFE_RETURN_COLORS["up"],
        "down_color": SAFE_RETURN_COLORS["down"],
        "neutral_color": SAFE_RETURN_COLORS["neutral"],
        "compliance_notice_color": SAFE_FOOTER_STYLE["compliance_color"],
        "compliance_notice_fontsize": SAFE_FOOTER_STYLE["compliance_fontsize"],
        "compliance_notice_fontweight": SAFE_FOOTER_STYLE["compliance_fontweight"],
        "footnote_color": SAFE_FOOTER_STYLE["footnote_color"],
        "footnote_fontsize": SAFE_FOOTER_STYLE["cumulative_footnote_fontsize"],
    }


def safe_watermark_style() -> dict:
    """返回 safe 公开图水印参数。返回副本，避免调用方误改全局配置。"""
    return deepcopy(SAFE_WATERMARK_STYLE)


def safe_scene_text_style() -> dict:
    """返回 safe 场景大字参数。返回副本，避免调用方误改全局配置。"""
    return deepcopy(SAFE_SCENE_TEXT_STYLE)


__all__ = [
    "SAFE_TITLE_STYLE",
    "SAFE_DAILY_TABLE_STYLE",
    "SAFE_CUMULATIVE_TABLE_STYLE",
    "SAFE_RETURN_COLORS",
    "SAFE_FOOTER_STYLE",
    "SAFE_DAILY_COLUMN_WIDTHS",
    "SAFE_CUMULATIVE_COLUMN_WIDTHS",
    "SAFE_BENCHMARK_COLUMN_WIDTHS",
    "SAFE_WATERMARK_STYLE",
    "SAFE_SCENE_TEXT_STYLE",
    "safe_daily_table_kwargs",
    "safe_cumulative_table_kwargs",
    "safe_watermark_style",
    "safe_scene_text_style",
]
