"""Small Rich-backed console helpers with plain-text fallback.

The project is often run in three different places:
- an interactive PowerShell window, where dynamic progress and tables help;
- git_main.py subprocess capture, where clean text is safer;
- redirected output, such as fund_estimate_breakdown.py --save-txt.

This module keeps those display choices out of the calculation code.
"""

from __future__ import annotations

import os
import sys
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterable, Sequence


try:  # pragma: no cover - fallback is exercised only without rich installed.
    from rich import box
    from rich.console import Console
    from rich.markup import escape as rich_escape
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table
except Exception:  # pragma: no cover
    box = None
    Console = None
    rich_escape = None
    Panel = None
    Progress = None
    Table = None
    BarColumn = None
    SpinnerColumn = None
    TaskProgressColumn = None
    TextColumn = None
    TimeElapsedColumn = None


_CURRENT_PROGRESS: ContextVar["FundProgress | None"] = ContextVar("ahns_progress", default=None)
_FALSE_VALUES = {"0", "false", "no", "off"}
_VERBOSE_VALUES = {"2", "full", "verbose", "debug", "detail", "details"}
_NUMERIC_TABLE_LABELS = {"模型估算观察", "盘前模型观察", "盘中模型观察", "盘后模型观察", "夜盘模型观察"}


def rich_enabled() -> bool:
    if progress_disabled():
        return False
    if Console is None:
        return False
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def progress_disabled() -> bool:
    return str(os.environ.get("AHNS_PROGRESS", "")).strip().lower() in _FALSE_VALUES


def progress_verbosity() -> str:
    value = str(os.environ.get("AHNS_PROGRESS_DETAIL", "1")).strip().lower()
    if value in _FALSE_VALUES:
        return "summary"
    if value in _VERBOSE_VALUES:
        return "verbose"
    return "compact"


def progress_detail_enabled() -> bool:
    return progress_verbosity() != "summary"


def progress_verbose_enabled() -> bool:
    return progress_verbosity() == "verbose"


def _console() -> Any:
    return Console() if rich_enabled() and Console is not None else None


def _format_cell(value: Any) -> str:
    if value is None:
        return ""
    try:
        if value != value:  # NaN
            return ""
    except Exception:
        pass
    return str(value)


def _display_width(text: Any) -> int:
    width = 0
    for char in _format_cell(text):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _truncate_display(text: Any, max_width: int) -> str:
    raw = _format_cell(text)
    if max_width <= 0 or _display_width(raw) <= max_width:
        return raw
    suffix = "..."
    suffix_width = _display_width(suffix)
    if max_width <= suffix_width:
        return suffix[:max_width]
    out = []
    used = 0
    for char in raw:
        char_width = 0 if unicodedata.combining(char) else (
            2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        )
        if used + char_width + suffix_width > max_width:
            break
        out.append(char)
        used += char_width
    return "".join(out) + suffix


def _pad_display(text: Any, width: int, *, justify: str = "left") -> str:
    raw = _truncate_display(text, width)
    pad = max(0, width - _display_width(raw))
    if justify == "right":
        return " " * pad + raw
    if justify == "center":
        left = pad // 2
        return " " * left + raw + " " * (pad - left)
    return raw + " " * pad


def _value_style(column: str, value: Any) -> str:
    text = _format_cell(value)
    lower_col = str(column or "").lower()
    if str(column or "") not in _NUMERIC_TABLE_LABELS and not any(
        token in lower_col for token in ["涨跌", "收益", "贡献", "return", "pct"]
    ):
        return ""
    try:
        numeric = float(str(text).replace("%", "").replace("+", "").strip())
    except Exception:
        return ""
    if numeric > 0:
        return "bold red"
    if numeric < 0:
        return "bold green"
    return "dim"


def _escape_rich_text(value: Any) -> str:
    text = _format_cell(value)
    if rich_escape is None:
        return text
    return rich_escape(text)


def _column_pairs(columns: Sequence[Any] | None, rows: Sequence[dict[str, Any]]) -> list[tuple[str, str]]:
    if columns:
        pairs: list[tuple[str, str]] = []
        for item in columns:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                pairs.append((str(item[0]), str(item[1])))
            else:
                text = str(item)
                pairs.append((text, text))
        return pairs

    keys: list[str] = []
    for row in rows:
        for key in row.keys():
            key_text = str(key)
            if key_text not in keys:
                keys.append(key_text)
    return [(key, key) for key in keys]


def print_records_table(
    rows: Sequence[dict[str, Any]],
    *,
    title: str | None = None,
    columns: Sequence[Any] | None = None,
) -> None:
    row_list = list(rows or [])
    pairs = _column_pairs(columns, row_list)
    console = _console()

    if console is None or Table is None:
        if title:
            print(title, flush=True)
        if not pairs:
            print("(无数据)", flush=True)
            return
        max_width_by_label = {
            "基金名称": 38,
            "指数名称": 30,
            "模型观察基金信息": 22,
            "限购金额": 18,
            "基准日或区间": 20,
        }
        widths = []
        for key, label in pairs:
            max_width = max_width_by_label.get(label, 24)
            if label in _NUMERIC_TABLE_LABELS or any(token in label for token in ["涨跌", "收益", "贡献", "权重", "比例", "%"]):
                max_width = min(max_width, 16)
            width = max(4, min(max_width, _display_width(label)))
            for row in row_list:
                width = max(width, min(max_width, _display_width(row.get(key))))
            widths.append(width)

        border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
        header = "| " + " | ".join(
            _pad_display(label, width, justify="center")
            for (_, label), width in zip(pairs, widths)
        ) + " |"
        print(border, flush=True)
        print(header, flush=True)
        print(border, flush=True)
        for row in row_list:
            cells = []
            for (key, label), width in zip(pairs, widths):
                justify = "right" if (
                    label in _NUMERIC_TABLE_LABELS
                    or any(token in label for token in ["涨跌", "收益", "贡献", "权重", "比例", "%", "序号"])
                ) else "left"
                cells.append(_pad_display(row.get(key), width, justify=justify))
            print("| " + " | ".join(cells) + " |", flush=True)
        print(border, flush=True)
        return

    table = Table(title=title, box=box.SIMPLE_HEAVY if box is not None else None, show_lines=False)
    for key, label in pairs:
        justify = "right" if (
            label in _NUMERIC_TABLE_LABELS
            or any(token in label for token in ["涨跌", "收益", "贡献", "权重", "比例", "%"])
        ) else "left"
        table.add_column(label, justify=justify, overflow="fold", no_wrap=False)
    for row in row_list:
        cells = []
        for key, label in pairs:
            value = row.get(key)
            style = _value_style(label, value)
            text = _escape_rich_text(value)
            cells.append(f"[{style}]{text}[/]" if style else text)
        table.add_row(*cells)
    console.print(table)


def print_dataframe_table(
    df: Any,
    *,
    title: str | None = None,
    columns: Sequence[Any] | None = None,
) -> None:
    try:
        rows = df.to_dict(orient="records")
    except Exception:
        if title:
            print(title, flush=True)
        print(df, flush=True)
        return
    print_records_table(rows, title=title, columns=columns)


def print_key_values(title: str, items: Sequence[tuple[str, Any]]) -> None:
    console = _console()
    if console is None or Table is None or Panel is None:
        print(title, flush=True)
        for key, value in items:
            print(f"{key}: {_format_cell(value)}", flush=True)
        return

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    for key, value in items:
        table.add_row(_escape_rich_text(key), _escape_rich_text(value))
    console.print(Panel(table, title=title, border_style="cyan"))


def print_stage(message: str) -> None:
    console = _console()
    if console is None:
        print(message, flush=True)
    else:
        console.print(f"[cyan]{message}[/cyan]")


@dataclass
class FundProgress:
    title: str
    total: int
    transient: bool = True

    def __post_init__(self) -> None:
        self.total = max(0, int(self.total or 0))
        self.success_count = 0
        self.failed_count = 0
        self.cache_count = 0
        self.last_cache_message = ""
        self._started_count = 0
        self._current_label = ""
        self._last_status = ""
        self._disabled = progress_disabled()
        self._enabled = rich_enabled() and Progress is not None
        self._progress = None
        self._task_id = None
        self._token = None

    def __enter__(self) -> "FundProgress":
        if self._disabled:
            return self
        self._token = _CURRENT_PROGRESS.set(self)
        if self._enabled:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                TextColumn("[dim]{task.fields[status]}"),
                transient=self.transient,
            )
            self._progress.__enter__()
            self._task_id = self._progress.add_task(self.title, total=self.total, status="")
        else:
            print(f"[PROGRESS] {self.title} 开始，共 {self.total} 项", flush=True)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._disabled:
            return
        if self._enabled and self._progress is not None:
            self._progress.__exit__(exc_type, exc, tb)
        summary = (
            f"[PROGRESS] {self.title} 完成: 成功 {self.success_count}，"
            f"失败 {self.failed_count}，缓存事件 {self.cache_count}"
        )
        if self.last_cache_message:
            summary += f"，最近缓存: {self.last_cache_message}"
        print(summary, flush=True)
        if self._token is not None:
            _CURRENT_PROGRESS.reset(self._token)

    def start_item(self, label: str) -> None:
        self._started_count += 1
        self._current_label = str(label)
        self._last_status = ""
        if self._enabled and self._progress is not None and self._task_id is not None:
            self._progress.update(
                self._task_id,
                description=str(label),
                status=f"成功 {self.success_count} / 失败 {self.failed_count}",
            )
        elif progress_verbose_enabled():
            print(f"[PROGRESS] {self.title}: {label}", flush=True)
        elif progress_detail_enabled():
            print(self._text_progress_line("开始", str(label)), flush=True)

    def advance(self, *, success: bool = True, status: str = "") -> None:
        if success:
            self.success_count += 1
        else:
            self.failed_count += 1
        text = status or f"成功 {self.success_count} / 失败 {self.failed_count}"
        if self._enabled and self._progress is not None and self._task_id is not None:
            self._progress.advance(self._task_id, 1)
            self._progress.update(self._task_id, status=text)
        elif progress_detail_enabled() and not progress_verbose_enabled():
            detail = status or self._last_status or self._current_label or text
            print(self._text_progress_line("完成" if success else "失败", detail), flush=True)

    def advance_units(self, count: int, *, success: bool = True, status: str = "") -> None:
        count = max(0, int(count or 0))
        if count <= 0:
            return
        if success:
            self.success_count += count
        else:
            self.failed_count += count
        text = status or f"成功 {self.success_count} / 失败 {self.failed_count}"
        if self._enabled and self._progress is not None and self._task_id is not None:
            self._progress.advance(self._task_id, count)
            self._progress.update(self._task_id, status=text)
        elif progress_detail_enabled() and not progress_verbose_enabled():
            detail = status or self._last_status or self._current_label or text
            print(self._text_progress_line("完成" if success else "失败", detail), flush=True)

    def set_status(self, status: str) -> None:
        self._last_status = str(status or "")
        if self._enabled and self._progress is not None and self._task_id is not None:
            self._progress.update(self._task_id, status=str(status))
        elif status and progress_verbose_enabled():
            print(f"[PROGRESS] {self.title}: {status}", flush=True)

    def cache_event(self, message: str) -> None:
        self.cache_count += 1
        self.last_cache_message = str(message)
        if self._enabled and self._progress is not None and self._task_id is not None:
            self._progress.update(self._task_id, status=f"缓存 {self.cache_count}: {self.last_cache_message[:40]}")
        elif progress_verbose_enabled():
            print(f"[CACHE] {self.last_cache_message}", flush=True)

    def _text_progress_line(self, phase: str, detail: str) -> str:
        total = self.total if self.total > 0 else max(self._started_count, self.success_count + self.failed_count)
        done = min(total, self.success_count + self.failed_count)
        current = min(total, max(self._started_count, done))
        bar_width = 18
        fill = int(round((done / total) * bar_width)) if total else 0
        if phase == "开始" and current > done and total:
            fill = max(fill, min(bar_width, int(((current - 1) / total) * bar_width)))
        bar = "#" * fill + "." * (bar_width - fill)
        return (
            f"[PROGRESS] {self.title} [{bar}] {current}/{total} "
            f"成功 {self.success_count} 失败 {self.failed_count} | {phase}: {detail}"
        )


def fund_progress(title: str, total: int, *, transient: bool = True) -> FundProgress:
    return FundProgress(title=title, total=total, transient=transient)


def cache_log(message: str) -> None:
    current = _CURRENT_PROGRESS.get()
    if current is not None:
        current.cache_event(message)
        return
    print(f"[CACHE] {message}", flush=True)
