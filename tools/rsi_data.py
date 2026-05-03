"""
rsi_module.py
功能：
1. 获取美股指数历史数据
2. 计算 Wilder RSI
3. 绘制收盘价、成交量、RSI 图
4. 提供统一外部调用入口 analyze_index()
5. 保留脚本直接运行入口 main()

常用指数 symbol：
    .NDX   纳斯达克100
    .IXIC  纳斯达克综合指数
    .INX   标普500
    .DJI   道琼斯工业指数
    512890 华泰柏瑞中证红利低波动 ETF
"""

from pathlib import Path
from typing import Optional

import re
import numpy as np
import pandas as pd
import akshare as ak
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager
from matplotlib.collections import LineCollection
import time
import requests

rule = "ME"

# 默认中文名称映射；可在 rsi_analyze_index(display_name=...) 中覆盖。
DEFAULT_SYMBOL_NAME_MAP = {
    ".IXIC": "纳斯达克综合指数",
    ".NDX": "纳斯达克100指数",
    ".INX": "标普500指数",
    ".DJI": "道琼斯工业指数",
    "512890": "红利低波华泰ETF",
    "510300": "沪深300ETF",
    "159915": "创业板ETF",
    "H30269": "中证红利低波动指数",
}

_CHINESE_FONT_READY = False
_CHINESE_FONT_NAME = None


def _setup_chinese_font(force: bool = False) -> None:
    """
    设置 Matplotlib 中文字体。

    实现说明：
    1. 不只设置字体名称，还会主动扫描 Windows/macOS/Linux 常见中文字体文件；
    2. 找到字体文件后用 font_manager.addfont 注册给 Matplotlib；
    3. save_signal_table_image() 和 plot_analysis() 都会调用本函数；
    4. 这样可以避免 DejaVu Sans 缺少中文字形而产生 Glyph missing 警告。
    """
    global _CHINESE_FONT_READY, _CHINESE_FONT_NAME

    if _CHINESE_FONT_READY and not force:
        return

    candidate_font_paths = [
        # Windows
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\simkai.ttf",
        r"C:\Windows\Fonts\Deng.ttf",
        r"C:\Windows\Fonts\Dengb.ttf",

        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/Library/Fonts/Arial Unicode.ttf",

        # Linux
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]

    # 主动把常见中文字体文件注册给 Matplotlib
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
        "KaiTi",
        "DengXian",
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "WenQuanYi Zen Hei",
        "PingFang SC",
        "Heiti SC",
        "Arial Unicode MS",
    ]

    available_font_names = {font.name for font in font_manager.fontManager.ttflist}

    chosen_font = None
    for name in candidate_font_names:
        if name in available_font_names:
            chosen_font = name
            break

    if chosen_font is not None:
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = [
            chosen_font,
            *candidate_font_names,
            "DejaVu Sans",
        ]
        _CHINESE_FONT_NAME = chosen_font
    else:
        # 没找到中文字体时仍设置候选名；如果系统确实没有中文字体，仍会有警告，需要安装字体。
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = [
            *candidate_font_names,
            "DejaVu Sans",
        ]
        _CHINESE_FONT_NAME = None

    plt.rcParams["axes.unicode_minus"] = False
    _CHINESE_FONT_READY = True


def get_symbol_display_name(
    symbol: str,
    display_name: Optional[str] = None,
    symbol_name_map: Optional[dict] = None,
) -> str:
    """
    获取图表显示名称。

    优先级：
    1. display_name 手动指定名称；
    2. symbol_name_map 用户自定义映射；
    3. DEFAULT_SYMBOL_NAME_MAP 默认映射；
    4. 原始 symbol。
    """
    if display_name is not None and str(display_name).strip():
        return str(display_name).strip()

    key = str(symbol).strip()

    if symbol_name_map is not None and key in symbol_name_map:
        return str(symbol_name_map[key])

    return DEFAULT_SYMBOL_NAME_MAP.get(key, key)



def get_current_chinese_font() -> Optional[str]:
    """
    返回当前 Matplotlib 选中的中文字体名称。
    可用于调试：
        from tools.rsi_module import get_current_chinese_font
        print(get_current_chinese_font())
    """
    _setup_chinese_font()
    return _CHINESE_FONT_NAME

__all__ = [
    "compute_rsi_wilder",
    "get_index_akshare",
    "get_us_index_akshare",
    "get_symbol_display_name",
    "get_current_chinese_font",
    "_is_cn_etf_symbol",
    "_fetch_cn_etf",
    "_merge_cn_etf_realtime_today",
    "_fetch_cn_etf_realtime_sina_row",
    "resample_ohlcv",
    "add_rsi",
    "build_period_rsi_df",
    "extract_rsi_signal_points",
    "build_signal_table",
    "print_signal_dates",
    "save_signal_table_image",
    "print_latest_summary",
    "plot_analysis",
    "rsi_analyze_index",
    "analyze_index",
]

def _standardize_index_df(df: pd.DataFrame, symbol: str, days: int) -> pd.DataFrame:
    """
    统一不同接口返回的列名和数据格式。
    """
    if df is None or df.empty:
        raise RuntimeError(f"返回空数据: {symbol}")

    df = df.copy()

    rename_map = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
    }
    df = df.rename(columns=rename_map)

    if "date" not in df.columns or "close" not in df.columns:
        raise RuntimeError(
            f"返回数据缺少 date 或 close 列: {symbol}, columns={list(df.columns)}"
        )

    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "", regex=False),
                errors="coerce",
            )

    if "volume" not in df.columns:
        df["volume"] = np.nan

    if "amount" not in df.columns:
        df["amount"] = np.nan

    df = df.dropna(subset=["date", "close"])
    df = df.sort_values("date").reset_index(drop=True)

    if df.empty:
        raise RuntimeError(f"清洗后无有效数据: {symbol}")

    return df.tail(days).copy()


def _read_cache(cache_file: Path, days: int) -> pd.DataFrame:
    """
    读取本地缓存行情。
    """
    df = pd.read_csv(cache_file)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "", regex=False),
                errors="coerce",
            )

    df = df.dropna(subset=["date", "close"])
    df = df.sort_values("date").reset_index(drop=True)
    return df.tail(days).copy()

def compute_rsi_wilder(close: pd.Series, window: int = 9) -> pd.Series:
    """
    Wilder RSI。

    实现说明：
    1. 使用 Wilder 原始递推逻辑：
       第一个 avg_gain / avg_loss 用前 window 个涨跌幅的简单平均初始化；
       后续使用 (前值 * (window - 1) + 当前值) / window 递推。
    2. 修复 avg_loss = 0 时 RSI 应为 100 的边界问题；
       修复 avg_gain = 0 时 RSI 应为 0 的边界问题；
       如果 avg_gain 和 avg_loss 同时为 0，则 RSI 记为 50。
    """
    close = pd.to_numeric(close, errors="coerce")
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    rsi = pd.Series(np.nan, index=close.index, dtype=float)

    if window <= 0:
        raise ValueError("window 必须为正整数。")

    if len(close) <= window:
        return rsi

    avg_gain = pd.Series(np.nan, index=close.index, dtype=float)
    avg_loss = pd.Series(np.nan, index=close.index, dtype=float)

    # 第一个可用 RSI 位于 index=window，对应使用第 1 到第 window 个涨跌幅。
    first_gain = gain.iloc[1: window + 1].mean()
    first_loss = loss.iloc[1: window + 1].mean()

    avg_gain.iloc[window] = first_gain
    avg_loss.iloc[window] = first_loss

    for i in range(window + 1, len(close)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (window - 1) + gain.iloc[i]) / window
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (window - 1) + loss.iloc[i]) / window

    rs = avg_gain / avg_loss
    rsi = 100 - 100 / (1 + rs)

    # RSI 的边界值需要单独处理，避免除零后产生 NaN。
    only_gain = (avg_loss == 0) & (avg_gain > 0)
    only_loss = (avg_gain == 0) & (avg_loss > 0)
    no_change = (avg_gain == 0) & (avg_loss == 0)

    rsi[only_gain] = 100
    rsi[only_loss] = 0
    rsi[no_change] = 50

    return rsi


def _cn_index_symbol_candidates(symbol: str) -> list[str]:
    """
    给新浪/腾讯指数接口生成可能的代码形式。

    常见情况：
    000300 -> sh000300
    000905 -> sh000905
    399006 -> sz399006
    H30269 -> shH30269 / shh30269 / csiH30269 等尝试
    """
    s = str(symbol).strip()
    su = s.upper()

    candidates = []

    if su.startswith("H"):
        candidates.extend([
            "sh" + su,
            "sh" + su.lower(),
            "csi" + su,
            "CSI" + su,
            su,
        ])
    elif su.startswith("000"):
        candidates.extend(["sh" + su, su])
    elif su.startswith("399"):
        candidates.extend(["sz" + su, su])
    elif su.startswith("93"):
        candidates.extend(["sh" + su, su])
    elif su.startswith("98"):
        candidates.extend(["sz" + su, su])
    else:
        candidates.extend([s, "sh" + s, "sz" + s])

    # 去重但保留顺序。
    out = []
    for item in candidates:
        if item not in out:
            out.append(item)

    return out


def _is_cn_etf_symbol(symbol: str) -> bool:
    """
    判断是否为中国场内 ETF/LOF 代码。

    常见：
    5xxxxx 上海 ETF
    1xxxxx 深圳 ETF/LOF
    """
    s = str(symbol).strip()
    return s.isdigit() and len(s) == 6 and s[0] in ["1", "5"]


def _fetch_cn_etf(symbol: str, days: int) -> pd.DataFrame:
    """
    获取中国场内 ETF 历史行情。

    用于 512890、510300、159915 等 ETF/LOF 代码。
    优先使用新浪 ETF 接口；如果失败，再尝试东方财富 ETF 接口。
    这样可以避免把 ETF 错误地送入指数接口，导致成交量缺失。
    """
    s = str(symbol).strip()
    errors = []

    # 优先使用新浪 ETF 历史行情。
    try:
        if s.startswith("5"):
            sina_symbol = "sh" + s
        elif s.startswith("1"):
            sina_symbol = "sz" + s
        else:
            sina_symbol = s

        df = ak.fund_etf_hist_sina(symbol=sina_symbol)
        return _standardize_index_df(
            df,
            symbol=f"{s}/fund_etf_hist_sina/{sina_symbol}",
            days=days,
        )
    except Exception as e:
        errors.append(f"fund_etf_hist_sina: {repr(e)}")

    # 新浪失败后使用东方财富 ETF 历史行情。
    try:
        df = ak.fund_etf_hist_em(
            symbol=s,
            period="daily",
            start_date="19700101",
            end_date="22220101",
            adjust="",
        )
        return _standardize_index_df(
            df,
            symbol=f"{s}/fund_etf_hist_em",
            days=days,
        )
    except Exception as e:
        errors.append(f"fund_etf_hist_em: {repr(e)}")

    raise RuntimeError(
        f"ETF 历史行情获取失败: {s}\n" + "\n".join(errors)
    )



def _safe_numeric(value):
    """
    将实时行情字段安全转换为数值。
    """
    return pd.to_numeric(value, errors="coerce")


def _fetch_cn_etf_realtime_sina_row(symbol: str, retry: int = 2, sleep_seconds: float = 0.8) -> dict | None:
    """
    新浪单代码 ETF 实时行情接口。

    适用：
        512890、510210、510300、159915 等中国场内 ETF/LOF。

    返回字段：
        date, open, high, low, close, volume, amount, source

    说明：
        1. 这是轻量级单代码接口，比一次拉全市场 ETF 快照更适合少量标的；
        2. 午盘/盘中返回的是临时行情，不是收盘确认值；
        3. 失败返回 None，不中断主流程。
    """
    s = str(symbol).strip().zfill(6)

    if s.startswith("5"):
        sina_symbol = "sh" + s
    elif s.startswith("1"):
        sina_symbol = "sz" + s
    else:
        return None

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
                last_error = f"新浪实时行情返回格式异常: {text[:120]}"
                continue

            values = m.group(1).split(",")

            if len(values) < 32:
                last_error = f"新浪实时行情字段数量异常: len={len(values)}"
                continue

            name = values[0]
            open_price = _safe_numeric(values[1])
            prev_close = _safe_numeric(values[2])
            latest_price = _safe_numeric(values[3])
            high_price = _safe_numeric(values[4])
            low_price = _safe_numeric(values[5])
            volume = _safe_numeric(values[8])
            amount = _safe_numeric(values[9])
            trade_date = values[30]
            trade_time = values[31]

            if pd.isna(latest_price) or float(latest_price) <= 0:
                last_error = f"新浪实时行情最新价无效: latest={latest_price}"
                continue

            if not trade_date:
                trade_date = str(pd.Timestamp.today().date())

            trade_day = pd.to_datetime(trade_date, errors="coerce")
            if pd.isna(trade_day):
                trade_day = pd.Timestamp.today().normalize()
            else:
                trade_day = trade_day.normalize()

            # 新浪有时停牌/未开盘时 open/high/low 可能为 0，用最新价兜底
            open_value = open_price if not pd.isna(open_price) and float(open_price) > 0 else latest_price
            high_value = high_price if not pd.isna(high_price) and float(high_price) > 0 else latest_price
            low_value = low_price if not pd.isna(low_price) and float(low_price) > 0 else latest_price

            return {
                "date": trade_day,
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": latest_price,
                "volume": volume,
                "amount": amount,
                "name": name,
                "time": trade_time,
                "prev_close": prev_close,
                "source": "sina_realtime",
            }

        except Exception as e:
            last_error = repr(e)
            if i < max(1, retry) - 1:
                time.sleep(sleep_seconds)

    print(f"[WARN] 新浪单代码实时行情失败: {symbol}, 原因: {last_error}")
    return None



def _fetch_cn_etf_realtime_spot_row(symbol: str, retry: int = 2, sleep_seconds: float = 1.2) -> dict | None:
    """
    从 ETF 实时行情接口获取单个 ETF 的盘中数据。

    返回字段：
        date, open, high, low, close, volume, amount, source

    失败返回 None，不抛出异常。
    """
    s = str(symbol).strip().zfill(6)
    last_error = None

    for i in range(max(1, retry)):
        try:
            spot_df = ak.fund_etf_spot_em()
            if spot_df is None or spot_df.empty or "代码" not in spot_df.columns:
                last_error = "fund_etf_spot_em 未返回有效代码列"
                continue

            spot_df = spot_df.copy()
            spot_df["代码"] = spot_df["代码"].astype(str).str.zfill(6)

            hit = spot_df[spot_df["代码"] == s]
            if hit.empty:
                last_error = f"fund_etf_spot_em 找不到代码 {symbol}"
                continue

            row = hit.iloc[0]

            latest_price = _safe_numeric(row.get("最新价"))
            open_price = _safe_numeric(row.get("今开"))
            high_price = _safe_numeric(row.get("最高"))
            low_price = _safe_numeric(row.get("最低"))
            volume = _safe_numeric(row.get("成交量"))
            amount = _safe_numeric(row.get("成交额"))

            if pd.isna(latest_price):
                last_error = f"fund_etf_spot_em 最新价无效: {symbol}"
                continue

            return {
                "date": pd.Timestamp.today().normalize(),
                "open": open_price if not pd.isna(open_price) else latest_price,
                "high": high_price if not pd.isna(high_price) else latest_price,
                "low": low_price if not pd.isna(low_price) else latest_price,
                "close": latest_price,
                "volume": volume,
                "amount": amount,
                "source": "fund_etf_spot_em",
            }

        except Exception as e:
            last_error = repr(e)
            if i < max(1, retry) - 1:
                time.sleep(sleep_seconds)

    print(f"[WARN] fund_etf_spot_em 获取 ETF 实时行情失败: {symbol}, 原因: {last_error}")
    return None


def _fetch_cn_etf_realtime_minute_row(symbol: str) -> dict | None:
    """
    用 ETF 分时行情接口兜底生成当天临时日线。

    逻辑：
        1. 获取今天 09:30 到当前时间的 1 分钟数据；
        2. 用第一条开盘价作为 open；
        3. 用最高价最大值作为 high；
        4. 用最低价最小值作为 low；
        5. 用最后一条收盘价/最新价作为 close；
        6. 成交量、成交额求和。

    失败返回 None，不抛出异常。
    """
    s = str(symbol).strip().zfill(6)
    now = pd.Timestamp.now()
    today = now.normalize()

    start_date = f"{today.date()} 09:30:00"
    end_date = now.strftime("%Y-%m-%d %H:%M:%S")

    try:
        min_df = ak.fund_etf_hist_min_em(
            symbol=s,
            period="1",
            adjust="",
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:
        print(f"[WARN] fund_etf_hist_min_em 获取 ETF 分时行情失败: {symbol}, 原因: {e}")
        return None

    if min_df is None or min_df.empty:
        print(f"[WARN] fund_etf_hist_min_em 未返回分时数据: {symbol}")
        return None

    min_df = min_df.copy()

    # 兼容中文字段
    rename_map = {
        "时间": "datetime",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "最新价": "latest",
    }
    min_df = min_df.rename(columns=rename_map)

    if "datetime" in min_df.columns:
        min_df["datetime"] = pd.to_datetime(min_df["datetime"], errors="coerce")
        min_df = min_df.dropna(subset=["datetime"])
        min_df = min_df[min_df["datetime"].dt.date == today.date()]

    for col in ["open", "high", "low", "close", "volume", "amount", "latest"]:
        if col in min_df.columns:
            min_df[col] = pd.to_numeric(
                min_df[col].astype(str).str.replace(",", "", regex=False),
                errors="coerce",
            )

    # close 优先取最后一条 close；没有则取 latest
    close_col = "close" if "close" in min_df.columns else "latest"
    required_cols = [c for c in ["open", "high", "low", close_col] if c in min_df.columns]
    min_df = min_df.dropna(subset=required_cols)

    if min_df.empty:
        print(f"[WARN] ETF 分时行情清洗后为空: {symbol}")
        return None

    last_row = min_df.iloc[-1]
    latest_price = last_row.get(close_col)

    if pd.isna(latest_price):
        print(f"[WARN] ETF 分时行情最新价无效: {symbol}")
        return None

    open_price = min_df["open"].dropna().iloc[0] if "open" in min_df.columns and min_df["open"].notna().any() else latest_price
    high_price = min_df["high"].max() if "high" in min_df.columns else latest_price
    low_price = min_df["low"].min() if "low" in min_df.columns else latest_price
    volume = min_df["volume"].sum() if "volume" in min_df.columns else np.nan
    amount = min_df["amount"].sum() if "amount" in min_df.columns else np.nan

    return {
        "date": today,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": latest_price,
        "volume": volume,
        "amount": amount,
        "source": "fund_etf_hist_min_em",
    }


def _get_cn_etf_realtime_row(symbol: str) -> dict | None:
    """
    ETF 盘中行情统一入口。

    顺序：
        1. 新浪单代码实时行情 sina_realtime；
        2. fund_etf_spot_em 实时全市场快照；
        3. fund_etf_hist_min_em 1分钟分时行情兜底。

    这样比先拉全市场快照更轻，也更适合只跟踪少量 ETF 的场景。
    """
    row = _fetch_cn_etf_realtime_sina_row(symbol=symbol)
    if row is not None:
        return row

    row = _fetch_cn_etf_realtime_spot_row(symbol=symbol)
    if row is not None:
        return row

    row = _fetch_cn_etf_realtime_minute_row(symbol=symbol)
    if row is not None:
        return row

    return None


def _merge_cn_etf_realtime_today(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    将中国场内 ETF 的实时行情合并到历史日线末尾。

    先尝试 fund_etf_spot_em；
    如果实时快照接口失败，再用 fund_etf_hist_min_em 分时数据兜底。
    如果两个实时接口都失败，则返回原始历史日线数据，不中断主流程。
    """
    if df is None or df.empty:
        return df

    realtime_row = _get_cn_etf_realtime_row(symbol=symbol)

    if realtime_row is None:
        print(f"[WARN] 无法合并 ETF 盘中实时行情，继续使用历史日线数据: {symbol}")
        return df

    today = pd.Timestamp(realtime_row["date"]).normalize()

    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")

    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in out.columns:
            out[col] = pd.to_numeric(
                out[col].astype(str).str.replace(",", "", regex=False),
                errors="coerce",
            )

    out = out.dropna(subset=["date", "close"])
    out = out.sort_values("date").reset_index(drop=True)

    new_row = {
        "date": today,
        "open": float(realtime_row["open"]) if not pd.isna(realtime_row["open"]) else float(realtime_row["close"]),
        "high": float(realtime_row["high"]) if not pd.isna(realtime_row["high"]) else float(realtime_row["close"]),
        "low": float(realtime_row["low"]) if not pd.isna(realtime_row["low"]) else float(realtime_row["close"]),
        "close": float(realtime_row["close"]),
        "volume": np.nan if pd.isna(realtime_row.get("volume")) else float(realtime_row.get("volume")),
        "amount": np.nan if pd.isna(realtime_row.get("amount")) else float(realtime_row.get("amount")),
        "is_realtime": True,
    }

    if "is_realtime" not in out.columns:
        out["is_realtime"] = False

    if len(out) > 0 and out.iloc[-1]["date"].normalize() == today:
        for k, v in new_row.items():
            out.loc[out.index[-1], k] = v
    else:
        out = pd.concat([out, pd.DataFrame([new_row])], ignore_index=True)

    out = out.sort_values("date").reset_index(drop=True)

    print(
        f"[INFO] 已合并 ETF 盘中行情: {symbol}, "
        f"date={today.date()}, latest={float(new_row['close']):.4f}, "
        f"source={realtime_row.get('source')}"
    )

    return out


def _fetch_yahoo_index(symbol: str, days: int) -> pd.DataFrame:
    """
    用 Yahoo Finance 获取指数日线数据。

    H30269 默认映射为 H30269.SS。
    优先使用 yfinance；如果未安装 yfinance，再尝试 requests 直连。
    """
    s = str(symbol).strip().upper()

    yahoo_map = {
        "H30269": "H30269.SS",
        ".IXIC": "^IXIC",
        ".NDX": "^NDX",
        ".INX": "^GSPC",
        ".DJI": "^DJI",
    }

    yahoo_symbol = yahoo_map.get(s, s)

    try:
        import yfinance as yf
    except ImportError:
        yf = None
    # 优先使用 yfinance，避免 Yahoo chart API 直连被 403 拦截。
    if yf is not None:
        try:
            df = yf.download(
                yahoo_symbol,
                period="max",
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
            )

            if df is not None and not df.empty:
                df = df.reset_index()

                # yfinance 有时会返回多层列索引。
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [
                        col[0] if isinstance(col, tuple) else col
                        for col in df.columns
                    ]

                rename_map = {
                    "Date": "date",
                    "Open": "open",
                    "High": "high",
                    "Low": "low",
                    "Close": "close",
                    "Volume": "volume",
                }

                df = df.rename(columns=rename_map)

                if "Adj Close" in df.columns and "close" not in df.columns:
                    df["close"] = df["Adj Close"]

                df["amount"] = np.nan

                return _standardize_index_df(df, symbol=f"{symbol}/{yahoo_symbol}", days=days)

        except Exception as e:
            # yfinance 失败后继续走 requests 直连。
            pass

    # 备用：requests 直连 Yahoo chart API。
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
    params = {
        "range": "max",
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        "Referer": f"https://finance.yahoo.com/quote/{yahoo_symbol}/history/",
    }

    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()

    data = resp.json()
    result = data.get("chart", {}).get("result", None)

    if not result:
        raise RuntimeError(f"Yahoo Finance 未返回有效数据: {symbol} -> {yahoo_symbol}")

    result = result[0]
    timestamps = result.get("timestamp", [])
    quote = result.get("indicators", {}).get("quote", [{}])[0]

    if not timestamps or not quote:
        raise RuntimeError(f"Yahoo Finance 数据结构异常: {symbol} -> {yahoo_symbol}")

    n = len(timestamps)

    def _field(name: str):
        values = quote.get(name, [])
        if values is None:
            values = []
        if len(values) < n:
            values = list(values) + [np.nan] * (n - len(values))
        return values[:n]

    df = pd.DataFrame({
        "date": pd.to_datetime(timestamps, unit="s").date,
        "open": _field("open"),
        "high": _field("high"),
        "low": _field("low"),
        "close": _field("close"),
        "volume": _field("volume"),
    })

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["amount"] = np.nan

    return _standardize_index_df(df, symbol=f"{symbol}/{yahoo_symbol}", days=days)

def get_index_akshare(
    symbol: str = ".IXIC",
    days: int = 180,
    cache_dir: str = "cache",
    retry: int = 3,
    sleep_seconds: tuple = (2, 5, 10),
    use_cache: bool = True,
    allow_eastmoney: bool = False,
    include_realtime: bool = False,
) -> pd.DataFrame:
    """
    获取指数历史数据，兼容美股指数和中国指数。

    数据源顺序：
    1. 美股指数：新浪美股指数接口；
    2. 中国场内 ETF：新浪 ETF 接口，失败后尝试东方财富 ETF 接口；
    3. H30269 等中证指数：Yahoo Finance；
    4. 中国指数：腾讯接口 stock_zh_index_daily_tx；
    5. 中国指数：新浪接口 stock_zh_index_daily；
    6. 可选：东方财富指数接口，allow_eastmoney=True 时启用；
    7. include_realtime=True 时，对中国场内 ETF 合并盘中实时行情；
    8. 如果联网失败且有缓存，则读取本地缓存。

    默认 allow_eastmoney=False，即不使用东方财富接口。
    """
    symbol = str(symbol).strip()

    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    safe_symbol = (
        symbol.replace(".", "dot_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
    )
    cache_file = cache_path / f"{safe_symbol}_index_daily.csv"

    errors = []

    def try_fetch_once() -> pd.DataFrame:
        # 美股指数。
        if symbol.startswith("."):
            df = ak.index_us_stock_sina(symbol=symbol)
            return _standardize_index_df(df, symbol=symbol, days=days)

        # 中国场内 ETF，例如 512890、510300、159915。
        if _is_cn_etf_symbol(symbol):
            try:
                df = _fetch_cn_etf(symbol=symbol, days=days)

                # 盘中查看时，将实时行情合并成今天的临时日线
                if include_realtime:
                    df = _merge_cn_etf_realtime_today(df, symbol=symbol)

                return df.tail(days).copy()
            except Exception as e:
                errors.append(f"ETF: {repr(e)}")

        # Yahoo Finance 优先用于 H30269 这类 CSI 指数。
        if symbol.upper().startswith("H"):
            try:
                df = _fetch_yahoo_index(symbol=symbol, days=days)
                return df
            except Exception as e:
                errors.append(f"Yahoo Finance: {repr(e)}")

        # 中国指数优先尝试腾讯接口。
        for tx_symbol in _cn_index_symbol_candidates(symbol):
            try:
                df = ak.stock_zh_index_daily_tx(symbol=tx_symbol)
                df = _standardize_index_df(df, symbol=f"{symbol}/{tx_symbol}", days=days)
                return df
            except Exception as e:
                errors.append(f"stock_zh_index_daily_tx({tx_symbol}): {repr(e)}")

        # 腾讯失败后尝试新浪接口。
        for sina_symbol in _cn_index_symbol_candidates(symbol):
            try:
                df = ak.stock_zh_index_daily(symbol=sina_symbol)
                df = _standardize_index_df(df, symbol=f"{symbol}/{sina_symbol}", days=days)
                return df
            except Exception as e:
                errors.append(f"stock_zh_index_daily({sina_symbol}): {repr(e)}")

        # 东方财富接口默认关闭，仅在 allow_eastmoney=True 时启用。
        if allow_eastmoney:
            try:
                df = ak.index_zh_a_hist(
                    symbol=symbol,
                    period="daily",
                    start_date="19700101",
                    end_date="22220101",
                )
                return _standardize_index_df(df, symbol=symbol, days=days)
            except Exception as e:
                errors.append(f"index_zh_a_hist: {repr(e)}")

            try:
                if symbol.upper().startswith("H"):
                    em_symbol = "csi" + symbol.upper()
                elif symbol.startswith("000"):
                    em_symbol = "sh" + symbol
                elif symbol.startswith("399"):
                    em_symbol = "sz" + symbol
                else:
                    em_symbol = symbol

                df = ak.stock_zh_index_daily_em(symbol=em_symbol)
                return _standardize_index_df(df, symbol=f"{symbol}/{em_symbol}", days=days)
            except Exception as e:
                errors.append(f"stock_zh_index_daily_em: {repr(e)}")

        raise RuntimeError(f"未返回有效指数数据: {symbol}")

    for i in range(max(1, retry)):
        try:
            df = try_fetch_once()

            if use_cache:
                df.to_csv(cache_file, index=False, encoding="utf-8-sig")

            return df

        except Exception as e:
            errors.append(f"第 {i + 1} 次尝试失败: {repr(e)}")

            if i < max(1, retry) - 1:
                wait = sleep_seconds[min(i, len(sleep_seconds) - 1)]
                print(f"[WARN] 获取 {symbol} 失败，{wait} 秒后重试。原因: {e}")
                time.sleep(wait)

    if use_cache and cache_file.exists():
        print(f"[WARN] 联网获取 {symbol} 失败，改用本地缓存: {cache_file}")
        df = _read_cache(cache_file, days=days)

        # 即使历史接口失败，只要实时 ETF 接口可用，也尝试合并今天盘中行情
        if include_realtime and _is_cn_etf_symbol(symbol):
            df = _merge_cn_etf_realtime_today(df, symbol=symbol)

        return df.tail(days).copy()

    raise RuntimeError(
        f"所有非东方财富接口均未返回有效指数数据: {symbol}\n"
        + "\n".join(errors[-12:])
    )


# 兼容旧函数名。
get_us_index_akshare = get_index_akshare

def resample_ohlcv(
    df: pd.DataFrame,
    period: str = "D",
) -> pd.DataFrame:
    """
    将日线行情转换为指定周期行情。

    period 可选：
    D 或 daily   日线
    W 或 weekly  周线
    M 或 monthly 月线

    聚合规则：
    open   取周期内第一天开盘价
    high   取周期内最高价
    low    取周期内最低价
    close  取周期内最后一天收盘价
    volume 取周期内成交量合计
    amount 取周期内成交额合计

    注意：
    周线和月线的 date 使用周期内最后一个实际交易日，而不是机械的周五或月末日期。
    这样后续标注三角形、星星时，标记会尽量贴近真实收盘价曲线。
    """
    if df is None or df.empty:
        raise ValueError("输入数据为空，无法转换周期。")

    period_upper = str(period).upper()

    if period_upper in ["D", "DAILY", "DAY"]:
        out = df.copy()
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        out = out.dropna(subset=["date", "close"])
        out = out.sort_values("date").reset_index(drop=True)
        return out

    if period_upper in ["W", "WEEKLY", "WEEK"]:
        rule = "W-FRI"   # 美股周线常用周五作为周结束日
    elif period_upper in ["M", "MONTHLY", "MONTH"]:
        rule = "ME"      # 月末；date 最终会被替换成周期内最后一个实际交易日
    else:
        raise ValueError("period 只能是 D/daily、W/weekly、M/monthly")

    work = df.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["date", "close"])
    work = work.sort_values("date")

    # 保留周期内最后一个实际交易日，便于把周线/月线 RSI 信号标到日线图上。
    work["period_last_trade_date"] = work["date"]
    work = work.set_index("date")

    agg_dict = {"period_last_trade_date": "last"}

    if "open" in work.columns:
        agg_dict["open"] = "first"
    if "high" in work.columns:
        agg_dict["high"] = "max"
    if "low" in work.columns:
        agg_dict["low"] = "min"
    if "close" in work.columns:
        agg_dict["close"] = "last"
    if "volume" in work.columns:
        agg_dict["volume"] = "sum"
    if "amount" in work.columns:
        agg_dict["amount"] = "sum"

    out = work.resample(rule).agg(agg_dict)
    out = out.dropna(subset=["close"]).reset_index(drop=True)
    out = out.rename(columns={"period_last_trade_date": "date"})
    out = out.sort_values("date").reset_index(drop=True)

    return out


def add_rsi(
    df: pd.DataFrame,
    close_col: str = "close",
    rsi_col: str = "RSI",
    window: int = 9,
) -> pd.DataFrame:
    """
    外部调用时，如果已经有自己的行情数据，可以直接用这个函数添加 RSI。
    """
    df = df.copy()

    if close_col not in df.columns:
        raise KeyError(f"数据中找不到收盘价列: {close_col}")

    df[rsi_col] = compute_rsi_wilder(df[close_col], window=window)

    return df


def build_period_rsi_df(
    df: pd.DataFrame,
    period: str,
    rsi_col: str,
    rsi_window: int,
) -> pd.DataFrame:
    """
    先把日线数据转换为指定周期，再基于该周期收盘价计算 RSI。
    period="D" 得到日线 RSI；
    period="W" 得到周线 RSI；
    period="M" 得到月线 RSI。
    """
    period_df = resample_ohlcv(df, period=period)
    period_df = add_rsi(
        period_df,
        close_col="close",
        rsi_col=rsi_col,
        window=rsi_window,
    )
    return period_df


def extract_rsi_signal_points(
    df: pd.DataFrame,
    period_name: str,
    rsi_col: str,
    rsi_window: int,
    rsi_high: float,
    rsi_low: float,
    start_date=None,
    end_date=None,
) -> pd.DataFrame:
    """
    提取 RSI 信号点。

    RSI > rsi_high：高位信号
    RSI < rsi_low ：低位信号

    返回表中会保留：
    period、date、close、RSI、signal、rsi_window、rsi_high、rsi_low
    """
    if df is None or df.empty:
        return pd.DataFrame()

    if rsi_col not in df.columns:
        raise KeyError(f"数据中找不到 RSI 列: {rsi_col}")

    signal_df = df.copy()
    signal_df["date"] = pd.to_datetime(signal_df["date"], errors="coerce")
    signal_df["close"] = pd.to_numeric(signal_df["close"], errors="coerce")
    signal_df[rsi_col] = pd.to_numeric(signal_df[rsi_col], errors="coerce")
    signal_df = signal_df.dropna(subset=["date", "close", rsi_col])

    if start_date is not None:
        signal_df = signal_df[signal_df["date"] >= pd.to_datetime(start_date)]

    if end_date is not None:
        signal_df = signal_df[signal_df["date"] <= pd.to_datetime(end_date)]

    signal_df["signal"] = np.where(
        signal_df[rsi_col] > rsi_high,
        "high",
        np.where(signal_df[rsi_col] < rsi_low, "low", "")
    )

    signal_df = signal_df[signal_df["signal"].isin(["high", "low"])].copy()
    signal_df["period"] = period_name
    signal_df["rsi_window"] = rsi_window
    signal_df["rsi_high"] = rsi_high
    signal_df["rsi_low"] = rsi_low
    signal_df = signal_df.reset_index(drop=True)

    return signal_df


def build_signal_table(
    daily_signal_df: Optional[pd.DataFrame] = None,
    weekly_signal_df: Optional[pd.DataFrame] = None,
    monthly_signal_df: Optional[pd.DataFrame] = None,
    daily_rsi_col: str = "RSI",
    weekly_rsi_col: str = "RSI_W",
    monthly_rsi_col: str = "RSI_M",
) -> pd.DataFrame:
    """
    整理日线、周线、月线 RSI 信号表，方便打印和输出表格图片。
    """
    parts = []

    for signal_df, rsi_col in [
        (daily_signal_df, daily_rsi_col),
        (weekly_signal_df, weekly_rsi_col),
        (monthly_signal_df, monthly_rsi_col),
    ]:
        if signal_df is None or signal_df.empty:
            continue

        tmp = signal_df.copy()
        tmp["date"] = pd.to_datetime(tmp["date"], errors="coerce")
        tmp["close"] = pd.to_numeric(tmp["close"], errors="coerce")
        tmp[rsi_col] = pd.to_numeric(tmp[rsi_col], errors="coerce")

        tmp["Date"] = tmp["date"].dt.strftime("%Y-%m-%d")
        tmp["Period"] = tmp["period"]
        tmp["Signal"] = np.where(tmp["signal"] == "high", "High", "Low")
        tmp["Close"] = tmp["close"].map(lambda x: f"{x:.2f}")
        tmp["RSI"] = tmp[rsi_col].map(lambda x: f"{x:.2f}")
        tmp["RSI Window"] = tmp["rsi_window"].astype(int)
        tmp["Threshold"] = np.where(
            tmp["signal"] == "high",
            ">" + tmp["rsi_high"].map(lambda x: f"{x:g}"),
            "<" + tmp["rsi_low"].map(lambda x: f"{x:g}"),
        )

        parts.append(
            tmp[[
                "date",
                "Period",
                "Date",
                "Signal",
                "Close",
                "RSI",
                "RSI Window",
                "Threshold",
            ]]
        )

    if not parts:
        return pd.DataFrame(columns=[
            "Period", "Date", "Signal", "Close", "RSI", "RSI Window", "Threshold"
        ])

    table_df = pd.concat(parts, ignore_index=True)
    table_df = table_df.sort_values(["date", "Period"]).reset_index(drop=True)
    table_df = table_df.drop(columns=["date"])

    return table_df


def print_signal_dates(
    signal_table: pd.DataFrame,
) -> None:
    """
    打印具体信号日期。
    """
    print("\nRSI 信号日期明细：")

    if signal_table is None or signal_table.empty:
        print("无日线、周线或月线 RSI 高低位信号。")
        return

    for period in ["Daily", "Weekly", "Monthly"]:
        sub = signal_table[signal_table["Period"] == period]
        print(f"\n[{period}]")

        if sub.empty:
            print("无信号")
            continue

        print(sub[["Date", "Signal", "Close", "RSI", "RSI Window", "Threshold"]].to_string(index=False))


def save_signal_table_image(
    signal_table: pd.DataFrame,
    output_file: str = "output/rsi_signal_table.png",
    title: str = "RSI Signal Dates",
    max_rows: Optional[int] = 80,
    compliance_notice_text: str = "个人模型，数据来源于网络公开资料，不构成任何投资建议",
    compliance_notice_color: str = "#2f3b52",
    compliance_notice_fontsize: int = 12,
    dpi: int = 180,
) -> None:
    """
    生成好看的表格图片。

    max_rows:
        表格最多显示多少行。超过时默认保留最近 max_rows 行，避免图片过高。
    """
    _setup_chinese_font()
    output_path = Path(output_file)
    if output_path.parent and str(output_path.parent) != ".":
        output_path.parent.mkdir(parents=True, exist_ok=True)

    if signal_table is None or signal_table.empty:
        fig, ax = plt.subplots(figsize=(10, 2.2))
        ax.axis("off")
        ax.set_title(title, fontsize=14, fontweight="bold", pad=12)
        notice_artist = None
        if compliance_notice_text:
            notice_artist = fig.text(
                0.5,
                0.035,
                str(compliance_notice_text).strip(),
                ha="center",
                va="bottom",
                fontsize=compliance_notice_fontsize,
                color=compliance_notice_color,
                fontweight="bold",
            )
        ax.text(
            0.5,
            0.45,
            "No RSI signals in the selected range.",
            ha="center",
            va="center",
            fontsize=12,
        )
        fig.tight_layout(rect=[0, 0.08, 1, 1])
        extra_artists = [notice_artist] if notice_artist is not None else None
        fig.savefig(output_file, dpi=dpi, bbox_inches="tight", bbox_extra_artists=extra_artists)
        plt.close(fig)
        print(f"信号表图片已保存: {output_file}")
        return

    table_df = signal_table.copy()

    note = ""
    if max_rows is not None and len(table_df) > max_rows:
        table_df = table_df.tail(max_rows).copy()
        note = f"  Last {max_rows} rows shown."

    nrows = len(table_df)
    fig_h = max(2.8, min(0.36 * nrows + 1.8, 22))
    fig_w = 12

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.axis("off")
    ax.set_title(f"{title}{note}", fontsize=15, fontweight="bold", pad=14)

    table = ax.table(
        cellText=table_df.values,
        colLabels=table_df.columns,
        cellLoc="center",
        colLoc="center",
        loc="center",
    )

    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.28)

    # 表头和信号行做轻微美化。
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#d9d9d9")

        if row == 0:
            cell.set_facecolor("#2f3b52")
            cell.set_text_props(color="white", weight="bold")
        else:
            signal_value = table_df.iloc[row - 1]["Signal"]
            if signal_value == "High":
                cell.set_facecolor("#fff2f2")
            elif signal_value == "Low":
                cell.set_facecolor("#f2f2f2")

    notice_artist = None
    if compliance_notice_text:
        notice_artist = fig.text(
            0.5,
            0.025,
            str(compliance_notice_text).strip(),
            ha="center",
            va="bottom",
            fontsize=compliance_notice_fontsize,
            color=compliance_notice_color,
            fontweight="bold",
        )

    fig.tight_layout(rect=[0, 0.06, 1, 1])
    extra_artists = [notice_artist] if notice_artist is not None else None
    fig.savefig(output_file, dpi=dpi, bbox_inches="tight", bbox_extra_artists=extra_artists)
    plt.close(fig)

    print(f"信号表图片已保存: {output_file}")


def _latest_valid_rsi_row(
    df: Optional[pd.DataFrame],
    rsi_col: str,
):
    """
    返回最后一个 RSI 非空的行。
    """
    if df is None or df.empty or rsi_col not in df.columns:
        return None

    tmp = df.copy()
    tmp[rsi_col] = pd.to_numeric(tmp[rsi_col], errors="coerce")
    tmp = tmp.dropna(subset=[rsi_col])

    if tmp.empty:
        return None

    return tmp.iloc[-1]


def print_latest_summary(
    df: pd.DataFrame,
    symbol: str,
    daily_rsi_col: str = "RSI",
    daily_rsi_window: int = 9,
    weekly_df: Optional[pd.DataFrame] = None,
    weekly_rsi_col: str = "RSI_W",
    weekly_rsi_window: int = 9,
    monthly_df: Optional[pd.DataFrame] = None,
    monthly_rsi_col: str = "RSI_M",
    monthly_rsi_window: int = 9,
) -> None:
    """
    打印最新行情摘要，并增加日、周、月最新 RSI。
    """
    if df is None or df.empty:
        raise ValueError("输入数据为空，无法打印摘要。")

    latest = df.iloc[-1]

    daily_latest = _latest_valid_rsi_row(df, daily_rsi_col)
    weekly_latest = _latest_valid_rsi_row(weekly_df, weekly_rsi_col)
    monthly_latest = _latest_valid_rsi_row(monthly_df, monthly_rsi_col)

    print("=" * 60)
    print(f"标的: {symbol}")
    print(f"日期: {latest['date'].date()}")
    print(f"最新收盘: {latest['close']:.2f}")

    if "volume" in df.columns and pd.notna(latest["volume"]):
        print(f"最新交易量: {latest['volume']:,.0f}")
    else:
        print("最新交易量: 无有效数据")

    if daily_latest is not None:
        print(
            f"最新日线 RSI({daily_rsi_window}): "
            f"{daily_latest[daily_rsi_col]:.2f} "
            f"[日期: {daily_latest['date'].date()}, 收盘: {daily_latest['close']:.2f}]"
        )
    else:
        print(f"最新日线 RSI({daily_rsi_window}): 无有效数据")

    if weekly_latest is not None:
        print(
            f"最新周线 RSI({weekly_rsi_window}): "
            f"{weekly_latest[weekly_rsi_col]:.2f} "
            f"[周线截止: {weekly_latest['date'].date()}, 周线收盘: {weekly_latest['close']:.2f}]"
        )
    else:
        print(f"最新周线 RSI({weekly_rsi_window}): 无有效数据")

    if monthly_latest is not None:
        print(
            f"最新月线 RSI({monthly_rsi_window}): "
            f"{monthly_latest[monthly_rsi_col]:.2f} "
            f"[月线截止: {monthly_latest['date'].date()}, 月线收盘: {monthly_latest['close']:.2f}]"
        )
    else:
        print(f"最新月线 RSI({monthly_rsi_window}): 无有效数据")

    print("=" * 60)

    show_cols = [
        col for col in ["date", "open", "high", "low", "close", "volume", daily_rsi_col]
        if col in df.columns
    ]

def _plot_segmented_by_rsi(
    ax,
    df: pd.DataFrame,
    y_col: str,
    rsi_col: str,
    date_col: str,
    rsi_high: float,
    rsi_low: float,
    ylabel: str,
    title: str,
    linewidth: float = 1.8,
    show_points: bool = True,
    color_by_rsi: bool = True,
):
    """
    用连续线段绘制曲线，并根据 RSI 阈值改变线段颜色。
    RSI > rsi_high: 红色
    RSI < rsi_low : 黑色
    其他区间     : 蓝色

    color_by_rsi=False 时，线段统一使用蓝色。
    这可以避免关闭日线信号后，线段仍然显示日线信号色的问题。
    """
    cols = list(dict.fromkeys([date_col, y_col, rsi_col]))
    plot_df = df[cols].copy()
    # 统一类型并过滤缺失值，避免 LineCollection 接收非法坐标。
    plot_df[date_col] = pd.to_datetime(plot_df[date_col], errors="coerce")
    plot_df[y_col] = pd.to_numeric(plot_df[y_col], errors="coerce")
    if rsi_col != y_col:
        plot_df[rsi_col] = pd.to_numeric(plot_df[rsi_col], errors="coerce")
    plot_df = plot_df.dropna(subset=[date_col, y_col, rsi_col]).reset_index(drop=True)
    if len(plot_df) < 2:
        return

    x = mdates.date2num(plot_df[date_col])
    y = plot_df[y_col].to_numpy(dtype=float)
    rsi = plot_df[rsi_col].to_numpy(dtype=float)
    # 把连续点转换成线段，才能按每段结束点 RSI 独立上色。
    points = np.column_stack([x, y]).reshape(-1, 1, 2)
    segments = np.concatenate([points[:-1], points[1:]], axis=1)

    if color_by_rsi:
        colors = np.where(
            rsi[1:] > rsi_high,
            "red",
            np.where(rsi[1:] < rsi_low, "black", "#1f77b4")
        )
    else:
        colors = np.array(["#1f77b4"] * (len(rsi) - 1))

    lc = LineCollection(
        segments,
        colors=colors,
        linewidths=linewidth,
        zorder=2,
    )
    ax.add_collection(lc)
    ax.update_datalim(np.column_stack([x, y]))
    ax.autoscale_view()
    ax.xaxis_date()

    if show_points and color_by_rsi:
        mask_high = plot_df[rsi_col] > rsi_high
        mask_low = plot_df[rsi_col] < rsi_low

        ax.scatter(
            plot_df.loc[mask_high, date_col],
            plot_df.loc[mask_high, y_col],
            color="red",
            s=18,
            zorder=3,
        )

        ax.scatter(
            plot_df.loc[mask_low, date_col],
            plot_df.loc[mask_low, y_col],
            color="black",
            s=18,
            zorder=3,
        )

    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.margins(x=0.01, y=0.10)


def _annotate_period_rsi_signals(
    ax,
    signal_df: pd.DataFrame,
    rsi_col: str,
    rsi_high: float,
    rsi_low: float,
    high_marker: str,
    low_marker: str,
    high_offset_points: int,
    low_offset_points: int,
    marker_fontsize: float,
) -> None:
    """
    在价格曲线上方或下方标注周期 RSI 信号。

    这里使用 ax.annotate 的 offset points，而不是直接把 y 值加减一个固定比例。
    这样三角形或星星会和线段保持固定的屏幕距离，可以明显减少与线段重叠的问题。

    high_marker:
        RSI > rsi_high 时使用的标记。
    low_marker:
        RSI < rsi_low 时使用的标记。
    """
    if signal_df is None or signal_df.empty:
        return

    signal_df = signal_df.copy()
    signal_df["date"] = pd.to_datetime(signal_df["date"], errors="coerce")
    signal_df["close"] = pd.to_numeric(signal_df["close"], errors="coerce")
    signal_df[rsi_col] = pd.to_numeric(signal_df[rsi_col], errors="coerce")
    signal_df = signal_df.dropna(subset=["date", "close", rsi_col])

    if signal_df.empty:
        return

    mask_high = signal_df[rsi_col] > rsi_high
    mask_low = signal_df[rsi_col] < rsi_low

    for _, row in signal_df.loc[mask_high].iterrows():
        ax.annotate(
            high_marker,
            xy=(row["date"], row["close"]),
            xytext=(0, high_offset_points),
            textcoords="offset points",
            ha="center",
            va="bottom",
            color="red",
            fontsize=marker_fontsize,
            fontweight="bold",
            zorder=6,
            annotation_clip=False,
        )

    for _, row in signal_df.loc[mask_low].iterrows():
        ax.annotate(
            low_marker,
            xy=(row["date"], row["close"]),
            xytext=(0, -abs(low_offset_points)),
            textcoords="offset points",
            ha="center",
            va="top",
            color="black",
            fontsize=marker_fontsize,
            fontweight="bold",
            zorder=6,
            annotation_clip=False,
        )


def _plot_volume_bar(ax, df: pd.DataFrame) -> None:
    """
    绘制成交量柱状图。

    如果数据源没有成交量，或成交量全为空/全为 0，则不画空白图，
    而是在子图中显示 No valid volume data。
    """
    if df is None or df.empty or "volume" not in df.columns:
        ax.text(
            0.5,
            0.5,
            "No valid volume data",
            transform=ax.transAxes,
            ha="center",
            va="center",
        )
        ax.set_title("成交量")
        ax.set_ylabel("成交量")
        return

    volume = pd.to_numeric(df["volume"], errors="coerce")
    valid = volume.notna() & (volume != 0)

    if valid.any():
        ax.bar(df.loc[valid, "date"], volume.loc[valid], color="#1f77b4")
    else:
        ax.text(
            0.5,
            0.5,
            "No valid volume data",
            transform=ax.transAxes,
            ha="center",
            va="center",
        )

    ax.set_title("成交量")
    ax.set_ylabel("成交量")


def plot_analysis(
    df: pd.DataFrame,
    symbol: str,
    output_file: str = "nasdaq_analysis.png",
    output_two_file="output/two_nasdaq_analysis.png",
    display_name: Optional[str] = None,
    symbol_name_map: Optional[dict] = None,
    rsi_col: str = "RSI",
    rsi_high: float = 80,
    rsi_low: float = 30,
    show_points: bool = True,
    show_plot: bool = True,
    daily_signal_df: Optional[pd.DataFrame] = None,
    weekly_signal_df: Optional[pd.DataFrame] = None,
    monthly_signal_df: Optional[pd.DataFrame] = None,
    weekly_rsi_col: str = "RSI_W",
    monthly_rsi_col: str = "RSI_M",
    weekly_rsi_high: float = 80,
    weekly_rsi_low: float = 30,
    monthly_rsi_high: float = 80,
    monthly_rsi_low: float = 30,
    show_daily_signals: bool = True,
    show_weekly_signals: bool = True,
    show_monthly_signals: bool = True,
    dpi: int = 180,
):
    """
    输出价格、成交量、RSI 图。曲线按 RSI 阈值连续变色。

    周线/月线标注：
    周线 RSI > weekly_rsi_high：在收盘价线段上方标注红色三角形
    周线 RSI < weekly_rsi_low ：在收盘价线段下方标注黑色三角形
    月线 RSI > monthly_rsi_high：在收盘价线段上方标注红色星星
    月线 RSI < monthly_rsi_low ：在收盘价线段下方标注黑色星星

    周线/月线标记使用屏幕坐标偏移，避免与线段直接重叠。
    """
    if df is None or df.empty:
        raise ValueError("输入数据为空，无法绘图。")

    if rsi_col not in df.columns:
        raise KeyError(f"数据中找不到 RSI 列: {rsi_col}")

    _setup_chinese_font()
    plot_title_name = get_symbol_display_name(
        symbol=symbol,
        display_name=display_name,
        symbol_name_map=symbol_name_map,
    )

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)

    # 关闭日线信号时，价格和 RSI 曲线统一使用蓝色。
    daily_color_by_rsi = bool(show_daily_signals)

    _plot_segmented_by_rsi(
        ax=axes[0],
        df=df,
        y_col="close",
        rsi_col=rsi_col,
        date_col="date",
        rsi_high=rsi_high,
        rsi_low=rsi_low,
        ylabel="收盘价",
        title=f"{plot_title_name}",
        show_points=show_points and show_daily_signals,
        color_by_rsi=daily_color_by_rsi,
    )

    # 给周线/月线标记留出上下空间。
    axes[0].margins(y=0.20)
    axes[0].autoscale_view()

    # 在收盘价曲线上叠加周线/月线极端区间信号。
    if show_weekly_signals:
        _annotate_period_rsi_signals(
            ax=axes[0],
            signal_df=weekly_signal_df,
            rsi_col=weekly_rsi_col,
            rsi_high=weekly_rsi_high,
            rsi_low=weekly_rsi_low,
            high_marker="▲",
            low_marker="▼",
            high_offset_points=10,
            low_offset_points=10,
            marker_fontsize=13,
        )

    if show_monthly_signals:
        _annotate_period_rsi_signals(
            ax=axes[0],
            signal_df=monthly_signal_df,
            rsi_col=monthly_rsi_col,
            rsi_high=monthly_rsi_high,
            rsi_low=monthly_rsi_low,
            high_marker="★",
            low_marker="★",
            high_offset_points=15,
            low_offset_points=15,
            marker_fontsize=18,
        )

    _plot_volume_bar(axes[1], df)

    _plot_segmented_by_rsi(
        ax=axes[2],
        df=df,
        y_col=rsi_col,
        rsi_col=rsi_col,
        date_col="date",
        rsi_high=rsi_high,
        rsi_low=rsi_low,
        ylabel="RSI",
        title=f"{rsi_col}",
        show_points=show_points and show_daily_signals,
        color_by_rsi=daily_color_by_rsi,
    )

    # RSI 子图固定 0-100，便于和阈值线对照。
    axes[2].axhline(rsi_high, linestyle="--", linewidth=1, color="red")
    axes[2].axhline(rsi_low, linestyle="--", linewidth=1, color="black")
    axes[2].set_ylim(0, 100)

    plt.tight_layout()

    output_path = Path(output_file)
    if output_path.parent and str(output_path.parent) != ".":
        output_path.parent.mkdir(parents=True, exist_ok=True)

    plt.savefig(output_file, dpi=dpi)

    plt.close(fig)
    
    if output_two_file:
        if df is None or df.empty:
            raise ValueError("输入数据为空，无法绘图。")

        if rsi_col not in df.columns:
            raise KeyError(f"数据中找不到 RSI 列: {rsi_col}")
        fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

        daily_color_by_rsi = bool(show_daily_signals)

        _plot_segmented_by_rsi(
            ax=axes[0],
            df=df,
            y_col="close",
            rsi_col=rsi_col,
            date_col="date",
            rsi_high=rsi_high,
            rsi_low=rsi_low,
            ylabel="收盘价",
            title=f"{plot_title_name}",
            show_points=show_points and show_daily_signals,
            color_by_rsi=daily_color_by_rsi,
        )

        # 给周线/月线标记留出上下空间。
        axes[0].margins(y=0.20)
        axes[0].autoscale_view()

        # 二图版同样在收盘价曲线上叠加周期信号。
        if show_weekly_signals:
            _annotate_period_rsi_signals(
                ax=axes[0],
                signal_df=weekly_signal_df,
                rsi_col=weekly_rsi_col,
                rsi_high=weekly_rsi_high,
                rsi_low=weekly_rsi_low,
                high_marker="▲",
                low_marker="▼",
                high_offset_points=10,
                low_offset_points=10,
                marker_fontsize=13,
            )

        if show_monthly_signals:
            _annotate_period_rsi_signals(
                ax=axes[0],
                signal_df=monthly_signal_df,
                rsi_col=monthly_rsi_col,
                rsi_high=monthly_rsi_high,
                rsi_low=monthly_rsi_low,
                high_marker="★",
                low_marker="★",
                high_offset_points=15,
                low_offset_points=15,
                marker_fontsize=18,
            )

        _plot_volume_bar(axes[1], df)

        plt.tight_layout()

        output_path = Path(output_two_file)
        if output_path.parent and str(output_path.parent) != ".":
            output_path.parent.mkdir(parents=True, exist_ok=True)

        plt.savefig(output_two_file, dpi=dpi)

        if show_plot:
            plt.show()

        plt.close(fig)
        
def rsi_analyze_index(
    symbol: str = ".IXIC",
    display_name: Optional[str] = None,
    symbol_name_map: Optional[dict] = None,
    days: int = 180,
    rsi_window: int = 9,
    rsi_col: str = "RSI",
    rsi_high: float = 80,
    rsi_low: float = 30,
    output_file: str = "output/nasdaq_analysis.png",
    output_two_file: str = "output/two_nasdaq_analysis.png",
    show_points: bool = True,
    do_print: bool = True,
    do_plot: bool = True,
    show_plot: bool = True,
    show_period_markers: bool = True,
    weekly_rsi_col: str = "RSI_W",
    monthly_rsi_col: str = "RSI_M",
    signal_fetch_days: Optional[int] = None,
    return_signals: bool = False,

    # 数据缓存和重试参数。
    cache_dir: str = "cache",
    retry: int = 3,
    use_cache: bool = True,
    allow_eastmoney: bool = False,
    include_realtime: bool = False,

    # 日线 RSI 参数；未单独设置时沿用 rsi_window/rsi_high/rsi_low。
    daily_rsi_window: Optional[int] = None,
    daily_rsi_high: Optional[float] = None,
    daily_rsi_low: Optional[float] = None,

    # 周线 RSI 参数，可与日线不同。
    weekly_rsi_window: Optional[int] = None,
    weekly_rsi_high: Optional[float] = None,
    weekly_rsi_low: Optional[float] = None,

    # 月线 RSI 参数，可与日线/周线不同。
    monthly_rsi_window: Optional[int] = None,
    monthly_rsi_high: Optional[float] = None,
    monthly_rsi_low: Optional[float] = None,

    # 分别控制是否显示/输出日、周、月信号。
    show_daily_signals: bool = True,
    show_weekly_signals: bool = True,
    show_monthly_signals: bool = True,

    # 控制是否打印具体信号日期、是否生成信号表图片。
    print_signal_dates_flag: bool = False,
    save_signal_table: bool = True,
    signal_table_file: str = "output/rsi_signal_table.png",
    signal_table_max_rows: Optional[int] = 80,
) -> pd.DataFrame:
    """
    外部调用主函数。

    示例：
    from index_rsi_module import analyze_index

    hist = rsi_analyze_index(
        symbol=".IXIC",
        days=180,
        rsi_window=9,
        rsi_high=80,
        rsi_low=30,
        output_file="nasdaq_analysis.png",
    )

    返回：
        默认返回带有 RSI 列的日线行情 DataFrame。

    关键逻辑：
        1. 日线 RSI 先在 raw_hist 长历史上计算，再截取最近 days，避免展示区间左侧失真。
        2. 日线、周线、月线 RSI 均使用同一个 Wilder RSI。
        3. print_latest_summary 会同时打印最新日线 RSI、最新周线 RSI、最新月线 RSI。
        4. return_signals=True 时，返回：
           hist, daily_signal_df, weekly_signal_df, monthly_signal_df, signal_table, weekly_df, monthly_df
    """
    # 兼容旧参数：没有单独设置时，默认使用 rsi_window/rsi_high/rsi_low。
    daily_rsi_window = rsi_window if daily_rsi_window is None else daily_rsi_window
    daily_rsi_high = rsi_high if daily_rsi_high is None else daily_rsi_high
    daily_rsi_low = rsi_low if daily_rsi_low is None else daily_rsi_low

    weekly_rsi_window = daily_rsi_window if weekly_rsi_window is None else weekly_rsi_window
    weekly_rsi_high = daily_rsi_high if weekly_rsi_high is None else weekly_rsi_high
    weekly_rsi_low = daily_rsi_low if weekly_rsi_low is None else weekly_rsi_low

    monthly_rsi_window = daily_rsi_window if monthly_rsi_window is None else monthly_rsi_window
    monthly_rsi_high = daily_rsi_high if monthly_rsi_high is None else monthly_rsi_high
    monthly_rsi_low = daily_rsi_low if monthly_rsi_low is None else monthly_rsi_low

    plot_title_name = get_symbol_display_name(
        symbol=symbol,
        display_name=display_name,
        symbol_name_map=symbol_name_map,
    )

    # show_period_markers=False 时同时关闭周线和月线标记。
    if not show_period_markers:
        show_weekly_signals = False
        show_monthly_signals = False

    # 月线 RSI 需要比最终展示区间更长的日线数据。
    if signal_fetch_days is None:
        signal_fetch_days = max(
            days,
            days + daily_rsi_window + 60,
            days + weekly_rsi_window * 7 + 120,
            days + monthly_rsi_window * 31 + 365,
            1200,
        )

    raw_hist = get_index_akshare(
        symbol=symbol,
        days=signal_fetch_days,
        cache_dir=cache_dir,
        retry=retry,
        use_cache=use_cache,
        allow_eastmoney=allow_eastmoney,
        include_realtime=include_realtime,
    )

    # 先在长历史上计算日线 RSI，再截取最近 days，避免左侧 RSI 失真。
    raw_daily_df = build_period_rsi_df(
        df=raw_hist,
        period="D",
        rsi_col=rsi_col,
        rsi_window=daily_rsi_window,
    )

    hist = raw_daily_df.tail(days).copy()

    # 周线 RSI 和月线 RSI 也在完整 raw_hist 上计算。
    weekly_df = build_period_rsi_df(
        df=raw_hist,
        period="W",
        rsi_col=weekly_rsi_col,
        rsi_window=weekly_rsi_window,
    )

    monthly_df = build_period_rsi_df(
        df=raw_hist,
        period="M",
        rsi_col=monthly_rsi_col,
        rsi_window=monthly_rsi_window,
    )

    start_date = hist["date"].min()
    end_date = hist["date"].max()

    daily_signal_df = None
    weekly_signal_df = None
    monthly_signal_df = None

    if show_daily_signals:
        daily_signal_df = extract_rsi_signal_points(
            df=hist,
            period_name="Daily",
            rsi_col=rsi_col,
            rsi_window=daily_rsi_window,
            rsi_high=daily_rsi_high,
            rsi_low=daily_rsi_low,
            start_date=start_date,
            end_date=end_date,
        )

    if show_weekly_signals:
        weekly_signal_df = extract_rsi_signal_points(
            df=weekly_df,
            period_name="Weekly",
            rsi_col=weekly_rsi_col,
            rsi_window=weekly_rsi_window,
            rsi_high=weekly_rsi_high,
            rsi_low=weekly_rsi_low,
            start_date=start_date,
            end_date=end_date,
        )

    if show_monthly_signals:
        monthly_signal_df = extract_rsi_signal_points(
            df=monthly_df,
            period_name="Monthly",
            rsi_col=monthly_rsi_col,
            rsi_window=monthly_rsi_window,
            rsi_high=monthly_rsi_high,
            rsi_low=monthly_rsi_low,
            start_date=start_date,
            end_date=end_date,
        )

    signal_table = build_signal_table(
        daily_signal_df=daily_signal_df,
        weekly_signal_df=weekly_signal_df,
        monthly_signal_df=monthly_signal_df,
        daily_rsi_col=rsi_col,
        weekly_rsi_col=weekly_rsi_col,
        monthly_rsi_col=monthly_rsi_col,
    )

    if do_print:
        print_latest_summary(
            df=hist,
            symbol=symbol,
            daily_rsi_col=rsi_col,
            daily_rsi_window=daily_rsi_window,
            weekly_df=weekly_df,
            weekly_rsi_col=weekly_rsi_col,
            weekly_rsi_window=weekly_rsi_window,
            monthly_df=monthly_df,
            monthly_rsi_col=monthly_rsi_col,
            monthly_rsi_window=monthly_rsi_window,
        )

        if print_signal_dates_flag:
            print_signal_dates(signal_table)

    if save_signal_table:
        save_signal_table_image(
            signal_table=signal_table,
            output_file=signal_table_file,
            title=f"{plot_title_name} RSI 信号日期",
            max_rows=signal_table_max_rows,
        )

    if do_plot:
        plot_analysis(
            df=hist,
            symbol=symbol,
            output_file=output_file,
            output_two_file=output_two_file,
            display_name=plot_title_name,
            symbol_name_map=symbol_name_map,
            rsi_col=rsi_col,
            rsi_high=daily_rsi_high,
            rsi_low=daily_rsi_low,
            show_points=show_points,
            show_plot=show_plot,
            daily_signal_df=daily_signal_df,
            weekly_signal_df=weekly_signal_df,
            monthly_signal_df=monthly_signal_df,
            weekly_rsi_col=weekly_rsi_col,
            monthly_rsi_col=monthly_rsi_col,
            weekly_rsi_high=weekly_rsi_high,
            weekly_rsi_low=weekly_rsi_low,
            monthly_rsi_high=monthly_rsi_high,
            monthly_rsi_low=monthly_rsi_low,
            show_daily_signals=show_daily_signals,
            show_weekly_signals=show_weekly_signals,
            show_monthly_signals=show_monthly_signals,
        )

    if return_signals:
        return hist, daily_signal_df, weekly_signal_df, monthly_signal_df, signal_table, weekly_df, monthly_df

    return hist


# 兼容原先说明中的 analyze_index 名称。
analyze_index = rsi_analyze_index


def main():
    symbol = ".IXIC"  # 纳斯达克综合指数

    hist = rsi_analyze_index(
        symbol=symbol,
        days=180,
        rsi_window=9,
        rsi_col="RSI",
        rsi_high=80,
        rsi_low=30,
        output_file="output/nasdaq_analysis.png",
        show_points=True,
        do_print=True,
        do_plot=True,
        show_plot=True,

        # 日线、周线、月线参数可分别控制。
        daily_rsi_window=9,
        daily_rsi_high=80,
        daily_rsi_low=30,

        weekly_rsi_window=8,
        weekly_rsi_high=80,
        weekly_rsi_low=30,

        monthly_rsi_window=9,
        monthly_rsi_high=80,
        monthly_rsi_low=30,

        show_daily_signals=True,
        show_weekly_signals=True,
        show_monthly_signals=True,

        print_signal_dates_flag=True,
        save_signal_table=True,
        signal_table_file="output/rsi_signal_table.png",
    )

    return hist


if __name__ == "__main__":
    main()
