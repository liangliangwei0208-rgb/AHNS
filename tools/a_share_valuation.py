"""沪深市价总值 / 名义 GDP TTM，专用于走势图的可选估值背景。

初始化旧历史是修订序列的回顾展示。后续观察另存时间，不能把修订值
倒填为当年已经知道的策略信号。模块导入不联网、不写文件。
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

import numpy as np
import pandas as pd

from tools.configs.a_share_valuation_configs import (
    MC_GDP_OVER_THRESHOLD, MC_GDP_LOW_THRESHOLD, MC_GDP_DEEP_LOW_THRESHOLD,
    MC_GDP_MARKET_CAP_TTL_HOURS, MC_GDP_GDP_TTL_HOURS, MC_GDP_FAILURE_RETRY_HOURS,
    MC_GDP_REFRESH_BUDGET_SECONDS, MC_GDP_CACHE_FILENAME,
    MC_GDP_STATE_COLORS, MC_GDP_STATE_ALPHAS, MC_GDP_REGIME_LABEL_MIN_WIDTH_PX,
)

SOURCE_URLS = {"cap": "https://data.eastmoney.com/cjsj/gpjytj.html",
               "gdp": "https://data.eastmoney.com/cjsj/gdp.html"}
_EVENT_COLUMNS = ["available_at", "ratio", "basis", "period_date", "gdp_period"]


def _utc(value=None):
    stamp = pd.Timestamp.now(tz="Asia/Shanghai") if value is None else pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("Asia/Shanghai")
    return stamp.tz_convert("UTC")


def _numeric(values):
    text = values.astype(str).str.replace(",", "", regex=False).str.strip()
    missing = values.isna() | text.isin(["", "--", "-", "None", "nan"])
    result = pd.to_numeric(text.where(~missing), errors="coerce")
    if (result.isna() & ~missing).any():
        raise ValueError("金额字段含无法识别的非空值")
    if ((result <= 0) | np.isinf(result)).any():
        raise ValueError("金额必须为有限正数，检查字段或单位")
    return result


def _deduplicate(frame, columns):
    if frame.groupby("period_date")[columns].nunique(dropna=False).gt(1).any().any():
        raise ValueError("同一统计期存在相互冲突的记录")
    return frame.drop_duplicates("period_date").sort_values("period_date").reset_index(drop=True)


def normalize_market_cap(raw):
    """AKShare 当前沪深市价总值均为亿元；不含北交所，不取流通市值。"""
    required = {"数据日期", "市价总值-上海", "市价总值-深圳"}
    if not required.issubset(raw.columns):
        raise ValueError(f"市值字段不完整: {list(raw.columns)}")
    periods = []
    for value in raw["数据日期"]:
        match = re.fullmatch(r"(\d{4})年(\d{1,2})月份?", str(value).strip())
        date = pd.Timestamp(int(match[1]), int(match[2]), 1) if match else pd.Timestamp(value)
        periods.append(date.to_period("M").end_time.normalize())
    frame = pd.DataFrame({"period_date": periods,
                          "sse_market_cap_yi": _numeric(raw["市价总值-上海"]).to_numpy(),
                          "szse_market_cap_yi": _numeric(raw["市价总值-深圳"]).to_numpy()})
    # 最新月可能只发布了指数高低值，缺市值时整月跳过，绝不补零。
    frame = frame.dropna()
    frame = _deduplicate(frame, ["sse_market_cap_yi", "szse_market_cap_yi"])
    frame["market_cap_yi"] = frame.sse_market_cap_yi + frame.szse_market_cap_yi
    if frame.empty:
        raise ValueError("没有完整的沪深市值记录")
    return frame


def normalize_gdp(raw):
    """名义 GDP 年内累计（亿元）先差分为单季，再对连续四季求 TTM。"""
    if not {"季度", "国内生产总值-绝对值"}.issubset(raw.columns):
        raise ValueError(f"GDP 字段不完整: {list(raw.columns)}")
    quarters = []
    for value in raw["季度"]:
        text = str(value).strip()
        if "季度" in text:
            numbers = re.findall(r"\d+", text)
            year, quarter = int(numbers[0]), int(numbers[-1])
            if not 1 <= quarter <= 4:
                raise ValueError(f"无法识别 GDP 季度: {text}")
            period = pd.Period(f"{year}Q{quarter}", freq="Q")
        else:
            period = pd.Timestamp(value).to_period("Q")
        quarters.append(period)
    frame = pd.DataFrame({"period_date": [p.end_time.normalize() for p in quarters],
                          "gdp_ytd_yi": _numeric(raw["国内生产总值-绝对值"]).to_numpy()}).dropna()
    frame = _deduplicate(frame, ["gdp_ytd_yi"])
    frame["year"] = frame.period_date.dt.year
    frame["quarter"] = frame.period_date.dt.quarter
    previous = frame.groupby("year").gdp_ytd_yi.shift()
    consecutive = frame.period_date.dt.to_period("Q").astype("int64").diff().eq(1)
    frame["gdp_single_quarter_yi"] = np.where(
        frame.quarter.eq(1), frame.gdp_ytd_yi,
        (frame.gdp_ytd_yi - previous).where(consecutive))
    if frame.gdp_single_quarter_yi.le(0).any():
        raise ValueError("GDP 单季值非正，累计口径可能变化")
    serial = frame.period_date.dt.to_period("Q").astype("int64")
    full_window = serial.diff(3).eq(3)
    frame["gdp_ttm_yi"] = frame.gdp_single_quarter_yi.rolling(4, min_periods=4).sum().where(full_window)
    return frame


def build_revised_history(cap, gdp):
    """仅用于初始化的修订历史：统计期不是实际发布日期。"""
    usable = gdp.dropna(subset=["gdp_ttm_yi"])[["period_date", "gdp_ttm_yi"]]
    if cap.empty or usable.empty:
        return pd.DataFrame(columns=["period_date", "gdp_period", "ratio"])
    result = pd.merge_asof(cap.sort_values("period_date"),
                           usable.rename(columns={"period_date": "gdp_period"}).sort_values("gdp_period"),
                           left_on="period_date", right_on="gdp_period", direction="backward")
    result["ratio"] = result.market_cap_yi / result.gdp_ttm_yi
    return result.dropna(subset=["ratio"])


def classify_ratio(ratio, over=None, low=None, deep_low=None):
    over = MC_GDP_OVER_THRESHOLD if over is None else over
    low = MC_GDP_LOW_THRESHOLD if low is None else low
    deep_low = MC_GDP_DEEP_LOW_THRESHOLD if deep_low is None else deep_low
    if not 0 < deep_low < low < over:
        raise ValueError("MC/GDP 阈值必须满足 0 < deep_low < low < over")
    if pd.isna(ratio) or not math.isfinite(float(ratio)) or ratio <= 0:
        return None
    if ratio >= over:
        return "OVER"
    if ratio <= deep_low:
        return "DEEP LOW"
    return "LOW" if ratio <= low else "NEUTRAL"


def _records(frame):
    # pandas 的 JSON 转换同时规范 Timestamp 与 NaN，避免缓存出现非标准 NaN。
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def _source_frame(source):
    frame = pd.DataFrame(source.get("rows", []))
    if not frame.empty:
        frame["period_date"] = pd.to_datetime(frame.period_date, errors="raise")
    return frame


def _seed_legacy(cache_dir):
    """只读迁入参考脚本缓存，重新验证原始字段，不信任旧的派生 ratio。"""
    sources = {}
    specs = {
        "cap": ("a_share_market_cap_monthly.csv", {"date": "数据日期", "sse_market_cap_yi": "市价总值-上海",
                    "szse_market_cap_yi": "市价总值-深圳"}, normalize_market_cap),
        "gdp": ("china_nominal_gdp_quarterly.csv", {"date": "季度", "gdp_ytd_yi": "国内生产总值-绝对值"}, normalize_gdp),
    }
    for key, (name, rename, normalizer) in specs.items():
        path = cache_dir / name
        if path.exists():
            try:
                frame = normalizer(pd.read_csv(path).rename(columns=rename))
                sources[key] = {"rows": _records(frame), "source": SOURCE_URLS[key],
                                "unit": "亿元", "seed": name}
            except (ValueError, OSError, KeyError, TypeError) as error:
                print(f"[WARN] MC/GDP 参考缓存无效: {name}: {error}")
    return sources


def fetch_sources(due):
    """在独立进程限制 AKShare 总耗时，不修改主进程 requests 设置。"""
    project = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        result = subprocess.run([sys.executable, "-m", "tools.a_share_valuation", "--fetch", *due],
                                cwd=project, env=env, timeout=MC_GDP_REFRESH_BUDGET_SECONDS,
                                capture_output=True, encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise ValueError(result.stderr[-300:] or "宏观子进程失败")
        payload = json.loads(result.stdout)
        return {key: pd.DataFrame(value["rows"]) if "rows" in value else value.get("error", "来源失败")
                for key, value in payload.items()}
    except (subprocess.TimeoutExpired, OSError, ValueError) as error:
        return {key: str(error) for key in due}


def _as_events(doc):
    frame = pd.DataFrame(doc.get("history", []) + doc.get("observations", []), columns=_EVENT_COLUMNS)
    frame.attrs["historical_cutoff"] = doc.get("historical_cutoff")
    frame.attrs["sources"] = {k: {field: v.get(field) for field in ("source", "last_success_at", "last_attempt_at", "error")}
                              for k, v in doc.get("sources", {}).items()}
    return frame


def load_valuation(cache_dir="cache", *, now=None, fetcher=None, refresh=True):
    """有效缓存优先；按来源 TTL 刷新，失败不阻断出图。返回绘图事件表。"""
    stamp = _utc(now)
    cache_dir = Path(cache_dir)
    path = cache_dir / MC_GDP_CACHE_FILENAME
    doc = {"version": 1, "unit": "亿元", "sources": {}, "history": [], "observations": []}
    try:
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if loaded.get("version") != 1 or loaded.get("unit") != "亿元":
                raise ValueError("未知估值缓存版本或单位")
            # 同一容器读取固定键，不将元数据当作业务行枚举。
            _as_events(loaded)
            doc = loaded
        else:
            doc["sources"] = _seed_legacy(cache_dir)
        due = []
        for key, ttl in (("cap", MC_GDP_MARKET_CAP_TTL_HOURS), ("gdp", MC_GDP_GDP_TTL_HOURS)):
            source = doc["sources"].get(key, {})
            success, attempt = source.get("last_success_at"), source.get("last_attempt_at")
            fresh = success and 0 <= (stamp - _utc(success)).total_seconds() < ttl * 3600
            backoff = source.get("error") and attempt and 0 <= (stamp - _utc(attempt)).total_seconds() < MC_GDP_FAILURE_RETRY_HOURS * 3600
            if not fresh and not backoff:
                due.append(key)
        if refresh and due:
            try:
                fetched = (fetcher or fetch_sources)(due)
            except Exception as error:
                # 传输层意外异常也走逐来源失败路径，不能丢掉可用旧缓存。
                fetched = {key: str(error) for key in due}
            if now is None:
                stamp = _utc()  # 首次可用时点是响应取得之后，不是请求发出之前。
            for key in due:
                source = doc["sources"].setdefault(key, {})
                source["last_attempt_at"] = stamp.isoformat()
                try:
                    raw = fetched.get(key, "缺少来源响应")
                    if not isinstance(raw, pd.DataFrame):
                        raise ValueError(str(raw))
                    normalized = (normalize_market_cap if key == "cap" else normalize_gdp)(raw)
                    normalized = normalized.loc[normalized.period_date <= stamp.tz_convert("Asia/Shanghai").tz_localize(None).normalize()]
                    if normalized.empty or (key == "gdp" and normalized.gdp_ttm_yi.notna().sum() == 0):
                        raise ValueError("没有有效完整统计期")
                    previous = _source_frame(source)
                    column = "market_cap_yi" if key == "cap" else "gdp_ttm_yi"
                    if not previous.empty:
                        if normalized.period_date.max() < previous.period_date.max():
                            raise ValueError("来源历史发生截断，不覆盖较新缓存")
                        if key == "gdp" and normalized.dropna(subset=[column]).period_date.max() < previous.dropna(subset=[column]).period_date.max():
                            # 新季度标签不代表新TTM可用，缺中间季度时不能退回旧分母。
                            raise ValueError("最新有效 GDP TTM 统计期回退，不覆盖可信旧值")
                        old_value = previous[column].dropna().iloc[-1]
                        new_value = normalized[column].dropna().iloc[-1]
                        if not .1 < new_value / old_value < 10:
                            raise ValueError("金额出现数量级变化，拒绝疑似单位漂移")
                    source.update(rows=_records(normalized), source=SOURCE_URLS[key], unit="亿元",
                                  last_success_at=stamp.isoformat(), error=None)
                except (ValueError, TypeError, KeyError, IndexError) as error:
                    source["error"] = str(error)
                    print(f"[WARN] MC/GDP {key} 刷新失败，保留可信旧值: {error}")
        cap = _source_frame(doc["sources"].get("cap", {}))
        gdp = _source_frame(doc["sources"].get("gdp", {}))
        if not cap.empty and not gdp.empty:
            if not doc.get("historical_cutoff"):
                history = build_revised_history(cap, gdp)
                if not history.empty:
                    doc["historical_cutoff"] = stamp.isoformat()
                    doc["history"] = [{"available_at": row.period_date.tz_localize("Asia/Shanghai").isoformat(),
                                       "period_date": str(row.period_date.date()), "gdp_period": str(row.gdp_period.date()),
                                       "ratio": float(row.ratio), "basis": "historical revised series"}
                                      for row in history.itertuples()]
            latest_cap = cap.iloc[-1]
            latest_gdp = gdp.dropna(subset=["gdp_ttm_yi"]).iloc[-1]
            ratio = float(latest_cap.market_cap_yi / latest_gdp.gdp_ttm_yi)
            observation = {"available_at": stamp.isoformat(), "period_date": str(latest_cap.period_date.date()),
                           "gdp_period": str(latest_gdp.period_date.date()), "ratio": ratio, "basis": "observed"}
            previous = doc["observations"][-1] if doc["observations"] else {}
            if any(previous.get(k) != observation[k] for k in ("period_date", "gdp_period", "ratio")):
                # 接口没有发布日期和 vintage；首次观测是可核验的保守可用时点。
                doc["observations"].append(observation)
        if refresh and (due or not path.exists()):
            cache_dir.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            os.replace(temp, path)
        frame = _as_events(doc)
        if not cap.empty and not gdp.empty and not frame.empty:
            age_text = []
            for key in ("cap", "gdp"):
                success = doc["sources"][key].get("last_success_at")
                age = f"{(stamp-_utc(success)).total_seconds()/3600:.1f}h" if success else "seed"
                age_text.append(f"{key} cache_age={age}")
            latest = doc["observations"][-1]
            print(f"[MC/GDP] cap={latest['period_date']} GDP={latest['gdp_period']} "
                  f"ratio={latest['ratio']:.6f} {classify_ratio(latest['ratio'])}; " + "; ".join(age_text))
        return frame
    except Exception as error:
        # 估值是可选图层，缓存损坏/权限异常均不能拖垮股票图或基金总入口。
        print(f"[WARN] MC/GDP unavailable: {error}")
        try:
            return _as_events(doc)
        except Exception:
            return pd.DataFrame(columns=_EVENT_COLUMNS)


def align_valuation(frame, dates, *, now=None):
    """以 available_at 向后匹配；当日用实际当前时间，历史日线用15:00。"""
    current = _utc(now)
    days = pd.DatetimeIndex(pd.to_datetime(dates)).tz_localize(None).normalize()
    result = pd.DataFrame({"date": days, "ratio": np.nan, "basis": None})
    if frame is None or frame.empty:
        return result
    events = frame.copy()
    events["available_at"] = pd.to_datetime(events.available_at, utc=True, errors="coerce")
    events["ratio"] = pd.to_numeric(events.ratio, errors="coerce")
    events = events.loc[events.available_at.notna() & events.ratio.gt(0) & np.isfinite(events.ratio)]
    cutoff = frame.attrs.get("historical_cutoff")
    cutoff_day = _utc(cutoff).tz_convert("Asia/Shanghai").tz_localize(None).normalize() if cutoff else None
    history_mask = days < cutoff_day if cutoff_day is not None else np.zeros(len(days), dtype=bool)
    queries = days.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=15)
    today = current.tz_convert("Asia/Shanghai").normalize()
    queries = pd.DatetimeIndex([min(q, current) if q.normalize() == today else q for q in queries]).tz_convert("UTC")
    for historical, mask in ((True, history_mask), (False, ~history_mask)):
        selected = events.loc[events.basis.eq("historical revised series") if historical else events.basis.ne("historical revised series")]
        if selected.empty or not mask.any():
            continue
        query = pd.DataFrame({"available_at": queries[mask], "position": np.flatnonzero(mask)}).sort_values("available_at")
        merged = pd.merge_asof(query, selected.sort_values("available_at"), on="available_at", direction="backward")
        result.loc[merged.position, ["ratio", "basis"]] = merged[["ratio", "basis"]].to_numpy()
    return result


def regime_segments(aligned, *, over=None, low=None, deep_low=None):
    """按展示交易日压缩连续状态，缺失和 NEUTRAL 都会中断背景。"""
    if aligned.empty:
        return []
    dates = pd.DatetimeIndex(pd.to_datetime(aligned.date))
    states = [classify_ratio(value, over, low, deep_low) for value in aligned.ratio]
    segments = []
    start = 0
    for end in range(1, len(states) + 1):
        if end == len(states) or states[end] != states[start]:
            if states[start] in MC_GDP_STATE_ALPHAS:
                # 两交易日之间以中点分界；周末不制造额外 artist。
                left = dates[start] if start == 0 else dates[start-1] + (dates[start]-dates[start-1])/2
                right = dates[end-1] if end == len(states) else dates[end-1] + (dates[end]-dates[end-1])/2
                segments.append((left, right, states[start]))
            start = end
    return segments


def draw_valuation_background(ax, aligned, *, over=None, low=None, deep_low=None):
    limits = ax.get_xlim(), ax.get_ylim()
    for left, right, state in regime_segments(aligned, over=over, low=low, deep_low=deep_low):
        ax.axvspan(left, right, facecolor=MC_GDP_STATE_COLORS[state], alpha=MC_GDP_STATE_ALPHAS[state],
                   edgecolor="none", zorder=0, label="_nolegend_")
    ax.set_xlim(limits[0]); ax.set_ylim(limits[1])


def draw_regime_labels(ax, aligned, *, over=None, low=None, deep_low=None):
    """布局完成后按像素宽度放置英文状态，避开信号与边缘色带。"""
    import matplotlib.dates as mdates
    renderer = ax.figure.canvas.get_renderer()
    obstacles = [text.get_window_extent(renderer) for text in ax.texts]
    obstacles += [patch.get_window_extent(renderer) for patch in ax.patches if patch.get_zorder() > 0]
    for left, right, state in regime_segments(aligned, over=over, low=low, deep_low=deep_low):
        x0, x1 = mdates.date2num(left), mdates.date2num(right)
        if ax.transData.transform((x1, 0))[0] - ax.transData.transform((x0, 0))[0] < MC_GDP_REGIME_LABEL_MIN_WIDTH_PX:
            continue
        text = ax.text((x0+x1)/2, .13, state, transform=ax.get_xaxis_transform(), ha="center", va="bottom",
                       fontsize=6.5, color=MC_GDP_STATE_COLORS[state], alpha=.85, zorder=1, clip_on=True)
        bbox = text.get_window_extent(renderer)
        if any(bbox.overlaps(other) for other in obstacles):
            text.remove()
        else:
            obstacles.append(bbox)


def _worker(due):
    from concurrent.futures import ThreadPoolExecutor
    import akshare as ak
    import requests
    original = requests.get
    def bounded_get(*args, **kwargs):
        kwargs.setdefault("timeout", (3, 8))
        return original(*args, **kwargs)
    requests.get = bounded_get
    def fetch(key):
        try:
            raw = (ak.macro_china_stock_market_cap if key == "cap" else ak.macro_china_gdp)()
            return key, {"rows": _records(raw)}
        except Exception as error:
            return key, {"error": str(error)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = dict(pool.map(fetch, due))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[1] == "--fetch":
    _worker(sys.argv[2:])
