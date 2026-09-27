"""限购变化检测：成功抓取时间控制刷新，独立状态控制出图与失败重试。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from tools.configs.cache_policy_configs import FUND_PURCHASE_LIMIT_CACHE_DAYS
from tools.configs.fund_universe_configs import HAIWAI_FUND_CODES
from tools.fund_cache_maintenance import prune_inactive_fund_records, write_json_if_changed
from tools.get_top10_holdings import (
    fetch_fund_purchase_limit_bulk_map,
    get_fund_purchase_limit,
    resolve_purchase_limit_display_value,
)
from tools.paths import FUND_LIMIT_CHANGE_STATE_CACHE, FUND_PURCHASE_LIMIT_CACHE, OUTPUT_DIR

BJ = ZoneInfo("Asia/Shanghai")


def read_mapping(path: Path) -> dict:
    """状态文件损坏时显式失败，不能悄悄重建基线而漏报变化。"""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"缓存不是对象: {path}")
    return data


def parse_time(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=BJ) if dt.tzinfo is None else dt.astimezone(BJ)
    except (ValueError, TypeError):
        return None


def normalize_limit(record) -> str | None:
    """规范化状态与金额；格式变化不会被视作一次新的限购变化。"""
    value = re.sub(r"\s+", "", resolve_purchase_limit_display_value(record)).replace(",", "").replace("，", "")
    if value in {"", "未知", "--", "—", "None", "nan"}:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(万)?元", value)
    if match:
        amount = Decimal(match[1]) * (10000 if match[2] else 1)
        if amount <= 0:
            return None
        return f"{format(amount.normalize(), 'f')}元"
    return value


def snapshot(record) -> dict | None:
    if not isinstance(record, dict):
        return None
    value = normalize_limit(record)
    observed = parse_time(record.get("fetched_at"))
    if value is None or observed is None:
        return None
    return {"value": value, "observed_at": observed.isoformat(timespec="seconds")}


def refresh_due(record, now: datetime) -> bool:
    valid = snapshot(record)
    if not valid:
        return True
    age = now - parse_time(valid["observed_at"])
    return age < timedelta(0) or age >= timedelta(days=FUND_PURCHASE_LIMIT_CACHE_DAYS)


def render_change(code: str, event: dict, output_file: Path) -> Path:
    # 延迟导入绘图模块；无变化的日常检查不加载 Pillow 和持仓绘图依赖。
    from tools.fund_limit_change_image import render_cached_change
    return render_cached_change(code, event, output_file)


def queue_observation(entry: dict, current: dict | None, detected_at: str) -> None:
    """联网覆盖缓存前保留已有观察；绘图失败时后续观察也不会丢失。"""
    if current is None:
        return
    waiting = entry.get("observations", [])
    latest = waiting[-1] if waiting else entry.get("baseline")
    if latest:
        if parse_time(current["observed_at"]) < parse_time(latest["observed_at"]):
            return
        if all(current.get(key) == latest.get(key) for key in ("value", "observed_at")):
            return
    entry.setdefault("observations", []).append({**current, "detected_at": detected_at})


def run_auto(*, fund_codes=None, cache_path: Path = FUND_PURCHASE_LIMIT_CACHE,
             state_path: Path = FUND_LIMIT_CHANGE_STATE_CACHE,
             output_dir: Path = OUTPUT_DIR / "fund_limit_change" / "latest",
             now: datetime | None = None) -> int:
    now = (now or datetime.now(BJ)).astimezone(BJ)
    stamp = now.isoformat(timespec="seconds")
    codes = list(dict.fromkeys(str(c).zfill(6) for c in (HAIWAI_FUND_CODES if fund_codes is None else fund_codes)))
    cache = read_mapping(cache_path)
    state = read_mapping(state_path)
    state, _ = prune_inactive_fund_records(state, active_fund_codes=codes, now=now)

    # 首次必须在刷新之前保存旧缓存基线，否则本轮到期刷新会吞掉真实变化。
    for code in codes:
        if code not in state:
            old = snapshot(cache.get(code))
            if old:
                state[code] = {"baseline": old, "updated_at": stamp}
        if snapshot(cache.get(code)):
            queue_observation(state[code], snapshot(cache.get(code)), stamp)
    write_json_if_changed(state_path, state)
    due = {code for code in codes if refresh_due(cache.get(code), now)}
    bulk = None
    if due:
        try:
            bulk = fetch_fund_purchase_limit_bulk_map(force_refresh=True)
        except Exception as exc:
            print(f"[LIMIT_CHANGE] 批量接口失败，回退单基金页面: {exc}", flush=True)

    failures = generated = 0
    for code in codes:
        try:
            if code in due:
                try:
                    detail = get_fund_purchase_limit(code, force_refresh=True, bulk_limit_map=bulk, return_detail=True)
                    if normalize_limit(detail) is None or str(detail.get("source", "")).startswith("old_cache_after"):
                        raise RuntimeError(detail.get("error") or "未取得有效新限购信息，保留旧缓存")
                except Exception as exc:
                    failures += 1
                    print(f"[LIMIT_CHANGE] {code} 刷新失败: {exc}", flush=True)
            # 以落盘成功的缓存为准，也能发现 GUI 或其他入口提前刷新产生的变化。
            current = snapshot(read_mapping(cache_path).get(code))
            entry = state.setdefault(code, {})
            queue_observation(entry, current, stamp)
            write_json_if_changed(state_path, state)

            def generate_pending():
                nonlocal generated
                event = entry.get("event")
                if event and not event.get("generated_at"):
                    path = render_change(code, event, output_dir / f"{code}.png")
                    if not path.is_file():
                        raise RuntimeError("绘图未生成文件")
                    event["generated_at"] = stamp
                    entry["updated_at"] = stamp
                    write_json_if_changed(state_path, state)
                    generated += 1
                    print(f"[LIMIT_CHANGE] {code} {event['old_value']} -> {event['new_value']}: {path}", flush=True)

            # 老事件优先重试；即使最新缓存已经再次变化，也不会直接覆盖失败事件。
            generate_pending()
            while entry.get("observations"):
                current = entry["observations"].pop(0)
                baseline = entry.get("baseline")
                if baseline and normalize_limit(baseline["value"]) != current["value"]:
                    identity = json.dumps([code, baseline["value"], current["value"], current["observed_at"]], ensure_ascii=False)
                    entry["event"] = {
                        "id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                        "old_value": baseline["value"], "new_value": current["value"],
                        "detected_at": current.get("detected_at", stamp), "observed_at": current["observed_at"],
                    }
                entry["baseline"] = {key: current[key] for key in ("value", "observed_at")}
                entry["updated_at"] = stamp
                write_json_if_changed(state_path, state)
                generate_pending()
        except Exception as exc:
            failures += 1
            print(f"[LIMIT_CHANGE] {code} 处理失败，保留状态下次重试: {exc}", flush=True)
    print(f"[LIMIT_CHANGE] 检查 {len(codes)} 只，到期刷新 {len(due)} 只，生成 {generated} 张，失败 {failures} 项", flush=True)
    return 1 if failures else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="每 3 天更新基金限购信息，有变化时生成观察图")
    parser.add_argument("--auto", action="store_true", help="自动检查海外/全球基金池（默认行为）")
    parser.parse_args(argv)
    try:
        return run_auto()
    except Exception as exc:
        print(f"[LIMIT_CHANGE] 无法完成限购检查: {exc}", flush=True)
        return 1
