"""
Project paths used by runtime scripts.

Keep frequently edited cache/output filenames here so daily workflow scripts do
not each carry their own hard-coded path strings.  Most plotting functions still
accept plain strings such as ``output/foo.png``; use ``relative_path_str`` when a
relative repo path is easier to read in logs or third-party drawing helpers.
"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = PROJECT_ROOT / "cache"
OUTPUT_DIR = PROJECT_ROOT / "output"

FUND_ESTIMATE_CACHE = CACHE_DIR / "fund_estimate_return_cache.json"
FUND_HOLDINGS_CACHE = CACHE_DIR / "fund_holdings_cache.json"
FUND_PURCHASE_LIMIT_CACHE = CACHE_DIR / "fund_purchase_limit_cache.json"
SECURITY_RETURN_CACHE = CACHE_DIR / "security_return_cache.json"
PREMARKET_QUOTE_CACHE = CACHE_DIR / "premarket_quote_cache.json"
AFTERHOURS_QUOTE_CACHE = CACHE_DIR / "afterhours_quote_cache.json"
INTRADAY_QUOTE_CACHE = CACHE_DIR / "intraday_quote_cache.json"
MARK_IMAGE = CACHE_DIR / "mark.jpg"

FIRST_PIC_IMAGE = OUTPUT_DIR / "first_pic.png"
HAIWAI_FUND_IMAGE = OUTPUT_DIR / "haiwai_fund.png"
HAIWAI_HOLIDAYS_IMAGE = OUTPUT_DIR / "haiwai_holidays.png"
SAFE_HAIWAI_FUND_IMAGE = OUTPUT_DIR / "safe_haiwai_fund.png"
SAFE_HAIWAI_PREMARKET_IMAGE = OUTPUT_DIR / "safe_haiwai_premarket.png"
PREMARKET_FAILED_HOLDINGS_REPORT = OUTPUT_DIR / "premarket_failed_holdings_latest.txt"
SAFE_HAIWAI_AFTERHOURS_IMAGE = OUTPUT_DIR / "safe_haiwai_afterhours.png"
AFTERHOURS_FAILED_HOLDINGS_REPORT = OUTPUT_DIR / "afterhours_failed_holdings_latest.txt"
SAFE_HAIWAI_INTRADAY_IMAGE = OUTPUT_DIR / "safe_haiwai_intraday.png"
INTRADAY_FAILED_HOLDINGS_REPORT = OUTPUT_DIR / "intraday_failed_holdings_latest.txt"
SAFE_HOLIDAYS_IMAGE = OUTPUT_DIR / "safe_holidays.png"
SAFE_SUM_HOLIDAYS_IMAGE = OUTPUT_DIR / "safe_sum_holidays.png"
KEPU_SUM_HOLIDAYS_IMAGE = OUTPUT_DIR / "kepu_sum_holidays.png"
KEPU_XIANE_IMAGE = OUTPUT_DIR / "kepu_xiane.png"
XIANE_IMAGE = OUTPUT_DIR / "xiane.png"


def ensure_runtime_dirs() -> None:
    """Create the standard cache/output directories used by runtime scripts."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def relative_path_str(path: str | Path) -> str:
    """
    Return a repo-relative path string when possible.

    Existing plotting functions and logs are easiest to scan when they receive
    ``output/foo.png`` rather than an absolute Windows path.  Absolute paths
    outside the repo are returned unchanged.
    """
    path_obj = Path(path)
    if not path_obj.is_absolute():
        return path_obj.as_posix()

    try:
        return path_obj.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path_obj)


__all__ = [
    "PROJECT_ROOT",
    "CACHE_DIR",
    "OUTPUT_DIR",
    "FUND_ESTIMATE_CACHE",
    "FUND_HOLDINGS_CACHE",
    "FUND_PURCHASE_LIMIT_CACHE",
    "SECURITY_RETURN_CACHE",
    "PREMARKET_QUOTE_CACHE",
    "AFTERHOURS_QUOTE_CACHE",
    "INTRADAY_QUOTE_CACHE",
    "MARK_IMAGE",
    "FIRST_PIC_IMAGE",
    "HAIWAI_FUND_IMAGE",
    "HAIWAI_HOLIDAYS_IMAGE",
    "SAFE_HAIWAI_FUND_IMAGE",
    "SAFE_HAIWAI_PREMARKET_IMAGE",
    "PREMARKET_FAILED_HOLDINGS_REPORT",
    "SAFE_HAIWAI_AFTERHOURS_IMAGE",
    "AFTERHOURS_FAILED_HOLDINGS_REPORT",
    "SAFE_HAIWAI_INTRADAY_IMAGE",
    "INTRADAY_FAILED_HOLDINGS_REPORT",
    "SAFE_HOLIDAYS_IMAGE",
    "SAFE_SUM_HOLIDAYS_IMAGE",
    "KEPU_SUM_HOLIDAYS_IMAGE",
    "KEPU_XIANE_IMAGE",
    "XIANE_IMAGE",
    "ensure_runtime_dirs",
    "relative_path_str",
]
