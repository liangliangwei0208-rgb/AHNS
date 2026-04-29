import json
from datetime import datetime
from pathlib import Path

from stock_analysis import build_stock_analysis
from tools.quote_manager import get_daily_quote_text
from tools.email_send import send_email
from tools.get_top10_holdings import (
    estimate_funds_and_save_table,
    DEFAULT_FUND_PROXY_MAP,
)

Path("output").mkdir(parents=True, exist_ok=True)
Path("cache").mkdir(parents=True, exist_ok=True)

# ============================================================
# 基金池配置
# ============================================================
HAIWAI_FUND_CODES = [
    "017437",  # 华宝纳斯达克精选股票
    "012922",  # 易方达全球成长精选混合
    "016702",  # 银华海外数字经济量化选股混合
    "015016",  # 华安德国DAX指数
    "007722",  # 天弘标普500指数
    "024239",  # 华夏全球科技先锋混合
    "021842",  # 国富全球科技互联混合
    "021277",  # 广发全球精选股票
    "018036",  # 长城全球新能源汽车股票
    "022184",  # 富国全球科技互联网股票
    "020713",  # 华安三菱日联日经225ETF
    "016665",  # 天弘全球高端制造
    "539002",  # 建信新兴市场优选混合
    "000043",  # 嘉实美国成长股票
    "013328",  # 嘉实全球价值机会股票
    "002891",  # 华夏移动互联灵活配置混合
    "008254",  # 华宝致远混合
    "006555",  # 浦银安盛全球智能科技股票
    "017654",  # 创金合信全球芯片产业股票
    "015202",  # 汇添富全球移动互联网灵活配置
]
GUONEI_FUND_CODES = [
    "007467",  # 华泰柏瑞中证红利低波
    "015311",  # 华泰柏瑞南方东英恒生科技指数
    "005125",  # 华宝标普中国A股红利指数
    "019127",  # 华泰柏瑞港股通医疗精选混合
    "023918",  # 华夏国证自由现金流
    "008987",  # 广发上海金ETF
    "014143",  # 银河创新成长混合
    "025196",  # 广发创业板指数增强
    "010238",  # 安信创新先锋混合
    "013881",  # 长信中证500指数增强
    "016020",  # 招商中证电池主题ETF
    "025924",  # 平安新能源精选混合
    "012414",  # 招商中证白酒
    "110022",  # 易方达消费行业股票
    "012725",  # 国泰中证畜牧养殖
    "015850",  # 宝盈国证证券龙头指数
    "023145",  # 汇添富中证油气资源
    "011840",  # 天弘中证人工智能主题
    "011103",  # 天弘中证光伏产业
    "020691",  # 博时中证全指通信设备指数
]

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
# ============================================================
# 主程序
# ============================================================
log("程序开始运行")
now = datetime.now()

all_fund_codes = HAIWAI_FUND_CODES + GUONEI_FUND_CODES
stock_holding_fund_codes = get_stock_holding_fund_codes(all_fund_codes)

log(f"基金总数: {len(all_fund_codes)}")
log(f"前十大持仓估算基金数量: {len(stock_holding_fund_codes)}")
log(f"代理估算基金数量: {len(all_fund_codes) - len(stock_holding_fund_codes)}")

# ============================================================
# 获得 RSI 和市场分析结果
# ============================================================

log("开始生成 RSI 和市场分析图片")
stock_text, image_paths, results = build_stock_analysis(
    return_raw=True,
    include_factors=True,
    include_realtime=True,
)
log("RSI 和市场分析图片生成完成")
log(f"当前已有图片数量: {len(image_paths)}")
log(f"当前图片列表: {image_paths}")

# ============================================================
# 获得每日语录
# ============================================================

log("开始获取每日语录")
quote_text = get_daily_quote_text(
    quote_file="investment_quotes.txt",
    history_file="investment_quote_history.json",
)
log("每日语录获取完成")

if now.hour < 14:
    time_note = "注：本邮件包含午盘盘中临时行情，RSI 与量化因子并非收盘确认值。"
else:
    time_note = "注：本邮件为收盘后或接近收盘后的行情摘要。"

# ============================================================
# 海外基金持仓估算表格
# ============================================================
log("开始生成海外基金持仓估算表格")
estimate_funds_and_save_table(
    fund_codes=HAIWAI_FUND_CODES,
    top_n=10,
    output_file="output/haiwai_fund_estimate_table.png",
    title="海外市场收益预估 " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

    # 自动选择：在 proxy_map 中的基金走 ETF / 指数代理，不在的走前十大持仓
    holding_mode="auto",
    # ETF / FOF 代理资产按原始披露仓位计算，现金仓位按 0
    proxy_normalize_weights=False,
    # 美股默认使用最新交易日日线，不拉实时全市场
    us_realtime=False,
    # 港股优先使用新浪安全实时；失败后东方财富兜底；再失败回落日线
    hk_realtime=True,
    # 某些持仓行情缺失时，用可查持仓重新归一化估算
    renormalize_available_holdings=True,
    # 显示限购金额
    include_purchase_limit=True,
    # 不显示内部估算方式列
    include_method_col=False,
    # 按今日预估涨跌幅从高到低排序
    sort_by_return=True,

    watermark_text="鱼师-发光发热",
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
)

log("海外基金持仓估算表格生成完成")
image_paths.append("output/haiwai_fund_estimate_table.png")
log("已将海外基金估算表加入邮件图片列表")
# ============================================================
# 国内基金持仓估算表格
# ============================================================
log("开始生成国内基金持仓估算表格")
estimate_funds_and_save_table(
    fund_codes=GUONEI_FUND_CODES,
    top_n=10,
    output_file="output/guonei_fund_estimate_table.png",
    title="国内市场收益预估 " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    # 自动选择股票持仓或代理估算
    holding_mode="auto",
    # 代理按原始权重计算，现金按 0
    proxy_normalize_weights=False,
    # 美股默认使用最新交易日日线
    us_realtime=False,
    # 港股优先使用新浪安全实时；失败后东方财富兜底；再失败回落日线
    hk_realtime=True,
    # 某些持仓行情缺失时，用可查持仓重新归一化估算
    renormalize_available_holdings=True,

    include_purchase_limit=True,
    include_method_col=False,
    sort_by_return=True,
    watermark_text="鱼师-发光发热",
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
)

log("国内基金持仓估算表格生成完成")
image_paths.append("output/guonei_fund_estimate_table.png")
log("已将国内基金估算表加入邮件图片列表")
# ============================================================
# 邮件正文：基金持仓缓存状态 + 市场分析
# ============================================================
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
# ============================================================
# 发送邮件
# ============================================================
log("准备发送邮件")
# send_email(
#     subject=f"发光发热—每日提醒——分析结果—{now.strftime('%Y-%m-%d %H:%M')}",
#     text=email_text,
#     image_paths=image_paths,
#     to_email="2569236501@qq.com",
# )
log("程序运行完成")
