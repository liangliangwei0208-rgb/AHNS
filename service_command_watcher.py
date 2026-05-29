"""
service_command_watcher.py

Watch a tracked JSON command file from GitHub and trigger the small-server
service runner when run_flag is set to 1.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import service_runner


PROJECT_ROOT = service_runner.PROJECT_ROOT
DEFAULT_COMMAND_FILE = PROJECT_ROOT / "service_command.json"
DEFAULT_INTERVAL_SECONDS = 60
MAX_BACKOFF_SECONDS = 300
COMMAND_STATUS_COMMIT_MESSAGE = "Update service command status [skip ci]"
BJ_TZ = timezone(timedelta(hours=8))


class CommandWatcherError(RuntimeError):
    """Command watcher readable error."""


@dataclass
class WatchState:
    consecutive_failures: int = 0
    current_sleep_seconds: int = DEFAULT_INTERVAL_SECONDS


def log(message: str) -> None:
    print(f"[AHNS-COMMAND] {message}", flush=True)


def now_bj() -> datetime:
    return datetime.now(tz=BJ_TZ)


def iso_bj(value: datetime) -> str:
    return value.astimezone(BJ_TZ).isoformat(timespec="seconds")


def normalize_command_file(path_text: str | Path) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()
    try:
        path.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise CommandWatcherError(f"Command file must be inside repository: {path}") from exc
    return path


def load_command(command_file: Path) -> dict[str, Any]:
    try:
        with command_file.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
    except FileNotFoundError as exc:
        raise CommandWatcherError(f"Command file not found: {command_file}") from exc
    except json.JSONDecodeError as exc:
        raise CommandWatcherError(f"Command file is not valid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise CommandWatcherError("Command file root must be a JSON object.")
    return loaded


def write_command(command_file: Path, command: dict[str, Any]) -> None:
    command_file.write_text(
        json.dumps(command, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on"}


def command_requests_run(command: dict[str, Any]) -> bool:
    return truthy_flag(command.get("run_flag"))


def command_no_send(command: dict[str, Any]) -> bool:
    return truthy_flag(command.get("no_send"))


def command_receiver(command: dict[str, Any]) -> str | None:
    receiver = str(command.get("receiver") or "").strip()
    return receiver or None


def build_completed_command(
    command: dict[str, Any],
    *,
    started_at: datetime,
    finished_at: datetime,
    exit_code: int,
    message: str,
) -> dict[str, Any]:
    status = "success" if int(exit_code) == 0 else "failed"
    updated = dict(command)
    updated["run_flag"] = 0
    updated["status"] = status
    updated["last_started_at_bj"] = iso_bj(started_at)
    updated["last_finished_at_bj"] = iso_bj(finished_at)
    updated["last_exit_code"] = int(exit_code)
    updated["last_message"] = str(message or "").strip()[:500]
    return updated


def command_file_git_path(command_file: Path) -> str:
    try:
        return command_file.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError as exc:
        raise CommandWatcherError(f"Command file must be inside repository: {command_file}") from exc


def commit_and_push_command_status(command_file: Path, branch: str) -> bool:
    git_path = command_file_git_path(command_file)
    service_runner.run_git(["add", "--", git_path])
    files = service_runner.staged_files()
    if not files:
        log("Command status did not change; commit skipped.")
        return False

    unexpected = [path for path in files if path.replace("\\", "/") != git_path]
    if unexpected:
        raise CommandWatcherError(
            "Refusing to commit files outside command status update: " + ", ".join(unexpected)
        )
    service_runner.assert_staged_files_are_safe(files)
    service_runner.ensure_git_identity()
    service_runner.run_git(["commit", "-m", COMMAND_STATUS_COMMIT_MESSAGE])
    service_runner.push_with_retry(branch)
    return True


def pull_latest(branch: str) -> None:
    service_runner.run_git(["pull", "--rebase", "origin", branch])


def prepare_git() -> str:
    service_runner.run_git(["--version"])
    branch = service_runner.current_branch()
    remote = service_runner.origin_url()
    log(f"Current branch: {branch}")
    log(f"Origin: {remote}")
    return branch


def run_requested_command(
    command: dict[str, Any],
    *,
    command_file: Path,
    branch: str,
    python_exe: str,
) -> int:
    no_send = command_no_send(command)
    receiver = command_receiver(command)
    started_at = now_bj()
    log(
        "Run command detected: "
        f"no_send={no_send}, receiver={receiver or '(default)'}"
    )

    try:
        result = service_runner.run_service_once(
            python_exe=python_exe,
            no_send=no_send,
            receiver=receiver,
            skip_git=False,
        )
        exit_code = int(result.service_exit_code)
        message = f"service_main.py exit code {exit_code}"
    except Exception as exc:
        exit_code = 1
        message = f"service_runner error: {exc}"
        log(f"[ERROR] {message}")

    finished_at = now_bj()
    updated = build_completed_command(
        command,
        started_at=started_at,
        finished_at=finished_at,
        exit_code=exit_code,
        message=message,
    )
    write_command(command_file, updated)
    log("Command flag reset to 0; committing status update.")
    commit_and_push_command_status(command_file, branch)
    return exit_code


def check_once(
    *,
    command_file: Path,
    branch: str,
    python_exe: str,
) -> int:
    pull_latest(branch)
    try:
        command = load_command(command_file)
    except CommandWatcherError as exc:
        log(f"[ERROR] {exc}")
        return 1

    if not command_requests_run(command):
        status = str(command.get("status") or "idle").strip() or "idle"
        log(f"No run command. status={status}, run_flag={command.get('run_flag', 0)}")
        return 0

    return run_requested_command(
        command,
        command_file=command_file,
        branch=branch,
        python_exe=python_exe,
    )


def is_likely_network_or_git_sync_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = [
        "git pull",
        "git push",
        "unable to access",
        "failed to connect",
        "could not connect",
        "connection timed out",
        "timed out",
        "could not resolve host",
        "recv failure",
        "schannel",
        "gnutls",
        "tls",
        "ssl",
        "http",
        "https",
        "port 443",
    ]
    return any(marker in text for marker in markers)


def next_backoff_seconds(base_interval_seconds: int, consecutive_failures: int) -> int:
    base = max(5, int(base_interval_seconds or DEFAULT_INTERVAL_SECONDS))
    if consecutive_failures <= 0:
        return base
    return min(MAX_BACKOFF_SECONDS, base * (2 ** min(consecutive_failures - 1, 4)))


def record_success(state: WatchState, *, base_interval_seconds: int) -> None:
    if state.consecutive_failures:
        log("GitHub sync recovered; polling interval reset.")
    state.consecutive_failures = 0
    state.current_sleep_seconds = max(5, int(base_interval_seconds or DEFAULT_INTERVAL_SECONDS))


def record_failure(
    state: WatchState,
    exc: Exception,
    *,
    base_interval_seconds: int,
) -> None:
    state.consecutive_failures += 1
    state.current_sleep_seconds = next_backoff_seconds(base_interval_seconds, state.consecutive_failures)
    if is_likely_network_or_git_sync_error(exc):
        log(
            "[WARN] GitHub sync failed; will retry after "
            f"{state.current_sleep_seconds}s "
            f"(consecutive failures: {state.consecutive_failures}). Reason: {exc}"
        )
    else:
        log(
            "[ERROR] Watch iteration failed; will retry after "
            f"{state.current_sleep_seconds}s "
            f"(consecutive failures: {state.consecutive_failures}). Reason: {exc}"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="监听 GitHub command 文件并触发 AHNS 小电脑服务流程")
    parser.add_argument(
        "--python-exe",
        default=service_runner.DEFAULT_SERVICE_PYTHON,
        help=f"运行 service_main.py 的 Python 解释器，默认 {service_runner.DEFAULT_SERVICE_PYTHON}",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"轮询间隔秒数，默认 {DEFAULT_INTERVAL_SECONDS}",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只检查一次 command 文件后退出，便于调试或任务计划程序调用",
    )
    parser.add_argument(
        "--command-file",
        default=str(DEFAULT_COMMAND_FILE),
        help=f"command JSON 文件路径，默认 {DEFAULT_COMMAND_FILE}",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        command_file = normalize_command_file(args.command_file)
        interval_seconds = max(5, int(args.interval_seconds or DEFAULT_INTERVAL_SECONDS))
        branch = prepare_git()
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1

    log(f"Command file: {command_file}")
    log(f"Poll interval: {interval_seconds}s")
    state = WatchState(current_sleep_seconds=interval_seconds)

    while True:
        try:
            exit_code = check_once(
                command_file=command_file,
                branch=branch,
                python_exe=str(args.python_exe),
            )
            record_success(state, base_interval_seconds=interval_seconds)
        except Exception as exc:
            record_failure(state, exc, base_interval_seconds=interval_seconds)
            exit_code = 1

        if args.once:
            return int(exit_code)

        time.sleep(state.current_sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
