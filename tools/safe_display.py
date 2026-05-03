"""
safe 系列公开展示工具。

这里集中处理基金名称脱敏和风险提示水印。`safe_fund.py` 与
`safe_holidays.py` 共用同一套规则，避免后续只改一个脚本导致图片口径不一致。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


RISK_WATERMARK_TEXT = "个人模型估算观察｜仅供个人学习\n不构成任何投资建议"
WATERMARK_ALPHA = 0.15


def mask_fund_name(name: Any) -> str:
    """
    对公开图片中的基金名称做自适应脱敏。

    短名称保留约一半，长名称保留约 50%，后半段统一用 `*******` 替代。
    这样既能让自己复盘时大致识别，又降低公开平台将图片识别为具体荐基清单的风险。
    """
    text = str(name or "").strip()
    if not text:
        return "基金名称缺失"

    visible_ratio = 0.80 if len(text) <= 8 else 0.50
    keep_len = max(1, math.ceil(len(text) * visible_ratio))
    return text[:keep_len] + "********"


def get_watermark_font(size: int) -> ImageFont.ImageFont:
    """优先使用 Windows 中文字体，保证水印中文字稳定显示。"""
    font_paths = [
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    for font_path in font_paths:
        path = Path(font_path)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def add_risk_watermark(
    output_file: str | Path,
    *,
    text: str = RISK_WATERMARK_TEXT,
    alpha: float = WATERMARK_ALPHA,
) -> None:
    """
    在已生成表格图上叠加交错风险提示水印。

    原表格绘图函数已经负责 `鱼师AHNS` 品牌水印；这里额外把风险提示放在
    品牌水印之间，形成错开排列，既可见又不遮挡表格主体。
    """
    path = Path(output_file)
    if not path.exists():
        return

    image = Image.open(path).convert("RGBA")
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))
    font_size = max(54, min(82, width // 48))
    font = get_watermark_font(font_size)
    text_alpha = int(255 * alpha)
    line_spacing = max(8, font_size // 5)

    temp = Image.new("RGBA", (1, 1), (255, 255, 255, 0))
    temp_draw = ImageDraw.Draw(temp)
    bbox = temp_draw.multiline_textbbox(
        (0, 0),
        text,
        font=font,
        spacing=line_spacing,
        align="center",
    )
    text_width = math.ceil(bbox[2] - bbox[0])
    text_height = math.ceil(bbox[3] - bbox[1])
    pad = max(28, font_size // 2)

    text_patch = Image.new(
        "RGBA",
        (text_width + pad * 2, text_height + pad * 2),
        (255, 255, 255, 0),
    )
    patch_draw = ImageDraw.Draw(text_patch)
    patch_draw.multiline_text(
        (pad - bbox[0], pad - bbox[1]),
        text,
        font=font,
        fill=(0, 0, 0, text_alpha),
        spacing=line_spacing,
        align="center",
    )

    resample_filter = Image.Resampling.BICUBIC if hasattr(Image, "Resampling") else Image.BICUBIC
    text_patch = text_patch.rotate(28, expand=True, resample=resample_filter)

    centers = []
    y_ratios = [0.23, 0.41, 0.59, 0.77]
    for row, y_ratio in enumerate(y_ratios):
        x_ratios = [0.18, 0.54, 0.90] if row % 2 == 0 else [0.36, 0.72]
        for x_ratio in x_ratios:
            centers.append((int(width * x_ratio), int(height * y_ratio)))

    for cx, cy in centers:
        x = int(cx - text_patch.width / 2)
        y = int(cy - text_patch.height / 2)
        src_left = max(0, -x)
        src_top = max(0, -y)
        src_right = min(text_patch.width, width - x)
        src_bottom = min(text_patch.height, height - y)
        if src_left >= src_right or src_top >= src_bottom:
            continue

        patch_crop = text_patch.crop((src_left, src_top, src_right, src_bottom))
        overlay.alpha_composite(patch_crop, (max(0, x), max(0, y)))

    Image.alpha_composite(image, overlay).convert("RGB").save(path)


__all__ = [
    "RISK_WATERMARK_TEXT",
    "WATERMARK_ALPHA",
    "mask_fund_name",
    "get_watermark_font",
    "add_risk_watermark",
]
