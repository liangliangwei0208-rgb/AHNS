"""指数基金真实收益与限购事件协调；与海外基金估算、持仓及旧事件状态隔离。"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from tools.configs.cache_policy_configs import FUND_PURCHASE_LIMIT_CACHE_DAYS
from tools.configs.index_fund_tracking_configs import (
    DATE_TOLERANCE_DAYS, INDEX_FUND_RETURN_PERIODS, INDEX_FUND_TRACKING_MAP, INDEX_HISTORY_FETCH_ROWS,
)
from tools.fund_limit_change import BJ, normalize_limit, parse_time, read_mapping, refresh_due
from tools.fund_cache_maintenance import replace_atomic_with_retry
from tools.paths import CACHE_DIR, OUTPUT_DIR, MARK_IMAGE
from tools.get_top10_holdings import get_fund_purchase_limit, fetch_fund_purchase_limit_bulk_map


def atomic_json(path: Path, value: dict) -> None:
    """唯一临时文件原子替换；内容未变不更新时间戳。"""
    path = Path(path)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        replace_atomic_with_retry(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def source_due(meta: dict, now: datetime, *, valid: bool = True) -> bool:
    success = parse_time(meta.get("last_success_at"))
    return not valid or success is None or now < success or now-success >= timedelta(days=FUND_PURCHASE_LIMIT_CACHE_DAYS)


def parse_cumulative_curve(payload: dict, now: datetime) -> list[dict]:
    """只取官方响应第一条基金曲线；整个响应共享一个累计收益基准。"""
    try:
        data = payload["Data"][0]["data"]
        out = {}
        for timestamp, cumulative, *_ in data:
            day = str(pd.to_datetime(timestamp, unit="ms", utc=True).tz_convert(BJ).date())
            wealth = 1 + float(cumulative)/100
            if not math.isfinite(wealth) or wealth <= 0:
                raise ValueError("累计收益因子无效")
            if day in out:
                raise ValueError("累计曲线估值日期重复")
            if day <= now.date().isoformat():
                out[day] = {"date": day, "wealth": wealth}
        if not out:
            raise ValueError("基金累计收益曲线为空")
        return [out[key] for key in sorted(out)]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("官方累计收益响应结构无效") from exc


def clean_prices(prices: pd.DataFrame, cutoff: str) -> pd.DataFrame:
    """仅接受已结束交易日的有限正收盘价，不向历史填充价格。"""
    if prices.empty or not {"date", "close"} <= set(prices.columns):
        return pd.DataFrame(columns=["date", "close"])
    frame = prices.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.loc[(frame.date <= cutoff) & frame.close.map(lambda n: pd.notna(n) and math.isfinite(n) and n > 0)]
    return frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def calculate_comparison(series: list[dict], prices: pd.DataFrame, *, now: datetime,
                         latest_nav_date: str | None = None) -> dict:
    """共同日期交集，四期同截止；自然月向前选真实共有日期，最大回退20日。"""
    from tools.breadth_engine import market_clock
    cutoff = min(now.date().isoformat(), market_clock("US", now)["complete_day"])
    confirmed_prices = clean_prices(prices, cutoff)
    if latest_nav_date:
        cutoff = min(cutoff, latest_nav_date)
    funds = {}
    for row in series:
        day, wealth = row["date"], row["wealth"]
        if day <= cutoff and isinstance(wealth, (int, float)) and math.isfinite(wealth) and wealth > 0:
            if day in funds and funds[day] != wealth:
                raise ValueError(f"同日基金收益因子冲突: {day}")
            funds[day] = wealth
    frame = clean_prices(prices, cutoff)
    index = dict(zip(frame.date, frame.close))
    common = sorted(set(funds) & set(index))
    end = common[-1] if common else None
    result = {"fund_nav_date": latest_nav_date or (max(funds) if funds else None),
              "benchmark_close_date": confirmed_prices.date.max() if not confirmed_prices.empty else None,
              "comparison_end_date": end, "periods": {}}
    for months in INDEX_FUND_RETURN_PERIODS:
        period = {"fund_return": None, "index_return": None, "start_date": None, "end_date": end, "reason": ""}
        reason = ""
        if not end:
            reason = "基金或指数没有真实共有日期"
        elif (now.date()-pd.Timestamp(end).date()).days > DATE_TOLERANCE_DAYS:
            reason = "共同截止日期过旧，超过20自然日"
        else:
            target = str((pd.Timestamp(end)-pd.DateOffset(months=months)).date())
            period["target_start_date"] = target
            eligible = [day for day in common if day <= target]
            if not eligible:
                reason = "成立时间或真实历史不足，不能缩短区间"
            else:
                start = eligible[-1]
                period["start_date"] = start
                if (pd.Timestamp(target)-pd.Timestamp(start)).days > DATE_TOLERANCE_DAYS:
                    reason = "区间起点回退超过20自然日"
                else:
                    period.update(fund_start=funds[start], fund_end=funds[end],
                                  index_start=index[start], index_end=index[end],
                                  fund_return=(funds[end]/funds[start]-1)*100,
                                  index_return=(index[end]/index[start]-1)*100)
        period["reason"] = reason
        result["periods"][f"{months}m"] = period
    return result


def valid_limit(detail: dict) -> bool:
    """限大额但金额未知不是可比较状态；旧缓存降级不算新的成功抓取。"""
    if not isinstance(detail, dict) or str(detail.get("source", "")).startswith(("old_cache_after", "failed")):
        return False
    value = normalize_limit(detail)
    return value in {"暂停申购", "暂不销售", "不限额度"} or bool(value and re.fullmatch(r"\d+(?:\.\d+)?元", value))


def limit_observation(record: dict, now: datetime) -> dict | None:
    time = parse_time(record.get("fetched_at")) if isinstance(record, dict) else None
    if not valid_limit(record) or time is None or time > now:
        return None
    return {"value": normalize_limit(record), "observed_at": time.isoformat(timespec="seconds")}


def observe(entry: dict, observation: dict | None, code: str, stamp: str) -> None:
    """先建立基线；新有效观察创建不可丢失的事件，再推进比较基线。"""
    if not observation:
        return
    unresolved = [c for c in entry.get("baseline_conflicts", {}).values() if not c.get("resolved_at")]
    if unresolved:
        # 同秒不同限额无法确认先后；等下一次更晚的有效抓取重建可信基线，不伪造变化。
        if any(parse_time(observation["observed_at"]) <= parse_time(c["observed_at"]) for c in unresolved):
            return
        for conflict in unresolved:
            conflict["resolved_at"] = observation["observed_at"]
        entry["baseline"] = observation
        entry.setdefault("events", {})
        print(f"[INDEX_TRACKING] {code} 同秒限额冲突已由新有效观察确认基线")
        return
    baseline = entry.get("baseline")
    if baseline and parse_time(observation["observed_at"]) < parse_time(baseline["observed_at"]):
        return
    if baseline and baseline["value"] != observation["value"]:
        identity = json.dumps([code, baseline["value"], observation["value"], observation["observed_at"]], ensure_ascii=False)
        event_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        entry.setdefault("events", {}).setdefault(event_id, {
            "id": event_id, "old_value": baseline["value"], "new_value": observation["value"],
            "observed_at": observation["observed_at"], "detected_at": stamp,
        })
    entry["baseline"] = observation
    entry.setdefault("events", {})


def _get(url: str, *, params=None, json_data=False):
    """公共东方财富/新浪接口，有限超时；不继承不稳定的系统代理。"""
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(url, params=params, timeout=(5, 15),
                               headers={"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"})
        response.raise_for_status()
        response.encoding = response.apparent_encoding if not json_data else "utf-8"
        return response.json() if json_data else response.text


def matches_tracking_target(symbol: str, target: str) -> bool:
    target = target.replace(" ", "").replace("标准普尔", "标普")
    return ("纳斯达克" in target and "100" in target) if symbol == ".NDX" else "标普500" in target


def fetch_fund_source(code: str, now: datetime) -> dict:
    """复用AkShare的东方财富累计收益数据源，不抓持仓、不调用估算函数。"""
    from bs4 import BeautifulSoup
    profile_url = f"https://fundf10.eastmoney.com/jbgk_{code}.html"
    soup = BeautifulSoup(_get(profile_url), "html.parser")
    fields = {}
    for th in soup.select("th"):
        td = th.find_next_sibling("td")
        if td:
            fields[th.get_text(strip=True)] = td.get_text(" ", strip=True)
    name = fields.get("基金简称") or fields.get("基金全称")
    if not name or "基金" not in fields.get("基金全称", name):
        # 简称有时省略“基金”，只要求存在可核验的基本资料表。
        if not name or not fields.get("基金类型"):
            raise ValueError(f"{code} 基金基本资料解析失败")
    target = fields.get("跟踪标的", "")
    benchmark = fields.get("业绩比较基准", "")
    expected = INDEX_FUND_TRACKING_MAP[code]
    conflict = ""
    if code == "007721":
        conflict = "QDII-FOF；官方跟踪标的未列，业绩基准含标普500，按用户指定分组观察"
    elif not matches_tracking_target(expected["index_symbol"], target):
        conflict = f"资料跟踪标的与指定分组待核对: {target}"
    currency = "CNY" if "人民币" in name or "人民币" in benchmark else "unknown"
    if "美元" in name:
        currency = "USD"
    profile = {"fund_code": code, "fund_name": name, "fund_full_name": fields.get("基金全称"),
               "fund_type": fields.get("基金类型"), "currency": currency,
               "currency_evidence": name if "人民币" in name or "美元" in name else benchmark,
               "tracking_target": target, "performance_benchmark": benchmark,
               "inception": fields.get("成立日期/规模", ""), "group_audit": conflict,
               "profile_source": profile_url, **expected}
    nav_url = "https://api.fund.eastmoney.com/f10/lsjz"
    nav = _get(nav_url, params={"fundCode": code, "pageIndex": 1, "pageSize": 1}, json_data=True)
    latest_nav = str(nav["Data"]["LSJZList"][0]["FSRQ"])
    if pd.Timestamp(latest_nav).date() > now.date():
        raise ValueError("基金最新净值日期在未来")
    curve_url = "https://api.fund.eastmoney.com/pinzhong/LJSYLZS"
    errors = []
    selected = None
    for basis in ("y", "try"):
        try:
            payload = _get(curve_url, params={"fundCode": code, "indexcode": "000300", "type": basis}, json_data=True)
            series = [r for r in parse_cumulative_curve(payload, now) if r["date"] <= latest_nav]
            if not series:
                raise ValueError("没有已确认净值日期以内的累计收益")
            selected = {"profile": profile, "series": series, "latest_nav_date": latest_nav,
                        "source": "eastmoney_official_cumulative", "source_url": curve_url,
                        "source_params": {"fundCode": code, "indexcode": "000300", "type": basis},
                        "basis": basis, "definition": "官方累计收益曲线因子之比；含分红，源百分比精度0.01个百分点",
                        "latest_nav_source": nav_url, "data_quality": "official"}
            target_start = (pd.Timestamp(series[-1]["date"])-pd.DateOffset(months=12)).date()
            if pd.Timestamp(series[0]["date"]).date() <= target_start or basis == "try":
                break
        except Exception as exc:
            errors.append(f"{basis}: {exc}")
    if selected is None:
        raise ValueError("官方累计收益获取失败: " + " | ".join(errors))
    selected["source_warnings"] = errors
    return selected


def fetch_index_prices(symbol: str, now: datetime) -> pd.DataFrame:
    """现有AkShare新浪美股指数适配的同一解码器，补充有限网络超时。"""
    from akshare.stock.cons import zh_js_decode
    import py_mini_racer
    source = _get(f"https://finance.sina.com.cn/staticdata/us/{symbol}")
    decoder = py_mini_racer.MiniRacer()
    decoder.eval(zh_js_decode)
    data = decoder.call("d", source.split("=")[1].split(";")[0].replace('"', ""))
    frame = pd.DataFrame(data)
    from tools.breadth_engine import market_clock
    frame = clean_prices(frame, market_clock("US", now)["complete_day"])
    if frame.empty:
        raise ValueError(f"{symbol} 无正式指数收盘记录")
    return frame.tail(INDEX_HISTORY_FETCH_ROWS).reset_index(drop=True)


def index_csv_path(cache_dir: Path, symbol: str) -> Path:
    return cache_dir / f"dot_{symbol.lstrip('.')}_index_daily.csv"


def merge_index_prices(path: Path, incoming: pd.DataFrame, cutoff: str) -> pd.DataFrame:
    existing = pd.read_csv(path) if path.exists() else pd.DataFrame()
    merged = clean_prices(pd.concat([existing, incoming], ignore_index=True), cutoff)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        merged.to_csv(tmp, index=False, encoding="utf-8-sig")
        replace_atomic_with_retry(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return merged


def with_verified_index_endpoints(frame: pd.DataFrame, returns: dict, symbol: str, cutoff: str) -> pd.DataFrame:
    """共享CSV被其它入口缩短时，复用已验证的少量端点；不填未来价、不另存指数序列。"""
    endpoints = []
    for code, data in returns.get("funds", {}).items():
        if INDEX_FUND_TRACKING_MAP.get(code, {}).get("index_symbol") != symbol:
            continue
        for period in data.get("comparison", {}).get("periods", {}).values():
            for side in ("start", "end"):
                day, close = period.get(f"{side}_date"), period.get(f"index_{side}")
                if day and close is not None:
                    endpoints.append({"date": day, "close": close})
    # CSV来自本轮正式行情，重叠日期优先；证据只补真实缺失的日期。
    return clean_prices(pd.concat([pd.DataFrame(endpoints), frame], ignore_index=True), cutoff)


def _newer(a, b, key):
    """同时间用稳定内容排序，合并方向不影响结果。"""
    return copy.deepcopy(max([x for x in (a, b) if x] or [{}], key=lambda x: (
        parse_time(x.get(key)) or datetime.min.replace(tzinfo=BJ), json.dumps(x, sort_keys=True, ensure_ascii=False))))


def _merge_refresh(a, b):
    merged = _newer(a, b, "last_success_at")
    attempt = _newer(a, b, "last_attempt_at")
    for field in ("last_attempt_at", "error"):
        if field in attempt:
            merged[field] = attempt[field]
    return merged


def validate_state(state: dict) -> None:
    """JSON能解析仍可能结构损坏，写入前检查，不能悄悄重建基线。"""
    if not state:
        return
    if state.get("schema_version") != 1 or not isinstance(state.get("funds"), dict):
        raise ValueError("指数事件状态版本或funds结构无效")
    for code, entry in state["funds"].items():
        if not isinstance(entry, dict) or not isinstance(entry.get("events", {}), dict):
            raise ValueError(f"{code} 事件状态结构损坏")
        baseline = entry.get("baseline")
        if baseline and (not isinstance(baseline, dict) or not valid_limit(baseline) or not parse_time(baseline.get("observed_at"))):
            raise ValueError(f"{code} 基线结构损坏")
        for identity, event in entry.get("events", {}).items():
            if not isinstance(event, dict) or event.get("id") != identity or not {"old_value", "new_value", "observed_at", "detected_at"} <= set(event):
                raise ValueError(f"{code} 事件内容损坏: {identity}")


def merge_tracking_state(left: dict, right: dict) -> dict:
    out = {"schema_version": 1, "funds": {}}
    for code in sorted(set(left.get("funds", {})) | set(right.get("funds", {}))):
        a, b = left.get("funds", {}).get(code, {}), right.get("funds", {}).get(code, {})
        entry = {"baseline": _newer(a.get("baseline"), b.get("baseline"), "observed_at"), "events": {}}
        for event_id in sorted(set(a.get("events", {})) | set(b.get("events", {}))):
            x, y = a.get("events", {}).get(event_id, {}), b.get("events", {}).get(event_id, {})
            event = _newer(x, y, "last_attempt_at")
            completed = [t for t in (x.get("generated_at"), y.get("generated_at")) if parse_time(t)]
            if completed:
                event["generated_at"] = max(completed, key=parse_time)
                event.pop("error", None)
            entry["events"][event_id] = event
        # 两机各自首次初始化也可能观察到不同限额；同步不能直接丢掉中间变化。
        old = min((x for x in (a.get("baseline"), b.get("baseline")) if x),
                  key=lambda x: (parse_time(x.get("observed_at")) or datetime.min.replace(tzinfo=BJ),
                                 json.dumps(x, sort_keys=True, ensure_ascii=False)), default=None)
        new = entry["baseline"]
        conflicts = {}
        for identity in sorted(set(a.get("baseline_conflicts", {})) | set(b.get("baseline_conflicts", {}))):
            conflicts[identity] = _newer(a.get("baseline_conflicts", {}).get(identity), b.get("baseline_conflicts", {}).get(identity), "resolved_at")
        same_second = old and new and parse_time(old.get("observed_at")) == parse_time(new.get("observed_at")) and old.get("value") != new.get("value")
        if same_second:
            observations = sorted((old, new), key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
            identity = hashlib.sha256(json.dumps(observations, sort_keys=True).encode("utf-8")).hexdigest()
            conflicts.setdefault(identity, {"observed_at": new["observed_at"], "observations": observations})
        if conflicts:
            entry["baseline_conflicts"] = conflicts
        ambiguous = any(o == old or o == new for c in conflicts.values() for o in c.get("observations", []))
        covered = any(e.get("old_value") == (old or {}).get("value") and e.get("new_value") == new.get("value") and
                      parse_time(e.get("observed_at")) and
                      (parse_time((old or {}).get("observed_at")) or datetime.min.replace(tzinfo=BJ)) <=
                      parse_time(e["observed_at"]) <= (parse_time(new.get("observed_at")) or datetime.min.replace(tzinfo=BJ))
                      for e in entry["events"].values())
        if old and new and old.get("value") != new.get("value") and not covered and not same_second and not ambiguous:
            restored = {"baseline": copy.deepcopy(old), "events": entry["events"]}
            observe(restored, new, code, new["observed_at"])
        out["funds"][code] = entry
    return out


def merge_returns_cache(left: dict, right: dict) -> dict:
    """完整基金曲线快照不拼接，避免不同累计基准混合；失败不抹去成功数据。"""
    out = {"schema_version": 1, "funds": {}, "indices": {}}
    for section in ("funds", "indices"):
        for key in sorted(set(left.get(section, {})) | set(right.get(section, {}))):
            a, b = left.get(section, {}).get(key, {}), right.get(section, {}).get(key, {})
            if section == "indices":
                chosen = _merge_refresh(a, b)
            else:
                candidates = [x for x in (a, b) if x.get("nav", {}).get("series") and parse_time(x["nav"].get("last_success_at"))]
                chosen = copy.deepcopy(max(candidates or [a, b], key=lambda x: (
                    x.get("nav", {}).get("latest_nav_date", ""),
                    parse_time(x.get("nav", {}).get("last_success_at")) or datetime.min.replace(tzinfo=BJ),
                    json.dumps(x, sort_keys=True, ensure_ascii=False))))
                chosen["limit_refresh"] = _merge_refresh(a.get("limit_refresh"), b.get("limit_refresh"))
                nav_attempt = _newer(a.get("nav"), b.get("nav"), "last_attempt_at")
                for field in ("last_attempt_at", "error"):
                    if field in nav_attempt:
                        chosen.setdefault("nav", {})[field] = nav_attempt[field]
            out[section][key] = chosen
    return out


def run_tracking(*, fund_codes=None, cache_dir: Path = CACHE_DIR,
                 output_dir: Path = OUTPUT_DIR / "index_fund_limit_change" / "latest",
                 now: datetime | None = None, force_refresh=False) -> int:
    """同机锁只防并发，不做每日一次限制；状态先保存，最多每基金一张事件图。"""
    from tools.service_daily_step import DailyStepGuard
    with DailyStepGuard("index_fund_tracking") as guard:
        if not guard.acquired:
            print("[INDEX_TRACKING] 已有同机任务运行，本轮跳过")
            return 0
        clock = (lambda: now.astimezone(BJ)) if now is not None else (lambda: datetime.now(BJ))
        return _run_tracking(fund_codes=fund_codes, cache_dir=Path(cache_dir), output_dir=Path(output_dir),
                             now=clock(), clock=clock, force_refresh=force_refresh)


def _run_tracking(*, fund_codes, cache_dir, output_dir, now, clock, force_refresh):
    from tools.index_fund_tracking_image import render_index_fund_limit_change
    from tools.breadth_engine import market_clock
    codes = list(dict.fromkeys(INDEX_FUND_TRACKING_MAP if fund_codes is None else fund_codes))
    if not codes or any(code not in INDEX_FUND_TRACKING_MAP for code in codes):
        raise ValueError("基金代码必须是独立25只观察名单中的六位字符串")
    state_path = cache_dir / "index_fund_tracking_state.json"
    returns_path = cache_dir / "index_fund_tracking_returns.json"
    limit_path = cache_dir / "fund_purchase_limit_cache.json"
    state, returns, limits = read_mapping(state_path), read_mapping(returns_path), read_mapping(limit_path)
    validate_state(state)
    if returns and (returns.get("schema_version") != 1 or not isinstance(returns.get("funds"), dict) or not isinstance(returns.get("indices"), dict)):
        raise ValueError("指数收益缓存结构损坏")
    for doc in (state, returns):
        doc.setdefault("schema_version", 1)
        doc.setdefault("funds", {})
        if not isinstance(doc["funds"], dict):
            raise ValueError("指数跟踪缓存funds结构损坏")
    returns.setdefault("indices", {})
    stamp = now.isoformat(timespec="seconds")
    for code in codes:
        entry = state["funds"].setdefault(code, {})
        observe(entry, limit_observation(limits.get(code), now), code, stamp)
    atomic_json(state_path, state)
    due_limits = {c for c in codes if force_refresh or not valid_limit(limits.get(c)) or refresh_due(limits.get(c), now) or
                  any(not v.get("resolved_at") for v in state["funds"][c].get("baseline_conflicts", {}).values())}
    bulk = None
    if due_limits:
        try:
            bulk = fetch_fund_purchase_limit_bulk_map(force_refresh=True)
        except Exception as exc:
            print(f"[INDEX_TRACKING] 批量申购接口失败，使用现有详情回退: {exc}")
    failures = generated = 0
    frames, index_changed = {}, set()
    cutoff = market_clock("US", now)["complete_day"]
    # 每个共享指数每轮至多取一次；其它模块缩短CSV不影响已保存的有效端点证明。
    for symbol in sorted({INDEX_FUND_TRACKING_MAP[c]["index_symbol"] for c in codes}):
        path = index_csv_path(cache_dir, symbol)
        meta = returns["indices"].setdefault(symbol, {})
        valid = bool(meta.get("last_success_at") and meta.get("history_start_date") and path.exists())
        if force_refresh or source_due(meta, now, valid=valid):
            meta["last_attempt_at"] = clock().isoformat(timespec="seconds")
            atomic_json(returns_path, returns)
            try:
                frames[symbol] = merge_index_prices(path, fetch_index_prices(symbol, now), cutoff)
                meta.update(last_success_at=clock().isoformat(timespec="seconds"), latest_close_date=frames[symbol].date.max(),
                            history_start_date=frames[symbol].date.min(), source="sina_us_index_daily",
                            source_url=f"https://finance.sina.com.cn/staticdata/us/{symbol}", error=None)
                index_changed.add(symbol)
            except Exception as exc:
                failures += 1; meta["error"] = str(exc)
                print(f"[INDEX_TRACKING] {symbol} 指数刷新失败: {exc}")
        if symbol not in frames:
            frames[symbol] = clean_prices(pd.read_csv(path), cutoff) if path.exists() else pd.DataFrame()
        frames[symbol] = with_verified_index_endpoints(frames[symbol], returns, symbol, cutoff)
    atomic_json(returns_path, returns)
    for code in codes:
        entry, data = state["funds"][code], returns["funds"].setdefault(code, {})
        nav = data.setdefault("nav", {})
        nav_changed = False
        if force_refresh or source_due(nav, now, valid=bool(nav.get("series") and nav.get("profile"))):
            nav["last_attempt_at"] = clock().isoformat(timespec="seconds")
            atomic_json(returns_path, returns)
            try:
                fetched = fetch_fund_source(code, now)
                if not fetched.get("series") or not fetched.get("profile", {}).get("fund_name"):
                    raise ValueError("基金资料或官方累计收益缺失")
                nav = {**fetched, "last_attempt_at": nav["last_attempt_at"], "last_success_at": clock().isoformat(timespec="seconds"), "error": None}
                data["nav"] = nav; nav_changed = True
                if nav.get("profile", {}).get("group_audit"):
                    print(f"[INDEX_TRACKING] {code} 资料核对: {nav['profile']['group_audit']}")
            except Exception as exc:
                failures += 1; nav["error"] = str(exc)
                print(f"[INDEX_TRACKING] {code} 实际收益刷新失败，保留旧成功快照: {exc}")
        # 旧审计标签只重新核对已有真实字段，不为元数据修正额外联网。
        profile = nav.get("profile", {})
        if code != "007721" and matches_tracking_target(INDEX_FUND_TRACKING_MAP[code]["index_symbol"], profile.get("tracking_target", "")):
            profile["group_audit"] = ""
        meta = data.setdefault("limit_refresh", {})
        if code in due_limits:
            meta["last_attempt_at"] = clock().isoformat(timespec="seconds")
            atomic_json(returns_path, returns)
            try:
                detail = get_fund_purchase_limit(code, force_refresh=True, bulk_limit_map=bulk,
                                                 return_detail=True, detail_validator=valid_limit)
                if not valid_limit(detail):
                    raise ValueError(detail.get("error") or "限购结果未经有效确认")
                current_record = read_mapping(limit_path).get(code)
                # 真正抓取/落盘晚于本轮启动时间；仅新抓取允许使用当前执行时钟。
                current = limit_observation(current_record, clock())
                if not current:
                    raise ValueError("新限购记录未有效落盘")
                meta.update(last_success_at=current["observed_at"], error=None)
                observe(entry, current, code, clock().isoformat(timespec="seconds"))
            except Exception as exc:
                failures += 1; meta["error"] = str(exc)
                print(f"[INDEX_TRACKING] {code} 限购刷新失败: {exc}")
        elif code in limits:
            meta.setdefault("last_success_at", limits[code].get("fetched_at"))
        symbol = INDEX_FUND_TRACKING_MAP[code]["index_symbol"]
        # 仅在输入刷新时重算。已成功事件绝不因收益从N/A补齐而重绘。
        comparison_end = data.get("comparison", {}).get("comparison_end_date")
        expired = comparison_end and (clock().date()-pd.Timestamp(comparison_end).date()).days > DATE_TOLERANCE_DAYS
        if nav_changed or symbol in index_changed or "comparison" not in data or expired:
            data["comparison"] = calculate_comparison(nav.get("series", []), frames[symbol], now=now,
                                                       latest_nav_date=nav.get("latest_nav_date"))
            data["comparison"]["calculated_at"] = stamp
        atomic_json(returns_path, returns)
        atomic_json(state_path, state)
        pending = sorted((e for e in entry.get("events", {}).values() if not e.get("generated_at")),
                         key=lambda e: (e.get("observed_at", ""), e["id"]))
        if pending:
            event = pending[0]
            event["last_attempt_at"] = stamp
            try:
                profile = {"fund_code": code, "fund_name": f"基金{code}（资料暂缺）",
                           "currency": "unknown", **INDEX_FUND_TRACKING_MAP[code], **nav.get("profile", {})}
                path = output_dir / f"{code}.png"
                # 若图片已原子保存但状态提交中断，PNG事件ID可恢复完成标记，避免再发送。
                from PIL import Image
                recovered = False
                if path.exists():
                    try:
                        with Image.open(path) as image:
                            image.verify()
                        with Image.open(path) as image:
                            recovered = image.info.get("index_fund_event_id") == event["id"]
                    except (OSError, SyntaxError):
                        print(f"[INDEX_TRACKING] {code} 旧PNG无效，按待处理事件重新绘图")
                if not recovered:
                    render_index_fund_limit_change(profile, event, data["comparison"], path, logo_path=MARK_IMAGE)
                    with Image.open(path) as image:
                        image.verify()
                    generated += 1
                event["generated_at"] = clock().isoformat(timespec="seconds")
                event.pop("error", None)
                print(f"[INDEX_TRACKING] {code} 事件完成: {event['old_value']} -> {event['new_value']} ({path})")
            except Exception as exc:
                failures += 1; event["error"] = str(exc)
                print(f"[INDEX_TRACKING] {code} 事件绘图失败，保留待重试: {exc}")
            atomic_json(state_path, state)
    print(f"[INDEX_TRACKING] 检查{len(codes)}只，限购到期{len(due_limits)}只，新图{generated}张，异常{failures}项")
    return 1 if failures else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="25只指数基金真实历史收益及限购事件观察")
    parser.add_argument("--auto", action="store_true", help="检查独立观察池（默认全部25只）")
    parser.add_argument("--fund-code", choices=list(INDEX_FUND_TRACKING_MAP), help="只检查指定基金")
    parser.add_argument("--force-refresh", action="store_true", help="提前刷新选中基金及其共享指数")
    args = parser.parse_args(argv)
    try:
        return run_tracking(fund_codes=[args.fund_code] if args.fund_code else None, force_refresh=args.force_refresh)
    except Exception as exc:
        print(f"[INDEX_TRACKING] 检查无法完成，保留缓存: {exc}")
        return 1
