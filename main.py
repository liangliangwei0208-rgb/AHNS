from datetime import datetime
import pandas as pd

from stock_analysis import build_stock_analysis
from tools.quote_manager import get_daily_quote_text
from tools.email_send import send_email
from tools.get_top10_holdings import estimate_funds_and_save_table

def latest_trade_date(df):
    if df is None or df.empty or "date" not in df.columns:
        return None

    dates = pd.to_datetime(df["date"], errors="coerce").dropna()

    if dates.empty:
        return None

    return dates.max().date()


def is_trade_day(results):
    """
    用 A 股 ETF 判断今天是否为交易日。
    只要红利低波或上证 ETF 任意一个最新交易日为今天，就认为今天有效。
    """
    today = datetime.now().date()

    check_keywords = [
        "红利低波",
        "上证指数ETF",
        "510210",
    ]

    trade_dates = []

    for item in results:
        if any(k in item.name for k in check_keywords):
            d = latest_trade_date(item.hist)
            trade_dates.append((item.name, d))

    print("交易日检查：", trade_dates)

    return any(d == today for _, d in trade_dates)


# 获得分析结果
stock_text, image_paths, results = build_stock_analysis(
    return_raw=True,
    include_factors=True,
    include_realtime=True,
)

# 如果不是交易日，不发送邮件
if not is_trade_day(results):
    print("今天不是有效交易日，或行情没有更新到今天，本次不发送邮件。")
    raise SystemExit(0)

# 获得每日语录
quote_text = get_daily_quote_text(
    quote_file="investment_quotes.txt",
    history_file="investment_quote_history.json",
)

now = datetime.now()

if now.hour < 14:
    time_note = "注：本邮件包含午盘盘中临时行情，RSI 与量化因子并非收盘确认值。"
else:
    time_note = "注：本邮件为收盘后或接近收盘后的行情摘要。"
email_text = quote_text + "\n\n" + time_note + "\n\n" + stock_text
print(email_text)
# 海外基金持仓估算表格
estimate_funds_and_save_table(
    fund_codes=[
        "017437", # 华宝纳斯达克精选股票
        "012922", # 易方达全球成长精选混合
        "016702", # 银华海外数字经济量化选股混合
        "015016", # 华安德国DAX指数
        "007722", # 天弘标普500指数
        "024239", # 华夏全球科技先锋混合
        "021842", # 国富全球科技互联混合
        "021277", # 广发全球精选股票
        "018036", # 长城全球新能源汽车股票
        "022184", # 富国全球科技互联网股票
        "020713", # 华安三菱日联日经225ETF
        "016665", # 天弘全球高端制造
        "539002", # 建信新兴市场优选混合
        "000043", # 嘉实美国成长股票
        "013328", # 嘉实全球价值机会股票
        "002891", # 华夏移动互联灵活配置混合
        "008254", # 华宝致远混合
        "006555", # 浦银安盛全球智能科技股票
        "017654", # 创金合信全球芯片产业股票
        "015202", # 汇添富全球移动互联网灵活配置
    ],
    top_n=10,
    output_file="output/haiwai_fund_estimate_table.png",
    title="海外市场收益预估 " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    holding_mode="auto",
    proxy_normalize_weights=False,
    us_realtime=False,   # 如果开启实时数据，则会拉取所有的美股数据，耗时较长
    hk_realtime=False,   # 新增：港股默认走日线，不拉全市场
    renormalize_available_holdings=True,  # 新增：只用可查持仓重新归一化
    include_purchase_limit=True,
    include_method_col=False,
    sort_by_return=True,
    watermark_text="鱼师-发光发热",
    up_color="red",
    down_color="green",
    print_table=True,
    save_table=True,
)
image_paths.append("output/haiwai_fund_estimate_table.png")
# 国内基金持仓估算表格
estimate_funds_and_save_table(
    fund_codes=[
        "007467", # 华泰柏瑞中证红利低波
        "015311", # 华泰柏瑞南方东英恒生科技指数
        "005125", # 华宝标普中国A股红利指数
        "019127", # 华泰柏瑞港股通医疗精选混合
        "023918", # 华夏国证自由现金流
        "008987", # 广发上海金ETF
        "014143", # 银河创新成长混合
        "025196", # 广发创业板指数增强
        "010238", # 安信创新先锋混合
        "013881", # 长信中证500指数增强
        "016020", # 招商中证电池主题ETF
        "025924", # 平安新能源精选混合
        "012414", # 招商中证白酒
        "110022", # 易方达消费行业股票
        "012725", # 国泰中证畜牧养殖
        "015850", # 宝盈国证证券龙头指数
        "023145", # 汇添富中证油气资源
        "011840", # 天弘中证人工智能主题
        "011103", # 天弘中证光伏产业
        "020691", # 博时中证全指通信设备指数
    ],
    top_n=10, # 股票持仓估算取前 10 大股票
    output_file="output/guonei_fund_estimate_table.png",
    title=None,
    holding_mode="auto", # 自动选择股票持仓或代理估算
    proxy_normalize_weights=False, # 代理按原始权重计算，现金按 0
    us_realtime=False,  # 如果开启实时数据，则会拉取所有的美股数据，耗时较长
    hk_realtime=False,  # 港股默认使用日线；True 会先尝试新浪单只港股实时行情
    renormalize_available_holdings=True,  # 某些持仓行情缺失时，用可查持仓重新归一化估算
    include_purchase_limit=True,
    include_method_col=False,
    sort_by_return=True,
    watermark_text="鱼师-发光发热",
    up_color="red",
    down_color="green",
    print_table=True,
    save_table=True,
)
image_paths.append("output/guonei_fund_estimate_table.png")

send_email(
    subject=f"发光发热—每日提醒——分析结果—{now.strftime('%Y-%m-%d %H:%M')}",
    text=email_text,
    image_paths=image_paths,
    to_email="2569236501@qq.com",
)



