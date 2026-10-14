"""只读正式数据的验收导出；图片写在邮件目录之外，不制造正式事件。"""
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from tools.configs.index_fund_tracking_configs import INDEX_FUND_TRACKING_MAP
from tools.fund_limit_change import BJ
from tools.index_fund_tracking import calculate_comparison, normalize_limit, read_mapping
from tools.index_fund_tracking_image import render_index_fund_limit_change
from tools.paths import CACHE_DIR, PROJECT_ROOT, MARK_IMAGE


def main():
    import pandas as pd
    returns = read_mapping(CACHE_DIR / "index_fund_tracking_returns.json")
    limits = read_mapping(CACHE_DIR / "fund_purchase_limit_cache.json")
    state = read_mapping(CACHE_DIR / "index_fund_tracking_state.json")
    now = datetime.now(BJ)
    artifacts = PROJECT_ROOT / "tests" / "artifacts" / "index_fund_tracking"
    artifacts.mkdir(parents=True, exist_ok=True)
    rows = []
    for code, group in INDEX_FUND_TRACKING_MAP.items():
        data = returns["funds"][code]
        nav, comparison = data["nav"], data["comparison"]
        profile = nav["profile"]
        # 重新计算端点，不依赖缓存中已有收益数字；逐一核验正式结果。
        prices = pd.read_csv(CACHE_DIR / f"dot_{group['index_symbol'][1:]}_index_daily.csv")
        verified = calculate_comparison(nav["series"], prices, now=now, latest_nav_date=nav["latest_nav_date"])
        for key, period in comparison["periods"].items():
            assert period == verified["periods"][key], (code, key)
        raw_limit = limits[code]
        audit = {"fund_code": code, **profile, "limit_status": normalize_limit(raw_limit),
                 "limit_amount": normalize_limit(raw_limit) if re.fullmatch(r"\d+(?:\.\d+)?元", normalize_limit(raw_limit) or "") else None,
                 "limit_source": raw_limit.get("source"), "limit_fetched_at": raw_limit.get("fetched_at"),
                 "limit_raw": raw_limit, "fund_source": nav.get("source_url"),
                 "fund_source_params": nav.get("source_params"), "nav_last_success_at": nav.get("last_success_at"),
                 "nav_last_attempt_at": nav.get("last_attempt_at"), **comparison,
                 "missing_reasons": [p["reason"] for p in comparison["periods"].values() if p["reason"]]}
        for key, period in comparison["periods"].items():
            audit[f"fund_return_{key}"] = period["fund_return"]
            audit[f"index_return_{key}"] = period["index_return"]
        if profile.get("currency") == "unknown":
            audit["missing_reasons"].append("币种尚无明确资料证据；图片明示币种待核验")
        rows.append(audit)
        assert not state["funds"][code].get("events"), "首次初始化不应制造事件"
    for code, filename in (("021000", "real_nasdaq100_acceptance.png"), ("017641", "real_sp500_acceptance.png")):
        row = next(x for x in rows if x["fund_code"] == code)
        value = normalize_limit(limits[code])
        render_index_fund_limit_change(row, {"id": f"acceptance-{code}", "old_value": value, "new_value": value,
                                      "detected_at": limits[code]["fetched_at"]}, row, artifacts / filename,
                                      logo_path=MARK_IMAGE, validation=True)
    # 固定样本仅做布局验收，全部存于邮件扫描目录之外。
    from copy import deepcopy
    fixed = deepcopy(next(x for x in rows if x["fund_code"] == "021000"))
    for months, fund, index in ((1, -3.5, 4), (3, 0, None), (6, 14, -2), (12, None, None)):
        fixed["periods"][f"{months}m"].update(fund_return=fund, index_return=index)
    for case, old, new in (("amount_decrease", "1000元", "100元"), ("amount_increase", "100元", "1000元"),
                           ("pause", "100元", "暂停申购"), ("resume", "暂停申购", "不限额度"),
                           ("negative", "100元", "500元"), ("partial_missing", "100元", "500元"),
                           ("long_name", "100元", "500元")):
        sample = deepcopy(fixed)
        if case == "long_name":
            sample["fund_name"] = "用于离线几何检查的超长基金名称"*6
        render_index_fund_limit_change(sample, {"id": f"fixed-{case}", "old_value": old, "new_value": new,
                                      "detected_at": "2026-10-14T12:00:00+08:00"}, sample,
                                      artifacts / f"fixed_{case}.png", logo_path=MARK_IMAGE)
    doc = {"audited_at": now.isoformat(timespec="seconds"), "fund_count": len(rows),
           "indices": returns["indices"], "funds": rows, "initialization_event_count": 0,
           "images": [(artifacts / name).relative_to(PROJECT_ROOT).as_posix()
                      for name in ("real_nasdaq100_acceptance.png", "real_sp500_acceptance.png")]}
    audit_path = PROJECT_ROOT / "docs" / "audits" / "index-fund-tracking-2026-10-14.json"
    audit_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    before_path = artifacts / "protected-before.json"
    if before_path.exists():
        protected = read_mapping(before_path)
        changed = [p for p, digest in protected.items() if hashlib.sha256((CACHE_DIR / p).read_bytes()).hexdigest() != digest]
        assert not changed, f"受保护缓存被修改: {changed}"
        print(f"受保护缓存哈希一致: {len(protected)}份")
    print(f"真实初始化 {len(rows)}/25，四期端点独立复核完成，正式事件0；审计: {audit_path}")


if __name__ == "__main__":
    main()
