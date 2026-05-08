"""
Compatibility facade for fund estimation helpers.

The maintained implementation lives in ``tools.get_top10_holdings``.  This file
keeps the historical ``tools.fund_estimator`` import path working for wrapper
modules and older scripts, including explicit imports of private compatibility
helpers such as ``_load_json_cache``.
"""

from __future__ import annotations

from tools import get_top10_holdings as _impl


for _name in dir(_impl):
    if _name.startswith("__"):
        continue
    globals()[_name] = getattr(_impl, _name)


def __getattr__(name: str):
    return getattr(_impl, name)


__all__ = [name for name in dir(_impl) if not name.startswith("__")]
