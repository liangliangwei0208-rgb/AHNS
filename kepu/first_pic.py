"""
kepu/first_pic.py

用 Pillow 生成抖音图集第一张说明图：
output/first_pic.png

本脚本只负责绘制固定说明图，不读取行情、不访问网络、不依赖缓存。
"""

from __future__ import annotations
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WIDTH = 2720
HEIGHT = 3200
OUTPUT_FILE = PROJECT_ROOT / "output" / "first_pic.png"

BG = "#f4f7fb"
NAVY = "#2f3f5c"
INK = "#111827"
MUTED = "#667085"
LINE = "#d8e0ec"
CARD_BG = "#ffffff"
SOFT_CARD = "#f6f8fb"
BLUE = "#2f65a7"
GOLD = "#d5a035"
GREEN = "#2b855f"
RED = "#be3b3b"


def load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """优先使用 Windows 中文字体，保证中文在本地绘图中稳定显示。"""
    if bold:
        candidates = [
            r"C:\Windows\Fonts\msyhbd.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\simsun.ttc",
        ]
    else:
        candidates = [
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\simsun.ttc",
        ]

    for font_path in candidates:
        path = Path(font_path)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except Exception:
                pass

    return ImageFont.load_default()


FONT_TITLE = load_font(118, bold=True)
FONT_SUBTITLE = load_font(52)
FONT_TIME = load_font(44, bold=True)
FONT_SECTION = load_font(58, bold=True)
FONT_BOX_TITLE = load_font(52, bold=True)
FONT_BOX_SUB = load_font(44)
FONT_BODY = load_font(43, bold=True)
FONT_SMALL = load_font(36)
FONT_CARD_TITLE = load_font(44, bold=True)
FONT_CARD_BODY = load_font(42, bold=True)
FONT_FOOTER = load_font(82, bold=True)
FONT_SIGNATURE = load_font(34)


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return int(bbox[2] - bbox[0]), int(bbox[3] - bbox[1])


def draw_center_text(
    draw: ImageDraw.ImageDraw,
    center_x: int,
    y: int,
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    w, _ = text_size(draw, text, font)
    draw.text((center_x - w / 2, y), text, font=font, fill=fill)


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    """按像素宽度折行，中文按字符处理，英文连续片段按宽度兜底。"""
    lines: list[str] = []
    current = ""

    for char in text:
        if char == "\n":
            if current:
                lines.append(current)
                current = ""
            continue

        candidate = current + char
        if text_size(draw, candidate, font)[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = char

    if current:
        lines.append(current)

    return lines


def draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
    max_width: int,
    line_gap: int = 10,
    align: str = "left",
) -> int:
    x, y = xy
    lines = wrap_text(draw, text, font, max_width)

    for line in lines:
        w, h = text_size(draw, line, font)
        if align == "center":
            tx = x + (max_width - w) / 2
        else:
            tx = x
        draw.text((tx, y), line, font=font, fill=fill)
        y += h + line_gap

    return y


def measure_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    line_gap: int = 10,
) -> tuple[int, list[str]]:
    lines = wrap_text(draw, text, font, max_width)
    if not lines:
        return 0, []

    height = 0
    for idx, line in enumerate(lines):
        _, line_h = text_size(draw, line, font)
        height += line_h
        if idx < len(lines) - 1:
            height += line_gap

    return height, lines


def draw_text_in_box(
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
    """在指定区域内自适应字号和换行，避免说明文字跑出卡片。"""
    left, top, right, bottom = box
    max_width = max(10, right - left)
    max_height = max(10, bottom - top)

    chosen_font = load_font(font_size, bold=bold)
    chosen_lines: list[str] = []
    chosen_height = 0
    for size in range(font_size, min_font_size - 1, -2):
        font = load_font(size, bold=bold)
        height, lines = measure_wrapped_text(draw, text, font, max_width, line_gap)
        if height <= max_height:
            chosen_font = font
            chosen_lines = lines
            chosen_height = height
            break
        chosen_font = font
        chosen_lines = lines
        chosen_height = height

    y = top
    if valign == "center":
        y = top + max(0, (max_height - chosen_height) // 2)

    for line in chosen_lines:
        w, h = text_size(draw, line, chosen_font)
        x = left
        if align == "center":
            x = left + (max_width - w) / 2
        draw.text((x, y), line, font=chosen_font, fill=fill)
        y += h + line_gap
        if y > bottom:
            break


def measure_bullets(
    draw: ImageDraw.ImageDraw,
    bullets: list[str],
    font: ImageFont.ImageFont,
    max_width: int,
    line_gap: int,
    gap: int,
) -> tuple[int, list[list[str]]]:
    wrapped: list[list[str]] = []
    height = 0
    text_width = max(10, max_width - 48)

    for idx, text in enumerate(bullets):
        lines = wrap_text(draw, text, font, text_width)
        wrapped.append(lines)
        bullet_height = 0
        for line_idx, line in enumerate(lines):
            _, line_h = text_size(draw, line, font)
            bullet_height += line_h
            if line_idx < len(lines) - 1:
                bullet_height += line_gap
        height += bullet_height
        if idx < len(bullets) - 1:
            height += gap

    return height, wrapped


def draw_bullets_in_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    bullets: list[str],
    dot_color: str,
    *,
    font_size: int,
    min_font_size: int = 30,
    bold: bool = True,
    fill: str = INK,
    line_gap: int = 6,
    gap: int = 16,
    valign: str = "top",
) -> None:
    """在指定内容框内绘制项目符号；字号会随可用高度自动收缩。"""
    left, top, right, bottom = box
    max_width = max(10, right - left)
    max_height = max(10, bottom - top)

    chosen_font = load_font(font_size, bold=bold)
    chosen_wrapped: list[list[str]] = []
    chosen_height = 0
    for size in range(font_size, min_font_size - 1, -2):
        font = load_font(size, bold=bold)
        height, wrapped = measure_bullets(draw, bullets, font, max_width, line_gap, gap)
        if height <= max_height:
            chosen_font = font
            chosen_wrapped = wrapped
            chosen_height = height
            break
        chosen_font = font
        chosen_wrapped = wrapped
        chosen_height = height

    y = top
    if valign == "center":
        y = top + max(0, (max_height - chosen_height) // 2)

    for bullet_lines in chosen_wrapped:
        dot_y = y + 12
        draw.ellipse((left, dot_y, left + 18, dot_y + 18), fill="#dbeafe")
        draw.ellipse((left + 5, dot_y + 5, left + 13, dot_y + 13), fill=dot_color)

        line_y = y
        for line in bullet_lines:
            _, line_h = text_size(draw, line, chosen_font)
            draw.text((left + 48, line_y), line, font=chosen_font, fill=fill)
            line_y += line_h + line_gap
            if line_y > bottom:
                return

        y = line_y - line_gap + gap
        if y > bottom:
            return


def rounded(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    radius: int,
    fill: str,
    outline: str | None = None,
    width: int = 1,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def draw_arrow(draw: ImageDraw.ImageDraw, x1: int, y: int, x2: int, color: str = LINE) -> None:
    draw.line((x1, y, x2 - 26, y), fill=color, width=8)
    draw.polygon([(x2 - 26, y - 20), (x2 - 26, y + 20), (x2 + 8, y)], fill=color)


def draw_watermarks(image: Image.Image) -> None:
    """绘制轻水印，放在内容卡片下方，不影响正文阅读。"""
    overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))
    wm_specs = [
        ("鱼师AHNS", load_font(90, bold=True), (22, 30, 45, 22)),
        ("个人模型预估｜仅供学习", load_font(58, bold=True), (22, 30, 45, 20)),
    ]

    for text, font, fill in wm_specs:
        patch_draw_img = Image.new("RGBA", (900, 220), (255, 255, 255, 0))
        patch_draw = ImageDraw.Draw(patch_draw_img)
        bbox = patch_draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        patch_draw.text(((900 - tw) / 2, (220 - th) / 2), text, font=font, fill=fill)
        rotated = patch_draw_img.rotate(28, expand=True, resample=Image.Resampling.BICUBIC)

        for y in range(420, HEIGHT - 120, 520):
            offset = 0 if (y // 520) % 2 == 0 else 360
            for x in range(-260 + offset, WIDTH + 260, 780):
                overlay.alpha_composite(rotated, (x, y))

    image.alpha_composite(overlay)


def draw_title(draw: ImageDraw.ImageDraw) -> None:
    time_text = "北京时间：" + datetime.now().strftime("%Y-%m-%d %H:%M")
    draw_center_text(draw, WIDTH // 2, 45, time_text, FONT_TITLE, INK)
    draw_center_text(draw, WIDTH // 2, 185, "基金预估图怎么看？", FONT_TITLE, INK)
    draw_center_text(draw, WIDTH // 2, 325, "个人公开数据建模复盘，不是净值预告", FONT_SUBTITLE, MUTED)


def draw_section_shell(
    draw: ImageDraw.ImageDraw,
    top: int,
    height: int,
    title: str,
) -> tuple[int, int, int, int]:
    left, right = 150, WIDTH - 150
    bottom = top + height
    rounded(draw, (left, top, right, bottom), 38, CARD_BG, LINE, 2)
    draw.rounded_rectangle((left, top, right, top + 130), radius=38, fill=NAVY)
    draw.rectangle((left, top + 72, right, top + 130), fill=NAVY)
    draw.text((left + 90, top + 32), title, font=FONT_SECTION, fill="white")
    return left, top + 130, right, bottom


def draw_top_time_section(draw: ImageDraw.ImageDraw) -> None:
    left, content_top, right, section_bottom = draw_section_shell(
        draw,
        440,
        720,
        "1｜海外基金估值时间点为什么不同？",
    )

    box_w, box_h = 650, 150
    box_y = content_top + 45
    xs = [left + 120, left + 885, left + 1650]
    items = [
        ("北京时间发图日", "你看到图片的日期"),
        ("海外市场交易日", "常参考最近有效收盘"),
        ("基金公告净值日", "基金公司最终披露"),
    ]

    for x, (title, sub) in zip(xs, items):
        rounded(draw, (x, box_y, x + box_w, box_y + box_h), 26, SOFT_CARD, LINE, 2)
        draw_center_text(draw, x + box_w // 2, box_y + 35, title, FONT_BOX_TITLE, INK)
        draw_center_text(draw, x + box_w // 2, box_y + 92, sub, FONT_BOX_SUB, MUTED)

    arrow_y = box_y + box_h // 2
    draw_arrow(draw, xs[0] + box_w + 40, arrow_y, xs[1] - 35)
    draw_arrow(draw, xs[1] + box_w + 40, arrow_y, xs[2] - 35)

    bullet_top = box_y + box_h + 45
    bullet_box = (left + 110, bullet_top, right - 110, section_bottom - 55)
    rounded(draw, bullet_box, 26, "#ffffff", LINE, 2)
    bullets = [
        "海外/QDII基金受境外市场收盘、汇率和基金公司公告节奏影响",
        "北京时间当天看到的观察值，可能对应海外市场上一交易日或最近有效交易日",
        "节假日、周末、海外或国内市场休市时，估值日期可能不同步",
    ]
    draw_bullets_in_box(
        draw,
        (bullet_box[0] + 70, bullet_box[1] + 34, bullet_box[2] - 50, bullet_box[3] - 34),
        bullets,
        BLUE,
        font_size=48,
        min_font_size=38,
        gap=10,
        valign="center",
    )


def draw_bullets(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    bullets: list[str],
    dot_color: str,
    font: ImageFont.ImageFont,
    max_width: int,
    gap: int = 18,
) -> int:
    for text in bullets:
        draw.ellipse((x, y + 12, x + 18, y + 30), fill="#dbeafe")
        draw.ellipse((x + 5, y + 17, x + 13, y + 25), fill=dot_color)
        y = draw_wrapped_text(draw, (x + 48, y), text, font, INK, max_width - 48, line_gap=6)
        y += gap
    return y


def draw_mid_basis_section(draw: ImageDraw.ImageDraw) -> None:
    left, content_top, right, section_bottom = draw_section_shell(
        draw,
        1235,
        780,
        "2｜预估收益率主要依据什么？",
    )

    inner_left, inner_right = left + 150, right - 150
    gap = 80
    box_w = int((inner_right - inner_left - gap * 3) / 4)
    box_h = 165
    box_y = content_top + 70
    items = [
        ("季度披露", "前十大持仓股", BLUE),
        ("相关指数 /", "ETF代理", GOLD),
        ("汇率等", "公开信息", BLUE),
        ("复合估算", "模型观察", GREEN),
    ]

    for idx, (line1, line2, color) in enumerate(items):
        x = inner_left + idx * (box_w + gap)
        rounded(draw, (x, box_y, x + box_w, box_y + box_h), 22, SOFT_CARD, LINE, 2)
        draw.rounded_rectangle((x, box_y, x + box_w, box_y + 18), radius=12, fill=color)
        draw_text_in_box(
            draw,
            (x + 20, box_y + 40, x + box_w - 20, box_y + 92),
            line1,
            font_size=54,
            min_font_size=42,
            bold=True,
            fill=INK,
            align="center",
            valign="center",
        )
        draw_text_in_box(
            draw,
            (x + 20, box_y + 94, x + box_w - 20, box_y + 150),
            line2,
            font_size=54,
            min_font_size=42,
            bold=True,
            fill=INK,
            align="center",
            valign="center",
        )
        if idx < len(items) - 1:
            draw_arrow(draw, x + box_w + 38, box_y + box_h // 2, x + box_w + gap - 30)

    note_top = box_y + box_h + 55
    note_box = (left + 110, note_top, right - 110, section_bottom - 55)
    rounded(draw, note_box, 28, "#fff8eb", "#e8cf9e", 2)

    title_text = "估算限制"
    limit_text = "部分基金衔接国内全球ETF，国内不开盘时无法形成新的可用估值，只能等待市场或公告更新。"
    text_left = note_box[0] + 170
    text_right = note_box[2] - 55
    text_width = text_right - text_left
    title_font = load_font(54, bold=True)
    body_font = load_font(47, bold=True)
    body_line_gap = 12
    title_body_gap = 22
    title_h = text_size(draw, title_text, title_font)[1]
    body_h = 0
    body_lines: list[str] = []

    max_text_h = note_box[3] - note_box[1] - 80
    for size in range(47, 35, -2):
        candidate_font = load_font(size, bold=True)
        candidate_h, candidate_lines = measure_wrapped_text(
            draw,
            limit_text,
            candidate_font,
            text_width,
            body_line_gap,
        )
        total_h = title_h + title_body_gap + candidate_h
        body_font = candidate_font
        body_h = candidate_h
        body_lines = candidate_lines
        if total_h <= max_text_h:
            break

    text_group_h = title_h + title_body_gap + body_h
    text_top = int(note_box[1] + (note_box[3] - note_box[1] - text_group_h) / 2)

    icon_size = 84
    icon_center_y = text_top + text_group_h / 2
    icon_y = int(icon_center_y - icon_size / 2)
    icon_x = note_box[0] + 48
    draw.ellipse((icon_x, icon_y, icon_x + icon_size, icon_y + icon_size), fill=GOLD)

    bang_font = load_font(70, bold=True)
    bang_bbox = draw.textbbox((0, 0), "!", font=bang_font)
    bang_w = bang_bbox[2] - bang_bbox[0]
    bang_h = bang_bbox[3] - bang_bbox[1]
    bang_x = icon_x + icon_size / 2 - bang_w / 2 - bang_bbox[0]
    bang_y = icon_y + icon_size / 2 - bang_h / 2 - bang_bbox[1]
    draw.text((bang_x, bang_y), "!", font=bang_font, fill="white")

    draw.text((text_left, text_top), title_text, font=title_font, fill=INK)
    body_y = text_top + title_h + title_body_gap
    for line in body_lines:
        _, line_h = text_size(draw, line, body_font)
        draw.text((text_left, body_y), line, font=body_font, fill=INK)
        body_y += line_h + body_line_gap


def draw_bottom_boundary_section(draw: ImageDraw.ImageDraw) -> None:
    left, content_top, right, section_bottom = draw_section_shell(
        draw,
        2100,
        850,
        "3｜模型边界与安全提醒",
    )

    card_y = content_top + 70
    card_gap = 70
    start_x = left + 110
    card_w = int((right - left - 220 - card_gap * 2) / 3)
    reminder_h = 145
    card_h = section_bottom - 55 - reminder_h - 45 - card_y
    cards = [
        (
            "模型优点",
            GREEN,
            ["公开数据可追溯", "便于个人学习复盘", "结果可与公告净值对照"],
        ),
        (
            "主要局限",
            GOLD,
            ["持仓披露存在滞后", "汇率、费用、现金仓位会影响结果", "估值时点和指数代理存在误差"],
        ),
        (
            "使用边界",
            RED,
            ["非实时净值，最终以公告为准", "不作为基金选择依据", "不提供任何投资建议"],
        ),
    ]

    for idx, (title, color, bullets) in enumerate(cards):
        x = start_x + idx * (card_w + card_gap)
        rounded(draw, (x, card_y, x + card_w, card_y + card_h), 28, SOFT_CARD, LINE, 2)
        rounded(draw, (x + 34, card_y + 38, x + card_w - 34, card_y + 110), 28, color, None)
        draw_text_in_box(
            draw,
            (x + 52, card_y + 38, x + card_w - 52, card_y + 110),
            title,
            font_size=52,
            min_font_size=40,
            bold=True,
            fill="white",
            align="center",
            valign="center",
        )
        draw_bullets_in_box(
            draw,
            (x + 64, card_y + 150, x + card_w - 45, card_y + card_h - 42),
            bullets,
            color,
            font_size=46,
            min_font_size=34,
            gap=12,
            valign="center",
        )

    reminder = (left + 110, card_y + card_h + 45, right - 110, section_bottom - 55)
    rounded(draw, reminder, 26, "#fffdf8", "#e7d0a3", 2)
    draw_text_in_box(
        draw,
        (reminder[0] + 45, reminder[1] + 25, reminder[2] - 45, reminder[3] - 25),
        "仅作为个人学习记录，不提供任何投资建议",
        font_size=68,
        min_font_size=50,
        bold=True,
        fill=RED,
        align="center",
        valign="center",
    )


def draw_footer(draw: ImageDraw.ImageDraw) -> None:
    footer_box = (150, 3000, WIDTH - 150, 3155)
    rounded(draw, footer_box, 28, "#fffdf8", "#e7d0a3", 2)
    draw_text_in_box(
        draw,
        (footer_box[0] + 45, footer_box[1] + 22, footer_box[2] - 45, footer_box[3] - 22),
        "个人模型预估｜仅供个人学习｜不构成任何投资建议",
        font_size=82,
        min_font_size=58,
        bold=True,
        fill=RED,
        align="center",
        valign="center",
    )
    signature = "鱼师AHNS · 个人公开数据建模复盘"
    sw, _ = text_size(draw, signature, FONT_SIGNATURE)
    draw.text((WIDTH - 150 - sw, 3168), signature, font=FONT_SIGNATURE, fill="#7b8796")


def build_image() -> Image.Image:
    image = Image.new("RGBA", (WIDTH, HEIGHT), BG)
    draw_watermarks(image)
    draw = ImageDraw.Draw(image)

    draw_title(draw)
    draw_top_time_section(draw)
    draw_mid_basis_section(draw)
    draw_bottom_boundary_section(draw)
    draw_footer(draw)

    return image.convert("RGB")


def main() -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    image = build_image()
    image.save(OUTPUT_FILE, optimize=True, compress_level=9)
    print(f"说明图已生成: {OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
