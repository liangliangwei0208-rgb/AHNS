"""
AHNS 日常主入口。单独可运行，也可被 git_main.py 调用。
生成市场 RSI/趋势图、海外与国内基金模型估算图，并把基金级估算结果写入缓存。
邮件发送由 git_main.py 统一编排，本文件默认只负责生成正文、图片和缓存。
"""
import json
from datetime import datetime
from pathlib import Path

from stock_analysis import build_stock_analysis
from tools.quote_manager import get_daily_quote_text
from tools.get_top10_holdings import (
    estimate_funds_and_save_table,
    DEFAULT_FUND_PROXY_MAP,
    get_us_index_benchmark_items,
    determine_latest_valuation_anchor_date,
)
from tools.fund_universe import HAIWAI_FUND_CODES


def log(msg: str):
    """打印带时间戳的日志，方便查看运行进度"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def normalize_fund_code(code) -> str:
    """统一基金代码格式。"""
    return str(code).strip().zfill(6)

def get_stock_holding_fund_codes(fund_codes):
    """
    只保留真正需要读取前十大股票持仓缓存的基金。
    说明：
    - DEFAULT_FUND_PROXY_MAP 中的基金走 ETF / 指数代理；
    - 代理基金通常不进入 fund_holdings_cache.json；
    - 因此统计“持仓更新进度”时应排除代理基金，避免误判为“无缓存”。
    """
    proxy_codes = {normalize_fund_code(x) for x in DEFAULT_FUND_PROXY_MAP.keys()}
    return [
        normalize_fund_code(code)
        for code in fund_codes
        if normalize_fund_code(code) not in proxy_codes
    ]

def parse_iso_datetime(value):
    """
    尽量解析 ISO 时间字符串。
    解析失败返回 None。
    """
    if not value:
        return None

    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:
        return None

def build_fund_holding_cache_status_text(
    tracked_fund_codes,
    top_n=10,
    cache_file="cache/fund_holdings_cache.json",
):
    """
    读取基金持仓缓存状态，生成邮件正文中的持仓更新进度摘要。
    统计口径：
    - 只统计真正走“前十大股票持仓估算”的基金；
    - ETF 联接 / FOF / 指数代理基金不统计在内；
    - target_quarter_confirmed=True 视为已确认本轮目标季度；
    - 如果 latest_quarter_key >= target_quarter_key，也视为已确认；
    - 没有缓存或字段不足的基金，归入“待补齐”。
    """
    tracked_fund_codes = [normalize_fund_code(x) for x in tracked_fund_codes]
    cache_path = Path(cache_file)

    if not tracked_fund_codes:
        return (
            "【基金持仓缓存状态】\n"
            "当前基金池中没有需要按前十大股票持仓估算的基金。"
        )

    if not cache_path.exists():
        return (
            "【基金持仓缓存状态】\n"
            f"纳入前十大持仓跟踪的基金：{len(tracked_fund_codes)} 只\n"
            "尚未发现基金持仓缓存文件。本次运行会在成功获取持仓后逐步建立缓存。"
        )

    try:
        with cache_path.open("r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception as e:
        return (
            "【基金持仓缓存状态】\n"
            f"基金持仓缓存读取失败：{e}"
        )

    total = 0
    confirmed = 0
    pending = 0
    missing = 0
    unknown = 0

    confirmed_codes = []
    pending_codes = []
    missing_codes = []
    unknown_codes = []

    latest_quarter_labels = set()
    target_quarter_keys = set()
    latest_quarter_keys = set()

    pending_next_check_times = []
    all_next_check_times = []

    for fund_code in tracked_fund_codes:
        total += 1
        cache_key = f"{fund_code}:top{int(top_n)}"
        item = cache.get(cache_key)

        if not isinstance(item, dict):
            missing += 1
            missing_codes.append(fund_code)
            continue

        latest_quarter_label = item.get("latest_quarter_label")
        latest_quarter_key = item.get("latest_quarter_key")
        target_quarter_key = item.get("target_quarter_key")
        target_confirmed = bool(item.get("target_quarter_confirmed", False))
        next_check_after = item.get("next_check_after")

        if latest_quarter_label:
            latest_quarter_labels.add(str(latest_quarter_label))

        if latest_quarter_key is not None:
            latest_quarter_keys.add(str(latest_quarter_key))

        if target_quarter_key is not None:
            target_quarter_keys.add(str(target_quarter_key))

        if next_check_after:
            all_next_check_times.append(str(next_check_after))

        is_confirmed = False

        if target_confirmed:
            is_confirmed = True
        elif latest_quarter_key is not None and target_quarter_key is not None:
            try:
                is_confirmed = int(latest_quarter_key) >= int(target_quarter_key)
            except Exception:
                is_confirmed = False

        if is_confirmed:
            confirmed += 1
            confirmed_codes.append(fund_code)
            continue

        if latest_quarter_key is not None and target_quarter_key is not None:
            pending += 1
            pending_codes.append(fund_code)

            if next_check_after:
                pending_next_check_times.append(str(next_check_after))
            continue

        unknown += 1
        unknown_codes.append(fund_code)

        if next_check_after:
            pending_next_check_times.append(str(next_check_after))

    remaining = pending + missing + unknown

    # 优先展示待更新基金的下一次检查时间；
    # 如果没有待更新基金，则展示所有已确认基金中的下一次检查时间，通常是下一季度窗口。
    next_check_candidates = pending_next_check_times or all_next_check_times
    next_check_text = "暂无"

    if next_check_candidates:
        parsed = []
        raw_fallback = []

        for item in next_check_candidates:
            dt = parse_iso_datetime(item)
            if dt is not None:
                parsed.append(dt)
            else:
                raw_fallback.append(str(item))

        if parsed:
            next_check_text = min(parsed).strftime("%Y-%m-%d %H:%M")
        elif raw_fallback:
            next_check_text = sorted(raw_fallback)[0]

    latest_quarter_text = (
        "、".join(sorted(latest_quarter_labels))
        if latest_quarter_labels
        else "暂无可读季度标签"
    )

    target_quarter_text = (
        "、".join(sorted(target_quarter_keys))
        if target_quarter_keys
        else "待补齐"
    )

    lines = [
        "【基金持仓缓存状态】",
        f"纳入前十大持仓跟踪的基金：{total} 只",
        f"已确认本轮目标季度持仓：{confirmed} 只",
        f"尚未确认 / 待补齐：{remaining} 只",
        f"其中：待刷新 {pending} 只，无缓存 {missing} 只，字段待补齐 {unknown} 只",
        f"缓存中已见持仓季度：{latest_quarter_text}",
        f"当前目标季度键：{target_quarter_text}",
        f"下一次持仓检查时间：{next_check_text}",
    ]

    # 邮件正文只列前 8 个，避免太长。
    if pending_codes:
        lines.append(
            "待刷新基金："
            + "、".join(pending_codes[:8])
            + (" 等" if len(pending_codes) > 8 else "")
        )

    if missing_codes:
        lines.append(
            "无缓存基金："
            + "、".join(missing_codes[:8])
            + (" 等" if len(missing_codes) > 8 else "")
        )

    if unknown_codes:
        lines.append(
            "字段待补齐基金："
            + "、".join(unknown_codes[:8])
            + (" 等" if len(unknown_codes) > 8 else "")
        )

    return "\n".join(lines)


def resolve_haiwai_valuation_date(benchmark_items):
    """
    从海外指数基准中提取本次海外基金估值交易日。

    优先使用纳斯达克100的 trade_date；如果没有，则使用任一可用 trade_date；
    最后才退回北京时间运行日。这个日期用于判断 A股/港股在本次
    QDII / 全球基金估值交易日是否有新行情。
    """
    if not benchmark_items:
        return datetime.now().strftime("%Y-%m-%d")

    for item in benchmark_items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", ""))
        symbol = str(item.get("symbol", "")).upper()
        trade_date = str(item.get("trade_date", "")).strip()
        if trade_date and ("纳斯达克100" in label or symbol in {".NDX", "^NDX", "NDX"}):
            return trade_date[:10]

    for item in benchmark_items:
        if not isinstance(item, dict):
            continue
        trade_date = str(item.get("trade_date", "")).strip()
        if trade_date:
            return trade_date[:10]

    return datetime.now().strftime("%Y-%m-%d")


def main() -> None:
    """生成每日市场图、基金估算图和基金级估算缓存。"""
    Path("output").mkdir(parents=True, exist_ok=True)
    Path("cache").mkdir(parents=True, exist_ok=True)


    log("程序开始运行")
    now = datetime.now()

    all_fund_codes = HAIWAI_FUND_CODES
    stock_holding_fund_codes = get_stock_holding_fund_codes(all_fund_codes)

    log(f"海外/全球基金总数: {len(all_fund_codes)}")
    log(f"前十大持仓估算基金数量: {len(stock_holding_fund_codes)}")
    log(f"代理估算基金数量: {len(all_fund_codes) - len(stock_holding_fund_codes)}")

    log("开始生成 RSI 和市场分析图片")
    stock_text, image_paths, results = build_stock_analysis(
        return_raw=True,
        include_factors=True,
        include_realtime=True,
    )
    log("RSI 和市场分析图片生成完成")
    log(f"当前已有图片数量: {len(image_paths)}")
    log(f"当前图片列表: {image_paths}")

    log("开始获取每日语录")
    quote_text = get_daily_quote_text(
        quote_file="investment_quotes.txt",
        history_file="investment_quote_history.json",
    )
    log("每日语录获取完成")

    if now.hour < 14:
        time_note = (
            "注：本邮件包含午盘盘中临时行情，RSI 与量化因子并非收盘确认值；"
            "海外基金表采用自动估值口径：含美股持仓的全球/QDII基金按海外估值交易日统一收盘估算；"
        )
    else:
        time_note = (
            "注：本邮件为收盘后或接近收盘后的行情摘要；"
            "海外基金表采用自动估值口径：含美股持仓的全球/QDII基金按海外估值交易日统一收盘估算；"
        )

    # 海外基金用实际估值交易日判断 A股/港股行情是否应置零。
    log("开始确定海外/全球基金估值锚点")
    valuation_anchor_date = determine_latest_valuation_anchor_date()
    log(f"海外/全球基金估值锚点: {valuation_anchor_date}")

    log("开始获取海外市场指数基准")
    haiwai_benchmark_footer_items = get_us_index_benchmark_items(
        cache_enabled=True,
        valuation_anchor_date=valuation_anchor_date,
    )
    haiwai_valuation_date = valuation_anchor_date or resolve_haiwai_valuation_date(haiwai_benchmark_footer_items)
    log(f"海外市场指数基准: {haiwai_benchmark_footer_items}")
    log(f"海外基金估值交易日: {haiwai_valuation_date}")

    haiwai_generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    haiwai_title = f"海外市场收益率预估 估值日：{haiwai_valuation_date} 生成：{haiwai_generated_at}"
    haiwai_title_segments = [
        {"text": "海外市场收益率预估  ", "color": "black", "fontweight": "bold"},
        {"text": f"估值日：{haiwai_valuation_date}", "color": "red", "fontweight": "bold"},
        {"text": f"  生成：{haiwai_generated_at}", "color": "black", "fontweight": "bold"},
    ]

    log("开始生成海外基金持仓估算表格")
    estimate_funds_and_save_table(
        fund_codes=HAIWAI_FUND_CODES,
        top_n=10,
        output_file="output/haiwai_fund.png",
        title=haiwai_title,
        title_segments=haiwai_title_segments,

        # proxy_map 命中的基金走代理资产，其余基金走前十大持仓估算。
        holding_mode="auto",
        proxy_normalize_weights=False,
        us_realtime=False,
        # 含美股持仓的全球 / QDII 基金统一使用海外收盘口径。
        hk_realtime=True,
        valuation_mode="last_close",

        # 关键：QDII / 全球型基金按海外估值交易日判断 A股/港股是否闭市。
        # 例如北京时间4月2日晚运行，若纳斯达克100最新交易日是4月1日，
        # 则 A股/港股只有在没有4月1日行情时才置零。
        zero_stale_cn_hk_returns=True,
        stale_market_estimate_date=haiwai_valuation_date,

        benchmark_footer_items=haiwai_benchmark_footer_items,
        benchmark_footer_fontsize=15,
        footnote_text=(
            "基金收益为披露持仓近似估算；"
            "A股/港股若无对应海外估值交易日的新行情，则保留仓位但收益按0%计入；"
        ),

        # 持仓行情缺失时，只在可查持仓内部重新归一化。
        renormalize_available_holdings=True,
        include_purchase_limit=True,
        include_method_col=False,
        sort_by_return=True,

        watermark_text="鱼师AHNS",
        up_color="red",
        down_color="green",
        print_table=True,
        save_table=True,

        # 缓存策略：
        # 基金持仓更新由 get_top10_holdings.py 内部按季度披露窗口管理；
        # CN/HK 行情小时级缓存，US 行情日级缓存。
        cache_enabled=True,
        security_return_cache_enabled=True,
        cn_hk_hourly_cache=True,
        valuation_anchor_date=haiwai_valuation_date,
    )

    log("海外基金持仓估算表格生成完成")
    image_paths.append("output/haiwai_fund.png")
    log("已将海外基金估算表加入邮件图片列表")

    holding_status_text = build_fund_holding_cache_status_text(
        tracked_fund_codes=stock_holding_fund_codes,
        top_n=10,
        cache_file="cache/fund_holdings_cache.json",
    )
    email_text = (
        quote_text
        + "\n\n"
        + time_note
        + "\n\n"
        + holding_status_text
        + "\n\n"
        + stock_text
    )
    log("邮件正文组装完成")
    print(email_text)

    log(f"最终图片数量: {len(image_paths)}")
    log(f"最终图片列表: {image_paths}")
    log("程序运行完成")


if __name__ == "__main__":
    main()
