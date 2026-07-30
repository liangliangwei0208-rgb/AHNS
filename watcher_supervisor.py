"""以 UTF-8 记录监听日志，并在监听器异常退出后自动恢复。"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_LOG = PROJECT_ROOT / "logs" / "service_command_watcher.log"


def append_log(log_path: Path, message: str) -> None:
    """单次打开日志，避免长期占用文件导致恢复进程无法启动。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(message.rstrip("\r\n") + "\r\n")


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def in_service_window() -> bool:
    # 小电脑系统时区固定为北京时间；00:00-06:00 交给睡眠任务管理。
    return datetime.now().hour >= 6


def run_watcher(args: argparse.Namespace) -> int:
    command = [
        args.python,
        str(PROJECT_ROOT / "service_command_watcher.py"),
        "--interval-seconds",
        str(args.interval_seconds),
        "--primary-remote",
        args.primary_remote,
    ]
    append_log(args.log, f"[AHNS-SUPERVISOR] {timestamp()} 启动监听器。")

    try:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        append_log(args.log, f"[AHNS-SUPERVISOR] {timestamp()} 启动失败：{exc}")
        return 127

    assert process.stdout is not None
    with args.log.open("a", encoding="utf-8", newline="", buffering=1) as handle:
        for line in process.stdout:
            handle.write(line)

    exit_code = int(process.wait())
    append_log(
        args.log,
        f"[AHNS-SUPERVISOR] {timestamp()} 监听器已退出，退出码={exit_code}。",
    )
    return exit_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AHNS command watcher supervisor")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--primary-remote", default="gitee")
    parser.add_argument("--restart-delay-seconds", type=int, default=60)
    parser.add_argument("--max-restarts", type=int, default=999)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    last_exit_code = 0

    for attempt in range(args.max_restarts + 1):
        if not in_service_window():
            append_log(args.log, f"[AHNS-SUPERVISOR] {timestamp()} 当前不在 06:00-24:00，停止恢复。")
            return last_exit_code

        last_exit_code = run_watcher(args)
        if attempt >= args.max_restarts:
            break

        append_log(
            args.log,
            f"[AHNS-SUPERVISOR] {timestamp()} {args.restart_delay_seconds} 秒后进行第 {attempt + 1} 次恢复。",
        )
        time.sleep(args.restart_delay_seconds)

    return last_exit_code


if __name__ == "__main__":
    raise SystemExit(main())
