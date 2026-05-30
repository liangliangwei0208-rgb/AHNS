"""
service_runner.py

小电脑服务器的一次性控制入口：
1. 同步远程仓库；
2. 运行 service_main.py；
3. 提交源码/配置/缓存变化；
4. 再同步并推送。

未来接入邮件、HTTP/Webhook 或其他消息触发器时，直接调用
run_service_once(...) 即可，不需要重复实现 Git 和运行流程。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SERVICE_PYTHON = r"D:\anaconda\envs\py310\python.exe"
DEFAULT_PRIMARY_REMOTE = os.environ.get("AHNS_SERVICE_PRIMARY_REMOTE", "gitee")
DEFAULT_FALLBACK_REMOTE = os.environ.get("AHNS_SERVICE_FALLBACK_REMOTE", "origin").strip() or None
LOCAL_CHANGE_COMMIT_MESSAGE = "Update service local changes [skip ci]"
RUNTIME_CHANGE_COMMIT_MESSAGE = "Update service runtime changes [skip ci]"
SERVICE_GIT_USER_NAME = "ahns-service[bot]"
SERVICE_GIT_USER_EMAIL = "ahns-service@local"


class ServiceRunnerError(RuntimeError):
    """服务端 runner 的可读错误。"""


@dataclass
class ServiceRunResult:
    service_exit_code: int
    git_commits_created: bool


def log(message: str) -> None:
    print(f"[AHNS-SERVICE] {message}", flush=True)


def format_command(args: Sequence[str | os.PathLike[str]]) -> str:
    return " ".join(str(arg) for arg in args)


def run_command(
    args: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path = PROJECT_ROOT,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    log(format_command(args))
    result = subprocess.run(
        [str(arg) for arg in args],
        cwd=str(cwd),
        env=env,
        check=False,
    )
    if check and result.returncode != 0:
        raise ServiceRunnerError(
            f"{format_command(args)} failed with exit code {result.returncode}"
        )
    return result


def git_output(
    args: Sequence[str],
    *,
    check: bool = True,
    allow_exit_codes: set[int] | None = None,
) -> tuple[int, str]:
    full_args = ["git", *args]
    result = subprocess.run(
        full_args,
        cwd=str(PROJECT_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    allowed = allow_exit_codes or set()
    if check and result.returncode != 0 and result.returncode not in allowed:
        message = (result.stderr or result.stdout or "").strip()
        raise ServiceRunnerError(
            f"{format_command(full_args)} failed with exit code {result.returncode}: {message}"
        )
    return result.returncode, (result.stdout or "").strip()


def run_git(args: Sequence[str]) -> None:
    run_command(["git", *args])


def get_git_config_value(key: str) -> str:
    code, output = git_output(["config", "--get", key], allow_exit_codes={1})
    if code == 1:
        return ""
    return output.strip()


def ensure_git_identity() -> None:
    if not get_git_config_value("user.name"):
        run_git(["config", "user.name", SERVICE_GIT_USER_NAME])
    if not get_git_config_value("user.email"):
        run_git(["config", "user.email", SERVICE_GIT_USER_EMAIL])


def current_branch() -> str:
    _, branch = git_output(["branch", "--show-current"])
    branch = branch.strip()
    if not branch:
        raise ServiceRunnerError("Current HEAD is detached; service runner requires a branch.")
    return branch


def remote_candidates(primary_remote: str, fallback_remote: str | None = None) -> list[str]:
    candidates: list[str] = []
    for remote in (primary_remote, fallback_remote):
        remote = str(remote or "").strip()
        if remote and remote not in candidates:
            candidates.append(remote)
    available: list[str] = []
    unavailable: list[str] = []
    for remote in candidates:
        code, _ = git_output(["remote", "get-url", remote], check=False)
        if code == 0:
            available.append(remote)
        else:
            unavailable.append(remote)
    if unavailable:
        log("Configured git remote is not available locally: " + ", ".join(unavailable))
    if not available:
        raise ServiceRunnerError("At least one configured git remote must exist locally.")
    return available


def remote_url(remote: str) -> str:
    remote = str(remote or "").strip()
    if not remote:
        raise ServiceRunnerError("Git remote name is empty.")
    _, url = git_output(["remote", "get-url", remote])
    url = url.strip()
    if not url:
        raise ServiceRunnerError(f"Git remote '{remote}' is not configured.")
    return url


def origin_url() -> str:
    return remote_url("origin")


def pull_with_fallback(branch: str, remotes: Sequence[str]) -> str:
    failures: list[str] = []
    for remote in remotes:
        log(f"Pull from {remote}/{branch}.")
        result = run_command(["git", "pull", "--rebase", remote, branch], check=False)
        if result.returncode == 0:
            return remote
        failures.append(f"{remote}: exit code {result.returncode}")
    raise ServiceRunnerError(
        "git pull failed for all configured remotes: " + "; ".join(failures)
    )


def staged_files() -> list[str]:
    _, output = git_output(["diff", "--cached", "--name-only"])
    return [line.strip() for line in output.splitlines() if line.strip()]


def is_blocked_path(path: str) -> bool:
    normalized = path.replace("\\", "/").strip()
    lower = normalized.lower()
    return (
        lower == ".env"
        or lower.startswith(".env.")
        or lower == "tools/email_local_config.py"
        or lower == "sent_email_log.json"
        or lower.startswith("output/")
        or lower.startswith("logs/")
        or lower.startswith("__pycache__/")
        or "/__pycache__/" in lower
        or lower.startswith(".vscode/")
        or lower.startswith(".idea/")
        or lower.endswith(".log")
        or lower.endswith(".pyc")
        or lower.endswith(".pyo")
        or lower.endswith(".pyd")
    )


def assert_staged_files_are_safe(files: Iterable[str]) -> None:
    blocked = [path for path in files if is_blocked_path(path)]
    if blocked:
        run_git(["reset", "--quiet"])
        raise ServiceRunnerError(
            "Refusing to commit sensitive or ignored-style paths: " + ", ".join(blocked)
        )


def save_repo_changes(message: str) -> bool:
    run_git(["add", "-A"])
    files = staged_files()
    if not files:
        log("No repository changes to commit.")
        return False

    assert_staged_files_are_safe(files)
    ensure_git_identity()
    run_git(["commit", "-m", message])
    return True


def push_with_retry(branch: str, remote: str = "origin") -> None:
    for attempt in range(1, 3):
        log(f"Push attempt {attempt} to {remote}.")
        result = run_command(["git", "push", remote, f"HEAD:{branch}"], check=False)
        if result.returncode == 0:
            log("Runtime changes pushed.")
            return

        log(f"Push failed on attempt {attempt}; rebasing before retry.")
        run_git(["pull", "--rebase", remote, branch])

    raise ServiceRunnerError("git push failed after retry")


def build_service_args(
    *,
    python_exe: str,
    no_send: bool,
    receiver: str | None,
) -> list[str]:
    args = [python_exe, str(PROJECT_ROOT / "service_main.py")]
    if no_send:
        args.append("--no-send")
    if receiver:
        args.extend(["--receiver", receiver])
    return args


def service_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run_service_once(
    *,
    python_exe: str = DEFAULT_SERVICE_PYTHON,
    no_send: bool = False,
    receiver: str | None = None,
    skip_git: bool = False,
    primary_remote: str = DEFAULT_PRIMARY_REMOTE,
    fallback_remote: str | None = DEFAULT_FALLBACK_REMOTE,
) -> ServiceRunResult:
    python_path = Path(python_exe)
    if not python_path.exists() or not python_path.is_file():
        raise ServiceRunnerError(f"Python executable not found: {python_exe}")

    log(f"Repository root: {PROJECT_ROOT}")
    commits_created = False

    if not skip_git:
        remotes = remote_candidates(primary_remote, fallback_remote)
        run_git(["--version"])
        branch = current_branch()
        log(f"Current branch: {branch}")
        for remote in remotes:
            log(f"Remote {remote}: {remote_url(remote)}")

        commits_created = save_repo_changes(LOCAL_CHANGE_COMMIT_MESSAGE) or commits_created
        active_remote = pull_with_fallback(branch, remotes)
    else:
        branch = ""
        active_remote = ""
        log("Git synchronization is skipped for this run.")

    service_args = build_service_args(
        python_exe=str(python_path),
        no_send=no_send,
        receiver=receiver,
    )
    log(f"Running service_main.py with {python_path}")
    service_result = run_command(service_args, check=False, env=service_env())
    service_exit_code = service_result.returncode
    log(f"service_main.py exited with code {service_exit_code}")

    if not skip_git:
        commits_created = save_repo_changes(RUNTIME_CHANGE_COMMIT_MESSAGE) or commits_created

        if commits_created:
            run_git(["pull", "--rebase", active_remote, branch])
            push_with_retry(branch, active_remote)
        else:
            log("No local commits were created; push skipped.")

    return ServiceRunResult(
        service_exit_code=service_exit_code,
        git_commits_created=commits_created,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步仓库并运行 AHNS 小电脑服务流程")
    parser.add_argument(
        "--python-exe",
        default=DEFAULT_SERVICE_PYTHON,
        help=f"运行 service_main.py 的 Python 解释器，默认 {DEFAULT_SERVICE_PYTHON}",
    )
    parser.add_argument(
        "--no-send",
        action="store_true",
        help="透传给 service_main.py：只运行流程，不发送邮件",
    )
    parser.add_argument(
        "--receiver",
        default=None,
        help="透传给 service_main.py：临时指定收件邮箱",
    )
    parser.add_argument(
        "--skip-git",
        action="store_true",
        help="跳过 pull/commit/push，仅运行 service_main.py，便于本地调试",
    )
    parser.add_argument(
        "--primary-remote",
        default=DEFAULT_PRIMARY_REMOTE,
        help=f"优先同步的 Git remote，默认 {DEFAULT_PRIMARY_REMOTE}",
    )
    parser.add_argument(
        "--fallback-remote",
        default=DEFAULT_FALLBACK_REMOTE,
        help="主 remote 不可用时兜底同步的 Git remote，例如 origin",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_service_once(
            python_exe=args.python_exe,
            no_send=bool(args.no_send),
            receiver=args.receiver,
            skip_git=bool(args.skip_git),
            primary_remote=args.primary_remote,
            fallback_remote=args.fallback_remote,
        )
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return 1
    return int(result.service_exit_code)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
