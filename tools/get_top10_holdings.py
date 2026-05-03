"""
基金估算兼容入口。

旧项目长期从 ``tools.get_top10_holdings`` 导入基金估算、表格绘图、代理
ETF 配置等对象。为了降低本次拆分风险，真实实现已搬到
``tools.fund_estimator``，本文件只负责重新导出旧接口。

维护建议：
- 新增或调整估算逻辑，优先放到 ``tools.fund_estimator`` 及相关分组模块。
- 外部脚本仍可继续使用旧导入路径，避免影响 `main.py`、`safe_fund.py`。
"""

from tools import fund_estimator as _impl


for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)


__all__ = [_name for _name in globals() if not _name.startswith("_")]
