"""
stock_analysis.py
股票分析模块：只负责生成 RSI 图片、信号表，并返回行情摘要。
不负责每日语录；每日语录请使用 quote_manager.py。

行情摘要包含：
1. 保留原有“最新交易日、最新收盘、最新涨跌、最新成交量”；
2. 新增常用量化因子：
   - MA20 / MA60 趋势状态
   - MA20 五日斜率
   - 20日动量
   - 60日动量
   - 20日年化波动率
   - 20日成交量比
   - 60日高点回撤
   - ATR14 百分比
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from tools.rsi_module import rsi_analyze_index
from tools.configs.rsi_configs import RSI_ANALYSIS_CONFIGS


@dataclass
class StockAnalysisResult:
    name: str
    hist: pd.DataFrame
    weekly_df: pd.DataFrame
    monthly_df: pd.DataFrame
    daily_signal_df: pd.DataFrame
    weekly_signal_df: pd.DataFrame
    monthly_signal_df: pd.DataFrame
    signal_table: pd.DataFrame

def _prepare_price_df(hist: pd.DataFrame) -> pd.DataFrame:
    """统一行情字段类型，后续因子计算都基于这份清洗结果。"""
    if hist is None or hist.empty:
        return pd.DataFrame()

    df = hist.copy()

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    if "prev_close" not in df.columns and "prevclose" in df.columns:
        df["prev_close"] = df["prevclose"]

    for col in ["open", "high", "low", "close", "volume", "amount", "prev_close", "prevclose"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["date", "close"])
    df = df.sort_values("date").reset_index(drop=True)

    return df


def latest_price_change(hist: pd.DataFrame):
    """
    计算最新交易日相对上一交易日的涨跌额与涨幅。
    """
    tmp = _prepare_price_df(hist)

    if len(tmp) < 2:
        return None, None, None, None, ""

    latest = tmp.iloc[-1]
    previous = tmp.iloc[-2]

    latest_close = float(latest["close"])
    previous_close = float(previous["close"])
    compare_label = ""

    is_realtime = str(latest.get("is_realtime", "")).strip().lower() in {"true", "1", "yes"}
    realtime_prev_close = latest.get("prev_close", np.nan)
    if is_realtime and pd.notna(realtime_prev_close) and float(realtime_prev_close) > 0:
        previous_close = float(realtime_prev_close)
        previous_date = pd.to_datetime(previous["date"]).date()
        historical_previous_close = float(previous["close"])
        if abs(historical_previous_close - previous_close) <= max(abs(previous_close) * 1e-6, 1e-6):
            compare_label = f"{previous_date} 收盘（实时昨收）"
        else:
            compare_label = "实时昨收"

    if previous_close == 0:
        return latest, previous, None, None, compare_label

    change = latest_close - previous_close
    pct = change / previous_close * 100

    return latest, previous, change, pct, compare_label


def add_quant_factors(hist: pd.DataFrame) -> pd.DataFrame:
    """
    给日线行情表增加常用量化因子。
    """
    df = _prepare_price_df(hist)

    if df.empty:
        return df

    close = df["close"]

    df["MA20"] = close.rolling(20, min_periods=20).mean()
    df["MA60"] = close.rolling(60, min_periods=60).mean()
    df["MA20_slope_5_pct"] = (df["MA20"] / df["MA20"].shift(5) - 1) * 100

    df["momentum_20_pct"] = (close / close.shift(20) - 1) * 100
    df["momentum_60_pct"] = (close / close.shift(60) - 1) * 100

    daily_ret = close.pct_change()
    df["vol_20_ann_pct"] = daily_ret.rolling(20, min_periods=20).std() * np.sqrt(252) * 100

    if "volume" in df.columns:
        volume = pd.to_numeric(df["volume"], errors="coerce")
        volume_ma20 = volume.rolling(20, min_periods=20).mean()
        df["volume_ratio_20"] = volume / volume_ma20
    else:
        df["volume_ratio_20"] = np.nan

    high_60 = close.rolling(60, min_periods=60).max()
    df["drawdown_60_pct"] = (close / high_60 - 1) * 100

    if "high" in df.columns and "low" in df.columns:
        high = pd.to_numeric(df["high"], errors="coerce")
        low = pd.to_numeric(df["low"], errors="coerce")
        prev_close = close.shift(1)

        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        df["ATR14"] = tr.rolling(14, min_periods=14).mean()
        df["ATR14_pct"] = df["ATR14"] / close * 100
    else:
        df["ATR14"] = np.nan
        df["ATR14_pct"] = np.nan

    return df


def _latest_factor_row(hist: pd.DataFrame):
    df = add_quant_factors(hist)

    if df.empty:
        return None

    return df.iloc[-1]


def _format_signed_number(value, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "无有效数据"
    return f"{float(value):+.{digits}f}"


def _format_signed_pct(value, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "无有效数据"
    return f"{float(value):+.{digits}f}%"


def _format_pct(value, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "无有效数据"
    return f"{float(value):.{digits}f}%"


def _format_ratio(value, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "无有效数据"
    return f"{float(value):.{digits}f} 倍"


def _trend_state(row) -> str:
    if row is None:
        return "无有效数据"

    close = row.get("close", np.nan)
    ma20 = row.get("MA20", np.nan)
    ma60 = row.get("MA60", np.nan)

    if pd.isna(close) or pd.isna(ma20) or pd.isna(ma60):
        return "均线数据不足"

    above20 = close >= ma20
    above60 = close >= ma60

    if above20 and above60:
        return "收盘价高于 MA20 / MA60，趋势偏强"
    if above20 and not above60:
        return "收盘价高于 MA20、低于 MA60，短线修复但中期仍弱"
    if not above20 and above60:
        return "收盘价低于 MA20、高于 MA60，短线转弱但中期尚可"
    return "收盘价低于 MA20 / MA60，趋势偏弱"


def format_stock_factor_text(
    result: StockAnalysisResult,
    include_basic: bool = True,
    include_factors: bool = True,
) -> str:
    """
    生成单个标的的邮件正文文字。

    include_basic=True:
        最新交易日、最新收盘、最新涨跌、最新成交量

    include_factors=True:
        趋势、动量、波动率、成交量比、回撤、ATR
    """
    latest, previous, change, pct, compare_label = latest_price_change(result.hist)

    if latest is None:
        return f"【{result.name}】\n无有效行情数据"

    factor_row = _latest_factor_row(result.hist)

    latest_date = pd.to_datetime(latest["date"]).date()
    latest_close = float(latest["close"])

    lines = [f"【{result.name}】"]

    if include_basic:
        lines.extend(
            [
                f"最新交易日：{latest_date}",
                f"最新收盘：{latest_close:.2f}",
            ]
        )

        if previous is not None:
            previous_date = pd.to_datetime(previous["date"]).date()
            compare_label = compare_label or f"{previous_date} 收盘"
            lines.append(
                "最新涨跌："
                f"{_format_signed_number(change)} "
                f"（{_format_signed_pct(pct)}，对比 {compare_label}）"
            )

        if "volume" in result.hist.columns and pd.notna(latest.get("volume", None)):
            lines.append(f"最新成交量：{float(latest['volume']):,.0f}")
        else:
            lines.append("最新成交量：无有效数据")

    if include_factors:
        if factor_row is None:
            lines.append("量化因子：无有效数据")
        else:
            lines.extend(
                [
                    f"趋势状态：{_trend_state(factor_row)}",
                    f"MA20斜率：{_format_signed_pct(factor_row.get('MA20_slope_5_pct'))}",
                    f"20日动量：{_format_signed_pct(factor_row.get('momentum_20_pct'))}",
                    f"60日动量：{_format_signed_pct(factor_row.get('momentum_60_pct'))}",
                    f"20日年化波动率：{_format_pct(factor_row.get('vol_20_ann_pct'))}",
                    f"20日成交量比：{_format_ratio(factor_row.get('volume_ratio_20'))}",
                    f"距60日高点回撤：{_format_signed_pct(factor_row.get('drawdown_60_pct'))}",
                    f"ATR14：{_format_pct(factor_row.get('ATR14_pct'))}",
                ]
            )

    return "\n".join(lines)


def build_change_summary_text(
    results: list[StockAnalysisResult],
    intro: str | None = "以下涨跌幅均按最新收盘价相对上一交易日收盘价计算。",
    include_factors: bool = True,
) -> str:
    sections = []

    if intro:
        sections.append(str(intro))

    for result in results:
        sections.append(
            format_stock_factor_text(
                result,
                include_basic=True,
                include_factors=include_factors,
            )
        )

    return "\n\n".join(sections)



def _run_rsi_analysis(config: dict, include_realtime: bool) -> StockAnalysisResult:
    """按配置运行一次 RSI 分析，并统一封装返回结构。"""
    kwargs = dict(config["kwargs"])
    if config.get("use_realtime_param"):
        kwargs["include_realtime"] = include_realtime

    (
        hist,
        daily_signal_df,
        weekly_signal_df,
        monthly_signal_df,
        signal_table,
        weekly_df,
        monthly_df,
    ) = rsi_analyze_index(**kwargs)

    return StockAnalysisResult(
        name=config["name"],
        hist=hist,
        weekly_df=weekly_df,
        monthly_df=monthly_df,
        daily_signal_df=daily_signal_df,
        weekly_signal_df=weekly_signal_df,
        monthly_signal_df=monthly_signal_df,
        signal_table=signal_table,
    )


def build_stock_analysis(
    return_raw: bool = False,
    include_factors: bool = True,
    include_realtime: bool = True,
):
    """
    生成当前关注标的的 RSI 图片和邮件正文。

    配置集中在 `RSI_ANALYSIS_CONFIGS`，后续新增标的只需要复制一项配置；
    `include_realtime=True` 只作用于国内 ETF，用于把盘中行情临时合并到日线末尾。
    """
    results = [
        _run_rsi_analysis(config, include_realtime=include_realtime)
        for config in RSI_ANALYSIS_CONFIGS
    ]

    stock_text = build_change_summary_text(
        results,
        include_factors=include_factors,
    )
    image_paths = [config["image"] for config in RSI_ANALYSIS_CONFIGS]

    if return_raw:
        return stock_text, image_paths, results

    return stock_text, image_paths


if __name__ == "__main__":
    text, images = build_stock_analysis(include_factors=True)
    print(text)
    print("\n图片路径：")
    for image in images:
        print(image)
