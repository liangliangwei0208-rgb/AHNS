"""
fund_estimator.py

用途
====
输入一个或多个基金代码，估算今日涨跌幅，并生成汇总表格图片。

支持类型
========
1. 普通股票型 / QDII 股票型基金
   - 从 ak.fund_portfolio_hold_em() 获取最近披露前 N 大股票持仓；
   - 默认将前 N 大持仓权重归一化到 100%；
   - 海外表可启用“有效持仓增强 + 失败持仓/未披露仓位配置基准补偿”口径；
   - 按股票最新交易日涨跌幅估算；
   - 新增支持港股持仓，港股代码自动识别为 HK 市场。

2. ETF 联接基金 / 指数联接基金 / FOF
   - 使用 DEFAULT_FUND_PROXY_MAP 或用户传入的 proxy_map 指定底层 ETF / 指数代理；
   - 按底层 ETF / 指数涨跌幅 × 持仓权重估算；
   - 默认不把 ETF 联接基金的底层 ETF 权重归一化到 100%，现金仓位按 0 处理；
   - 如果你想把底层代理权重也归一化，可以设置 proxy_normalize_weights=True。

核心输出表
==========
序号 | 基金代码 | 基金名称 | 今日预估涨跌幅 | 限购金额

默认排序
========
按“今日预估涨跌幅”从高到低排序：
- 序号越小，涨幅越高；
- 序号越大，跌幅越大；
- 计算失败的基金排最后。

最推荐调用方式
==============
from tools.get_top10_holdings import estimate_funds_and_save_table

result_df, detail_map = estimate_funds_and_save_table(
    fund_codes=["017437", "007467", "015016", "007722"],
    top_n=10,
    output_file="output/fund_estimate_table.png",
    title=None,
    holding_mode="auto",
    us_realtime=False,
    hk_realtime=True,
    include_purchase_limit=True,
    sort_by_return=True,
    watermark_text="鱼师",
    up_color="red",
    down_color="green",
    print_table=True,
    save_table=True,
)

重要说明
========
1. 基金持仓来自公开披露数据，不是基金实时持仓。
2. 股票型基金默认按“可获取行情的前 N 大股票再次归一化到 100%”后的估算；如果某只持仓行情缺失，会剔除该持仓并在剩余可查持仓中重新分配权重。
3. ETF 联接 / FOF 的估算值默认使用代理资产的原始披露仓位，不强制归一化。
4. QDII 基金会受汇率、估值时点、现金仓位、费用、申赎等影响；本模块只做近似估算。
5. 限购金额来自公开网页文本解析，可能返回“未知”。
6. 本版本新增 JSON 文件缓存：
   - 基金持仓默认 75 天更新一次；
   - 限购金额默认 7 天更新一次；
   - CN/HK 行情默认小时级缓存，US 行情默认日级缓存。
"""

import re
import json
import time
import warnings
import requests
import akshare as ak
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.offsetbox import AnchoredOffsetbox, HPacker, TextArea, VPacker

from pathlib import Path
from io import StringIO
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from matplotlib import font_manager

from tools.configs.fund_proxy_configs import DEFAULT_FUND_PROXY_MAP, OVERSEAS_VALID_HOLDING_BOOST
from tools.configs.market_calendar_configs import (
    KR_MARKET_ZERO_HOLIDAYS,
    KR_MARKET_ZERO_HOLIDAY_MD,
    MARKET_CALENDAR_NAMES,
    MARKET_CLOSE_BUFFER_HOURS,
)
from tools.configs.market_benchmark_configs import MARKET_BENCHMARK_ITEMS
from tools.configs.residual_benchmark_configs import (
    DEFAULT_RESIDUAL_BENCHMARK_KEY,
    FUND_RESIDUAL_BENCHMARK_MAP,
    RESIDUAL_BENCHMARK_SPECS,
)
from tools.configs.security_mappings import KR_TICKER_MAP, US_TICKER_MAP
from tools.paths import CACHE_DIR

# Runtime JSON cache utilities.

FUND_HOLDINGS_CACHE_FILE = "fund_holdings_cache.json"
FUND_PURCHASE_LIMIT_CACHE_FILE = "fund_purchase_limit_cache.json"
SECURITY_RETURN_CACHE_FILE = "security_return_cache.json"
FUND_ESTIMATE_RETURN_CACHE_FILE = "fund_estimate_return_cache.json"

_SECURITY_RETURN_RUNTIME_CACHE = {}

SECURITY_HOURLY_CACHE_RETENTION_DAYS = 15
SECURITY_DAILY_CACHE_RETENTION_DAYS = 30
SECURITY_INDEX_CACHE_RETENTION_DAYS = 300
FUND_ESTIMATE_HISTORY_RETENTION_DAYS = 300

ANCHOR_CACHE_STABLE_RETENTION_DAYS = 300
ANCHOR_TRANSIENT_CACHE_HOURS = 2
ANCHOR_PENDING_CACHE_HOURS = 1
ANCHOR_MARKET_STATUSES = {"traded", "closed", "pending", "missing", "stale"}
ANCHOR_COMPLETE_STATUSES = {"traded", "closed"}
ANCHOR_BAD_STATUSES = {"pending", "missing", "stale"}


def _cache_log(message: str) -> None:
    """统一缓存日志输出，便于在 GitHub Actions 中定位。"""
    print(f"[CACHE] {message}", flush=True)


def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _load_json_cache(filename: str, default=None):
    """
    读取 cache/*.json。文件不存在或损坏时返回 default。
    """
    if default is None:
        default = {}

    _ensure_cache_dir()
    path = CACHE_DIR / filename

    if not path.exists():
        return default

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(default, dict) and not isinstance(data, dict):
            return default

        return data

    except Exception as e:
        print(f"[WARN] 缓存读取失败: {path}, 原因: {e}", flush=True)
        return default


def _save_json_cache(filename: str, data) -> None:
    """
    保存 cache/*.json。
    """
    _ensure_cache_dir()
    path = CACHE_DIR / filename

    tmp_path = path.with_suffix(path.suffix + ".tmp")

    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    tmp_path.replace(path)


def _is_cache_fresh(fetched_at, max_age_days=None, max_age_hours=None) -> bool:
    """
    判断缓存是否仍在有效期内。

    max_age_days:
        日级有效期，例如基金持仓 75 天、限购 7 天。
    max_age_hours:
        小时级有效期，例如 A股/港股盘中行情 1-2 小时。
    """
    if not fetched_at:
        return False

    try:
        t = pd.to_datetime(fetched_at)
    except Exception:
        return False

    if pd.isna(t):
        return False

    now = pd.Timestamp.now()

    # 如果缓存时间带 timezone，而 now 不带 timezone，做一次兼容
    try:
        age_seconds = (now - t).total_seconds()
    except TypeError:
        age_seconds = (now.tz_localize(None) - t.tz_localize(None)).total_seconds()

    if max_age_hours is not None:
        return age_seconds <= float(max_age_hours) * 3600

    if max_age_days is not None:
        return age_seconds <= float(max_age_days) * 86400

    return False


def _parse_security_cache_bucket_from_key(cache_key: str) -> tuple[datetime | None, str | None]:
    """
    从行情缓存 key 中解析日期桶。

    支持：
        CN:300502:2026-05-03-13:intraday
        US:NVDA:2026-05-03:last_close
        INDEX:.NDX:2026-05-03:last_close
    """
    text = str(cache_key)
    matches = list(re.finditer(r"(20\d{2}-\d{2}-\d{2})(?:-(\d{1,2}))?", text))
    if not matches:
        return None, None

    match = matches[-1]
    date_text = match.group(1)
    hour_text = match.group(2)

    try:
        if hour_text is not None:
            return datetime.strptime(
                f"{date_text}-{str(hour_text).zfill(2)}",
                "%Y-%m-%d-%H",
            ), "hourly"
        return datetime.strptime(date_text, "%Y-%m-%d"), "daily"
    except Exception:
        return None, None


def _parse_cache_fetched_at(value) -> datetime | None:
    if not value:
        return None

    try:
        dt = pd.to_datetime(value, errors="coerce")
        if pd.isna(dt):
            return None
        if getattr(dt, "tzinfo", None) is not None:
            dt = dt.tz_localize(None)
        return dt.to_pydatetime()
    except Exception:
        return None


def _security_cache_market(cache_key: str, item) -> str:
    if isinstance(item, dict) and item.get("market"):
        return str(item.get("market", "")).strip().upper()

    return str(cache_key).split(":", 1)[0].strip().upper()


def _security_cache_retention_days(cache_key: str, item, bucket_kind: str | None) -> int:
    if str(cache_key).startswith("SECURITY:") and isinstance(item, dict):
        status = str(item.get("status", "")).strip().lower()
        if status in {"traded", "closed"}:
            return ANCHOR_CACHE_STABLE_RETENTION_DAYS
        return SECURITY_HOURLY_CACHE_RETENTION_DAYS

    market = _security_cache_market(cache_key, item)
    if market == "INDEX":
        return SECURITY_INDEX_CACHE_RETENTION_DAYS

    if bucket_kind == "hourly":
        return SECURITY_HOURLY_CACHE_RETENTION_DAYS

    if bucket_kind is None and isinstance(item, dict):
        valuation_mode = str(item.get("valuation_mode", "")).strip().lower()
        if valuation_mode == "intraday" and market in {"CN", "HK"}:
            return SECURITY_HOURLY_CACHE_RETENTION_DAYS

    return SECURITY_DAILY_CACHE_RETENTION_DAYS


def _is_security_cache_entry_expired(
    cache_key: str,
    item,
    *,
    now: datetime,
) -> bool:
    bucket_dt, bucket_kind = _parse_security_cache_bucket_from_key(cache_key)
    retention_days = _security_cache_retention_days(cache_key, item, bucket_kind)

    if bucket_dt is not None and bucket_kind == "hourly":
        return bucket_dt < now - timedelta(days=retention_days)

    if bucket_dt is not None:
        cutoff_date = (now - timedelta(days=retention_days)).date()
        return bucket_dt.date() < cutoff_date

    if isinstance(item, dict):
        fetched_dt = _parse_cache_fetched_at(item.get("fetched_at"))
        if fetched_dt is not None:
            return fetched_dt < now - timedelta(days=retention_days)

    # 日期不可解析时保留，避免误删有效缓存。
    return False


def _prune_security_return_cache(cache, now: datetime | None = None):
    """
    裁剪行情缓存，防止 security_return_cache.json 无限制增长。
    """
    if not isinstance(cache, dict):
        return cache

    if now is None:
        now = datetime.now()

    pruned = {}
    removed = 0
    for key, item in cache.items():
        if _is_security_cache_entry_expired(str(key), item, now=now):
            removed += 1
            continue
        pruned[key] = item

    if removed:
        _cache_log(f"行情缓存裁剪: removed={removed}, kept={len(pruned)}")

    return pruned


def _save_security_return_cache(cache) -> None:
    cache = _prune_security_return_cache(cache)
    _save_json_cache(SECURITY_RETURN_CACHE_FILE, cache)


def _anchor_security_cache_key(market, ticker, valuation_anchor_date) -> tuple[str, str, str]:
    market_norm = str(market or "").strip().upper()
    ticker_norm = _normalize_security_cache_ticker(market_norm, ticker)
    anchor_date = _normalize_trade_date_key(valuation_anchor_date)
    return f"SECURITY:{market_norm}:{ticker_norm}:{anchor_date}", ticker_norm, anchor_date


def _anchor_status_rank(status) -> int:
    status = str(status or "").strip().lower()
    return {
        "failed": 0,
        "missing": 1,
        "stale": 2,
        "pending": 3,
        "closed": 4,
        "traded": 5,
    }.get(status, 0)


def _is_anchor_cache_entry_fresh(item: dict) -> bool:
    status = str(item.get("status", "")).strip().lower()
    if status in {"traded", "closed"}:
        return True

    market = str(item.get("market", "")).strip().upper()
    if market == "US" and status == "stale":
        source_error_text = f"{item.get('source', '')} {item.get('error', '')}".lower()
        # 旧版本美股只用新浪日线；如果新浪停在前一天，会缓存 stale。
        # 现在新增了东方财富 / Yahoo 日线兜底，所以这类旧 stale 不应继续挡住重试。
        if "ak_stock_us_daily_trade_date_older_than_anchor" in source_error_text:
            return False

    if market in {"CN", "HK"} and status in {"missing", "stale"}:
        source_error_text = f"{item.get('source', '')} {item.get('error', '')}".lower()
        if "sina" not in source_error_text:
            return False

    max_age_hours = ANCHOR_PENDING_CACHE_HOURS if status == "pending" else ANCHOR_TRANSIENT_CACHE_HOURS
    return _is_cache_fresh(item.get("fetched_at"), max_age_hours=max_age_hours)


def _save_anchor_security_cache_entry(cache_key: str, entry: dict) -> None:
    cache = _load_json_cache(SECURITY_RETURN_CACHE_FILE, default={})
    if not isinstance(cache, dict):
        cache = {}

    old = cache.get(cache_key)
    if isinstance(old, dict):
        old_status = str(old.get("status", "")).strip().lower()
        new_status = str(entry.get("status", "")).strip().lower()
        if old_status == "traded" and new_status != "traded":
            return
        if (
            old_status == "traded"
            and new_status == "traded"
            and _normalize_trade_date_key(old.get("trade_date")) == _normalize_trade_date_key(old.get("valuation_anchor_date"))
            and _normalize_trade_date_key(entry.get("trade_date")) != _normalize_trade_date_key(entry.get("valuation_anchor_date"))
        ):
            return

    cache[cache_key] = entry
    _save_security_return_cache(cache)
    _SECURITY_RETURN_RUNTIME_CACHE[cache_key] = entry


def _anchor_return_result(
    *,
    market: str,
    ticker: str,
    valuation_anchor_date: str,
    status: str,
    return_pct=None,
    trade_date: str = "",
    source: str = "",
    calendar_is_open=None,
    error: str = "",
) -> dict:
    status = str(status or "missing").strip().lower()
    if status not in ANCHOR_MARKET_STATUSES:
        status = "missing"

    return {
        "market": str(market or "").strip().upper(),
        "ticker": str(ticker or "").strip().upper(),
        "valuation_anchor_date": _normalize_trade_date_key(valuation_anchor_date),
        "trade_date": _normalize_trade_date_key(trade_date),
        "return_pct": None if return_pct is None else float(return_pct),
        "status": status,
        "source": str(source or ""),
        "calendar_is_open": calendar_is_open,
        "error": str(error or ""),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def _beijing_now(now=None) -> datetime:
    if now is None:
        return datetime.now(ZoneInfo("Asia/Shanghai"))

    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return now.astimezone(ZoneInfo("Asia/Shanghai"))

    parsed = pd.to_datetime(now, errors="coerce")
    if pd.isna(parsed):
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    dt = parsed.to_pydatetime()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return dt.astimezone(ZoneInfo("Asia/Shanghai"))


def _market_calendar(market: str):
    market = str(market or "").strip().upper()
    calendar_name = MARKET_CALENDAR_NAMES.get(market)
    if not calendar_name:
        raise RuntimeError(f"未配置交易日历: market={market}")

    import pandas_market_calendars as mcal

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r".*break_start.*break_end.*",
            category=UserWarning,
        )
        return mcal.get_calendar(calendar_name)


def _market_schedule(market: str, start_date, end_date) -> pd.DataFrame:
    cal = _market_calendar(market)
    return cal.schedule(start_date=str(start_date), end_date=str(end_date))


def _market_is_open_on(market: str, day) -> bool:
    day_key = _normalize_trade_date_key(day)
    if not day_key:
        return False
    try:
        schedule = _market_schedule(market, day_key, day_key)
        return schedule is not None and not schedule.empty
    except Exception as exc:
        _cache_log(f"交易日历判断失败: market={market}, date={day_key}, error={exc}")
        return False


def _market_session_complete(market: str, day, now=None) -> bool:
    day_key = _normalize_trade_date_key(day)
    if not day_key:
        return False

    try:
        schedule = _market_schedule(market, day_key, day_key)
    except Exception:
        return False

    if schedule is None or schedule.empty or "market_close" not in schedule.columns:
        return False

    close_value = schedule.iloc[-1]["market_close"]
    close_dt = pd.Timestamp(close_value).to_pydatetime()
    if close_dt.tzinfo is None:
        close_dt = close_dt.replace(tzinfo=timezone.utc)

    complete_after = close_dt.astimezone(timezone.utc) + timedelta(hours=MARKET_CLOSE_BUFFER_HOURS)
    now_utc = _beijing_now(now).astimezone(timezone.utc)
    return now_utc >= complete_after


def _latest_complete_market_session(market: str, now=None, lookback_days: int = 15) -> str:
    now_bj = _beijing_now(now)
    end_date = now_bj.date()
    start_date = end_date - timedelta(days=int(lookback_days))

    try:
        schedule = _market_schedule(market, start_date, end_date)
    except Exception as exc:
        raise RuntimeError(f"{market} 交易日历读取失败: {exc}") from exc

    if schedule is None or schedule.empty or "market_close" not in schedule.columns:
        return ""

    now_utc = now_bj.astimezone(timezone.utc)
    out = schedule.copy()
    close_utc = pd.to_datetime(out["market_close"], utc=True)
    complete_after = close_utc + pd.Timedelta(hours=MARKET_CLOSE_BUFFER_HOURS)
    complete = out[complete_after <= pd.Timestamp(now_utc)]
    if complete.empty:
        return ""

    return pd.Timestamp(complete.index[-1]).strftime("%Y-%m-%d")


def determine_latest_valuation_anchor_date(markets=("US", "CN", "HK", "KR"), now=None) -> str:
    """
    确定本次海外/全球基金估算使用的全表统一估值锚点。

    取相关市场中最近一个已过“收盘 + 缓冲时间”的完整交易日；如果所有市场
    都没有完整交易日，返回空字符串，由调用方跳过写入有效收益记录。
    """
    candidates: list[str] = []
    errors: list[str] = []

    for market in markets:
        market_norm = str(market).strip().upper()
        try:
            latest = _latest_complete_market_session(market_norm, now=now)
            if latest:
                candidates.append(latest)
        except Exception as exc:
            errors.append(f"{market_norm}: {exc}")

    if not candidates:
        raise RuntimeError("无法确定海外/全球基金估值锚点: " + " | ".join(errors))

    anchor = max(candidates)
    _cache_log(f"估值锚点候选: {sorted(set(candidates))} -> {anchor}")
    return anchor


def _record_retention_date(record: dict) -> str:
    return (
        _normalize_date_string(record.get("valuation_date"))
        or _normalize_date_string(record.get("run_date_bj"))
    )


def _prune_estimate_record_map(records, now: datetime | None = None):
    if not isinstance(records, dict):
        return records, 0

    if now is None:
        now = datetime.now()

    cutoff_date = (now - timedelta(days=FUND_ESTIMATE_HISTORY_RETENTION_DAYS)).date()
    pruned = {}
    removed = 0

    for key, record in records.items():
        if not isinstance(record, dict):
            pruned[key] = record
            continue

        date_text = _record_retention_date(record)
        if not date_text:
            pruned[key] = record
            continue

        try:
            record_date = datetime.strptime(date_text, "%Y-%m-%d").date()
        except Exception:
            pruned[key] = record
            continue

        if record_date < cutoff_date:
            removed += 1
            continue

        pruned[key] = record

    return pruned, removed


def _prune_fund_estimate_return_cache(cache, now: datetime | None = None):
    """
    裁剪基金估算历史缓存，保留最近 300 天的 records 与 benchmark_records。
    """
    if not isinstance(cache, dict):
        return cache

    records, removed_records = _prune_estimate_record_map(cache.get("records"), now=now)
    benchmark_records, removed_benchmarks = _prune_estimate_record_map(
        cache.get("benchmark_records"),
        now=now,
    )

    if isinstance(records, dict):
        overseas_records = {}
        removed_domestic = 0
        for key, record in records.items():
            is_domestic_key = str(key).startswith("domestic:")
            is_domestic_record = (
                isinstance(record, dict)
                and str(record.get("market_group", "")).strip().lower() == "domestic"
            )
            if is_domestic_key or is_domestic_record:
                removed_domestic += 1
                continue
            overseas_records[key] = record
        records = overseas_records
        removed_records += removed_domestic

    if isinstance(records, dict):
        cache["records"] = records
    if isinstance(benchmark_records, dict):
        cache["benchmark_records"] = benchmark_records

    removed_total = removed_records + removed_benchmarks
    if removed_total:
        _cache_log(
            "基金估算历史缓存裁剪: "
            f"records_removed={removed_records}, benchmark_removed={removed_benchmarks}"
        )

    return cache


def _save_fund_estimate_return_cache(cache) -> None:
    cache = _prune_fund_estimate_return_cache(cache)
    _save_json_cache(FUND_ESTIMATE_RETURN_CACHE_FILE, cache)

# Last-close cache freshness helpers.

US_LAST_CLOSE_REFRESH_HOUR_BJ = 7


def _today_local_date_key(now=None) -> str:
    """返回本机时区下的日期字符串；GitHub Actions 中 TZ=Asia/Shanghai。"""
    if now is None:
        now = datetime.now()
    return now.strftime("%Y-%m-%d")


def _is_after_us_postclose_refresh_window(now=None) -> bool:
    """
    是否已经进入美股收盘后刷新窗口。

    说明：
    - 不在这里猜测“理论最新美股交易日”；
    - 周末、美国节假日、数据源延迟都交给 rsi_module / 行情源实际返回的 trade_date 处理；
    - 北京时间 07:00 后每天最多主动检查一次。
    """
    if now is None:
        now = datetime.now()
    return int(now.hour) >= int(US_LAST_CLOSE_REFRESH_HOUR_BJ)


def _parse_trade_date_value(value):
    """把缓存或行情返回的 trade_date 解析成 date；失败返回 None。"""
    if value is None:
        return None
    try:
        dt = pd.to_datetime(str(value), errors="coerce")
        if pd.isna(dt):
            return None
        return dt.date()
    except Exception:
        return None


def _normalize_trade_date_key(value) -> str:
    """将行情返回的交易日规范化为 YYYY-MM-DD；失败返回空字符串。"""
    if value is None:
        return ""
    try:
        dt = pd.to_datetime(str(value), errors="coerce")
        if pd.isna(dt):
            return ""
        return pd.Timestamp(dt).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _extract_trade_date_from_row(row, date_col) -> str:
    """从日线 DataFrame 的一行中提取交易日。"""
    if date_col is None:
        return ""
    try:
        return _normalize_trade_date_key(row.get(date_col))
    except Exception:
        return ""


def _daily_fetch_window(lookback_days=90, end_date=None) -> tuple[str, str, str]:
    """生成日线查询窗口，并把 end_date 统一限制为目标估值日。"""
    end_key = _normalize_trade_date_key(end_date) or _today_local_date_key()
    try:
        end_dt = pd.to_datetime(end_key)
        if pd.isna(end_dt):
            raise ValueError("invalid end date")
    except Exception:
        end_dt = pd.to_datetime(_today_local_date_key())
        end_key = pd.Timestamp(end_dt).strftime("%Y-%m-%d")

    start_dt = end_dt - timedelta(days=int(lookback_days))
    return start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d"), end_key


def _drop_rows_after_target_date(df: pd.DataFrame, date_values, end_date_key: str) -> pd.DataFrame:
    """丢弃晚于目标估值日的日线行，避免读入未来交易日或盘中行。"""
    if df is None or df.empty or not end_date_key:
        return df
    try:
        mask = pd.to_datetime(date_values, errors="coerce") <= pd.to_datetime(end_date_key)
        return df.loc[mask].copy()
    except Exception:
        return df


def _trade_date_is_after_target(trade_date, target_date) -> bool:
    """判断行情交易日是否晚于本次基金目标估值日。"""
    trade_key = _normalize_trade_date_key(trade_date)
    target_key = _normalize_trade_date_key(target_date)
    if not trade_key or not target_key:
        return False
    return trade_key > target_key


def _kr_zero_holiday_name(date_text) -> str:
    """返回韩国市场需要按 0% 处理的休市日名称；非目标休市日返回空字符串。"""
    date_key = _normalize_trade_date_key(date_text)
    if not date_key:
        return ""

    if date_key in KR_MARKET_ZERO_HOLIDAYS:
        return str(KR_MARKET_ZERO_HOLIDAYS[date_key])

    md_key = date_key[5:]
    return str(KR_MARKET_ZERO_HOLIDAY_MD.get(md_key, ""))


def _previous_calendar_date_key(now=None) -> str:
    """返回本地运行日期的前一自然日。用于收盘日线估值的默认目标日期。"""
    if now is None:
        now = datetime.now()
    return (now.date() - timedelta(days=1)).strftime("%Y-%m-%d")


def _apply_kr_holiday_zero_policy(return_pct, trade_date, source, now=None, target_date=None):
    """
    韩国已知休市日置零。

    关键点：这里的 target_date 是基金估算日/海外估值交易日，不一定等于代码运行日。
    例如 2026-05-06 运行程序时，美股最新交易日可能是 2026-05-05；
    若要估算 2026-05-05 的 QDII 收益，而韩国 2026-05-05 因儿童节休市，
    则无论缓存或行情源是否已经出现 2026-05-06 的韩国交易数据，
    2026-05-05 这一天的韩国持仓收益都必须按 0% 计入，避免未来交易日数据泄漏。
    """
    trade_date_norm = _normalize_trade_date_key(trade_date)
    target_date_norm = _normalize_trade_date_key(target_date) or _previous_calendar_date_key(now=now)
    holiday_name = _kr_zero_holiday_name(target_date_norm)

    if not target_date_norm or not holiday_name:
        return return_pct, trade_date, source

    # 如果目标估算日本身就是韩国休市日，直接置零。
    # 这可以同时处理两类情况：
    # 1) 行情源仍停在节前交易日；
    # 2) 第二天运行时，缓存/行情源已经更新到节后交易日。
    source_text = str(source or "")
    if f"zeroed_for_{target_date_norm}_kr_holiday" in source_text:
        return 0.0, target_date_norm, source

    suffix = f"zeroed_for_{target_date_norm}_kr_holiday"
    if trade_date_norm and trade_date_norm != target_date_norm:
        suffix = f"{trade_date_norm}_{suffix}"

    return 0.0, target_date_norm, f"{source}_{suffix}"


def _apply_stale_market_zero_policy(
    market,
    return_pct,
    source,
    trade_date,
    zero_stale_cn_hk_returns=False,
    stale_market_estimate_date=None,
    stale_market_zero_markets=("CN", "HK", "KR"),
):
    """
    节假日海外估值防重复口径。

    对 A股 / 港股 / 韩国等指定市场，如果本次拿到的行情交易日早于估算日，
    说明该市场在估算日没有产生新的交易收益。此时保留仓位，
    但将该市场持仓当日收益置为 0%，避免把节前涨跌重复计入假期收益。
    """
    market = str(market).strip().upper()
    zero_market_set = {str(x).strip().upper() for x in stale_market_zero_markets}

    # KR 日线函数已经能识别 5月5日儿童节并直接返回 0%。
    # 这里补充标记，使明细表中的“闭市置零/市场有效”字段也保持一致。
    if market == "KR" and source and "_kr_holiday" in str(source):
        return 0.0, source, True

    if not zero_stale_cn_hk_returns or market not in zero_market_set:
        return return_pct, source, False

    if return_pct is None:
        return return_pct, source, False

    estimate_date = _normalize_trade_date_key(stale_market_estimate_date) or _today_local_date_key()
    trade_date_norm = _normalize_trade_date_key(trade_date)

    if not estimate_date or not trade_date_norm:
        return return_pct, source, False

    if trade_date_norm < estimate_date:
        new_source = f"{source}_stale_{trade_date_norm}_zeroed_for_{estimate_date}"
        return 0.0, new_source, True

    return return_pct, source, False


def _safe_bool_series(series, index=None) -> pd.Series:
    """
    将可能包含 None / NaN / bool / 数值 / 字符串的 Series 安全转换为 bool。

    目的：
        避免在 object dtype 上使用 fillna(False).astype(bool) 触发
        pandas FutureWarning，同时保持闭市置零统计逻辑不变。
    """
    if series is None:
        return pd.Series(False, index=index, dtype=bool)

    if isinstance(series, pd.Series):
        s = series.copy()
        if index is not None:
            s = s.reindex(index)
    else:
        s = pd.Series(series, index=index)

    def _to_bool(value):
        if isinstance(value, bool):
            return value

        try:
            if pd.isna(value):
                return False
        except Exception:
            pass

        if isinstance(value, (int, float)):
            return bool(value)

        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "y", "是", "真", "t"}:
            return True
        if text in {"false", "0", "no", "n", "否", "假", "", "none", "nan", "null"}:
            return False

        return bool(value)

    return s.map(_to_bool).astype(bool)


def _summarize_market_effective(detail_df: pd.DataFrame) -> dict:
    """汇总每个市场本次估算是否有有效交易收益。"""
    if detail_df is None or detail_df.empty or "市场" not in detail_df.columns:
        return {}

    out = {}
    for market, g in detail_df.groupby(detail_df["市场"].astype(str)):
        market_key = str(market).strip().upper()
        if not market_key:
            continue

        if "闭市置零" in g.columns:
            zeroed_mask = _safe_bool_series(g["闭市置零"], index=g.index)
            zeroed = bool(zeroed_mask.all())
            zeroed_count = int(zeroed_mask.sum())
        else:
            zeroed = False
            zeroed_count = 0

        if "当日涨跌幅" in g.columns:
            has_return = bool(pd.to_numeric(g["当日涨跌幅"], errors="coerce").notna().any())
        else:
            has_return = False

        out[market_key] = {
            "has_return": has_return,
            "all_zeroed_as_stale": zeroed,
            "zeroed_count": zeroed_count,
        }

    return out


def _compare_trade_dates(new_trade_date, old_trade_date) -> int:
    """
    比较两个交易日。

    返回：
        1  : new > old
        0  : new == old 或无法比较
        -1 : new < old
    """
    new_dt = _parse_trade_date_value(new_trade_date)
    old_dt = _parse_trade_date_value(old_trade_date)

    if new_dt is None or old_dt is None:
        return 0

    if new_dt > old_dt:
        return 1
    if new_dt < old_dt:
        return -1
    return 0


def _last_close_cache_checked_today(item) -> bool:
    """判断某个 last_close 缓存项今天是否已经做过收盘后检查。"""
    if not isinstance(item, dict):
        return False
    return str(item.get("postclose_checked_date", "")) == _today_local_date_key()


def _should_use_trade_date_cache_without_refresh(item, max_age_hours=None) -> bool:
    """
    判断 last_close 缓存是否可以直接使用。

    策略：
    - 北京时间 07:00 前：只要缓存未超过 max_age_hours，就使用缓存；
    - 北京时间 07:00 后：必须今天已经检查过一次，才直接使用缓存；
    - 检查时不猜测美股交易日，实际是否更新由行情源返回的 trade_date 决定。
    """
    if not isinstance(item, dict):
        return False

    if not _is_cache_fresh(item.get("fetched_at"), max_age_hours=max_age_hours):
        return False

    if not _is_after_us_postclose_refresh_window():
        return True

    return _last_close_cache_checked_today(item)


def _mark_last_close_cache_checked(entry: dict) -> dict:
    """给 last_close 缓存项标记今日已做过收盘后检查。"""
    if not isinstance(entry, dict):
        entry = {}

    entry["last_refresh_attempt_at"] = datetime.now().isoformat(timespec="seconds")

    if _is_after_us_postclose_refresh_window():
        entry["postclose_checked_date"] = _today_local_date_key()

    return entry


def _cached_return_tuple(item):
    """从缓存项恢复 get_stock_return_pct 兼容的二元返回值。"""
    return float(item["return_pct"]), item.get("source", "file_cache")


def _cached_index_tuple(item):
    """从指数缓存项恢复三元返回值。"""
    return (
        float(item["return_pct"]),
        str(item.get("trade_date", "")),
        item.get("source", "file_cache"),
    )

def _df_to_cache_json(df: pd.DataFrame) -> str:
    """
    DataFrame 序列化为 JSON 字符串。
    """
    return df.to_json(
        orient="records",
        force_ascii=False,
        date_format="iso",
    )


def _df_from_cache_json(data_json: str) -> pd.DataFrame:
    """
    从缓存 JSON 字符串恢复 DataFrame。
    """
    return pd.read_json(StringIO(data_json), orient="records")


def _normalize_security_cache_ticker(market, ticker) -> str:
    """
    统一行情缓存中的 ticker 写法。
    """
    market = str(market).strip().upper()

    if market == "CN":
        return str(ticker).strip().zfill(6)

    if market == "HK":
        return normalize_hk_code(ticker)

    if market == "US":
        return str(ticker).strip().upper()

    if market == "KR":
        return normalize_kr_code(ticker)

    return str(ticker).strip().upper()


def _security_return_cache_bucket(market, cn_hk_hourly_cache=True) -> tuple[str, float]:
    """
    返回行情缓存时间桶和有效期。

    规则：
        CN/HK：小时级 key，适合 A股交易日盘中估算；
        US：日级 key，因为北京时间运行时美股通常已经收盘。
    """
    market = str(market).strip().upper()
    now = datetime.now()
    stage = "quality_driven"
    stage = "quality_driven"
    stage = "quality_driven"

    if cn_hk_hourly_cache and market in {"CN", "HK"}:
        return now.strftime("%Y-%m-%d-%H"), 2.0

    return now.strftime("%Y-%m-%d"), 36.0


def _security_return_cache_key(market, ticker, cn_hk_hourly_cache=True) -> tuple[str, str, float]:
    """
    生成行情缓存 key。
    """
    market = str(market).strip().upper()
    ticker_norm = _normalize_security_cache_ticker(market, ticker)
    bucket, max_age_hours = _security_return_cache_bucket(
        market=market,
        cn_hk_hourly_cache=cn_hk_hourly_cache,
    )
    return f"{market}:{ticker_norm}:{bucket}", ticker_norm, max_age_hours
# ETF 联接 / FOF / 指数基金代理映射。



# Matplotlib 中文字体。

_CHINESE_FONT_READY = False


def setup_chinese_font(force=False):
    """
    设置 Matplotlib 中文字体，避免表格图片中文乱码。

    参数
    ----
    force : bool
        False：如果已经设置过字体，则不重复设置。
        True ：强制重新扫描字体。
    """
    global _CHINESE_FONT_READY

    if _CHINESE_FONT_READY and not force:
        return

    candidate_font_paths = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\Deng.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]

    for font_path in candidate_font_paths:
        fp = Path(font_path)
        if fp.exists():
            try:
                font_manager.fontManager.addfont(str(fp))
            except Exception:
                pass

    candidate_font_names = [
        "Microsoft YaHei",
        "SimHei",
        "SimSun",
        "DengXian",
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "WenQuanYi Zen Hei",
        "PingFang SC",
        "Arial Unicode MS",
    ]

    available_font_names = {font.name for font in font_manager.fontManager.ttflist}

    chosen = None
    for name in candidate_font_names:
        if name in available_font_names:
            chosen = name
            break

    if chosen:
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = [chosen, *candidate_font_names, "DejaVu Sans"]
    else:
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = [*candidate_font_names, "DejaVu Sans"]

    plt.rcParams["axes.unicode_minus"] = False
    _CHINESE_FONT_READY = True


# 基金名称与限购信息。

_FUND_NAME_CACHE = None
_FUND_LIMIT_CACHE = {}


def get_fund_name(fund_code: str) -> str:
    """
    根据基金代码查询基金简称。

    参数
    ----
    fund_code : str
        基金代码，例如 "017437"。

    返回
    ----
    str
        基金简称；失败时返回 "基金xxxxxx"。
    """
    global _FUND_NAME_CACHE

    fund_code = str(fund_code).zfill(6)

    try:
        if _FUND_NAME_CACHE is None:
            _FUND_NAME_CACHE = ak.fund_name_em()

        name_df = _FUND_NAME_CACHE.copy()
        name_df["基金代码"] = name_df["基金代码"].astype(str).str.zfill(6)

        hit = name_df[name_df["基金代码"] == fund_code]

        if not hit.empty:
            return str(hit.iloc[0]["基金简称"])

    except Exception as e:
        print(f"[WARN] 基金名称获取失败: {fund_code}, 原因: {e}")

    return f"基金{fund_code}"


def _normalize_html_text(text):
    """
    粗略压缩 HTML 文本，方便正则匹配。
    """
    text = re.sub(r"<script.*?</script>", "", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", "")
    text = text.replace("&amp;", "&")
    text = re.sub(r"\s+", "", text)
    return text


def get_fund_purchase_limit_uncached(fund_code: str, timeout=8) -> str:
    """
    尝试获取基金当前限购金额。

    参数
    ----
    fund_code : str
        基金代码，例如 "017437"。
    timeout : int or float
        单个网页请求超时时间，单位秒。

    返回
    ----
    str
        常见返回值：
            "100元"
            "1000元"
            "1万元"
            "暂停申购"
            "限购(未识别金额)"
            "不限购/开放申购"
            "未知"

    说明
    ----
    限购信息来自公开网页文本解析。不同基金页面结构不同，结果可能为“未知”。
    """
    global _FUND_LIMIT_CACHE

    fund_code = str(fund_code).zfill(6)

    if fund_code in _FUND_LIMIT_CACHE:
        return _FUND_LIMIT_CACHE[fund_code]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://fund.eastmoney.com/{fund_code}.html",
    }

    urls = [
        f"https://fund.eastmoney.com/{fund_code}.html",
        f"https://fundf10.eastmoney.com/jbgk_{fund_code}.html",
        f"https://fundf10.eastmoney.com/jjfl_{fund_code}.html",
        f"https://fundf10.eastmoney.com/jjjz_{fund_code}.html",
    ]

    amount_patterns = [
        r"单日累计购买上限[:：]?(?:为)?(\d+(?:\.\d+)?(?:万)?元)",
        r"单日申购上限[:：]?(?:为)?(\d+(?:\.\d+)?(?:万)?元)",
        r"申购上限[:：]?(?:为)?(\d+(?:\.\d+)?(?:万)?元)",
        r"限购金额[:：]?(?:为)?(\d+(?:\.\d+)?(?:万)?元)",
        r"大额申购.*?(\d+(?:\.\d+)?(?:万)?元)",
        r"单个基金账户.*?累计.*?(\d+(?:\.\d+)?(?:万)?元)",
        r"每个基金账户.*?累计.*?(\d+(?:\.\d+)?(?:万)?元)",
    ]

    result = "未知"

    for url in urls:
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()

            if not resp.encoding:
                resp.encoding = resp.apparent_encoding or "utf-8"

            clean_text = _normalize_html_text(resp.text)

            found_amount = None
            for pattern in amount_patterns:
                m = re.search(pattern, clean_text, flags=re.S)
                if m:
                    found_amount = m.group(1)
                    break

            if found_amount:
                result = found_amount
                break

            if "暂停申购" in clean_text and "开放申购" not in clean_text:
                result = "暂停申购"
                break

            if "暂停大额申购" in clean_text or "限制大额申购" in clean_text:
                result = "限购(未识别金额)"
                break

            if "开放申购" in clean_text:
                result = "不限购/开放申购"
                break

        except Exception:
            continue

    _FUND_LIMIT_CACHE[fund_code] = result
    return result


def get_fund_purchase_limit(
    fund_code: str,
    timeout=8,
    cache_days=7,
    cache_enabled=True,
) -> str:
    """
    获取基金限购金额，带文件缓存。

    设计：
        - 默认 7 天更新一次；
        - GitHub Actions 中配合提交 cache/*.json 回仓库，可跨任务复用；
        - 如果更新失败且旧缓存存在，优先使用旧缓存。
    """
    fund_code = str(fund_code).zfill(6)

    if not cache_enabled:
        return get_fund_purchase_limit_uncached(fund_code=fund_code, timeout=timeout)

    cache = _load_json_cache(FUND_PURCHASE_LIMIT_CACHE_FILE, default={})
    item = cache.get(fund_code)

    if item and _is_cache_fresh(item.get("fetched_at"), max_age_days=cache_days):
        value = item.get("value", "未知")
        _FUND_LIMIT_CACHE[fund_code] = value
        _cache_log(f"使用限购缓存: {fund_code} -> {value}")
        return value

    old_value = item.get("value") if isinstance(item, dict) else None

    try:
        _cache_log(f"重新获取限购信息: {fund_code}")
        value = get_fund_purchase_limit_uncached(
            fund_code=fund_code,
            timeout=timeout,
        )

        # 如果本次只得到“未知”，但旧缓存有明确值，则保留旧值，避免网络异常污染缓存。
        if value == "未知" and old_value and old_value != "未知":
            print(f"[WARN] 限购新结果为未知，继续沿用旧缓存: {fund_code} -> {old_value}", flush=True)
            return old_value

        cache[fund_code] = {
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "value": value,
        }
        _save_json_cache(FUND_PURCHASE_LIMIT_CACHE_FILE, cache)

        _FUND_LIMIT_CACHE[fund_code] = value
        return value

    except Exception as e:
        if old_value:
            print(f"[WARN] 限购更新失败，使用旧缓存: {fund_code}, 原因: {e}", flush=True)
            return old_value

        print(f"[WARN] 限购获取失败且无缓存: {fund_code}, 原因: {e}", flush=True)
        return "未知"


# 字段排序、股票代码识别和通用工具。

def quarter_key(q):
    """
    把类似 '2026年1季度股票投资明细' 的字段转成 20261，方便排序。
    """
    text = str(q)

    year_match = re.search(r"(\d{4})", text)
    quarter_match = re.search(r"([1-4])\s*季度", text)

    if not year_match:
        return -1

    year = int(year_match.group(1))
    quarter = int(quarter_match.group(1)) if quarter_match else 0

    return year * 10 + quarter




def normalize_kr_code(code):
    """
    规范化韩国股票代码。

    支持：
        000660
        000660.KS
        KR000660

    返回：
        000660
    """
    text = str(code).strip().upper()
    text = text.replace(".KS", "")
    text = text.replace(".KQ", "")
    text = text.replace("KR", "")
    digits = re.sub(r"\D", "", text)
    if not digits:
        raise RuntimeError(f"无法从韩国股票代码中提取数字: {code}")
    if len(digits) > 6:
        digits = digits[-6:]
    return digits.zfill(6)


def _match_alias_in_name(name, aliases) -> bool:
    """判断股票名称是否命中别名。"""
    name_text = str(name or "").strip()
    name_upper = name_text.upper()
    for alias in aliases or []:
        alias_text = str(alias or "").strip()
        if not alias_text:
            continue
        if alias_text in name_text or alias_text.upper() in name_upper:
            return True
    return False


def _detect_known_kr_numeric_ticker(raw_code, stock_name):
    """
    识别已知韩国数字股票代码。

    重要原则：
        只在“代码 + 名称别名”同时命中时返回 KR，避免把同号 A 股误判为韩国股票。
    """
    raw = str(raw_code).strip().upper()

    # 显式韩国后缀可以直接判定。
    if re.match(r"^\d{1,6}\.(KS|KQ)$", raw):
        return "KR", normalize_kr_code(raw)

    code = ""
    if re.fullmatch(r"\d{1,6}", raw):
        code = raw.zfill(6)
    elif raw.startswith("KR") and re.fullmatch(r"KR\d{1,6}", raw):
        code = raw.replace("KR", "").zfill(6)

    if not code:
        return None

    item = KR_TICKER_MAP.get(code)
    if not item:
        return None

    if _match_alias_in_name(stock_name, item.get("aliases", [])):
        return "KR", str(item.get("ticker", code)).zfill(6)

    return None



def normalize_hk_code(code):
    """
    规范化港股代码。

    支持输入：
        700
        "700"
        "00700"
        "0700.HK"
        "00700.HK"
        "HK00700"
        "hk00700"

    返回：
        "00700"
    """
    text = str(code).strip().upper()

    text = text.replace(".HK", "")
    text = text.replace("HK", "")

    digits = re.sub(r"\D", "", text)

    if not digits:
        raise RuntimeError(f"无法从港股代码中提取数字: {code}")

    if len(digits) > 5:
        digits = digits[-5:]

    return digits.zfill(5)


def detect_market_and_ticker(raw_code, stock_name):
    """
    识别股票市场和行情代码。

    返回
    ----
    tuple[str, str]
        market, ticker

    market 取值
    ----------
    US:
        美股。使用美股行情接口获取。
    CN:
        A股。使用新浪实时行情或 A 股日线接口获取。
    HK:
        港股。使用港股行情接口获取。
    KR:
        韩国股票。使用 pykrx 日线接口获取；必要时可回退 Yahoo。
    UNKNOWN:
        未识别。

    识别原则
    --------
    1. 裸数字代码不是全球唯一标识符，例如 000660 既可能是 A 股代码，
       也可能是韩国 SK 海力士代码；
    2. 对这类冲突代码，只有“代码 + 股票名称别名”同时命中韩国映射表时，
       才判为 KR；
    3. 未命中韩国映射表时，仍保留原有 A 股六位数字识别逻辑，不影响 A 股。
    """
    raw = str(raw_code).strip()
    name = str(stock_name).strip()
    raw_upper = raw.upper()

    # 优先用“持仓名称 -> 美股 ticker”的人工映射兜底。
    # 维护原因：
    # - 一些海外基金披露的代码不是美股 ADR 代码，例如英美烟草常见披露为
    #   BATS（伦敦代码），但我们当前美股数据源可稳定读取的是 BTI；
    # - 如果先按裸 ticker 识别，BATS 会被误当成美股代码，导致后续日线取到
    #   错误或过旧数据；
    # - 所以这里让你在 `tools/configs/security_mappings.py` 里维护的
    #   `US_TICKER_MAP` 拥有更高优先级。
    for key, ticker in US_TICKER_MAP.items():
        if key in name:
            return "US", ticker

    # 1. 显式韩国后缀或已知韩国数字代码。
    # 必须放在 A 股六位数字判断之前；但裸代码必须同时匹配名称别名，避免误伤 A 股。
    kr_hit = _detect_known_kr_numeric_ticker(raw_upper, name)
    if kr_hit is not None:
        return kr_hit

    # 2. 港股代码：00700.HK / 0700.HK / HK00700
    if re.match(r"^(HK)?\d{1,5}(\.HK)?$", raw_upper) and not re.match(r"^\d{6}$", raw_upper):
        try:
            return "HK", normalize_hk_code(raw_upper)
        except Exception:
            pass

    # 3. 美股 ticker：NFLX, NVDA, AAPL, TSM, GOOGL, LITE
    if re.match(r"^[A-Z]{1,8}$", raw_upper):
        return "US", raw_upper

    # 4. 美股带后缀：NFLX.O, NVDA.O；韩国后缀已在前面处理。
    if re.match(r"^[A-Z]{1,8}\.[A-Z]+$", raw_upper) and not raw_upper.endswith(".HK"):
        return "US", raw_upper.split(".")[0]

    # 5. A股 6 位数字。
    # 注意：韩国冲突代码已在前面用“代码 + 名称”排除；未命中时继续保持原 A 股逻辑。
    if re.match(r"^\d{6}$", raw):
        return "CN", raw

    # 6. 港股 1-5 位纯数字兜底
    if re.match(r"^\d{1,5}$", raw):
        return "HK", normalize_hk_code(raw)

    # 7. 名称映射兜底：韩国股票。只有在名称强匹配时生效。
    for code, item in KR_TICKER_MAP.items():
        if _match_alias_in_name(name, item.get("aliases", [])):
            return "KR", str(item.get("ticker", code)).zfill(6)

    return "UNKNOWN", raw


def _to_float_safe(value):
    """
    安全转换数值，兼容 '1.23%'、'1,234.56'、'--' 等形式。
    """
    if value is None:
        return None

    text = str(value).strip()

    if text in {"", "-", "--", "None", "nan", "NaN"}:
        return None

    text = text.replace("%", "").replace(",", "")

    try:
        return float(text)
    except Exception:
        return None


def _pick_column(df, candidates):
    """
    从 DataFrame 中选择第一个存在的候选列。
    """
    for col in candidates:
        if col in df.columns:
            return col

    return None


def _last_us_symbol(code):
    """
    将常见美股代码格式归一成 ticker。

    例：
        105.NVDA -> NVDA
        gb_aapl  -> AAPL
        AAPL     -> AAPL
    """
    text = str(code).strip().upper()

    if "." in text:
        tail = text.split(".")[-1]
        if re.match(r"^[A-Z]{1,8}$", tail):
            return tail

    if "_" in text:
        tail = text.split("_")[-1]
        if re.match(r"^[A-Z]{1,8}$", tail):
            return tail

    return text


def _match_us_row(df, ticker):
    """
    在美股实时行情表中匹配 ticker。
    """
    ticker = str(ticker).strip().upper()

    code_cols = [
        "代码",
        "股票代码",
        "symbol",
        "Symbol",
        "SYMBOL",
        "code",
        "Code",
        "标识",
    ]

    code_col = _pick_column(df, code_cols)

    if code_col is None:
        raise RuntimeError(f"美股行情表缺少代码列，当前列={list(df.columns)}")

    tmp = df.copy()
    tmp["_ticker_norm"] = tmp[code_col].astype(str).map(_last_us_symbol)

    hit = tmp[tmp["_ticker_norm"] == ticker]

    if hit.empty:
        return None

    return hit.iloc[0]


def format_pct(value, digits=4):
    """
    百分数格式化。
    """
    if value is None or pd.isna(value):
        return "计算失败"

    return f"{float(value):+.{digits}f}%"


def _normalize_valuation_mode(valuation_mode):
    """
    统一估值口径。

    intraday:
        A股/港股尽量使用盘中实时；美股默认使用最新完整交易日日线。
    last_close:
        A股、港股、美股全部使用最新完整交易日日线。
        适合 QDII / 全球投资基金，避免把昨夜美股和今日 A/H 盘中混在一起。
    auto:
        股票持仓估算时按持仓市场自动判断：
        - 含 US 持仓：使用 last_close；
        - 不含 US 且为 CN/HK 持仓：使用 intraday。
        这样纯港股基金继续走港股实时，全球跨市场基金走统一收盘口径。
    """
    mode = str(valuation_mode or "intraday").strip().lower()
    aliases = {
        "realtime": "intraday",
        "real_time": "intraday",
        "live": "intraday",
        "t+0": "intraday",
        "close": "last_close",
        "daily": "last_close",
        "lastclose": "last_close",
        "last_close": "last_close",
        "t+1": "last_close",
        "automatic": "auto",
        "smart": "auto",
    }
    mode = aliases.get(mode, mode)
    if mode not in {"intraday", "last_close", "auto"}:
        raise ValueError("valuation_mode 只能是 'intraday'、'last_close' 或 'auto'")
    return mode


def _resolve_auto_valuation_mode_from_markets(markets):
    """
    根据持仓市场决定 auto 估值口径。

    规则：
        - 只要含 US，就使用 last_close，避免美股收盘和 A/H 盘中混算；
        - 不含 US 的 CN/HK 组合使用 intraday；
        - UNKNOWN 不改变判断，尽量由可识别市场决定。
    """
    market_set = {str(x).strip().upper() for x in markets if str(x).strip()}
    if "US" in market_set or "KR" in market_set:
        return "last_close"
    return "intraday"


def _component_market_type(component):
    """
    从代理组件 type 推断市场。
    """
    ctype = str(component.get("type", "")).strip().lower()
    if ctype in {"us_ticker", "us_stock", "us_etf"}:
        return "US"
    if ctype in {"hk_stock", "hk_etf", "hk_security"}:
        return "HK"
    if ctype in {"kr_stock", "kr_etf", "kr_security"}:
        return "KR"
    if ctype in {"cn_etf", "cn_stock", "cn_security", "cn_fund"}:
        return "CN"
    return "UNKNOWN"


def _resolve_auto_valuation_mode_from_components(components):
    """
    根据代理组件市场决定 auto 估值口径。
    """
    return _resolve_auto_valuation_mode_from_markets(
        [_component_market_type(x) for x in components]
    )

# 行情接口。

_US_SPOT_SINA_CACHE = None
_US_SPOT_EM_CACHE = None
_HK_SPOT_EM_CACHE = None


def infer_sina_cn_symbol(code):
    """
    根据 A 股 / ETF / 场内基金代码推断新浪 symbol。

    常见规则：
        5xxxxx, 6xxxxx, 688xxx -> sh
        0xxxxx, 1xxxxx, 2xxxxx, 3xxxxx -> sz
    """
    code = str(code).strip().zfill(6)

    if code.startswith(("5", "6", "9")) or code.startswith("688"):
        return "sh" + code

    if code.startswith(("0", "1", "2", "3")):
        return "sz" + code

    raise RuntimeError(f"无法识别沪深交易所前缀: {code}")


def fetch_cn_security_return_pct(code, retry=2, sleep_seconds=0.8):
    """
    获取 A股 / A股ETF / 场内基金实时涨跌幅，返回百分数。

    实时逻辑：
        最新价 / 昨收价 - 1
    """
    code = str(code).zfill(6)
    sina_symbol = infer_sina_cn_symbol(code)
    url = f"https://hq.sinajs.cn/list={sina_symbol}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Referer": "https://finance.sina.com.cn/",
    }

    last_error = None

    for i in range(max(1, retry)):
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = "gbk"

            text = resp.text.strip()
            m = re.search(r'="(.*)"', text)

            if not m:
                raise RuntimeError(f"新浪返回格式异常: {text[:100]}")

            values = m.group(1).split(",")

            if len(values) < 32:
                raise RuntimeError(f"新浪字段数量异常: len={len(values)}")

            prev_close = pd.to_numeric(values[2], errors="coerce")
            latest_price = pd.to_numeric(values[3], errors="coerce")

            if pd.isna(latest_price) or pd.isna(prev_close) or float(prev_close) == 0:
                raise RuntimeError(f"新浪价格无效: latest={latest_price}, prev={prev_close}")

            return (float(latest_price) / float(prev_close) - 1.0) * 100.0, "sina_realtime"

        except Exception as e:
            last_error = e

            if i < max(1, retry) - 1:
                time.sleep(sleep_seconds)

    raise RuntimeError(f"新浪行情失败: {code}, 原因: {last_error}")



def fetch_cn_security_return_pct_daily_with_date(code, lookback_days=90, end_date=None):
    """
    获取 A股 / A股ETF / 场内基金目标日期之前的最新完整交易日日线涨跌幅，并返回实际交易日。

    优先使用新浪日线接口：
        1. ak.stock_zh_a_daily
        2. ak.fund_etf_hist_sina

    新浪接口失败后才回落到东方财富日线接口，避免单一数据源临时断线导致
    海外锚点估算大面积 missing。

    end_date:
        目标估值日。海外/QDII 估算时传入美股基准交易日，避免北京时间早盘运行时
        误读 A 股当天盘中行或晚于目标估值日的未来交易日。

    返回：
        return_pct, trade_date, source
    """
    code = str(code).strip().zfill(6)
    start_date, end_date_api, end_date_key = _daily_fetch_window(
        lookback_days=lookback_days,
        end_date=end_date,
    )

    errors = []
    frames = []
    sina_symbol = infer_sina_cn_symbol(code)

    sina_fetchers = []
    if code.startswith(("5", "1")):
        sina_fetchers.extend([
            ("ak_fund_etf_hist_sina", lambda: ak.fund_etf_hist_sina(symbol=sina_symbol)),
            ("ak_stock_zh_a_daily_sina", lambda: ak.stock_zh_a_daily(symbol=sina_symbol, start_date=start_date, end_date=end_date_api, adjust="")),
        ])
    else:
        sina_fetchers.extend([
            ("ak_stock_zh_a_daily_sina", lambda: ak.stock_zh_a_daily(symbol=sina_symbol, start_date=start_date, end_date=end_date_api, adjust="")),
            ("ak_fund_etf_hist_sina", lambda: ak.fund_etf_hist_sina(symbol=sina_symbol)),
        ])

    for source_name, fetcher in sina_fetchers:
        try:
            df = fetcher()
            if df is not None and not df.empty:
                frames.append((df, source_name))
        except Exception as e:
            errors.append(f"{source_name}: {repr(e)}")

    try:
        df = ak.fund_etf_hist_em(symbol=code, period="daily", start_date=start_date, end_date=end_date_api, adjust="")
        if df is not None and not df.empty:
            frames.append((df, "ak_fund_etf_hist_em"))
    except Exception as e:
        errors.append(f"fund_etf_hist_em: {repr(e)}")

    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date_api, adjust="")
        if df is not None and not df.empty:
            frames.append((df, "ak_stock_zh_a_hist"))
    except Exception as e:
        errors.append(f"stock_zh_a_hist: {repr(e)}")

    if not frames:
        raise RuntimeError(f"A股/场内基金日线返回空数据: {code}; {' | '.join(errors)}")

    for raw_df, source_name in frames:
        out = raw_df.copy()
        date_col = _pick_column(out, ["日期", "date", "Date"])
        close_col = _pick_column(out, ["收盘", "close", "Close", "收盘价"])
        pct_col = _pick_column(out, ["涨跌幅", "涨幅", "pct_chg", "change_percent", "ChangePercent"])

        if date_col is not None:
            out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
            out = out.dropna(subset=[date_col]).sort_values(date_col)
            out = _drop_rows_after_target_date(out, out[date_col], end_date_key)

        if out.empty:
            continue

        if pct_col is not None:
            pct_values = pd.to_numeric(out[pct_col], errors="coerce")
            valid_idx = pct_values[pct_values.notna()].index
            if len(valid_idx) > 0:
                last_idx = valid_idx[-1]
                trade_date = _extract_trade_date_from_row(out.loc[last_idx], date_col)
                return float(pct_values.loc[last_idx]), trade_date, f"{source_name}_pct"

        if close_col is None:
            continue

        out[close_col] = pd.to_numeric(out[close_col], errors="coerce")
        out = out.dropna(subset=[close_col])

        if len(out) >= 2:
            last_close = float(out.iloc[-1][close_col])
            prev_close = float(out.iloc[-2][close_col])
            if prev_close != 0:
                trade_date = _extract_trade_date_from_row(out.iloc[-1], date_col)
                return (last_close / prev_close - 1.0) * 100.0, trade_date, f"{source_name}_close_calc"

    raise RuntimeError(f"无法解析 A股/场内基金 {code} 在目标日期 {end_date_key} 之前的日线涨跌幅; {' | '.join(errors)}")



def fetch_cn_security_return_pct_daily(code, lookback_days=90, end_date=None):
    """获取 A股 / A股ETF / 场内基金目标日期前最新完整交易日日线涨跌幅，兼容旧接口。"""
    r_pct, trade_date, source = fetch_cn_security_return_pct_daily_with_date(
        code=code,
        lookback_days=lookback_days,
        end_date=end_date,
    )
    return r_pct, source



def fetch_hk_return_pct_akshare_daily_with_date(code, lookback_days=90, end_date=None):
    """
    使用港股历史日线获取目标日期之前的最新交易日涨跌幅，并返回实际交易日。

    优先使用新浪港股日线 ak.stock_hk_daily；新浪失败后才回落到东方财富
    ak.stock_hk_hist。函数名保留 akshare_daily 是为了兼容旧调用方。

    end_date:
        目标估值日。用于避免北京时间早盘运行时把晚于海外估值日的港股行情计入。

    返回：
        return_pct, trade_date, source
    """
    hk_code = normalize_hk_code(code)
    start_date, end_date_api, end_date_key = _daily_fetch_window(
        lookback_days=lookback_days,
        end_date=end_date,
    )
    errors = []
    frames = []

    try:
        df = ak.stock_hk_daily(symbol=hk_code, adjust="")
        if df is not None and not df.empty:
            frames.append((df, "ak_stock_hk_daily_sina"))
    except Exception as e:
        errors.append(f"stock_hk_daily_sina: {repr(e)}")

    try:
        try:
            df = ak.stock_hk_hist(symbol=hk_code, period="daily", start_date=start_date, end_date=end_date_api, adjust="")
        except TypeError:
            df = ak.stock_hk_hist(symbol=hk_code, period="daily", adjust="")
        if df is not None and not df.empty:
            frames.append((df, "ak_stock_hk_hist_em"))
    except Exception as e:
        errors.append(f"stock_hk_hist_em: {repr(e)}")

    if not frames:
        raise RuntimeError(f"港股日线返回空数据: {hk_code}; {' | '.join(errors)}")

    for raw_df, source_name in frames:
        out = raw_df.copy()
        date_col = _pick_column(out, ["日期", "date", "Date"])
        close_col = _pick_column(out, ["收盘", "close", "Close", "收盘价"])
        pct_col = _pick_column(out, ["涨跌幅", "涨幅", "pct_chg", "change_percent", "ChangePercent"])

        if date_col is not None:
            out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
            out = out.dropna(subset=[date_col]).sort_values(date_col)
            out = _drop_rows_after_target_date(out, out[date_col], end_date_key)

        if out.empty:
            continue

        if pct_col is not None:
            pct_values = pd.to_numeric(out[pct_col], errors="coerce")
            valid_idx = pct_values[pct_values.notna()].index
            if len(valid_idx) > 0:
                last_idx = valid_idx[-1]
                trade_date = _extract_trade_date_from_row(out.loc[last_idx], date_col)
                return float(pct_values.loc[last_idx]), trade_date, f"{source_name}_pct"

        if close_col is None:
            continue

        out[close_col] = pd.to_numeric(out[close_col], errors="coerce")
        out = out.dropna(subset=[close_col])

        if len(out) >= 2:
            last_close = float(out.iloc[-1][close_col])
            prev_close = float(out.iloc[-2][close_col])
            if prev_close != 0:
                trade_date = _extract_trade_date_from_row(out.iloc[-1], date_col)
                return (last_close / prev_close - 1.0) * 100.0, trade_date, f"{source_name}_close_calc"

    raise RuntimeError(f"无法解析港股 {hk_code} 在目标日期 {end_date_key} 之前的日线涨跌幅; {' | '.join(errors)}")



def fetch_hk_return_pct_akshare_daily(code, lookback_days=90, end_date=None):
    """使用 AKShare 港股历史日线获取目标日期前最新交易日涨跌幅，兼容旧接口。"""
    r_pct, trade_date, source = fetch_hk_return_pct_akshare_daily_with_date(
        code=code,
        lookback_days=lookback_days,
        end_date=end_date,
    )
    return r_pct, source


def _normalize_hk_symbol_for_match(value):
    """
    统一港股行情表中的代码格式，用于匹配。

    支持：
        700
        00700
        HK00700
        00700.HK

    返回：
        00700
    """
    text = str(value).strip().upper()
    text = text.replace(".HK", "")
    text = text.replace("HK", "")

    digits = re.sub(r"\D", "", text)

    if not digits:
        return ""

    if len(digits) > 5:
        digits = digits[-5:]

    return digits.zfill(5)


def _match_hk_row(df, hk_code):
    """
    在 AKShare 港股实时行情表中匹配指定港股代码。
    """
    hk_code = normalize_hk_code(hk_code)

    code_cols = [
        "代码",
        "股票代码",
        "symbol",
        "Symbol",
        "SYMBOL",
        "code",
        "Code",
    ]

    code_col = _pick_column(df, code_cols)

    if code_col is None:
        raise RuntimeError(f"港股实时行情表缺少代码列，当前列={list(df.columns)}")

    tmp = df.copy()
    tmp["_hk_code_norm"] = tmp[code_col].astype(str).map(_normalize_hk_symbol_for_match)

    hit = tmp[tmp["_hk_code_norm"] == hk_code]

    if hit.empty:
        return None

    return hit.iloc[0]


def fetch_hk_return_pct_akshare_spot_em(code):
    """
    使用 AKShare 东方财富港股实时行情获取当日涨跌幅。

    逻辑：
        1. 通过 ak.stock_hk_spot_em() 拉取港股实时行情表；
        2. 按港股代码匹配；
        3. 优先读取“涨跌幅”列；
        4. 如果没有“涨跌幅”列，则用 最新价 / 昨收价 - 1 计算。

    返回：
        return_pct, source
    """
    global _HK_SPOT_EM_CACHE

    hk_code = normalize_hk_code(code)

    if _HK_SPOT_EM_CACHE is None:
        _HK_SPOT_EM_CACHE = ak.stock_hk_spot_em()

    df = _HK_SPOT_EM_CACHE

    if df is None or df.empty:
        raise RuntimeError("ak.stock_hk_spot_em 返回空数据")

    row = _match_hk_row(df, hk_code)

    if row is None:
        raise RuntimeError(f"stock_hk_spot_em 未找到港股 {hk_code}; 当前列={list(df.columns)}")

    pct_col = _pick_column(
        df,
        [
            "涨跌幅",
            "涨幅",
            "changePercent",
            "ChangePercent",
            "pct_chg",
            "change_percent",
            "涨跌幅%",
        ],
    )

    if pct_col is not None:
        pct = _to_float_safe(row.get(pct_col))
        if pct is not None:
            return float(pct), "ak_stock_hk_spot_em"

    price_col = _pick_column(
        df,
        [
            "最新价",
            "最新",
            "现价",
            "price",
            "Price",
            "last",
            "Last",
            "收盘价",
        ],
    )

    prev_col = _pick_column(
        df,
        [
            "昨收价",
            "昨收",
            "previousClose",
            "PreviousClose",
            "prev_close",
            "昨收盘",
        ],
    )

    if price_col is not None and prev_col is not None:
        price = _to_float_safe(row.get(price_col))
        prev = _to_float_safe(row.get(prev_col))

        if price is not None and prev not in (None, 0):
            return (float(price) / float(prev) - 1.0) * 100.0, "ak_stock_hk_spot_em_calc"

    raise RuntimeError(
        f"stock_hk_spot_em 无法解析港股 {hk_code} 涨跌幅；当前列={list(df.columns)}"
    )


def fetch_hk_return_pct_sina(code, retry=2, sleep_seconds=0.8):
    """
    使用新浪港股单只股票实时行情获取涨跌幅。

    安全逻辑：
        - 不使用字段猜测；
        - 只使用明确价格字段计算：最新价 / 昨收价 - 1；
        - 如果 latest / prev_close 不能可靠解析，直接失败；
        - 上游 fetch_hk_return_pct() 再尝试东方财富实时与港股日线兜底。
    """
    hk_code = normalize_hk_code(code)
    sina_symbol = "hk" + hk_code
    url = f"https://hq.sinajs.cn/list={sina_symbol}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Referer": "https://finance.sina.com.cn/",
    }

    last_error = None

    for i in range(max(1, retry)):
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = "gbk"

            text = resp.text.strip()
            m = re.search(r'="(.*)"', text)

            if not m:
                raise RuntimeError(f"新浪港股返回格式异常: {text[:120]}")

            raw = m.group(1)

            if not raw:
                raise RuntimeError(f"新浪港股返回空内容: {hk_code}")

            values = raw.split(",")

            if len(values) < 8:
                raise RuntimeError(f"新浪港股字段数量不足: len={len(values)}, raw={values[:12]}")

            # 新浪港股常见字段顺序：
            # 0 名称；1 今日开盘价；2 昨日收盘价；3 最高价；4 最低价；5 当前价 / 最新价。
            prev_close = _to_float_safe(values[2])
            latest_price = _to_float_safe(values[5])

            if latest_price is None or prev_close is None:
                raise RuntimeError(
                    f"新浪港股价格字段解析失败: {hk_code}, "
                    f"prev_close_raw={values[2] if len(values) > 2 else None}, "
                    f"latest_raw={values[5] if len(values) > 5 else None}, "
                    f"raw_head={values[:12]}"
                )

            if float(prev_close) <= 0 or float(latest_price) <= 0:
                raise RuntimeError(
                    f"新浪港股价格字段无效: {hk_code}, "
                    f"latest={latest_price}, prev_close={prev_close}, raw_head={values[:12]}"
                )

            return_pct = (float(latest_price) / float(prev_close) - 1.0) * 100.0

            if abs(return_pct) > 40:
                raise RuntimeError(
                    f"新浪港股计算涨跌幅异常: {hk_code}, "
                    f"return_pct={return_pct:.4f}%, "
                    f"latest={latest_price}, prev_close={prev_close}, raw_head={values[:12]}"
                )

            return return_pct, "sina_hk_realtime_price_calc"

        except Exception as e:
            last_error = e
            if i < max(1, retry) - 1:
                time.sleep(sleep_seconds)

    raise RuntimeError(f"新浪港股行情失败: {hk_code}, 原因: {last_error}")


def fetch_hk_return_pct(code, hk_realtime=False):
    """
    获取港股涨跌幅，返回百分数。

    hk_realtime=True：
        新浪港股实时安全解析 -> 新浪港股日线 -> 东方财富港股实时兜底 -> 东方财富港股日线兜底。
    hk_realtime=False：
        使用港股历史日线，优先新浪，东方财富仅作兜底。
    """
    hk_code = normalize_hk_code(code)
    errors = []

    if hk_realtime:
        try:
            return fetch_hk_return_pct_sina(hk_code)
        except Exception as e:
            errors.append(f"sina_hk_price_calc: {repr(e)}")

        try:
            return fetch_hk_return_pct_akshare_daily(hk_code)
        except Exception as e:
            errors.append(f"ak_hk_daily: {repr(e)}")

        try:
            return fetch_hk_return_pct_akshare_spot_em(hk_code)
        except Exception as e:
            errors.append(f"ak_hk_spot_em: {repr(e)}")

    else:
        try:
            return fetch_hk_return_pct_akshare_daily(hk_code)
        except Exception as e:
            errors.append(f"ak_hk_daily: {repr(e)}")

    raise RuntimeError(f"无法获取港股 {hk_code} 涨跌幅: {' | '.join(errors)}")



def fetch_hk_return_pct_last_close_with_fallback_with_date(code, end_date=None):
    """港股统一收盘口径的安全获取，并返回实际交易日。"""
    hk_code = normalize_hk_code(code)
    try:
        return fetch_hk_return_pct_akshare_daily_with_date(hk_code, end_date=end_date)
    except Exception as e_daily:
        target_key = _normalize_trade_date_key(end_date)
        if target_key and target_key < _today_local_date_key():
            raise RuntimeError(
                f"港股 {hk_code} 目标估值日 {target_key} 的日线获取失败，"
                f"为避免未来交易日泄漏，不使用实时行情兜底: daily={e_daily}"
            )
        try:
            r_pct, source = fetch_hk_return_pct(hk_code, hk_realtime=True)
            return r_pct, "", f"{source}_fallback_intraday_after_daily_fail"
        except Exception as e_rt:
            raise RuntimeError(f"港股 {hk_code} 日线和实时兜底均失败: daily={e_daily}; realtime={e_rt}")



def fetch_hk_return_pct_last_close_with_fallback(code, end_date=None):
    """港股统一收盘口径的安全获取，兼容旧接口。"""
    r_pct, trade_date, source = fetch_hk_return_pct_last_close_with_fallback_with_date(
        code,
        end_date=end_date,
    )
    return r_pct, source


def fetch_us_return_pct_akshare_daily_with_date(ticker, end_date=None):
    """
    使用 AKShare 美股历史日线获取目标日期之前的最新交易日涨跌幅，并返回实际交易日。

    逻辑：
        最新交易日收盘价 / 前一交易日收盘价 - 1

    返回：
        return_pct, trade_date, source
    """
    ticker = str(ticker).strip().upper()

    df = ak.stock_us_daily(symbol=ticker, adjust="")

    if df is None or df.empty:
        raise RuntimeError(f"stock_us_daily 返回空数据: {ticker}")

    out = df.copy()
    end_date_key = _normalize_trade_date_key(end_date)

    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        out = out.dropna(subset=["date"])
        out = _drop_rows_after_target_date(out, out["date"], end_date_key)

    if "close" not in out.columns:
        raise RuntimeError(f"stock_us_daily 缺少 close 列: {ticker}; 当前列={list(out.columns)}")

    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["close"])

    if "date" in out.columns:
        out = out.sort_values("date")

    if len(out) < 2:
        raise RuntimeError(f"stock_us_daily 在目标日期 {end_date_key or 'latest'} 前有效 close 数量不足: {ticker}")

    last_close = float(out.iloc[-1]["close"])
    prev_close = float(out.iloc[-2]["close"])

    if prev_close == 0:
        raise RuntimeError(f"stock_us_daily 前一交易日收盘价为 0: {ticker}")

    if "date" in out.columns:
        trade_date = pd.Timestamp(out.iloc[-1]["date"]).strftime("%Y-%m-%d")
    else:
        trade_date = ""

    return (last_close / prev_close - 1.0) * 100.0, trade_date, "ak_stock_us_daily"


def fetch_us_stock_return_pct_eastmoney_daily_with_date(ticker, end_date=None, lookback_days=80):
    """
    使用东方财富美股历史日线作为第二顺位兜底，并返回实际交易日。

    维护说明：
    - 第一顺位仍是上面的新浪日线 `ak.stock_us_daily()`；
    - 这个函数只在新浪日线缺失、过旧或报错时才会被调用；
    - 只请求单只股票的 K 线，不拉全市场列表，尽量减少接口压力；
    - 美股在东方财富里有不同市场前缀，常见为 105/106/107，所以逐个尝试。
    """
    symbol = str(ticker).strip().upper()
    end_date_key = _normalize_trade_date_key(end_date)
    end_api = (end_date_key or datetime.now().strftime("%Y-%m-%d")).replace("-", "")
    url = "https://63.push2his.eastmoney.com/api/qt/stock/kline/get"
    params_base = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "end": end_api,
        "lmt": str(int(lookback_days)),
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://quote.eastmoney.com/us/{symbol}.html",
    }

    errors = []
    for market_prefix in ("105", "106", "107"):
        secid = f"{market_prefix}.{symbol}"
        try:
            params = dict(params_base)
            params["secid"] = secid
            resp = requests.get(url, params=params, headers=headers, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            klines = (data.get("data") or {}).get("klines") or []
            if not klines:
                errors.append(f"{secid}: empty klines")
                continue

            rows = []
            for item in klines:
                parts = str(item).split(",")
                if len(parts) < 3:
                    continue
                rows.append({"date": parts[0], "close": parts[2]})

            out = pd.DataFrame(rows)
            if out.empty:
                errors.append(f"{secid}: parsed empty")
                continue
            out["date"] = pd.to_datetime(out["date"], errors="coerce")
            out["close"] = pd.to_numeric(out["close"], errors="coerce")
            out = out.dropna(subset=["date", "close"]).sort_values("date")
            out = _drop_rows_after_target_date(out, out["date"], end_date_key)

            if len(out) < 2:
                errors.append(f"{secid}: valid close count < 2")
                continue

            last_close = float(out.iloc[-1]["close"])
            prev_close = float(out.iloc[-2]["close"])
            if prev_close == 0:
                errors.append(f"{secid}: prev_close is 0")
                continue

            trade_date = pd.Timestamp(out.iloc[-1]["date"]).strftime("%Y-%m-%d")
            return (last_close / prev_close - 1.0) * 100.0, trade_date, f"eastmoney_us_hist_daily_{market_prefix}"
        except Exception as exc:
            errors.append(f"{secid}: {repr(exc)}")

    raise RuntimeError(f"东方财富美股日线获取失败: {symbol}: {' | '.join(errors)}")


def fetch_us_stock_return_pct_yahoo_daily_with_date(ticker, end_date=None, lookback_days=20, retry=2, sleep_seconds=0.8):
    """
    使用 Yahoo Finance Chart 作为美股个股完整日线兜底。

    维护说明：
    - 主数据源仍然是 `ak.stock_us_daily()`，因为它和项目原有逻辑最一致；
    - 但 AKShare 单只美股日线偶尔会比目标估值日慢一天，例如目标是
      2026-05-07，接口只返回到 2026-05-06；
    - 这种情况下如果 Yahoo 已经有完整日线，就用 Yahoo 兜底，避免把
      正常美股持仓误记为 stale；
    - 这里仍然只取日线收盘价，不使用盘中行情，符合海外基金估值锚点口径。
    """
    symbol = str(ticker).strip().upper()
    encoded_symbol = requests.utils.quote(symbol, safe="=")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}"
    params = {
        "range": f"{int(lookback_days)}d",
        "interval": "1d",
        "includePrePost": "false",
        "events": "history",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Referer": "https://finance.yahoo.com/",
    }

    end_date_key = _normalize_trade_date_key(end_date)
    last_error = None

    for i in range(max(1, retry)):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=12)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("chart", {}).get("result", [None])[0]
            if not result:
                raise RuntimeError(f"Yahoo 返回结构异常: {symbol}, data={data}")

            timestamps = result.get("timestamp") or []
            quote = (result.get("indicators", {}).get("quote") or [{}])[0]
            closes = quote.get("close") or []

            rows = []
            for ts, close in zip(timestamps, closes):
                if close is None:
                    continue
                try:
                    close_f = float(close)
                except Exception:
                    continue
                if close_f <= 0:
                    continue
                rows.append({
                    "date": datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d"),
                    "close": close_f,
                })

            out = pd.DataFrame(rows).dropna(subset=["close"]).sort_values("date")
            if end_date_key and not out.empty:
                out = _drop_rows_after_target_date(out, out["date"], end_date_key)
            if len(out) < 2:
                raise RuntimeError(f"Yahoo 日线在目标日期 {end_date_key or 'latest'} 前有效数据不足: {symbol}")

            last_close = float(out.iloc[-1]["close"])
            prev_close = float(out.iloc[-2]["close"])
            if prev_close == 0:
                raise RuntimeError(f"Yahoo 前一交易日收盘价为 0: {symbol}")

            trade_date = str(out.iloc[-1]["date"])
            return (last_close / prev_close - 1.0) * 100.0, trade_date, "yahoo_chart_daily_us_fallback"

        except Exception as exc:
            last_error = exc
            if i < max(1, retry) - 1:
                time.sleep(sleep_seconds)

    raise RuntimeError(f"Yahoo 美股日线获取失败: {symbol}: {last_error}")


def fetch_us_return_pct_daily_with_date(ticker, end_date=None):
    """
    获取美股个股目标估值日完整日线涨跌幅。

    主流程：
    1. 先走 AKShare 的新浪美股日线封装 `stock_us_daily()`；
    2. 如果新浪返回的实际交易日早于目标估值日，再尝试东方财富单只美股日线；
    3. 最后尝试 Yahoo Chart 兜底；
    4. 如果都没拿到目标日，但某个源返回了旧交易日，保留最接近目标日的结果，
       交给上层标记为 stale，不中断整套程序。

    注意：
    - 这里不使用盘中行情，继续保持海外基金“完整日线锚点”口径。
    - 雪球、腾讯等接口暂不硬接；如果后续确认稳定、免登录、字段明确，可以继续
      加在东方财富和 Yahoo 之间。
    """
    end_date_key = _normalize_trade_date_key(end_date)
    best_stale_result = None
    errors = []

    def _try_source(source_name, fetcher):
        nonlocal best_stale_result
        try:
            result = fetcher()
            _, trade_date, _ = result
            trade_date_key = _normalize_trade_date_key(trade_date)
            if not end_date_key or trade_date_key == end_date_key:
                return result

            errors.append(f"{source_name} trade_date={trade_date_key} != target={end_date_key}")
            if trade_date_key and (
                best_stale_result is None
                or trade_date_key > _normalize_trade_date_key(best_stale_result[1])
            ):
                best_stale_result = result
        except Exception as exc:
            errors.append(f"{source_name}: {repr(exc)}")
        return None

    for source_name, fetcher in (
        ("ak_stock_us_daily_sina", lambda: fetch_us_return_pct_akshare_daily_with_date(ticker, end_date=end_date_key)),
        ("eastmoney_us_hist_daily", lambda: fetch_us_stock_return_pct_eastmoney_daily_with_date(ticker, end_date=end_date_key)),
        ("yahoo_chart_daily_us_fallback", lambda: fetch_us_stock_return_pct_yahoo_daily_with_date(ticker, end_date=end_date_key)),
    ):
        result = _try_source(source_name, fetcher)
        if result is not None:
            return result

    if best_stale_result is not None:
        return best_stale_result

    raise RuntimeError(f"美股 {ticker} 日线获取失败: {' | '.join(errors)}")


def fetch_us_return_pct_akshare_daily(ticker):
    """
    使用 AKShare 美股历史日线获取最新交易日涨跌幅，返回百分数。

    保持旧接口兼容：
        return_pct, source
    """
    r_pct, trade_date, source = fetch_us_return_pct_akshare_daily_with_date(ticker)
    return r_pct, source


def fetch_us_return_pct_akshare_spot_sina(ticker):
    """
    使用 AKShare 新浪美股实时行情获取涨跌幅。

    注意：
        ak.stock_us_spot() 会拉较大的美股列表，速度可能较慢。
    """
    global _US_SPOT_SINA_CACHE

    ticker = str(ticker).strip().upper()

    if _US_SPOT_SINA_CACHE is None:
        _US_SPOT_SINA_CACHE = ak.stock_us_spot()

    df = _US_SPOT_SINA_CACHE

    if df is None or df.empty:
        raise RuntimeError("ak.stock_us_spot 返回空数据")

    row = _match_us_row(df, ticker)

    if row is None:
        raise RuntimeError(f"stock_us_spot 未找到 {ticker}; 当前列={list(df.columns)}")

    pct_col = _pick_column(
        df,
        ["涨跌幅", "涨幅", "changePercent", "ChangePercent", "pct_chg", "change_percent", "涨跌幅%"],
    )

    if pct_col is not None:
        pct = _to_float_safe(row.get(pct_col))
        if pct is not None:
            return pct, "ak_stock_us_spot_sina"

    price_col = _pick_column(df, ["最新价", "最新", "现价", "price", "Price", "last", "Last", "收盘价"])
    prev_col = _pick_column(df, ["昨收价", "昨收", "previousClose", "PreviousClose", "prev_close", "昨收盘"])

    if price_col is not None and prev_col is not None:
        price = _to_float_safe(row.get(price_col))
        prev = _to_float_safe(row.get(prev_col))

        if price is not None and prev not in (None, 0):
            return (price / prev - 1.0) * 100.0, "ak_stock_us_spot_sina_calc"

    raise RuntimeError(f"stock_us_spot 无法解析 {ticker} 涨跌幅；当前列={list(df.columns)}")


def fetch_us_return_pct_akshare_spot_em(ticker):
    """
    使用 AKShare 东方财富美股实时行情获取涨跌幅。

    维护说明：
    - 这是旧实时模式的备用函数，不参与海外基金“完整日线锚点”估算；
    - 普通海外基金估算会走 `fetch_us_return_pct_daily_with_date()`，不会用这里的盘中数；
    - 如果你只想控制完整日线兜底顺序，优先维护上面的日线函数。
    """
    global _US_SPOT_EM_CACHE

    ticker = str(ticker).strip().upper()

    if _US_SPOT_EM_CACHE is None:
        _US_SPOT_EM_CACHE = ak.stock_us_spot_em()

    df = _US_SPOT_EM_CACHE

    if df is None or df.empty:
        raise RuntimeError("ak.stock_us_spot_em 返回空数据")

    row = _match_us_row(df, ticker)

    if row is None:
        raise RuntimeError(f"stock_us_spot_em 未找到 {ticker}; 当前列={list(df.columns)}")

    pct_col = _pick_column(
        df,
        ["涨跌幅", "涨幅", "changePercent", "ChangePercent", "pct_chg", "change_percent", "涨跌幅%"],
    )

    if pct_col is not None:
        pct = _to_float_safe(row.get(pct_col))
        if pct is not None:
            return pct, "ak_stock_us_spot_em"

    price_col = _pick_column(df, ["最新价", "最新", "现价", "price", "Price", "last", "Last"])
    prev_col = _pick_column(df, ["昨收价", "昨收", "previousClose", "PreviousClose", "prev_close", "昨收盘"])

    if price_col is not None and prev_col is not None:
        price = _to_float_safe(row.get(price_col))
        prev = _to_float_safe(row.get(prev_col))

        if price is not None and prev not in (None, 0):
            return (price / prev - 1.0) * 100.0, "ak_stock_us_spot_em_calc"

    raise RuntimeError(f"stock_us_spot_em 无法解析 {ticker} 涨跌幅；当前列={list(df.columns)}")


def fetch_us_return_pct(
    ticker,
    prefer_intraday=True,
    us_realtime=False,
):
    """
    获取美股 ticker 最新交易日涨跌幅，返回百分数。

    us_realtime=False：
        默认快速模式，优先使用 ak.stock_us_daily() 获取单只股票日线。
        适合收盘后估算，速度较快。

    us_realtime=True：
        盘中实时模式，优先尝试 AKShare 新浪美股实时行情；
        再尝试 AKShare 东方财富美股实时行情；
        最后回落到 ak.stock_us_daily()。

    prefer_intraday:
        兼容旧调用保留，当前流程不依赖该参数。
    """
    ticker = str(ticker).strip().upper()
    errors = []

    if us_realtime:
        try:
            return fetch_us_return_pct_akshare_spot_sina(ticker)
        except Exception as e:
            errors.append(f"ak_spot_sina: {repr(e)}")

        try:
            return fetch_us_return_pct_akshare_spot_em(ticker)
        except Exception as e:
            errors.append(f"ak_spot_em: {repr(e)}")

        try:
            return fetch_us_return_pct_akshare_daily(ticker)
        except Exception as e:
            errors.append(f"ak_daily: {repr(e)}")

    else:
        try:
            return fetch_us_return_pct_akshare_daily(ticker)
        except Exception as e:
            errors.append(f"ak_daily: {repr(e)}")

        try:
            return fetch_us_stock_return_pct_eastmoney_daily_with_date(ticker)
        except Exception as e:
            errors.append(f"eastmoney_daily: {repr(e)}")

        try:
            r_pct, _trade_date, source = fetch_us_stock_return_pct_yahoo_daily_with_date(ticker)
            return r_pct, source
        except Exception as e:
            errors.append(f"yahoo_daily: {repr(e)}")

    raise RuntimeError(f"无法获取美股 {ticker} 涨跌幅: {' | '.join(errors)}")




def fetch_kr_return_pct_pykrx_daily_with_date(code, lookback_days=30, end_date=None):
    """
    使用 pykrx 获取韩国股票目标日期之前的最新完整交易日日线涨跌幅，并返回实际交易日。
    """
    kr_code = normalize_kr_code(code)

    try:
        from pykrx import stock as krx_stock
    except Exception as e:
        raise RuntimeError(f"pykrx 未安装或导入失败，请先 pip install pykrx: {e}")

    start_date, end_date_api, end_date_key = _daily_fetch_window(
        lookback_days=lookback_days,
        end_date=end_date,
    )

    df = krx_stock.get_market_ohlcv_by_date(start_date, end_date_api, kr_code)
    if df is None or df.empty:
        raise RuntimeError(f"pykrx 返回空数据: {kr_code}")

    out = df.copy().sort_index()
    out = _drop_rows_after_target_date(out, out.index, end_date_key)
    if out.empty:
        raise RuntimeError(f"pykrx 在目标日期 {end_date_key} 之前无有效数据: {kr_code}")

    pct_col = _pick_column(out, ["涨跌率", "등락률", "涨跌幅", "change_percent", "ChangePercent"])
    if pct_col is not None:
        pct_values = pd.to_numeric(out[pct_col], errors="coerce")
        valid_idx = pct_values[pct_values.notna()].index
        if len(valid_idx) > 0:
            last_idx = valid_idx[-1]
            trade_date = _normalize_trade_date_key(last_idx)
            return float(pct_values.loc[last_idx]), trade_date, "pykrx_ohlcv_pct"

    close_col = _pick_column(out, ["收盘价", "종가", "close", "Close"])
    if close_col is None:
        raise RuntimeError(f"pykrx 缺少收盘价列: {kr_code}; 当前列={list(out.columns)}")

    close = pd.to_numeric(out[close_col], errors="coerce").dropna()
    if len(close) < 2:
        raise RuntimeError(f"pykrx 有效收盘价不足: {kr_code}")

    last_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    if prev_close == 0:
        raise RuntimeError(f"pykrx 前一交易日收盘价为 0: {kr_code}")

    trade_date = _normalize_trade_date_key(close.index[-1])
    return (last_close / prev_close - 1.0) * 100.0, trade_date, "pykrx_ohlcv_close_calc"



def fetch_kr_return_pct_yahoo_daily_with_date(code, lookback_days=15, end_date=None):
    """
    使用 Yahoo Finance 作为韩国股票日线兜底接口。
    """
    kr_code = normalize_kr_code(code)
    symbol = f"{kr_code}.KS"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": f"{int(lookback_days)}d", "interval": "1d", "events": "history"}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        )
    }

    resp = requests.get(url, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    result = data.get("chart", {}).get("result", [None])[0]
    if not result:
        raise RuntimeError(f"Yahoo 返回结构异常: {symbol}, data={data}")

    timestamps = result.get("timestamp", [])
    quote = result.get("indicators", {}).get("quote", [{}])[0]
    closes = quote.get("close", [])

    rows = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        rows.append({
            "date": datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d"),
            "close": float(close),
        })

    out = pd.DataFrame(rows).dropna(subset=["close"]).sort_values("date")
    end_date_key = _normalize_trade_date_key(end_date)
    if end_date_key and not out.empty:
        out = _drop_rows_after_target_date(out, out["date"], end_date_key)
    if len(out) < 2:
        raise RuntimeError(f"Yahoo 日线在目标日期 {end_date_key or 'latest'} 前有效数据不足: {symbol}")

    last_close = float(out.iloc[-1]["close"])
    prev_close = float(out.iloc[-2]["close"])
    if prev_close == 0:
        raise RuntimeError(f"Yahoo 前一交易日收盘价为 0: {symbol}")

    trade_date = str(out.iloc[-1]["date"])
    return (last_close / prev_close - 1.0) * 100.0, trade_date, "yahoo_chart_daily_kr_fallback"



def fetch_kr_return_pct_daily_with_date(code, target_date=None):
    """
    获取韩国股票目标估值日之前的最新完整交易日日线涨跌幅。
    """
    kr_code = normalize_kr_code(code)
    errors = []

    try:
        r_pct, trade_date, source = fetch_kr_return_pct_pykrx_daily_with_date(
            kr_code,
            end_date=target_date,
        )
        return _apply_kr_holiday_zero_policy(r_pct, trade_date, source, target_date=target_date)
    except Exception as e:
        errors.append(f"pykrx: {repr(e)}")

    try:
        r_pct, trade_date, source = fetch_kr_return_pct_yahoo_daily_with_date(
            kr_code,
            end_date=target_date,
        )
        return _apply_kr_holiday_zero_policy(r_pct, trade_date, source, target_date=target_date)
    except Exception as e:
        errors.append(f"yahoo: {repr(e)}")

    raise RuntimeError(f"韩国股票行情获取失败: {kr_code}; {' | '.join(errors)}")



def fetch_kr_return_pct_daily(code, target_date=None):
    """获取韩国股票目标估值日之前最新完整交易日日线涨跌幅，兼容旧二元返回接口。"""
    r_pct, trade_date, source = fetch_kr_return_pct_daily_with_date(code, target_date=target_date)
    return r_pct, source


def get_stock_return_pct(
    market,
    ticker,
    manual_returns_pct=None,
    prefer_intraday=True,
    us_realtime=False,
    hk_realtime=False,
    security_return_cache_enabled=True,
    cn_hk_hourly_cache=True,
    valuation_mode="intraday",
    return_trade_date=False,
    stale_market_estimate_date=None,
):
    """
    根据 market 自动选择行情接口，并对行情涨跌幅做缓存。

    return_trade_date=False 时兼容旧接口，返回 (return_pct, source)；
    return_trade_date=True 时返回 (return_pct, source, trade_date)。
    last_close 口径下 US/CN/HK/KR 都缓存 trade_date。
    stale_market_estimate_date 用于 QDII/海外基金按指定估值日处理跨市场休市。
    """
    market = str(market).strip().upper()
    valuation_mode = _normalize_valuation_mode(valuation_mode)
    key = str(ticker).strip().upper()

    def _return_result(r_pct, source, trade_date=""):
        if return_trade_date:
            return float(r_pct), source, _normalize_trade_date_key(trade_date)
        return float(r_pct), source

    if manual_returns_pct:
        if key in manual_returns_pct:
            return _return_result(manual_returns_pct[key], "manual", _today_local_date_key())
        if str(ticker).strip() in manual_returns_pct:
            return _return_result(manual_returns_pct[str(ticker).strip()], "manual", _today_local_date_key())

    cache_key = None
    ticker_norm = None
    max_age_hours = None
    cache = None
    item = None
    # 韩国股票即使在 intraday 口径下也使用最新完整日线；
    # 因此 KR 也必须缓存/返回 trade_date，才能在 5月5日儿童节等休市日正确置零。
    needs_trade_date_cache = (valuation_mode == "last_close") or (market == "KR")

    if security_return_cache_enabled:
        effective_cn_hk_hourly_cache = cn_hk_hourly_cache and valuation_mode == "intraday"
        cache_key, ticker_norm, max_age_hours = _security_return_cache_key(market=market, ticker=ticker, cn_hk_hourly_cache=effective_cn_hk_hourly_cache)
        cache_key = f"{cache_key}:{valuation_mode}"

        if cache_key in _SECURITY_RETURN_RUNTIME_CACHE and not needs_trade_date_cache:
            _cache_log(f"使用本轮内存行情缓存: {cache_key}")
            result = _SECURITY_RETURN_RUNTIME_CACHE[cache_key]
            if return_trade_date:
                return float(result[0]), result[1], ""
            return result

        cache = _load_json_cache(SECURITY_RETURN_CACHE_FILE, default={})
        item = cache.get(cache_key)
        if item:
            try:
                if needs_trade_date_cache:
                    # 节假日防重复估值依赖 trade_date，缺失时必须刷新一次。
                    if not item.get("trade_date") and market in {"CN", "HK", "KR"}:
                        pass
                    elif _should_use_trade_date_cache_without_refresh(item, max_age_hours=max_age_hours):
                        result = _cached_return_tuple(item)
                        cached_trade_date = _normalize_trade_date_key(item.get("trade_date", ""))
                        if market in {"CN", "HK", "KR"} and _trade_date_is_after_target(cached_trade_date, stale_market_estimate_date):
                            _cache_log(
                                f"跳过晚于目标估值日的行情缓存: {cache_key}, "
                                f"cached_trade_date={cached_trade_date}, target={_normalize_trade_date_key(stale_market_estimate_date)}"
                            )
                        else:
                            if market == "KR":
                                zero_r_pct, zero_trade_date, zero_source = _apply_kr_holiday_zero_policy(
                                    result[0],
                                    cached_trade_date,
                                    result[1],
                                    target_date=stale_market_estimate_date,
                                )
                                result = (float(zero_r_pct), zero_source)
                                cached_trade_date = _normalize_trade_date_key(zero_trade_date)
                            _SECURITY_RETURN_RUNTIME_CACHE[cache_key] = result
                            _cache_log(f"使用文件行情缓存: {cache_key} -> {result[0]:+.4f}% trade_date={cached_trade_date}")
                            if return_trade_date:
                                return result[0], result[1], cached_trade_date
                            return result
                else:
                    if _is_cache_fresh(item.get("fetched_at"), max_age_hours=max_age_hours):
                        result = (float(item["return_pct"]), item.get("source", "file_cache"))
                        _SECURITY_RETURN_RUNTIME_CACHE[cache_key] = result
                        _cache_log(f"使用文件行情缓存: {cache_key} -> {result[0]:+.4f}%")
                        if return_trade_date:
                            return result[0], result[1], _normalize_trade_date_key(item.get("trade_date", ""))
                        return result
            except Exception:
                pass

    if ticker_norm is None:
        ticker_norm = _normalize_security_cache_ticker(market, ticker)

    _cache_log(f"重新获取行情: {market}:{ticker_norm} [{valuation_mode}]")
    fetched_trade_date = ""

    try:
        if valuation_mode == "last_close":
            if market == "US":
                r_pct, fetched_trade_date, source = fetch_us_return_pct_akshare_daily_with_date(ticker_norm)
                if item:
                    cached_trade_date = item.get("trade_date", "")
                    cmp_result = _compare_trade_dates(fetched_trade_date, cached_trade_date)
                    if cmp_result < 0:
                        _cache_log(f"拒绝用更旧的行情覆盖缓存: {cache_key}, fresh={fetched_trade_date}, cached={cached_trade_date}")
                        if security_return_cache_enabled:
                            cache = cache or _load_json_cache(SECURITY_RETURN_CACHE_FILE, default={})
                            old_entry = _mark_last_close_cache_checked(dict(item))
                            cache[cache_key] = old_entry
                            _save_security_return_cache(cache)
                        result = _cached_return_tuple(item)
                        if return_trade_date:
                            return result[0], result[1], _normalize_trade_date_key(item.get("trade_date", ""))
                        return result
                result = (r_pct, source)
            elif market == "CN":
                try:
                    r_pct, fetched_trade_date, source = fetch_cn_security_return_pct_daily_with_date(str(ticker_norm).zfill(6), end_date=stale_market_estimate_date)
                    result = (r_pct, source)
                except Exception:
                    r_pct, source = fetch_cn_security_return_pct(str(ticker_norm).zfill(6))
                    fetched_trade_date = ""
                    result = (r_pct, f"{source}_fallback_intraday_after_daily_fail")
            elif market == "HK":
                r_pct, fetched_trade_date, source = fetch_hk_return_pct_last_close_with_fallback_with_date(ticker_norm, end_date=stale_market_estimate_date)
                result = (r_pct, source)
            elif market == "KR":
                r_pct, fetched_trade_date, source = fetch_kr_return_pct_daily_with_date(ticker_norm, target_date=stale_market_estimate_date)
                result = (r_pct, source)
            else:
                raise RuntimeError(f"未知市场类型: market={market}, ticker={ticker}")
        else:
            if market == "US":
                result = fetch_us_return_pct(ticker_norm, prefer_intraday=prefer_intraday, us_realtime=us_realtime)
            elif market == "CN":
                result = fetch_cn_security_return_pct(str(ticker_norm).zfill(6))
                fetched_trade_date = _today_local_date_key()
            elif market == "HK":
                result = fetch_hk_return_pct(ticker_norm, hk_realtime=hk_realtime)
                fetched_trade_date = _today_local_date_key() if hk_realtime else ""
            elif market == "KR":
                # 韩国股票不使用盘中实时行情；intraday 口径下也回退为最新完整日线，避免盘中数据污染估值。
                r_pct, fetched_trade_date, source = fetch_kr_return_pct_daily_with_date(ticker_norm, target_date=stale_market_estimate_date)
                result = (r_pct, source)
            else:
                raise RuntimeError(f"未知市场类型: market={market}, ticker={ticker}")
    except Exception as e:
        if needs_trade_date_cache and item:
            _cache_log(f"行情刷新失败，使用旧缓存: {cache_key}, 原因: {e}")
            try:
                cache = cache or _load_json_cache(SECURITY_RETURN_CACHE_FILE, default={})
                old_entry = _mark_last_close_cache_checked(dict(item))
                cache[cache_key] = old_entry
                _save_security_return_cache(cache)
            except Exception:
                pass
            result = _cached_return_tuple(item)
            cached_trade_date = _normalize_trade_date_key(item.get("trade_date", ""))
            if market == "KR":
                zero_r_pct, zero_trade_date, zero_source = _apply_kr_holiday_zero_policy(
                    result[0],
                    cached_trade_date,
                    result[1],
                    target_date=stale_market_estimate_date,
                )
                result = (float(zero_r_pct), zero_source)
                cached_trade_date = _normalize_trade_date_key(zero_trade_date)
            if return_trade_date:
                return result[0], result[1], cached_trade_date
            return result
        raise

    # 文件缓存只保存行情源返回的原始行情，避免把某个目标估值日的置零结果
    # 污染同一天稍后针对其他估值日的计算。最终返回值再按 target_date 调整。
    raw_result_for_cache = result
    return_trade_date_final = _normalize_trade_date_key(fetched_trade_date)
    if market == "KR":
        zero_r_pct, zero_trade_date, zero_source = _apply_kr_holiday_zero_policy(
            result[0],
            fetched_trade_date,
            result[1],
            target_date=stale_market_estimate_date,
        )
        result = (float(zero_r_pct), zero_source)
        return_trade_date_final = _normalize_trade_date_key(zero_trade_date)

    if security_return_cache_enabled and cache_key:
        r_pct, source = raw_result_for_cache
        cache = cache or _load_json_cache(SECURITY_RETURN_CACHE_FILE, default={})
        entry = {
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "market": market,
            "ticker": ticker_norm,
            "return_pct": float(r_pct),
            "source": source,
            "valuation_mode": valuation_mode,
        }
        if needs_trade_date_cache:
            entry["trade_date"] = _normalize_trade_date_key(fetched_trade_date)
            entry = _mark_last_close_cache_checked(entry)
        cache[cache_key] = entry
        _save_security_return_cache(cache)
        _SECURITY_RETURN_RUNTIME_CACHE[cache_key] = raw_result_for_cache

    if return_trade_date:
        return float(result[0]), result[1], return_trade_date_final
    return result


def _fetch_us_index_return_pct_with_date(symbol, display_name=None, end_date=None):
    errors = []
    try:
        return fetch_us_index_return_pct_from_rsi_module(
            symbol=symbol,
            display_name=display_name,
            end_date=end_date,
        )
    except Exception as exc:
        errors.append(f"rsi_module: {repr(exc)}")

    try:
        yahoo_symbol = {
            ".NDX": "^NDX",
            ".INX": "^GSPC",
            ".IXIC": "^IXIC",
            ".DJI": "^DJI",
            ".SOX": "^SOX",
        }.get(str(symbol).strip().upper(), symbol)
        return fetch_us_index_return_pct_yahoo(
            symbol=yahoo_symbol,
            display_name=display_name,
            end_date=end_date,
        )
    except Exception as exc:
        errors.append(f"yahoo: {repr(exc)}")

    raise RuntimeError(f"指数 {display_name or symbol} 获取失败: {' | '.join(errors)}")


def _canonical_us_index_symbol(symbol: str) -> tuple[str, str]:
    symbol_norm = str(symbol or "").strip().upper()
    mapping = {
        ".NDX": (".NDX", "纳斯达克100"),
        "^NDX": (".NDX", "纳斯达克100"),
        "NDX": (".NDX", "纳斯达克100"),
        ".INX": (".INX", "标普500"),
        "^GSPC": (".INX", "标普500"),
        "GSPC": (".INX", "标普500"),
        "SPX": (".INX", "标普500"),
        ".SOX": (".SOX", "费城半导体"),
        "^SOX": (".SOX", "费城半导体"),
        "SOX": (".SOX", "费城半导体"),
        ".IXIC": (".IXIC", "纳斯达克综合"),
        "^IXIC": (".IXIC", "纳斯达克综合"),
        ".DJI": (".DJI", "道琼斯工业"),
        "^DJI": (".DJI", "道琼斯工业"),
    }
    return mapping.get(symbol_norm, (symbol_norm, symbol_norm))


def _return_pct_from_daily_price_df(
    df: pd.DataFrame,
    *,
    symbol: str,
    source: str,
    end_date=None,
) -> tuple[float, str, str]:
    if df is None or df.empty:
        raise RuntimeError(f"{source} 返回空数据: {symbol}")

    out = df.copy()
    date_col = _pick_column(out, ["date", "日期", "Date"])
    close_col = _pick_column(out, ["close", "收盘", "最新价", "Close"])

    if date_col is None or close_col is None:
        raise RuntimeError(
            f"{source} 缺少日期或收盘价列: {symbol}; 当前列={list(out.columns)}"
        )

    out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
    out[close_col] = pd.to_numeric(out[close_col], errors="coerce")
    out = out.dropna(subset=[date_col, close_col]).sort_values(date_col)

    end_date_key = _normalize_trade_date_key(end_date)
    if end_date_key:
        out = _drop_rows_after_target_date(out, out[date_col], end_date_key).sort_values(date_col)

    if len(out) < 2:
        raise RuntimeError(
            f"{source} 在目标日期 {end_date_key or 'latest'} 前有效收盘点不足: {symbol}"
        )

    last_close = float(out.iloc[-1][close_col])
    prev_close = float(out.iloc[-2][close_col])

    if prev_close == 0:
        raise RuntimeError(f"{source} 前一交易日收盘价为 0: {symbol}")

    trade_date = pd.Timestamp(out.iloc[-1][date_col]).strftime("%Y-%m-%d")
    return (last_close / prev_close - 1.0) * 100.0, trade_date, source


def fetch_foreign_futures_return_pct_with_date(symbol, end_date=None, fallback_ticker=None):
    """
    获取外盘期货 / 贵金属完整日线涨跌幅。

    主源使用新浪外盘期货历史行情，适合 XAU / GC；可选 fallback 使用
    东方财富国际期货历史行情，例如 GC00Y。这里同样只使用完整日线，
    并按 end_date 丢弃晚于估值锚点的行。
    """
    symbol_norm = str(symbol or "").strip().upper()
    fallback_norm = str(fallback_ticker or "").strip().upper()
    errors = []

    if symbol_norm:
        try:
            df = ak.futures_foreign_hist(symbol=symbol_norm)
            return _return_pct_from_daily_price_df(
                df,
                symbol=symbol_norm,
                source="ak_futures_foreign_hist_sina",
                end_date=end_date,
            )
        except Exception as exc:
            errors.append(f"ak_futures_foreign_hist_sina({symbol_norm}): {repr(exc)}")

    if fallback_norm:
        try:
            df = ak.futures_global_hist_em(symbol=fallback_norm)
            return _return_pct_from_daily_price_df(
                df,
                symbol=fallback_norm,
                source="ak_futures_global_hist_em_fallback",
                end_date=end_date,
            )
        except Exception as exc:
            errors.append(f"ak_futures_global_hist_em({fallback_norm}): {repr(exc)}")

    raise RuntimeError(f"外盘期货 {symbol_norm or fallback_norm} 日线获取失败: {' | '.join(errors)}")


def _fetch_daily_return_for_anchor(market: str, ticker: str, valuation_anchor_date: str):
    market = str(market or "").strip().upper()
    ticker_norm = _normalize_security_cache_ticker(market, ticker)
    anchor = _normalize_trade_date_key(valuation_anchor_date)

    if market == "US":
        symbol, display_name = _canonical_us_index_symbol(ticker_norm)
        if symbol.startswith("."):
            return _fetch_us_index_return_pct_with_date(symbol, display_name, end_date=anchor)
        return fetch_us_return_pct_daily_with_date(ticker_norm, end_date=anchor)

    if market == "CN":
        return fetch_cn_security_return_pct_daily_with_date(str(ticker_norm).zfill(6), end_date=anchor)

    if market == "HK":
        return fetch_hk_return_pct_akshare_daily_with_date(ticker_norm, end_date=anchor)

    if market == "KR":
        return fetch_kr_return_pct_daily_with_date(ticker_norm, target_date=anchor)

    raise RuntimeError(f"未知市场类型: market={market}, ticker={ticker}")


def get_security_return_by_anchor_date(
    market,
    ticker,
    valuation_anchor_date,
    allow_intraday=False,
    security_return_cache_enabled=True,
    now=None,
) -> dict:
    """
    按“市场 + 证券 + 估值锚点”获取完整日线涨跌幅。

    默认不允许盘中数据。只有行情源返回的 trade_date 与 valuation_anchor_date
    完全一致时，收益才可用于本次海外/全球基金估算。
    """
    market_norm = str(market or "").strip().upper()
    cache_key, ticker_norm, anchor = _anchor_security_cache_key(
        market_norm,
        ticker,
        valuation_anchor_date,
    )

    if not anchor:
        return _anchor_return_result(
            market=market_norm,
            ticker=ticker_norm,
            valuation_anchor_date=anchor,
            status="missing",
            source="anchor_missing",
            error="valuation_anchor_date 为空",
        )

    if allow_intraday:
        raise ValueError("海外/全球基金锚点估算不允许 allow_intraday=True")

    if security_return_cache_enabled:
        cached = _SECURITY_RETURN_RUNTIME_CACHE.get(cache_key)
        if isinstance(cached, dict) and _is_anchor_cache_entry_fresh(cached):
            return dict(cached)

        cache = _load_json_cache(SECURITY_RETURN_CACHE_FILE, default={})
        item = cache.get(cache_key) if isinstance(cache, dict) else None
        if isinstance(item, dict) and _is_anchor_cache_entry_fresh(item):
            _SECURITY_RETURN_RUNTIME_CACHE[cache_key] = dict(item)
            return dict(item)

    try:
        schedule = _market_schedule(market_norm, anchor, anchor)
        calendar_is_open = bool(schedule is not None and not schedule.empty)
    except Exception as exc:
        entry = _anchor_return_result(
            market=market_norm,
            ticker=ticker_norm,
            valuation_anchor_date=anchor,
            status="missing",
            return_pct=0.0,
            source="calendar_failed",
            calendar_is_open=None,
            error=str(exc),
        )
        if security_return_cache_enabled:
            _save_anchor_security_cache_entry(cache_key, entry)
        return entry

    if not calendar_is_open:
        entry = _anchor_return_result(
            market=market_norm,
            ticker=ticker_norm,
            valuation_anchor_date=anchor,
            status="closed",
            return_pct=0.0,
            trade_date="",
            source="market_calendar_closed",
            calendar_is_open=False,
        )
        if security_return_cache_enabled:
            _save_anchor_security_cache_entry(cache_key, entry)
        return entry

    if not _market_session_complete(market_norm, anchor, now=now):
        entry = _anchor_return_result(
            market=market_norm,
            ticker=ticker_norm,
            valuation_anchor_date=anchor,
            status="pending",
            return_pct=0.0,
            trade_date="",
            source="market_close_not_confirmed",
            calendar_is_open=True,
        )
        if security_return_cache_enabled:
            _save_anchor_security_cache_entry(cache_key, entry)
        return entry

    try:
        return_pct, trade_date, source = _fetch_daily_return_for_anchor(
            market_norm,
            ticker_norm,
            anchor,
        )
    except Exception as exc:
        entry = _anchor_return_result(
            market=market_norm,
            ticker=ticker_norm,
            valuation_anchor_date=anchor,
            status="missing",
            return_pct=0.0,
            trade_date="",
            source="daily_fetch_failed",
            calendar_is_open=True,
            error=str(exc),
        )
        if security_return_cache_enabled:
            _save_anchor_security_cache_entry(cache_key, entry)
        return entry

    trade_date_norm = _normalize_trade_date_key(trade_date)
    if trade_date_norm == anchor:
        entry = _anchor_return_result(
            market=market_norm,
            ticker=ticker_norm,
            valuation_anchor_date=anchor,
            status="traded",
            return_pct=float(return_pct),
            trade_date=trade_date_norm,
            source=source,
            calendar_is_open=True,
        )
    else:
        relation = "empty" if not trade_date_norm else ("older" if trade_date_norm < anchor else "future")
        entry = _anchor_return_result(
            market=market_norm,
            ticker=ticker_norm,
            valuation_anchor_date=anchor,
            status="stale",
            return_pct=0.0,
            trade_date=trade_date_norm,
            source=f"{source}_trade_date_{relation}_than_anchor",
            calendar_is_open=True,
            error=f"trade_date={trade_date_norm or '空'} 与 valuation_anchor_date={anchor} 不一致",
        )

    if security_return_cache_enabled:
        _save_anchor_security_cache_entry(cache_key, entry)
    return entry


def _return_from_anchor_result(anchor_result: dict) -> tuple[float | None, str, str, str]:
    status = str(anchor_result.get("status", "")).strip().lower()
    source = str(anchor_result.get("source", "anchor")).strip()
    trade_date = _normalize_trade_date_key(anchor_result.get("trade_date"))
    if status in ANCHOR_COMPLETE_STATUSES:
        return_pct = anchor_result.get("return_pct")
        return None if return_pct is None else float(return_pct), source, trade_date, status
    return 0.0, source, trade_date, status


def _normalize_residual_benchmark_key(value) -> str:
    return str(value or "").strip().lower()


def _resolve_residual_benchmark_spec(benchmark) -> dict | None:
    """
    将配置 key / 别名 / ticker 转成补偿基准配置。

    维护入口在 tools/configs/residual_benchmark_configs.py。这里尽量兼容旧写法：
    例如 nasdaq100、ndx、.NDX 都会解析到纳斯达克100。
    """
    if isinstance(benchmark, dict):
        market = str(benchmark.get("market") or "US").strip().upper()
        ticker = str(benchmark.get("ticker") or benchmark.get("symbol") or "").strip().upper()
        if not ticker:
            return None
        label = str(benchmark.get("label") or benchmark.get("name") or ticker).strip()
        key = _normalize_residual_benchmark_key(benchmark.get("key") or ticker)
        return {
            "key": key,
            "label": label,
            "market": market,
            "ticker": ticker,
        }

    key = _normalize_residual_benchmark_key(benchmark)
    if not key:
        return None

    for spec_key, spec in RESIDUAL_BENCHMARK_SPECS.items():
        aliases = {
            _normalize_residual_benchmark_key(spec_key),
            _normalize_residual_benchmark_key(spec.get("ticker")),
        }
        aliases.update(
            _normalize_residual_benchmark_key(alias)
            for alias in spec.get("aliases", set())
        )
        if key in aliases:
            return {
                "key": spec_key,
                "label": str(spec.get("label") or spec.get("ticker") or spec_key).strip(),
                "market": str(spec.get("market") or "US").strip().upper(),
                "ticker": str(spec.get("ticker") or "").strip().upper(),
            }

    return None


def _configured_residual_benchmark_for_fund(fund_code: str) -> str:
    code = str(fund_code or "").strip().zfill(6)
    return FUND_RESIDUAL_BENCHMARK_MAP.get(code, DEFAULT_RESIDUAL_BENCHMARK_KEY)


def _fetch_residual_benchmark_for_fund(
    fund_code: str,
    *,
    valuation_anchor_date: str,
    explicit_benchmark=None,
    explicit_return_pct=None,
    explicit_label=None,
    explicit_source=None,
    explicit_status=None,
    explicit_trade_date=None,
    auto_enabled=False,
    security_return_cache_enabled=True,
) -> dict:
    """
    解析并获取某只基金的补偿仓位基准。

    优先级：
    1. 外部显式传入 return_pct：完全尊重旧调用方覆盖；
    2. 外部显式传入 benchmark：该批基金统一使用这个基准；
    3. auto_enabled=True 时，按 FUND_RESIDUAL_BENCHMARK_MAP 查单基金配置；
    4. 没有单基金配置时，回落到默认纳斯达克100。
    """
    benchmark_key = explicit_benchmark
    if benchmark_key is None and auto_enabled:
        benchmark_key = _configured_residual_benchmark_for_fund(fund_code)

    spec = _resolve_residual_benchmark_spec(benchmark_key) if benchmark_key is not None else None

    if explicit_return_pct is not None:
        label = explicit_label
        if not label and spec:
            label = f"{spec['label']}({_normalize_trade_date_key(valuation_anchor_date)})"
        return {
            "return_pct": explicit_return_pct,
            "label": label or "剩余仓位基准",
            "source": explicit_source or "residual_benchmark",
            "status": explicit_status or "traded",
            "trade_date": _normalize_trade_date_key(explicit_trade_date),
            "market": spec.get("market", "US") if spec else "US",
            "ticker": spec.get("ticker", "") if spec else "",
            "key": spec.get("key", "") if spec else "",
        }

    if not spec:
        if benchmark_key:
            print(
                f"[WARN] 未识别的补偿仓位基准 {benchmark_key!r}，基金 {fund_code} 将沿用原股票持仓估算口径。",
                flush=True,
            )
        return {}

    anchor = _normalize_trade_date_key(valuation_anchor_date)
    try:
        anchor_result = get_security_return_by_anchor_date(
            market=spec["market"],
            ticker=spec["ticker"],
            valuation_anchor_date=anchor,
            allow_intraday=False,
            security_return_cache_enabled=security_return_cache_enabled,
        )
        r_pct, source, trade_date, bench_status = _return_from_anchor_result(anchor_result)
        return {
            "return_pct": r_pct,
            "label": explicit_label or f"{spec['label']}({anchor})",
            "source": explicit_source or source,
            "status": explicit_status or bench_status,
            "trade_date": trade_date,
            "market": spec["market"],
            "ticker": spec["ticker"],
            "key": spec["key"],
        }
    except Exception as e:
        print(
            f"[WARN] 基金 {str(fund_code).zfill(6)} 补偿仓位基准 {spec['label']}({spec['ticker']}) "
            f"获取失败，将沿用原股票持仓估算口径: {e}",
            flush=True,
        )
        return {}



def get_proxy_return_pct(
    component,
    manual_returns_pct=None,
    prefer_intraday=True,
    us_realtime=False,
    hk_realtime=False,
    security_return_cache_enabled=True,
    cn_hk_hourly_cache=True,
    valuation_mode="intraday",
    return_trade_date=False,
    stale_market_estimate_date=None,
):
    """获取 ETF 联接 / FOF 代理资产涨跌幅。"""
    code = str(component.get("code", "")).strip()
    ctype = str(component.get("type", "")).strip().lower()

    def _return_result(r_pct, source, trade_date=""):
        if return_trade_date:
            return float(r_pct), source, _normalize_trade_date_key(trade_date)
        return float(r_pct), source

    manual_key_candidates = [code, code.upper(), str(component.get("name", "")).strip()]
    if manual_returns_pct:
        for key in manual_key_candidates:
            if key in manual_returns_pct:
                return _return_result(manual_returns_pct[key], "manual", _today_local_date_key())

    if ctype == "manual":
        if "return_pct" not in component:
            raise RuntimeError(f"manual 代理缺少 return_pct: {component}")
        return _return_result(component["return_pct"], "manual_component", _today_local_date_key())

    if ctype in {"cn_etf", "cn_stock", "cn_security", "cn_fund"}:
        return get_stock_return_pct("CN", code, manual_returns_pct, prefer_intraday, us_realtime, hk_realtime, security_return_cache_enabled, cn_hk_hourly_cache, valuation_mode, return_trade_date=return_trade_date, stale_market_estimate_date=stale_market_estimate_date)
    if ctype in {"us_ticker", "us_stock", "us_etf"}:
        return get_stock_return_pct("US", code, manual_returns_pct, prefer_intraday, us_realtime, hk_realtime, security_return_cache_enabled, cn_hk_hourly_cache, valuation_mode, return_trade_date=return_trade_date, stale_market_estimate_date=stale_market_estimate_date)
    if ctype in {"hk_stock", "hk_etf", "hk_security"}:
        return get_stock_return_pct("HK", code, manual_returns_pct, prefer_intraday, us_realtime, hk_realtime, security_return_cache_enabled, cn_hk_hourly_cache, valuation_mode, return_trade_date=return_trade_date, stale_market_estimate_date=stale_market_estimate_date)
    if ctype in {"kr_stock", "kr_etf", "kr_security"}:
        return get_stock_return_pct("KR", code, manual_returns_pct, prefer_intraday, us_realtime, hk_realtime, security_return_cache_enabled, cn_hk_hourly_cache, valuation_mode, return_trade_date=return_trade_date, stale_market_estimate_date=stale_market_estimate_date)

    raise RuntimeError(f"未知代理组件类型: type={ctype}, component={component}")


# 股票持仓估算。

def get_latest_stock_holdings_df_uncached(fund_code="017437", top_n=10):
    """
    获取基金最新披露季度前 N 大股票持仓。
    """
    current_year = datetime.now().year
    years = [str(current_year), str(current_year - 1)]

    frames = []

    for year in years:
        try:
            df = ak.fund_portfolio_hold_em(
                symbol=str(fund_code),
                date=year,
            )

            if df is not None and not df.empty:
                df = df.copy()
                df["查询年份"] = year
                frames.append(df)

        except Exception as e:
            print(f"[WARN] {fund_code} {year} 股票持仓获取失败: {e}")

    if not frames:
        raise RuntimeError(f"未获取到基金 {fund_code} 的股票持仓数据。")

    data = pd.concat(frames, ignore_index=True)

    required_cols = ["股票代码", "股票名称", "占净值比例", "季度"]
    missing = [c for c in required_cols if c not in data.columns]

    if missing:
        raise RuntimeError(f"股票持仓缺少必要字段: {missing}; 当前字段: {list(data.columns)}")

    data["占净值比例"] = pd.to_numeric(data["占净值比例"], errors="coerce")
    data["_quarter_key"] = data["季度"].apply(quarter_key)

    data = data.dropna(subset=["占净值比例"])
    data = data[data["_quarter_key"] >= 0]

    if data.empty:
        raise RuntimeError(f"基金 {fund_code} 股票持仓清洗后为空。")

    latest_key = data["_quarter_key"].max()
    latest_df = data[data["_quarter_key"] == latest_key].copy()

    latest_df = latest_df.sort_values("占净值比例", ascending=False).head(top_n)
    latest_df = latest_df.reset_index(drop=True)

    latest_df[["市场", "ticker"]] = latest_df.apply(
        lambda row: pd.Series(
            detect_market_and_ticker(
                row["股票代码"],
                row["股票名称"],
            )
        ),
        axis=1,
    )

    total_weight = latest_df["占净值比例"].sum()

    if total_weight <= 0:
        raise RuntimeError("前 N 大股票持仓权重合计无效。")

    latest_df["归一化权重"] = latest_df["占净值比例"] / total_weight * 100.0

    return latest_df



def _target_holding_quarter_key_for_now(now=None):
    """
    返回当前是否处于基金季报持仓披露试探窗口，以及本轮目标季度。

    窗口：
        Q4：1月20日 - 2月10日，目标上一年Q4
        Q1：4月20日 - 5月10日，目标当年Q1
        Q2：7月20日 - 8月10日，目标当年Q2
        Q3：10月20日 - 11月10日，目标当年Q3

    返回：
        target_key, window_end
        不在窗口时 target_key=None。
    """
    if now is None:
        now = datetime.now()

    y, m, d = now.year, now.month, now.day

    if m == 1 and d >= 20:
        return (y - 1) * 10 + 4, datetime(y, 2, 10, 23, 59, 59)
    if m == 2 and d <= 10:
        return (y - 1) * 10 + 4, datetime(y, 2, 10, 23, 59, 59)

    if m == 4 and d >= 20:
        return y * 10 + 1, datetime(y, 5, 10, 23, 59, 59)
    if m == 5 and d <= 10:
        return y * 10 + 1, datetime(y, 5, 10, 23, 59, 59)

    if m == 7 and d >= 20:
        return y * 10 + 2, datetime(y, 8, 10, 23, 59, 59)
    if m == 8 and d <= 10:
        return y * 10 + 2, datetime(y, 8, 10, 23, 59, 59)

    if m == 10 and d >= 20:
        return y * 10 + 3, datetime(y, 11, 10, 23, 59, 59)
    if m == 11 and d <= 10:
        return y * 10 + 3, datetime(y, 11, 10, 23, 59, 59)

    return None, None


def _next_holding_disclosure_window_start(now=None):
    """
    返回下一次基金持仓披露试探窗口开始时间。
    """
    if now is None:
        now = datetime.now()

    y = now.year
    candidates = [
        datetime(y, 1, 20),
        datetime(y, 4, 20),
        datetime(y, 7, 20),
        datetime(y, 10, 20),
        datetime(y + 1, 1, 20),
    ]
    for dt in candidates:
        if dt > now:
            return dt
    return datetime(y + 1, 1, 20)


def _holding_cache_item_to_df(item):
    """
    尝试从基金持仓缓存项恢复 DataFrame。
    """
    if not isinstance(item, dict) or not item.get("data_json"):
        return None
    try:
        return _df_from_cache_json(item["data_json"])
    except Exception as e:
        print(f"[WARN] 基金持仓缓存损坏，将重新获取: {e}", flush=True)
        return None


def _holding_df_quarter_meta(df):
    """
    从持仓 DataFrame 中提取最新季度 key 和 label。
    """
    if df is None or df.empty:
        return None, None

    qkey = None
    qlabel = None

    if "_quarter_key" in df.columns:
        try:
            vals = pd.to_numeric(df["_quarter_key"], errors="coerce").dropna()
            if not vals.empty:
                qkey = int(vals.iloc[0])
        except Exception:
            qkey = None

    if "季度" in df.columns and not df["季度"].empty:
        try:
            qlabel = str(df["季度"].iloc[0])
            if qkey is None:
                qkey = quarter_key(qlabel)
        except Exception:
            pass

    return qkey, qlabel


def get_latest_stock_holdings_df(
    fund_code="017437",
    top_n=10,
    holding_cache_days=None,
    cache_enabled=True,
):
    """
    获取基金最新披露季度前 N 大股票持仓，带文件缓存。

    当前策略：
        - 平时有缓存就直接用缓存，不主动刷新基金持仓；
        - 只在 1/4/7/10 月20日至次月10日的季报披露窗口低频试探；
        - 窗口内每只基金约每 3 天最多请求一次；
        - 某基金已拿到本轮目标季度后，停止请求，等下一季度窗口；
        - 请求失败或返回旧季度时保留旧缓存，不污染数据。

    holding_cache_days 仅保留用于兼容旧调用；新策略不依赖固定 75 天周期。
    """
    fund_code = str(fund_code).zfill(6)
    top_n = int(top_n)
    cache_key = f"{fund_code}:top{top_n}"

    if not cache_enabled:
        return get_latest_stock_holdings_df_uncached(
            fund_code=fund_code,
            top_n=top_n,
        )

    now = datetime.now()
    target_key, window_end = _target_holding_quarter_key_for_now(now)
    in_window = target_key is not None

    cache = _load_json_cache(FUND_HOLDINGS_CACHE_FILE, default={})
    item = cache.get(cache_key)
    cached_df = _holding_cache_item_to_df(item)

    # 无缓存：必须抓一次，否则无法估算。
    if cached_df is None:
        try:
            _cache_log(f"无基金持仓缓存，首次获取: {cache_key}")
            df = get_latest_stock_holdings_df_uncached(fund_code=fund_code, top_n=top_n)
            latest_key, latest_label = _holding_df_quarter_meta(df)
            confirmed = bool(target_key is not None and latest_key is not None and latest_key >= target_key)
            next_check = _next_holding_disclosure_window_start(now) if confirmed or not in_window else now + timedelta(days=3)

            cache[cache_key] = {
                "fetched_at": now.isoformat(timespec="seconds"),
                "last_checked_at": now.isoformat(timespec="seconds"),
                "next_check_after": next_check.isoformat(timespec="seconds"),
                "fund_code": fund_code,
                "top_n": top_n,
                "latest_quarter_label": latest_label,
                "latest_quarter_key": latest_key,
                "target_quarter_key": target_key,
                "target_quarter_confirmed": confirmed,
                "data_json": _df_to_cache_json(df),
            }
            _save_json_cache(FUND_HOLDINGS_CACHE_FILE, cache)
            return df
        except Exception:
            raise

    cached_key = None
    cached_label = None
    if isinstance(item, dict):
        cached_key = item.get("latest_quarter_key")
        cached_label = item.get("latest_quarter_label")

    if cached_key is None or cached_label is None:
        cached_key, cached_label = _holding_df_quarter_meta(cached_df)

    # 不在披露窗口：直接用缓存，不做无意义请求。
    if not in_window:
        next_window = _next_holding_disclosure_window_start(now)
        if isinstance(item, dict):
            item.update({
                "latest_quarter_label": cached_label,
                "latest_quarter_key": cached_key,
                "target_quarter_key": item.get("target_quarter_key"),
                "target_quarter_confirmed": bool(item.get("target_quarter_confirmed", False)),
                "next_check_after": item.get("next_check_after") or next_window.isoformat(timespec="seconds"),
            })
            cache[cache_key] = item
            _save_json_cache(FUND_HOLDINGS_CACHE_FILE, cache)

        _cache_log(f"非披露窗口，使用基金持仓缓存: {cache_key}")
        return cached_df

    # 已经拿到本轮目标季度，直接使用缓存。
    if cached_key is not None and int(cached_key) >= int(target_key):
        next_window = _next_holding_disclosure_window_start(window_end or now)
        if isinstance(item, dict):
            item.update({
                "latest_quarter_label": cached_label,
                "latest_quarter_key": int(cached_key),
                "target_quarter_key": int(target_key),
                "target_quarter_confirmed": True,
                "next_check_after": next_window.isoformat(timespec="seconds"),
            })
            cache[cache_key] = item
            _save_json_cache(FUND_HOLDINGS_CACHE_FILE, cache)

        _cache_log(f"已确认目标季度持仓，使用缓存: {cache_key} -> {cached_label}")
        return cached_df

    # 尚未拿到目标季度：检查 next_check_after，未到时间则不请求。
    next_check_after = item.get("next_check_after") if isinstance(item, dict) else None
    if next_check_after:
        try:
            next_check_dt = pd.to_datetime(next_check_after).to_pydatetime()
            if now < next_check_dt:
                _cache_log(f"未到下次持仓检查时间，使用缓存: {cache_key}, next={next_check_after}")
                return cached_df
        except Exception:
            pass

    # 到达检查时间：低频试探。
    try:
        _cache_log(f"披露窗口内试探更新基金持仓: {cache_key}, target={target_key}")
        df = get_latest_stock_holdings_df_uncached(fund_code=fund_code, top_n=top_n)
        latest_key, latest_label = _holding_df_quarter_meta(df)

        # 防止接口返回更旧数据覆盖较新缓存。
        if cached_key is not None and latest_key is not None and int(latest_key) < int(cached_key):
            print(
                f"[WARN] 远程持仓季度旧于缓存，拒绝覆盖: {cache_key}, remote={latest_key}, cache={cached_key}",
                flush=True,
            )
            next_check = now + timedelta(days=3)
            item.update({
                "last_checked_at": now.isoformat(timespec="seconds"),
                "next_check_after": next_check.isoformat(timespec="seconds"),
                "target_quarter_key": int(target_key),
                "target_quarter_confirmed": False,
            })
            cache[cache_key] = item
            _save_json_cache(FUND_HOLDINGS_CACHE_FILE, cache)
            return cached_df

        confirmed = bool(latest_key is not None and int(latest_key) >= int(target_key))
        next_check = _next_holding_disclosure_window_start(window_end or now) if confirmed else now + timedelta(days=3)

        cache[cache_key] = {
            "fetched_at": now.isoformat(timespec="seconds"),
            "last_checked_at": now.isoformat(timespec="seconds"),
            "next_check_after": next_check.isoformat(timespec="seconds"),
            "fund_code": fund_code,
            "top_n": top_n,
            "latest_quarter_label": latest_label,
            "latest_quarter_key": latest_key,
            "target_quarter_key": int(target_key),
            "target_quarter_confirmed": confirmed,
            "data_json": _df_to_cache_json(df),
        }
        _save_json_cache(FUND_HOLDINGS_CACHE_FILE, cache)

        if confirmed:
            _cache_log(f"已更新到目标季度持仓: {cache_key} -> {latest_label}")
        else:
            _cache_log(f"远程仍未披露目标季度，保留本次最新持仓: {cache_key} -> {latest_label}")

        return df

    except Exception as e:
        print(f"[WARN] 基金持仓更新失败，使用旧缓存: {cache_key}, 原因: {e}", flush=True)
        if isinstance(item, dict):
            next_check = now + timedelta(days=3)
            item.update({
                "last_checked_at": now.isoformat(timespec="seconds"),
                "next_check_after": next_check.isoformat(timespec="seconds"),
                "target_quarter_key": int(target_key),
                "target_quarter_confirmed": False,
            })
            cache[cache_key] = item
            _save_json_cache(FUND_HOLDINGS_CACHE_FILE, cache)

        return cached_df



def _append_detail_row_without_concat_warning(df: pd.DataFrame, row: dict) -> pd.DataFrame:
    """
    向 detail DataFrame 追加一行，避免 pandas 在 concat / loc 追加全 NA 列时触发 FutureWarning。

    说明：
        - 不使用 pd.concat([df, one_row_df])；
        - 不使用 df.loc[len(df)] = ... 直接扩展；
        - 先 reindex 扩展一个唯一索引，再逐列写入。
    """
    out = df.copy()

    for col in row.keys():
        if col not in out.columns:
            out[col] = pd.NA

    new_index = "__extra_row__"
    suffix = 0
    while new_index in out.index:
        suffix += 1
        new_index = f"__extra_row__{suffix}"

    out = out.reindex(list(out.index) + [new_index])

    for col in out.columns:
        out.at[new_index, col] = row.get(col, pd.NA)

    return out.reset_index(drop=True)

def estimate_stock_holdings_return(
    latest_df,
    manual_returns_pct=None,
    prefer_intraday=True,
    us_realtime=False,
    hk_realtime=False,
    failed_return_as_zero=True,
    renormalize_available_holdings=True,
    security_return_cache_enabled=True,
    cn_hk_hourly_cache=True,
    valuation_mode="intraday",
    stock_residual_benchmark_return_pct=None,
    stock_residual_benchmark_label=None,
    stock_residual_benchmark_source=None,
    stock_residual_benchmark_status=None,
    stock_residual_benchmark_trade_date=None,
    stock_residual_benchmark_ticker=None,
    stock_residual_benchmark_market="US",
    stock_residual_benchmark_key=None,
    zero_stale_cn_hk_returns=False,
    stale_market_estimate_date=None,
    stale_market_zero_markets=("CN", "HK", "KR"),
    valuation_anchor_date=None,
):
    """
    使用前 N 大股票持仓估算基金收益。

    核心逻辑
    --------
    1. 先按披露的前 N 大持仓权重计算“归一化权重”；
    2. 获取每只持仓的当日涨跌幅；
    3. 如果某些持仓无法获取行情：
        - renormalize_available_holdings=True：
            剔除失败持仓，并把“可获取行情的持仓”再次归一化到 100% 后计算；
            适合你说的“日东纺这类无法获取数据时，用能查到的持仓股估算”。
        - renormalize_available_holdings=False：
            不重新归一化，缺失持仓贡献为空，等价于把缺失仓位视为未估算。
    """
    df = latest_df.copy()

    requested_valuation_mode = _normalize_valuation_mode(valuation_mode)
    if requested_valuation_mode == "auto":
        effective_valuation_mode = _resolve_auto_valuation_mode_from_markets(df.get("市场", []))
    else:
        effective_valuation_mode = requested_valuation_mode

    anchor_date = _normalize_trade_date_key(valuation_anchor_date)

    returns = []
    sources = []
    trade_dates = []
    anchor_statuses = []
    anchor_errors = []
    stale_zero_flags = []
    market_effective_flags = []
    warnings = []

    for _, row in df.iterrows():
        market = row["市场"]
        ticker = row["ticker"]
        name = row["股票名称"]
        trade_date = ""
        stale_zeroed = False

        try:
            if anchor_date:
                manual_key = str(ticker).strip().upper()
                manual_raw_key = str(ticker).strip()
                if manual_returns_pct and (
                    manual_key in manual_returns_pct or manual_raw_key in manual_returns_pct
                ):
                    manual_value = manual_returns_pct.get(manual_key, manual_returns_pct.get(manual_raw_key))
                    anchor_result = _anchor_return_result(
                        market=market,
                        ticker=ticker,
                        valuation_anchor_date=anchor_date,
                        status="traded",
                        return_pct=manual_value,
                        trade_date=anchor_date,
                        source="manual_anchor",
                        calendar_is_open=True,
                    )
                else:
                    anchor_result = get_security_return_by_anchor_date(
                        market=market,
                        ticker=ticker,
                        valuation_anchor_date=anchor_date,
                        allow_intraday=False,
                        security_return_cache_enabled=security_return_cache_enabled,
                    )

                r_pct, source, trade_date, anchor_status = _return_from_anchor_result(anchor_result)
                stale_zeroed = anchor_status == "closed"
                anchor_error = str(anchor_result.get("error", ""))

                if anchor_status in ANCHOR_BAD_STATUSES:
                    warnings.append(
                        f"{name}({ticker}) 所属市场 {market} 在估值锚点 {anchor_date} "
                        f"状态为 {anchor_status}，本次贡献暂按 0% 计入；{anchor_error}"
                    )
            else:
                r_pct, source, trade_date = get_stock_return_pct(
                    market=market,
                    ticker=ticker,
                    manual_returns_pct=manual_returns_pct,
                    prefer_intraday=prefer_intraday,
                    us_realtime=us_realtime,
                    hk_realtime=hk_realtime,
                    security_return_cache_enabled=security_return_cache_enabled,
                    cn_hk_hourly_cache=cn_hk_hourly_cache,
                    valuation_mode=effective_valuation_mode,
                    return_trade_date=True,
                    stale_market_estimate_date=stale_market_estimate_date,
                )
                r_pct, source, stale_zeroed = _apply_stale_market_zero_policy(
                    market=market,
                    return_pct=r_pct,
                    source=source,
                    trade_date=trade_date,
                    zero_stale_cn_hk_returns=zero_stale_cn_hk_returns,
                    stale_market_estimate_date=stale_market_estimate_date,
                    stale_market_zero_markets=stale_market_zero_markets,
                )
                anchor_status = "traded" if r_pct is not None and not stale_zeroed else ("closed" if stale_zeroed else "missing")
                anchor_error = ""
                if stale_zeroed:
                    warnings.append(
                        f"{name}({ticker}) 所属市场 {market} 最新交易日为 {trade_date}，"
                        f"早于估算日 {(_normalize_trade_date_key(stale_market_estimate_date) or _today_local_date_key())}，"
                        f"本次保留仓位但收益按 0% 计入，避免节假日重复计入旧涨跌幅。"
                    )
        except Exception as e:
            if failed_return_as_zero:
                r_pct, source = None, "failed"
                anchor_status = "missing"
                anchor_error = str(e)
                warnings.append(f"{name}({ticker}) 涨跌幅获取失败，已从有效估算权重中剔除：{e}")
            else:
                raise

        returns.append(r_pct)
        sources.append(source)
        trade_dates.append(_normalize_trade_date_key(trade_date))
        anchor_statuses.append(anchor_status)
        anchor_errors.append(anchor_error)
        stale_zero_flags.append(bool(stale_zeroed))
        market_effective_flags.append(bool(anchor_status in ANCHOR_COMPLETE_STATUSES))

    df["当日涨跌幅"] = returns
    df["收益数据源"] = sources
    df["收益交易日"] = trade_dates
    df["锚点状态"] = anchor_statuses
    df["锚点错误"] = anchor_errors
    df["闭市置零"] = stale_zero_flags
    df["市场有效"] = market_effective_flags

    if anchor_date:
        valid_mask = df["锚点状态"].isin(ANCHOR_COMPLETE_STATUSES)
    else:
        valid_mask = df["当日涨跌幅"].notna()
    valid_count = int(valid_mask.sum())
    failed_count = int((~valid_mask).sum())

    df["有效估算权重"] = pd.NA
    df["收益贡献"] = pd.NA

    # 海外股票持仓型基金的增强补偿口径：
    # 行情有效持仓按原始占净值比例放大；行情失败持仓与未披露仓位进入配置基准补偿仓位。
    raw_weight_sum_pct = float(pd.to_numeric(df["占净值比例"], errors="coerce").fillna(0).sum())
    available_raw_weight_sum_pct = float(pd.to_numeric(df.loc[valid_mask, "占净值比例"], errors="coerce").fillna(0).sum())
    failed_raw_weight_sum_pct = float(pd.to_numeric(df.loc[~valid_mask, "占净值比例"], errors="coerce").fillna(0).sum())

    use_residual_benchmark = stock_residual_benchmark_return_pct is not None

    if use_residual_benchmark:
        residual_label = stock_residual_benchmark_label or "剩余仓位基准"
        residual_source = stock_residual_benchmark_source or "residual_benchmark"
        residual_return_pct = float(stock_residual_benchmark_return_pct)
        residual_market = str(stock_residual_benchmark_market or "US").strip().upper()
        residual_ticker = str(stock_residual_benchmark_ticker or "").strip().upper()
        residual_status = str(
            stock_residual_benchmark_status
            or ("traded" if stock_residual_benchmark_return_pct is not None else "missing")
        ).strip().lower()
        if residual_status not in ANCHOR_MARKET_STATUSES:
            residual_status = "missing"
        residual_trade_date = _normalize_trade_date_key(stock_residual_benchmark_trade_date)

        # 海外股票持仓型基金专用口径：
        # 1. 行情有效的已披露持仓按人工放大系数计算；
        # 2. 行情失败的已披露持仓划入配置基准补偿仓位；
        # 3. 未披露仓位也划入配置基准补偿仓位；
        # 4. 为避免总权重超过 100%，补偿仓位 = 100% - 放大后的有效持仓权重。
        try:
            holding_boost = float(OVERSEAS_VALID_HOLDING_BOOST)
        except Exception:
            holding_boost = 1.0

        if not pd.notna(holding_boost) or holding_boost < 0:
            holding_boost = 1.0

        uncapped_boosted_available_weight_sum_pct = available_raw_weight_sum_pct * holding_boost

        if available_raw_weight_sum_pct > 0:
            # 封顶保护：无论 OVERSEAS_VALID_HOLDING_BOOST 设多大，
            # 放大后的有效持仓估算权重最多为 100%，避免总估算权重超过 100%。
            boosted_available_weight_sum_pct = min(
                100.0,
                uncapped_boosted_available_weight_sum_pct,
            )
            actual_boost = boosted_available_weight_sum_pct / available_raw_weight_sum_pct
        else:
            boosted_available_weight_sum_pct = 0.0
            actual_boost = 0.0

        cap_applied = uncapped_boosted_available_weight_sum_pct > 100.0
        residual_weight_pct = max(0.0, 100.0 - boosted_available_weight_sum_pct)

        if valid_count == 0 and residual_weight_pct <= 0:
            estimated_return_pct = None
            available_weight_sum_pct = 0.0
            failed_weight_sum_pct = failed_raw_weight_sum_pct
            method = "stock_boosted_raw_plus_residual_benchmark_failed"
        else:
            # 行情有效的已披露持仓：原始占净值比例 × 实际放大系数。
            if valid_count > 0:
                df.loc[valid_mask, "有效估算权重"] = (
                    df.loc[valid_mask, "占净值比例"] * actual_boost
                )
                df.loc[valid_mask, "收益贡献"] = (
                    df.loc[valid_mask, "有效估算权重"] * df.loc[valid_mask, "当日涨跌幅"] / 100.0
                )

            known_contribution = float(pd.to_numeric(df.loc[valid_mask, "收益贡献"], errors="coerce").sum())
            residual_contribution = residual_weight_pct * residual_return_pct / 100.0
            estimated_return_pct = known_contribution + residual_contribution

            available_weight_sum_pct = boosted_available_weight_sum_pct
            failed_weight_sum_pct = failed_raw_weight_sum_pct
            method = "stock_boosted_raw_plus_residual_benchmark"

            if residual_weight_pct > 0:
                residual_row = {
                    "股票代码": "RESIDUAL",
                    "股票名称": f"补偿仓位（{residual_label}）",
                    "占净值比例": residual_weight_pct,
                    "季度": "失败持仓与未披露仓位基准补偿",
                    "_quarter_key": pd.NA,
                    "市场": residual_market,
                    "ticker": residual_ticker,
                    "归一化权重": pd.NA,
                    "当日涨跌幅": residual_return_pct,
                    "收益数据源": residual_source,
                    "收益交易日": residual_trade_date,
                    "锚点状态": residual_status,
                    "锚点错误": "",
                    "闭市置零": residual_status == "closed",
                    "市场有效": residual_status in ANCHOR_COMPLETE_STATUSES,
                    "有效估算权重": residual_weight_pct,
                    "收益贡献": residual_contribution,
                }
                df = _append_detail_row_without_concat_warning(df, residual_row)

            unreported_weight_pct = max(0.0, 100.0 - raw_weight_sum_pct)
            transferred_boost_weight_pct = max(0.0, boosted_available_weight_sum_pct - available_raw_weight_sum_pct)

            if cap_applied:
                warnings.append(
                    f"有效持仓放大后超过 100%，已执行封顶保护："
                    f"原始有效持仓 {available_raw_weight_sum_pct:.2f}% × 配置放大系数 {holding_boost:.2f} "
                    f"= {uncapped_boosted_available_weight_sum_pct:.2f}%，"
                    f"实际有效估算权重封顶为 {boosted_available_weight_sum_pct:.2f}%，"
                    f"实际放大系数 {actual_boost:.4f}。"
                )

            warnings.append(
                f"已启用海外股票持仓增强补偿口径：已披露前N大持仓合计 {raw_weight_sum_pct:.2f}%，"
                f"其中行情有效 {available_raw_weight_sum_pct:.2f}%，行情失败 {failed_raw_weight_sum_pct:.2f}%，"
                f"未披露仓位 {unreported_weight_pct:.2f}%；"
                f"有效持仓放大系数 {holding_boost:.2f}，实际有效估算权重 {boosted_available_weight_sum_pct:.2f}%，"
                f"从基准补偿仓位转移 {transferred_boost_weight_pct:.2f}% 给有效持仓；"
                f"补偿仓位 {residual_weight_pct:.2f}% 使用 {residual_label} {residual_return_pct:+.4f}% 估算。"
            )
            if residual_status in ANCHOR_BAD_STATUSES:
                warnings.append(
                    f"补偿基准 {residual_label} 在估值锚点 {anchor_date or stale_market_estimate_date} "
                    f"状态为 {residual_status}，补偿仓位贡献暂按 0% 计入。"
                )
    else:
        if valid_count == 0:
            estimated_return_pct = None
            available_weight_sum_pct = 0.0
            failed_weight_sum_pct = float(df["归一化权重"].sum())
            method = "stock_topn_available_normalized_failed"
        else:
            available_weight_sum_pct = float(df.loc[valid_mask, "归一化权重"].sum())
            failed_weight_sum_pct = float(df.loc[~valid_mask, "归一化权重"].sum())

            if available_weight_sum_pct <= 0:
                estimated_return_pct = None
                method = "stock_topn_available_normalized_failed"
            else:
                if renormalize_available_holdings:
                    df.loc[valid_mask, "有效估算权重"] = (
                        df.loc[valid_mask, "归一化权重"] / available_weight_sum_pct * 100.0
                    )
                    method = "stock_topn_available_normalized"
                else:
                    df.loc[valid_mask, "有效估算权重"] = df.loc[valid_mask, "归一化权重"]
                    method = "stock_topn_original_normalized"

                df.loc[valid_mask, "收益贡献"] = (
                    df.loc[valid_mask, "有效估算权重"] * df.loc[valid_mask, "当日涨跌幅"] / 100.0
                )
                estimated_return_pct = float(pd.to_numeric(df.loc[valid_mask, "收益贡献"], errors="coerce").sum())

    zeroed_mask = _safe_bool_series(
        df["闭市置零"] if "闭市置零" in df.columns else None,
        index=df.index,
    )

    market_status = {}
    market_trade_dates = {}
    if "市场" in df.columns and "锚点状态" in df.columns:
        for market_name, group in df.groupby(df["市场"].astype(str).str.upper()):
            statuses = [str(x).strip().lower() for x in group["锚点状态"].dropna().tolist()]
            statuses = [x for x in statuses if x]
            if not statuses:
                continue
            if "stale" in statuses:
                chosen_status = "stale"
            elif "missing" in statuses:
                chosen_status = "missing"
            elif "pending" in statuses:
                chosen_status = "pending"
            elif "traded" in statuses:
                chosen_status = "traded"
            else:
                chosen_status = "closed"
            market_status[market_name] = chosen_status

            dates = [
                _normalize_trade_date_key(x)
                for x in group.get("收益交易日", pd.Series(dtype=str)).dropna().tolist()
            ]
            dates = [x for x in dates if x]
            market_trade_dates[market_name] = max(dates) if dates else None

    detail_statuses = [
        str(x).strip().lower()
        for x in df.get("锚点状态", pd.Series(dtype=str)).dropna().tolist()
    ]
    has_bad_status = any(status in ANCHOR_BAD_STATUSES for status in detail_statuses)
    has_stale_status = any(status == "stale" for status in detail_statuses)
    if use_residual_benchmark and "residual_status" in locals():
        if residual_status in ANCHOR_BAD_STATUSES:
            has_bad_status = True
        if residual_status == "stale":
            has_stale_status = True
        if "US" not in market_status:
            market_status["US"] = residual_status
            market_trade_dates["US"] = residual_trade_date or None
    completeness_score = max(0.0, min(100.0, 100.0 - float(failed_raw_weight_sum_pct)))
    if use_residual_benchmark and "residual_status" in locals() and residual_status in ANCHOR_BAD_STATUSES:
        completeness_score = max(0.0, completeness_score - float(residual_weight_pct))

    if estimated_return_pct is None:
        data_status = "failed"
    elif has_stale_status:
        data_status = "stale"
    elif has_bad_status:
        data_status = "partial"
    else:
        data_status = "complete"

    summary = {
        "method": method,
        "data_status": data_status,
        "completeness_score": float(completeness_score),
        "is_final": bool(data_status == "complete"),
        "valuation_anchor_date": anchor_date,
        "raw_weight_sum_pct": raw_weight_sum_pct,
        "raw_holding_weight_sum_pct": raw_weight_sum_pct,
        "normalized_weight_sum_pct": float(pd.to_numeric(df.get("归一化权重", pd.Series(dtype=float)), errors="coerce").sum()),
        "available_normalized_weight_sum_pct": float(available_weight_sum_pct),
        "failed_normalized_weight_sum_pct": float(failed_weight_sum_pct),
        "available_raw_weight_sum_pct": available_raw_weight_sum_pct,
        "valid_holding_weight_pct": available_raw_weight_sum_pct,
        "failed_raw_weight_sum_pct": failed_raw_weight_sum_pct,
        "residual_benchmark_enabled": bool(use_residual_benchmark),
        "residual_benchmark_label": stock_residual_benchmark_label,
        "residual_benchmark_key": stock_residual_benchmark_key or "",
        "residual_benchmark_market": str(stock_residual_benchmark_market or "US").strip().upper() if use_residual_benchmark else "",
        "residual_benchmark_ticker": str(stock_residual_benchmark_ticker or "").strip().upper() if use_residual_benchmark else "",
        "residual_benchmark_return_pct": None if stock_residual_benchmark_return_pct is None else float(stock_residual_benchmark_return_pct),
        "residual_benchmark_trade_date": _normalize_trade_date_key(stock_residual_benchmark_trade_date),
        "residual_benchmark_status": stock_residual_benchmark_status or ("traded" if stock_residual_benchmark_return_pct is not None else ""),
        "residual_benchmark_weight_pct": float(residual_weight_pct) if use_residual_benchmark and "residual_weight_pct" in locals() else 0.0,
        "overseas_valid_holding_boost": float(OVERSEAS_VALID_HOLDING_BOOST) if use_residual_benchmark else 1.0,
        "boosted_available_raw_weight_sum_pct": float(available_weight_sum_pct) if use_residual_benchmark else available_raw_weight_sum_pct,
        "boosted_valid_holding_weight_pct": float(available_weight_sum_pct) if use_residual_benchmark else available_raw_weight_sum_pct,
        "valid_holding_count": valid_count,
        "failed_holding_count": failed_count,
        "renormalize_available_holdings": bool(renormalize_available_holdings),
        "requested_valuation_mode": requested_valuation_mode,
        "effective_valuation_mode": effective_valuation_mode,
        "estimated_return_pct": estimated_return_pct,
        "zero_stale_cn_hk_returns": bool(zero_stale_cn_hk_returns),
        "stale_market_estimate_date": _normalize_trade_date_key(stale_market_estimate_date) or _today_local_date_key(),
        "stale_zeroed_count": int(zeroed_mask.sum()),
        "stale_zeroed_markets": sorted(df.loc[zeroed_mask, "市场"].astype(str).str.upper().unique().tolist()) if "市场" in df.columns else [],
        "market_effective": _summarize_market_effective(df),
        "market_status": market_status,
        "market_trade_dates": market_trade_dates,
        "source": "anchor_daily" if anchor_date else "legacy",
        "warnings": warnings,
    }

    return df, summary


# ETF 联接 / FOF / 指数代理估算。

def estimate_proxy_components_return(
    fund_code,
    proxy_map=None,
    manual_returns_pct=None,
    prefer_intraday=True,
    us_realtime=False,
    hk_realtime=False,
    proxy_normalize_weights=False,
    failed_return_as_zero=True,
    security_return_cache_enabled=True,
    cn_hk_hourly_cache=True,
    valuation_mode="intraday",
    zero_stale_cn_hk_returns=False,
    stale_market_estimate_date=None,
    stale_market_zero_markets=("CN", "HK", "KR"),
    valuation_anchor_date=None,
):
    """
    根据 proxy_map 中的底层 ETF / 指数组件估算基金涨跌幅。

    proxy_normalize_weights:
        False：
            默认。按组件原始 weight_pct 计算，现金仓位视为 0。
            适合 ETF 联接基金。
        True：
            将所有组件权重归一化到 100% 后估算。
            适合只想看代理资产本身表现。
    """
    fund_code = str(fund_code).zfill(6)

    if proxy_map is None:
        proxy_map = DEFAULT_FUND_PROXY_MAP

    if fund_code not in proxy_map:
        raise RuntimeError(
            f"基金 {fund_code} 未配置代理资产。请在 DEFAULT_FUND_PROXY_MAP 或 proxy_map 中增加配置。"
        )

    config = proxy_map[fund_code]
    components = config.get("components", [])

    requested_valuation_mode = _normalize_valuation_mode(valuation_mode)
    if requested_valuation_mode == "auto":
        effective_valuation_mode = _resolve_auto_valuation_mode_from_components(components)
    else:
        effective_valuation_mode = requested_valuation_mode

    anchor_date = _normalize_trade_date_key(valuation_anchor_date)

    if not components:
        raise RuntimeError(f"基金 {fund_code} 的代理配置缺少 components。")

    df = pd.DataFrame(components).copy()

    if "weight_pct" not in df.columns:
        raise RuntimeError(f"基金 {fund_code} 的代理组件缺少 weight_pct。")

    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce")
    df = df.dropna(subset=["weight_pct"])

    if df.empty or df["weight_pct"].sum() <= 0:
        raise RuntimeError(f"基金 {fund_code} 的代理组件权重无效。")

    if proxy_normalize_weights:
        df["估算权重"] = df["weight_pct"] / df["weight_pct"].sum() * 100.0
        weight_mode = "normalized_proxy_weights"
    else:
        df["估算权重"] = df["weight_pct"]
        weight_mode = "raw_proxy_weights_cash_as_zero"

    returns = []
    sources = []
    trade_dates = []
    anchor_statuses = []
    anchor_errors = []
    stale_zero_flags = []
    market_effective_flags = []
    warnings = []

    for _, row in df.iterrows():
        component = row.to_dict()
        name = component.get("name", component.get("code", ""))
        market = _component_market_type(component)
        trade_date = ""
        stale_zeroed = False

        try:
            if anchor_date:
                code = str(component.get("code", "")).strip()
                manual_key_candidates = [code, code.upper(), str(component.get("name", "")).strip()]
                manual_value = None
                if manual_returns_pct:
                    for manual_key in manual_key_candidates:
                        if manual_key in manual_returns_pct:
                            manual_value = manual_returns_pct[manual_key]
                            break

                if manual_value is not None:
                    anchor_result = _anchor_return_result(
                        market=market,
                        ticker=code,
                        valuation_anchor_date=anchor_date,
                        status="traded",
                        return_pct=manual_value,
                        trade_date=anchor_date,
                        source="manual_anchor",
                        calendar_is_open=True,
                    )
                else:
                    anchor_result = get_security_return_by_anchor_date(
                        market=market,
                        ticker=code,
                        valuation_anchor_date=anchor_date,
                        allow_intraday=False,
                        security_return_cache_enabled=security_return_cache_enabled,
                    )

                r_pct, source, trade_date, anchor_status = _return_from_anchor_result(anchor_result)
                stale_zeroed = anchor_status == "closed"
                anchor_error = str(anchor_result.get("error", ""))
                if anchor_status in ANCHOR_BAD_STATUSES:
                    warnings.append(
                        f"{name} 代理资产所属市场 {market} 在估值锚点 {anchor_date} "
                        f"状态为 {anchor_status}，本次贡献暂按 0% 计入；{anchor_error}"
                    )
            else:
                r_pct, source, trade_date = get_proxy_return_pct(
                    component=component,
                    manual_returns_pct=manual_returns_pct,
                    prefer_intraday=prefer_intraday,
                    us_realtime=us_realtime,
                    hk_realtime=hk_realtime,
                    security_return_cache_enabled=security_return_cache_enabled,
                    cn_hk_hourly_cache=cn_hk_hourly_cache,
                    valuation_mode=effective_valuation_mode,
                    return_trade_date=True,
                    stale_market_estimate_date=stale_market_estimate_date,
                )
                r_pct, source, stale_zeroed = _apply_stale_market_zero_policy(
                    market=market,
                    return_pct=r_pct,
                    source=source,
                    trade_date=trade_date,
                    zero_stale_cn_hk_returns=zero_stale_cn_hk_returns,
                    stale_market_estimate_date=stale_market_estimate_date,
                    stale_market_zero_markets=stale_market_zero_markets,
                )
                anchor_status = "traded" if r_pct is not None and not stale_zeroed else ("closed" if stale_zeroed else "missing")
                anchor_error = ""
                if stale_zeroed:
                    warnings.append(
                        f"{name} 代理资产所属市场 {market} 最新交易日为 {trade_date}，"
                        f"早于估算日 {(_normalize_trade_date_key(stale_market_estimate_date) or _today_local_date_key())}，"
                        f"本次保留仓位但收益按 0% 计入。"
                    )
        except Exception as e:
            if failed_return_as_zero:
                r_pct, source = None, "failed"
                anchor_status = "missing"
                anchor_error = str(e)
                warnings.append(f"{name} 代理涨跌幅获取失败：{e}")
            else:
                raise

        returns.append(r_pct)
        sources.append(source)
        trade_dates.append(_normalize_trade_date_key(trade_date))
        anchor_statuses.append(anchor_status)
        anchor_errors.append(anchor_error)
        stale_zero_flags.append(bool(stale_zeroed))
        market_effective_flags.append(bool(anchor_status in ANCHOR_COMPLETE_STATUSES))

    df["市场"] = [_component_market_type(x) for x in df.to_dict("records")]
    df["当日涨跌幅"] = returns
    df["收益数据源"] = sources
    df["收益交易日"] = trade_dates
    df["锚点状态"] = anchor_statuses
    df["锚点错误"] = anchor_errors
    df["闭市置零"] = stale_zero_flags
    df["市场有效"] = market_effective_flags

    if anchor_date:
        valid_df = df[df["锚点状态"].isin(ANCHOR_COMPLETE_STATUSES)].copy()
    else:
        valid_df = df.dropna(subset=["当日涨跌幅"]).copy()

    if valid_df.empty:
        estimated_return_pct = None
        df["收益贡献"] = None
    else:
        df["收益贡献"] = df["估算权重"] * df["当日涨跌幅"] / 100.0
        estimated_return_pct = float(df["收益贡献"].sum(skipna=True))

    zeroed_mask = _safe_bool_series(
        df["闭市置零"] if "闭市置零" in df.columns else None,
        index=df.index,
    )

    market_status = {}
    market_trade_dates = {}
    for market_name, group in df.groupby(df["市场"].astype(str).str.upper()):
        statuses = [str(x).strip().lower() for x in group["锚点状态"].dropna().tolist()]
        if "stale" in statuses:
            chosen_status = "stale"
        elif "missing" in statuses:
            chosen_status = "missing"
        elif "pending" in statuses:
            chosen_status = "pending"
        elif "traded" in statuses:
            chosen_status = "traded"
        else:
            chosen_status = "closed"
        market_status[market_name] = chosen_status
        dates = [_normalize_trade_date_key(x) for x in group["收益交易日"].dropna().tolist()]
        dates = [x for x in dates if x]
        market_trade_dates[market_name] = max(dates) if dates else None

    detail_statuses = [str(x).strip().lower() for x in df["锚点状态"].dropna().tolist()]
    has_bad_status = any(status in ANCHOR_BAD_STATUSES for status in detail_statuses)
    has_stale_status = any(status == "stale" for status in detail_statuses)
    unresolved_weight_pct = float(
        pd.to_numeric(
            df.loc[~df["锚点状态"].isin(ANCHOR_COMPLETE_STATUSES), "估算权重"],
            errors="coerce",
        ).fillna(0).sum()
    )
    completeness_score = max(0.0, min(100.0, 100.0 - unresolved_weight_pct))

    if estimated_return_pct is None:
        data_status = "failed"
    elif has_stale_status:
        data_status = "stale"
    elif has_bad_status:
        data_status = "partial"
    else:
        data_status = "complete"

    summary = {
        "method": "proxy_components",
        "data_status": data_status,
        "completeness_score": float(completeness_score),
        "is_final": bool(data_status == "complete"),
        "valuation_anchor_date": anchor_date,
        "weight_mode": weight_mode,
        "proxy_description": config.get("description", ""),
        "raw_weight_sum_pct": float(df["weight_pct"].sum()),
        "raw_holding_weight_sum_pct": float(df["weight_pct"].sum()),
        "estimated_weight_sum_pct": float(df["估算权重"].sum()),
        "valid_holding_weight_pct": float(pd.to_numeric(valid_df.get("weight_pct", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not valid_df.empty else 0.0,
        "boosted_valid_holding_weight_pct": float(pd.to_numeric(valid_df.get("估算权重", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not valid_df.empty else 0.0,
        "residual_benchmark_weight_pct": 0.0,
        "residual_benchmark_return_pct": None,
        "residual_benchmark_trade_date": "",
        "estimated_return_pct": estimated_return_pct,
        "requested_valuation_mode": requested_valuation_mode,
        "effective_valuation_mode": effective_valuation_mode,
        "zero_stale_cn_hk_returns": bool(zero_stale_cn_hk_returns),
        "stale_market_estimate_date": _normalize_trade_date_key(stale_market_estimate_date) or _today_local_date_key(),
        "stale_zeroed_count": int(zeroed_mask.sum()),
        "stale_zeroed_markets": sorted(df.loc[zeroed_mask, "市场"].astype(str).str.upper().unique().tolist()) if "市场" in df.columns else [],
        "market_effective": _summarize_market_effective(df),
        "market_status": market_status,
        "market_trade_dates": market_trade_dates,
        "source": "anchor_daily" if anchor_date else "legacy",
        "warnings": warnings,
    }

    return df, summary


# 单基金估算与批量估算。

def estimate_one_fund(
    fund_code,
    top_n=10,
    manual_returns_pct=None,
    prefer_intraday=True,
    us_realtime=False,
    hk_realtime=False,
    renormalize_available_holdings=True,
    include_purchase_limit=True,
    purchase_limit_timeout=8,
    purchase_limit_cache_days=7,
    holding_cache_days=75,
    cache_enabled=True,
    security_return_cache_enabled=True,
    cn_hk_hourly_cache=True,
    holding_mode="auto",
    proxy_map=None,
    proxy_normalize_weights=False,
    valuation_mode="intraday",
    stock_residual_benchmark_return_pct=None,
    stock_residual_benchmark_label=None,
    stock_residual_benchmark_source=None,
    stock_residual_benchmark_status=None,
    stock_residual_benchmark_trade_date=None,
    stock_residual_benchmark_ticker=None,
    stock_residual_benchmark_market="US",
    stock_residual_benchmark_key=None,
    zero_stale_cn_hk_returns=False,
    stale_market_estimate_date=None,
    valuation_anchor_date=None,
):
    """
    估算单只基金的今日涨跌幅。

    参数
    ----
    fund_code:
        基金代码。

    top_n:
        股票型基金取前 N 大股票持仓。

    manual_returns_pct:
        手动覆盖涨跌幅，单位百分数。
        示例：{"NVDA": 4.00, "512890": 0.35, "^GSPC": -0.20}

    prefer_intraday:
        兼容旧调用保留，当前估值流程不依赖该参数。

    us_realtime:
        是否启用美股实时行情。

    renormalize_available_holdings:
        True：
            如果部分持仓行情获取失败，则只使用可获取行情的持仓，并把这些持仓重新归一化到 100%。
        False：
            不重新归一化，缺失持仓不参与贡献。

    include_purchase_limit:
        是否获取限购金额。

    purchase_limit_timeout:
        限购网页请求超时秒数。

    holding_mode:
        "auto"：
            如果 fund_code 在 proxy_map 中，优先走代理估算；
            否则走股票持仓估算。
        "stock"：
            强制股票持仓估算。
        "proxy"：
            强制代理估算，适合 ETF 联接 / FOF。

    proxy_map:
        代理映射表。None 时使用 DEFAULT_FUND_PROXY_MAP。

    proxy_normalize_weights:
        ETF/FOF 代理组件是否归一化到 100%。
        False：默认，按原始权重计算，现金按 0。
        True ：代理组件归一化到 100%。
    """
    fund_code = str(fund_code).zfill(6)
    fund_name = get_fund_name(fund_code)

    if proxy_map is None:
        proxy_map = DEFAULT_FUND_PROXY_MAP

    mode = str(holding_mode).strip().lower()

    if mode not in {"auto", "stock", "proxy"}:
        raise ValueError("holding_mode 只能是 'auto', 'stock', 'proxy'")

    detail_df = None
    summary = None

    if mode == "proxy" or (mode == "auto" and fund_code in proxy_map):
        detail_df, summary = estimate_proxy_components_return(
            fund_code=fund_code,
            proxy_map=proxy_map,
            manual_returns_pct=manual_returns_pct,
            prefer_intraday=prefer_intraday,
            us_realtime=us_realtime,
            hk_realtime=hk_realtime,
            proxy_normalize_weights=proxy_normalize_weights,
            security_return_cache_enabled=security_return_cache_enabled,
            cn_hk_hourly_cache=cn_hk_hourly_cache,
            valuation_mode=valuation_mode,
            zero_stale_cn_hk_returns=zero_stale_cn_hk_returns,
            stale_market_estimate_date=stale_market_estimate_date,
            valuation_anchor_date=valuation_anchor_date,
        )
    else:
        latest_df = get_latest_stock_holdings_df(
            fund_code=fund_code,
            top_n=top_n,
            holding_cache_days=holding_cache_days,
            cache_enabled=cache_enabled,
        )

        detail_df, summary = estimate_stock_holdings_return(
            latest_df=latest_df,
            manual_returns_pct=manual_returns_pct,
            prefer_intraday=prefer_intraday,
            us_realtime=us_realtime,
            hk_realtime=hk_realtime,
            renormalize_available_holdings=renormalize_available_holdings,
            security_return_cache_enabled=security_return_cache_enabled,
            cn_hk_hourly_cache=cn_hk_hourly_cache,
            valuation_mode=valuation_mode,
            stock_residual_benchmark_return_pct=stock_residual_benchmark_return_pct,
            stock_residual_benchmark_label=stock_residual_benchmark_label,
            stock_residual_benchmark_source=stock_residual_benchmark_source,
            stock_residual_benchmark_status=stock_residual_benchmark_status,
            stock_residual_benchmark_trade_date=stock_residual_benchmark_trade_date,
            stock_residual_benchmark_ticker=stock_residual_benchmark_ticker,
            stock_residual_benchmark_market=stock_residual_benchmark_market,
            stock_residual_benchmark_key=stock_residual_benchmark_key,
            zero_stale_cn_hk_returns=zero_stale_cn_hk_returns,
            stale_market_estimate_date=stale_market_estimate_date,
            valuation_anchor_date=valuation_anchor_date,
        )

    summary["valuation_mode"] = summary.get("effective_valuation_mode", _normalize_valuation_mode(valuation_mode))

    result_row = {
        "基金代码": fund_code,
        "基金名称": fund_name,
        "今日预估涨跌幅": summary["estimated_return_pct"],
        "_估算方式": summary.get("method", ""),
    }

    if include_purchase_limit:
        result_row["限购金额"] = get_fund_purchase_limit(
            fund_code=fund_code,
            timeout=purchase_limit_timeout,
            cache_days=purchase_limit_cache_days,
            cache_enabled=cache_enabled,
        )

    return result_row, detail_df, summary


def estimate_funds(
    fund_codes,
    top_n=10,
    manual_returns_pct=None,
    prefer_intraday=True,
    us_realtime=False,
    hk_realtime=False,
    renormalize_available_holdings=True,
    include_purchase_limit=True,
    purchase_limit_timeout=8,
    purchase_limit_cache_days=7,
    holding_cache_days=75,
    cache_enabled=True,
    security_return_cache_enabled=True,
    cn_hk_hourly_cache=True,
    sort_by_return=True,
    holding_mode="auto",
    proxy_map=None,
    proxy_normalize_weights=False,
    include_method_col=False,
    valuation_mode="intraday",
    auto_residual_benchmark_enabled=False,
    stock_residual_benchmark=None,
    stock_residual_benchmark_return_pct=None,
    stock_residual_benchmark_label=None,
    stock_residual_benchmark_source=None,
    stock_residual_benchmark_status=None,
    stock_residual_benchmark_trade_date=None,
    zero_stale_cn_hk_returns=False,
    stale_market_estimate_date=None,
    valuation_anchor_date=None,
):
    """
    批量估算多只基金的今日预估涨跌幅。

    参数
    ----
    sort_by_return:
        True：按“今日预估涨跌幅”从高到低排序，并重新编号。
        False：保留 fund_codes 输入顺序。

    holding_mode:
        "auto"：自动选择股票持仓或代理估算。
        "stock"：强制股票持仓。
        "proxy"：强制代理估算。

    include_method_col:
        True：表格显示“估算方式”列。
        False：不显示，保持表格简洁。

    renormalize_available_holdings:
        True：部分股票行情失败时，把剩余可查持仓重新归一化到 100% 后估算。
        False：失败持仓不参与收益贡献，但不重新分配其权重。

    返回
    ----
    result_df, detail_map
    """
    if isinstance(fund_codes, str):
        fund_codes = [fund_codes]

    if proxy_map is None:
        proxy_map = DEFAULT_FUND_PROXY_MAP

    rows = []
    detail_map = {}

    for i, fund_code in enumerate(fund_codes, start=1):
        code = str(fund_code).zfill(6)

        try:
            mode_norm = str(holding_mode).strip().lower()
            will_use_stock_holdings = not (
                mode_norm == "proxy"
                or (mode_norm == "auto" and code in proxy_map)
            )
            residual_kwargs = {}
            if will_use_stock_holdings and (
                stock_residual_benchmark_return_pct is not None
                or stock_residual_benchmark is not None
                or auto_residual_benchmark_enabled
            ):
                residual_info = _fetch_residual_benchmark_for_fund(
                    code,
                    valuation_anchor_date=valuation_anchor_date,
                    explicit_benchmark=stock_residual_benchmark,
                    explicit_return_pct=stock_residual_benchmark_return_pct,
                    explicit_label=stock_residual_benchmark_label,
                    explicit_source=stock_residual_benchmark_source,
                    explicit_status=stock_residual_benchmark_status,
                    explicit_trade_date=stock_residual_benchmark_trade_date,
                    auto_enabled=auto_residual_benchmark_enabled,
                    security_return_cache_enabled=security_return_cache_enabled,
                )
                if residual_info:
                    residual_kwargs = {
                        "stock_residual_benchmark_return_pct": residual_info.get("return_pct"),
                        "stock_residual_benchmark_label": residual_info.get("label"),
                        "stock_residual_benchmark_source": residual_info.get("source"),
                        "stock_residual_benchmark_status": residual_info.get("status"),
                        "stock_residual_benchmark_trade_date": residual_info.get("trade_date"),
                        "stock_residual_benchmark_ticker": residual_info.get("ticker"),
                        "stock_residual_benchmark_market": residual_info.get("market", "US"),
                        "stock_residual_benchmark_key": residual_info.get("key"),
                    }

            result_row, detail_df, summary = estimate_one_fund(
                fund_code=code,
                top_n=top_n,
                manual_returns_pct=manual_returns_pct,
                prefer_intraday=prefer_intraday,
                us_realtime=us_realtime,
                hk_realtime=hk_realtime,
                renormalize_available_holdings=renormalize_available_holdings,
                include_purchase_limit=include_purchase_limit,
                purchase_limit_timeout=purchase_limit_timeout,
                purchase_limit_cache_days=purchase_limit_cache_days,
                holding_cache_days=holding_cache_days,
                cache_enabled=cache_enabled,
                security_return_cache_enabled=security_return_cache_enabled,
                cn_hk_hourly_cache=cn_hk_hourly_cache,
                holding_mode=holding_mode,
                proxy_map=proxy_map,
                proxy_normalize_weights=proxy_normalize_weights,
                valuation_mode=valuation_mode,
                stock_residual_benchmark_return_pct=residual_kwargs.get("stock_residual_benchmark_return_pct"),
                stock_residual_benchmark_label=residual_kwargs.get("stock_residual_benchmark_label"),
                stock_residual_benchmark_source=residual_kwargs.get("stock_residual_benchmark_source"),
                stock_residual_benchmark_status=residual_kwargs.get("stock_residual_benchmark_status"),
                stock_residual_benchmark_trade_date=residual_kwargs.get("stock_residual_benchmark_trade_date"),
                stock_residual_benchmark_ticker=residual_kwargs.get("stock_residual_benchmark_ticker"),
                stock_residual_benchmark_market=residual_kwargs.get("stock_residual_benchmark_market", "US"),
                stock_residual_benchmark_key=residual_kwargs.get("stock_residual_benchmark_key"),
                zero_stale_cn_hk_returns=zero_stale_cn_hk_returns,
                stale_market_estimate_date=stale_market_estimate_date,
                valuation_anchor_date=valuation_anchor_date,
            )

            result_row["_输入顺序"] = i
            rows.append(result_row)

            detail_map[code] = {
                "detail_df": detail_df,
                "summary": summary,
                "error": None,
            }

        except Exception as e:
            error_row = {
                "_输入顺序": i,
                "基金代码": code,
                "基金名称": get_fund_name(code),
                "今日预估涨跌幅": None,
                "_估算方式": "failed",
            }

            if include_purchase_limit:
                error_row["限购金额"] = get_fund_purchase_limit(
                    fund_code=code,
                    timeout=purchase_limit_timeout,
                    cache_days=purchase_limit_cache_days,
                    cache_enabled=cache_enabled,
                )

            rows.append(error_row)

            detail_map[code] = {
                "detail_df": None,
                "summary": None,
                "error": repr(e),
            }

            print(f"[WARN] 基金 {code} 估算失败: {e}")

    result_df = pd.DataFrame(rows)

    if sort_by_return:
        result_df["_排序收益"] = pd.to_numeric(
            result_df["今日预估涨跌幅"],
            errors="coerce",
        )

        result_df = result_df.sort_values(
            by=["_排序收益", "_输入顺序"],
            ascending=[False, True],
            na_position="last",
        ).reset_index(drop=True)

        result_df = result_df.drop(columns=["_排序收益"])
    else:
        result_df = result_df.sort_values("_输入顺序").reset_index(drop=True)

    result_df["序号"] = range(1, len(result_df) + 1)

    cols = ["序号", "基金代码", "基金名称", "今日预估涨跌幅"]

    if include_purchase_limit:
        cols.append("限购金额")

    if include_method_col:
        result_df["估算方式"] = result_df["_估算方式"]
        cols.append("估算方式")

    result_df = result_df[cols]

    return result_df, detail_map


def _write_failed_holdings_report(
    detail_map,
    valuation_anchor_date,
    output_file: str | Path = "output/failed_holdings_latest.txt",
) -> None:
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    anchor = _normalize_trade_date_key(valuation_anchor_date)
    lines = [
        f"valuation_anchor_date: {anchor or '未知'}",
        f"generated_at: {datetime.now().isoformat(timespec='seconds')}",
        "",
    ]

    rows = []
    if isinstance(detail_map, dict):
        for fund_code, item in detail_map.items():
            if not isinstance(item, dict):
                continue

            if item.get("error"):
                rows.append({
                    "fund_code": fund_code,
                    "holding_name": "基金估算失败",
                    "market": "",
                    "ticker": "",
                    "status": "failed",
                    "trade_date": "",
                    "source": "",
                    "error": str(item.get("error")),
                })
                continue

            detail_df = item.get("detail_df")
            if not isinstance(detail_df, pd.DataFrame) or detail_df.empty:
                continue

            for _, row in detail_df.iterrows():
                status = str(row.get("锚点状态", "")).strip().lower()
                source = str(row.get("收益数据源", "")).strip()
                if status not in ANCHOR_BAD_STATUSES and source != "failed":
                    continue
                rows.append({
                    "fund_code": fund_code,
                    "holding_name": str(row.get("股票名称", row.get("name", ""))).strip(),
                    "market": str(row.get("市场", "")).strip(),
                    "ticker": str(row.get("ticker", row.get("code", ""))).strip(),
                    "status": status or "failed",
                    "trade_date": str(row.get("收益交易日", "")).strip(),
                    "source": source,
                    "error": str(row.get("锚点错误", "")).strip(),
                })

    if not rows:
        lines.append("本次无 pending/missing/stale/failed 持仓。")
    else:
        headers = ["fund_code", "holding_name", "market", "ticker", "status", "trade_date", "source", "error"]
        lines.append("\t".join(headers))
        for row in rows:
            lines.append("\t".join(str(row.get(header, "") or "") for header in headers))

    Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")


# 市场基准：直接获取指数涨跌幅。


def fetch_us_index_return_pct_from_rsi_module(symbol, display_name=None, days=15, end_date=None):
    """
    使用 tools/rsi_module.py 中已经验证过的指数行情入口获取美股指数最新完整交易日涨跌幅。

    约定：
        .NDX -> 纳斯达克100
        .INX -> 标普500

    返回：
        return_pct, trade_date, source
    """
    symbol = str(symbol).strip()
    display_name = display_name or symbol

    last_error = None

    import_candidates = [
        ("tools.rsi_module", "get_us_index_akshare"),
        ("tools.rsi_modul", "get_us_index_akshare"),
        ("rsi_module", "get_us_index_akshare"),
        ("rsi_modul", "get_us_index_akshare"),
    ]

    getter = None
    for module_name, func_name in import_candidates:
        try:
            module = __import__(module_name, fromlist=[func_name])
            getter = getattr(module, func_name)
            break
        except Exception as e:
            last_error = e

    if getter is None:
        raise RuntimeError(f"无法导入 rsi_module.get_us_index_akshare: {last_error}")

    try:
        df = getter(
            symbol=symbol,
            days=days,
            cache_dir="cache",
            retry=3,
            use_cache=True,
            allow_eastmoney=False,
            include_realtime=False,
        )
    except TypeError:
        # 兼容旧函数签名
        df = getter(symbol=symbol, days=days)

    if df is None or df.empty:
        raise RuntimeError(f"rsi_module 返回空数据: {display_name}({symbol})")

    out = df.copy()

    rename_map = {
        "日期": "date",
        "收盘": "close",
        "Date": "date",
        "Close": "close",
    }
    out = out.rename(columns=rename_map)

    if "date" not in out.columns or "close" not in out.columns:
        raise RuntimeError(
            f"rsi_module 指数数据缺少 date 或 close 列: {display_name}({symbol}), columns={list(out.columns)}"
        )

    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["close"] = pd.to_numeric(
        out["close"].astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    )
    out = out.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    end_date_key = _normalize_trade_date_key(end_date)
    if end_date_key:
        out = _drop_rows_after_target_date(out, out["date"], end_date_key).reset_index(drop=True)

    if len(out) < 2:
        raise RuntimeError(f"rsi_module 在目标日期 {end_date_key or 'latest'} 前有效收盘点不足: {display_name}({symbol})")

    last_close = float(out.iloc[-1]["close"])
    prev_close = float(out.iloc[-2]["close"])

    if prev_close == 0:
        raise RuntimeError(f"rsi_module 前一收盘价为0: {display_name}({symbol})")

    return_pct = (last_close / prev_close - 1.0) * 100.0
    trade_date = pd.Timestamp(out.iloc[-1]["date"]).strftime("%Y-%m-%d")

    return return_pct, trade_date, "rsi_module_index_daily"


def fetch_us_index_return_pct_yahoo(symbol, display_name=None, retry=2, sleep_seconds=0.8, end_date=None):
    """
    从 Yahoo Finance chart 接口直接获取美股指数最新完整交易日涨跌幅。

    这是备用兜底。常规情况下优先使用 tools/rsi_module.py 的 get_us_index_akshare。
    """
    symbol = str(symbol).strip().upper()
    display_name = display_name or symbol
    encoded_symbol = requests.utils.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}"
    params = {
        "range": "15d",
        "interval": "1d",
        "includePrePost": "false",
        "events": "history",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Referer": "https://finance.yahoo.com/",
    }

    last_error = None

    for i in range(max(1, retry)):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=12)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("chart", {}).get("result", [None])[0]

            if not result:
                raise RuntimeError(f"Yahoo 返回空 result: {display_name}({symbol})")

            timestamps = result.get("timestamp") or []
            quote = (result.get("indicators", {}).get("quote") or [{}])[0]
            closes = quote.get("close") or []

            points = []
            for ts, close in zip(timestamps, closes):
                if close is None:
                    continue
                try:
                    close_f = float(close)
                except Exception:
                    continue
                if close_f > 0:
                    points.append((int(ts), close_f))

            end_date_key = _normalize_trade_date_key(end_date)
            if end_date_key:
                points = [
                    (ts, close)
                    for ts, close in points
                    if datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") <= end_date_key
                ]

            if len(points) < 2:
                raise RuntimeError(f"Yahoo 有效收盘点不足: {display_name}({symbol})")

            prev_ts, prev_close = points[-2]
            last_ts, last_close = points[-1]

            if prev_close == 0:
                raise RuntimeError(f"Yahoo 前一收盘价为0: {display_name}({symbol})")

            return_pct = (last_close / prev_close - 1.0) * 100.0
            trade_date = datetime.utcfromtimestamp(last_ts).strftime("%Y-%m-%d")
            return return_pct, trade_date, "yahoo_index_chart"

        except Exception as e:
            last_error = e
            if i < max(1, retry) - 1:
                time.sleep(sleep_seconds)

    raise RuntimeError(f"指数 {display_name}({symbol}) 获取失败: {last_error}")


def get_us_index_return_pct_cached(symbol, display_name=None, cache_enabled=True, cache_hours=36, valuation_anchor_date=None):
    """
    获取美股指数最新完整交易日涨跌幅，带 JSON 缓存。

    缓存策略：
        - 不用简单日历推断最新美股交易日；
        - 北京时间 07:00 后每天主动检查一次；
        - rsi_module / Yahoo 返回的 trade_date 晚于缓存才更新；
        - trade_date 相同表示周末、美国节假日或上游尚未更新，保留缓存并标记今日已检查；
        - 返回更旧数据时拒绝覆盖旧缓存。
    """
    symbol = str(symbol).strip().upper()
    display_name = display_name or symbol
    anchor = _normalize_trade_date_key(valuation_anchor_date)
    if anchor:
        result = get_security_return_by_anchor_date(
            market="US",
            ticker=symbol,
            valuation_anchor_date=anchor,
            allow_intraday=False,
            security_return_cache_enabled=cache_enabled,
        )
        return_pct, source, trade_date, _status = _return_from_anchor_result(result)
        return float(return_pct or 0.0), trade_date or anchor, source

    today_bucket = datetime.now().strftime("%Y-%m-%d")
    cache_key = f"INDEX:{symbol}:{today_bucket}:last_close"

    cache = _load_json_cache(SECURITY_RETURN_CACHE_FILE, default={}) if cache_enabled else {}
    item = cache.get(cache_key) if cache_enabled else None

    if cache_enabled and item:
        try:
            if _should_use_trade_date_cache_without_refresh(
                item,
                max_age_hours=cache_hours,
            ):
                r_pct, trade_date, source = _cached_index_tuple(item)
                _cache_log(
                    f"使用指数缓存: {display_name}({symbol}) -> {r_pct:+.4f}% "
                    f"trade_date={trade_date}"
                )
                return r_pct, trade_date, source
        except Exception:
            pass

    _cache_log(f"重新获取指数: {display_name}({symbol}) [last_close]")

    errors = []
    fresh_result = None

    try:
        fresh_result = fetch_us_index_return_pct_from_rsi_module(
            symbol=symbol,
            display_name=display_name,
        )
    except Exception as e:
        errors.append(f"rsi_module: {repr(e)}")
        try:
            yahoo_symbol = {
                ".NDX": "^NDX",
                ".INX": "^GSPC",
                ".IXIC": "^IXIC",
                ".DJI": "^DJI",
            }.get(symbol, symbol)
            fresh_result = fetch_us_index_return_pct_yahoo(
                symbol=yahoo_symbol,
                display_name=display_name,
            )
        except Exception as e2:
            errors.append(f"yahoo: {repr(e2)}")

            if cache_enabled and item:
                _cache_log(
                    f"指数刷新失败，使用旧缓存: {display_name}({symbol}), "
                    f"trade_date={item.get('trade_date', '')}, 原因: {' | '.join(errors)}"
                )
                old_entry = dict(item)
                old_entry = _mark_last_close_cache_checked(old_entry)
                cache[cache_key] = old_entry
                _save_security_return_cache(cache)
                return _cached_index_tuple(old_entry)

            raise RuntimeError(f"指数 {display_name}({symbol}) 获取失败: {' | '.join(errors)}")

    r_pct, trade_date, source = fresh_result

    if cache_enabled and item:
        cached_trade_date = item.get("trade_date", "")
        cmp_result = _compare_trade_dates(trade_date, cached_trade_date)

        if cmp_result < 0:
            _cache_log(
                f"拒绝用更旧指数数据覆盖缓存: {display_name}({symbol}), "
                f"fresh={trade_date}, cached={cached_trade_date}"
            )
            old_entry = dict(item)
            old_entry = _mark_last_close_cache_checked(old_entry)
            cache[cache_key] = old_entry
            _save_security_return_cache(cache)
            return _cached_index_tuple(old_entry)

    if cache_enabled:
        entry = {
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "market": "INDEX",
            "ticker": symbol,
            "name": display_name,
            "return_pct": float(r_pct),
            "trade_date": trade_date,
            "source": source,
            "valuation_mode": "last_close",
        }
        entry = _mark_last_close_cache_checked(entry)
        cache[cache_key] = entry
        _save_security_return_cache(cache)

    return r_pct, trade_date, source



def _enabled_market_benchmark_specs():
    """
    返回已启用的海外基金图基准配置。

    配置维护入口是 tools/configs/market_benchmark_configs.py。这里不写死纳指/
    标普，方便后续继续加入 XOP、VIX、费城半导体、伦敦金等观察项。
    """
    specs = []
    for order, item in enumerate(MARKET_BENCHMARK_ITEMS, start=1):
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled", True)):
            continue

        label = str(item.get("label", "")).strip()
        ticker = str(item.get("ticker", "")).strip()
        kind = str(item.get("kind", "")).strip().lower()
        if not label or not ticker or not kind:
            continue

        specs.append({
            "order": order,
            "label": label,
            "symbol": ticker.upper(),
            "kind": kind,
            "fallback_ticker": str(item.get("fallback_ticker", "")).strip().upper(),
        })
    return specs


def _configured_benchmark_status_from_trade_date(trade_date, valuation_anchor_date) -> str:
    anchor = _normalize_trade_date_key(valuation_anchor_date)
    trade = _normalize_trade_date_key(trade_date)
    if not anchor:
        return "traded" if trade else "missing"
    if trade == anchor:
        return "traded"
    if trade:
        return "stale"
    return "missing"


def _get_yahoo_benchmark_return_by_anchor_date(symbol, valuation_anchor_date, cache_enabled=True) -> dict:
    """
    Yahoo 特殊基准的估值日缓存读取器。

    用途：
    - VIX 这类配置为 kind="yahoo" 的基准，直接走 Yahoo
      完整日线，不再额外尝试新浪或东方财富；
    - 同一 ticker + valuation_anchor_date 写入 security_return_cache.json，
      避免 safe/节假日流程重复请求。
    """
    cache_key, ticker_norm, anchor = _anchor_security_cache_key("YAHOO", symbol, valuation_anchor_date)

    if not anchor:
        return _anchor_return_result(
            market="YAHOO",
            ticker=ticker_norm,
            valuation_anchor_date=anchor,
            status="missing",
            source="anchor_missing",
            error="valuation_anchor_date 为空",
        )

    if cache_enabled:
        cached = _SECURITY_RETURN_RUNTIME_CACHE.get(cache_key)
        if isinstance(cached, dict) and _is_anchor_cache_entry_fresh(cached):
            return dict(cached)

        cache = _load_json_cache(SECURITY_RETURN_CACHE_FILE, default={})
        item = cache.get(cache_key) if isinstance(cache, dict) else None
        if isinstance(item, dict) and _is_anchor_cache_entry_fresh(item):
            _SECURITY_RETURN_RUNTIME_CACHE[cache_key] = dict(item)
            return dict(item)

    try:
        return_pct, trade_date, source = fetch_us_stock_return_pct_yahoo_daily_with_date(
            ticker_norm,
            end_date=anchor,
        )
        status = _configured_benchmark_status_from_trade_date(trade_date, anchor)
        entry = _anchor_return_result(
            market="YAHOO",
            ticker=ticker_norm,
            valuation_anchor_date=anchor,
            status=status,
            return_pct=return_pct if status == "traded" else 0.0,
            trade_date=trade_date,
            source=source,
            calendar_is_open=None,
            error="" if status == "traded" else f"trade_date={trade_date} 与 valuation_anchor_date={anchor} 不一致",
        )
    except Exception as exc:
        entry = _anchor_return_result(
            market="YAHOO",
            ticker=ticker_norm,
            valuation_anchor_date=anchor,
            status="missing",
            return_pct=0.0,
            trade_date="",
            source="yahoo_chart_daily_failed",
            calendar_is_open=None,
            error=str(exc),
        )

    if cache_enabled:
        _save_anchor_security_cache_entry(cache_key, entry)
    return entry


def _get_foreign_futures_benchmark_return_by_anchor_date(
    symbol,
    valuation_anchor_date,
    *,
    fallback_ticker=None,
    cache_enabled=True,
) -> dict:
    """
    外盘期货 / 贵金属基准的估值日缓存读取器。

    当前用于伦敦金：优先新浪外盘期货 XAU，失败后回退东方财富国际期货
    GC00Y。缓存 key 仍按配置主 ticker + valuation_anchor_date 保存，避免
    safe/节假日流程重复请求。
    """
    cache_key, ticker_norm, anchor = _anchor_security_cache_key(
        "FOREIGN_FUTURES",
        symbol,
        valuation_anchor_date,
    )

    if not anchor:
        return _anchor_return_result(
            market="FOREIGN_FUTURES",
            ticker=ticker_norm,
            valuation_anchor_date=anchor,
            status="missing",
            source="anchor_missing",
            error="valuation_anchor_date 为空",
        )

    if cache_enabled:
        cached = _SECURITY_RETURN_RUNTIME_CACHE.get(cache_key)
        if isinstance(cached, dict) and _is_anchor_cache_entry_fresh(cached):
            return dict(cached)

        cache = _load_json_cache(SECURITY_RETURN_CACHE_FILE, default={})
        item = cache.get(cache_key) if isinstance(cache, dict) else None
        if isinstance(item, dict) and _is_anchor_cache_entry_fresh(item):
            _SECURITY_RETURN_RUNTIME_CACHE[cache_key] = dict(item)
            return dict(item)

    try:
        return_pct, trade_date, source = fetch_foreign_futures_return_pct_with_date(
            ticker_norm,
            end_date=anchor,
            fallback_ticker=fallback_ticker,
        )
        status = _configured_benchmark_status_from_trade_date(trade_date, anchor)
        entry = _anchor_return_result(
            market="FOREIGN_FUTURES",
            ticker=ticker_norm,
            valuation_anchor_date=anchor,
            status=status,
            return_pct=return_pct if status == "traded" else 0.0,
            trade_date=trade_date,
            source=source,
            calendar_is_open=None,
            error="" if status == "traded" else f"trade_date={trade_date} 与 valuation_anchor_date={anchor} 不一致",
        )
    except Exception as exc:
        entry = _anchor_return_result(
            market="FOREIGN_FUTURES",
            ticker=ticker_norm,
            valuation_anchor_date=anchor,
            status="missing",
            return_pct=0.0,
            trade_date="",
            source="foreign_futures_daily_failed",
            calendar_is_open=None,
            error=str(exc),
        )

    if cache_enabled:
        _save_anchor_security_cache_entry(cache_key, entry)
    return entry


def _fetch_configured_market_benchmark(spec: dict, cache_enabled=True, valuation_anchor_date=None) -> dict:
    label = str(spec.get("label", "基准")).strip() or "基准"
    symbol = str(spec.get("symbol", "")).strip().upper()
    kind = str(spec.get("kind", "")).strip().lower()
    fallback_ticker = str(spec.get("fallback_ticker", "")).strip().upper()
    order = int(spec.get("order", 999) or 999)
    anchor = _normalize_trade_date_key(valuation_anchor_date)

    if not symbol:
        raise RuntimeError(f"基准 {label} 缺少 ticker")

    if kind == "us_index":
        if anchor:
            anchor_result = get_security_return_by_anchor_date(
                market="US",
                ticker=symbol,
                valuation_anchor_date=anchor,
                allow_intraday=False,
                security_return_cache_enabled=cache_enabled,
            )
            return_pct, source, trade_date, status = _return_from_anchor_result(anchor_result)
        else:
            return_pct, trade_date, source = get_us_index_return_pct_cached(
                symbol=symbol,
                display_name=label,
                cache_enabled=cache_enabled,
            )
            status = _configured_benchmark_status_from_trade_date(trade_date, anchor)
    elif kind == "us_security":
        if anchor:
            anchor_result = get_security_return_by_anchor_date(
                market="US",
                ticker=symbol,
                valuation_anchor_date=anchor,
                allow_intraday=False,
                security_return_cache_enabled=cache_enabled,
            )
            return_pct, source, trade_date, status = _return_from_anchor_result(anchor_result)
        else:
            return_pct, trade_date, source = fetch_us_return_pct_daily_with_date(symbol)
            status = _configured_benchmark_status_from_trade_date(trade_date, anchor)
    elif kind == "yahoo":
        # Yahoo 用于 VIX 等特殊海外资产；仍然只取日线收盘数据。
        if anchor:
            anchor_result = _get_yahoo_benchmark_return_by_anchor_date(
                symbol,
                valuation_anchor_date=anchor,
                cache_enabled=cache_enabled,
            )
            return_pct, source, trade_date, status = _return_from_anchor_result(anchor_result)
        else:
            return_pct, trade_date, source = fetch_us_stock_return_pct_yahoo_daily_with_date(symbol)
            status = _configured_benchmark_status_from_trade_date(trade_date, anchor)
    elif kind == "foreign_futures":
        # 用于伦敦金等海外贵金属，优先国内更友好的新浪/东方财富日线。
        if anchor:
            anchor_result = _get_foreign_futures_benchmark_return_by_anchor_date(
                symbol,
                valuation_anchor_date=anchor,
                fallback_ticker=fallback_ticker,
                cache_enabled=cache_enabled,
            )
            return_pct, source, trade_date, status = _return_from_anchor_result(anchor_result)
        else:
            return_pct, trade_date, source = fetch_foreign_futures_return_pct_with_date(
                symbol,
                fallback_ticker=fallback_ticker,
            )
            status = _configured_benchmark_status_from_trade_date(trade_date, anchor)
    else:
        raise RuntimeError(f"未知基准 kind={kind!r}，请检查 market_benchmark_configs.py")

    display_return_pct = return_pct if str(status).strip().lower() in ANCHOR_COMPLETE_STATUSES else None
    return {
        "order": order,
        "label": label,
        "symbol": symbol,
        "kind": kind,
        "fallback_ticker": fallback_ticker,
        "return_pct": display_return_pct,
        "trade_date": trade_date,
        "source": source,
        "status": status,
        "valuation_anchor_date": anchor,
        "error": None,
    }


def get_us_index_benchmark_items(cache_enabled=True, valuation_anchor_date=None):
    """
    获取海外表底部基准信息。

    基准清单来自 tools/configs/market_benchmark_configs.py，不再写死纳斯达克100
    和标普500。单个基准失败只返回失败行，不影响基金估算或图片生成。
    所有基准涨跌幅都取完整日线收盘数据，不使用盘中实时行情。
    """
    items = []
    anchor = _normalize_trade_date_key(valuation_anchor_date)

    for spec in _enabled_market_benchmark_specs():
        label = str(spec.get("label", "基准"))
        symbol = str(spec.get("symbol", "")).strip().upper()
        try:
            items.append(
                _fetch_configured_market_benchmark(
                    spec,
                    cache_enabled=cache_enabled,
                    valuation_anchor_date=anchor,
                )
            )
        except Exception as e:
            print(f"[WARN] 指数基准 {label}({symbol}) 获取失败: {e}", flush=True)
            items.append({
                "order": int(spec.get("order", 999) or 999),
                "label": label,
                "symbol": symbol,
                "kind": str(spec.get("kind", "")).strip().lower(),
                "return_pct": None,
                "trade_date": "",
                "source": "failed",
                "status": "missing",
                "valuation_anchor_date": anchor,
                "error": str(e),
            })

    return items


# 表格打印与图片输出。

def print_fund_estimate_table(result_df, title=None, pct_digits=4):
    """
    打印多基金估算结果。
    """
    if title is None:
        title = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    show_df = result_df.copy()
    show_df["今日预估涨跌幅"] = show_df["今日预估涨跌幅"].map(lambda x: format_pct(x, digits=pct_digits))

    print("=" * 100)
    print(title)
    print("=" * 100)
    print(show_df.to_string(index=False))
    print("=" * 100)


def _build_daily_benchmark_table_rows(benchmark_footer_items, pct_digits=2):
    rows = []
    raw_values = []
    for index, item in enumerate(list(benchmark_footer_items or []), start=1):
        if not isinstance(item, dict):
            continue

        value = _safe_float_or_none(item.get("return_pct"))
        raw_values.append(value)
        trade_date = _normalize_date_string(item.get("trade_date")) or _normalize_date_string(
            item.get("valuation_anchor_date")
        )
        rows.append(
            {
                "序号": index,
                "指数名称": str(item.get("label", item.get("symbol", "基准"))).strip() or "基准",
                "模型观察": format_pct(value, digits=pct_digits) if value is not None else "获取失败",
                "基准日或区间": trade_date or "--",
            }
        )

    if not rows:
        return pd.DataFrame(columns=["序号", "指数名称", "模型观察", "基准日或区间"]), []

    return pd.DataFrame(rows, columns=["序号", "指数名称", "模型观察", "基准日或区间"]), raw_values


def _draw_benchmark_table(
    ax,
    benchmark_df,
    raw_values,
    bbox,
    *,
    fontsize,
    header_bg,
    header_text_color,
    grid_color,
    up_color,
    down_color,
    neutral_color,
    column_widths=None,
):
    table = ax.table(
        cellText=benchmark_df.values,
        colLabels=benchmark_df.columns,
        cellLoc="center",
        colLoc="center",
        bbox=bbox,
        zorder=2,
    )
    table.auto_set_font_size(False)
    table.set_fontsize(fontsize)
    table.scale(1.0, 1.18)

    value_col_idx = None
    for candidate in ("模型观察", "区间模型观察"):
        if candidate in benchmark_df.columns:
            value_col_idx = list(benchmark_df.columns).index(candidate)
            break
    default_width_by_name = {
        "序号": 0.08,
        "指数名称": 0.34,
        "模型观察": 0.20,
        "基准日或区间": 0.38,
    }
    if column_widths is not None and len(column_widths) != len(benchmark_df.columns):
        column_widths = None

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(grid_color)
        cell.set_linewidth(0.8)

        if row == 0:
            cell.set_facecolor(header_bg)
            cell.set_text_props(color=header_text_color, weight="bold")
        else:
            cell.set_facecolor("white")
            if value_col_idx is not None and col == value_col_idx:
                raw_val = raw_values[row - 1] if row - 1 < len(raw_values) else None
                if raw_val is None or pd.isna(raw_val):
                    cell.get_text().set_color(neutral_color)
                elif float(raw_val) > 0:
                    cell.get_text().set_color(up_color)
                    cell.get_text().set_weight("bold")
                elif float(raw_val) < 0:
                    cell.get_text().set_color(down_color)
                    cell.get_text().set_weight("bold")
                else:
                    cell.get_text().set_color(neutral_color)

        if col < len(benchmark_df.columns):
            col_name = benchmark_df.columns[col]
            if column_widths is not None:
                cell.set_width(column_widths[col])
            elif col_name in default_width_by_name:
                cell.set_width(default_width_by_name[col_name])

    return table


def save_fund_estimate_table_image(
    result_df,
    output_file="output/fund_estimate_table.png",
    title=None,
    title_segments=None,
    dpi=180,
    watermark_text="鱼师AHNS",
    watermark_alpha=0.15,
    watermark_fontsize=32,
    watermark_rotation=28,
    watermark_rows=5,
    watermark_cols=4,
    watermark_color="#050505",
    watermark_zorder=3,
    up_color="red",
    down_color="green",
    neutral_color="black",
    pct_digits=4,
    header_bg="#3f4d66",
    header_text_color="white",
    grid_color="#d9d9d9",
    figure_width=None,
    row_height=0.45,
    footnote_text="依据基金季度报告前十大持仓股及指数估算，仅供学习记录，不构成投资建议；最终以基金公司更新为准。",
    footnote_color="#666666",
    footnote_fontsize=15,
    compliance_notice_text="个人模型，数据来源于网络公开资料，不构成任何投资建议",
    compliance_notice_color="#2f3b52",
    compliance_notice_fontsize=20,
    compliance_notice_fontweight="bold",
    benchmark_footer_items=None,
    benchmark_footer_fontsize=15,
    display_column_names=None,
    title_fontsize=20,
    title_color="black",
    title_fontweight="bold",
    title_gap_ratio=0.05,
    title_gap_min=0.010,
    title_gap_max=0.020,
    footnote_gap_ratio=0.10,
    footnote_gap_min=0.008,
    footnote_gap_max=0.026,
    pad_inches=0.12,
):
    """
    保存基金预估收益表格图片。

    标题和备注按表格真实边界自适应定位：
        1. 先绘制表格；
        2. 读取表格真实边界；
        3. 标题自动贴近表格上沿；
        4. 备注自动贴近表格下沿；

    今日预估涨跌幅：
        正数：up_color
        负数：down_color
        0 或失败：neutral_color

    title_gap_* 和 footnote_gap_* 控制标题、备注与表格边界的自适应距离。
    """
    setup_chinese_font()

    if title is None:
        title = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    output_path = Path(output_file)
    if output_path.parent and str(output_path.parent) != ".":
        output_path.parent.mkdir(parents=True, exist_ok=True)

    estimate_col_name = "今日预估涨跌幅"
    display_column_names = display_column_names or {}
    estimate_display_col_name = display_column_names.get(estimate_col_name, estimate_col_name)

    table_df = result_df.copy()
    table_df[estimate_col_name] = table_df[estimate_col_name].map(
        lambda x: format_pct(x, digits=pct_digits)
    )
    table_df = table_df.rename(columns=display_column_names)

    benchmark_table_df, benchmark_raw_values = _build_daily_benchmark_table_rows(
        benchmark_footer_items,
        pct_digits=2,
    )
    has_benchmark_footer = not benchmark_table_df.empty

    nrows = len(table_df)
    ncols = len(table_df.columns)
    has_compliance_notice = bool(str(compliance_notice_text).strip()) if compliance_notice_text else False

    # 画布高度随行数增长，避免标题/备注离表格过远。
    benchmark_height_units = row_height * (len(benchmark_table_df) + 1) if has_benchmark_footer else 0.0
    footer_height_units = 0.85 if has_compliance_notice and footnote_text else (
        0.55 if (has_compliance_notice or footnote_text) else 0.25
    )
    fig_h = max(
        1.8,
        row_height * (nrows + 1)
        + benchmark_height_units
        + footer_height_units
        + (0.35 if has_benchmark_footer else 0.0),
    )

    if figure_width is None:
        fig_w = 14.0 if ncols >= 5 else 12.5
    else:
        fig_w = figure_width

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.axis("off")

    # 让轴区域占据大部分画布；标题和备注位置后续根据表格边界计算。
    fig.subplots_adjust(left=0.015, right=0.985, top=0.985, bottom=0.015)

    # 先不直接画水印；等表格创建完成后，在表格区域内部平铺多个浅色水印。
    watermark_artists = []

    # 为标题和备注预留很小的区域，主体交给表格
    top_reserved = 0.08 if title else 0.03
    footer_reserved = 0.070 if has_compliance_notice and footnote_text else (
        0.050 if (has_compliance_notice or footnote_text) else 0.025
    )
    benchmark_reserved = min(max(benchmark_height_units / max(fig_h, 0.01), 0.16), 0.26) if has_benchmark_footer else 0.0
    benchmark_gap = 0.010 if has_benchmark_footer else 0.0
    bottom_reserved = footer_reserved + benchmark_reserved + benchmark_gap

    table_bbox = [0.02, bottom_reserved, 0.96, 1 - top_reserved - bottom_reserved]
    benchmark_bbox = [0.08, footer_reserved, 0.84, benchmark_reserved] if has_benchmark_footer else None

    table = ax.table(
        cellText=table_df.values,
        colLabels=table_df.columns,
        cellLoc="center",
        colLoc="center",
        bbox=table_bbox,
        zorder=2,
    )

    table.auto_set_font_size(False)
    table.set_fontsize(17)
    table.scale(1.0, 1.22)

    est_col_idx = list(table_df.columns).index(estimate_display_col_name)

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(grid_color)
        cell.set_linewidth(0.8)

        if row == 0:
            cell.set_facecolor(header_bg)
            cell.set_text_props(color=header_text_color, weight="bold")
        else:
            cell.set_facecolor("white")

            if col == est_col_idx:
                raw_val = result_df.iloc[row - 1][estimate_col_name]

                if raw_val is not None and not pd.isna(raw_val):
                    if raw_val > 0:
                        cell.get_text().set_color(up_color)
                        cell.get_text().set_weight("bold")
                    elif raw_val < 0:
                        cell.get_text().set_color(down_color)
                        cell.get_text().set_weight("bold")
                    else:
                        cell.get_text().set_color(neutral_color)
                else:
                    cell.get_text().set_color(neutral_color)

    # 按列名动态设置列宽
    col_width_by_name = {
        "序号": 0.06,
        "基金代码": 0.10,
        "基金名称": 0.37,
        "今日预估涨跌幅": 0.15,
        "模型估算观察": 0.15,
        "模型观察限购信息": 0.18,
        "限购金额": 0.15,
        "估算方式": 0.16,
    }

    for (row, col), cell in table.get_celld().items():
        if col < len(table_df.columns):
            col_name = table_df.columns[col]
            if col_name in col_width_by_name:
                cell.set_width(col_width_by_name[col_name])

    same_column_count_as_main = has_benchmark_footer and len(benchmark_table_df.columns) == len(table_df.columns)
    benchmark_column_widths = None
    if same_column_count_as_main:
        # 当基准表和主表都是 4 列时，按位置复用主表列宽。
        # 这样 safe_fund / 节后单日图里上下两张表的列边界会尽量对齐。
        benchmark_bbox = list(table_bbox)
        benchmark_bbox[1] = footer_reserved
        benchmark_bbox[3] = benchmark_reserved
        benchmark_column_widths = [
            table.get_celld()[(0, col)].get_width()
            for col in range(len(table_df.columns))
        ]

    benchmark_table_artist = None
    separator_artists = []
    if has_benchmark_footer and benchmark_bbox is not None:
        benchmark_table_artist = _draw_benchmark_table(
            ax,
            benchmark_table_df,
            benchmark_raw_values,
            benchmark_bbox,
            fontsize=17,
            header_bg=header_bg,
            header_text_color=header_text_color,
            grid_color=grid_color,
            up_color=up_color,
            down_color=down_color,
            neutral_color=neutral_color,
            column_widths=benchmark_column_widths,
        )
        separator_y = benchmark_bbox[1] + benchmark_bbox[3] + max(benchmark_gap * 0.5, 0.003)
        separator_artists.extend(
            ax.plot(
                [table_bbox[0], table_bbox[0] + table_bbox[2]],
                [separator_y, separator_y],
                transform=ax.transAxes,
                color=header_bg,
                linewidth=1.2,
                alpha=0.45,
                zorder=4,
            )
        )

    # 水印画在表格区域内部，透明度较低但保证可见。
    if watermark_text and watermark_rows > 0 and watermark_cols > 0:
        table_left, table_bottom, table_width, table_height = table_bbox

        for r in range(int(watermark_rows)):
            for c in range(int(watermark_cols)):
                x = table_left + table_width * (c + 0.5) / float(watermark_cols)
                y = table_bottom + table_height * (r + 0.5) / float(watermark_rows)

                wm = ax.text(
                    x,
                    y,
                    watermark_text,
                    transform=ax.transAxes,
                    fontsize=watermark_fontsize,
                    color=watermark_color,
                    alpha=watermark_alpha,
                    ha="center",
                    va="center",
                    rotation=watermark_rotation,
                    zorder=watermark_zorder,
                )
                wm.set_in_layout(False)
                watermark_artists.append(wm)

    # 自适应标题与备注位置。
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()

    bbox_disp = table.get_window_extent(renderer=renderer)
    bbox_fig = bbox_disp.transformed(fig.transFigure.inverted())

    table_top = bbox_fig.y1
    table_bottom = bbox_fig.y0
    table_height = max(table_top - table_bottom, 0.01)
    benchmark_bottom = table_bottom
    if benchmark_table_artist is not None:
        benchmark_bbox_disp = benchmark_table_artist.get_window_extent(renderer=renderer)
        benchmark_bbox_fig = benchmark_bbox_disp.transformed(fig.transFigure.inverted())
        benchmark_bottom = benchmark_bbox_fig.y0

    title_gap = max(
        min(table_height * title_gap_ratio, title_gap_max),
        title_gap_min,
    )
    footnote_gap = max(
        min(table_height * footnote_gap_ratio, footnote_gap_max),
        footnote_gap_min,
    )

    title_artist = None
    bottom_block_artist = None

    if title_segments:
        title_y = min(table_top + title_gap, 0.985)
        title_children = []
        for segment in title_segments:
            if isinstance(segment, dict):
                segment_text = str(segment.get("text", ""))
                if not segment_text:
                    continue
                segment_color = segment.get("color", title_color)
                segment_fontweight = segment.get("fontweight", title_fontweight)
                segment_fontsize = segment.get("fontsize", title_fontsize)
            else:
                segment_text = str(segment)
                if not segment_text:
                    continue
                segment_color = title_color
                segment_fontweight = title_fontweight
                segment_fontsize = title_fontsize

            title_children.append(
                TextArea(
                    segment_text,
                    textprops={
                        "fontsize": segment_fontsize,
                        "color": segment_color,
                        "fontweight": segment_fontweight,
                    },
                )
            )

        if title_children:
            title_pack = HPacker(children=title_children, align="center", pad=0, sep=2)
            title_artist = AnchoredOffsetbox(
                loc="lower center",
                child=title_pack,
                pad=0,
                frameon=False,
                bbox_to_anchor=(0.5, title_y),
                bbox_transform=fig.transFigure,
                borderpad=0,
            )
            fig.add_artist(title_artist)
    elif title:
        title_y = min(table_top + title_gap, 0.985)
        title_artist = fig.text(
            0.5,
            title_y,
            title,
            ha="center",
            va="bottom",
            fontsize=title_fontsize,
            color=title_color,
            fontweight=title_fontweight,
        )

    # 底部免责声明和备注作为一个整体排版；基准已经单独绘制成表格。
    bottom_children = []
    if has_compliance_notice:
        bottom_children.append(
            TextArea(
                str(compliance_notice_text).strip(),
                textprops={
                    "fontsize": compliance_notice_fontsize,
                    "color": compliance_notice_color,
                    "fontweight": compliance_notice_fontweight,
                },
            )
        )

    if footnote_text:
        footnote_display_text = str(footnote_text).strip()
        if footnote_display_text and not footnote_display_text.startswith("备注"):
            footnote_display_text = f"备注：{footnote_display_text}"
        bottom_children.append(
            TextArea(
                footnote_display_text,
                textprops={"fontsize": footnote_fontsize, "color": footnote_color},
            )
        )

    if bottom_children:
        bottom_pack = VPacker(children=bottom_children, align="center", pad=0, sep=4)
        bottom_block_y = max(
            benchmark_bottom - max(min(table_height * 0.012, 0.010), 0.006),
            0.030,
        )
        bottom_block_artist = AnchoredOffsetbox(
            loc="upper center",
            child=bottom_pack,
            pad=0.0,
            frameon=False,
            bbox_to_anchor=(0.5, bottom_block_y),
            bbox_transform=fig.transFigure,
            borderpad=0.0,
        )
        fig.add_artist(bottom_block_artist)

    # 使用 bbox_extra_artists 确保标题和备注被纳入裁剪范围，避免 tight_layout 推远元素。
    extra_artists = []
    if title_artist is not None:
        extra_artists.append(title_artist)
    if bottom_block_artist is not None:
        extra_artists.append(bottom_block_artist)
    extra_artists.extend(separator_artists)
    extra_artists.extend(watermark_artists)

    fig.savefig(
        output_file,
        dpi=dpi,
        bbox_inches="tight",
        bbox_extra_artists=extra_artists,
        pad_inches=pad_inches,
    )
    plt.close(fig)

    print(f"基金预估收益表格图片已保存: {output_file}")

def build_benchmark_rows(
    benchmark_components,
    include_purchase_limit=True,
    manual_returns_pct=None,
    prefer_intraday=True,
    us_realtime=False,
    hk_realtime=False,
    security_return_cache_enabled=True,
    cn_hk_hourly_cache=True,
    valuation_mode="last_close",
):
    """
    构造市场基准行，用于追加到基金收益表。
    """
    if not benchmark_components:
        return []

    rows = []
    for i, comp in enumerate(benchmark_components, start=1):
        name = str(comp.get("name", comp.get("code", f"基准{i}"))).strip()
        code = str(comp.get("code", "")).strip()
        market = str(comp.get("market", "US")).strip().upper()
        display_code = str(comp.get("display_code", code)).strip()

        try:
            r_pct, source = get_stock_return_pct(
                market=market,
                ticker=code,
                manual_returns_pct=manual_returns_pct,
                prefer_intraday=prefer_intraday,
                us_realtime=us_realtime,
                hk_realtime=hk_realtime,
                security_return_cache_enabled=security_return_cache_enabled,
                cn_hk_hourly_cache=cn_hk_hourly_cache,
                valuation_mode=valuation_mode,
            )
            method = f"benchmark_{source}"
        except Exception as e:
            print(f"[WARN] 市场基准 {name}({code}) 获取失败: {e}", flush=True)
            r_pct = None
            method = "benchmark_failed"

        row = {
            "基金代码": display_code,
            "基金名称": name,
            "今日预估涨跌幅": r_pct,
            "_估算方式": method,
        }

        if include_purchase_limit:
            row["限购金额"] = "—"

        rows.append(row)

    return rows


def _attach_benchmark_rows_to_result_df(
    result_df,
    benchmark_rows,
    include_purchase_limit=True,
    include_method_col=False,
    benchmark_position="top",
):
    """
    将市场基准行追加到收益表，并重新编号。
    """
    if not benchmark_rows:
        return result_df

    bench_df = pd.DataFrame(benchmark_rows)

    cols = ["序号", "基金代码", "基金名称", "今日预估涨跌幅"]
    if include_purchase_limit:
        cols.append("限购金额")
    if include_method_col:
        bench_df["估算方式"] = bench_df.get("_估算方式", "benchmark")
        cols.append("估算方式")

    for col in cols:
        if col not in bench_df.columns:
            bench_df[col] = "" if col != "今日预估涨跌幅" else None
        if col not in result_df.columns:
            result_df[col] = "" if col != "今日预估涨跌幅" else None

    bench_df = bench_df[cols]
    result_df = result_df[cols]

    if str(benchmark_position).strip().lower() == "bottom":
        out = pd.concat([result_df, bench_df], ignore_index=True)
    else:
        out = pd.concat([bench_df, result_df], ignore_index=True)

    out["序号"] = range(1, len(out) + 1)
    return out




def _is_overseas_fund_table_context(title=None, output_file=None) -> bool:
    """
    判断当前表格是否为“海外市场基金收益表”。

    只用于自动启用海外股票持仓型基金的剩余仓位配置基准补偿口径。
    不改变国内基金表，不改变 DEFAULT_FUND_PROXY_MAP 中 ETF/FOF/指数代理基金的计算逻辑。
    """
    texts = []
    if title is not None:
        texts.append(str(title))
    if output_file is not None:
        texts.append(str(output_file))

    joined = " ".join(texts).lower()
    return any(key in joined for key in ["海外", "haiwai", "global", "qdii", "oversea", "overseas"])


def _is_domestic_fund_table_context(title=None, output_file=None) -> bool:
    """
    判断当前表格是否为“国内市场基金收益表”。

    仅用于把 main.py 生成的国内基金表同步写入基金级每日预估缓存，
    供 safe_fund.py 后续只读缓存绘制公开展示图。
    """
    texts = []
    if title is not None:
        texts.append(str(title))
    if output_file is not None:
        texts.append(str(output_file))

    joined = " ".join(texts).lower()
    return any(key in joined for key in ["国内", "guonei", "domestic", "china"])



# 海外/国内基金每日估算缓存。

OVERSEAS_ESTIMATE_FINAL_HOUR_BJ = 15
OVERSEAS_ESTIMATE_FINAL_MINUTE_BJ = 30
DOMESTIC_ESTIMATE_FINAL_HOUR_BJ = 15
DOMESTIC_ESTIMATE_FINAL_MINUTE_BJ = 30


def _normalize_date_string(value) -> str:
    """
    将日期字段规范化为 YYYY-MM-DD。失败返回空字符串。
    """
    if value is None:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    try:
        dt = pd.to_datetime(text, errors="coerce")
        if pd.isna(dt):
            return ""
        return pd.Timestamp(dt).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _is_after_overseas_estimate_freeze_time(now=None) -> bool:
    """
    判断是否已经达到海外基金预估收益缓存的冻结时间。

    规则：
        - 北京时间 15:30 前：同一基金、同一 valuation_date 可反复覆盖；
        - 北京时间 15:30 后：第一次写入或从 intraday 升级为 final；
        - 已有 final 后保持原记录。
    """
    if now is None:
        now = datetime.now()

    return (int(now.hour), int(now.minute)) >= (
        int(OVERSEAS_ESTIMATE_FINAL_HOUR_BJ),
        int(OVERSEAS_ESTIMATE_FINAL_MINUTE_BJ),
    )


def _is_after_domestic_estimate_freeze_time(now=None) -> bool:
    """
    判断是否已经达到国内基金预估收益缓存的冻结时间。

    国内基金缓存按北京时间运行日写入，15:30 后视为 final；
    已有 final 后保持原记录。
    """
    if now is None:
        now = datetime.now()

    return (int(now.hour), int(now.minute)) >= (
        int(DOMESTIC_ESTIMATE_FINAL_HOUR_BJ),
        int(DOMESTIC_ESTIMATE_FINAL_MINUTE_BJ),
    )


def _extract_valuation_date_from_benchmark_footer_items(benchmark_footer_items) -> str:
    """
    从海外表底部指数基准中提取实际 valuation_date。

    优先使用纳斯达克100 / .NDX 的 trade_date；
    如果缺失，则使用第一个有效 trade_date。
    """
    if not benchmark_footer_items:
        return ""

    items = list(benchmark_footer_items)

    # 优先取纳斯达克100 / .NDX
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", ""))
        symbol = str(item.get("symbol", "")).upper()
        if "纳斯达克100" in label or symbol in {".NDX", "^NDX"}:
            dt = _normalize_date_string(item.get("trade_date"))
            if dt:
                return dt

    # 兜底取任一有效 trade_date
    for item in items:
        if not isinstance(item, dict):
            continue
        dt = _normalize_date_string(item.get("trade_date"))
        if dt:
            return dt

    return ""


def _extract_date_from_text(text_value) -> str:
    """
    从类似 '纳斯达克100(2026-04-30)' 的文本中提取日期。
    """
    if not text_value:
        return ""

    m = re.search(r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2})", str(text_value))
    if not m:
        return ""

    return _normalize_date_string(m.group(1))


def _resolve_overseas_estimate_valuation_date(
    benchmark_footer_items=None,
    stock_residual_benchmark_label=None,
) -> str:
    """
    解析海外基金每日预估收益缓存使用的 valuation_date。

    优先级：
        1. 海外表底部 benchmark_footer_items 中的纳斯达克100 trade_date；
        2. stock_residual_benchmark_label 中的日期；
        3. 本机日期兜底。
    """
    valuation_date = _extract_valuation_date_from_benchmark_footer_items(
        benchmark_footer_items
    )

    if valuation_date:
        return valuation_date

    valuation_date = _extract_date_from_text(stock_residual_benchmark_label)
    if valuation_date:
        return valuation_date

    return datetime.now().strftime("%Y-%m-%d")


def _safe_float_or_none(value):
    """
    转成 float；失败或 NaN 返回 None。
    """
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _estimate_data_status_rank(status) -> int:
    return {
        "failed": 0,
        "stale": 1,
        "partial": 2,
        "complete": 3,
    }.get(str(status or "").strip().lower(), 0)


def _market_status_bad_count(record: dict) -> int:
    statuses = record.get("market_status", {}) if isinstance(record, dict) else {}
    if not isinstance(statuses, dict):
        return 999
    return sum(1 for value in statuses.values() if str(value).strip().lower() in ANCHOR_BAD_STATUSES)


def _should_replace_estimate_record(old_record, new_record: dict) -> bool:
    if not isinstance(old_record, dict):
        return True

    old_has_return = _safe_float_or_none(old_record.get("estimate_return_pct")) is not None
    new_has_return = _safe_float_or_none(new_record.get("estimate_return_pct")) is not None
    if new_has_return and not old_has_return:
        return True
    if old_has_return and not new_has_return:
        return False

    old_status_rank = _estimate_data_status_rank(old_record.get("data_status"))
    new_status_rank = _estimate_data_status_rank(new_record.get("data_status"))
    if new_status_rank != old_status_rank:
        return new_status_rank > old_status_rank

    old_score = _safe_float_or_none(old_record.get("completeness_score"))
    new_score = _safe_float_or_none(new_record.get("completeness_score"))
    old_score = -1.0 if old_score is None else float(old_score)
    new_score = -1.0 if new_score is None else float(new_score)
    if abs(new_score - old_score) > 1e-9:
        return new_score > old_score

    old_bad = _market_status_bad_count(old_record)
    new_bad = _market_status_bad_count(new_record)
    if new_bad != old_bad:
        return new_bad < old_bad

    return True


def _write_overseas_fund_estimate_history_cache(
    result_df,
    detail_map,
    valuation_date,
    title=None,
    output_file=None,
    cache_enabled=True,
):
    """
    写入海外基金级每日预估收益缓存。

    注意：
        - 只由海外市场收益表调用；
        - 国内基金表不写入此缓存；
        - key = overseas:fund_code:valuation_date；
        - 15:30 前允许同一 key 反复覆盖；
        - 15:30 后可将 intraday 升级为 final；
        - 已有 final 后保持原记录。
    """
    if not cache_enabled:
        return {
            "enabled": False,
            "written": 0,
            "skipped_final": 0,
            "valuation_date": valuation_date,
        }

    valuation_date = _normalize_date_string(valuation_date)
    if not valuation_date:
        return {
            "enabled": True,
            "written": 0,
            "skipped_final": 0,
            "valuation_date": "",
            "error": "valuation_date 为空",
        }

    now = datetime.now()
    stage = "quality_driven"

    cache = _load_json_cache(
        FUND_ESTIMATE_RETURN_CACHE_FILE,
        default={"version": 1, "records": {}},
    )

    if not isinstance(cache, dict):
        cache = {"version": 1, "records": {}}

    records = cache.get("records")
    if not isinstance(records, dict):
        records = {}
        cache["records"] = records

    written = 0
    skipped_final = 0
    skipped_invalid = 0

    if result_df is None or result_df.empty:
        return {
            "enabled": True,
            "written": 0,
            "skipped_final": 0,
            "valuation_date": valuation_date,
            "error": "result_df 为空",
        }

    for _, row in result_df.iterrows():
        fund_code = str(row.get("基金代码", "")).strip()

        # 避免把后续可能插入的基准行写进基金缓存
        if not re.fullmatch(r"\d{6}", fund_code):
            continue

        estimate_return_pct = _safe_float_or_none(row.get("今日预估涨跌幅"))
        if estimate_return_pct is None:
            skipped_invalid += 1
            continue

        fund_name = str(row.get("基金名称", "")).strip()
        key = f"overseas:{fund_code}:{valuation_date}"
        old_record = records.get(key)

        detail_item = detail_map.get(fund_code, {}) if isinstance(detail_map, dict) else {}
        summary = detail_item.get("summary") if isinstance(detail_item, dict) else None
        if not isinstance(summary, dict):
            summary = {}

        data_status = str(summary.get("data_status", "complete")).strip().lower()
        if data_status not in {"complete", "partial", "stale", "failed"}:
            data_status = "partial"
        completeness_score = _safe_float_or_none(summary.get("completeness_score"))
        if completeness_score is None:
            completeness_score = 100.0 if data_status == "complete" else 0.0
        is_final = bool(summary.get("is_final", data_status == "complete"))
        market_status = summary.get("market_status", {})
        market_trade_dates = summary.get("market_trade_dates", {})
        warnings = summary.get("warnings", [])

        record = {
            "market_group": "overseas",
            "fund_code": fund_code,
            "fund_name": fund_name,
            "valuation_date": valuation_date,
            "valuation_anchor_date": valuation_date,
            "run_date_bj": now.strftime("%Y-%m-%d"),
            "run_time_bj": now.isoformat(timespec="seconds"),
            "stage": "final" if is_final else "partial",
            "data_status": data_status,
            "completeness_score": float(completeness_score),
            "is_final": bool(is_final),
            "estimate_return_pct": float(estimate_return_pct),
            "method": summary.get("method", row.get("_估算方式", "")),
            "valuation_mode": summary.get("valuation_mode", summary.get("effective_valuation_mode", "")),
            "effective_valuation_mode": summary.get("effective_valuation_mode", ""),
            "raw_weight_sum_pct": _safe_float_or_none(summary.get("raw_weight_sum_pct")),
            "raw_holding_weight_sum_pct": _safe_float_or_none(summary.get("raw_holding_weight_sum_pct", summary.get("raw_weight_sum_pct"))),
            "available_raw_weight_sum_pct": _safe_float_or_none(summary.get("available_raw_weight_sum_pct")),
            "valid_holding_weight_pct": _safe_float_or_none(summary.get("valid_holding_weight_pct", summary.get("available_raw_weight_sum_pct"))),
            "failed_raw_weight_sum_pct": _safe_float_or_none(summary.get("failed_raw_weight_sum_pct")),
            "estimated_weight_sum_pct": _safe_float_or_none(summary.get("estimated_weight_sum_pct")),
            "residual_benchmark_label": summary.get("residual_benchmark_label", ""),
            "residual_benchmark_key": summary.get("residual_benchmark_key", ""),
            "residual_benchmark_market": summary.get("residual_benchmark_market", ""),
            "residual_benchmark_ticker": summary.get("residual_benchmark_ticker", ""),
            "residual_benchmark_return_pct": _safe_float_or_none(summary.get("residual_benchmark_return_pct")),
            "residual_benchmark_trade_date": summary.get("residual_benchmark_trade_date", ""),
            "residual_benchmark_status": summary.get("residual_benchmark_status", ""),
            "residual_weight_pct": _safe_float_or_none(
                summary.get("residual_benchmark_weight_pct", summary.get("residual_weight_pct"))
            ),
            "residual_benchmark_weight_pct": _safe_float_or_none(
                summary.get("residual_benchmark_weight_pct", summary.get("residual_weight_pct"))
            ),
            "boosted_valid_holding_weight_pct": _safe_float_or_none(
                summary.get("boosted_valid_holding_weight_pct", summary.get("boosted_available_raw_weight_sum_pct"))
            ),
            "holding_boost": _safe_float_or_none(
                summary.get("overseas_valid_holding_boost", summary.get("holding_boost"))
            ),
            "zero_stale_cn_hk_returns": bool(summary.get("zero_stale_cn_hk_returns", False)),
            "stale_market_estimate_date": summary.get("stale_market_estimate_date", ""),
            "stale_zeroed_count": int(summary.get("stale_zeroed_count", 0) or 0),
            "stale_zeroed_markets": summary.get("stale_zeroed_markets", []),
            "market_effective": summary.get("market_effective", {}),
            "market_status": market_status if isinstance(market_status, dict) else {},
            "market_trade_dates": market_trade_dates if isinstance(market_trade_dates, dict) else {},
            "source": summary.get("source", "anchor_daily"),
            "warnings": warnings if isinstance(warnings, list) else [str(warnings)],
            "holding_quarter": "",
            "table_title": str(title or ""),
            "output_file": str(output_file or ""),
            "cache_key": key,
        }

        # 股票持仓型基金尝试记录季度标签
        detail_df = detail_item.get("detail_df") if isinstance(detail_item, dict) else None
        if isinstance(detail_df, pd.DataFrame) and "季度" in detail_df.columns:
            try:
                q_values = detail_df["季度"].dropna().astype(str).unique().tolist()
                if q_values:
                    record["holding_quarter"] = q_values[0]
            except Exception:
                pass

        if _should_replace_estimate_record(old_record, record):
            records[key] = record
            written += 1
        else:
            skipped_final += 1

    cache["version"] = max(int(cache.get("version", 1) or 1), 1)
    cache["updated_at"] = now.isoformat(timespec="seconds")
    cache["records"] = records

    _save_fund_estimate_return_cache(cache)

    _cache_log(
        f"海外基金每日预估收益缓存: valuation_date={valuation_date}, "
        f"stage={stage}, written={written}, skipped_final={skipped_final}, skipped_invalid={skipped_invalid}"
    )

    return {
        "enabled": True,
        "written": written,
        "skipped_final": skipped_final,
        "skipped_invalid": skipped_invalid,
        "valuation_date": valuation_date,
        "stage": stage,
    }
def _write_domestic_fund_estimate_history_cache(
    result_df,
    detail_map,
    valuation_date,
    title=None,
    output_file=None,
    cache_enabled=True,
):
    """
    写入国内基金级每日预估收益缓存。

    注意：
        - 由国内市场收益表调用；
        - 与海外基金缓存复用同一个 JSON 文件；
        - key = domestic:fund_code:valuation_date；
        - valuation_date 使用北京时间运行日；
        - 15:30 前允许同一 key 反复覆盖；
        - 15:30 后可将 intraday 升级为 final；
        - 已有 final 后保持原记录。
    """
    if not cache_enabled:
        return {
            "enabled": False,
            "written": 0,
            "skipped_final": 0,
            "valuation_date": valuation_date,
        }

    valuation_date = _normalize_date_string(valuation_date)
    if not valuation_date:
        return {
            "enabled": True,
            "written": 0,
            "skipped_final": 0,
            "valuation_date": "",
            "error": "valuation_date 为空",
        }

    now = datetime.now()
    is_final = _is_after_domestic_estimate_freeze_time(now)
    stage = "final" if is_final else "intraday"

    cache = _load_json_cache(
        FUND_ESTIMATE_RETURN_CACHE_FILE,
        default={"version": 1, "records": {}, "benchmark_records": {}},
    )

    if not isinstance(cache, dict):
        cache = {"version": 1, "records": {}, "benchmark_records": {}}

    records = cache.get("records")
    if not isinstance(records, dict):
        records = {}
        cache["records"] = records

    written = 0
    skipped_final = 0
    skipped_invalid = 0

    if result_df is None or result_df.empty:
        return {
            "enabled": True,
            "written": 0,
            "skipped_final": 0,
            "valuation_date": valuation_date,
            "error": "result_df 为空",
        }

    for _, row in result_df.iterrows():
        fund_code = str(row.get("基金代码", "")).strip()

        if not re.fullmatch(r"\d{6}", fund_code):
            continue

        estimate_return_pct = _safe_float_or_none(row.get("今日预估涨跌幅"))
        if estimate_return_pct is None:
            skipped_invalid += 1
            continue

        fund_name = str(row.get("基金名称", "")).strip()
        key = f"domestic:{fund_code}:{valuation_date}"
        old_record = records.get(key)

        if isinstance(old_record, dict) and bool(old_record.get("is_final", False)):
            skipped_final += 1
            continue

        detail_item = detail_map.get(fund_code, {}) if isinstance(detail_map, dict) else {}
        summary = detail_item.get("summary") if isinstance(detail_item, dict) else None
        if not isinstance(summary, dict):
            summary = {}

        record = {
            "market_group": "domestic",
            "fund_code": fund_code,
            "fund_name": fund_name,
            "valuation_date": valuation_date,
            "run_date_bj": now.strftime("%Y-%m-%d"),
            "run_time_bj": now.isoformat(timespec="seconds"),
            "stage": stage,
            "is_final": bool(is_final),
            "estimate_return_pct": float(estimate_return_pct),
            "method": summary.get("method", row.get("_估算方式", "")),
            "valuation_mode": summary.get("valuation_mode", summary.get("effective_valuation_mode", "")),
            "effective_valuation_mode": summary.get("effective_valuation_mode", ""),
            "raw_weight_sum_pct": _safe_float_or_none(summary.get("raw_weight_sum_pct")),
            "available_raw_weight_sum_pct": _safe_float_or_none(summary.get("available_raw_weight_sum_pct")),
            "failed_raw_weight_sum_pct": _safe_float_or_none(summary.get("failed_raw_weight_sum_pct")),
            "estimated_weight_sum_pct": _safe_float_or_none(summary.get("estimated_weight_sum_pct")),
            "residual_benchmark_label": summary.get("residual_benchmark_label", ""),
            "residual_benchmark_key": summary.get("residual_benchmark_key", ""),
            "residual_benchmark_market": summary.get("residual_benchmark_market", ""),
            "residual_benchmark_ticker": summary.get("residual_benchmark_ticker", ""),
            "residual_benchmark_return_pct": _safe_float_or_none(summary.get("residual_benchmark_return_pct")),
            "residual_weight_pct": _safe_float_or_none(
                summary.get("residual_benchmark_weight_pct", summary.get("residual_weight_pct"))
            ),
            "holding_boost": _safe_float_or_none(
                summary.get("overseas_valid_holding_boost", summary.get("holding_boost"))
            ),
            "zero_stale_cn_hk_returns": bool(summary.get("zero_stale_cn_hk_returns", False)),
            "stale_market_estimate_date": summary.get("stale_market_estimate_date", ""),
            "stale_zeroed_count": int(summary.get("stale_zeroed_count", 0) or 0),
            "stale_zeroed_markets": summary.get("stale_zeroed_markets", []),
            "market_effective": summary.get("market_effective", {}),
            "holding_quarter": "",
            "table_title": str(title or ""),
            "output_file": str(output_file or ""),
            "cache_key": key,
        }

        detail_df = detail_item.get("detail_df") if isinstance(detail_item, dict) else None
        if isinstance(detail_df, pd.DataFrame) and "季度" in detail_df.columns:
            try:
                q_values = detail_df["季度"].dropna().astype(str).unique().tolist()
                if q_values:
                    record["holding_quarter"] = q_values[0]
            except Exception:
                pass

        records[key] = record
        written += 1

    cache["version"] = max(int(cache.get("version", 1) or 1), 2)
    cache["updated_at"] = now.isoformat(timespec="seconds")
    cache["records"] = records

    _save_fund_estimate_return_cache(cache)

    _cache_log(
        f"国内基金每日预估收益缓存: valuation_date={valuation_date}, "
        f"stage={stage}, written={written}, skipped_final={skipped_final}, skipped_invalid={skipped_invalid}"
    )

    return {
        "enabled": True,
        "written": written,
        "skipped_final": skipped_final,
        "skipped_invalid": skipped_invalid,
        "valuation_date": valuation_date,
        "stage": stage,
    }


def _write_overseas_benchmark_history_cache(
    benchmark_footer_items,
    valuation_date,
    title=None,
    output_file=None,
    cache_enabled=True,
):
    """
    写入海外市场指数基准每日涨跌幅缓存。

    设计目标：
        - 与海外基金每日预估收益写入同一个 JSON 文件；
        - 基金记录放在 records；指数记录放在 benchmark_records；
        - key = benchmark:指数代码:valuation_date；
        - 15:30 前允许同一 key 反复覆盖；
        - 15:30 后可将 intraday 升级为 final；
        - 已有 final 后保持原记录。

    benchmark_footer_items 预期来自 get_us_index_benchmark_items()，结构类似：
        {
            "label": "纳斯达克100",
            "symbol": ".NDX",
            "return_pct": 0.98,
            "trade_date": "2026-04-30",
            "source": "rsi_module_index_daily",
        }
    """
    if not cache_enabled:
        return {
            "enabled": False,
            "written": 0,
            "skipped_final": 0,
            "skipped_invalid": 0,
            "valuation_date": valuation_date,
        }

    valuation_date = _normalize_date_string(valuation_date)
    if not valuation_date:
        return {
            "enabled": True,
            "written": 0,
            "skipped_final": 0,
            "skipped_invalid": 0,
            "valuation_date": "",
            "error": "valuation_date 为空",
        }

    if not benchmark_footer_items:
        return {
            "enabled": True,
            "written": 0,
            "skipped_final": 0,
            "skipped_invalid": 0,
            "valuation_date": valuation_date,
            "error": "benchmark_footer_items 为空",
        }

    now = datetime.now()
    is_final = _is_after_overseas_estimate_freeze_time(now)
    stage = "final" if is_final else "intraday"

    cache = _load_json_cache(
        FUND_ESTIMATE_RETURN_CACHE_FILE,
        default={"version": 1, "records": {}, "benchmark_records": {}},
    )

    if not isinstance(cache, dict):
        cache = {"version": 1, "records": {}, "benchmark_records": {}}

    records = cache.get("benchmark_records")
    if not isinstance(records, dict):
        records = {}
        cache["benchmark_records"] = records

    written = 0
    skipped_final = 0
    skipped_invalid = 0

    for item in list(benchmark_footer_items):
        if not isinstance(item, dict):
            skipped_invalid += 1
            continue

        label = str(item.get("label", "")).strip()
        symbol = str(item.get("symbol", "")).strip().upper()
        return_pct = _safe_float_or_none(item.get("return_pct"))
        trade_date = _normalize_date_string(item.get("trade_date")) or valuation_date

        if not symbol:
            skipped_invalid += 1
            continue

        key = f"benchmark:{symbol}:{valuation_date}"
        status = str(item.get("status", "traded")).strip().lower()
        is_final = return_pct is not None and status in ANCHOR_COMPLETE_STATUSES and trade_date == valuation_date
        if return_pct is None:
            data_status = "failed"
        elif status == "stale":
            data_status = "stale"
        elif is_final:
            data_status = "complete"
        else:
            data_status = "partial"

        record = {
            "market_group": "overseas",
            "record_type": "benchmark",
            "label": label or symbol,
            "symbol": symbol,
            "kind": str(item.get("kind", "")).strip(),
            "order": item.get("order"),
            "valuation_date": valuation_date,
            "valuation_anchor_date": valuation_date,
            "trade_date": trade_date,
            "run_date_bj": now.strftime("%Y-%m-%d"),
            "run_time_bj": now.isoformat(timespec="seconds"),
            "stage": "final" if is_final else "partial",
            "data_status": data_status,
            "is_final": bool(is_final),
            "return_pct": float(return_pct) if return_pct is not None else None,
            "status": status,
            "source": str(item.get("source", "")),
            "error": str(item.get("error", "") or ""),
            "table_title": str(title or ""),
            "output_file": str(output_file or ""),
            "cache_key": key,
        }

        records[key] = record
        written += 1

    cache["version"] = max(int(cache.get("version", 1) or 1), 2)
    cache["updated_at"] = now.isoformat(timespec="seconds")
    cache["benchmark_records"] = records

    _save_fund_estimate_return_cache(cache)

    _cache_log(
        f"海外指数基准每日涨跌幅缓存: valuation_date={valuation_date}, "
        f"written={written}, skipped_final={skipped_final}, skipped_invalid={skipped_invalid}"
    )

    return {
        "enabled": True,
        "written": written,
        "skipped_final": skipped_final,
        "skipped_invalid": skipped_invalid,
        "valuation_date": valuation_date,
        "stage": "quality_driven",
    }

def estimate_funds_and_save_table(
    fund_codes,
    top_n=10,
    output_file="output/fund_estimate_table.png",
    title=None,
    title_segments=None,
    manual_returns_pct=None,
    prefer_intraday=True,
    us_realtime=False,
    hk_realtime=False,
    renormalize_available_holdings=True,
    include_purchase_limit=True,
    purchase_limit_timeout=8,
    purchase_limit_cache_days=7,
    holding_cache_days=75,
    cache_enabled=True,
    security_return_cache_enabled=True,
    cn_hk_hourly_cache=True,
    print_table=True,
    save_table=True,
    watermark_text="鱼师AHNS",
    watermark_alpha=0.15,
    watermark_fontsize=32,
    watermark_rotation=28,
    watermark_rows=5,
    watermark_cols=4,
    watermark_color="#000000FC",
    watermark_zorder=3,
    up_color="red",
    down_color="green",
    neutral_color="black",
    pct_digits=2,
    dpi=180,
    header_bg="#3f4d66",
    header_text_color="white",
    grid_color="#d9d9d9",
    figure_width=None,
    row_height=0.55,
    footnote_text="依据基金季度报告前十大持仓股及指数估算，仅供学习记录，不构成投资建议；最终以基金公司更新为准。",
    footnote_color="#666666",
    footnote_fontsize=15,
    benchmark_footer_items=None,
    benchmark_footer_fontsize=15,
    title_fontsize=20,
    title_color="black",
    title_fontweight="bold",
    title_gap_ratio=0.02,
    title_gap_min=0.008,
    title_gap_max=0.026,
    footnote_gap_ratio=0.02,
    footnote_gap_min=0.008,
    footnote_gap_max=0.026,
    pad_inches=0.14,
    sort_by_return=True,
    holding_mode="auto",
    proxy_map=None,
    proxy_normalize_weights=False,
    include_method_col=False,
    valuation_mode="intraday",
    stock_residual_benchmark=None,
    stock_residual_benchmark_return_pct=None,
    stock_residual_benchmark_label=None,
    stock_residual_benchmark_source=None,
    stock_residual_benchmark_status=None,
    stock_residual_benchmark_trade_date=None,
    benchmark_components=None,
    benchmark_position="top",
    zero_stale_cn_hk_returns=None,
    stale_market_estimate_date=None,
    valuation_anchor_date=None,
):
    """
    一站式入口：估算多个基金、打印表格、保存图片。

    主要参数
    --------
    fund_codes:
        基金代码。可传字符串或列表。
        示例："017437" 或 ["017437", "007467", "015016"]

    top_n:
        股票型基金取前 N 大股票持仓，并归一化到 100%。

    holding_mode:
        "auto"：
            fund_code 在 proxy_map 中时，走 ETF/FOF 代理估算；
            否则走股票持仓估算。
        "stock"：
            强制股票持仓估算。
        "proxy"：
            强制 ETF/FOF 代理估算。

    proxy_map:
        代理映射表。None 时使用 DEFAULT_FUND_PROXY_MAP。
        新增 ETF 联接基金时，优先改 DEFAULT_FUND_PROXY_MAP。

    proxy_normalize_weights:
        False：
            默认。ETF/FOF 代理按原始仓位计算，现金仓位按 0。
        True：
            将代理组件归一化到 100%。

    sort_by_return:
        True：
            按今日预估涨跌幅从高到低排序，并重新编号。
        False：
            保留输入顺序。

    include_method_col:
        True：显示估算方式列。
        False：不显示，表格更简洁。

    us_realtime:
        False：默认快速模式，美股用单只股票日线。
        True ：盘中实时模式，美股优先新浪/东方财富实时，可能较慢。

    hk_realtime:
        False：默认使用港股历史日线，避免全市场行情。
        True ：先尝试新浪单只港股实时行情，再回落到港股日线。

    renormalize_available_holdings:
        True：
            如果某些持仓股无法获取行情，则剔除这些持仓，
            并把剩余可获取行情的持仓重新归一化到 100% 后估算收益。
            适合日东纺这类行情缺失的情况。
        False：
            保留原前 N 大归一化权重，不把缺失仓位重新分配给可查持仓。

    title_gap_ratio / title_gap_min / title_gap_max:
        标题与表格上边界之间的自适应距离。数值越小，标题越贴近表格。

    footnote_gap_ratio / footnote_gap_min / footnote_gap_max:
        备注与表格下边界之间的自适应距离。数值越小，备注越贴近表格。

    pad_inches:
        保存图片时的外边距。数值越小，整张图片留白越少。
    """
    auto_overseas_residual_enabled = _is_overseas_fund_table_context(
        title=title,
        output_file=output_file,
    )
    auto_domestic_cache_enabled = False

    if title is None:
        title = "海外市场收益预估" + datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if proxy_map is None:
        proxy_map = DEFAULT_FUND_PROXY_MAP

    anchor_date = _normalize_trade_date_key(valuation_anchor_date)
    if auto_overseas_residual_enabled and not anchor_date:
        anchor_date = determine_latest_valuation_anchor_date()

    # 海外基金表默认获取纳斯达克100和标普500：
    # 1. 当前收益表底部显示最新交易日基准；
    # 2. 同步写入 fund_estimate_return_cache.json 的 benchmark_records，供区间累计表读取；
    # 3. 作为 QDII / 全球基金本次估值交易日，用来判断 A股/港股/韩国市场是否应闭市置零。
    if auto_overseas_residual_enabled and benchmark_footer_items is None:
        benchmark_footer_items = get_us_index_benchmark_items(
            cache_enabled=cache_enabled,
            valuation_anchor_date=anchor_date,
        )

    overseas_valuation_date = ""
    if auto_overseas_residual_enabled:
        overseas_valuation_date = anchor_date or _resolve_overseas_estimate_valuation_date(
            benchmark_footer_items=benchmark_footer_items,
            stock_residual_benchmark_label=stock_residual_benchmark_label,
        )

    if zero_stale_cn_hk_returns is None:
        zero_stale_cn_hk_returns = bool(auto_overseas_residual_enabled)

    if stale_market_estimate_date is None:
        # 节假日防重复口径必须使用海外基金估值交易日，而不是北京时间运行日。
        # 例：北京时间 4月2日晚运行，若纳斯达克100最新交易日为 4月1日，
        # 则 A股/港股/韩国市场只有在没有 4月1日行情时才置零；不能因为运行日是 4月2日而误置零。
        stale_market_estimate_date = overseas_valuation_date or datetime.now().strftime("%Y-%m-%d")

    # 股票持仓型基金的“剩余仓位基准补偿”会在 estimate_funds() 的单基金循环中解析。
    # 这样 007844 可以使用 XOP，其他基金仍自动回落到默认纳斯达克100。
    # 如果外部显式传入 stock_residual_benchmark 或 stock_residual_benchmark_return_pct，
    # 仍按旧逻辑作为整批基金的覆盖参数。

    result_df, detail_map = estimate_funds(
        fund_codes=fund_codes,
        top_n=top_n,
        manual_returns_pct=manual_returns_pct,
        prefer_intraday=prefer_intraday,
        us_realtime=us_realtime,
        hk_realtime=hk_realtime,
        renormalize_available_holdings=renormalize_available_holdings,
        include_purchase_limit=include_purchase_limit,
        purchase_limit_timeout=purchase_limit_timeout,
        purchase_limit_cache_days=purchase_limit_cache_days,
        holding_cache_days=holding_cache_days,
        cache_enabled=cache_enabled,
        security_return_cache_enabled=security_return_cache_enabled,
        cn_hk_hourly_cache=cn_hk_hourly_cache,
        sort_by_return=sort_by_return,
        holding_mode=holding_mode,
        proxy_map=proxy_map,
        proxy_normalize_weights=proxy_normalize_weights,
        include_method_col=include_method_col,
        valuation_mode=valuation_mode,
        auto_residual_benchmark_enabled=auto_overseas_residual_enabled,
        stock_residual_benchmark=stock_residual_benchmark,
        stock_residual_benchmark_return_pct=stock_residual_benchmark_return_pct,
        stock_residual_benchmark_label=stock_residual_benchmark_label,
        stock_residual_benchmark_source=stock_residual_benchmark_source,
        stock_residual_benchmark_status=stock_residual_benchmark_status,
        stock_residual_benchmark_trade_date=stock_residual_benchmark_trade_date,
        zero_stale_cn_hk_returns=zero_stale_cn_hk_returns,
        stale_market_estimate_date=stale_market_estimate_date,
        valuation_anchor_date=overseas_valuation_date if auto_overseas_residual_enabled else None,
    )

    if auto_overseas_residual_enabled:
        _write_failed_holdings_report(detail_map, overseas_valuation_date)

    # 缓存基金级每日预估收益，供 safe_fund.py 只读缓存绘制公开展示图。
    # 当前项目只写入海外/全球基金缓存，国内基金业务线已停用。
    if auto_overseas_residual_enabled:
        overseas_valuation_date = anchor_date or _resolve_overseas_estimate_valuation_date(
            benchmark_footer_items=benchmark_footer_items,
            stock_residual_benchmark_label=stock_residual_benchmark_label,
        )
        _write_overseas_fund_estimate_history_cache(
            result_df=result_df,
            detail_map=detail_map,
            valuation_date=overseas_valuation_date,
            title=title,
            output_file=output_file,
            cache_enabled=cache_enabled,
        )
        _write_overseas_benchmark_history_cache(
            benchmark_footer_items=benchmark_footer_items,
            valuation_date=overseas_valuation_date,
            title=title,
            output_file=output_file,
            cache_enabled=cache_enabled,
        )

    benchmark_rows = build_benchmark_rows(
        benchmark_components=benchmark_components,
        include_purchase_limit=include_purchase_limit,
        manual_returns_pct=manual_returns_pct,
        prefer_intraday=prefer_intraday,
        us_realtime=us_realtime,
        hk_realtime=hk_realtime,
        security_return_cache_enabled=security_return_cache_enabled,
        cn_hk_hourly_cache=cn_hk_hourly_cache,
        valuation_mode=valuation_mode,
    )

    result_df = _attach_benchmark_rows_to_result_df(
        result_df=result_df,
        benchmark_rows=benchmark_rows,
        include_purchase_limit=include_purchase_limit,
        include_method_col=include_method_col,
        benchmark_position=benchmark_position,
    )

    if print_table:
        print_fund_estimate_table(
            result_df,
            title=title,
            pct_digits=pct_digits,
        )

    if save_table:
        save_fund_estimate_table_image(
            result_df=result_df,
            output_file=output_file,
            title=title,
            title_segments=title_segments,
            dpi=dpi,
            watermark_text=watermark_text,
            watermark_alpha=watermark_alpha,
            watermark_fontsize=watermark_fontsize,
            watermark_rotation=watermark_rotation,
            watermark_rows=watermark_rows,
            watermark_cols=watermark_cols,
            watermark_color=watermark_color,
            watermark_zorder=watermark_zorder,
            up_color=up_color,
            down_color=down_color,
            neutral_color=neutral_color,
            pct_digits=pct_digits,
            header_bg=header_bg,
            header_text_color=header_text_color,
            grid_color=grid_color,
            figure_width=figure_width,
            row_height=row_height,
            footnote_text=footnote_text,
            footnote_color=footnote_color,
            footnote_fontsize=footnote_fontsize,
            benchmark_footer_items=benchmark_footer_items,
            benchmark_footer_fontsize=benchmark_footer_fontsize,
            title_fontsize=title_fontsize,
            title_color=title_color,
            title_fontweight=title_fontweight,
            title_gap_ratio=title_gap_ratio,
            title_gap_min=title_gap_min,
            title_gap_max=title_gap_max,
            footnote_gap_ratio=footnote_gap_ratio,
            footnote_gap_min=footnote_gap_min,
            footnote_gap_max=footnote_gap_max,
            pad_inches=pad_inches,
        )

    return result_df, detail_map


# 兼容旧函数名。

def get_jijin_holdings(
    fund_code="017437",
    top_n=10,
    estimate_return=True,
    manual_returns_pct=None,
    prefer_intraday=True,
    us_realtime=False,
    hk_realtime=False,
    renormalize_available_holdings=True,
    include_purchase_limit=True,
    purchase_limit_timeout=8,
    purchase_limit_cache_days=7,
    holding_cache_days=75,
    cache_enabled=True,
    security_return_cache_enabled=True,
    cn_hk_hourly_cache=True,
    return_summary=False,
    return_text=False,
    holding_mode="auto",
    proxy_map=None,
    proxy_normalize_weights=False,
    valuation_mode="intraday",
):
    """
    兼容旧调用方式。

    新项目建议直接使用：
        estimate_funds_and_save_table(...)
    """
    fund_code = str(fund_code).zfill(6)
    fund_name = get_fund_name(fund_code)

    if not estimate_return:
        latest_df = get_latest_stock_holdings_df(
            fund_code=fund_code,
            top_n=top_n,
            holding_cache_days=holding_cache_days,
            cache_enabled=cache_enabled,
        )
        print(latest_df)
        return latest_df

    result_row, estimate_df, summary = estimate_one_fund(
        fund_code=fund_code,
        top_n=top_n,
        manual_returns_pct=manual_returns_pct,
        prefer_intraday=prefer_intraday,
        us_realtime=us_realtime,
        hk_realtime=hk_realtime,
        renormalize_available_holdings=renormalize_available_holdings,
        include_purchase_limit=include_purchase_limit,
        purchase_limit_timeout=purchase_limit_timeout,
        purchase_limit_cache_days=purchase_limit_cache_days,
        holding_cache_days=holding_cache_days,
        cache_enabled=cache_enabled,
        security_return_cache_enabled=security_return_cache_enabled,
        cn_hk_hourly_cache=cn_hk_hourly_cache,
        holding_mode=holding_mode,
        proxy_map=proxy_map,
        proxy_normalize_weights=proxy_normalize_weights,
        valuation_mode=valuation_mode,
    )

    purchase_limit_text = ""
    if include_purchase_limit:
        purchase_limit_text = f"\n限购金额：{result_row.get('限购金额', '未知')}"

    estimate_text = (
        f"【基金收益预估】\n"
        f"基金代码：{fund_code}\n"
        f"基金名称：{fund_name}\n"
        f"估算方式：{summary.get('method', '')}\n"
        f"最终预估收益：{format_pct(summary['estimated_return_pct'], digits=4)}"
        f"{purchase_limit_text}"
    )

    print("=" * 100)
    print(estimate_text)
    print("=" * 100)

    if return_text:
        return estimate_df, summary, estimate_text

    if return_summary:
        return estimate_df, summary

    return estimate_df


if __name__ == "__main__":
    # 直接运行本文件时，使用海外/全球基金池做一次示例估算。
    estimate_funds_and_save_table(
        fund_codes=[
            "017437",
            "012922",
            "016702",
        ],
        top_n=10,
        output_file="output/haiwai_fund.png",
        title="海外市场收益率预估 " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        holding_mode="auto",
        proxy_normalize_weights=False,
        us_realtime=False,
        hk_realtime=False,
        valuation_mode="last_close",
        renormalize_available_holdings=True,
        include_purchase_limit=True,
        include_method_col=False,
        sort_by_return=True,
        watermark_text="鱼师AHNS",
        up_color="red",
        down_color="green",
        print_table=True,
        save_table=True,
    )
