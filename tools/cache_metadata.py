"""
Cache metadata helpers.

This module keeps human-readable cache descriptions in one place.  Some JSON
caches can safely carry a top-level ``_cache_info`` field; key-map caches and
CSV files are documented through ``cache/README.md`` instead.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any


CACHE_INFO_VERSION = 1
CACHE_README_FILENAME = "README.md"

EMBEDDED_CACHE_INFO_FILENAMES = {
    "fund_estimate_return_cache.json",
    "a_share_trade_calendar_cache.json",
}

_INFO_BY_NAME: dict[str, dict[str, Any]] = {
    "fund_estimate_return_cache.json": {
        "purpose": "海外/全球基金每日估算收益和海外基准结果缓存，供 safe 图、节假日累计图和拆解工具只读复用。",
        "producer": "tools/get_top10_holdings.py 在海外基金估算表生成后写入。",
        "consumers": [
            "safe_fund.py",
            "safe_holidays.py",
            "holidays.py",
            "sum_holidays.py",
            "fund_estimate_breakdown.py",
        ],
        "refresh_policy": "同一基金或基准、同一 valuation_anchor_date 使用固定 key，按数据质量覆盖。",
        "retention_policy": "records 与 benchmark_records 默认保留最近 300 天；国内历史记录会被裁剪。",
        "data_shape": "顶层包含 version、updated_at、records、benchmark_records；基金记录在 records，基准记录在 benchmark_records。",
        "notes": [
            "只有本文件适合内嵌 _cache_info，因为真实缓存项不在顶层直接枚举。",
            "VIX 这类点位记录使用 value/value_type/display_value，不参与累计收益。",
        ],
    },
    "a_share_trade_calendar_cache.json": {
        "purpose": "A 股交易日历文件缓存，用于判断普通交易日、周末和节假日累计窗口。",
        "producer": "tools/fund_history_io.py 从 AkShare 交易日历刷新后写入。",
        "consumers": [
            "safe_holidays.py",
            "holidays.py",
            "sum_holidays.py",
            "kepu/kepu_sum_holidays.py",
        ],
        "refresh_policy": "缓存新鲜期默认 7 天；过期后才尝试联网刷新，失败时允许用旧缓存兜底。",
        "retention_policy": "整份交易日历覆盖写入，不按每日追加。",
        "data_shape": "顶层包含 fetched_at、source、trade_dates；trade_dates 是 YYYY-MM-DD 字符串列表。",
        "notes": [
            "本文件适合内嵌 _cache_info，因为读取方只读取固定字段。",
            "trade_dates 通常覆盖多年历史和当年未来已公布交易日。",
        ],
    },
    "security_return_cache.json": {
        "purpose": "证券、指数和锚点行情收益缓存，降低重复行情请求并保护已确认完整交易日结果。",
        "producer": "tools/get_top10_holdings.py 在拉取 CN/HK/US/KR/指数/期货等行情后写入。",
        "consumers": [
            "tools/get_top10_holdings.py",
            "fund_estimate_breakdown.py",
        ],
        "refresh_policy": "traded/closed 稳定记录优先保留；pending/missing/stale 只短期复用后重试。",
        "retention_policy": "小时桶 15 天，普通证券日线 30 天，指数和稳定锚点 300 天。",
        "data_shape": "顶层是缓存 key -> 行情记录的映射，例如 SECURITY:US:NVDA:2026-05-08。",
        "notes": [
            "不要在顶层内嵌 _cache_info，避免遍历逻辑把说明误认为行情记录。",
        ],
    },
    "fund_holdings_cache.json": {
        "purpose": "基金最近披露前 N 大股票持仓缓存，用于估算海外/全球基金持仓贡献。",
        "producer": "tools/get_top10_holdings.py 在首次缺失或披露窗口低频试探时写入。",
        "consumers": [
            "tools/get_top10_holdings.py",
            "fund_estimate_breakdown.py",
        ],
        "refresh_policy": "非披露窗口直接复用；披露窗口内每只基金约 3 天最多试探一次。",
        "retention_policy": "每个 fund_code:topN 一个 key，更新时覆盖同 key，不按日期追加。",
        "data_shape": "顶层是 fund_code:topN -> 持仓记录的映射；data_json 内保存持仓表。",
        "notes": [
            "不要在顶层内嵌 _cache_info，避免遍历逻辑把说明误认为基金持仓。",
        ],
    },
    "fund_purchase_limit_cache.json": {
        "purpose": "基金限购金额缓存，用于每日基金图展示模型观察限购信息。",
        "producer": "tools/get_top10_holdings.py 解析公开网页限购文本后写入。",
        "consumers": [
            "tools/get_top10_holdings.py",
            "kepu/kepu_xiane.py",
        ],
        "refresh_policy": "默认 7 天刷新一次；新结果为未知且旧值明确时保留旧值。",
        "retention_policy": "每个基金代码一个 key，更新时覆盖同 key，不按日期追加。",
        "data_shape": "顶层是 fund_code -> {fetched_at, value} 的映射。",
        "notes": [
            "不要在顶层内嵌 _cache_info，避免遍历逻辑把说明误认为限购记录。",
        ],
    },
    "mark.jpg": {
        "purpose": "safe 公开图使用的居中 logo 水印素材。",
        "producer": "人工维护。",
        "consumers": [
            "tools/safe_display.py",
            "safe_fund.py",
            "safe_holidays.py",
            "sum_holidays.py",
        ],
        "refresh_policy": "需要更换水印素材时人工替换。",
        "retention_policy": "固定资源文件，不由运行脚本裁剪。",
        "data_shape": "JPEG 图片。",
        "notes": [
            "不是行情缓存；保留在 cache/ 下是为了 GitHub Actions 和本地运行共用路径。",
        ],
    },
}

_PATTERN_INFO: list[tuple[str, dict[str, Any]]] = [
    (
        "*_index_daily.csv",
        {
            "purpose": "RSI 和指数分析图使用的本地日线行情 CSV 缓存。",
            "producer": "tools/rsi_data.py 从 AkShare、Yahoo、腾讯或新浪等数据源拉取后写入。",
            "consumers": [
                "stock_analysis.py",
                "tools/rsi_data.py",
            ],
            "refresh_policy": "当天已检查或包含今日记录时优先复用；国内 ETF 可在历史缓存较新时只补实时点。",
            "retention_policy": "写入前按调用参数保留最近 days 行，常见为 15、180 或 1200 行。",
            "data_shape": "CSV 表，常见列为 date、open、high、low、close、volume、amount。",
            "notes": [
                "不要在 CSV 文件头部添加说明行，避免 pandas.read_csv() 把说明当成数据。",
            ],
        },
    ),
]


def _normalize_filename(filename: str | Path) -> str:
    return Path(str(filename)).name


def cache_info_for_filename(filename: str | Path) -> dict[str, Any] | None:
    name = _normalize_filename(filename)
    info = _INFO_BY_NAME.get(name)
    if info is not None:
        return info

    for pattern, pattern_info in _PATTERN_INFO:
        if fnmatch.fnmatch(name, pattern):
            return pattern_info

    return None


def _embedded_cache_info(filename: str | Path) -> dict[str, Any] | None:
    name = _normalize_filename(filename)
    if name not in EMBEDDED_CACHE_INFO_FILENAMES:
        return None

    info = cache_info_for_filename(name)
    if info is None:
        return None

    return {
        "schema_version": CACHE_INFO_VERSION,
        "cache_file": name,
        **info,
    }


def attach_cache_info(filename: str | Path, data):
    """
    Return data with top-level _cache_info only for safe container JSON files.
    """
    info = _embedded_cache_info(filename)
    if info is None or not isinstance(data, dict):
        return data

    with_info = {"_cache_info": info}
    for key, value in data.items():
        if key != "_cache_info":
            with_info[key] = value
    return with_info


def build_cache_readme(cache_dir: str | Path) -> str:
    cache_path = Path(cache_dir)
    files = []
    if cache_path.exists():
        files = sorted(
            p.name
            for p in cache_path.iterdir()
            if p.is_file() and p.name != CACHE_README_FILENAME and not p.name.endswith(".tmp")
        )

    lines = [
        "# AHNS cache 目录说明",
        "",
        "本文件由 `tools/cache_metadata.py` 生成，用于说明各缓存文件的用途、刷新策略和读取方。",
        "不要在 CSV 缓存或 key-map JSON 顶层手工添加注释字段，可能破坏现有读取逻辑。",
        "",
        "## 当前缓存文件",
        "",
    ]

    if not files:
        lines.append("当前未发现缓存文件。")
        lines.append("")
        return "\n".join(lines)

    for name in files:
        info = cache_info_for_filename(name)
        lines.append(f"### `{name}`")
        if info is None:
            lines.append("- 用途：暂未登记。")
            lines.append("- 说明位置：仅在本 README 中记录。")
            lines.append("")
            continue

        embedded = name in EMBEDDED_CACHE_INFO_FILENAMES
        lines.extend(
            [
                f"- 用途：{info['purpose']}",
                f"- 生成：{info['producer']}",
                f"- 读取：{', '.join(info['consumers'])}",
                f"- 刷新：{info['refresh_policy']}",
                f"- 保留：{info['retention_policy']}",
                f"- 结构：{info['data_shape']}",
                f"- 说明位置：{'本文件内嵌 `_cache_info` + 本 README' if embedded else '本 README'}",
            ]
        )
        for note in info.get("notes", []):
            lines.append(f"- 注意：{note}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_cache_readme(cache_dir: str | Path) -> Path:
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    readme_path = cache_path / CACHE_README_FILENAME
    readme_path.write_text(build_cache_readme(cache_path), encoding="utf-8")
    return readme_path


def refresh_embedded_cache_info(cache_dir: str | Path) -> list[Path]:
    """
    Add or update _cache_info for safe JSON caches that already exist.
    """
    cache_path = Path(cache_dir)
    updated: list[Path] = []
    for filename in sorted(EMBEDDED_CACHE_INFO_FILENAMES):
        path = cache_path / filename
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        with_info = attach_cache_info(filename, data)
        if with_info == data:
            continue
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(with_info, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)
        updated.append(path)
    return updated


def main() -> None:
    cache_dir = Path(__file__).resolve().parents[1] / "cache"
    updated = refresh_embedded_cache_info(cache_dir)
    readme_path = write_cache_readme(cache_dir)
    print(f"cache README written: {readme_path}")
    if updated:
        print("embedded cache info updated:")
        for path in updated:
            print(f"- {path}")


if __name__ == "__main__":
    main()
