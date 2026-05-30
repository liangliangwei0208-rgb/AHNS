"""
Generic GitHub/Gitee repository synchronizer.

Copy this file into a local Git repository and run it from the repository root.
It reads the GitHub remote, derives the same-name Gitee repository, creates the
Gitee repository when needed, and then synchronizes local/GitHub/Gitee without
rewriting history.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


DEFAULT_BRANCH = "main"
DEFAULT_GITHUB_REMOTE = "origin"
DEFAULT_GITEE_REMOTE = "gitee"
GITEE_API_BASE = "https://gitee.com/api/v5"
GITEE_TOKEN_ENV = "GITEE_ACCESS_TOKEN"


class SyncError(RuntimeError):
    """Readable repository sync error."""


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RepoSlug:
    owner: str
    name: str


@dataclass(frozen=True)
class GiteeTarget:
    owner: str
    name: str
    ssh_url: str
    web_url: str


@dataclass(frozen=True)
class ApiResponse:
    status: int
    text: str
    data: Any


def log(message: str) -> None:
    print(f"[GITHUB-GITEE-SYNC] {message}", flush=True)


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


def git_output(repo: Path, args: Sequence[str], *, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise SyncError(
            f"{format_command(['git', *args])} failed with exit code "
            f"{result.returncode}: {message}"
        )
    return (result.stdout or "").strip()


def ensure_repo(repo: Path) -> Path:
    repo = repo.resolve()
    if not repo.exists() or not repo.is_dir():
        raise SyncError(f"Repository directory does not exist: {repo}")
    if not (repo / ".git").exists():
        raise SyncError(f"Not a Git repository: {repo}")
    return repo


def ensure_current_branch(repo: Path, branch: str) -> None:
    current = git_output(repo, ["branch", "--show-current"])
    if current != branch:
        raise SyncError(f"Current branch is {current!r}; expected {branch!r}.")


def ensure_clean_worktree(repo: Path) -> None:
    status = git_output(repo, ["status", "--porcelain"])
    if status:
        raise SyncError(
            "Worktree is not clean. Commit or stash local changes before syncing."
        )


def get_remote_url(repo: Path, remote: str) -> str | None:
    result = subprocess.run(
        ["git", "remote", "get-url", remote],
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def strip_dot_git(repo_name: str) -> str:
    repo_name = repo_name.strip().rstrip("/")
    if repo_name.endswith(".git"):
        return repo_name[:-4]
    return repo_name


def parse_remote_slug(remote_url: str, expected_host: str) -> RepoSlug | None:
    scp_match = re.match(
        rf"^(?:[^@]+@)?{re.escape(expected_host)}:(?P<owner>[^/]+)/(?P<repo>[^/]+?)/?$",
        remote_url,
    )
    if scp_match:
        return RepoSlug(
            owner=scp_match.group("owner"),
            name=strip_dot_git(scp_match.group("repo")),
        )

    parsed = urllib.parse.urlparse(remote_url)
    if parsed.hostname != expected_host:
        return None

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        return None
    return RepoSlug(owner=parts[0], name=strip_dot_git(parts[1]))


def parse_github_remote(remote_url: str) -> RepoSlug:
    slug = parse_remote_slug(remote_url, "github.com")
    if slug is None:
        raise SyncError(
            "GitHub remote must look like "
            "git@github.com:owner/repo.git or https://github.com/owner/repo.git"
        )
    return slug


def build_gitee_target(owner: str, repo_name: str) -> GiteeTarget:
    return GiteeTarget(
        owner=owner,
        name=repo_name,
        ssh_url=f"git@gitee.com:{owner}/{repo_name}.git",
        web_url=f"https://gitee.com/{owner}/{repo_name}",
    )


def remote_matches_target(existing_url: str, target: GiteeTarget) -> bool:
    if existing_url.strip() == target.ssh_url:
        return True
    slug = parse_remote_slug(existing_url, "gitee.com")
    return slug == RepoSlug(owner=target.owner, name=target.name)


def ensure_gitee_remote(
    *,
    repo: Path,
    remote: str,
    target: GiteeTarget,
    fix_remote: bool,
    dry_run: bool,
) -> None:
    existing_url = get_remote_url(repo, remote)
    if existing_url is None:
        step(f"Add Gitee remote {remote}: {target.ssh_url}")
        run_git(repo, ["remote", "add", remote, target.ssh_url], dry_run=dry_run)
        return

    if remote_matches_target(existing_url, target):
        log(f"Gitee remote {remote} already points to {existing_url}")
        return

    message = (
        f"Gitee remote {remote!r} points to {existing_url!r}, "
        f"expected {target.ssh_url!r}."
    )
    if not fix_remote:
        raise SyncError(message + " Re-run with --fix-remote to update it.")

    step(f"Update Gitee remote {remote}: {target.ssh_url}")
    run_git(repo, ["remote", "set-url", remote, target.ssh_url], dry_run=dry_run)


def api_quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def read_json_response(response: Any) -> ApiResponse:
    status_value = getattr(response, "status", None)
    if status_value is None:
        status_value = getattr(response, "code")
    status = int(status_value)
    raw = response.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        data = None
    return ApiResponse(status=status, text=raw, data=data)


def gitee_api_request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    form: dict[str, str] | None = None,
) -> ApiResponse:
    query: dict[str, str] = {}
    body = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "github-gitee-sync/1.0",
    }

    if method.upper() == "GET":
        if token:
            query["access_token"] = token
    else:
        form = dict(form or {})
        if token:
            form["access_token"] = token
        body = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    url = f"{GITEE_API_BASE}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return read_json_response(response)
    except urllib.error.HTTPError as exc:
        return read_json_response(exc)
    except urllib.error.URLError as exc:
        raise SyncError(f"Gitee API request failed: {exc}") from exc


def gitee_error_message(response: ApiResponse) -> str:
    if isinstance(response.data, dict):
        for key in ("message", "error", "error_description"):
            value = response.data.get(key)
            if value:
                return str(value)
    return response.text.strip() or f"HTTP {response.status}"


def gitee_repo_exists(target: GiteeTarget, token: str | None) -> bool:
    response = gitee_api_request(
        "GET",
        f"/repos/{api_quote(target.owner)}/{api_quote(target.name)}",
        token=token,
    )
    if response.status == 200:
        return True
    if response.status == 404:
        return False
    raise SyncError(
        f"Could not check Gitee repository {target.web_url}: "
        f"HTTP {response.status}: {gitee_error_message(response)}"
    )


def create_gitee_repo(target: GiteeTarget, *, token: str, private: bool) -> None:
    response = gitee_api_request(
        "POST",
        "/user/repos",
        token=token,
        form={
            "name": target.name,
            "private": str(private).lower(),
            "has_issues": "true",
            "has_wiki": "false",
            "can_comment": "true",
            "description": f"Mirror of GitHub repository {target.owner}/{target.name}",
        },
    )
    if response.status not in (200, 201):
        raise SyncError(
            f"Could not create Gitee repository {target.web_url}: "
            f"HTTP {response.status}: {gitee_error_message(response)}"
        )


def ensure_gitee_repo(
    *,
    target: GiteeTarget,
    token: str | None,
    private: bool,
    dry_run: bool,
) -> None:
    visibility = "private" if private else "public"
    if dry_run:
        step(f"Would check or create {visibility} Gitee repository: {target.web_url}")
        return

    step(f"Check Gitee repository: {target.web_url}")
    if gitee_repo_exists(target, token):
        log(f"Gitee repository exists: {target.web_url}")
        return

    if not token:
        raise SyncError(
            f"Gitee repository does not exist: {target.web_url}. "
            f"Set {GITEE_TOKEN_ENV} before running so the script can create it."
        )

    step(f"Create {visibility} Gitee repository: {target.web_url}")
    create_gitee_repo(target, token=token, private=private)
    if not gitee_repo_exists(target, token):
        raise SyncError(
            "Gitee repository was created, but the expected target path was not found. "
            "Check whether --gitee-owner matches your Gitee account namespace."
        )
    log(f"Gitee repository created: {target.web_url}")


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
    gitee_owner: str | None,
    private: bool,
    fix_remote: bool,
    dry_run: bool,
) -> None:
    repo = ensure_repo(repo)
    log(f"Repository: {repo}")
    log(f"Branch: {branch}")
    log(f"GitHub remote: {github_remote}")
    log(f"Gitee remote: {gitee_remote}")

    step("Check current branch and worktree")
    ensure_current_branch(repo, branch)
    ensure_clean_worktree(repo)

    step("Read GitHub remote")
    github_url = get_remote_url(repo, github_remote)
    if not github_url:
        raise SyncError(f"Missing GitHub remote: {github_remote}")
    github_slug = parse_github_remote(github_url)
    target = build_gitee_target(gitee_owner or github_slug.owner, github_slug.name)
    log(f"GitHub repository: {github_slug.owner}/{github_slug.name}")
    log(f"Gitee target: {target.owner}/{target.name}")

    token = os.environ.get(GITEE_TOKEN_ENV)
    ensure_gitee_repo(target=target, token=token, private=private, dry_run=dry_run)
    ensure_gitee_remote(
        repo=repo,
        remote=gitee_remote,
        target=target,
        fix_remote=fix_remote,
        dry_run=dry_run,
    )

    if dry_run:
        step("Dry run: print sync commands without changing repositories")
        run_git(repo, ["fetch", github_remote, branch], dry_run=True)
        run_git(repo, ["fetch", gitee_remote, branch], dry_run=True)
        run_git(repo, ["merge", "--no-edit", remote_ref(github_remote, branch)], dry_run=True)
        run_git(repo, ["merge", "--no-edit", remote_ref(gitee_remote, branch)], dry_run=True)
        run_git(repo, ["push", github_remote, f"HEAD:{branch}"], dry_run=True)
        run_git(repo, ["push", gitee_remote, f"HEAD:{branch}"], dry_run=True)
        log("Dry run complete.")
        return

    fetch_warnings: list[str] = []
    fetched_remotes: list[str] = []
    for remote in (github_remote, gitee_remote):
        step(f"Fetch {remote}/{branch}")
        result = run_git(repo, ["fetch", remote, branch], check=False)
        if result.returncode == 0:
            fetched_remotes.append(remote)
        else:
            fetch_warnings.append(f"fetch {remote}: exit code {result.returncode}")

    if not fetched_remotes:
        raise SyncError("Both GitHub and Gitee fetch operations failed.")

    try:
        for remote in fetched_remotes:
            step(f"Merge {remote}/{branch}")
            run_git(repo, ["merge", "--no-edit", remote_ref(remote, branch)])
    except SyncError:
        print(
            "\nMerge stopped. Resolve conflicts manually, then run:\n"
            "  git add <resolved files>\n"
            "  git commit\n"
            "  python github_gitee_sync.py\n",
            file=sys.stderr,
        )
        raise

    push_failures: list[str] = []
    for remote in (github_remote, gitee_remote):
        step(f"Push HEAD to {remote}/{branch}")
        result = run_git(repo, ["push", remote, f"HEAD:{branch}"], check=False)
        if result.returncode != 0:
            push_failures.append(f"push {remote}: exit code {result.returncode}")

    for remote in (github_remote, gitee_remote):
        step(f"Refresh {remote}/{branch}")
        result = run_git(repo, ["fetch", remote, branch], check=False)
        if result.returncode != 0:
            fetch_warnings.append(f"refresh {remote}: exit code {result.returncode}")

    print_final_refs(repo, branch, github_remote, gitee_remote)
    for warning in fetch_warnings:
        log(f"[WARN] {warning}")
    if push_failures:
        for failure in push_failures:
            log(f"[ERROR] {failure}")
        raise SyncError("At least one push failed. Re-run after fixing remote access.")
    log("Sync complete: local, GitHub, and Gitee are aligned.")


def public_key_text() -> str | None:
    pub_key = Path.home() / ".ssh" / "id_ed25519.pub"
    if not pub_key.exists():
        return None
    return pub_key.read_text(encoding="utf-8", errors="replace").strip()


def known_hosts_has_gitee() -> bool:
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    if not known_hosts.exists():
        return False
    text = known_hosts.read_text(encoding="utf-8", errors="replace")
    return "gitee.com" in text


def print_init_gitee(repo: Path, github_remote: str, gitee_owner: str | None) -> None:
    log("Gitee initialization checklist")
    key_text = public_key_text()
    if key_text:
        print("\nSSH public key to add to Gitee:")
        print(key_text)
    else:
        print("\nNo SSH public key found at ~/.ssh/id_ed25519.pub")

    print("\nKnown hosts:")
    if known_hosts_has_gitee():
        print("  gitee.com is already present in ~/.ssh/known_hosts")
    else:
        print("  gitee.com is not present in ~/.ssh/known_hosts yet")

    print("\nAccess token:")
    if os.environ.get(GITEE_TOKEN_ENV):
        print(f"  {GITEE_TOKEN_ENV} is set")
    else:
        print(f"  {GITEE_TOKEN_ENV} is not set")
        print("  Current PowerShell session:")
        print(f"    $env:{GITEE_TOKEN_ENV}='your-gitee-token'")
        print("  Persist for your Windows user:")
        print(
            "    [Environment]::SetEnvironmentVariable("
            f"'{GITEE_TOKEN_ENV}','your-gitee-token','User')"
        )

    try:
        repo = ensure_repo(repo)
        github_url = get_remote_url(repo, github_remote)
        if github_url:
            slug = parse_github_remote(github_url)
            target = build_gitee_target(gitee_owner or slug.owner, slug.name)
            print("\nDerived repository mapping:")
            print(f"  GitHub: {slug.owner}/{slug.name}")
            print(f"  Gitee : {target.owner}/{target.name}")
            print(f"  Remote: {target.ssh_url}")
    except SyncError as exc:
        print(f"\nRepository mapping skipped: {exc}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synchronize a local repository with same-name GitHub and Gitee remotes."
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
        "--gitee-owner",
        default=None,
        help="Gitee user or namespace. Defaults to the GitHub owner.",
    )
    parser.add_argument(
        "--private",
        action="store_true",
        help="Create the Gitee repository as private. Default is public.",
    )
    parser.add_argument(
        "--fix-remote",
        action="store_true",
        help="Update an existing mismatched Gitee remote URL.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions and git commands without changing repositories.",
    )
    parser.add_argument(
        "--init-gitee",
        action="store_true",
        help="Print local Gitee SSH/token initialization checklist and exit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        repo = Path(args.repo)
        if args.init_gitee:
            print_init_gitee(repo, str(args.github_remote), args.gitee_owner)
            return 0
        sync_repositories(
            repo=repo,
            branch=str(args.branch),
            github_remote=str(args.github_remote),
            gitee_remote=str(args.gitee_remote),
            gitee_owner=args.gitee_owner,
            private=bool(args.private),
            fix_remote=bool(args.fix_remote),
            dry_run=bool(args.dry_run),
        )
    except SyncError as exc:
        log(f"[ERROR] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
