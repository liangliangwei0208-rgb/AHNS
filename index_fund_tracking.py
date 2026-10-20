"""指数基金跟踪薄入口；不计算持仓预估，不自行发送邮件。"""
from tools.index_fund_tracking import main

if __name__ == "__main__":
    raise SystemExit(main())
