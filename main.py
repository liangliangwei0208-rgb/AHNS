from datetime import datetime
import pandas as pd

from stock_analysis import build_stock_analysis
from tools.quote_manager import get_daily_quote_text
from tools.email_send import send_email
from tools.get_top10_holdings import estimate_funds_and_save_table


def log(msg: str):
    """打印带时间戳的日志，适合 GitHub Actions 实时查看"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# 已不再需要交易日判断功能，因此注释掉相关函数
# def latest_trade_date(df):
#     if df is None or df.empty or "date" not in df.columns:
#         return None
#     dates = pd.to_datetime(df["date"], errors="coerce").dropna()
#     if dates.empty:
#         return None
#     return dates.max().date()

# def is_trade_day(results):
#     today = datetime.now().date()
#     check_keywords = ["红利低波", "上证指数ETF", "510210"]
#     trade_dates = []
#     for item in results:
#         if any(k in item.name for k in check_keywords):
#             d = latest_trade_date(item.hist)
#             trade_dates.append((item.name, d))
#     log(f"交易日检查详情：{trade_dates}")
#     return any(d == today for _, d in trade_dates)


def main():
    log("程序开始运行")

    try:
        # =========================================================
        # 1. 生成 RSI 分析结果
        # =========================================================
        log("开始生成 RSI 和市场分析图片")
        stock_text, image_paths, results = build_stock_analysis(
            return_raw=True,
            include_factors=True,
            include_realtime=True,
        )
        log("RSI 和市场分析图片生成完成")
        log(f"当前已有图片数量: {len(image_paths)}")
        log(f"当前图片列表: {image_paths}")

        # =========================================================
        # 2. 跳过交易日判断，直接发送邮件
        # =========================================================
        log("已关闭交易日检查，本次运行强制发送邮件")

        # =========================================================
        # 3. 获取每日语录
        # =========================================================
        log("开始获取每日语录")
        quote_text = get_daily_quote_text(
            quote_file="investment_quotes.txt",
            history_file="investment_quote_history.json",
        )
        log("每日语录获取完成")

        # =========================================================
        # 4. 生成邮件正文
        # =========================================================
        log("开始组装邮件正文")
        now = datetime.now()

        if now.hour < 14:
            time_note = "注：本邮件包含午盘盘中临时行情，RSI 与量化因子并非收盘确认值。"
        else:
            time_note = "注：本邮件为收盘后或接近收盘后的行情摘要。"

        email_text = quote_text + "\n\n" + time_note + "\n\n" + stock_text
        log("邮件正文组装完成")
        log("邮件正文预览如下：")
        print(email_text, flush=True)

        # =========================================================
        # 5. 生成海外基金持仓估算表格
        # =========================================================
        log("开始生成海外基金持仓估算表格")
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
            us_realtime=False,
            hk_realtime=False,
            renormalize_available_holdings=True,
            include_purchase_limit=True,
            include_method_col=False,
            sort_by_return=True,
            watermark_text="鱼师-发光发热",
            up_color="red",
            down_color="green",
            print_table=True,
            save_table=True,
        )
        log("海外基金持仓估算表格生成完成")

        image_paths.append("output/haiwai_fund_estimate_table.png")
        log("已将海外基金估算表加入邮件图片列表")
        log(f"当前图片数量: {len(image_paths)}")
        log(f"当前图片列表: {image_paths}")

        # =========================================================
        # 6. 生成国内基金持仓估算表格
        # =========================================================
        log("开始生成国内基金持仓估算表格")
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
            top_n=10,
            output_file="output/guonei_fund_estimate_table.png",
            title=None,
            holding_mode="auto",
            proxy_normalize_weights=False,
            us_realtime=False,
            hk_realtime=False,
            renormalize_available_holdings=True,
            include_purchase_limit=True,
            include_method_col=False,
            sort_by_return=True,
            watermark_text="鱼师-发光发热",
            up_color="red",
            down_color="green",
            print_table=True,
            save_table=True,
        )
        log("国内基金持仓估算表格生成完成")

        image_paths.append("output/guonei_fund_estimate_table.png")
        log("已将国内基金估算表加入邮件图片列表")
        log(f"最终图片数量: {len(image_paths)}")
        log(f"最终图片列表: {image_paths}")

        # =========================================================
        # 7. 发送邮件
        # =========================================================
        log("准备发送邮件")

        send_email(
            subject=f"发光发热—每日提醒——分析结果—{now.strftime('%Y-%m-%d %H:%M')}",
            text=email_text,
            image_paths=image_paths,
            to_email="2569236501@qq.com",
        )

        log("邮件发送完成")
        log("程序运行完成")

    except SystemExit:
        log("程序正常退出（例如非交易日不发送）")
        raise
    except Exception as e:
        log(f"[ERROR] 程序运行失败: {repr(e)}")
        raise


if __name__ == "__main__":
    main()
