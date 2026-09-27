"""用 Pillow 绘制限购变化竖图，持仓与基金名称只读取本地缓存。"""
from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw

from tools.configs.fund_limit_change_style_configs import LIMIT_CHANGE_IMAGE_STYLE
from tools.fund_holding_change import _cached_period_from_item, _fund_name_from_cache_or_fallback
from tools.paths import FUND_HOLDINGS_CACHE, MARK_IMAGE
from tools.safe_display import get_watermark_font


def wrap_text(draw, text: str, font, width: int) -> list[str]:
    """按实际字宽换行，完整保留基金名、长证券名与状态文案。"""
    lines, line = [], ""
    for ch in str(text):
        if ch == "\n" or (line and draw.textlength(line + ch, font=font) > width):
            lines.append(line)
            line = "" if ch == "\n" else ch
        else:
            line += ch
    return lines + ([line] if line else [])


def cached_holdings(code: str) -> tuple[str, list[dict]]:
    """缓存无效时使用占位区域，不触发持仓联网或正式估算。"""
    try:
        cache = json.loads(FUND_HOLDINGS_CACHE.read_text(encoding="utf-8"))
        item = cache.get(f"{code}:top10")
        if not isinstance(item, dict):
            return "", []
        period = _cached_period_from_item(code, 10, item)
        if period is None:
            return "", []
        df = period.df
        df = df[df["_quarter_key"] == df["_quarter_key"].max()].sort_values("占净值比例", ascending=False).head(10)
        return period.quarter_label, df.to_dict(orient="records")
    except Exception as exc:
        print(f"[LIMIT_CHANGE] {code} 持仓缓存不可用，生成占位区域: {exc}", flush=True)
        return "", []


def save_change_image(*, fund_code: str, fund_name: str, event: dict,
                      period: str, holdings: list[dict], output_file: Path,
                      logo_path: Path = MARK_IMAGE) -> Path:
    style = LIMIT_CHANGE_IMAGE_STYLE
    width, margin, pad = style["width"], style["side_margin"], style["padding"]
    usable = width - 2 * margin
    measure = ImageDraw.Draw(Image.new("RGB", (width, 1)))
    fonts = {key: get_watermark_font(style[key]) for key in
             ("title_font", "section_font", "body_font", "small_font", "value_font")}
    title_lines = wrap_text(measure, fund_name, fonts["title_font"], usable)
    title_h = len(title_lines) * (style["title_font"] + 14)
    card_width = (usable - 2 * pad - 52) // 2
    old_lines = wrap_text(measure, event["old_value"], fonts["value_font"], card_width - 40)
    new_lines = wrap_text(measure, event["new_value"], fonts["value_font"], card_width - 40)
    value_h = 86 + max(len(old_lines), len(new_lines)) * (style["value_font"] + 10)
    change_h = 170 + value_h
    period_lines = wrap_text(measure, f"披露期：{period}" if period else "披露期：暂无可用信息",
                             fonts["small_font"], usable - 2 * pad)

    # 证券名与代码按各自列宽换行，行高随内容增长，避免截断或挤占权重列。
    table_width = usable - 2 * pad
    name_width, code_width = int(table_width * .44), int(table_width * .22)
    rows = []
    for holding in holdings[:10]:
        name = str(holding.get("股票名称") or "—")
        code = str(holding.get("ticker") or holding.get("股票代码") or "—")
        name_lines = wrap_text(measure, name, fonts["body_font"], name_width - 18)
        code_lines = wrap_text(measure, code, fonts["small_font"], code_width - 12)
        row_h = max(style["row_height"], 24 + max(len(name_lines) * (style["body_font"] + 10),
                                               len(code_lines) * (style["small_font"] + 10)))
        try:
            weight = float(str(holding.get("占净值比例", "")).replace("%", ""))
            if not math.isfinite(weight):
                weight = None
        except (ValueError, TypeError):
            weight = None
        rows.append((name_lines, code_lines, weight, row_h))
    table_h = 120 + len(period_lines) * 32 + 62 + (sum(r[3] for r in rows) if rows else 260) + 86
    heading_y = style["top_margin"]
    change_y = heading_y + 52 + title_h + 60
    table_y = change_y + change_h + style["section_gap"]
    height = table_y + table_h + 170 + style["bottom_margin"]
    image = Image.new("RGBA", (width, height), style["background"])
    draw = ImageDraw.Draw(image)

    def text(x, y, content, key="body_font", color=None, anchor=None):
        draw.text((x, y), str(content), font=fonts[key], fill=color or style["text"], anchor=anchor)

    def lines(x, y, values, key, color=None):
        for line in values:
            text(x, y, line, key, color)
            y += style[key] + (14 if key == "title_font" else 10)

    draw.rounded_rectangle((margin, heading_y, margin + 8, heading_y + 30), radius=4, fill=style["accent"])
    text(margin + 24, heading_y - 2, "基金限购动态", "small_font", style["accent"])
    text(width - margin, heading_y - 2, style["watermark_text"], "small_font", style["muted"], "ra")
    lines(margin, heading_y + 52, title_lines, "title_font")
    text(margin, heading_y + 52 + title_h + 8, f"基金代码  {fund_code}", "small_font", style["muted"])

    draw.rounded_rectangle((margin, change_y, width-margin, change_y+change_h), radius=26, fill=style["card"])
    text(margin + pad, change_y + 26, "限购信息变化", "section_font")
    from tools.fund_limit_change import parse_time
    detected = parse_time(event.get("detected_at"))
    detected_text = detected.strftime("%Y-%m-%d %H:%M") if detected else "未知"
    text(margin + pad, change_y + 78, f"检测到变化  {detected_text}（北京时间）", "small_font", style["muted"])
    value_y = change_y + 128
    for x, label, value_lines, bg, fg in (
        (margin + pad, "原限购", old_lines, style["previous_bg"], style["muted"]),
        (width - margin - pad - card_width, "最新限购", new_lines, style["accent_light"], style["accent"]),
    ):
        draw.rounded_rectangle((x, value_y, x + card_width, value_y + value_h), radius=18, fill=bg)
        text(x + 20, value_y + 18, label, "small_font", fg)
        lines(x + 20, value_y + 66, value_lines, "value_font", fg)
    # 用线段画箭头，避免中文字体缺少箭头字形。
    arrow_y = value_y + value_h // 2
    draw.line((width//2-15, arrow_y, width//2+15, arrow_y), fill=style["accent"], width=3)
    draw.line((width//2+7, arrow_y-8, width//2+15, arrow_y, width//2+7, arrow_y+8), fill=style["accent"], width=3)

    draw.rounded_rectangle((margin, table_y, width-margin, table_y+table_h), radius=26, fill=style["card"])
    text(margin + pad, table_y + 28, "最新缓存持仓 · 前十大股票", "section_font")
    lines(margin + pad, table_y + 82, period_lines, "small_font", style["muted"])
    y = table_y + 120 + len(period_lines) * 32
    x = margin + pad
    name_x = x + int(table_width * .08)
    code_x = name_x + name_width
    right = width - margin - pad
    draw.rounded_rectangle((x, y, right, y + 62), radius=8, fill=style["text"])
    for col_x, label in [(x+10, "序"), (name_x, "股票名称"), (code_x, "代码")]:
        text(col_x, y+12, label, "small_font", "#FFFFFF")
    text(right-10, y+12, "占净值", "small_font", "#FFFFFF", "ra")
    y += 62
    body_top = y
    for index, (name_lines, code_lines, weight, row_h) in enumerate(rows, 1):
        if index % 2:
            draw.rectangle((x, y, right, y+row_h), fill=style["stripe"])
        text(x+12, y+16, f"{index:02}", "small_font", style["muted"])
        lines(name_x, y+12, name_lines, "body_font")
        lines(code_x, y+16, code_lines, "small_font", style["muted"])
        text(right-10, y+12, f"{weight:.2f}%" if weight is not None else "—", "body_font", anchor="ra")
        y += row_h
    if not rows:
        text(width//2, y+95, "暂无可用持仓缓存", "section_font", style["muted"], "ma")
        y += 260
    body_bottom = y
    draw.line((x, y+6, right, y+6), fill=style["line"], width=2)
    text(x+10, y+27, f"已列示 {len(rows)} 只股票合计", "small_font", style["muted"])
    total = sum(row[2] for row in rows if row[2] is not None)
    text(right-10, y+22, f"{total:.2f}%" if rows else "—", "section_font", style["accent"], "ra")

    if not logo_path.is_file():
        raise FileNotFoundError(f"缺少品牌 logo，保留事件待补齐后出图: {logo_path}")
    with Image.open(logo_path) as source:
        logo = source.convert("RGBA")
    logo.thumbnail((int(usable * style["logo_width_ratio"]), max(1, body_bottom-body_top-20)), Image.Resampling.LANCZOS)
    logo.putalpha(logo.getchannel("A").point(lambda a: round(a * style["logo_opacity"])))
    image.alpha_composite(logo, ((width-logo.width)//2, (body_top+body_bottom-logo.height)//2))
    draw = ImageDraw.Draw(image)
    foot_y = table_y + table_h + 32
    text(width//2, foot_y, "检测时间不代表公告生效时间，限购以销售平台为准", "small_font", style["muted"], "ma")
    text(width//2, foot_y+40, "持仓为缓存披露数据，不代表实时持仓", "small_font", style["muted"], "ma")
    text(width//2, foot_y+90, style["watermark_text"], "section_font", style["accent"], "ma")
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    # 原子替换避免半张 PNG 被邮件扫描器收集。
    temporary = output_file.with_suffix(".png.tmp")
    image.convert("RGB").save(temporary, format="PNG", dpi=(style["export_dpi"],)*2, compress_level=6)
    temporary.replace(output_file)
    return output_file


def render_cached_change(code: str, event: dict, output_file: Path) -> Path:
    period, holdings = cached_holdings(code)
    return save_change_image(fund_code=code, fund_name=_fund_name_from_cache_or_fallback(code),
                             event=event, period=period, holdings=holdings, output_file=output_file)
