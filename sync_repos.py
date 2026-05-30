"""
Synchronize the local AHNS repository with GitHub and Gitee.

This script is intended to be run on the main development computer after
editing code. It keeps the local branch, GitHub remote, and Gitee remote on the
same branch without rewriting history.1234
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


DEFAULT_BRANCH = "main"
DEFAULT_GITHUB_REMOTE = "origin"
DEFAULT_GITEE_REMOTE = "gitee"


class SyncError(RuntimeError):
    """Readable repository sync error."""


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str


def log(message: str) -> None:
    print(f"[SYNC-REPOS] {message}", flush=True)


def step(message: str) -> None:
    log(f"==> {message}")


def format_command(args: Sequence[str]) -> str:
    return " ".join(args)


def run_git(
    repo: Path,
    args: Sequence[str],
    *,
    check: bool = True,
    dry_run: bool = False,
) -> GitResult:
    full_args = ["git", *args]
    log(format_command(full_args))
    if dry_run:
        return GitResult(0, "", "")

    result = subprocess.run(
        full_args,
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    if check and result.returncode != 0:
        raise SyncError(
            f"{format_command(full_args)} failed with exit code {result.returncode}"
        )
    return GitResult(result.returncode, result.stdout or "", result.stderr or "")


def git_output(repo: Path, args: Sequence[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise SyncError(
            f"{format_command(['git', *args])} failed with exit code "
            f"{result.returncode}: {message}"
        )
    return (result.stdout or "").strip()


def ensure_repo(repo: Path) -> Path:
    repo = repo.resolve()
    if not repo.exists() or not repo.is_dir():
        raise SyncError(f"找不到仓库目录: {repo}")
    git_dir = repo / ".git"
    if not git_dir.exists():
        raise SyncError(f"这不是 Git 仓库: {repo}")
    return repo


def ensure_current_branch(repo: Path, branch: str) -> None:
    current = git_output(repo, ["branch", "--show-current"])
    if current != branch:
        raise SyncError(f"当前分支是 {current!r}，期望分支是 {branch!r}。")


def ensure_clean_worktree(repo: Path) -> None:
    status = git_output(repo, ["status", "--porcelain"])
    if status:
        raise SyncError(
            "工作区不干净。请先提交或暂存本地改动，再运行同步脚本。"
        )


def ensure_remote_exists(repo: Path, remote: str) -> None:
    run_git(repo, ["remote", "get-url", remote])


def remote_ref(remote: str, branch: str) -> str:
    return f"{remote}/{branch}"


def print_final_refs(repo: Path, branch: str, github_remote: str, gitee_remote: str) -> None:
    refs = [
        ("local", "HEAD"),
        (github_remote, remote_ref(github_remote, branch)),
        (gitee_remote, remote_ref(gitee_remote, branch)),
    ]
    log("最终提交位置:")
    for label, ref in refs:
        try:
            commit = git_output(repo, ["rev-parse", "--short=12", ref])
        except SyncError as exc:
            commit = f"不可用 ({exc})"
        print(f"  {label}: {commit}")


def sync_repositories(
    *,
    repo: Path,
    branch: str,
    github_remote: str,
    gitee_remote: str,
    dry_run: bool,
) -> None:
    repo = ensure_repo(repo)
    log(f"仓库目录: {repo}")
    log(f"同步分支: {branch}")
    log(f"GitHub 远程名: {github_remote}")
    log(f"Gitee 远程名: {gitee_remote}")

    step("检查当前分支和工作区状态")
    ensure_current_branch(repo, branch)
    ensure_clean_worktree(repo)
    step("检查 GitHub/Gitee 远程地址")
    ensure_remote_exists(repo, github_remote)
    ensure_remote_exists(repo, gitee_remote)

    if dry_run:
        step("预演模式：只打印将要执行的同步命令，不修改仓库")
        run_git(repo, ["fetch", github_remote, branch], dry_run=True)
        run_git(repo, ["fetch", gitee_remote, branch], dry_run=True)
        run_git(repo, ["merge", "--no-edit", remote_ref(github_remote, branch)], dry_run=True)
        run_git(repo, ["merge", "--no-edit", remote_ref(gitee_remote, branch)], dry_run=True)
        run_git(repo, ["push", github_remote, f"HEAD:{branch}"], dry_run=True)
        run_git(repo, ["push", gitee_remote, f"HEAD:{branch}"], dry_run=True)
        log("预演完成，没有修改任何仓库内容。")
        return

    remote_failures: list[str] = []
    fetched_remotes: list[str] = []
    for remote in (github_remote, gitee_remote):
        label = "GitHub" if remote == github_remote else "Gitee"
        step(f"拉取 {label} 最新代码")
        result = run_git(repo, ["fetch", remote, branch], check=False)
        if result.returncode == 0:
            fetched_remotes.append(remote)
        else:
            remote_failures.append(f"fetch {remote}: exit code {result.returncode}")

    if not fetched_remotes:
        raise SyncError("GitHub 和 Gitee 都拉取失败。" + "; ".join(remote_failures))

    try:
        for remote in fetched_remotes:
            label = "GitHub" if remote == github_remote else "Gitee"
            step(f"合并 {label} 最新提交到本地")
            run_git(repo, ["merge", "--no-edit", remote_ref(remote, branch)])
    except SyncError:
        print(
            "\n合并已停止。请手动解决冲突，然后执行：\n"
            "  git add <resolved files>\n"
            "  git commit\n"
            "  python sync_repos.py\n",
            file=sys.stderr,
        )
        raise

    for remote in (github_remote, gitee_remote):
        label = "GitHub" if remote == github_remote else "Gitee"
        step(f"推送同步结果到 {label}")
        result = run_git(repo, ["push", remote, f"HEAD:{branch}"], check=False)
        if result.returncode != 0:
            remote_failures.append(f"push {remote}: exit code {result.returncode}")

    for remote in (github_remote, gitee_remote):
        label = "GitHub" if remote == github_remote else "Gitee"
        step(f"刷新 {label} 远程提交位置")
        run_git(repo, ["fetch", remote, branch], check=False)

    print_final_refs(repo, branch, github_remote, gitee_remote)
    if remote_failures:
        log("仓库同步部分完成：至少一个远程操作失败。")
        for failure in remote_failures:
            log(f"[WARN] {failure}")
        raise SyncError("存在远程操作失败，请稍后重新运行同步脚本。")
    log("仓库同步完成：本地、GitHub、Gitee 已对齐。")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synchronize local AHNS main branch with GitHub and Gitee."
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Repository directory. Defaults to the current directory.",
    )
    parser.add_argument(
        "--branch",
        default=DEFAULT_BRANCH,
        help=f"Branch to synchronize. Defaults to {DEFAULT_BRANCH}.",
    )
    parser.add_argument(
        "--github-remote",
        default=DEFAULT_GITHUB_REMOTE,
        help=f"GitHub remote name. Defaults to {DEFAULT_GITHUB_REMOTE}.",
    )
    parser.add_argument(
        "--gitee-remote",
        default=DEFAULT_GITEE_REMOTE,
        help=f"Gitee remote name. Defaults to {DEFAULT_GITEE_REMOTE}.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the git commands that would run without changing the repository.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        sync_repositories(
            repo=Path(args.repo),
            branch=str(args.branch),
            github_remote=str(args.github_remote),
            gitee_remote=str(args.gitee_remote),
            dry_run=bool(args.dry_run),
        )
    except SyncError as exc:
        log(f"[ERROR] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
