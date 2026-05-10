"""
生成海外基金限额科普图和缓存限额表。

脚本会被 git_main.py 每天调用，但只有北京时间周日实际生成图片；其他日期
只打印跳过原因并正常退出。

本版只优化 output/kepu_xiane.png 科普图：
1. 水印只保留“鱼师AHNS”，并增强可见度；
2. 科普图正文、卡片、提示文字字号整体加大；
3. output/xiane.png 表格图生成逻辑保持原样。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

KEPU_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = KEPU_DIR.parent
for import_path in (PROJECT_ROOT, KEPU_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import first_pic as art
from tools.fund_universe import HAIWAI_FUND_CODES
from tools.paths import (
    FUND_ESTIMATE_CACHE,
    FUND_PURCHASE_LIMIT_CACHE,
    KEPU_XIANE_IMAGE,
    XIANE_IMAGE,
)
from tools.safe_display import mask_fund_name


OUTPUT_KEPU_FILE = KEPU_XIANE_IMAGE
OUTPUT_TABLE_FILE = XIANE_IMAGE
PURCHASE_LIMIT_CACHE_FILE = FUND_PURCHASE_LIMIT_CACHE
FUND_ESTIMATE_CACHE_FILE = FUND_ESTIMATE_CACHE


# ============================================================
# 数据读取与限额解析
# ============================================================

def _normalize_fund_code(value: Any) -> str:
    return str(value).strip().zfill(6)


def _normalize_today(value=None) -> date:
    if value is not None:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except Exception as exc:
            raise ValueError("today 必须是可解析日期，例如 2026-05-02。") from exc

    try:
        return datetime.now(ZoneInfo("Asia/Shanghai")).date()
    except Exception:
        return datetime.now().date()


AMOUNT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(万)?元")


def _parse_limit_amount_yuan(value: Any) -> float | None:
    text = str(value or "").replace(",", "").strip()
    amounts: list[float] = []

    for number_text, wan_unit in AMOUNT_PATTERN.findall(text):
        try:
            amount = float(number_text)
        except Exception:
            continue

        if wan_unit:
            amount *= 10000

        amounts.append(amount)

    return max(amounts) if amounts else None


def _limit_sort_key(limit_text: str, amount_yuan: float | None, code: str) -> tuple[int, float, str]:
    text = str(limit_text or "").strip()

    if "不限购" in text or "开放申购" in text:
        return (0, 0.0, code)

    if "暂停申购" in text:
        return (3, 0.0, code)

    if amount_yuan is not None:
        return (1, -amount_yuan, code)

    return (2, 0.0, code)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def _load_overseas_fund_names() -> dict[str, str]:
    data = _load_json(FUND_ESTIMATE_CACHE_FILE)
    records = data.get("records", {})

    if not isinstance(records, dict):
        return {}

    names: dict[str, str] = {}

    for item in records.values():
        if not isinstance(item, dict):
            continue

        if str(item.get("market_group", "")).strip() != "overseas":
            continue

        code = _normalize_fund_code(item.get("fund_code", ""))
        name = str(item.get("fund_name", "")).strip()

        if code and name:
            names[code] = name

    return names


def _load_limit_rows() -> list[dict[str, Any]]:
    names = _load_overseas_fund_names()
    purchase_cache = _load_json(PURCHASE_LIMIT_CACHE_FILE)
    rows: list[dict[str, Any]] = []

    for code in sorted({_normalize_fund_code(x) for x in HAIWAI_FUND_CODES}):
        item = purchase_cache.get(code, {}) if isinstance(purchase_cache, dict) else {}

        if not isinstance(item, dict):
            item = {}

        limit_text = str(item.get("value") or "暂无记录")
        amount_yuan = _parse_limit_amount_yuan(limit_text)
        fund_name = names.get(code, "缓存中暂无基金名称")

        rows.append(
            {
                "序号": "",
                "基金名称": mask_fund_name(fund_name, enabled=True),
                "限额信息": limit_text,
                "_code": code,
                "_amount_yuan": amount_yuan,
                "_sort_key": _limit_sort_key(limit_text, amount_yuan, code),
            }
        )

    rows.sort(key=lambda row: row["_sort_key"])

    for index, row in enumerate(rows, start=1):
        row["序号"] = str(index)

    return rows


# ============================================================
# kepu_xiane.png 专用精准文字布局工具
# ============================================================

def _text_bbox(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
) -> tuple[int, int, int, int]:
    return draw.textbbox((0, 0), text, font=font)


def _text_size(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
) -> tuple[int, int]:
    bbox = _text_bbox(draw, text, font)
    return int(bbox[2] - bbox[0]), int(bbox[3] - bbox[1])


def _draw_center_text(
    draw: ImageDraw.ImageDraw,
    center_x: int,
    y: int,
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    """
    精准绘制单行居中文字。

    Pillow 的 textbbox 在 Linux + Noto CJK 下可能存在 bbox[0] / bbox[1] 偏移。
    这里抵消偏移，让实际字形外框按视觉中心对齐。
    """
    bbox = _text_bbox(draw, text, font)
    text_w = bbox[2] - bbox[0]

    draw_x = center_x - text_w / 2 - bbox[0]
    draw_y = y - bbox[1]

    draw.text((draw_x, draw_y), text, font=font, fill=fill)


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    """
    按像素宽度折行。
    中文按字符处理；显式换行保留。
    """
    lines: list[str] = []

    for para in text.split("\n"):
        if para == "":
            lines.append("")
            continue

        current = ""

        for char in para:
            candidate = current + char

            if _text_size(draw, candidate, font)[0] <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = char

        if current:
            lines.append(current)

    return lines


def _measure_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    line_gap: int,
) -> tuple[int, list[str], list[tuple[int, int, int, int]]]:
    lines = _wrap_text(draw, text, font, max_width)

    if not lines:
        return 0, [], []

    boxes: list[tuple[int, int, int, int]] = []
    total_h = 0

    for idx, line in enumerate(lines):
        bbox = _text_bbox(draw, line, font)
        boxes.append(bbox)

        line_h = bbox[3] - bbox[1]
        total_h += line_h

        if idx < len(lines) - 1:
            total_h += line_gap

    return total_h, lines, boxes


def _draw_text_in_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    *,
    font_size: int,
    min_font_size: int,
    bold: bool,
    fill: str,
    line_gap: int = 10,
    align: str = "left",
    valign: str = "top",
) -> None:
    """
    在指定区域内绘制文本，并进行真正的视觉居中。
    """
    left, top, right, bottom = box
    max_width = max(10, right - left)
    max_height = max(10, bottom - top)

    chosen_font = art.load_font(font_size, bold=bold)
    chosen_lines: list[str] = []
    chosen_boxes: list[tuple[int, int, int, int]] = []
    chosen_height = 0

    for size in range(font_size, min_font_size - 1, -2):
        font = art.load_font(size, bold=bold)
        height, lines, boxes = _measure_wrapped_text(
            draw,
            text,
            font,
            max_width,
            line_gap,
        )

        chosen_font = font
        chosen_lines = lines
        chosen_boxes = boxes
        chosen_height = height

        if height <= max_height:
            break

    if not chosen_lines:
        return

    y = top

    if valign == "center":
        y = top + max(0, (max_height - chosen_height) // 2)

    for line, bbox in zip(chosen_lines, chosen_boxes):
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        if align == "center":
            visual_left = left + (max_width - text_w) / 2
        elif align == "right":
            visual_left = right - text_w
        else:
            visual_left = left

        draw_x = visual_left - bbox[0]
        draw_y = y - bbox[1]

        draw.text((draw_x, draw_y), line, font=chosen_font, fill=fill)

        y += text_h + line_gap

        if y > bottom:
            break


def _measure_bullets(
    draw: ImageDraw.ImageDraw,
    bullets: list[str],
    font: ImageFont.ImageFont,
    max_width: int,
    line_gap: int,
    gap: int,
) -> tuple[int, list[list[str]], list[list[tuple[int, int, int, int]]]]:
    wrapped: list[list[str]] = []
    wrapped_boxes: list[list[tuple[int, int, int, int]]] = []

    total_h = 0
    text_width = max(10, max_width - 54)

    for idx, text in enumerate(bullets):
        lines = _wrap_text(draw, text, font, text_width)
        boxes = [_text_bbox(draw, line, font) for line in lines]

        wrapped.append(lines)
        wrapped_boxes.append(boxes)

        bullet_h = 0

        for line_idx, bbox in enumerate(boxes):
            line_h = bbox[3] - bbox[1]
            bullet_h += line_h

            if line_idx < len(boxes) - 1:
                bullet_h += line_gap

        total_h += bullet_h

        if idx < len(bullets) - 1:
            total_h += gap

    return total_h, wrapped, wrapped_boxes


def _draw_bullets_in_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    bullets: list[str],
    dot_color: str,
    *,
    font_size: int,
    min_font_size: int = 30,
    bold: bool = True,
    fill: str = art.INK,
    line_gap: int = 6,
    gap: int = 16,
    valign: str = "top",
) -> None:
    """
    在指定区域内绘制项目符号，并整体垂直居中。
    同样抵消字体 bbox 偏移，避免 Linux 字体下文字下沉。
    """
    left, top, right, bottom = box
    max_width = max(10, right - left)
    max_height = max(10, bottom - top)

    chosen_font = art.load_font(font_size, bold=bold)
    chosen_wrapped: list[list[str]] = []
    chosen_wrapped_boxes: list[list[tuple[int, int, int, int]]] = []
    chosen_height = 0

    for size in range(font_size, min_font_size - 1, -2):
        font = art.load_font(size, bold=bold)
        height, wrapped, wrapped_boxes = _measure_bullets(
            draw,
            bullets,
            font,
            max_width,
            line_gap,
            gap,
        )

        chosen_font = font
        chosen_wrapped = wrapped
        chosen_wrapped_boxes = wrapped_boxes
        chosen_height = height

        if height <= max_height:
            break

    y = top

    if valign == "center":
        y = top + max(0, (max_height - chosen_height) // 2)

    for bullet_lines, bullet_boxes in zip(chosen_wrapped, chosen_wrapped_boxes):
        if not bullet_lines:
            continue

        first_box = bullet_boxes[0]
        first_line_h = first_box[3] - first_box[1]
        dot_size = 20

        dot_y = y + max(0, (first_line_h - dot_size) // 2)
        draw.ellipse((left, dot_y, left + dot_size, dot_y + dot_size), fill="#dbeafe")
        draw.ellipse((left + 5, dot_y + 5, left + 15, dot_y + 15), fill=dot_color)

        line_y = y

        for line, bbox in zip(bullet_lines, bullet_boxes):
            line_h = bbox[3] - bbox[1]

            draw.text(
                (left + 54 - bbox[0], line_y - bbox[1]),
                line,
                font=chosen_font,
                fill=fill,
            )

            line_y += line_h + line_gap

            if line_y > bottom:
                return

        y = line_y - line_gap + gap

        if y > bottom:
            return


# ============================================================
# kepu_xiane.png 科普图专用水印和标题栏
# ============================================================

def _draw_strong_brand_watermarks(image: Image.Image) -> None:
    """
    更明显的全页品牌水印：
    - 只显示“鱼师AHNS”
    - 字号较大、透明度较高、斜向平铺
    - 只用于 kepu_xiane.png；xiane.png 表格图不受影响。
    """
    overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))

    text = "鱼师AHNS"
    font = art.load_font(150, bold=True)
    fill = (18, 24, 38, 42)

    patch_w, patch_h = 980, 280
    patch = Image.new("RGBA", (patch_w, patch_h), (255, 255, 255, 0))
    patch_draw = ImageDraw.Draw(patch)

    bbox = patch_draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    patch_draw.text(
        ((patch_w - tw) / 2 - bbox[0], (patch_h - th) / 2 - bbox[1]),
        text,
        font=font,
        fill=fill,
    )

    rotated = patch.rotate(28, expand=True, resample=Image.Resampling.BICUBIC)

    step_x = 700
    step_y = 430

    for row, y in enumerate(range(280, art.HEIGHT - 80, step_y)):
        offset_x = 0 if row % 2 == 0 else 320
        for x in range(-260 + offset_x, art.WIDTH + 260, step_x):
            overlay.alpha_composite(rotated, (x, y))

    image.alpha_composite(overlay)


def _draw_section_shell(
    draw: ImageDraw.ImageDraw,
    top: int,
    height: int,
    title: str,
) -> tuple[int, int, int, int]:
    """
    与 first_pic.draw_section_shell 保持同样结构，只放大章节标题。
    """
    left, right = 150, art.WIDTH - 150
    bottom = top + height

    art.rounded(draw, (left, top, right, bottom), 38, art.CARD_BG, art.LINE, 2)
    draw.rounded_rectangle((left, top, right, top + 130), radius=38, fill=art.NAVY)
    draw.rectangle((left, top + 72, right, top + 130), fill=art.NAVY)

    section_font = art.load_font(66, bold=True)
    draw.text((left + 90, top + 27), title, font=section_font, fill="white")

    return left, top + 130, right, bottom


# ============================================================
# kepu_xiane.png 科普图绘制
# ============================================================

def _draw_title(draw: ImageDraw.ImageDraw, title: str, subtitle: str, today: date) -> None:
    font_title = art.load_font(124, bold=True)
    font_subtitle = art.load_font(62)

    _draw_center_text(
        draw,
        art.WIDTH // 2,
        45,
        f"北京时间：{today.strftime('%Y-%m-%d')}",
        font_title,
        art.INK,
    )

    _draw_center_text(
        draw,
        art.WIDTH // 2,
        188,
        title,
        font_title,
        art.INK,
    )

    _draw_center_text(
        draw,
        art.WIDTH // 2,
        330,
        subtitle,
        font_subtitle,
        art.MUTED,
    )


def _draw_footer(draw: ImageDraw.ImageDraw, top: int = 3015) -> None:
    footer_box = (150, top, art.WIDTH - 150, top + 130)

    art.rounded(draw, footer_box, 28, "#fffdf8", "#e7d0a3", 2)

    _draw_text_in_box(
        draw,
        (footer_box[0] + 50, footer_box[1] + 20, footer_box[2] - 50, footer_box[3] - 20),
        "仅供个人学习记录，不构成任何投资建议；具体申购规则以基金公告和销售平台展示为准。",
        font_size=72,
        min_font_size=48,
        bold=True,
        fill=art.RED,
        align="center",
        valign="center",
        line_gap=8,
    )

    signature = "鱼师AHNS · 个人公开数据建模复盘"
    sw, _ = art.text_size(draw, signature, art.FONT_SIGNATURE)
    draw.text((art.WIDTH - 150 - sw, top + 145), signature, font=art.FONT_SIGNATURE, fill="#7b8796")


def _draw_quota_source_section(draw: ImageDraw.ImageDraw) -> None:
    left, content_top, right, section_bottom = _draw_section_shell(
        draw,
        440,
        710,
        "1｜限额是什么意思？",
    )

    box_y = content_top + 62
    box_w, box_h = 650, 178
    gap = 155

    xs = [
        left + 140,
        left + 140 + box_w + gap,
        left + 140 + (box_w + gap) * 2,
    ]

    items = [
        ("每日限额", "一天内最多可以申购多少", art.BLUE),
        ("大额限制", "小额可能可以，大额可能不行", art.GOLD),
        ("暂停申购", "暂时不能买入或不能追加", art.GREEN),
    ]

    for idx, (title, sub, color) in enumerate(items):
        x = xs[idx]

        art.rounded(draw, (x, box_y, x + box_w, box_y + box_h), 28, art.SOFT_CARD, art.LINE, 2)
        draw.rounded_rectangle((x, box_y, x + box_w, box_y + 18), radius=12, fill=color)

        _draw_text_in_box(
            draw,
            (x + 35, box_y + 32, x + box_w - 35, box_y + box_h - 16),
            f"{title}\n{sub}",
            font_size=64,
            min_font_size=42,
            bold=True,
            fill=art.INK,
            align="center",
            valign="center",
            line_gap=12,
        )

        if idx < len(items) - 1:
            art.draw_arrow(draw, x + box_w + 42, box_y + box_h // 2, xs[idx + 1] - 45)

    note_box = (left + 120, box_y + box_h + 58, right - 120, section_bottom - 55)
    art.rounded(draw, note_box, 28, "#ffffff", art.LINE, 2)

    bullets = [
        "限额不是收益判断，它只是基金申购时的一条操作规则。",
        "同一只基金在不同平台、不同日期看到的限额，可能会有变化。",
        "看到限额时，重点是先确认“还能不能申购、最多能申购多少”。",
    ]

    _draw_bullets_in_box(
        draw,
        (note_box[0] + 70, note_box[1] + 34, note_box[2] - 60, note_box[3] - 34),
        bullets,
        art.BLUE,
        font_size=60,
        min_font_size=46,
        gap=14,
        valign="center",
    )


def _draw_limit_rule_section(draw: ImageDraw.ImageDraw) -> None:
    left, content_top, right, section_bottom = _draw_section_shell(
        draw,
        1220,
        820,
        "2｜为什么海外基金更容易限额？",
    )

    card_gap = 70
    card_w = int((right - left - 240 - card_gap * 2) / 3)
    card_h = section_bottom - content_top - 130
    card_y = content_top + 65
    start_x = left + 120

    cards = [
        (
            "要换成外币",
            art.BLUE,
            [
                "海外基金通常要把人民币换成外币",
                "换汇和额度安排会影响申购节奏",
                "不是基金好坏的判断",
            ],
        ),
        (
            "要买海外资产",
            art.GOLD,
            [
                "海外市场交易时间不同",
                "交易通道和流动性也会影响管理",
                "节假日还可能不同步",
            ],
        ),
        (
            "要控制规模",
            art.RED,
            [
                "规模变化太快会增加管理难度",
                "基金公司可能先限制新增申购",
                "具体以公告为准",
            ],
        ),
    ]

    for idx, (title, color, bullets) in enumerate(cards):
        x = start_x + idx * (card_w + card_gap)

        art.rounded(draw, (x, card_y, x + card_w, card_y + card_h), 30, art.SOFT_CARD, art.LINE, 2)

        art.rounded(draw, (x + 34, card_y + 38, x + card_w - 34, card_y + 122), 28, color, None)

        _draw_text_in_box(
            draw,
            (x + 52, card_y + 40, x + card_w - 52, card_y + 118),
            title,
            font_size=64,
            min_font_size=46,
            bold=True,
            fill="white",
            align="center",
            valign="center",
        )

        _draw_bullets_in_box(
            draw,
            (x + 62, card_y + 168, x + card_w - 42, card_y + card_h - 42),
            bullets,
            color,
            font_size=55,
            min_font_size=42,
            gap=20,
            valign="center",
        )


def _draw_open_logic_section(draw: ImageDraw.ImageDraw) -> None:
    left, content_top, right, section_bottom = _draw_section_shell(
        draw,
        2110,
        800,
        "3｜放开限额怎么看？",
    )

    inner = (left + 120, content_top + 70, right - 120, section_bottom - 145)
    art.rounded(draw, inner, 30, "#ffffff", art.LINE, 2)

    bullets = [
        "限额变宽，通常只说明申购管理状态发生变化，不代表基金更好。",
        "可能是额度更充足、规模压力下降，也可能是基金公司调整了运营安排。",
        "它不是收益信号；能不能申购、限额多少，最终都以公告和销售平台展示为准。",
    ]

    _draw_bullets_in_box(
        draw,
        (inner[0] + 80, inner[1] + 54, inner[2] - 70, inner[3] - 50),
        bullets,
        art.GREEN,
        font_size=60,
        min_font_size=46,
        gap=20,
        valign="center",
    )

    _draw_footer(draw, top=2985)


def build_kepu_image(today: date) -> Image.Image:
    image = Image.new("RGBA", (art.WIDTH, art.HEIGHT), art.BG)

    _draw_strong_brand_watermarks(image)

    draw = ImageDraw.Draw(image)

    _draw_title(
        draw,
        "海外基金为什么会限额？",
        "看懂限额数字、申购规则和额度变化",
        today,
    )
    _draw_quota_source_section(draw)
    _draw_limit_rule_section(draw)
    _draw_open_logic_section(draw)

    return image.convert("RGB")


def build_table_image(today: date) -> Image.Image:
    """使用 matplotlib 绘制限额表格，保持与 safe 系列一致的风格。"""
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    import pandas as pd

    candidate_font_paths = [
        # Windows
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\simkai.ttf",
        r"C:\Windows\Fonts\Deng.ttf",
        r"C:\Windows\Fonts\Dengb.ttf",

        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/Library/Fonts/Arial Unicode.ttf",

        # Linux
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]

    for font_path in candidate_font_paths:
        fp = Path(font_path)
        if fp.exists():
            try:
                font_manager.fontManager.addfont(str(fp))
            except Exception:
                pass

    candidate_font_names = [
        "Microsoft YaHei",
        "SimHei",
        "SimSun",
        "KaiTi",
        "DengXian",
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "WenQuanYi Zen Hei",
        "PingFang SC",
        "Heiti SC",
        "Arial Unicode MS",
    ]

    available_font_names = {font.name for font in font_manager.fontManager.ttflist}

    chosen_font = None
    for name in candidate_font_names:
        if name in available_font_names:
            chosen_font = name
            break

    if chosen_font is not None:
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = [
            chosen_font,
            *candidate_font_names,
            "DejaVu Sans",
        ]
    else:
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = [
            *candidate_font_names,
            "DejaVu Sans",
        ]

    plt.rcParams["axes.unicode_minus"] = False

    rows = _load_limit_rows()
    df = pd.DataFrame(rows)

    fig, ax = plt.subplots(figsize=(12.5, len(df) * 0.55 + 1.8))
    ax.axis("off")

    header_bg = "#2f3b52"
    header_text_color = "white"
    grid_color = "#d9d9d9"
    watermark_text = "鱼师AHNS"
    watermark_alpha = 0.15
    watermark_fontsize = 32
    watermark_rotation = 28
    watermark_color = "#050505"

    table = ax.table(
        cellText=df[["序号", "基金名称", "限额信息"]].values,
        colLabels=["序号", "基金名称", "限额信息"],
        cellLoc="center",
        colLoc="center",
        bbox=[0.02, 0.15, 0.96, 0.78],
        zorder=2,
    )

    table.auto_set_font_size(False)
    table.set_fontsize(17)
    table.scale(1.0, 1.22)

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(grid_color)
        cell.set_linewidth(0.8)

        if row == 0:
            cell.set_facecolor(header_bg)
            cell.set_text_props(color=header_text_color, weight="bold")
        else:
            cell.set_facecolor("white")

            if col == 2:
                value = df.iloc[row - 1]["限额信息"]

                if value == "暂无记录":
                    cell.get_text().set_color("#667085")
                else:
                    cell.get_text().set_color("#2f65a7")
                    cell.get_text().set_weight("bold")

    col_widths = [0.06, 0.57, 0.35]
    for (row, col), cell in table.get_celld().items():
        if col < len(col_widths):
            cell.set_width(col_widths[col])

    table_left, table_bottom, table_width, table_height = 0.02, 0.15, 0.94, 0.63
    for r in range(5):
        for c in range(4):
            x = table_left + table_width * (c + 0.5) / 4.0
            y = table_bottom + table_height * (r + 0.5) / 5.0
            ax.text(
                x,
                y,
                watermark_text,
                transform=ax.transAxes,
                fontsize=watermark_fontsize,
                color=watermark_color,
                alpha=watermark_alpha,
                ha="center",
                va="center",
                rotation=watermark_rotation,
                zorder=3,
            )

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()

    bbox_disp = table.get_window_extent(renderer=renderer)
    bbox_fig = bbox_disp.transformed(fig.transFigure.inverted())

    table_top = bbox_fig.y1
    table_bottom = bbox_fig.y0
    table_height = max(table_top - table_bottom, 0.01)

    title_gap = max(
        min(table_height * 0.06, 0.025),
        0.012,
    )

    title_y = min(table_top + title_gap, 0.985)
    fig.text(
        0.5,
        title_y,
        f"海外基金限额信息表  {today.strftime('%Y-%m-%d %H:%M:%S')}",
        ha="center",
        va="bottom",
        fontsize=20,
        color="black",
        fontweight="bold",
    )

    subtitle_y = title_y - 0.02
    fig.text(
        0.5,
        subtitle_y,
        "个人整理学习内容，所有信息来源于公开网络；限额信息具体以基金公告为准。",
        ha="center",
        va="bottom",
        fontsize=15,
        color="#666666",
    )

    bottom_block_y = max(
        table_bottom - max(min(table_height * 0.030, 0.035), 0.012),
        0.030,
    )
    fig.text(
        0.52,
        bottom_block_y,
        "数据来源于报告披露，仅供学习记录，不构成投资建议；最终以基金公司更新为准。",
        ha="center",
        va="bottom",
        fontsize=15,
        color="#666666",
    )

    compliance_y = bottom_block_y - 0.025
    fig.text(
        0.52,
        compliance_y,
        "个人模型，数据来源于网络公开资料，不构成任何投资建议",
        ha="center",
        va="bottom",
        fontsize=20,
        color="#2f3b52",
        fontweight="bold",
    )

    output_file = str(OUTPUT_TABLE_FILE)
    output_path = Path(output_file)
    if output_path.parent:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    fig.savefig(
        output_file,
        dpi=180,
        bbox_inches="tight",
        pad_inches=0.12,
    )
    plt.close(fig)

    from tools.safe_display import add_risk_watermark

    add_risk_watermark(output_file)

    image = Image.open(output_file)
    return image.convert("RGB")


# ============================================================
# 运行入口
# ============================================================

def run(today=None) -> bool:
    today_date = _normalize_today(today)

    OUTPUT_KEPU_FILE.parent.mkdir(parents=True, exist_ok=True)

    build_kepu_image(today_date).save(OUTPUT_KEPU_FILE, optimize=True, compress_level=9)
    print(f"海外基金限额科普图已生成: {OUTPUT_KEPU_FILE.resolve()}")

    if today_date.weekday() == 6:  # 6 表示周日，0 表示周一
        OUTPUT_TABLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        build_table_image(today_date)
        print(f"海外基金限额表格图已生成: {OUTPUT_TABLE_FILE.resolve()}")
    else:
        print(f"{today_date.isoformat()} 不是北京时间周日，跳过海外基金限额表格图生成。")

    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="每周日生成海外基金限额科普图和限额表格图")
    parser.add_argument(
        "--today",
        default=None,
        help="用于测试的北京时间日期，例如 2026-05-03；默认使用今天。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(today=args.today)


if __name__ == "__main__":
    main()
