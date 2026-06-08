"""
盘前海外基金观察配置。

这里维护两类信息：
1. 盘前附表显示哪些实时观察项，以及每个观察项实际使用哪个美股 ticker 取数；
2. 不同基金的“补仓仓位”使用哪个盘前实时观察项估算。

维护提示：
- `PREMARKET_BENCHMARK_SPECS` 的 key 是内部配置名，图片只显示 `label`。
- 实时观察图底部基准表也从这里控制：
  - `display_in_footer` 是默认开关；
  - `display_in_premarket_footer` / `display_in_afterhours_footer` /
    `display_in_intraday_footer` / `display_in_futu_night_footer` 可按入口单独覆盖；
  - `footer_labels` 可按入口指定图片里显示的名字。
- `PREMARKET_FUND_RESIDUAL_BENCHMARK_MAP` 左边写 6 位基金代码，右边写
  `PREMARKET_BENCHMARK_SPECS` 里的 key。
- 普通海外股票持仓基金默认使用纳指100方向补仓。
- 油气主题基金应显式配置为 `oil_gas_ep`，避免误用纳指100补仓。
"""

from __future__ import annotations


PREMARKET_START_HOUR_BJ = 17
PREMARKET_START_MINUTE_BJ = 30
PREMARKET_END_HOUR_BJ = 21
PREMARKET_END_MINUTE_BJ = 0


PREMARKET_BENCHMARK_SPECS = {
    "nasdaq100": {
        "order": 1,
        "label": "纳指100",
        "market": "US",
        "ticker": "QQQ",
        "kind": "us_security",
        "display_in_footer": True,
        "usable_as_residual": True,
        "footer_labels": {
            "premarket": "纳指100（盘前）",
            "afterhours": "纳指100（盘后）",
            "intraday": "纳指100（盘中）",
            "futu_night": "纳指100（夜盘）",
        },
        "description": "用 QQQ 作为纳斯达克100方向盘前实时观察项。",
    },
    "sp500": {
        "order": 2,
        "label": "标普500",
        "market": "US",
        "ticker": "SPY",
        "kind": "us_security",
        "display_in_footer": True,
        "usable_as_residual": True,
        "footer_labels": {
            "premarket": "标普500（盘前）",
            "afterhours": "标普500（盘后）",
            "intraday": "标普500（盘中）",
            "futu_night": "标普500（夜盘）",
        },
        "description": "用 SPY 作为标普500方向盘前实时观察项。",
    },
    "biotech": {
        "order": 3,
        "label": "生物科技",
        "market": "US",
        "ticker": "IBB",
        "kind": "us_security",
        "display_in_footer": False,
        "display_in_premarket_footer": False,
        "display_in_afterhours_footer": False,
        "display_in_intraday_footer": False,
        "display_in_futu_night_footer": False,
        "usable_as_residual": True,
        "footer_labels": {
            "premarket": "生物科技",
            "afterhours": "生物科技",
            "intraday": "生物科技",
            "futu_night": "生物科技",
        },
        "description": "用 IBB 作为生物科技方向盘前实时观察项。",
    },
    "oil_gas_ep": {
        "order": 4,
        "label": "油气开采",
        "market": "US",
        "ticker": "XOP",
        "kind": "us_security",
        "display_in_footer": True,
        "usable_as_residual": True,
        "footer_labels": {
            "premarket": "油气开采（盘前）",
            "afterhours": "油气开采（盘后）",
            "intraday": "油气开采（盘中）",
            "futu_night": "油气开采（夜盘）",
        },
        "description": "用 XOP 作为美国油气勘探与生产方向盘前实时观察项。",
    },
    "semiconductor": {
        "order": 5,
        "label": "费城半导体",
        "market": "US",
        "ticker": "SMH",
        "kind": "us_security",
        "display_in_footer": True,
        "display_in_premarket_footer": False,
        "display_in_afterhours_footer": False,
        "display_in_intraday_footer": True,
        "display_in_futu_night_footer": False,
        "usable_as_residual": True,
        "footer_labels": {
            "premarket": "费城半导体（盘前）",
            "afterhours": "费城半导体（盘后）",
            "intraday": "费城半导体（盘中）",
            "futu_night": "费城半导体（夜盘）",
        },
        "description": "用 SMH 作为半导体方向盘前实时观察项。",
    },
    "gold": {
        "order": 6,
        "label": "黄金",
        "market": "US",
        "ticker": "GLD",
        "kind": "us_security",
        "display_in_footer": True,
        "usable_as_residual": True,
        "footer_labels": {
            "premarket": "现货黄金（实时）",
            "afterhours": "现货黄金（实时）",
            "intraday": "现货黄金（实时）",
            "futu_night": "现货黄金（实时）",
        },
        "description": "用 GLD 作为黄金方向盘前实时观察项。",
    },
    "vix": {
        "order": 7,
        "label": "VIX恐慌指数",
        "market": "VIX_LEVEL",
        "ticker": "VIX",
        "kind": "vix_level",
        "display_in_footer": True,
        "usable_as_residual": False,
        "footer_labels": {
            "premarket": "VIX恐慌指数（实时）",
            "afterhours": "VIX恐慌指数（实时）",
            "intraday": "VIX恐慌指数（实时）",
            "futu_night": "VIX恐慌指数（实时）",
        },
        "description": "VIX 是点位观察项，不作为补仓收益率基准。",
    },
}


PREMARKET_DEFAULT_RESIDUAL_BENCHMARK_KEY = "nasdaq100"

# 盘前/盘中/盘后/富途夜盘海外股票持仓型基金的有效持仓增强系数。
# 仅作用于“有效持仓增强 + 补偿基准”口径：
# 有效披露持仓占比先乘以该系数，封顶到 100%，剩余权重再走补偿基准。
# `top10_available_normalized` 基金不使用该系数，也不走补偿基准。
PREMARKET_VALID_HOLDING_BOOST = 1.15


PREMARKET_FUND_RESIDUAL_BENCHMARK_MAP = {
    "007844": "oil_gas_ep",
    "006679": "oil_gas_ep",
    "018852": "oil_gas_ep",
    "012868": "sp500",
    "008401": "sp500",
    "519981": "sp500",
    "001092": "biotech",
}

PREMARKET_FUND_ESTIMATION_METHOD_MAP = {
    # 盘前/盘中/盘后/夜盘：只使用前十大持仓，可取行情持仓重新归一化到 100%。
    "015205": "top10_available_normalized",
}


def _footer_enabled_for_session(spec: dict, session: str) -> bool:
    flag = f"display_in_{session}_footer"
    if flag in spec:
        return bool(spec.get(flag))
    return bool(spec.get("display_in_footer", True))


def _footer_order_for_session(spec: dict, session: str) -> int:
    raw_value = spec.get(f"{session}_order", spec.get("order", 999))
    try:
        return int(raw_value)
    except Exception:
        return 999


def build_observation_footer_benchmark_keys(session: str) -> tuple[str, ...]:
    session_norm = str(session or "").strip().lower()
    rows = []
    for key, spec in PREMARKET_BENCHMARK_SPECS.items():
        if not isinstance(spec, dict):
            continue
        if not _footer_enabled_for_session(spec, session_norm):
            continue
        rows.append((_footer_order_for_session(spec, session_norm), str(key)))
    return tuple(key for _, key in sorted(rows, key=lambda item: (item[0], item[1])))


def build_observation_footer_labels(session: str) -> dict[str, str]:
    session_norm = str(session or "").strip().lower()
    labels: dict[str, str] = {}
    for key in build_observation_footer_benchmark_keys(session_norm):
        spec = PREMARKET_BENCHMARK_SPECS.get(key, {})
        footer_labels = spec.get("footer_labels") if isinstance(spec, dict) else None
        label = ""
        if isinstance(footer_labels, dict):
            label = str(footer_labels.get(session_norm, "")).strip()
        if not label and isinstance(spec, dict):
            label = str(
                spec.get(f"{session_norm}_footer_label")
                or spec.get("footer_label")
                or spec.get("label")
                or key
            ).strip()
        labels[key] = label or key
    return labels


PREMARKET_FOOTER_BENCHMARK_KEYS = build_observation_footer_benchmark_keys("premarket")
PREMARKET_FOOTER_LABELS = build_observation_footer_labels("premarket")
AFTERHOURS_FOOTER_BENCHMARK_KEYS = build_observation_footer_benchmark_keys("afterhours")
AFTERHOURS_FOOTER_LABELS = build_observation_footer_labels("afterhours")
INTRADAY_FOOTER_BENCHMARK_KEYS = build_observation_footer_benchmark_keys("intraday")
INTRADAY_FOOTER_LABELS = build_observation_footer_labels("intraday")
FUTU_NIGHT_FOOTER_BENCHMARK_KEYS = build_observation_footer_benchmark_keys("futu_night")
FUTU_NIGHT_FOOTER_LABELS = build_observation_footer_labels("futu_night")


__all__ = [
    "AFTERHOURS_FOOTER_BENCHMARK_KEYS",
    "AFTERHOURS_FOOTER_LABELS",
    "FUTU_NIGHT_FOOTER_BENCHMARK_KEYS",
    "FUTU_NIGHT_FOOTER_LABELS",
    "INTRADAY_FOOTER_BENCHMARK_KEYS",
    "INTRADAY_FOOTER_LABELS",
    "PREMARKET_BENCHMARK_SPECS",
    "PREMARKET_DEFAULT_RESIDUAL_BENCHMARK_KEY",
    "PREMARKET_VALID_HOLDING_BOOST",
    "PREMARKET_END_HOUR_BJ",
    "PREMARKET_END_MINUTE_BJ",
    "PREMARKET_FOOTER_BENCHMARK_KEYS",
    "PREMARKET_FOOTER_LABELS",
    "PREMARKET_FUND_ESTIMATION_METHOD_MAP",
    "PREMARKET_FUND_RESIDUAL_BENCHMARK_MAP",
    "PREMARKET_START_HOUR_BJ",
    "PREMARKET_START_MINUTE_BJ",
    "build_observation_footer_benchmark_keys",
    "build_observation_footer_labels",
]
