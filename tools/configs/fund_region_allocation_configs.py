"""晨星海外基金股票地区分布的数据源与解析配置。

本文件只维护“去哪里取、怎么解释晨星地区层级”的常量，不发请求、不写缓存、
不绘图。晨星的“股票地区分布”是股票组合内部的地区权重，不能和基金的现金、
债券等资产直接相加。
"""

from __future__ import annotations


MORNINGSTAR_FUND_URL_TEMPLATE = "https://www.morningstar.cn/fund/{fund_code}.html"

# 晨星页面偶尔较慢；连接和读取分开设置，避免主流程长时间卡住。
MORNINGSTAR_CONNECT_TIMEOUT_SECONDS = 8
MORNINGSTAR_READ_TIMEOUT_SECONDS = 25
MORNINGSTAR_RETRY_ATTEMPTS = 2
MORNINGSTAR_REQUEST_INTERVAL_SECONDS = 0.35

# 晨星页面的原始父级区域。三项与“未分类”可相加到约 100%。
MORNINGSTAR_PARENT_REGION_LABELS = (
    "大亚洲地区",
    "美洲",
    "大欧洲地区",
    "未分类",
)

# 晨星把“大洋洲”列在“大亚洲地区”下，把“非洲/中东”列在“大欧洲地区”下。
# 展示时会从父级中扣除这两项，得到互不重叠的五个区域。
MORNINGSTAR_SUBREGION_LABELS = (
    "日本",
    "大洋洲",
    "发达亚洲",
    "新兴亚洲",
    "北美",
    "拉丁美洲",
    "英国",
    "发达欧洲",
    "新兴欧洲",
    "非洲/中东",
)

# 下列 key 仍对应既有展示计算结果，只调整公开图片的呈现顺序。
DISPLAY_REGION_ORDER = ("美洲", "亚洲", "欧洲", "非洲中东", "大洋洲", "未分类")
DISPLAY_REGION_LABELS = {
    "美洲": "美洲",
    "亚洲": "亚洲",
    "欧洲": "欧洲",
    "非洲中东": "非洲/中东",
    "大洋洲": "大洋洲",
    "未分类": "未分类",
}
DISPLAY_REGION_COLORS = {
    "美洲": "#2F95D8",
    "亚洲": "#EF5675",
    "欧洲": "#25AE88",
    "非洲中东": "#F4A62A",
    "大洋洲": "#8367C7",
    "未分类": "#B7C0CC",
}


__all__ = [
    "MORNINGSTAR_FUND_URL_TEMPLATE",
    "MORNINGSTAR_CONNECT_TIMEOUT_SECONDS",
    "MORNINGSTAR_READ_TIMEOUT_SECONDS",
    "MORNINGSTAR_RETRY_ATTEMPTS",
    "MORNINGSTAR_REQUEST_INTERVAL_SECONDS",
    "MORNINGSTAR_PARENT_REGION_LABELS",
    "MORNINGSTAR_SUBREGION_LABELS",
    "DISPLAY_REGION_ORDER",
    "DISPLAY_REGION_LABELS",
    "DISPLAY_REGION_COLORS",
]
