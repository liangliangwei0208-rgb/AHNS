"""
手动提前刷新基金限购缓存和持仓缓存。

默认自动刷新策略仍由 FUND_PURCHASE_LIMIT_CACHE_DAYS 控制，本脚本只是显式跳过
新鲜度判断，成功刷新后把该基金的下一次自动刷新时间顺延到本次刷新后 7 天。
持仓缓存也会显式联网刷新一次，并写回 fund_holdings_cache.json；普通业务入口仍按
披露窗口策略低频检查。
"""

from __future__ import annotations

import argparse

from tools.configs.fund_universe_configs import HAIWAI_FUND_CODES
from tools.console_display import fund_progress, print_records_table
from tools.get_top10_holdings import (
    FUND_HOLDINGS_CACHE_DAYS,
    FUND_PURCHASE_LIMIT_CACHE_DAYS,
    fetch_fund_purchase_limit_bulk_map,
    get_fund_purchase_limit,
    get_latest_stock_holdings_df,
    print_purchase_limit_cache_refresh_summary,
)


def _normalize_fund_codes(values: list[str] | None) -> list[str]:
    if not values:
        values = list(HAIWAI_FUND_CODES)

    normalized = []
    seen = set()
    for value in values:
        code = str(value).strip()
        if not code:
            continue
        code = code.zfill(6)
        if code in seen:
            continue
        seen.add(code)
        normalized.append(code)
    return normalized


def _format_holdings_result(df) -> str:
    if df is None:
        return "无持仓"
    quarter = ""
    if "季度" in df.columns and not df.empty:
        try:
            quarter = str(df["季度"].iloc[0])
        except Exception:
            quarter = ""
    suffix = f"，季度={quarter}" if quarter else ""
    return f"{len(df)} 条{suffix}"


def _short_error(exc: Exception, max_len: int = 120) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="手动提前刷新基金限购缓存和持仓缓存")
    parser.add_argument(
        "--fund-code",
        nargs="+",
        help="只刷新指定基金代码；不传则默认刷新海外基金池",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8,
        help="单只基金限购页面请求超时秒数，默认 8 秒",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="同步刷新的基金股票持仓数量，默认前 10 大",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fund_codes = _normalize_fund_codes(args.fund_code)
    total = len(fund_codes)
    top_n = max(int(args.top_n or 10), 1)

    print(
        f"开始手动刷新基金限购缓存和持仓缓存: fund_count={total}, "
        f"purchase_limit_auto_refresh_days={FUND_PURCHASE_LIMIT_CACHE_DAYS}, "
        f"holdings_cache_days={FUND_HOLDINGS_CACHE_DAYS}, top_n={top_n}",
        flush=True,
    )

    rows: list[dict[str, str]] = []
    failures: list[tuple[str, str, str]] = []
    bulk_limit_map = None
    bulk_limit_error = ""

    if total > 0:
        bulk_timeout = max(float(args.timeout or 8), 20.0)
        print(
            f"预取东方财富申购状态批量接口: timeout={bulk_timeout:g}s",
            flush=True,
        )
        try:
            bulk_limit_map = fetch_fund_purchase_limit_bulk_map(
                timeout=bulk_timeout,
                force_refresh=True,
            )
            print(f"申购状态批量接口预取完成: {len(bulk_limit_map)} 条", flush=True)
        except Exception as exc:
            bulk_limit_error = _short_error(exc, max_len=180)
            print(
                f"[WARN] 申购状态批量接口预取失败，将逐只回退页面解析: {bulk_limit_error}",
                flush=True,
            )

    with fund_progress("强制刷新基金缓存", total, transient=False) as progress:
        for fund_code in fund_codes:
            progress.start_item(fund_code)
            ok = True
            error_parts: list[str] = []
            purchase_source = ""
            purchase_error = ""

            progress.set_status(f"{fund_code} 刷新限购缓存")
            try:
                purchase_detail = get_fund_purchase_limit(
                    fund_code=fund_code,
                    timeout=args.timeout,
                    cache_days=FUND_PURCHASE_LIMIT_CACHE_DAYS,
                    cache_enabled=True,
                    force_refresh=True,
                    bulk_limit_map=bulk_limit_map,
                    return_detail=True,
                )
                value = str(purchase_detail.get("value") or "未知")
                purchase_source = str(purchase_detail.get("source") or "")
                purchase_error = str(purchase_detail.get("error") or "")
                if value == "未知":
                    ok = False
                    error = purchase_error or bulk_limit_error or "限购结果未知"
                    error_parts.append(f"限购: {error}")
                    failures.append((fund_code, "限购", error))
            except Exception as exc:
                ok = False
                value = "刷新失败"
                purchase_source = "failed"
                error = _short_error(exc)
                error_parts.append(f"限购: {error}")
                failures.append((fund_code, "限购", error))

            progress.set_status(f"{fund_code} 刷新前{top_n}大持仓缓存")
            try:
                holdings_df = get_latest_stock_holdings_df(
                    fund_code=fund_code,
                    top_n=top_n,
                    holding_cache_days=FUND_HOLDINGS_CACHE_DAYS,
                    cache_enabled=True,
                    force_refresh=True,
                )
                holdings_text = _format_holdings_result(holdings_df)
            except Exception as exc:
                error = _short_error(exc)
                try:
                    holdings_df = get_latest_stock_holdings_df(
                        fund_code=fund_code,
                        top_n=top_n,
                        holding_cache_days=FUND_HOLDINGS_CACHE_DAYS,
                        cache_enabled=True,
                        force_refresh=False,
                    )
                    holdings_text = f"{_format_holdings_result(holdings_df)}，沿用旧缓存"
                    error_parts.append(f"持仓强刷失败，沿用旧缓存: {error}")
                except Exception as fallback_exc:
                    ok = False
                    holdings_text = "刷新失败"
                    fallback_error = _short_error(fallback_exc)
                    error_parts.append(f"持仓: {fallback_error}")
                    failures.append((fund_code, "持仓", fallback_error))

            status = "成功" if ok else "部分失败"
            row = {
                "fund_code": fund_code,
                "purchase_limit": value,
                "purchase_source": purchase_source,
                "holdings": holdings_text,
                "status": status,
            }
            if purchase_error and not error_parts:
                row["error"] = f"限购: {purchase_error}"
            elif error_parts:
                row["error"] = "；".join(error_parts)
            rows.append(row)
            progress.advance(
                success=ok,
                status=f"{fund_code} {status}: 限购 {value} ({purchase_source or 'unknown'}); 持仓 {holdings_text}",
            )

    print_records_table(
        rows,
        title="基金缓存强制刷新汇总",
        columns=[
            ("fund_code", "基金代码"),
            ("purchase_limit", "限购缓存"),
            ("purchase_source", "限购来源"),
            ("holdings", "持仓缓存"),
            ("status", "状态"),
            ("error", "错误"),
        ],
    )

    print_purchase_limit_cache_refresh_summary(cache_days=FUND_PURCHASE_LIMIT_CACHE_DAYS)
    if failures:
        print("以下缓存刷新失败：", flush=True)
        for fund_code, category, error in failures:
            print(f"  - {fund_code} {category}: {error}", flush=True)
        raise SystemExit(1)
    print("基金限购缓存和持仓缓存手动刷新完成", flush=True)


if __name__ == "__main__":
    main()
