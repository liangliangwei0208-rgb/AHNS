"""
基金前十大持仓变化图。

这个工具面向个人复盘：只使用基金披露的真实持仓字段，比较最新一期
和上一期前十大股票持仓的变化，不展示再分配权重，也不参与正式估算缓存。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import time
import warnings
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd
import requests
from PIL import Image, ImageDraw, ImageFont

from tools.console_display import print_key_values, print_records_table, print_stage
from tools.configs.fund_universe_configs import HAIWAI_FUND_CODES
from tools.get_top10_holdings import detect_market_and_ticker, quarter_key
from tools.paths import (
    FUND_ESTIMATE_CACHE,
    FUND_HOLDING_CHANGE_BATCH_STATE_CACHE,
    FUND_HOLDING_CHANGE_STATE_CACHE,
    FUND_HOLDINGS_CACHE,
    MARK_IMAGE,
    OUTPUT_DIR,
    ensure_runtime_dirs,
    relative_path_str,
)
from tools.safe_display import get_watermark_font


DEFAULT_FUND_CODE = "012922"
HOLDING_CHANGE_OUTPUT_DIR = OUTPUT_DIR / "fund_holding_change"
HOLDING_CHANGE_LATEST_DIR = HOLDING_CHANGE_OUTPUT_DIR / "latest"
HOLDING_CHANGE_PREVIOUS_DIR = HOLDING_CHANGE_OUTPUT_DIR / "previous"
HOLDING_CHANGE_MANUAL_DIR = HOLDING_CHANGE_OUTPUT_DIR / "manual"
DEFAULT_OUTPUT = HOLDING_CHANGE_MANUAL_DIR / f"{DEFAULT_FUND_CODE}.png"
BATCH_CLEANUP_GRACE_DAYS = 3
EASTMONEY_HOLDING_URL = "http://fundf10.eastmoney.com/FundArchivesDatas.aspx"
EASTMONEY_REFERER = "http://fundf10.eastmoney.com/ccmx_{fund_code}.html"

FUND_NAME_FALLBACKS = {
    "012922": "易方达全球成长精选混合(QDII)人民币C",
}


@dataclass
class HoldingPeriod:
    quarter_key: int
    quarter_label: str
    df: pd.DataFrame
    source: str


@dataclass
class HoldingChangeResult:
    fund_code: str
    fund_name: str
    latest: HoldingPeriod
    previous: HoldingPeriod
    change_df: pd.DataFrame
    latest_table_df: pd.DataFrame
    previous_table_df: pd.DataFrame
    summary: dict[str, Any]


def _normalize_fund_code(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return DEFAULT_FUND_CODE
    digits = re.sub(r"\D", "", text)
    return digits.zfill(6) if digits else text.zfill(6)


def _normalize_optional_fund_code(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    digits = re.sub(r"\D", "", text)
    if not digits:
        raise ValueError(f"基金代码必须包含数字，当前为: {text!r}")
    if len(digits) > 6:
        raise ValueError(f"基金代码必须是单个 6 位代码，当前为: {text!r}")
    return digits.zfill(6)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace("%", "").replace(",", "")
        if value in {"", "--", "-", "nan", "None"}:
            return None
    try:
        out = float(value)
        if not math.isfinite(out):
            return None
        return out
    except Exception:
        return None


def _fmt_number(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return "--"
    return f"{number:,.{digits}f}"


def _fmt_pct(value: Any, *, signed: bool = False, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return "--"
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:.{digits}f}%"


def _quarter_label_from_key(qkey: int) -> str:
    year = int(qkey) // 10
    quarter = int(qkey) % 10
    if year > 0 and quarter in {1, 2, 3, 4}:
        return f"{year}年{quarter}季度股票投资明细"
    return str(qkey)


def _previous_quarter_key(qkey: int) -> int:
    year = int(qkey) // 10
    quarter = int(qkey) % 10
    if quarter <= 1:
        return (year - 1) * 10 + 4
    return year * 10 + (quarter - 1)


def _security_key(row: pd.Series | dict[str, Any]) -> tuple[str, str]:
    market = str(row.get("市场", "") or "").strip().upper()
    ticker = str(row.get("ticker", "") or row.get("股票代码", "") or "").strip().upper()
    return market, ticker


def _display_ticker(row: pd.Series | dict[str, Any]) -> str:
    ticker = str(row.get("ticker", "") or row.get("股票代码", "") or "").strip()
    code = str(row.get("股票代码", "") or "").strip()
    return ticker or code or "--"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _cached_period_from_item(fund_code: str, top_n: int, item: dict[str, Any]) -> HoldingPeriod | None:
    payload = item.get("data_json")
    if not payload:
        return None
    rows = json.loads(payload) if isinstance(payload, str) else payload
    df = pd.DataFrame(rows)
    if df.empty:
        return None

    df = _normalize_holdings_df(df, fund_code=fund_code, top_n=top_n, source="cache")
    qkey = int(item.get("latest_quarter_key") or df["_quarter_key"].max())
    qlabel = str(item.get("latest_quarter_label") or _quarter_label_from_key(qkey))
    return HoldingPeriod(qkey, qlabel, df, "cache/fund_holdings_cache.json")


def _load_cached_latest_period(fund_code: str, top_n: int) -> HoldingPeriod | None:
    cache = _load_json(FUND_HOLDINGS_CACHE, {})
    item = cache.get(f"{fund_code}:top{top_n}")
    if not isinstance(item, dict):
        return None
    return _cached_period_from_item(fund_code, top_n, item)


def _fund_name_from_cache_or_fallback(fund_code: str) -> str:
    try:
        cache = _load_json(FUND_ESTIMATE_CACHE, {})
        records = cache.get("records", {}) if isinstance(cache, dict) else {}
        candidates = [
            item
            for item in records.values()
            if isinstance(item, dict) and str(item.get("fund_code", "")).zfill(6) == fund_code
        ]
        candidates.sort(key=lambda item: str(item.get("valuation_anchor_date", "")), reverse=True)
        for item in candidates:
            name = str(item.get("fund_name", "") or "").strip()
            if name:
                return name
    except Exception:
        pass
    return FUND_NAME_FALLBACKS.get(fund_code, f"基金{fund_code}")


def _normalize_holdings_df(
    raw_df: pd.DataFrame,
    *,
    fund_code: str,
    top_n: int,
    source: str,
    quarter_label: str | None = None,
) -> pd.DataFrame:
    df = raw_df.copy()

    rename_map = {
        "持股数 （万股）": "持股数",
        "持仓市值 （万元人民币）": "持仓市值",
    }
    df = df.rename(columns=rename_map)

    required = ["股票代码", "股票名称", "占净值比例"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise RuntimeError(f"{fund_code} 持仓数据缺少必要字段: {missing}; source={source}")

    if "季度" not in df.columns:
        if not quarter_label:
            raise RuntimeError(f"{fund_code} 持仓数据缺少季度字段; source={source}")
        df["季度"] = quarter_label

    df["占净值比例"] = df["占净值比例"].map(_safe_float)
    df["持股数"] = df["持股数"].map(_safe_float) if "持股数" in df.columns else None
    df["持仓市值"] = df["持仓市值"].map(_safe_float) if "持仓市值" in df.columns else None
    df["_quarter_key"] = df["季度"].map(quarter_key)
    df = df.dropna(subset=["占净值比例"])
    df = df[df["_quarter_key"] >= 0].copy()
    if df.empty:
        raise RuntimeError(f"{fund_code} 持仓数据清洗后为空; source={source}")

    if "市场" not in df.columns or "ticker" not in df.columns:
        df[["市场", "ticker"]] = df.apply(
            lambda row: pd.Series(detect_market_and_ticker(row["股票代码"], row["股票名称"])),
            axis=1,
        )

    # 同一季度内只保留真实披露权重最大的前 N 大。
    selected_frames: list[pd.DataFrame] = []
    for qkey, group in df.groupby("_quarter_key"):
        group = group.sort_values("占净值比例", ascending=False).head(int(top_n)).copy()
        group["序号"] = range(1, len(group) + 1)
        selected_frames.append(group)
    out = pd.concat(selected_frames, ignore_index=True)

    keep_cols = [
        "序号",
        "股票代码",
        "股票名称",
        "占净值比例",
        "持股数",
        "持仓市值",
        "季度",
        "_quarter_key",
        "市场",
        "ticker",
    ]
    for col in keep_cols:
        if col not in out.columns:
            out[col] = None
    return out[keep_cols].copy()


def _holding_fingerprint(period: HoldingPeriod, *, top_n: int) -> str:
    rows: list[dict[str, Any]] = []
    work = period.df.sort_values("序号").head(int(top_n))
    for row in work.to_dict(orient="records"):
        rows.append(
            {
                "rank": int(row.get("序号") or 0),
                "stock_code": str(row.get("股票代码") or "").strip(),
                "stock_name": str(row.get("股票名称") or "").strip(),
                "market": str(row.get("市场") or "").strip().upper(),
                "ticker": str(row.get("ticker") or "").strip().upper(),
                "weight_pct": _safe_float(row.get("占净值比例")),
                "shares": _safe_float(row.get("持股数")),
                "market_value": _safe_float(row.get("持仓市值")),
            }
        )
    payload = {
        "quarter_key": int(period.quarter_key),
        "quarter_label": str(period.quarter_label),
        "top_n": int(top_n),
        "rows": rows,
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _state_entry_from_period(
    *,
    fund_code: str,
    top_n: int,
    period: HoldingPeriod,
    image_path: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    entry: dict[str, Any] = {
        "fund_code": fund_code,
        "top_n": int(top_n),
        "latest_quarter_key": int(period.quarter_key),
        "latest_quarter_label": str(period.quarter_label),
        "fingerprint": _holding_fingerprint(period, top_n=top_n),
        "last_checked_at": now,
    }
    if image_path is not None:
        entry["last_image"] = relative_path_str(image_path)
    if generated_at is not None:
        entry["last_generated_at"] = generated_at
    return entry


def _load_change_state() -> dict[str, Any]:
    data = _load_json(FUND_HOLDING_CHANGE_STATE_CACHE, {})
    return data if isinstance(data, dict) else {}


def _save_change_state(state: dict[str, Any]) -> None:
    _write_json(FUND_HOLDING_CHANGE_STATE_CACHE, state)


def _load_batch_state() -> dict[str, Any]:
    data = _load_json(FUND_HOLDING_CHANGE_BATCH_STATE_CACHE, {})
    return data if isinstance(data, dict) else {}


def _save_batch_state(state: dict[str, Any]) -> None:
    _write_json(FUND_HOLDING_CHANGE_BATCH_STATE_CACHE, state)


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _repo_path_from_text(value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    return path if path.is_absolute() else OUTPUT_DIR.parent / path


def _is_safe_holding_change_file(path: Path) -> bool:
    try:
        resolved = path.resolve()
        root = HOLDING_CHANGE_OUTPUT_DIR.resolve()
        return resolved.is_file() and resolved.is_relative_to(root) and resolved.suffix.lower() == ".png"
    except Exception:
        return False


def _delete_previous_batch_images(previous_batch: dict[str, Any]) -> int:
    deleted = 0
    funds = previous_batch.get("funds")
    if not isinstance(funds, dict):
        return deleted

    for item in funds.values():
        if not isinstance(item, dict):
            continue
        path = _repo_path_from_text(item.get("image"))
        if path is None or not _is_safe_holding_change_file(path):
            continue
        # 只删除批次状态中记录的明确文件路径，避免通配符或递归删除。
        path.unlink()
        deleted += 1
        print(f"[HOLDING_CHANGE] 清理上一轮图片: {relative_path_str(path)}", flush=True)
    return deleted


def _move_current_batch_to_previous(batch_state: dict[str, Any]) -> None:
    current = batch_state.get("current")
    if not isinstance(current, dict) or not current.get("funds"):
        batch_state["current"] = None
        return

    funds = current.get("funds")
    if not isinstance(funds, dict):
        batch_state["current"] = None
        return

    HOLDING_CHANGE_PREVIOUS_DIR.mkdir(parents=True, exist_ok=True)
    previous = dict(current)
    previous["archived_at"] = datetime.now().isoformat(timespec="seconds")
    previous_funds: dict[str, Any] = {}

    for fund_code, item in funds.items():
        if not isinstance(item, dict):
            continue
        next_item = dict(item)
        source = _repo_path_from_text(next_item.get("image"))
        if source is not None and _is_safe_holding_change_file(source):
            target = HOLDING_CHANGE_PREVIOUS_DIR / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            next_item["image"] = relative_path_str(target)
            print(
                f"[HOLDING_CHANGE] 上一轮图片归档: {relative_path_str(source)} -> {relative_path_str(target)}",
                flush=True,
            )
        previous_funds[str(fund_code)] = next_item

    previous["funds"] = previous_funds
    batch_state["previous"] = previous
    batch_state["current"] = None


def _new_batch_record(*, period: HoldingPeriod, top_n: int, fund_pool_count: int, now: str) -> dict[str, Any]:
    return {
        "batch_key": f"top{int(top_n)}:{int(period.quarter_key)}",
        "top_n": int(top_n),
        "quarter_key": int(period.quarter_key),
        "quarter_label": str(period.quarter_label),
        "fund_pool_count": int(fund_pool_count),
        "first_generated_at": now,
        "updated_at": now,
        "funds": {},
    }


def _current_batch_for_period(
    *,
    batch_state: dict[str, Any],
    period: HoldingPeriod,
    top_n: int,
    fund_pool_count: int,
    now: str,
) -> dict[str, Any]:
    expected_key = f"top{int(top_n)}:{int(period.quarter_key)}"
    current = batch_state.get("current")
    if not isinstance(current, dict) or current.get("batch_key") != expected_key:
        _move_current_batch_to_previous(batch_state)
        current = _new_batch_record(period=period, top_n=top_n, fund_pool_count=fund_pool_count, now=now)
        batch_state["current"] = current
        print(
            f"[HOLDING_CHANGE] 开始新的持仓披露批次: {current['quarter_label']}，基金池 {fund_pool_count} 只",
            flush=True,
        )
    else:
        current["fund_pool_count"] = int(fund_pool_count)
        current["updated_at"] = now
    return current


def _next_batch_index(current_batch: dict[str, Any]) -> int:
    funds = current_batch.get("funds")
    if not isinstance(funds, dict) or not funds:
        return 1
    indexes = []
    for item in funds.values():
        if isinstance(item, dict):
            try:
                indexes.append(int(item.get("index") or 0))
            except Exception:
                continue
    return max(indexes or [0]) + 1


def _batch_output_for_fund(
    *,
    batch_state: dict[str, Any],
    result: HoldingChangeResult,
    top_n: int,
    fund_pool_count: int,
    generated_at: str,
) -> tuple[Path, int]:
    current = _current_batch_for_period(
        batch_state=batch_state,
        period=result.latest,
        top_n=top_n,
        fund_pool_count=fund_pool_count,
        now=generated_at,
    )
    funds = current.setdefault("funds", {})
    if not isinstance(funds, dict):
        funds = {}
        current["funds"] = funds

    existing = funds.get(result.fund_code)
    if isinstance(existing, dict) and existing.get("index"):
        index = int(existing["index"])
    else:
        index = _next_batch_index(current)

    image_path = HOLDING_CHANGE_LATEST_DIR / f"{index}_{result.fund_code}.png"
    return image_path, index


def _record_batch_image(
    *,
    batch_state: dict[str, Any],
    result: HoldingChangeResult,
    image_path: Path,
    index: int,
    fund_pool_count: int,
    generated_at: str,
) -> None:
    current = batch_state.get("current")
    if not isinstance(current, dict):
        return
    funds = current.setdefault("funds", {})
    if not isinstance(funds, dict):
        funds = {}
        current["funds"] = funds
    funds[result.fund_code] = {
        "fund_code": result.fund_code,
        "fund_name": result.fund_name,
        "index": int(index),
        "image": relative_path_str(image_path),
        "quarter_key": int(result.latest.quarter_key),
        "quarter_label": str(result.latest.quarter_label),
        "generated_at": generated_at,
    }
    current["fund_pool_count"] = int(fund_pool_count)
    current["updated_at"] = generated_at


def _maybe_cleanup_previous_batch(batch_state: dict[str, Any], *, now: datetime) -> int:
    current = batch_state.get("current")
    previous = batch_state.get("previous")
    if not isinstance(current, dict) or not isinstance(previous, dict):
        return 0

    funds = current.get("funds")
    current_count = len(funds) if isinstance(funds, dict) else 0
    fund_pool_count = int(current.get("fund_pool_count") or 0)
    first_generated_at = _parse_iso_datetime(current.get("first_generated_at"))
    if current_count < fund_pool_count or fund_pool_count <= 0 or first_generated_at is None:
        return 0
    if (now - first_generated_at).days < BATCH_CLEANUP_GRACE_DAYS:
        return 0

    deleted = _delete_previous_batch_images(previous)
    batch_state["last_cleaned_previous"] = {
        "batch_key": previous.get("batch_key"),
        "quarter_label": previous.get("quarter_label"),
        "deleted_images": deleted,
        "cleaned_at": now.isoformat(timespec="seconds"),
    }
    batch_state["previous"] = None
    print(
        f"[HOLDING_CHANGE] 当前批次已满 {current_count}/{fund_pool_count} 且超过 {BATCH_CLEANUP_GRACE_DAYS} 天，"
        f"清理上一轮持仓变化图片 {deleted} 张",
        flush=True,
    )
    return deleted


def _manual_output_for_fund(fund_code: str) -> Path:
    return HOLDING_CHANGE_MANUAL_DIR / f"{fund_code}.png"


def _fetch_holdings_by_akshare(fund_code: str, years: list[int]) -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    for year in years:
        try:
            df = ak.fund_portfolio_hold_em(symbol=fund_code, date=str(year))
            if df is not None and not df.empty:
                df = df.copy()
                df["查询年份"] = str(year)
                frames.append(df)
        except Exception as exc:
            errors.append(f"{year}: {exc}")
    if frames:
        return frames
    raise RuntimeError("AkShare 持仓接口失败: " + " | ".join(errors[-3:]))


def _extract_eastmoney_content(text: str) -> str:
    start = text.find('content:"')
    end = text.find('",arryear', start)
    if start < 0 or end <= start:
        raise RuntimeError("东方财富 HTTP 返回内容格式异常，未找到 content 字段")
    content = text[start + len('content:"') : end]
    return html.unescape(content.replace("\\/", "/").replace('\\"', '"'))


def _fetch_holdings_by_eastmoney_http(fund_code: str, years: list[int]) -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": EASTMONEY_REFERER.format(fund_code=fund_code),
    }

    for year in years:
        last_error = ""
        year_success = False
        for attempt in range(1, 7):
            try:
                resp = session.get(
                    EASTMONEY_HOLDING_URL,
                    params={
                        "type": "jjcc",
                        "code": fund_code,
                        "topline": "10000",
                        "year": str(year),
                        "month": "",
                        "rt": f"{time.time():.12f}",
                    },
                    headers=headers,
                    timeout=20,
                )
                if resp.status_code != 200 or not resp.text:
                    raise RuntimeError(f"HTTP {resp.status_code}, len={len(resp.text)}")
                content = _extract_eastmoney_content(resp.text)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", FutureWarning)
                    tables = pd.read_html(StringIO(content))
                year_frames = []
                quarter_labels = re.findall(r"(\d{4}年[1-4]季度股票投资明细)", content)
                for index, table in enumerate(tables):
                    table = table.copy()
                    if "股票代码" not in table.columns or "占净值比例" not in table.columns:
                        continue
                    if "季度" not in table.columns:
                        label = quarter_labels[index] if index < len(quarter_labels) else ""
                        table["季度"] = label or _quarter_label_from_key(year * 10)
                    table["查询年份"] = str(year)
                    year_frames.append(table)
                if year_frames:
                    frames.extend(year_frames)
                    year_success = True
                    break
                raise RuntimeError("未解析到股票持仓表")
            except Exception as exc:
                last_error = str(exc)
                time.sleep(0.8 * attempt)
        if last_error and not year_success:
            errors.append(f"{year}: {last_error}")

    if frames:
        return frames
    raise RuntimeError("东方财富 HTTP 持仓接口失败: " + " | ".join(errors[-3:]))


def _fetch_recent_holdings(fund_code: str, top_n: int, years_back: int = 2) -> pd.DataFrame:
    current_year = datetime.now().year
    years = [current_year - offset for offset in range(0, max(2, years_back) + 1)]
    errors: list[str] = []

    for fetcher in (_fetch_holdings_by_akshare, _fetch_holdings_by_eastmoney_http):
        try:
            frames = fetcher(fund_code, years)
            raw = pd.concat(frames, ignore_index=True)
            return _normalize_holdings_df(raw, fund_code=fund_code, top_n=top_n, source=fetcher.__name__)
        except Exception as exc:
            errors.append(str(exc))

    raise RuntimeError("无法联网获取最近两年持仓数据；" + "；".join(errors))


def _fetch_specific_year_holdings(fund_code: str, top_n: int, year: int) -> pd.DataFrame:
    errors: list[str] = []
    for fetcher in (_fetch_holdings_by_akshare, _fetch_holdings_by_eastmoney_http):
        try:
            frames = fetcher(fund_code, [int(year)])
            raw = pd.concat(frames, ignore_index=True)
            return _normalize_holdings_df(raw, fund_code=fund_code, top_n=top_n, source=f"{fetcher.__name__}:targeted")
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("无法定向获取上一期年份持仓；" + "；".join(errors))


def _pick_periods(
    *,
    fund_code: str,
    top_n: int,
    cache_only: bool,
) -> tuple[HoldingPeriod, HoldingPeriod]:
    cached_latest = _load_cached_latest_period(fund_code, top_n)
    if cache_only:
        if cached_latest is None:
            raise RuntimeError(f"未找到本地持仓缓存: {fund_code}:top{top_n}")
        raise RuntimeError(
            "当前 fund_holdings_cache.json 只保存最新一期持仓，缺少上一期；"
            "请去掉 --cache-only 后联网获取上一期。"
        )

    online_df = _fetch_recent_holdings(fund_code, top_n=top_n)
    quarter_keys = sorted(int(x) for x in pd.to_numeric(online_df["_quarter_key"], errors="coerce").dropna().unique())
    if len(quarter_keys) < 2 and cached_latest is None:
        raise RuntimeError(f"未获取到足够季度用于对比: fund={fund_code}, quarters={quarter_keys}")

    latest = cached_latest
    online_latest_key = quarter_keys[-1] if quarter_keys else None
    if latest is None or (online_latest_key is not None and int(online_latest_key) > int(latest.quarter_key)):
        latest_key = int(online_latest_key)
        latest_df = online_df[online_df["_quarter_key"] == latest_key].copy()
        latest = HoldingPeriod(
            latest_key,
            str(latest_df["季度"].iloc[0]) if not latest_df.empty else _quarter_label_from_key(latest_key),
            latest_df,
            "online",
        )

    expected_previous_key = _previous_quarter_key(latest.quarter_key)
    if expected_previous_key not in quarter_keys:
        targeted_year = expected_previous_key // 10
        try:
            targeted_df = _fetch_specific_year_holdings(fund_code, top_n=top_n, year=targeted_year)
            online_df = pd.concat([online_df, targeted_df], ignore_index=True)
            quarter_keys = sorted(
                int(x)
                for x in pd.to_numeric(online_df["_quarter_key"], errors="coerce").dropna().unique()
            )
        except Exception as exc:
            raise RuntimeError(
                f"未获取到相邻上一期持仓：最新期={latest.quarter_label}，"
                f"期望上一期={_quarter_label_from_key(expected_previous_key)}，"
                f"本次可用季度={[_quarter_label_from_key(key) for key in quarter_keys]}；"
                f"定向重试失败：{exc}"
            ) from exc

        if expected_previous_key not in quarter_keys:
            raise RuntimeError(
                f"未获取到相邻上一期持仓：最新期={latest.quarter_label}，"
                f"期望上一期={_quarter_label_from_key(expected_previous_key)}，"
                f"本次可用季度={[_quarter_label_from_key(key) for key in quarter_keys]}；"
                "请稍后重试东方财富接口。"
            )
    previous_key = int(expected_previous_key)
    previous_df = online_df[online_df["_quarter_key"] == previous_key].copy()
    previous = HoldingPeriod(
        previous_key,
        str(previous_df["季度"].iloc[0]) if not previous_df.empty else _quarter_label_from_key(previous_key),
        previous_df,
        "online",
    )
    return latest, previous


def _compare_holdings(latest: HoldingPeriod, previous: HoldingPeriod) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    latest_map = {_security_key(row): row for _, row in latest.df.iterrows()}
    previous_map = {_security_key(row): row for _, row in previous.df.iterrows()}
    all_keys = sorted(
        set(latest_map) | set(previous_map),
        key=lambda key: (
            int(latest_map[key]["序号"]) if key in latest_map else 999,
            int(previous_map[key]["序号"]) if key in previous_map else 999,
            key,
        ),
    )

    rows: list[dict[str, Any]] = []
    latest_status_by_key: dict[tuple[str, str], str] = {}
    previous_status_by_key: dict[tuple[str, str], str] = {}

    for key in all_keys:
        latest_row = latest_map.get(key)
        previous_row = previous_map.get(key)
        latest_weight = _safe_float(latest_row.get("占净值比例")) if latest_row is not None else None
        previous_weight = _safe_float(previous_row.get("占净值比例")) if previous_row is not None else None

        if latest_row is not None and previous_row is None:
            status = "新进"
            delta = latest_weight
        elif latest_row is None and previous_row is not None:
            status = "退出"
            delta = -previous_weight if previous_weight is not None else None
        else:
            delta = (latest_weight or 0.0) - (previous_weight or 0.0)
            if abs(delta) < 0.000001:
                status = "持平"
            elif delta > 0:
                status = "上升"
            else:
                status = "下降"

        name = ""
        if latest_row is not None:
            name = str(latest_row.get("股票名称", "") or "")
        elif previous_row is not None:
            name = str(previous_row.get("股票名称", "") or "")

        rows.append(
            {
                "状态": status,
                "股票名称": name,
                "市场": key[0] or "--",
                "代码": key[1] or "--",
                "最新排名": int(latest_row["序号"]) if latest_row is not None else "--",
                "上一排名": int(previous_row["序号"]) if previous_row is not None else "--",
                "最新占净值比例": latest_weight,
                "上一占净值比例": previous_weight,
                "变化百分点": delta,
            }
        )

        if latest_row is not None:
            latest_status_by_key[key] = status
        if previous_row is not None:
            previous_status_by_key[key] = "保留" if latest_row is not None else "退出"

    change_df = pd.DataFrame(rows)

    latest_table_rows = []
    for _, row in latest.df.sort_values("序号").iterrows():
        key = _security_key(row)
        latest_table_rows.append(
            {
                "排名": int(row["序号"]),
                "股票名称": str(row.get("股票名称", "")),
                "市场": str(row.get("市场", "")),
                "代码": _display_ticker(row),
                "占净值比例": _safe_float(row.get("占净值比例")),
                "持股数": _safe_float(row.get("持股数")),
                "持仓市值": _safe_float(row.get("持仓市值")),
                "趋势": latest_status_by_key.get(key, "持平"),
            }
        )

    previous_table_rows = []
    for _, row in previous.df.sort_values("序号").iterrows():
        key = _security_key(row)
        previous_table_rows.append(
            {
                "排名": int(row["序号"]),
                "股票名称": str(row.get("股票名称", "")),
                "市场": str(row.get("市场", "")),
                "代码": _display_ticker(row),
                "占净值比例": _safe_float(row.get("占净值比例")),
                "持股数": _safe_float(row.get("持股数")),
                "持仓市值": _safe_float(row.get("持仓市值")),
                "去向": previous_status_by_key.get(key, "退出"),
            }
        )

    latest_total = sum(_safe_float(row.get("占净值比例")) or 0.0 for _, row in latest.df.iterrows())
    previous_total = sum(_safe_float(row.get("占净值比例")) or 0.0 for _, row in previous.df.iterrows())
    active_changes = change_df[change_df["状态"].isin(["新进", "上升", "下降"])]
    max_up = None
    max_down = None
    if not active_changes.empty:
        max_up_row = active_changes.sort_values("变化百分点", ascending=False).iloc[0]
        max_up = f"{max_up_row['股票名称']} {_fmt_pct(max_up_row['变化百分点'], signed=True)}"
    down_changes = change_df[change_df["状态"].isin(["退出", "下降"])]
    if not down_changes.empty:
        max_down_row = down_changes.sort_values("变化百分点", ascending=True).iloc[0]
        max_down = f"{max_down_row['股票名称']} {_fmt_pct(max_down_row['变化百分点'], signed=True)}"

    summary = {
        "latest_total": latest_total,
        "previous_total": previous_total,
        "total_delta": latest_total - previous_total,
        "new_count": int((change_df["状态"] == "新进").sum()),
        "exit_count": int((change_df["状态"] == "退出").sum()),
        "kept_count": int(change_df["状态"].isin(["上升", "下降", "持平"]).sum()),
        "max_up": max_up or "--",
        "max_down": max_down or "--",
    }
    return change_df, pd.DataFrame(latest_table_rows), pd.DataFrame(previous_table_rows), summary


def build_holding_change(
    *,
    fund_code: str = DEFAULT_FUND_CODE,
    top_n: int = 10,
    cache_only: bool = False,
) -> HoldingChangeResult:
    fund_code = _normalize_fund_code(fund_code)
    fund_name = _fund_name_from_cache_or_fallback(fund_code)
    latest, previous = _pick_periods(fund_code=fund_code, top_n=top_n, cache_only=cache_only)
    change_df, latest_table_df, previous_table_df, summary = _compare_holdings(latest, previous)
    return HoldingChangeResult(
        fund_code=fund_code,
        fund_name=fund_name,
        latest=latest,
        previous=previous,
        change_df=change_df,
        latest_table_df=latest_table_df,
        previous_table_df=previous_table_df,
        summary=summary,
    )


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), str(text), font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _fit_text(text: Any, font: ImageFont.ImageFont, max_width: int, min_size: int = 16) -> tuple[str, ImageFont.ImageFont]:
    text = str(text if text is not None else "")
    size = getattr(font, "size", 22)
    probe = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(probe)
    cur_font = font
    while size > min_size and _text_size(draw, text, cur_font)[0] > max_width:
        size -= 1
        cur_font = get_watermark_font(size)
    if _text_size(draw, text, cur_font)[0] <= max_width:
        return text, cur_font

    ellipsis = "..."
    while text and _text_size(draw, text + ellipsis, cur_font)[0] > max_width:
        text = text[:-1]
    return (text + ellipsis if text else ellipsis), cur_font


def _draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: Any,
    *,
    font: ImageFont.ImageFont,
    fill: str,
    max_width: int | None = None,
    anchor: str | None = None,
) -> None:
    final_text = str(text if text is not None else "")
    final_font = font
    if max_width is not None:
        final_text, final_font = _fit_text(final_text, font, max_width)
    draw.text(xy, final_text, font=final_font, fill=fill, anchor=anchor)


def _status_color(value: Any) -> str:
    text = str(value or "")
    if text == "新进":
        return "#1a73e8"
    if text == "上升":
        return "#d7263d"
    if text in {"下降", "退出"}:
        return "#188038"
    if text == "持平":
        return "#5f6368"
    if text == "保留":
        return "#1a73e8"
    return "#202124"


def _display_stock_name(value: Any) -> str:
    """图片中展示完整股票名称；过长时由绘图层自动缩小字号适配。"""
    text = str(value or "").strip()
    return text or "--"


def _display_title_fund_name(value: Any, limit: int = 13) -> str:
    """标题里的基金名称做短显示，避免手机竖图标题过长。"""
    text = str(value or "").strip()
    if not text:
        return "--"
    base = re.split(r"[\(（]", text, maxsplit=1)[0].strip()
    if len(base) > limit:
        return base[:limit] + "***"
    if base and base != text:
        return base + "***"
    return base


def _format_cell_value(column: str, value: Any) -> str:
    if column in {"占净值比例", "最新占净值比例", "上一占净值比例"}:
        return _fmt_pct(value)
    if column == "变化百分点":
        return _fmt_pct(value, signed=True)
    if column in {"持股数", "持仓市值"}:
        return _fmt_number(value)
    return str(value if value is not None else "--")


def _draw_table(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    width: int,
    title: str,
    rows: list[dict[str, Any]],
    columns: list[tuple[str, str, float]],
    row_height: int,
    title_font: ImageFont.ImageFont,
    header_font: ImageFont.ImageFont,
    body_font: ImageFont.ImageFont,
    header_bg: str = "#334155",
    title_color: str = "#111827",
    row_status_key: str | None = None,
) -> int:
    _draw_text(draw, (x, y), title, font=title_font, fill=title_color)
    y += 38
    header_h = row_height
    col_widths = [int(width * ratio) for _, _, ratio in columns]
    col_widths[-1] += width - sum(col_widths)

    cur_x = x
    for (key, label, _ratio), col_w in zip(columns, col_widths):
        draw.rectangle([cur_x, y, cur_x + col_w, y + header_h], fill=header_bg, outline="#cbd5e1", width=1)
        _draw_text(
            draw,
            (cur_x + col_w // 2, y + header_h // 2),
            label,
            font=header_font,
            fill="#ffffff",
            max_width=col_w - 10,
            anchor="mm",
        )
        cur_x += col_w

    y += header_h
    for index, row in enumerate(rows):
        bg = "#ffffff" if index % 2 == 0 else "#f8fafc"
        row_text_color = _status_color(row.get(row_status_key)) if row_status_key else "#111827"
        cur_x = x
        for key, _label, _ratio in columns:
            col_w = col_widths[columns.index((key, _label, _ratio))]
            draw.rectangle([cur_x, y, cur_x + col_w, y + row_height], fill=bg, outline="#dbe3ee", width=1)
            raw_value = row.get(key)
            value = _format_cell_value(key, raw_value)
            fill = row_text_color
            if row_status_key is None and key in {"状态", "趋势", "去向"}:
                fill = _status_color(raw_value)
            if key == "变化百分点":
                fill = _status_color("上升" if (_safe_float(raw_value) or 0) > 0 else "下降" if (_safe_float(raw_value) or 0) < 0 else "持平")
            anchor = "mm"
            tx = cur_x + col_w // 2
            _draw_text(
                draw,
                (tx, y + row_height // 2),
                value,
                font=body_font,
                fill=fill,
                max_width=col_w - 12,
                anchor=anchor,
            )
            cur_x += col_w
        y += row_height
    return y


def _add_logo_watermark(image: Image.Image, *, table_top: int, table_bottom: int) -> None:
    """在表格区域中央叠加低透明 logo，不遮挡文字阅读。"""
    logo_path = Path(MARK_IMAGE)
    if not logo_path.exists():
        return

    try:
        logo = Image.open(logo_path).convert("RGBA")
    except Exception:
        return

    target_width = int(image.width * 0.36)
    ratio = target_width / max(1, logo.width)
    target_height = max(1, int(logo.height * ratio))
    logo = logo.resize((target_width, target_height), Image.Resampling.LANCZOS)

    alpha = logo.getchannel("A")
    alpha = alpha.point(lambda value: int(value * 0.10))
    logo.putalpha(alpha)

    center_y = table_top + max(0, table_bottom - table_top) // 2
    x = (image.width - target_width) // 2
    y = max(table_top, center_y - target_height // 2)
    image.alpha_composite(logo, (x, y))


def _draw_summary_cards(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    width: int,
    cards: list[tuple[str, str, str]],
    label_font: ImageFont.ImageFont,
    value_font: ImageFont.ImageFont,
    columns: int = 5,
    card_height: int = 92,
) -> int:
    gap = 14
    row_gap = 12
    cur_y = y
    for start in range(0, len(cards), columns):
        row_cards = cards[start : start + columns]
        card_w = (width - gap * (len(row_cards) - 1)) // len(row_cards)
        for idx, (label, value, color) in enumerate(row_cards):
            left = x + idx * (card_w + gap)
            draw.rounded_rectangle(
                [left, cur_y, left + card_w, cur_y + card_height],
                radius=14,
                fill="#f8fafc",
                outline="#dbe3ee",
                width=1,
            )
            label_text, fitted_label_font = _fit_text(label, label_font, card_w - 36)
            value_text, fitted_value_font = _fit_text(value, value_font, card_w - 36)
            label_h = _text_size(draw, label_text, fitted_label_font)[1]
            value_h = _text_size(draw, value_text, fitted_value_font)[1]
            line_gap = 8
            block_h = label_h + line_gap + value_h
            text_y = cur_y + max(0, (card_height - block_h) // 2)
            draw.text((left + 18, text_y), label_text, font=fitted_label_font, fill="#64748b")
            draw.text((left + 18, text_y + label_h + line_gap), value_text, font=fitted_value_font, fill=color)
        cur_y += card_height + row_gap
    return cur_y - row_gap


def save_holding_change_image(result: HoldingChangeResult, output_file: str | Path) -> Path:
    ensure_runtime_dirs()
    output_path = Path(output_file)
    if output_path.parent:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    width = 960
    margin = 36
    latest_rows = result.latest_table_df.to_dict(orient="records")
    previous_rows = result.previous_table_df.to_dict(orient="records")

    table_row_h = 56
    table_title_h = 38
    card_rows = 2
    card_h = 80
    card_gap = 12
    after_subtitle_gap = 42
    after_summary_gap = 30
    table_gap = 26
    footer_gap = 26
    footer_block_h = 122
    summary_h = card_rows * card_h + (card_rows - 1) * card_gap
    latest_table_h = table_title_h + table_row_h * (len(latest_rows) + 1)
    previous_table_h = table_title_h + table_row_h * (len(previous_rows) + 1)
    height = (
        margin
        + 50
        + after_subtitle_gap
        + summary_h
        + after_summary_gap
        + latest_table_h
        + table_gap
        + previous_table_h
        + footer_block_h
    )

    image = Image.new("RGBA", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    title_font = get_watermark_font(40)
    subtitle_font = get_watermark_font(21)
    section_font = get_watermark_font(30)
    label_font = get_watermark_font(20)
    card_font = get_watermark_font(26)
    header_font = get_watermark_font(27)
    body_font = get_watermark_font(27)
    note_font = get_watermark_font(31)

    y = margin
    title = f"{_display_title_fund_name(result.fund_name)} 前十大持仓变化解读"
    _draw_text(draw, (margin, y), title, font=title_font, fill="#0f172a", max_width=width - margin * 2)
    y += 50
    subtitle = (
        f"{result.previous.quarter_label} -> {result.latest.quarter_label}    "
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    _draw_text(draw, (margin, y), subtitle, font=subtitle_font, fill="#475569", max_width=width - margin * 2)
    y += after_subtitle_gap

    cards = [
        ("前十大合计变化", f"{_fmt_pct(result.summary['previous_total'])} -> {_fmt_pct(result.summary['latest_total'])}", "#0f172a"),
        ("变化百分点", _fmt_pct(result.summary["total_delta"], signed=True), _status_color("上升" if result.summary["total_delta"] > 0 else "下降" if result.summary["total_delta"] < 0 else "持平")),
        ("新进 / 退出", f"{result.summary['new_count']} / {result.summary['exit_count']}", "#0f172a"),
        ("最大增仓", result.summary["max_up"], "#d7263d"),
        ("最大减仓", result.summary["max_down"], "#188038"),
    ]
    y = _draw_summary_cards(
        draw,
        x=margin,
        y=y,
        width=width - margin * 2,
        cards=cards,
        label_font=label_font,
        value_font=card_font,
        columns=3,
        card_height=card_h,
    )
    y += after_summary_gap

    previous_simple_rows = [
        {
            "排名": row.get("排名"),
            "股票名称": _display_stock_name(row.get("股票名称")),
            "占净值比例": row.get("占净值比例"),
        }
        for row in previous_rows
    ]
    latest_simple_rows = [
        {
            "排名": row.get("排名"),
            "股票名称": _display_stock_name(row.get("股票名称")),
            "占净值比例": row.get("占净值比例"),
            "趋势": row.get("趋势"),
        }
        for row in latest_rows
    ]

    table_top = y
    table_area_x = margin
    table_width = width - margin * 2
    latest_columns = [
        ("排名", "序号", 0.13),
        ("股票名称", "股票名称", 0.43),
        ("占净值比例", "持仓占比", 0.25),
        ("趋势", "状态", 0.19),
    ]
    previous_columns = [
        ("排名", "序号", 0.14),
        ("股票名称", "股票名称", 0.55),
        ("占净值比例", "持仓占比", 0.31),
    ]
    latest_bottom = _draw_table(
        draw,
        x=table_area_x,
        y=y,
        width=table_width,
        title=f"最新一期 - {result.latest.quarter_label}",
        rows=latest_simple_rows,
        columns=latest_columns,
        row_height=table_row_h,
        title_font=section_font,
        header_font=header_font,
        body_font=body_font,
        header_bg="#36506b",
        row_status_key="趋势",
    )
    y = latest_bottom + table_gap
    previous_bottom = _draw_table(
        draw,
        x=table_area_x,
        y=y,
        width=table_width,
        title=f"上一期 - {result.previous.quarter_label}",
        rows=previous_simple_rows,
        columns=previous_columns,
        row_height=table_row_h,
        title_font=section_font,
        header_font=header_font,
        body_font=body_font,
        header_bg="#475569",
    )
    y = previous_bottom + footer_gap

    latest_body_top = table_top + table_title_h + table_row_h
    _add_logo_watermark(image, table_top=latest_body_top, table_bottom=latest_bottom)
    draw = ImageDraw.Draw(image)

    note = "个人模型观察，不构成任何投资建议"
    _draw_text(draw, (width // 2, y), note, font=note_font, fill="#1f2937", max_width=width - margin * 2, anchor="ma")
    _draw_text(draw, (width // 2, y + 40), "鱼师AHNS", font=get_watermark_font(20), fill="#94a3b8", anchor="ma")

    image.convert("RGB").save(output_path)
    return output_path


def _save_csv(result: HoldingChangeResult, csv_path: Path) -> Path:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    out = result.change_df.copy()
    for col in ["最新占净值比例", "上一占净值比例", "变化百分点"]:
        out[col] = out[col].map(lambda value: _safe_float(value))
    out.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return csv_path


def _default_output_for_fund(fund_code: str) -> Path:
    return _manual_output_for_fund(fund_code)


def _generate_and_record_change(
    *,
    fund_code: str,
    top_n: int,
    state: dict[str, Any],
    state_key: str,
    force: bool,
    batch_state: dict[str, Any] | None = None,
    fund_pool_count: int = 0,
) -> Path:
    result = build_holding_change(fund_code=fund_code, top_n=top_n, cache_only=False)
    generated_at = datetime.now().isoformat(timespec="seconds")
    if force:
        image_path = save_holding_change_image(result, _manual_output_for_fund(fund_code))
        print(f"[HOLDING_CHANGE] 手动生成: {fund_code} -> {relative_path_str(image_path)}", flush=True)
        return image_path

    if batch_state is None:
        raise RuntimeError("自动批次状态缺失，无法生成披露批次编号图")

    image_path, batch_index = _batch_output_for_fund(
        batch_state=batch_state,
        result=result,
        top_n=top_n,
        fund_pool_count=fund_pool_count,
        generated_at=generated_at,
    )
    image_path = save_holding_change_image(result, image_path)
    _record_batch_image(
        batch_state=batch_state,
        result=result,
        image_path=image_path,
        index=batch_index,
        fund_pool_count=fund_pool_count,
        generated_at=generated_at,
    )
    state[state_key] = _state_entry_from_period(
        fund_code=fund_code,
        top_n=top_n,
        period=result.latest,
        image_path=image_path,
        generated_at=generated_at,
    )
    action = "手动生成" if force else "检测到变动并生成"
    print(f"[HOLDING_CHANGE] {action}: {fund_code} -> {relative_path_str(image_path)}", flush=True)
    return image_path


def run_auto_holding_change(*, top_n: int, force_fund_code: str = "") -> int:
    ensure_runtime_dirs()
    state_key_suffix = f":top{top_n}"
    holdings_cache = _load_json(FUND_HOLDINGS_CACHE, {})
    if not isinstance(holdings_cache, dict):
        raise RuntimeError(f"持仓缓存格式异常: {relative_path_str(FUND_HOLDINGS_CACHE)}")

    state = _load_change_state()
    batch_state = {} if force_fund_code else _load_batch_state()
    pool_fund_codes = [_normalize_fund_code(code) for code in HAIWAI_FUND_CODES]
    fund_pool_count = len(pool_fund_codes)
    fund_codes = [force_fund_code] if force_fund_code else pool_fund_codes
    initialized = 0
    unchanged = 0
    generated = 0
    missing_cache = 0
    failures: list[str] = []

    if force_fund_code:
        print_stage(f"持仓变化图手动模式: {force_fund_code}")
    else:
        print_stage(f"持仓变化图自动检测 开始，共 {len(fund_codes)} 只基金")

    for fund_code in fund_codes:
        state_key = f"{fund_code}{state_key_suffix}"
        period: HoldingPeriod | None = None
        item = holdings_cache.get(state_key)
        if isinstance(item, dict):
            try:
                period = _cached_period_from_item(fund_code, top_n, item)
            except Exception as exc:
                failures.append(f"{fund_code}: 持仓缓存解析失败: {exc}")
                continue

        if force_fund_code:
            try:
                _generate_and_record_change(
                    fund_code=fund_code,
                    top_n=top_n,
                    state=state,
                    state_key=state_key,
                    force=True,
                )
                generated += 1
            except Exception as exc:
                failures.append(f"{fund_code}: 手动生成失败: {exc}")
            continue

        if period is None:
            missing_cache += 1
            continue

        new_entry = _state_entry_from_period(fund_code=fund_code, top_n=top_n, period=period)
        previous = state.get(state_key)
        if not isinstance(previous, dict):
            state[state_key] = new_entry
            initialized += 1
            print(
                f"[HOLDING_CHANGE] 初始化持仓状态: {fund_code} {period.quarter_label}",
                flush=True,
            )
            continue

        same_quarter = int(previous.get("latest_quarter_key") or -1) == int(period.quarter_key)
        same_fingerprint = str(previous.get("fingerprint") or "") == str(new_entry.get("fingerprint") or "")
        if same_quarter and same_fingerprint:
            previous["last_checked_at"] = new_entry["last_checked_at"]
            state[state_key] = previous
            unchanged += 1
            continue

        try:
            _generate_and_record_change(
                fund_code=fund_code,
                top_n=top_n,
                state=state,
                state_key=state_key,
                force=False,
                batch_state=batch_state,
                fund_pool_count=fund_pool_count,
            )
            generated += 1
        except Exception as exc:
            failures.append(f"{fund_code}: 变动图生成失败: {exc}")

    _save_change_state(state)
    current_count = 0
    deleted_previous_images = 0
    if not force_fund_code:
        current = batch_state.get("current")
        if isinstance(current, dict) and isinstance(current.get("funds"), dict):
            current_count = len(current["funds"])
        deleted_previous_images = _maybe_cleanup_previous_batch(batch_state, now=datetime.now())
        _save_batch_state(batch_state)

    print_key_values(
        "持仓变化图自动检测完成",
        [
            ("生成图片", generated),
            ("初始化状态", initialized),
            ("无变化", unchanged),
            ("缺持仓缓存", missing_cache),
            ("失败", len(failures)),
            ("状态缓存", relative_path_str(FUND_HOLDING_CHANGE_STATE_CACHE)),
        ],
    )
    print_key_values(
        "持仓变化图批次",
        [
            ("本轮持仓变化图", f"{current_count}/{fund_pool_count}" if not force_fund_code else "手动模式"),
            ("批次缓存", relative_path_str(FUND_HOLDING_CHANGE_BATCH_STATE_CACHE)),
            ("清理上一轮图片", deleted_previous_images),
        ],
    )
    for item in failures:
        print(f"[HOLDING_CHANGE][WARN] {item}", flush=True)
    return 1 if force_fund_code and failures else 0


def _display_change_rows(change_df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in change_df.to_dict(orient="records"):
        item = dict(row)
        item["最新占净值比例"] = _fmt_pct(item.get("最新占净值比例"))
        item["上一占净值比例"] = _fmt_pct(item.get("上一占净值比例"))
        item["变化百分点"] = _fmt_pct(item.get("变化百分点"), signed=True)
        rows.append(item)
    return rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成基金前十大持仓变化解读图")
    parser.add_argument("fund_code", nargs="?", default=None, help="基金代码，默认 012922；--auto 下填写则强制生成该基金")
    parser.add_argument("--top-n", type=int, default=10, help="对比前 N 大持仓，默认 10")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出图片路径")
    parser.add_argument("--cache-only", action="store_true", help="只读本地缓存；缺上一期时直接提示失败")
    parser.add_argument("--auto", action="store_true", help="自动检测基金库持仓缓存变动；只在变动或指定基金时出图")
    parser.add_argument(
        "--save-csv",
        nargs="?",
        const="auto",
        default=None,
        help="可选保存变化明细 CSV；不传路径时写入 output/fund_holding_change/fund_holding_change_<code>.csv",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    top_n = max(1, int(args.top_n or 10))

    if args.auto:
        env_fund_code = os.environ.get("AHNS_HOLDING_CHANGE_FUND_CODE", "")
        force_fund_code = _normalize_optional_fund_code(env_fund_code or args.fund_code or "")
        return run_auto_holding_change(top_n=top_n, force_fund_code=force_fund_code)

    fund_code = _normalize_fund_code(args.fund_code or DEFAULT_FUND_CODE)
    if str(args.output) == str(DEFAULT_OUTPUT) and fund_code != DEFAULT_FUND_CODE:
        output = _default_output_for_fund(fund_code)
    else:
        output = Path(args.output)

    try:
        print_stage(f"开始生成 {fund_code} 前{top_n}大持仓变化解读图")
        result = build_holding_change(fund_code=fund_code, top_n=top_n, cache_only=bool(args.cache_only))
        print_key_values(
            "持仓对比摘要",
            [
                ("基金", f"{result.fund_code} {result.fund_name}"),
                ("最新一期", result.latest.quarter_label),
                ("上一期", result.previous.quarter_label),
                ("前十大合计", f"{_fmt_pct(result.summary['previous_total'])} -> {_fmt_pct(result.summary['latest_total'])}"),
                ("新进/退出", f"{result.summary['new_count']} / {result.summary['exit_count']}"),
            ],
        )
        print_records_table(
            _display_change_rows(result.change_df),
            title="持仓变化明细",
            columns=[
                ("状态", "状态"),
                ("股票名称", "股票名称"),
                ("市场", "市场"),
                ("代码", "代码"),
                ("最新排名", "最新排名"),
                ("上一排名", "上一排名"),
                ("最新占净值比例", "最新占比"),
                ("上一占净值比例", "上一占比"),
                ("变化百分点", "变化"),
            ],
        )
        image_path = save_holding_change_image(result, output)
        print(f"持仓变化解读图已保存: {relative_path_str(image_path)}", flush=True)

        if args.save_csv:
            csv_path = (
                HOLDING_CHANGE_OUTPUT_DIR / f"fund_holding_change_{fund_code}.csv"
                if args.save_csv == "auto"
                else Path(args.save_csv)
            )
            saved_csv = _save_csv(result, csv_path)
            print(f"持仓变化明细 CSV 已保存: {relative_path_str(saved_csv)}", flush=True)
        return 0
    except Exception as exc:
        print(f"[ERROR] 持仓变化图生成失败：{exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
