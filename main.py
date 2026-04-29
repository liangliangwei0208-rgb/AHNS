from datetime import datetime
from pathlib import Path
import pandas as pd

from stock_analysis import build_stock_analysis
from tools.quote_manager import get_daily_quote_text
from tools.email_send import send_email
from tools.get_top10_holdings import estimate_funds_and_save_table

Path("output").mkdir(parents=True, exist_ok=True)
Path("cache").mkdir(parents=True, exist_ok=True)

def log(msg: str):
    """打印带时间戳的日志，方便查看运行进度"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

log("程序开始运行")

# 获得分析结果
log("开始生成 RSI 和市场分析图片")
stock_text, image_paths, results = build_stock_analysis(
    return_raw=True,
    include_factors=True,
    include_realtime=True,
)
log("RSI 和市场分析图片生成完成")
log(f"当前已有图片数量: {len(image_paths)}")
log(f"当前图片列表: {image_paths}")

# 获得每日语录
log("开始获取每日语录")
quote_text = get_daily_quote_text(
    quote_file="investment_quotes.txt",
    history_file="investment_quote_history.json",
)
log("每日语录获取完成")

now = datetime.now()

if now.hour < 14:
    time_note = "注：本邮件包含午盘盘中临时行情，RSI 与量化因子并非收盘确认值。"
else:
    time_note = "注：本邮件为收盘后或接近收盘后的行情摘要。"

email_text = quote_text + "\n\n" + time_note + "\n\n" + stock_text

log("邮件正文组装完成")
print(email_text)

# ============================================================
# 海外基金持仓估算表格
# ============================================================

log("开始生成海外基金持仓估算表格")
estimate_funds_and_save_table(
    fund_codes=[
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
    ],
    top_n=10,
    output_file="output/haiwai_fund_estimate_table.png",
    title="海外市场收益预估 " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

    # 自动选择：在 proxy_map 中的走 ETF / 指数代理，不在的走前十大持仓
    holding_mode="auto",
    # ETF / FOF 代理资产按原始披露仓位计算，现金仓位按 0
    proxy_normalize_weights=False,
    # 美股默认使用最新交易日日线，不拉实时全市场
    us_realtime=False,
    # 港股优先使用实时行情；失败后回落到港股日线
    hk_realtime=True,
    # 如果部分持仓行情失败，只用可查持仓重新归一化估算
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
    # 基金持仓默认 75 天，限购默认 7 天；
    # CN/HK 行情小时级缓存，US 行情日级缓存
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
    fund_codes=[
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
    ],
    top_n=10,
    output_file="output/guonei_fund_estimate_table.png",
    title="国内市场收益预估 " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    # 自动选择股票持仓或代理估算
    holding_mode="auto",
    # 代理按原始权重计算，现金按 0
    proxy_normalize_weights=False,
    # 美股默认使用最新交易日日线
    us_realtime=False,
    # 港股优先实时；失败后回落到日线
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
    # 缓存继续开启
    cache_enabled=True,
    security_return_cache_enabled=True,
    cn_hk_hourly_cache=True,
)
log("国内基金持仓估算表格生成完成")

image_paths.append("output/guonei_fund_estimate_table.png")
log("已将国内基金估算表加入邮件图片列表")

log(f"最终图片数量: {len(image_paths)}")
log(f"最终图片列表: {image_paths}")

# ============================================================
# 发送邮件
# ============================================================

log("准备发送邮件")

send_email(
    subject=f"发光发热—每日提醒——分析结果—{now.strftime('%Y-%m-%d %H:%M')}",
    text=email_text,
    image_paths=image_paths,
    to_email="2569236501@qq.com",
)

log("程序运行完成")
