"""
quote_manager.py

语录管理模块：只负责从 txt 文件读取每日语录，并尽量避免重复。
不负责股票分析，也不负责发邮件。

使用方式：
    from tools.quote_manager import get_daily_quote_text

    quote_text = get_daily_quote_text(
        quote_file="investment_quotes.txt",
        history_file="investment_quote_history.json",
    )
"""

from __future__ import annotations

from pathlib import Path
import json
import pandas as pd


DEFAULT_QUOTE = "市场不会奖励冲动，只奖励可重复的纪律。"


def load_quotes_from_txt(quote_file: str | Path = "investment_quotes.txt") -> list[str]:
    """
    从 txt 文件读取语录。

    规则：
        1. 每一行是一条语录；
        2. 空行会被忽略；
        3. 以 # 开头的行会被忽略；
        4. 自动去重，但保留原始顺序。

    如果文件不存在或没有有效语录，返回 [DEFAULT_QUOTE]。
    """
    path = Path(quote_file)

    if not path.exists():
        return [DEFAULT_QUOTE]

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        lines = path.read_text(encoding="gbk").splitlines()

    quotes = []
    seen = set()

    for line in lines:
        quote = line.strip()

        if not quote:
            continue

        if quote.startswith("#"):
            continue

        if quote in seen:
            continue

        quotes.append(quote)
        seen.add(quote)

    return quotes if quotes else [DEFAULT_QUOTE]


def _load_quote_history(history_file: str | Path) -> dict:
    """
    读取语录使用历史。
    """
    path = Path(history_file)

    if not path.exists():
        return {
            "by_date": {},
            "used_quotes": [],
        }

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("history json is not a dict")

        by_date = data.get("by_date", {})
        used_quotes = data.get("used_quotes", [])

        if not isinstance(by_date, dict):
            by_date = {}

        if not isinstance(used_quotes, list):
            used_quotes = []

        return {
            "by_date": by_date,
            "used_quotes": used_quotes,
        }

    except Exception:
        return {
            "by_date": {},
            "used_quotes": [],
        }


def _save_quote_history(history_file: str | Path, history: dict) -> None:
    """
    保存语录使用历史。
    """
    path = Path(history_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    by_date = history.get("by_date", {})

    # 只保留最近约 2 年的日期记录，防止文件无限增长
    if isinstance(by_date, dict) and len(by_date) > 730:
        keep_keys = sorted(by_date.keys())[-730:]
        history["by_date"] = {k: by_date[k] for k in keep_keys}

    with path.open("w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def get_daily_quote(
    quote_file: str | Path = "investment_quotes.txt",
    history_file: str | Path = "investment_quote_history.json",
    today=None,
) -> str:
    """
    从 txt 文件中选择每日语录，并尽可能避免重复。

    规则：
        1. 同一天重复运行，返回同一句；
        2. 优先从未使用过的语录中选择；
        3. 所有语录用完后，清空 used_quotes，重新开始下一轮；
        4. 选择结果写入 history_file。
    """
    if today is None:
        today = pd.Timestamp.today().date()

    date_key = str(today)
    quotes = load_quotes_from_txt(quote_file)
    history = _load_quote_history(history_file)

    by_date = history.setdefault("by_date", {})
    used_quotes = history.setdefault("used_quotes", [])

    # 同一天多次运行，保持同一句
    if date_key in by_date:
        return by_date[date_key]

    # 如果 txt 文件改过，历史里已经不存在于当前 quotes 的语录要过滤掉
    quote_set = set(quotes)
    used_quotes[:] = [q for q in used_quotes if q in quote_set]

    used_set = set(used_quotes)
    candidates = [q for q in quotes if q not in used_set]

    # 全部用完后开启新一轮
    if not candidates:
        used_quotes.clear()
        candidates = quotes.copy()

    # 用日期确定索引；保证每天稳定，同时尽量不重复
    idx = int(pd.Timestamp(today).strftime("%Y%m%d")) % len(candidates)
    quote = candidates[idx]

    by_date[date_key] = quote
    used_quotes.append(quote)

    _save_quote_history(history_file, history)

    return quote


def get_daily_quote_text(
    quote_file: str | Path = "investment_quotes.txt",
    history_file: str | Path = "investment_quote_history.json",
    today=None,
    prefix: str = "每日投资语录：",
) -> str:
    """
    返回可直接放入邮件正文的语录文本。

    prefix:
        默认加“每日投资语录：”；
        如果不想加前缀，传 prefix=""。
    """
    quote = get_daily_quote(
        quote_file=quote_file,
        history_file=history_file,
        today=today,
    )

    return f"{prefix}{quote}" if prefix else quote


__all__ = [
    "load_quotes_from_txt",
    "get_daily_quote",
    "get_daily_quote_text",
]
