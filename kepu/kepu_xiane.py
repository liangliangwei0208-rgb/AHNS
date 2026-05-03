"""
生成海外基金限额科普图和缓存限额表。

脚本会被 git_main.py 每天调用，但只有北京时间周六实际生成图片；其他日期
只打印跳过原因并正常退出。
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

from PIL import Image, ImageDraw

KEPU_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = KEPU_DIR.parent
for import_path in (PROJECT_ROOT, KEPU_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import first_pic as art
from tools.fund_universe import HAIWAI_FUND_CODES
from tools.safe_display import mask_fund_name


OUTPUT_KEPU_FILE = PROJECT_ROOT / "output" / "kepu_xiane.png"
OUTPUT_TABLE_FILE = PROJECT_ROOT / "output" / "xiane.png"
PURCHASE_LIMIT_CACHE_FILE = PROJECT_ROOT / "cache" / "fund_purchase_limit_cache.json"
FUND_ESTIMATE_CACHE_FILE = PROJECT_ROOT / "cache" / "fund_estimate_return_cache.json"


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
                "基金名称": mask_fund_name(fund_name),
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


def _draw_title(draw: ImageDraw.ImageDraw, title: str, subtitle: str, today: date) -> None:
    art.draw_center_text(draw, art.WIDTH // 2, 48, f"北京时间：{today.strftime('%Y-%m-%d')}", art.FONT_TITLE, art.INK)
    art.draw_center_text(draw, art.WIDTH // 2, 188, title, art.FONT_TITLE, art.INK)
    art.draw_center_text(draw, art.WIDTH // 2, 328, subtitle, art.FONT_SUBTITLE, art.MUTED)


def _draw_footer(draw: ImageDraw.ImageDraw, top: int = 3015) -> None:
    footer_box = (150, top, art.WIDTH - 150, top + 130)
    art.rounded(draw, footer_box, 28, "#fffdf8", "#e7d0a3", 2)
    art.draw_text_in_box(
        draw,
        (footer_box[0] + 50, footer_box[1] + 20, footer_box[2] - 50, footer_box[3] - 20),
        "仅供个人学习记录，不构成任何投资建议；具体申购规则以基金公告和销售平台展示为准。",
        font_size=60,
        min_font_size=43,
        bold=True,
        fill=art.RED,
        align="center",
        valign="center",
    )
    signature = "鱼师AHNS · 个人公开数据建模复盘"
    sw, _ = art.text_size(draw, signature, art.FONT_SIGNATURE)
    draw.text((art.WIDTH - 150 - sw, top + 145), signature, font=art.FONT_SIGNATURE, fill="#7b8796")


def _draw_quota_source_section(draw: ImageDraw.ImageDraw) -> None:
    left, content_top, right, section_bottom = art.draw_section_shell(
        draw,
        440,
        710,
        "1｜限额是什么意思？",
    )
    box_y = content_top + 65
    box_w, box_h = 650, 170
    gap = 155
    xs = [left + 140, left + 140 + box_w + gap, left + 140 + (box_w + gap) * 2]
    items = [
        ("每日限额", "一天内最多可以申购多少", art.BLUE),
        ("大额限制", "小额可能可以，大额可能不行", art.GOLD),
        ("暂停申购", "暂时不能买入或不能追加", art.GREEN),
    ]

    for idx, (title, sub, color) in enumerate(items):
        x = xs[idx]
        art.rounded(draw, (x, box_y, x + box_w, box_y + box_h), 28, art.SOFT_CARD, art.LINE, 2)
        draw.rounded_rectangle((x, box_y, x + box_w, box_y + 18), radius=12, fill=color)
        art.draw_text_in_box(
            draw,
            (x + 30, box_y + 38, x + box_w - 30, box_y + 94),
            title,
            font_size=52,
            min_font_size=40,
            bold=True,
            fill=art.INK,
            align="center",
            valign="center",
        )
        art.draw_text_in_box(
            draw,
            (x + 42, box_y + 96, x + box_w - 42, box_y + 155),
            sub,
            font_size=38,
            min_font_size=29,
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
        "限额不是收益判断，它只是基金申购时的一条操作规则。",
        "同一只基金在不同平台、不同日期看到的限额，可能会有变化。",
        "看到限额时，重点是先确认“还能不能申购、最多能申购多少”。",
    ]
    art.draw_bullets_in_box(
        draw,
        (note_box[0] + 70, note_box[1] + 38, note_box[2] - 60, note_box[3] - 36),
        bullets,
        art.BLUE,
        font_size=46,
        min_font_size=36,
        gap=12,
        valign="center",
    )


def _draw_limit_rule_section(draw: ImageDraw.ImageDraw) -> None:
    left, content_top, right, section_bottom = art.draw_section_shell(
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
        ("要换成外币", art.BLUE, ["海外基金通常要把人民币换成外币", "换汇和额度安排会影响申购节奏", "不是基金好坏的判断"]),
        ("要买海外资产", art.GOLD, ["海外市场交易时间不同", "交易通道和流动性也会影响管理", "节假日还可能不同步"]),
        ("要控制规模", art.RED, ["规模变化太快会增加管理难度", "基金公司可能先限制新增申购", "具体以公告为准"]),
    ]
    for idx, (title, color, bullets) in enumerate(cards):
        x = start_x + idx * (card_w + card_gap)
        art.rounded(draw, (x, card_y, x + card_w, card_y + card_h), 30, art.SOFT_CARD, art.LINE, 2)
        art.rounded(draw, (x + 34, card_y + 38, x + card_w - 34, card_y + 116), 28, color, None)
        art.draw_text_in_box(
            draw,
            (x + 52, card_y + 40, x + card_w - 52, card_y + 114),
            title,
            font_size=48,
            min_font_size=36,
            bold=True,
            fill="white",
            align="center",
            valign="center",
        )
        art.draw_bullets_in_box(
            draw,
            (x + 62, card_y + 165, x + card_w - 42, card_y + card_h - 42),
            bullets,
            color,
            font_size=42,
            min_font_size=32,
            gap=18,
            valign="center",
        )


def _draw_open_logic_section(draw: ImageDraw.ImageDraw) -> None:
    left, content_top, right, section_bottom = art.draw_section_shell(
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
    art.draw_bullets_in_box(
        draw,
        (inner[0] + 80, inner[1] + 58, inner[2] - 70, inner[3] - 52),
        bullets,
        art.GREEN,
        font_size=48,
        min_font_size=36,
        gap=18,
        valign="center",
    )
    _draw_footer(draw, top=2985)


def build_kepu_image(today: date) -> Image.Image:
    image = Image.new("RGBA", (art.WIDTH, art.HEIGHT), art.BG)
    art.draw_watermarks(image)
    draw = ImageDraw.Draw(image)
    _draw_title(draw, "海外基金为什么会限额？", "看懂限额数字、申购规则和额度变化", today)
    _draw_quota_source_section(draw)
    _draw_limit_rule_section(draw)
    _draw_open_logic_section(draw)
    return image.convert("RGB")


def _draw_table_watermarks(image: Image.Image, table_box: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = table_box
    overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))
    font = art.load_font(52, bold=True)
    text = "鱼师AHNS"
    patch = Image.new("RGBA", (420, 120), (255, 255, 255, 0))
    patch_draw = ImageDraw.Draw(patch)
    bbox = patch_draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    patch_draw.text(((420 - tw) / 2, (120 - th) / 2), text, font=font, fill=(5, 5, 5, 24))
    rotated = patch.rotate(28, expand=True, resample=Image.Resampling.BICUBIC)

    for y in range(top + 130, bottom - 80, 330):
        for x in range(left + 180, right - 120, 540):
            overlay.alpha_composite(rotated, (int(x - rotated.width / 2), int(y - rotated.height / 2)))

    image.alpha_composite(overlay)


def build_table_image(today: date) -> Image.Image:
    rows = _load_limit_rows()
    image = Image.new("RGBA", (art.WIDTH, art.HEIGHT), "#f5f7fb")
    art.draw_watermarks(image)
    draw = ImageDraw.Draw(image)
    _draw_title(draw, "海外基金限额信息表", "个人整理学习内容，所有信息来源于公开网络；限额信息具体以基金公告为准。", today)

    table_left, table_top = 165, 500
    col_widths = [130, 1550, 650]
    row_h = 86
    header_h = 96
    table_right = table_left + sum(col_widths)
    headers = ["序号", "基金名称", "限额信息"]
    table_bottom = table_top + header_h + row_h * len(rows)

    art.rounded(
        draw,
        (table_left, table_top - 28, table_right, table_bottom + 26),
        30,
        "#ffffff",
        art.LINE,
        2,
    )
    _draw_table_watermarks(image, (table_left, table_top, table_right, table_bottom))
    draw = ImageDraw.Draw(image)
    draw.rectangle((table_left, table_top, table_right, table_top + header_h), fill=art.NAVY)

    x = table_left
    for header, width in zip(headers, col_widths):
        art.draw_text_in_box(
            draw,
            (x + 10, table_top + 12, x + width - 10, table_top + header_h - 12),
            header,
            font_size=38,
            min_font_size=30,
            bold=True,
            fill="white",
            align="center",
            valign="center",
        )
        x += width
    for offset in (0, col_widths[0], col_widths[0] + col_widths[1], sum(col_widths)):
        draw.line((table_left + offset, table_top, table_left + offset, table_bottom), fill=art.LINE, width=2)

    y = table_top + header_h
    for idx, row in enumerate(rows):
        fill = "#ffffff" if idx % 2 == 0 else "#f7f9fd"
        draw.rectangle((table_left, y, table_right, y + row_h), fill=fill)
        draw.line((table_left, y, table_right, y), fill=art.LINE, width=1)
        x = table_left
        for key, width in zip(headers, col_widths):
            value = row.get(key, "")
            align = "left" if key == "基金名称" else "center"
            color = art.INK
            if key == "限额信息" and value in {"暂无记录"}:
                color = art.MUTED
            elif key == "限额信息":
                color = art.BLUE
            art.draw_text_in_box(
                draw,
                (x + 16, y + 9, x + width - 16, y + row_h - 9),
                value,
                font_size=33 if key != "基金名称" else 32,
                min_font_size=24,
                bold=True,
                fill=color,
                align=align,
                valign="center",
            )
            x += width
        y += row_h
    draw.line((table_left, table_bottom, table_right, table_bottom), fill=art.LINE, width=2)

    _draw_footer(draw, top=3000)
    return image.convert("RGB")


def run(today=None) -> bool:
    today_date = _normalize_today(today)
    if today_date.weekday() != 5:
        print(f"{today_date.isoformat()} 不是北京时间周六，跳过海外基金限额科普图生成。")
        return False

    OUTPUT_KEPU_FILE.parent.mkdir(parents=True, exist_ok=True)
    build_kepu_image(today_date).save(OUTPUT_KEPU_FILE, optimize=True, compress_level=9)
    build_table_image(today_date).save(OUTPUT_TABLE_FILE, optimize=True, compress_level=9)
    print(f"海外基金限额科普图已生成: {OUTPUT_KEPU_FILE}")
    print(f"海外基金限额表格图已生成: {OUTPUT_TABLE_FILE}")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="每周六生成海外基金限额科普图和限额表格图")
    parser.add_argument(
        "--today",
        default=None,
        help="用于测试的北京时间日期，例如 2026-05-02；默认使用今天。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(today=args.today)


if __name__ == "__main__":
    main()
