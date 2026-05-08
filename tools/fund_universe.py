"""
基金池配置的历史兼容入口。

真实配置已移动到 `tools.configs.fund_universe_configs`。这里保留旧导入路径，
是为了让现有代码继续可以写：

    from tools.fund_universe import HAIWAI_FUND_CODES

以后维护基金代码清单时，请去 `tools/configs/fund_universe_configs.py` 修改。
"""

from tools.configs.fund_universe_configs import HAIWAI_FUND_CODES, GUONEI_FUND_CODES


__all__ = ["HAIWAI_FUND_CODES", "GUONEI_FUND_CODES"]
