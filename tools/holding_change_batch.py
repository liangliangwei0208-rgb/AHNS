"""持仓图的季度、文件名及跨电脑批次合并规则（仅依赖标准库）。"""
from __future__ import annotations

import copy
import json
from typing import Any


RULES_VERSION = 1
RESULT_PREFIX = "AHNS_HOLDING_CHANGE_RESULT="
QUARTER_NAMES = {1: "一季度", 2: "二季度", 3: "三季度", 4: "四季度"}


def valid_quarter(value: Any) -> int:
    try:
        key = int(value)
        return key if 1900 <= key // 10 <= 2999 and key % 10 in QUARTER_NAMES else -1
    except (ValueError, TypeError, OverflowError):
        return -1


def holding_image_name(index: int, quarter: int, code: str) -> str:
    return f"{index}_{QUARTER_NAMES[quarter % 10]}_{code}.png"


def _index(item: dict) -> int:
    try:
        return max(0, int(item.get("index") or 0))
    except (ValueError, TypeError):
        return 0


def normalize_batch_indexes(batch: dict) -> None:
    """已分配序号优先保留；冲突输家追加编号并等待重绘，绝不搬移旧文件。"""
    quarter = valid_quarter(batch.get("quarter_key"))
    raw = batch.get("funds", {})
    funds = {str(code): dict(item) for code, item in raw.items()
             if isinstance(item, dict) and valid_quarter(item.get("quarter_key")) == quarter} if isinstance(raw, dict) else {}
    batch["funds"] = funds
    highest = max((_index(item) for item in funds.values()), default=0)
    used = set()
    ordered = sorted(funds, key=lambda code: (_index(funds[code]) or 10**12,
                                             str(funds[code].get("generated_at") or "9999"), code))
    for code in ordered:
        item = funds[code]
        index = _index(item)
        if not index or index in used:
            highest += 1
            index = highest
            item["needs_rebuild"] = True
        item["index"] = index
        used.add(index)


def merge_batch_states(ours: dict, theirs: dict) -> dict:
    """把 current/previous 放在一起比较，避免旧程序的季度往返污染当前批次。"""
    sources = []
    for state in (ours, theirs):
        for slot in ("current", "previous"):
            batch = state.get(slot)
            if isinstance(batch, dict) and valid_quarter(batch.get("quarter_key")) > 0:
                version = batch.get("rules_version", state.get("rules_version", 0) if slot == "current" else 0)
                sources.append((int(version or 0), batch))
    # 非业务字段也使用确定顺序，使交换合并方向得到相同结果。
    result = {key: copy.deepcopy(max((state[key] for state in (ours, theirs) if key in state),
                                    key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False)))
              for key in sorted(set(ours) | set(theirs))}
    if not sources:
        return result
    target = max(valid_quarter(batch.get("quarter_key")) for _, batch in sources)
    candidates = [(v, b) for v, b in sources if valid_quarter(b.get("quarter_key")) == target]
    version = max(v for v, _ in candidates)
    candidates = [b for v, b in candidates if v == version]
    current = copy.deepcopy(max(candidates, key=lambda b: (str(b.get("updated_at", "")), json.dumps(b, sort_keys=True))))
    current["funds"] = {}
    for batch in candidates:
        for code, item in (batch.get("funds") or {}).items():
            if not isinstance(item, dict) or valid_quarter(item.get("quarter_key")) != target:
                continue
            existing = current["funds"].get(code)
            def rank(value):
                return (str(value.get("generated_at") or ""), not value.get("needs_rebuild", False),
                        json.dumps(value, sort_keys=True, ensure_ascii=False))
            current["funds"][code] = copy.deepcopy(max((existing, item), key=rank) if existing else item)
    first = [b["first_generated_at"] for b in candidates if b.get("first_generated_at")]
    if first:
        current["first_generated_at"] = min(first)
    current["fund_pool_count"] = max(int(b.get("fund_pool_count") or 0) for b in candidates)
    current["rules_version"] = version
    normalize_batch_indexes(current)
    result["current"] = current
    result["rules_version"] = version
    older = [b for _, b in sources if valid_quarter(b.get("quarter_key")) < target]
    result["previous"] = copy.deepcopy(max(older, key=lambda b: (valid_quarter(b.get("quarter_key")),
                                                               json.dumps(b, sort_keys=True)))) if older else None
    return result
