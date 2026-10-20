"""纯Pillow事件图：只读准备好的真实资料，不联网、不读取基金持仓。"""
from __future__ import annotations

import math
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, PngImagePlugin

from tools.configs.index_fund_tracking_configs import FUND_RETURN_COLOR, INDEX_RETURN_COLOR
from tools.paths import MARK_IMAGE
from tools.fund_cache_maintenance import replace_atomic_with_retry
from tools.safe_display import get_watermark_font

BG, INK, MUTED, LINE = "#F3F6F7", "#203544", "#667782", "#E5ECEE"


def _wrap(draw, text, font, width):
    lines, line = [], ""
    for character in str(text):
        if character == "\n" or (line and draw.textlength(line+character, font=font) > width):
            lines.append(line)
            line = "" if character == "\n" else character
        else:
            line += character
    if line or not lines:
        lines.append(line)
    return lines


def _text(draw, box, text, size, fill, report, *, align="center"):
    """用真实字形边界定位整个多行块；中英数字混排共享垂直中心。"""
    x0, y0, x1, y1 = box
    while True:
        font = get_watermark_font(size)
        lines = _wrap(draw, text, font, x1-x0)
        boxes = [draw.textbbox((0, 0), line or " ", font=font) for line in lines]
        gap = max(5, round(size*.25))
        height = sum(b[3]-b[1] for b in boxes)+gap*(len(lines)-1)
        if height <= y1-y0 or size <= 10:
            break
        size -= 1
    y = y0+(y1-y0-height)/2
    bounds = []
    for line, bbox in zip(lines, boxes):
        width, line_height = bbox[2]-bbox[0], bbox[3]-bbox[1]
        x = x0+(x1-x0-width)/2 if align == "center" else x0
        draw.text((round(x-bbox[0]), round(y-bbox[1])), line, font=font, fill=fill)
        bounds.append((x, y, x+width, y+line_height))
        y += line_height+gap
    report["texts"].append({"text": str(text), "box": tuple(box),
        "bounds": (min(b[0] for b in bounds), min(b[1] for b in bounds),
                   max(b[2] for b in bounds), max(b[3] for b in bounds))})


def _number(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def render_index_fund_limit_change(profile: dict, event: dict, comparison: dict, output_file: Path,
                                  *, logo_path: Path = MARK_IMAGE, layout_report=None, validation=False) -> Path:
    """1080宽四区域；原子PNG含事件ID，支持状态提交中断后的幂等恢复。"""
    logo_path, output_file = Path(logo_path), Path(output_file)
    if not logo_path.is_file():
        raise FileNotFoundError(f"真实Logo缺失，事件保留待重试: {logo_path}")
    report = {"texts": [], "bars": []}
    probe = ImageDraw.Draw(Image.new("RGB", (1080, 10)))
    name = profile.get("fund_name") or f"基金{profile['fund_code']}（名称待核验）"
    name_lines = _wrap(probe, name, get_watermark_font(40), 710)
    name_height = max(70, len(name_lines)*54)
    header_height = 220+name_height
    report["header_bottom"] = 36+header_height
    change_y = 36+header_height+24
    return_y = change_y+270+24
    footer_y = return_y+710+24
    height = footer_y+300+36
    image = Image.new("RGB", (1080, height), BG)
    draw = ImageDraw.Draw(image)
    card = lambda y, h: draw.rounded_rectangle((48, y, 1032, y+h), radius=26, fill="white")
    text = lambda box, value, size=26, color=INK, **kw: _text(draw, box, value, size, color, report, **kw)
    card(36, header_height)
    text((80, 54, 790, 98), "指数基金跟踪观察", 25, MUTED, align="left")
    text((80, 108, 790, 108+name_height), name, 40, align="left")
    name_end = 108+name_height
    text((80, name_end+8, 790, name_end+42), f"基金代码  {profile['fund_code']}", 25, MUTED, align="left")
    text((80, name_end+50, 790, name_end+94), profile["index_name_cn"], 34, FUND_RETURN_COLOR, align="left")
    text((80, name_end+98, 790, name_end+130), f"{profile['index_name_en']} · {profile['index_symbol']}", 24, MUTED, align="left")
    with Image.open(logo_path) as original:
        logo = original.convert("RGB")
        logo.thumbnail((166, 166), Image.Resampling.LANCZOS)
        logo_box = (840+(166-logo.width)//2, 66, 840+(166-logo.width)//2+logo.width, 66+logo.height)
        image.paste(logo, logo_box[:2])
        report["logo"] = logo_box
    text((826, 238, 1016, 274), "鱼师AHNS", 26, MUTED)

    card(change_y, 270)
    text((80, change_y+20, 505, change_y+70), "申购限额变化", 34, align="left")
    draw.rounded_rectangle((650, change_y+28, 1000, change_y+68), radius=18, fill="#E5F2F1")
    text((662, change_y+28, 988, change_y+68), "验收用途 · 未检测变更" if validation else "检测到变更", 23, FUND_RETURN_COLOR)
    detected = str(event.get("detected_at", "未知")).replace("T", " ").replace("+08:00", "")
    text((80, change_y+75, 1000, change_y+110), f"北京时间检测  {detected}", 23, MUTED, align="left")
    for x0, x1, title, value, color in ((80, 480, "变更前", event["old_value"], "#F1F4F6"),
                                       (600, 1000, "变更后", event["new_value"], "#E9F5F4")):
        draw.rounded_rectangle((x0, change_y+125, x1, change_y+246), radius=18, fill=color)
        text((x0+16, change_y+134, x1-16, change_y+169), title, 23, MUTED)
        label = value + "/日" if value.endswith("元") else value
        text((x0+16, change_y+177, x1-16, change_y+235), label, 42, FUND_RETURN_COLOR if title == "变更后" else INK)
    text((493, change_y+145, 587, change_y+235), "→", 52, MUTED)

    card(return_y, 710)
    text((80, return_y+18, 1000, return_y+70), "基金与跟踪指数 · 区间收益", 34, align="left")
    text((80, return_y+73, 1000, return_y+108), "同一可比截止日期 · 近1 / 3 / 6 / 12个月", 23, MUTED, align="left")
    for x, color, label in ((80, FUND_RETURN_COLOR, "基金净值收益"), (420, INDEX_RETURN_COLOR, "指数价格收益")):
        draw.rounded_rectangle((x, return_y+124, x+30, return_y+140), radius=5, fill=color)
        text((x+42, return_y+112, x+290, return_y+151), label, 24, MUTED, align="left")
    periods = comparison.get("periods", {})
    values = [p.get(key) for p in periods.values() for key in ("fund_return", "index_return") if _number(p.get(key))]
    low, high = min([0]+values), max([0]+values)
    span = high-low or 2
    low, high = low-span*.08, high+span*.08
    project = lambda value: 245+(value-low)/(high-low)*570
    zero_x = project(0)
    for row, months in enumerate((1, 3, 6, 12)):
        y = return_y+164+row*133
        p = periods.get(f"{months}m", {})
        text((80, y+7, 210, y+85), f"近{months}月", 30)
        draw.line((245, y+98, 1000, y+98), fill=LINE, width=1)
        draw.line((zero_x, y+8, zero_x, y+83), fill="#CAD6DA", width=2)
        for offset, key, color in ((15, "fund_return", FUND_RETURN_COLOR), (57, "index_return", INDEX_RETURN_COLOR)):
            value = p.get(key)
            value = float(value) if _number(value) else None
            end_x = project(value) if value is not None else None
            if end_x is not None and end_x != zero_x:
                draw.rounded_rectangle((min(zero_x, end_x), y+offset, max(zero_x, end_x), y+offset+18), radius=5, fill=color)
            text((833, y+offset-10, 1000, y+offset+29), f"{value:+.2f}%" if value is not None else "N/A", 27, color if value is not None else MUTED)
            report["bars"].append({"period": months, "kind": key, "value": value,
                                    "zero_x": zero_x, "end_x": end_x, "scale": (low, high)})
        dates = f"{p.get('start_date') or 'N/A'} — {p.get('end_date') or 'N/A'}"
        text((245, y+101, 1000, y+127), dates, 20, MUTED)

    card(footer_y, 300)
    text((80, footer_y+14, 1000, footer_y+60), "数据口径与时效", 29, align="left")
    currency = {"CNY": "人民币", "USD": "美元", "HKD": "港币"}.get(profile.get("currency"), "币种待核验")
    notes = [f"基金：{currency}官方复权收益口径  |  指数：美元价格收益",
             f"可比截止：{comparison.get('comparison_end_date') or 'N/A'}  ·  端点见各区间",
             f"净值日期：{comparison.get('fund_nav_date') or 'N/A'}  |  指数收盘：{comparison.get('benchmark_close_date') or 'N/A'}",
             "汇率、分红、费用及交易日差异可能造成收益偏离；N/A为不可用。",
             "限购以基金公司与销售渠道为准，检测时间不等于公告生效时间。",
             "数据仅供观察，不构成投资建议。"]
    for i, note in enumerate(notes):
        text((80, footer_y+68+i*35, 1000, footer_y+100+i*35), note, 22, MUTED, align="left")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_file.with_name(f"{output_file.name}.{uuid.uuid4().hex}.tmp")
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("index_fund_event_id", event["id"])
    try:
        image.save(tmp, format="PNG", pnginfo=metadata, dpi=(144, 144))
        replace_atomic_with_retry(tmp, output_file)
    finally:
        tmp.unlink(missing_ok=True)
    if layout_report is not None:
        layout_report.update(report)
    return output_file
