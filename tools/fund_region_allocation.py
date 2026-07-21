"""晨星海外基金股票地区分布抓取、缓存与抖音竖版对比图。

晨星的“股票地区分布”按股票组合内部权重展示，并不等于基金全部资产配置。
本模块刻意使用 ``requests.Session.trust_env = False``，避免继承主机的 VPN
HTTP/SOCKS 代理环境变量；GitHub/Gitee 的 Git 代理配置不会影响这里。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageColor, ImageDraw, ImageFont

from tools.configs.cache_policy_configs import FUND_REGION_ALLOCATION_CACHE_DAYS
from tools.configs.fund_region_allocation_configs import (
    DISPLAY_REGION_COLORS,
    DISPLAY_REGION_LABELS,
    DISPLAY_REGION_ORDER,
    MORNINGSTAR_CONNECT_TIMEOUT_SECONDS,
    MORNINGSTAR_FUND_URL_TEMPLATE,
    MORNINGSTAR_PARENT_REGION_LABELS,
    MORNINGSTAR_READ_TIMEOUT_SECONDS,
    MORNINGSTAR_REQUEST_INTERVAL_SECONDS,
    MORNINGSTAR_RETRY_ATTEMPTS,
    MORNINGSTAR_SUBREGION_LABELS,
)
from tools.configs.fund_region_allocation_style_configs import FUND_REGION_ALLOCATION_IMAGE_STYLE
from tools.configs.fund_universe_configs import HAIWAI_FUND_CODES
from tools.console_display import print_key_values, print_records_table, print_stage
from tools.paths import (
    FUND_ESTIMATE_CACHE,
    FUND_REGION_ALLOCATION_CACHE,
    FUND_REGION_ALLOCATION_STATE_CACHE,
    MARK_IMAGE,
    OUTPUT_DIR,
    ensure_runtime_dirs,
    relative_path_str,
)
from tools.safe_display import get_watermark_font


REGION_OUTPUT_DIR = OUTPUT_DIR / "fund_region_allocation"
REGION_LATEST_OUTPUT_DIR = REGION_OUTPUT_DIR / "latest"
REGION_MANUAL_OUTPUT_DIR = REGION_OUTPUT_DIR / "manual"
DEFAULT_FUND_CODE = "012922"
SOURCE_NAME = "morningstar_direct"
FAILURE_RETRY_HOURS = 6
# 修改图片展示规则时递增，强制自动模式重绘旧版分页。
PAGE_RENDER_VERSION = 6


@dataclass(frozen=True)
class RegionPage:
    number: int
    fund_codes: tuple[str, ...]
    output_file: Path


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalize_fund_code(value: Any) -> str:
    text = str(value or "").strip()
    digits = "".join(char for char in text if char.isdigit())
    if not digits:
        raise ValueError(f"基金代码必须包含数字，当前为: {text!r}")
    if len(digits) > 6:
        raise ValueError(f"基金代码必须为单个 6 位代码，当前为: {text!r}")
    return digits.zfill(6)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("%", "").replace(",", "")
    if text in {"", "--", "-", "None", "nan", "NaN"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _record_fingerprint(record: dict[str, Any]) -> str:
    payload = {
        "report_date": str(record.get("report_date", "")),
        "primary_regions": record.get("primary_regions", {}),
        "subregions": record.get("subregions", {}),
        "valid": bool(record.get("valid", False)),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_valid_record(record: Any) -> bool:
    return bool(
        isinstance(record, dict)
        and record.get("valid") is True
        and isinstance(record.get("primary_regions"), dict)
        and record.get("report_date")
    )


def _cache_is_fresh(record: Any, *, now: datetime | None = None) -> bool:
    if not _is_valid_record(record):
        return False
    fetched_at = _parse_datetime(record.get("fetched_at"))
    if fetched_at is None:
        return False
    now = now or datetime.now().astimezone()
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=now.tzinfo)
    return now - fetched_at <= timedelta(days=FUND_REGION_ALLOCATION_CACHE_DAYS)


def _failure_retry_due(record: Any, *, now: datetime | None = None) -> bool:
    if not isinstance(record, dict) or _is_valid_record(record):
        return True
    checked_at = _parse_datetime(record.get("last_checked_at"))
    if checked_at is None:
        return True
    now = now or datetime.now().astimezone()
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=now.tzinfo)
    return now - checked_at >= timedelta(hours=FAILURE_RETRY_HOURS)


def _direct_morningstar_session() -> requests.Session:
    """创建不读取环境代理变量的晨星专用直连会话。"""
    session = requests.Session()
    # 关键：不继承 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY，也不读取 .netrc。
    session.trust_env = False
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        }
    )
    return session


def _decode_response_html(response: requests.Response) -> str:
    # 晨星当前为 UTF-8；显式解码避免 requests 在无 charset 时误判为拉丁编码。
    return response.content.decode("utf-8", errors="replace")


def _parse_region_table(html: str) -> tuple[str, dict[str, float], dict[str, float]]:
    soup = BeautifulSoup(html, "html.parser")
    matching_section = None
    for section in soup.select("div.pane-column-item"):
        header = section.select_one("div.pane-column-header")
        if header and "股票地区分布" in header.get_text(" ", strip=True):
            matching_section = section
            break
    if matching_section is None:
        raise RuntimeError("晨星页面未找到“股票地区分布”区块")

    header = matching_section.select_one("div.pane-column-header")
    header_text = header.get_text(" ", strip=True) if header else ""
    report_date = ""
    for token in header_text.split():
        if len(token) == 10 and token[4:5] == "-" and token[7:8] == "-":
            report_date = token
            break
    if not report_date:
        raise RuntimeError(f"晨星地区分布缺少披露日期: {header_text}")

    table = matching_section.find("table")
    if table is None:
        raise RuntimeError("晨星地区分布区块缺少数据表")

    primary_regions: dict[str, float] = {}
    subregions: dict[str, float] = {}
    for row in table.select("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        label = cells[0].get_text(" ", strip=True)
        value = _safe_float(cells[1].get_text(" ", strip=True))
        if value is None:
            continue
        if "row-head" in (row.get("class") or []):
            if label in MORNINGSTAR_PARENT_REGION_LABELS:
                primary_regions[label] = value
        elif label in MORNINGSTAR_SUBREGION_LABELS:
            subregions[label] = value
        elif label == "未分类":
            primary_regions[label] = value

    required = {"大亚洲地区", "美洲", "大欧洲地区"}
    missing = sorted(required - set(primary_regions))
    if missing:
        raise RuntimeError(f"晨星地区分布缺少父级区域: {', '.join(missing)}")

    parent_total = sum(primary_regions.get(label, 0.0) for label in MORNINGSTAR_PARENT_REGION_LABELS)
    if not 98.0 <= parent_total <= 102.0:
        raise RuntimeError(f"晨星地区父级权重合计异常: {parent_total:.2f}%")
    return report_date, primary_regions, subregions


def _fetch_morningstar_record(fund_code: str, fund_name: str) -> dict[str, Any]:
    url = MORNINGSTAR_FUND_URL_TEMPLATE.format(fund_code=fund_code)
    last_error: Exception | None = None
    for attempt in range(1, MORNINGSTAR_RETRY_ATTEMPTS + 1):
        try:
            session = _direct_morningstar_session()
            response = session.get(
                url,
                timeout=(MORNINGSTAR_CONNECT_TIMEOUT_SECONDS, MORNINGSTAR_READ_TIMEOUT_SECONDS),
            )
            response.raise_for_status()
            report_date, primary_regions, subregions = _parse_region_table(_decode_response_html(response))
            record = {
                "fund_code": fund_code,
                "fund_name": fund_name,
                "source": SOURCE_NAME,
                "source_url": url,
                "report_date": report_date,
                "primary_regions": primary_regions,
                "subregions": subregions,
                "fetched_at": _now_text(),
                "last_checked_at": _now_text(),
                "valid": True,
                "last_error": "",
            }
            record["fingerprint"] = _record_fingerprint(record)
            return record
        except Exception as exc:  # 单基金失败应由上层继续处理下一只基金。
            last_error = exc
            if attempt < MORNINGSTAR_RETRY_ATTEMPTS:
                time.sleep(0.8 * attempt)
    raise RuntimeError(f"晨星直连获取失败: {last_error!r}")


def _fund_names_from_estimate_cache() -> dict[str, str]:
    cache = _load_json(FUND_ESTIMATE_CACHE, {})
    records = cache.get("records", {}) if isinstance(cache, dict) else {}
    result: dict[str, tuple[str, str]] = {}
    if not isinstance(records, dict):
        return {}
    for record in records.values():
        if not isinstance(record, dict) or str(record.get("market_group", "")) != "overseas":
            continue
        code = str(record.get("fund_code", "")).zfill(6)
        name = str(record.get("fund_name", "")).strip()
        date_key = str(record.get("valuation_date") or record.get("run_time_bj") or "")
        if name and (code not in result or date_key >= result[code][0]):
            result[code] = (date_key, name)
    return {code: value[1] for code, value in result.items()}


def _candidate_fund_codes(fund_codes: Iterable[Any] | None = None) -> list[str]:
    raw_codes = fund_codes if fund_codes is not None else HAIWAI_FUND_CODES
    seen: set[str] = set()
    result: list[str] = []
    for value in raw_codes:
        try:
            code = _normalize_fund_code(value)
        except ValueError:
            continue
        if code not in seen:
            seen.add(code)
            result.append(code)
    return result


def _resolve_records(
    fund_codes: list[str],
    *,
    refresh: bool,
    emit_progress: bool = True,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    cache = _load_json(FUND_REGION_ALLOCATION_CACHE, {})
    if not isinstance(cache, dict):
        cache = {}
    names = _fund_names_from_estimate_cache()
    errors: list[dict[str, str]] = []
    now = datetime.now().astimezone()

    for index, fund_code in enumerate(fund_codes, start=1):
        cached = cache.get(fund_code) if isinstance(cache.get(fund_code), dict) else {}
        fund_name = names.get(fund_code) or str(cached.get("fund_name", "")).strip() or f"基金{fund_code}"
        should_fetch = refresh or not _cache_is_fresh(cached, now=now)
        if not _is_valid_record(cached) and not _failure_retry_due(cached, now=now) and not refresh:
            should_fetch = False

        if not should_fetch:
            if cached:
                cached = dict(cached)
                cached["fund_name"] = fund_name
                cache[fund_code] = cached
            if emit_progress:
                print(f"[MORNINGSTAR] {index}/{len(fund_codes)} {fund_code} 使用地区缓存", flush=True)
            continue

        if emit_progress:
            print(f"[MORNINGSTAR] {index}/{len(fund_codes)} {fund_code} 晨星直连获取地区分布", flush=True)
        try:
            fetched = _fetch_morningstar_record(fund_code, fund_name)
            cache[fund_code] = fetched
            if emit_progress:
                print(
                    f"[MORNINGSTAR] {fund_code} 成功: 披露日 {fetched['report_date']}，"
                    f"地区权重 {sum(fetched['primary_regions'].values()):.2f}%",
                    flush=True,
                )
        except Exception as exc:
            error_text = str(exc)
            errors.append({"基金代码": fund_code, "基金名称": fund_name, "错误": error_text})
            if _is_valid_record(cached):
                retained = dict(cached)
                retained["fund_name"] = fund_name
                retained["last_checked_at"] = _now_text()
                retained["last_error"] = error_text
                cache[fund_code] = retained
                print(f"[MORNINGSTAR] {fund_code} 失败，继续使用旧地区缓存: {error_text}", flush=True)
            else:
                cache[fund_code] = {
                    "fund_code": fund_code,
                    "fund_name": fund_name,
                    "source": SOURCE_NAME,
                    "source_url": MORNINGSTAR_FUND_URL_TEMPLATE.format(fund_code=fund_code),
                    "report_date": "",
                    "primary_regions": {},
                    "subregions": {},
                    "valid": False,
                    "last_checked_at": _now_text(),
                    "last_error": error_text,
                    "fingerprint": "",
                }
                print(f"[MORNINGSTAR] {fund_code} 失败，暂无可用缓存: {error_text}", flush=True)
        if index < len(fund_codes):
            time.sleep(MORNINGSTAR_REQUEST_INTERVAL_SECONDS)

    _write_json(FUND_REGION_ALLOCATION_CACHE, cache)
    selected = {code: dict(cache.get(code, {})) for code in fund_codes}
    return selected, errors


def _display_regions(record: dict[str, Any]) -> dict[str, float]:
    primary = record.get("primary_regions", {}) if isinstance(record, dict) else {}
    subregions = record.get("subregions", {}) if isinstance(record, dict) else {}
    asia_total = float(primary.get("大亚洲地区", 0.0) or 0.0)
    europe_total = float(primary.get("大欧洲地区", 0.0) or 0.0)
    oceania = float(subregions.get("大洋洲", 0.0) or 0.0)
    africa_middle_east = float(subregions.get("非洲/中东", 0.0) or 0.0)
    # 晨星层级有重叠：大洋洲属于大亚洲、非洲/中东属于大欧洲。
    # 扣除后展示区域才可在堆叠条中准确相加到 100%。
    return {
        "亚洲": max(0.0, asia_total - oceania),
        "美洲": max(0.0, float(primary.get("美洲", 0.0) or 0.0)),
        "欧洲": max(0.0, europe_total - africa_middle_east),
        "非洲中东": max(0.0, africa_middle_east),
        "大洋洲": max(0.0, oceania),
        "未分类": max(0.0, float(primary.get("未分类", 0.0) or 0.0)),
    }


def _font_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return max(0, bbox[2] - bbox[0]), max(0, bbox[3] - bbox[1])


def _draw_centered(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    left, top, right, bottom = box
    width, height = _font_size(draw, text, font)
    draw.text(((left + right - width) // 2, (top + bottom - height) // 2), text, font=font, fill=fill)


def _add_logo_watermark(
    image: Image.Image,
    *,
    center_y: int,
    width: int,
    available_height: int,
    width_ratio: float,
    opacity: float,
) -> None:
    """在卡片底色上叠加鱼师图像，后续数据文字会覆盖在水印上方。"""
    if not MARK_IMAGE.exists():
        return
    try:
        logo = Image.open(MARK_IMAGE).convert("RGBA")
        target_width = max(140, int(width * max(0.05, min(0.80, width_ratio))))
        ratio = target_width / max(1, logo.width)
        target_height = max(1, int(logo.height * ratio))
        max_height = max(120, int(available_height * 0.72))
        if target_height > max_height:
            ratio = max_height / max(1, logo.height)
            target_width = max(1, int(logo.width * ratio))
            target_height = max_height
        logo = logo.resize((target_width, target_height), Image.Resampling.LANCZOS)
        alpha = logo.getchannel("A").point(lambda value: int(value * max(0.0, min(0.35, opacity))))
        logo.putalpha(alpha)
        image.alpha_composite(logo, ((width - logo.width) // 2, center_y - logo.height // 2))
    except Exception:
        # Logo 读取异常不应影响地区数据抓取与图片生成。
        return


def _display_region_values(record: dict[str, Any]) -> dict[str, float]:
    """仅为图片展示做一位小数平衡舍入，底层数据和条形图仍使用真实权重。"""
    regions = _display_regions(record)
    total = sum(max(0.0, float(regions.get(key, 0.0) or 0.0)) for key in DISPLAY_REGION_ORDER)
    if total <= 0:
        return {key: 0.0 for key in DISPLAY_REGION_ORDER}

    # 最大余数法让展示值严格相加为 100.0%，避免一位小数四舍五入造成视觉误差。
    tenths: dict[str, int] = {}
    fractions: list[tuple[float, int, str]] = []
    for index, key in enumerate(DISPLAY_REGION_ORDER):
        exact = max(0.0, float(regions.get(key, 0.0) or 0.0)) / total * 1000
        base = int(math.floor(exact))
        tenths[key] = base
        fractions.append((exact - base, -index, key))
    remaining = 1000 - sum(tenths.values())
    for _, _, key in sorted(fractions, reverse=True)[: max(0, remaining)]:
        tenths[key] += 1
    return {key: tenths[key] / 10 for key in DISPLAY_REGION_ORDER}


def _subregion_lines(record: dict[str, Any]) -> tuple[str, str]:
    """二级地区固定分两行展示，避免把所有信息压成难以阅读的一整行。"""
    sub = record.get("subregions", {}) if isinstance(record, dict) else {}
    value = lambda key: float(sub.get(key, 0.0) or 0.0)
    first_line = (
        f"亚洲：日本 {value('日本'):.1f}% · 发达亚洲 {value('发达亚洲'):.1f}% · "
        f"新兴亚洲 {value('新兴亚洲'):.1f}% ｜ 大洋洲 {value('大洋洲'):.1f}% ｜ "
        f"非洲/中东 {value('非洲/中东'):.1f}%"
    )
    second_line = (
        f"美洲：北美 {value('北美'):.1f}% · 拉丁美洲 {value('拉丁美洲'):.1f}% ｜ "
        f"欧洲：英国 {value('英国'):.1f}% · 发达欧洲 {value('发达欧洲'):.1f}% · "
        f"新兴欧洲 {value('新兴欧洲'):.1f}%"
    )
    return first_line, second_line


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    """按实际字体宽度完整换行，不截断任何基金名称。"""
    text = str(text or "").strip() or "基金名称缺失"
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if not current or _font_size(draw, candidate, font)[0] <= max_width:
            current = candidate
            continue
        lines.append(current.rstrip())
        current = char.lstrip()
    if current:
        lines.append(current.rstrip())
    return lines or [text]


def _fit_name_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    font_size: int,
    min_font_size: int,
    max_width: int,
) -> tuple[list[str], ImageFont.ImageFont]:
    """优先两行展示完整名称；极端长名称允许更多行而不是省略内容。"""
    fallback_lines: list[str] = [str(text or "基金名称缺失")]
    fallback_font = get_watermark_font(min_font_size)
    for size in range(font_size, min_font_size - 1, -1):
        font = get_watermark_font(size)
        lines = _wrap_text(draw, text, font, max_width)
        fallback_lines, fallback_font = lines, font
        if len(lines) <= 2:
            return lines, font
    return fallback_lines, fallback_font


def _fit_font_for_lines(
    draw: ImageDraw.ImageDraw,
    lines: Iterable[str],
    *,
    font_size: int,
    min_font_size: int,
    max_width: int,
) -> ImageFont.ImageFont:
    values = list(lines)
    for size in range(font_size, min_font_size - 1, -1):
        font = get_watermark_font(size)
        if all(_font_size(draw, line, font)[0] <= max_width for line in values):
            return font
    return get_watermark_font(min_font_size)


def _primary_region_lines(
    draw: ImageDraw.ImageDraw,
    record: dict[str, Any],
    *,
    font_size: int,
    min_font_size: int,
    max_width: int,
) -> tuple[list[str], ImageFont.ImageFont]:
    values = _display_region_values(record)
    items = [f"{DISPLAY_REGION_LABELS[key]} {values[key]:.1f}%" for key in DISPLAY_REGION_ORDER]
    separator = "  ·  "
    for size in range(font_size, min_font_size - 1, -1):
        font = get_watermark_font(size)
        lines: list[str] = []
        current = ""
        for item in items:
            candidate = item if not current else current + separator + item
            if not current or _font_size(draw, candidate, font)[0] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = item
        if current:
            lines.append(current)
        if len(lines) <= 2:
            return lines, font
    return lines, get_watermark_font(min_font_size)


def _text_color_for_background(color: str) -> str:
    red, green, blue = ImageColor.getrgb(color)[:3]
    luminance = 0.299 * red + 0.587 * green + 0.114 * blue
    return "#0F172A" if luminance >= 165 else "#FFFFFF"


def _page_signature(records: Iterable[dict[str, Any]]) -> str:
    values = [
        {
            "fund_code": item.get("fund_code"),
            "fund_name": item.get("fund_name"),
            "fingerprint": item.get("fingerprint"),
            "valid": bool(item.get("valid")),
        }
        for item in records
    ]
    raw = json.dumps(
        {"render_version": PAGE_RENDER_VERSION, "records": values},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _page_output_file(page_number: int) -> Path:
    return REGION_LATEST_OUTPUT_DIR / f"{page_number}_海外基金地区分布.png"


def _pages_for_codes(fund_codes: list[str]) -> list[RegionPage]:
    per_page = max(1, int(FUND_REGION_ALLOCATION_IMAGE_STYLE["funds_per_page"]))
    pages: list[RegionPage] = []
    for start in range(0, len(fund_codes), per_page):
        number = start // per_page + 1
        pages.append(RegionPage(number, tuple(fund_codes[start : start + per_page]), _page_output_file(number)))
    return pages


def _draw_region_legend(
    draw: ImageDraw.ImageDraw,
    *,
    left: int,
    top: int,
    right: int,
    font: ImageFont.ImageFont,
    text_color: str,
) -> int:
    """在页眉绘制完整地区图例，宽度不足时自然换到下一行。"""
    swatch = 16
    line_height = max(swatch, _font_size(draw, "地区", font)[1]) + 8
    x, y = left, top
    for key in DISPLAY_REGION_ORDER:
        label = DISPLAY_REGION_LABELS[key]
        label_width, _ = _font_size(draw, label, font)
        item_width = swatch + 8 + label_width + 22
        if x > left and x + item_width > right:
            x = left
            y += line_height
        draw.rounded_rectangle((x, y + 2, x + swatch, y + 2 + swatch), radius=4, fill=DISPLAY_REGION_COLORS[key])
        draw.text((x + swatch + 8, y), label, font=font, fill=text_color)
        x += item_width
    return y + line_height


def _build_card_layouts(
    draw: ImageDraw.ImageDraw,
    records: list[dict[str, Any]],
    *,
    content_width: int,
    style: dict[str, Any],
) -> list[dict[str, Any]]:
    """先测量所有文本，再确定每张卡片高度，避免发布图出现重叠。"""
    font_sizes = style["font_sizes"]
    padding = int(style["card_padding_px"])
    code_font = get_watermark_font(int(font_sizes["code"]))
    date_font = get_watermark_font(int(font_sizes["report_date"]))
    layouts: list[dict[str, Any]] = []

    for record in records:
        code = str(record.get("fund_code", ""))
        date_text = str(record.get("report_date", ""))
        code_width, code_height = _font_size(draw, code, code_font)
        date_width, date_height = _font_size(draw, date_text, date_font)
        name_max_width = max(220, content_width - padding * 2 - code_width - date_width - 42)
        name_lines, name_font = _fit_name_lines(
            draw,
            str(record.get("fund_name", "基金名称缺失")),
            font_size=int(font_sizes["fund_name"]),
            min_font_size=int(font_sizes["fund_name_min"]),
            max_width=name_max_width,
        )
        name_line_height = max(int(style["long_name_line_height_px"]), _font_size(draw, "基金", name_font)[1] + 5)
        primary_lines, primary_font = _primary_region_lines(
            draw,
            record,
            font_size=int(font_sizes["primary"]),
            min_font_size=int(font_sizes["primary_min"]),
            max_width=content_width - padding * 2,
        )
        primary_line_height = _font_size(draw, "地区 100.0%", primary_font)[1] + int(style["primary_line_spacing_px"])
        detail_lines = list(_subregion_lines(record))
        detail_font = _fit_font_for_lines(
            draw,
            detail_lines,
            font_size=int(font_sizes["subregion"]),
            min_font_size=int(font_sizes["subregion_min"]),
            max_width=content_width - padding * 2,
        )
        detail_line_height = _font_size(draw, "发达亚洲 100.0%", detail_font)[1] + int(style["detail_line_spacing_px"])
        title_height = max(code_height, date_height, name_line_height * len(name_lines))
        required_height = (
            padding
            + title_height
            + int(style["title_to_bar_gap_px"])
            + int(style["bar_height_px"])
            + int(style["bar_to_primary_gap_px"])
            + primary_line_height * len(primary_lines)
            + int(style["primary_to_detail_gap_px"])
            + detail_line_height * len(detail_lines)
            + padding
        )
        layouts.append(
            {
                "record": record,
                "code": code,
                "date_text": date_text,
                "code_font": code_font,
                "date_font": date_font,
                "name_font": name_font,
                "name_lines": name_lines,
                "name_line_height": name_line_height,
                "primary_font": primary_font,
                "primary_lines": primary_lines,
                "primary_line_height": primary_line_height,
                "detail_font": detail_font,
                "detail_lines": detail_lines,
                "detail_line_height": detail_line_height,
                "height": max(int(style["card_height_px"]), required_height),
            }
        )
    return layouts


def save_region_page_image(records: list[dict[str, Any]], output_file: str | Path, *, page_number: int, page_count: int) -> Path:
    """生成一张 1080px 竖版、适合研究简报与手机阅读的地区分布图。"""
    records = [record for record in records if _is_valid_record(record)]
    if not records:
        raise ValueError("当前分页没有可展示的晨星地区数据")
    ensure_runtime_dirs()
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    style = FUND_REGION_ALLOCATION_IMAGE_STYLE
    width = max(1, int(style["canvas_width_px"]))
    top_margin = max(0, int(style["top_margin_px"]))
    bottom_margin = max(0, int(style["bottom_margin_px"]))
    left_margin = max(0, int(style["left_margin_px"]))
    right_margin = max(0, int(style["right_margin_px"]))
    content_width = width - left_margin - right_margin
    font_sizes = style["font_sizes"]
    colors = style["colors"]

    metrics_image = Image.new("RGBA", (width, 8), colors["background"])
    metrics_draw = ImageDraw.Draw(metrics_image)
    layouts = _build_card_layouts(metrics_draw, records, content_width=content_width, style=style)
    title_font = get_watermark_font(int(font_sizes["title"]))
    subtitle_font = get_watermark_font(int(font_sizes["subtitle"]))
    legend_font = get_watermark_font(int(font_sizes["legend"]))
    page_font = get_watermark_font(int(font_sizes["page"]))
    footer_font = get_watermark_font(int(font_sizes["footer"]))
    author_font = get_watermark_font(int(font_sizes["author"]))
    title_height = _font_size(metrics_draw, "海外基金股票持仓地区分布", title_font)[1]
    subtitle_height = _font_size(metrics_draw, "地区权重为各基金股票持仓内部占比，不代表基金全部资产配置。", subtitle_font)[1]
    legend_bottom = _draw_region_legend(
        metrics_draw,
        left=left_margin,
        top=top_margin + title_height + 17 + subtitle_height + 15,
        right=width - right_margin,
        font=legend_font,
        text_color=colors["secondary_text"],
    )
    cards_top = legend_bottom + 20
    card_gap = max(0, int(style["card_gap_px"]))
    cards_height = sum(int(item["height"]) for item in layouts) + card_gap * max(0, len(layouts) - 1)
    cards_bottom = cards_top + cards_height
    footer_height = max(
        78,
        _font_size(metrics_draw, "个人模型观察，不构成任何投资建议", footer_font)[1]
        + _font_size(metrics_draw, "作者：鱼师AHNS", author_font)[1]
        + 28,
    )
    height = cards_bottom + 24 + footer_height + bottom_margin

    image = Image.new("RGBA", (width, height), colors["background"])
    draw = ImageDraw.Draw(image)
    draw.text((left_margin, top_margin), "海外基金股票持仓地区分布", font=title_font, fill=colors["title"])
    page_text = f"第 {page_number}/{page_count} 页"
    page_width, _ = _font_size(draw, page_text, page_font)
    draw.text((width - right_margin - page_width, top_margin + 13), page_text, font=page_font, fill=colors["muted_text"])
    subtitle_y = top_margin + title_height + 17
    draw.text(
        (left_margin, subtitle_y),
        "地区权重为各基金股票持仓内部占比，不代表基金全部资产配置。",
        font=subtitle_font,
        fill=colors["secondary_text"],
    )
    _draw_region_legend(
        draw,
        left=left_margin,
        top=subtitle_y + subtitle_height + 15,
        right=width - right_margin,
        font=legend_font,
        text_color=colors["secondary_text"],
    )

    # 第一阶段只绘制卡片底色，保证水印位于卡片承载层而不是白底下方。
    y = cards_top
    for index, layout in enumerate(layouts, start=1):
        layout["top"] = y
        layout["bottom"] = y + int(layout["height"])
        draw.rounded_rectangle(
            (left_margin, layout["top"], width - right_margin, layout["bottom"]),
            radius=int(style["card_corner_radius_px"]),
            fill=colors["card_background"] if index % 2 else colors["card_alternate_background"],
            outline=colors["card_border"],
            width=1,
        )
        y = int(layout["bottom"]) + card_gap

    _add_logo_watermark(
        image,
        center_y=cards_top + (cards_bottom - cards_top) // 2,
        width=width,
        available_height=cards_bottom - cards_top,
        width_ratio=float(style["logo_width_ratio"]),
        opacity=float(style["logo_opacity"]),
    )
    draw = ImageDraw.Draw(image)

    # 第二阶段将文字和条形图绘制在水印上方，数据始终是最强视觉层级。
    padding = int(style["card_padding_px"])
    label_threshold = float(style["bar_label_min_pct"])
    bar_label_font = get_watermark_font(int(font_sizes["bar_label"]))
    for layout in layouts:
        row_top = int(layout["top"])
        row_bottom = int(layout["bottom"])
        title_top = row_top + padding
        code_font = layout["code_font"]
        date_font = layout["date_font"]
        name_font = layout["name_font"]
        name_lines = layout["name_lines"]
        code_width, code_height = _font_size(draw, layout["code"], code_font)
        date_width, date_height = _font_size(draw, layout["date_text"], date_font)
        name_line_height = int(layout["name_line_height"])
        code_x = left_margin + padding
        name_x = code_x + code_width + 18
        draw.text((code_x, title_top + max(0, (name_line_height - code_height) // 2)), layout["code"], font=code_font, fill=colors["fund_code"])
        for line_index, name_line in enumerate(name_lines):
            draw.text((name_x, title_top + line_index * name_line_height), name_line, font=name_font, fill=colors["title"])
        draw.text(
            (width - right_margin - padding - date_width, title_top + max(0, (name_line_height - date_height) // 2)),
            layout["date_text"],
            font=date_font,
            fill=colors["muted_text"],
        )

        bar_left = left_margin + padding
        bar_right = width - right_margin - padding
        bar_top = title_top + name_line_height * len(name_lines) + int(style["title_to_bar_gap_px"])
        bar_bottom = bar_top + int(style["bar_height_px"])
        draw.rounded_rectangle((bar_left, bar_top, bar_right, bar_bottom), radius=8, fill=colors["bar_background"])
        regions = _display_regions(layout["record"])
        shown_values = _display_region_values(layout["record"])
        total = sum(max(0.0, float(regions.get(key, 0.0) or 0.0)) for key in DISPLAY_REGION_ORDER)
        active_keys = [key for key in DISPLAY_REGION_ORDER if float(regions.get(key, 0.0) or 0.0) > 0.00001]
        cursor = bar_left
        for index, key in enumerate(active_keys):
            value = max(0.0, float(regions.get(key, 0.0) or 0.0))
            next_cursor = bar_right if index == len(active_keys) - 1 else min(bar_right, cursor + round((bar_right - bar_left) * value / max(total, 0.00001)))
            if next_cursor <= cursor:
                continue
            color = DISPLAY_REGION_COLORS[key]
            draw.rectangle((cursor, bar_top, next_cursor, bar_bottom), fill=color)
            label = f"{shown_values[key]:.1f}%"
            label_width, _ = _font_size(draw, label, bar_label_font)
            if shown_values[key] >= label_threshold and label_width + 12 <= next_cursor - cursor:
                _draw_centered(draw, (cursor, bar_top, next_cursor, bar_bottom), label, bar_label_font, _text_color_for_background(color))
            cursor = next_cursor

        primary_top = bar_bottom + int(style["bar_to_primary_gap_px"])
        for line_index, line in enumerate(layout["primary_lines"]):
            draw.text((bar_left, primary_top + line_index * int(layout["primary_line_height"])), line, font=layout["primary_font"], fill=colors["body_text"])
        detail_top = (
            primary_top
            + int(layout["primary_line_height"]) * len(layout["primary_lines"])
            + int(style["primary_to_detail_gap_px"])
        )
        for line_index, line in enumerate(layout["detail_lines"]):
            draw.text((bar_left, detail_top + line_index * int(layout["detail_line_height"])), line, font=layout["detail_font"], fill=colors["muted_text"])
        content_bottom = detail_top + int(layout["detail_line_height"]) * len(layout["detail_lines"])
        if content_bottom > row_bottom - padding + 2:
            raise RuntimeError(f"地区图卡片内容超出边界: {layout['code']}")

    footer_y = cards_bottom + 24
    _draw_centered(draw, (left_margin, footer_y, width - right_margin, footer_y + 30), "个人模型观察，不构成任何投资建议", footer_font, colors["footer"])
    _draw_centered(draw, (left_margin, footer_y + 34, width - right_margin, footer_y + 58), "作者：鱼师AHNS", author_font, colors["author"])
    image.convert("RGB").save(
        output_path,
        dpi=(max(1, int(style["export_dpi"])), max(1, int(style["export_dpi"]))),
        compress_level=6,
    )
    return output_path


def _load_state() -> dict[str, Any]:
    state = _load_json(FUND_REGION_ALLOCATION_STATE_CACHE, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("funds", {})
    state.setdefault("pages", {})
    return state


def _record_state_item(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "fingerprint": str(record.get("fingerprint", "")),
        "report_date": str(record.get("report_date", "")),
        "last_checked_at": _now_text(),
        "valid": bool(record.get("valid", False)),
    }


def _records_for_page(page: RegionPage, records: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(records.get(code, {"fund_code": code, "valid": False})) for code in page.fund_codes]


def _displayable_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """只保留可发布的有效地区记录；失败记录不占用图片版面。"""
    return [record for record in records if _is_valid_record(record)]


def _print_error_summary(errors: list[dict[str, str]]) -> None:
    if not errors:
        return
    print_records_table(errors, title="晨星地区分布获取失败（已继续运行）", columns=["基金代码", "基金名称", "错误"])


def run_auto(*, refresh: bool = False) -> int:
    fund_codes = _candidate_fund_codes()
    print_stage(f"晨星地区分布自动检测 开始，共 {len(fund_codes)} 只海外基金")
    records, errors = _resolve_records(fund_codes, refresh=refresh)
    pages = _pages_for_codes(fund_codes)
    state = _load_state()
    page_state = state.get("pages", {}) if isinstance(state.get("pages"), dict) else {}
    changed_pages: list[Path] = []

    for page in pages:
        page_records = _records_for_page(page, records)
        signature = _page_signature(page_records)
        old = page_state.get(str(page.number), {}) if isinstance(page_state.get(str(page.number)), dict) else {}
        if str(old.get("signature", "")) == signature and page.output_file.exists():
            continue
        visible_records = _displayable_records(page_records)
        if not visible_records:
            print(
                f"[MORNINGSTAR] 第 {page.number}/{len(pages)} 页暂无有效地区数据，"
                "本次不生成图片。",
                flush=True,
            )
            page_state[str(page.number)] = {
                "signature": signature,
                "fund_codes": list(page.fund_codes),
                "output_file": relative_path_str(page.output_file),
                "last_generated_at": str(old.get("last_generated_at", "")),
            }
            continue
        saved = save_region_page_image(visible_records, page.output_file, page_number=page.number, page_count=len(pages))
        changed_pages.append(saved)
        page_state[str(page.number)] = {
            "signature": signature,
            "fund_codes": list(page.fund_codes),
            "output_file": relative_path_str(saved),
            "last_generated_at": _now_text(),
        }

    state["funds"] = {code: _record_state_item(record) for code, record in records.items()}
    state["pages"] = page_state
    state["updated_at"] = _now_text()
    _write_json(FUND_REGION_ALLOCATION_STATE_CACHE, state)
    print_key_values(
        "晨星地区分布自动检测汇总",
        [
            ("基金数量", len(fund_codes)),
            ("更新页面", len(changed_pages)),
            ("晨星失败", len(errors)),
            ("缓存周期", f"{FUND_REGION_ALLOCATION_CACHE_DAYS} 天"),
        ],
    )
    for path in changed_pages:
        print(f"地区分布图已生成或更新: {relative_path_str(path)}", flush=True)
    _print_error_summary(errors)
    # 非零退出码会让总入口把失败摘要写入邮件，但 git_main.py 会继续后续步骤。
    return 1 if errors else 0


def run_all(*, refresh: bool = False) -> int:
    fund_codes = _candidate_fund_codes()
    print_stage(f"晨星地区分布全量生成 开始，共 {len(fund_codes)} 只海外基金")
    records, errors = _resolve_records(fund_codes, refresh=refresh)
    pages = _pages_for_codes(fund_codes)
    for page in pages:
        visible_records = _displayable_records(_records_for_page(page, records))
        if not visible_records:
            print(f"[MORNINGSTAR] 第 {page.number}/{len(pages)} 页暂无有效地区数据，未生成图片。", flush=True)
            continue
        saved = save_region_page_image(
            visible_records,
            page.output_file,
            page_number=page.number,
            page_count=len(pages),
        )
        print(f"地区分布图已保存: {relative_path_str(saved)}", flush=True)
    _print_error_summary(errors)
    return 1 if errors else 0


def run_single(fund_code: str, *, refresh: bool = False) -> int:
    code = _normalize_fund_code(fund_code)
    print_stage(f"开始生成 {code} 晨星股票地区分布图")
    records, errors = _resolve_records([code], refresh=refresh)
    output_file = REGION_MANUAL_OUTPUT_DIR / f"{code}_基金地区分布.png"
    visible_records = _displayable_records([records[code]])
    if visible_records:
        saved = save_region_page_image(visible_records, output_file, page_number=1, page_count=1)
        print(f"地区分布图已保存: {relative_path_str(saved)}", flush=True)
    else:
        print(f"[MORNINGSTAR] {code} 暂无有效地区数据，本次未生成图片。", flush=True)
    _print_error_summary(errors)
    return 1 if errors else 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成晨星海外基金股票地区分布抖音竖版对比图")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="生成海外基金池全部分页对比图（默认）")
    mode.add_argument("--auto", action="store_true", help="仅在晨星披露日期或地区权重变化时更新页面")
    mode.add_argument("--fund-code", help="生成指定 6 位基金代码的单页地区分布图")
    parser.add_argument("--refresh", action="store_true", help="忽略地区缓存，强制晨星直连刷新")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.fund_code:
        return run_single(args.fund_code, refresh=bool(args.refresh))
    if args.auto:
        return run_auto(refresh=bool(args.refresh))
    return run_all(refresh=bool(args.refresh))


__all__ = [
    "_direct_morningstar_session",
    "_parse_region_table",
    "run_all",
    "run_auto",
    "run_single",
    "save_region_page_image",
    "main",
]
