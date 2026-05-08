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

from tools.paths import MARK_IMAGE


RISK_WATERMARK_TEXT = "个人模型估算观察｜仅供个人学习\n不构成任何投资建议"
WATERMARK_ALPHA = 0.15
BRAND_WATERMARK_TEXT = "鱼师AHNS"

# Safe 系列图片是否用星号隐藏基金名称后半段。
# 默认不隐藏；如需公开发布时脱敏，把这里改成 True。
MASK_FUND_NAME_WITH_STAR = False


def mask_fund_name(name: Any, *, enabled: bool | None = None) -> str:
    """
    按开关对公开图片中的基金名称做自适应脱敏。

    短名称保留约一半，长名称保留约 50%，后半段统一用 `*******` 替代。
    这样既能让自己复盘时大致识别，又降低公开平台将图片识别为具体荐基清单的风险。
    """
    text = str(name or "").strip()
    if not text:
        return "基金名称缺失"

    if enabled is None:
        enabled = MASK_FUND_NAME_WITH_STAR
    if not enabled:
        return text

    visible_ratio = 0.65 if len(text) <= 8 else 0.55
    keep_len = max(1, math.ceil(len(text) * visible_ratio))
    return text[:keep_len] + "***"


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


def add_center_image_watermark(
    output_file: str | Path,
    *,
    watermark_file: str | Path = MARK_IMAGE,
    alpha: float = 0.12,
    width_ratio: float = 0.42,
    height_ratio: float = 0.42,
) -> None:
    """
    在 safe 输出图中央叠加一张淡化品牌图片。

    读取失败时只打印警告并跳过，避免因为本地未放置 `cache/mark.jpg`
    影响主流程出图。
    """
    output_path = Path(output_file)
    mark_path = Path(watermark_file)

    if not output_path.exists():
        return

    if not mark_path.exists():
        print(f"[WARN] 图片水印不存在，跳过: {mark_path}", flush=True)
        return

    try:
        image = Image.open(output_path).convert("RGBA")
        mark = Image.open(mark_path).convert("RGBA")
    except Exception as exc:
        print(f"[WARN] 图片水印读取失败，跳过: {mark_path}, 原因: {exc}", flush=True)
        return

    width, height = image.size
    max_w = max(1, int(width * float(width_ratio)))
    max_h = max(1, int(height * float(height_ratio)))
    mark_w, mark_h = mark.size

    if mark_w <= 0 or mark_h <= 0:
        print(f"[WARN] 图片水印尺寸异常，跳过: {mark_path}", flush=True)
        return

    scale = min(max_w / mark_w, max_h / mark_h)
    new_size = (max(1, int(mark_w * scale)), max(1, int(mark_h * scale)))
    resample_filter = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
    mark = mark.resize(new_size, resample_filter)

    alpha_value = max(0, min(255, int(255 * float(alpha))))
    alpha_layer = mark.getchannel("A").point(lambda value: int(value * alpha_value / 255))
    mark.putalpha(alpha_layer)

    overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))
    x = int((width - mark.width) / 2)
    y = int((height - mark.height) / 2)
    overlay.alpha_composite(mark, (x, y))
    Image.alpha_composite(image, overlay).convert("RGB").save(output_path)


def add_brand_text_watermark(
    output_file: str | Path,
    *,
    text: str = BRAND_WATERMARK_TEXT,
    alpha: float = 0.11,
) -> None:
    """
    在 safe 输出图上叠加淡斜向品牌文字水印。

    这层只承担品牌识别，不替代底部风险提示文字，也不恢复大段风险提示水印。
    """
    path = Path(output_file)
    if not path.exists():
        return

    image = Image.open(path).convert("RGBA")
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))
    font_size = 85
    font = get_watermark_font(font_size)
    text_alpha = max(0, min(255, int(255 * float(alpha))))

    temp = Image.new("RGBA", (1, 1), (255, 255, 255, 0))
    temp_draw = ImageDraw.Draw(temp)
    bbox = temp_draw.textbbox((0, 0), text, font=font)
    text_width = math.ceil(bbox[2] - bbox[0])
    text_height = math.ceil(bbox[3] - bbox[1])
    pad = max(22, font_size // 2)

    text_patch = Image.new(
        "RGBA",
        (text_width + pad * 2, text_height + pad * 2),
        (255, 255, 255, 0),
    )
    patch_draw = ImageDraw.Draw(text_patch)
    patch_draw.text(
        (pad - bbox[0], pad - bbox[1]),
        text,
        font=font,
        fill=(0, 0, 0, text_alpha),
    )

    resample_filter = Image.Resampling.BICUBIC if hasattr(Image, "Resampling") else Image.BICUBIC
    text_patch = text_patch.rotate(28, expand=True, resample=resample_filter)

    centers = []
    y_ratios = [0.24, 0.42, 0.60, 0.78]
    for row, y_ratio in enumerate(y_ratios):
        x_ratios = [0.18, 0.50, 0.82] if row % 2 == 0 else [0.34, 0.66]
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


def apply_safe_public_watermarks(output_file: str | Path) -> None:
    """
    Apply the standard public-safe watermark stack.

    Order matters: the centered logo is placed first, then the light diagonal
    brand text is layered over it.  Safe scripts should call this helper instead
    of repeating the two watermark calls.
    """
    add_center_image_watermark(output_file)
    add_brand_text_watermark(output_file)


__all__ = [
    "RISK_WATERMARK_TEXT",
    "WATERMARK_ALPHA",
    "BRAND_WATERMARK_TEXT",
    "MASK_FUND_NAME_WITH_STAR",
    "mask_fund_name",
    "get_watermark_font",
    "add_risk_watermark",
    "add_center_image_watermark",
    "add_brand_text_watermark",
    "apply_safe_public_watermarks",
]
