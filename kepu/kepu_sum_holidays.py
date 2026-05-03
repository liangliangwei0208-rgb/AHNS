"""
生成节后海外基金净值补更新规则科普图。

本脚本只在节后第 1 / 第 2 个 A 股交易日生成图片；普通周末、节假日休市日、
节后第 3 个交易日起都只打印原因并正常退出。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

KEPU_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = KEPU_DIR.parent
for import_path in (PROJECT_ROOT, KEPU_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import first_pic as art
from sum_holidays import PostHolidayContext, detect_post_holiday_context


OUTPUT_FILE = PROJECT_ROOT / "output" / "kepu_sum_holidays.png"


def _draw_title(draw: ImageDraw.ImageDraw, context: PostHolidayContext) -> None:
    art.draw_center_text(draw, art.WIDTH // 2, 50, f"北京时间：{context.today.strftime('%Y-%m-%d')}", art.FONT_TITLE, art.INK)
    art.draw_center_text(draw, art.WIDTH // 2, 190, "节后海外基金预估收益率怎么算？", art.FONT_TITLE, art.INK)
    art.draw_center_text(
        draw,
        art.WIDTH // 2,
        330,
        "说明模型区间观察口径，不代表基金公司净值公告",
        art.FONT_SUBTITLE,
        art.MUTED,
    )


def _draw_timeline_section(draw: ImageDraw.ImageDraw, context: PostHolidayContext) -> None:
    left, content_top, right, section_bottom = art.draw_section_shell(
        draw,
        430,
        735,
        "1｜节后为什么会出现“补更新”？",
    )

    box_w, box_h = 620, 175
    gap = 165
    box_y = content_top + 60
    xs = [left + 150, left + 150 + box_w + gap, left + 150 + (box_w + gap) * 2]
    items = [
        ("节前", "最后一个海外估值日，记作 T日", art.BLUE),
        ("假期中", "海外可能交易，国内披露会暂停", art.GOLD),
        ("节后", "把缺口分批补更新", art.GREEN),
    ]

    for idx, (title, sub, color) in enumerate(items):
        x = xs[idx]
        art.rounded(draw, (x, box_y, x + box_w, box_y + box_h), 28, art.SOFT_CARD, art.LINE, 2)
        draw.rounded_rectangle((x, box_y, x + box_w, box_y + 18), radius=12, fill=color)
        art.draw_text_in_box(
            draw,
            (x + 35, box_y + 36, x + box_w - 35, box_y + 94),
            title,
            font_size=54,
            min_font_size=40,
            bold=True,
            fill=art.INK,
            align="center",
            valign="center",
        )
        art.draw_text_in_box(
            draw,
            (x + 35, box_y + 100, x + box_w - 35, box_y + 160),
            sub,
            font_size=40,
            min_font_size=30,
            bold=True,
            fill=art.MUTED,
            align="center",
            valign="center",
        )
        if idx < len(items) - 1:
            art.draw_arrow(draw, x + box_w + 42, box_y + box_h // 2, xs[idx + 1] - 45)

    note_box = (left + 120, box_y + box_h + 60, right - 120, section_bottom - 55)
    art.rounded(draw, note_box, 28, "#ffffff", art.LINE, 2)
    bullets = [
        "海外基金净值通常不是海外市场一收盘就立刻公布，中间还要等估值和公告。",
        "国内放假时，海外市场可能照常交易；但国内平台可能等节后再集中显示。",
        "所以节后看到的涨跌幅，可能是在补前几天的估值影响，不一定是当天市场涨跌。",
    ]
    art.draw_bullets_in_box(
        draw,
        (note_box[0] + 70, note_box[1] + 35, note_box[2] - 60, note_box[3] - 35),
        bullets,
        art.BLUE,
        font_size=46,
        min_font_size=36,
        gap=12,
        valign="center",
    )


def _draw_rule_section(draw: ImageDraw.ImageDraw, context: PostHolidayContext) -> None:
    left, content_top, right, section_bottom = art.draw_section_shell(
        draw,
        1235,
        880,
        "2｜节后第1天、第2天收益率计算方式",
    )

    card_gap = 70
    card_w = int((right - left - 240 - card_gap * 2) / 3)
    card_h = section_bottom - content_top - 135
    card_y = content_top + 65
    start_x = left + 120

    cards = [
        (
            "节后第 1 天",
            art.BLUE,
            "单日观察",
            [
                "看 T日 的预估收益率",
                "T日=节前最后一个海外估值日",
                "这一天不做累计",
            ],
        ),
        (
            "节后第 2 天",
            art.GOLD,
            "区间累计",
            [
                "从 T日 之后开始看",
                "只累计实际存在的海外估值日",
                "周末或海外休市没有数据就跳过",
            ],
        ),
        (
            "节后第 3 天起",
            art.GREEN,
            "回到日常节奏",
            [
                "不再单独做节后补更新图",
                "继续看普通每日模型观察",
                "最终仍以基金公告为准",
            ],
        ),
    ]

    for idx, (title, color, tag, bullets) in enumerate(cards):
        x = start_x + idx * (card_w + card_gap)
        art.rounded(draw, (x, card_y, x + card_w, card_y + card_h), 30, art.SOFT_CARD, art.LINE, 2)
        art.rounded(draw, (x + 34, card_y + 36, x + card_w - 34, card_y + 125), 28, color, None)
        art.draw_text_in_box(
            draw,
            (x + 52, card_y + 39, x + card_w - 52, card_y + 121),
            title,
            font_size=48,
            min_font_size=36,
            bold=True,
            fill="white",
            align="center",
            valign="center",
        )
        art.draw_text_in_box(
            draw,
            (x + 58, card_y + 150, x + card_w - 58, card_y + 210),
            tag,
            font_size=45,
            min_font_size=34,
            bold=True,
            fill=color,
            align="center",
            valign="center",
        )
        art.draw_bullets_in_box(
            draw,
            (x + 58, card_y + 245, x + card_w - 44, card_y + card_h - 55),
            bullets,
            color,
            font_size=43,
            min_font_size=32,
            gap=20,
            valign="center",
        )

    badge_box = (left + 140, section_bottom - 112, right - 140, section_bottom - 38)
    art.rounded(draw, badge_box, 24, "#fff8eb", "#e8cf9e", 2)
    badge_text = "简单记：第1天看 T日 单日；第2天看 T日之后的有效估值日累计；第3天回到日常观察。"
    art.draw_text_in_box(
        draw,
        (badge_box[0] + 35, badge_box[1] + 8, badge_box[2] - 35, badge_box[3] - 8),
        badge_text,
        font_size=44,
        min_font_size=32,
        bold=True,
        fill=art.INK,
        align="center",
        valign="center",
    )


def _draw_boundary_section(draw: ImageDraw.ImageDraw) -> None:
    left, content_top, right, section_bottom = art.draw_section_shell(
        draw,
        2200,
        735,
        "3｜怎么看这张图",
    )

    inner = (left + 120, content_top + 62, right - 120, section_bottom - 160)
    art.rounded(draw, inner, 30, "#ffffff", art.LINE, 2)
    bullets = [
        "这是个人模型观察，用来帮助理解节后可能补披露的是哪一段。",
        "第2天的“累计”不是把自然日直接相加，而是按实际有估值的日期连续观察。",
        "它不是实时净值，也不是基金公司公告；最终净值和披露日期以公告为准。",
    ]
    art.draw_bullets_in_box(
        draw,
        (inner[0] + 80, inner[1] + 48, inner[2] - 70, inner[3] - 42),
        bullets,
        art.RED,
        font_size=48,
        min_font_size=36,
        gap=20,
        valign="center",
    )

    footer = (left + 120, section_bottom - 130, right - 120, section_bottom - 42)
    art.rounded(draw, footer, 26, "#fffdf8", "#e7d0a3", 2)
    art.draw_text_in_box(
        draw,
        (footer[0] + 35, footer[1] + 8, footer[2] - 35, footer[3] - 8),
        "仅供个人学习记录，不构成任何投资建议；不代表基金公司公告。",
        font_size=56,
        min_font_size=40,
        bold=True,
        fill=art.RED,
        align="center",
        valign="center",
    )


def build_image(context: PostHolidayContext) -> Image.Image:
    image = Image.new("RGBA", (art.WIDTH, art.HEIGHT), art.BG)
    art.draw_watermarks(image)
    draw = ImageDraw.Draw(image)
    _draw_title(draw, context)
    _draw_timeline_section(draw, context)
    _draw_rule_section(draw, context)
    _draw_boundary_section(draw)
    return image.convert("RGB")


def run(today=None) -> bool:
    context = detect_post_holiday_context(today=today)
    print(context.reason)

    if not context.should_generate:
        return False

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    image = build_image(context)
    image.save(OUTPUT_FILE, optimize=True, compress_level=9)
    print(f"节后海外基金补更新科普图已生成: {OUTPUT_FILE}")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成节后海外基金净值补更新规则科普图")
    parser.add_argument(
        "--today",
        default=None,
        help="用于测试的北京时间日期，例如 2026-05-06；默认使用今天。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(today=args.today)


if __name__ == "__main__":
    main()
