"""
Synchronize the local AHNS repository with GitHub and Gitee.

This script is intended to be run on the main development computer after
editing code. It keeps the local branch, GitHub remote, and Gitee remote on the
same branch without rewriting history.
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
        raise SyncError(f"Repository directory not found: {repo}")
    git_dir = repo / ".git"
    if not git_dir.exists():
        raise SyncError(f"Not a git repository: {repo}")
    return repo


def ensure_current_branch(repo: Path, branch: str) -> None:
    current = git_output(repo, ["branch", "--show-current"])
    if current != branch:
        raise SyncError(f"Current branch is {current!r}; expected {branch!r}.")


def ensure_clean_worktree(repo: Path) -> None:
    status = git_output(repo, ["status", "--porcelain"])
    if status:
        raise SyncError(
            "Working tree is not clean. Commit or stash local changes before syncing."
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
    log("Final refs:")
    for label, ref in refs:
        try:
            commit = git_output(repo, ["rev-parse", "--short=12", ref])
        except SyncError as exc:
            commit = f"unavailable ({exc})"
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
    log(f"Repository: {repo}")
    log(f"Branch: {branch}")
    log(f"GitHub remote: {github_remote}")
    log(f"Gitee remote: {gitee_remote}")

    ensure_current_branch(repo, branch)
    ensure_clean_worktree(repo)
    ensure_remote_exists(repo, github_remote)
    ensure_remote_exists(repo, gitee_remote)

    if dry_run:
        run_git(repo, ["fetch", github_remote, branch], dry_run=True)
        run_git(repo, ["fetch", gitee_remote, branch], dry_run=True)
        run_git(repo, ["merge", "--no-edit", remote_ref(github_remote, branch)], dry_run=True)
        run_git(repo, ["merge", "--no-edit", remote_ref(gitee_remote, branch)], dry_run=True)
        run_git(repo, ["push", github_remote, f"HEAD:{branch}"], dry_run=True)
        run_git(repo, ["push", gitee_remote, f"HEAD:{branch}"], dry_run=True)
        log("Dry run complete. No repository changes were made.")
        return

    remote_failures: list[str] = []
    fetched_remotes: list[str] = []
    for remote in (github_remote, gitee_remote):
        result = run_git(repo, ["fetch", remote, branch], check=False)
        if result.returncode == 0:
            fetched_remotes.append(remote)
        else:
            remote_failures.append(f"fetch {remote}: exit code {result.returncode}")

    if not fetched_remotes:
        raise SyncError("Could not fetch any remote. " + "; ".join(remote_failures))

    try:
        for remote in fetched_remotes:
            run_git(repo, ["merge", "--no-edit", remote_ref(remote, branch)])
    except SyncError:
        print(
            "\nMerge stopped. Resolve conflicts manually, then run:\n"
            "  git add <resolved files>\n"
            "  git commit\n"
            "  python sync_repos.py\n",
            file=sys.stderr,
        )
        raise

    for remote in (github_remote, gitee_remote):
        result = run_git(repo, ["push", remote, f"HEAD:{branch}"], check=False)
        if result.returncode != 0:
            remote_failures.append(f"push {remote}: exit code {result.returncode}")

    for remote in (github_remote, gitee_remote):
        run_git(repo, ["fetch", remote, branch], check=False)

    print_final_refs(repo, branch, github_remote, gitee_remote)
    if remote_failures:
        log("Repository sync partially complete.")
        for failure in remote_failures:
            log(f"[WARN] {failure}")
        raise SyncError("One or more remote operations failed.")
    log("Repository sync complete.")


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
