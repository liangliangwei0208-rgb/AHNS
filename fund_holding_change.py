"""
生成基金前十大持仓变化解读图。

手动默认分析 012922 最新一期和上一期真实披露持仓；总入口使用 --auto
自动检测基金库持仓缓存变化。
"""

from __future__ import annotations

from tools.fund_holding_change import main


if __name__ == "__main__":
    raise SystemExit(main())
