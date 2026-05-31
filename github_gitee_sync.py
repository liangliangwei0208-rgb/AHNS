"""
GitHub/Gitee 三方同步脚本。

使用方式：把这个文件放在本地 Git 仓库根目录运行。脚本会读取 GitHub
远程仓库，推导对应的 Gitee 仓库，必要时创建远程仓库。直接运行会
显示菜单，可选择只创建/补齐 GitHub 公开仓库，或把本地、GitHub、
Gitee 三端同步到同一个分支提交。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time


def prepend_conda_dll_paths() -> None:
    """直接运行 conda 环境 python.exe 时，补上激活环境本应设置的 DLL 路径。"""
    prefix = getattr(sys, "prefix", "")
    if not prefix or not (os.path.isdir(os.path.join(prefix, "conda-meta"))):
        return

    paths = [
        os.path.join(prefix, "Library", "bin"),
        os.path.join(prefix, "DLLs"),
        os.path.join(prefix, "Scripts"),
    ]
    current_path = os.environ.get("PATH", "")
    current_parts = [part.lower() for part in current_path.split(os.pathsep)]
    missing_paths = [
        path for path in paths if os.path.isdir(path) and path.lower() not in current_parts
    ]
    if missing_paths:
        os.environ["PATH"] = os.pathsep.join([*missing_paths, current_path])


# 必须在 urllib.request 导入前补 DLL 路径，否则 Python 可能没有 HTTPS 支持。
prepend_conda_dll_paths()

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


DEFAULT_BRANCH = None
DEFAULT_GITHUB_REMOTE = "origin"
DEFAULT_GITEE_REMOTE = "gitee"
DEFAULT_GITHUB_OWNER = "liangliangwei0208-rgb"
MODE_ENSURE_GITHUB = "ensure-github"
MODE_SYNC = "sync"
MODE_EXIT = "exit"
GITHUB_API_BASE = "https://api.github.com"
GITHUB_TOKEN_ENV_NAMES = ("GITHUB_TOKEN", "GH_TOKEN")
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
class GitHubTarget:
    owner: str
    name: str
    ssh_url: str
    web_url: str


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
    # 所有 git 命令都从这里走，方便 dry-run 时只打印命令、不真正修改远程。
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


def local_folder_repo_name(repo: Path) -> str:
    # GitHub 自动建库时，默认严格使用当前文件夹名，避免误用远端历史名称。
    return repo.resolve().name


def ensure_current_branch(repo: Path, branch: str) -> None:
    current = git_output(repo, ["branch", "--show-current"])
    if current != branch:
        raise SyncError(f"Current branch is {current!r}; expected {branch!r}.")


def current_branch(repo: Path) -> str:
    branch = git_output(repo, ["branch", "--show-current"])
    if not branch:
        raise SyncError("Could not detect current branch. Check whether HEAD is detached.")
    return branch


def ensure_clean_worktree(repo: Path) -> None:
    # 同步前强制要求工作区干净：脚本只负责同步提交，不负责帮你自动提交代码。
    status = git_output(repo, ["status", "--porcelain"])
    if status:
        raise SyncError(
            "Worktree is not clean. Commit or stash local changes before syncing."
        )


def ensure_has_commit(repo: Path) -> None:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SyncError(
            "This repository has no commits yet. Create an initial commit before syncing."
        )


def is_shallow_repository(repo: Path) -> bool:
    return git_output(repo, ["rev-parse", "--is-shallow-repository"]) == "true"


def git_dir(repo: Path) -> Path:
    path = Path(git_output(repo, ["rev-parse", "--git-dir"]))
    if not path.is_absolute():
        path = repo / path
    return path.resolve()


def is_commit_reachable_from_refs(repo: Path, commit: str) -> bool:
    result = subprocess.run(
        ["git", "name-rev", "--name-only", "--no-undefined", commit],
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def prune_stale_shallow_markers(repo: Path, *, dry_run: bool) -> None:
    shallow_file = git_dir(repo) / "shallow"
    if not shallow_file.exists():
        return

    markers = [
        line.strip()
        for line in shallow_file.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    stale_markers = [
        marker for marker in markers if not is_commit_reachable_from_refs(repo, marker)
    ]
    if not stale_markers:
        return

    stale_marker_set = set(stale_markers)
    kept_markers = [marker for marker in markers if marker not in stale_marker_set]
    if kept_markers:
        step(f"Remove stale shallow marker entries: {len(stale_markers)}")
        if not dry_run:
            shallow_file.write_text("\n".join(kept_markers) + "\n", encoding="utf-8")
        return

    step("Remove stale shallow marker file")
    if not dry_run:
        shallow_file.unlink()


def ensure_complete_history(repo: Path, remote: str, *, dry_run: bool) -> None:
    prune_stale_shallow_markers(repo, dry_run=dry_run)
    if not is_shallow_repository(repo):
        return

    step(f"Complete shallow Git history from {remote}")
    run_git(repo, ["fetch", "--unshallow", remote], dry_run=dry_run)
    prune_stale_shallow_markers(repo, dry_run=dry_run)
    if not dry_run and is_shallow_repository(repo):
        raise SyncError(
            "Repository is still shallow after fetching full history. "
            "Check .git/shallow before pushing to mirrors."
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
    # 同时支持 SSH 地址 git@host:owner/repo.git 和 HTTPS 地址 https://host/owner/repo.git。
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


def parse_github_slug(remote_url: str) -> RepoSlug | None:
    # GitHub 在部分网络环境下需要走 443 端口，地址会变成 ssh.github.com。
    slug = parse_remote_slug(remote_url, "github.com")
    if slug is not None:
        return slug

    parsed = urllib.parse.urlparse(remote_url)
    if parsed.scheme != "ssh" or parsed.hostname != "ssh.github.com":
        return None
    if parsed.port not in (None, 443):
        return None

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        return None
    return RepoSlug(owner=parts[0], name=strip_dot_git(parts[1]))


def parse_github_remote(remote_url: str) -> RepoSlug:
    slug = parse_github_slug(remote_url)
    if slug is None:
        raise SyncError(
            "GitHub remote must look like "
            "git@github.com:owner/repo.git, https://github.com/owner/repo.git, "
            "or ssh://git@ssh.github.com:443/owner/repo.git"
        )
    return slug


def validate_slug_part(value: str, label: str) -> str:
    value = strip_dot_git(value.strip())
    if not value or "/" in value or "\\" in value:
        raise SyncError(f"Invalid {label}: {value!r}")
    return value


def build_github_target(owner: str, repo_name: str) -> GitHubTarget:
    owner = validate_slug_part(owner, "GitHub owner")
    repo_name = validate_slug_part(repo_name, "GitHub repository name")
    return GitHubTarget(
        owner=owner,
        name=repo_name,
        ssh_url=f"git@github.com:{owner}/{repo_name}.git",
        web_url=f"https://github.com/{owner}/{repo_name}",
    )


def build_gitee_target(owner: str, repo_name: str) -> GiteeTarget:
    owner = validate_slug_part(owner, "Gitee owner")
    repo_name = validate_slug_part(repo_name, "Gitee repository name")
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


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def detect_gitee_ssh_owner() -> str | None:
    """通过 SSH 登录提示识别当前 Gitee 账号，例如 liangliang2000。"""
    # Gitee 的 SSH 认证成功时会输出类似：
    # Hi liangliang2000(@liangliang2000)! You've successfully authenticated...
    # 这里解析这个登录名，避免默认拿 GitHub 用户名当 Gitee 命名空间。
    result = subprocess.run(
        [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            "git@gitee.com",
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    output = strip_ansi(f"{result.stdout}\n{result.stderr}")
    match = re.search(r"Hi\s+([^\s(!]+)(?:\(@[^)]*\))?!", output)
    if not match:
        return None

    owner = match.group(1).strip()
    try:
        return validate_slug_part(owner, "Gitee SSH owner")
    except SyncError:
        return None


def choose_gitee_owner(explicit_owner: str | None, fallback_owner: str) -> str:
    """优先使用命令行参数，其次使用 SSH 账号，最后回退 GitHub owner。"""
    if explicit_owner:
        return explicit_owner

    detected_owner = detect_gitee_ssh_owner()
    if detected_owner:
        log(f"Detected Gitee SSH owner: {detected_owner}")
        return detected_owner

    log(f"[WARN] Could not detect Gitee SSH owner; fallback to {fallback_owner}")
    return fallback_owner


def env_token(names: Sequence[str]) -> tuple[str | None, str]:
    for name in names:
        token = os.environ.get(name)
        if token:
            return token, name
    return None, names[0]


def github_cli_token() -> tuple[str | None, str | None]:
    # 如果用户已经用 gh 登录，就复用 gh 保存的 token；捕获输出，避免泄露 token。
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None, None

    token = (result.stdout or "").strip()
    if result.returncode == 0 and token:
        return token, "gh auth token"
    return None, None


def github_token() -> tuple[str | None, str]:
    token, source = env_token(GITHUB_TOKEN_ENV_NAMES)
    if token:
        return token, source

    token, source = github_cli_token()
    if token and source:
        log(f"Using GitHub token from {source}.")
        return token, source

    return None, GITHUB_TOKEN_ENV_NAMES[0]


def ensure_https_support() -> None:
    """检查当前 Python 是否能使用 HTTPS；conda 环境未激活时常在这里失败。"""
    try:
        import ssl  # noqa: F401
    except ImportError as exc:
        raise SyncError(
            "Current Python cannot import ssl, so HTTPS API requests are unavailable. "
            "Do not run the conda env python.exe directly. Use:\n"
            r"  F:\anaconda\Scripts\conda.exe run -n tf22 python github_gitee_sync.py"
            "\nOr activate tf22 in the shell first, then run the script."
        ) from exc

    if not hasattr(urllib.request, "HTTPSHandler"):
        raise SyncError(
            "Current Python urllib has no HTTPSHandler. Run the script through "
            "an activated conda environment, for example:\n"
            r"  F:\anaconda\Scripts\conda.exe run -n tf22 python github_gitee_sync.py"
        )


def prompt_text(label: str, default: str | None = None) -> str:
    default_hint = f" [{default}]" if default else ""
    while True:
        try:
            value = input(f"{label}{default_hint}: ").strip()
        except EOFError as exc:
            raise SyncError("Interactive input is not available.") from exc
        if value:
            return value
        if default:
            return default
        print("This value is required.")


def prompt_yes_no(label: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        try:
            value = input(f"{label} [{suffix}]: ").strip().lower()
        except EOFError as exc:
            raise SyncError("Interactive input is not available.") from exc
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer y or n.")


def urlopen_json_request(service: str, request: urllib.request.Request) -> ApiResponse:
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return read_json_response(response)
        except urllib.error.HTTPError as exc:
            return read_json_response(exc)
        except urllib.error.URLError as exc:
            if attempt >= 3:
                raise SyncError(f"{service} API request failed: {exc}") from exc
            log(f"[WARN] {service} API request failed once; retrying ({attempt}/3): {exc}")
            time.sleep(attempt)

    raise SyncError(f"{service} API request failed.")


def github_api_request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
) -> ApiResponse:
    ensure_https_support()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-gitee-sync/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        f"{GITHUB_API_BASE}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    return urlopen_json_request("GitHub", request)


def github_error_message(response: ApiResponse) -> str:
    if isinstance(response.data, dict):
        message = response.data.get("message")
        if message:
            return str(message)
    return response.text.strip() or f"HTTP {response.status}"


def github_authenticated_login(token: str) -> str:
    response = github_api_request("GET", "/user", token=token)
    if response.status != 200 or not isinstance(response.data, dict):
        raise SyncError(
            f"Could not read GitHub user from token: "
            f"HTTP {response.status}: {github_error_message(response)}"
        )
    login = response.data.get("login")
    if not login:
        raise SyncError("GitHub token response did not include a login.")
    return str(login)


def github_repo_exists(target: GitHubTarget, token: str | None) -> bool:
    response = github_api_request(
        "GET",
        f"/repos/{api_quote(target.owner)}/{api_quote(target.name)}",
        token=token,
    )
    if response.status == 200:
        return True
    if response.status == 404:
        return False
    raise SyncError(
        f"Could not check GitHub repository {target.web_url}: "
        f"HTTP {response.status}: {github_error_message(response)}"
    )


def create_github_repo(target: GitHubTarget, *, token: str, private: bool) -> None:
    login = github_authenticated_login(token)
    payload: dict[str, Any] = {
        "name": target.name,
        "private": private,
        "description": "Created by github_gitee_sync.py",
        "has_issues": True,
        "has_projects": True,
        "has_wiki": False,
    }
    if target.owner.lower() == login.lower():
        path = "/user/repos"
    else:
        path = f"/orgs/{api_quote(target.owner)}/repos"

    response = github_api_request("POST", path, token=token, payload=payload)
    if response.status not in (200, 201):
        raise SyncError(
            f"Could not create GitHub repository {target.web_url}: "
            f"HTTP {response.status}: {github_error_message(response)}"
        )


def ensure_github_repo(
    *,
    target: GitHubTarget,
    token: str | None,
    private: bool,
    dry_run: bool,
) -> None:
    visibility = "private" if private else "public"
    if dry_run:
        step(f"Would check or create {visibility} GitHub repository: {target.web_url}")
        return

    step(f"Check GitHub repository: {target.web_url}")
    if github_repo_exists(target, token):
        log(f"GitHub repository exists: {target.web_url}")
        return

    if not token:
        raise SyncError(
            f"GitHub 仓库不存在：{target.web_url}。"
            "请先设置 GITHUB_TOKEN/GH_TOKEN，或执行 gh auth login，"
            "脚本才能自动创建公开仓库。"
        )

    step(f"Create {visibility} GitHub repository: {target.web_url}")
    create_github_repo(target, token=token, private=private)
    if not github_repo_exists(target, token):
        raise SyncError(
            "GitHub repository was created, but the expected target path was not found. "
            "Check whether --github-owner matches your GitHub account or organization."
        )
    log(f"GitHub repository created: {target.web_url}")


def choose_github_target(
    *,
    repo: Path,
    token: str | None,
    github_owner: str | None,
    github_repo: str | None,
    github_private: bool,
    dry_run: bool,
) -> tuple[GitHubTarget, bool]:
    default_owner: str | None = DEFAULT_GITHUB_OWNER
    if token and not dry_run:
        try:
            token_owner = github_authenticated_login(token)
            if not github_owner and token_owner != DEFAULT_GITHUB_OWNER:
                log(
                    f"[WARN] GitHub token login is {token_owner}, "
                    f"but the default owner is {DEFAULT_GITHUB_OWNER}."
                )
        except SyncError as exc:
            log(f"[WARN] {exc}")

    owner = github_owner or default_owner
    repo_default_name = local_folder_repo_name(repo)
    name = github_repo or repo_default_name
    private = github_private

    has_complete_cli_target = bool(owner and name)
    should_prompt = sys.stdin.isatty() and not has_complete_cli_target
    if should_prompt:
        print("\nGitHub remote is missing or not a GitHub URL.")
        owner = prompt_text("GitHub owner/user/org", owner or default_owner)
        name = prompt_text("GitHub repository name", name)
        if not github_private:
            private = prompt_yes_no("Create GitHub repository as private?", False)
    elif not owner:
        raise SyncError(
            "Missing GitHub owner. Pass --github-owner and optionally --github-repo."
        )

    return build_github_target(owner, name), private


def ensure_existing_github_repository(
    *,
    repo: Path,
    remote: str,
    target: GitHubTarget,
    token: str | None,
    github_owner: str | None,
    github_repo: str | None,
    private: bool,
    fix_remote: bool,
    confirm_remote_retarget: bool,
    dry_run: bool,
) -> RepoSlug:
    folder_name = local_folder_repo_name(repo)
    visibility = "private" if private else "public"

    step(f"Check GitHub repository: {target.web_url}")
    if github_repo_exists(target, token):
        log(f"GitHub repository already configured: {target.web_url}")
        return RepoSlug(owner=target.owner, name=target.name)

    replacement = build_github_target(
        github_owner or target.owner,
        github_repo or folder_name,
    )
    if replacement.owner == target.owner and replacement.name == target.name:
        step(f"Create missing {visibility} GitHub repository: {target.web_url}")
        ensure_github_repo(target=target, token=token, private=private, dry_run=dry_run)
        return RepoSlug(owner=target.owner, name=target.name)

    message = (
        f"GitHub remote {remote!r} still points to {target.web_url}, "
        "but that repository was not found. 远程仓库可能已经删除，"
        f"但本地 {remote!r} 仍保留旧地址。"
    )
    if dry_run:
        log(f"[WARN] {message}")
        ensure_github_repo(
            target=replacement,
            token=token,
            private=private,
            dry_run=True,
        )
        step(f"Would update GitHub remote {remote}: {replacement.ssh_url}")
        return RepoSlug(owner=replacement.owner, name=replacement.name)

    if not fix_remote:
        if confirm_remote_retarget:
            print("\n检测到本地 GitHub 远端指向的仓库已经不存在：")
            print(f"  当前 {remote}: {target.web_url}")
            print(f"  新目标: {replacement.web_url}")
            confirmed = prompt_yes_no(
                "是否改为当前文件夹名对应的 GitHub 仓库？",
                False,
            )
            if not confirmed:
                raise SyncError("用户取消修改 GitHub remote。")
        else:
            raise SyncError(
                f"{message} 如需改成当前文件夹名仓库，请在菜单中选择功能 1 并确认，"
                "或在非交互运行时追加 --fix-remote。"
            )

    if replacement.name != folder_name and not github_repo:
        raise SyncError(
            f"Internal target mismatch: expected folder repository {folder_name!r}, "
            f"got {replacement.name!r}."
        )

    step(f"Create or check {visibility} GitHub repository: {replacement.web_url}")
    ensure_github_repo(
        target=replacement,
        token=token,
        private=private,
        dry_run=dry_run,
    )
    step(f"Update GitHub remote {remote}: {replacement.ssh_url}")
    run_git(repo, ["remote", "set-url", remote, replacement.ssh_url], dry_run=dry_run)
    return RepoSlug(owner=replacement.owner, name=replacement.name)


def ensure_github_remote(
    *,
    repo: Path,
    remote: str,
    github_owner: str | None,
    github_repo: str | None,
    github_private: bool,
    fix_remote: bool,
    confirm_remote_retarget: bool,
    dry_run: bool,
) -> RepoSlug:
    token = github_token()[0]
    existing_url = get_remote_url(repo, remote)
    if existing_url:
        slug = parse_github_slug(existing_url)
        if slug is not None:
            log(f"GitHub remote {remote} points to {existing_url}")
            return ensure_existing_github_repository(
                repo=repo,
                remote=remote,
                target=build_github_target(slug.owner, slug.name),
                token=token,
                github_owner=github_owner,
                github_repo=github_repo,
                private=github_private,
                fix_remote=fix_remote,
                confirm_remote_retarget=confirm_remote_retarget,
                dry_run=dry_run,
            )

        target, private = choose_github_target(
            repo=repo,
            token=token,
            github_owner=github_owner,
            github_repo=github_repo,
            github_private=github_private,
            dry_run=dry_run,
        )
        message = (
            f"Remote {remote!r} points to {existing_url!r}, "
            f"not a GitHub repository. Expected {target.ssh_url!r}."
        )
        if not fix_remote:
            raise SyncError(message + " Re-run with --fix-remote to update it.")
        ensure_github_repo(target=target, token=token, private=private, dry_run=dry_run)
        step(f"Update GitHub remote {remote}: {target.ssh_url}")
        run_git(repo, ["remote", "set-url", remote, target.ssh_url], dry_run=dry_run)
        return RepoSlug(owner=target.owner, name=target.name)

    target, private = choose_github_target(
        repo=repo,
        token=token,
        github_owner=github_owner,
        github_repo=github_repo,
        github_private=github_private,
        dry_run=dry_run,
    )
    ensure_github_repo(target=target, token=token, private=private, dry_run=dry_run)
    step(f"Add GitHub remote {remote}: {target.ssh_url}")
    run_git(repo, ["remote", "add", remote, target.ssh_url], dry_run=dry_run)
    return RepoSlug(owner=target.owner, name=target.name)


def ensure_gitee_remote(
    *,
    repo: Path,
    remote: str,
    target: GiteeTarget,
    fix_remote: bool,
    auto_fix_remote: bool,
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
    if not fix_remote and not auto_fix_remote:
        raise SyncError(message + " Re-run with --fix-remote to update it.")

    if auto_fix_remote and not fix_remote:
        log(f"[WARN] {message} 三方同步会自动改成 GitHub 同名的 Gitee 目标。")
    step(f"Update Gitee remote {remote}: {target.ssh_url}")
    run_git(repo, ["remote", "set-url", remote, target.ssh_url], dry_run=dry_run)


def ensure_github_repository(
    *,
    repo: Path,
    github_remote: str,
    github_owner: str | None,
    github_repo: str | None,
    github_private: bool,
    fix_remote: bool,
    confirm_remote_retarget: bool,
    dry_run: bool,
) -> RepoSlug:
    repo = ensure_repo(repo)
    log(f"Repository: {repo}")
    log(f"GitHub remote: {github_remote}")

    # 只补齐 GitHub，不检查 Gitee，也不要求工作区干净，方便先把仓库入口建好。
    slug = ensure_github_remote(
        repo=repo,
        remote=github_remote,
        github_owner=github_owner,
        github_repo=github_repo,
        github_private=github_private,
        fix_remote=fix_remote,
        confirm_remote_retarget=confirm_remote_retarget,
        dry_run=dry_run,
    )
    log(f"GitHub ready: {slug.owner}/{slug.name}")
    return slug


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
    ensure_https_support()
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
    return urlopen_json_request("Gitee", request)


def gitee_error_message(response: ApiResponse) -> str:
    if isinstance(response.data, dict):
        for key in ("message", "error", "error_description"):
            value = response.data.get(key)
            if value:
                return str(value)
    return response.text.strip() or f"HTTP {response.status}"


def is_empty_gitee_repo_public_error(response: ApiResponse) -> bool:
    message = gitee_error_message(response)
    return response.status == 422 and "空仓库不支持设置为公开仓库" in message


def get_gitee_repo(target: GiteeTarget, token: str | None) -> ApiResponse:
    # 查询 Gitee 仓库是否存在；公开仓库通常不用 token 也能查到。
    return gitee_api_request(
        "GET",
        f"/repos/{api_quote(target.owner)}/{api_quote(target.name)}",
        token=token,
    )


def gitee_repo_exists(target: GiteeTarget, token: str | None) -> bool:
    response = get_gitee_repo(target, token)
    if response.status == 200:
        return True
    if response.status == 404:
        return False
    raise SyncError(
        f"Could not check Gitee repository {target.web_url}: "
        f"HTTP {response.status}: {gitee_error_message(response)}"
    )


def is_gitee_repo_private(response: ApiResponse) -> bool:
    if not isinstance(response.data, dict):
        return False
    private_value = response.data.get("private")
    return private_value is True or str(private_value).lower() == "true"


def ensure_gitee_repo_visibility(
    target: GiteeTarget,
    response: ApiResponse,
    *,
    private: bool,
) -> None:
    # 默认要求 Gitee 仓库公开。只有用户显式加 --private 时，才允许私有仓库。
    if private:
        return

    if is_gitee_repo_private(response):
        raise SyncError(
            f"Gitee repository already exists but is private: {target.web_url}. "
            "Make it public on Gitee, or re-run with --private if you really want "
            "a private mirror."
        )


def make_gitee_repo_public_if_needed(
    target: GiteeTarget,
    response: ApiResponse,
    *,
    token: str | None,
    private: bool,
) -> tuple[ApiResponse, bool]:
    if private or not is_gitee_repo_private(response):
        return response, False

    if not token:
        raise SyncError(
            f"Gitee repository already exists but is private: {target.web_url}. "
            f"Set {GITEE_TOKEN_ENV} so the script can make it public, "
            "or make it public on Gitee manually."
        )

    step(f"Make Gitee repository public: {target.web_url}")
    if not update_gitee_repo_visibility(
        target,
        token=token,
        private=False,
        allow_empty_repo=True,
    ):
        log(
            "[WARN] Gitee does not allow making an empty repository public yet; "
            "will retry after pushing code."
        )
        return response, True
    return get_gitee_repo(target, token), False


def update_gitee_repo_visibility(
    target: GiteeTarget,
    *,
    token: str,
    private: bool,
    allow_empty_repo: bool = False,
) -> bool:
    # Gitee 使用 PATCH 仓库接口更新公开/私有状态，private=false 表示公开仓库。
    response = gitee_api_request(
        "PATCH",
        f"/repos/{api_quote(target.owner)}/{api_quote(target.name)}",
        token=token,
        form={
            "name": target.name,
            "private": str(private).lower(),
        },
    )
    if response.status not in (200, 201, 204):
        if not private and allow_empty_repo and is_empty_gitee_repo_public_error(response):
            return False
        raise SyncError(
            f"Could not update Gitee repository visibility {target.web_url}: "
            f"HTTP {response.status}: {gitee_error_message(response)}"
        )
    return True


def gitee_default_branch(response: ApiResponse) -> str | None:
    if not isinstance(response.data, dict):
        return None
    value = response.data.get("default_branch")
    if not value:
        return None
    return str(value)


def update_gitee_default_branch(
    target: GiteeTarget,
    *,
    token: str,
    branch: str,
) -> None:
    # Gitee 网页默认展示 default_branch；同步后也要把默认分支切到当前同步分支。
    response = gitee_api_request(
        "PATCH",
        f"/repos/{api_quote(target.owner)}/{api_quote(target.name)}",
        token=token,
        form={
            "name": target.name,
            "default_branch": branch,
        },
    )
    if response.status not in (200, 201, 204):
        raise SyncError(
            f"Could not update Gitee default branch {target.web_url}: "
            f"HTTP {response.status}: {gitee_error_message(response)}"
        )


def ensure_gitee_default_branch(
    *,
    target: GiteeTarget,
    branch: str,
    token: str | None,
    dry_run: bool,
) -> None:
    if dry_run:
        step(f"Would ensure Gitee default branch is {branch}: {target.web_url}")
        return

    if not token:
        log(
            f"[WARN] {GITEE_TOKEN_ENV} is not set; code was pushed to {branch}, "
            f"but Gitee may still open another default branch in the web UI. "
            f"Set the default branch to {branch} on Gitee manually, or set "
            f"{GITEE_TOKEN_ENV} and re-run this script."
        )
        return

    response = get_gitee_repo(target, token)
    if response.status != 200:
        raise SyncError(
            f"Could not read Gitee repository default branch {target.web_url}: "
            f"HTTP {response.status}: {gitee_error_message(response)}"
        )

    current = gitee_default_branch(response)
    if current == branch:
        log(f"Gitee default branch is already {branch}")
        return

    step(f"Set Gitee default branch to {branch}: {target.web_url}")
    update_gitee_default_branch(target, token=token, branch=branch)


def create_gitee_repo(target: GiteeTarget, *, token: str, private: bool) -> None:
    # Gitee API 的 private=false 表示创建公开仓库；脚本默认 private=False。
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
) -> bool:
    visibility = "private" if private else "public"
    if dry_run:
        step(f"Would check or create {visibility} Gitee repository: {target.web_url}")
        return False

    step(f"Check Gitee repository: {target.web_url}")
    response = get_gitee_repo(target, token)
    if response.status == 200:
        response, retry_public_after_push = make_gitee_repo_public_if_needed(
            target,
            response,
            token=token,
            private=private,
        )
        if not retry_public_after_push:
            ensure_gitee_repo_visibility(target, response, private=private)
        log(f"Gitee repository exists: {target.web_url}")
        return retry_public_after_push
    if response.status != 404:
        raise SyncError(
            f"Could not check Gitee repository {target.web_url}: "
            f"HTTP {response.status}: {gitee_error_message(response)}"
        )

    if not token:
        raise SyncError(
            f"Gitee repository does not exist: {target.web_url}. "
            f"Set {GITEE_TOKEN_ENV} before running so the script can create it."
        )

    step(f"Create {visibility} Gitee repository: {target.web_url}")
    create_gitee_repo(target, token=token, private=private)
    response = get_gitee_repo(target, token)
    if response.status != 200:
        raise SyncError(
            "Gitee repository was created, but the expected target path was not found. "
            "Check whether --gitee-owner matches your Gitee account namespace."
        )
    response, retry_public_after_push = make_gitee_repo_public_if_needed(
        target,
        response,
        token=token,
        private=private,
    )
    if not retry_public_after_push:
        ensure_gitee_repo_visibility(target, response, private=private)
    log(f"Gitee repository created: {target.web_url}")
    return retry_public_after_push


def remote_ref(remote: str, branch: str) -> str:
    return f"{remote}/{branch}"


def is_missing_remote_ref(result: GitResult) -> bool:
    output = f"{result.stdout}\n{result.stderr}".lower()
    return "couldn't find remote ref" in output or "could not find remote ref" in output


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
    branch: str | None,
    github_remote: str,
    gitee_remote: str,
    github_owner: str | None,
    github_repo: str | None,
    github_private: bool,
    gitee_owner: str | None,
    private: bool,
    fix_remote: bool,
    confirm_github_remote_retarget: bool,
    dry_run: bool,
) -> None:
    repo = ensure_repo(repo)
    if branch is None:
        branch = current_branch(repo)
    log(f"Repository: {repo}")
    log(f"Branch: {branch}")
    log(f"GitHub remote: {github_remote}")
    log(f"Gitee remote: {gitee_remote}")

    step("Check current branch and worktree")
    ensure_current_branch(repo, branch)
    ensure_has_commit(repo)
    ensure_clean_worktree(repo)

    # 1. 先确认 GitHub remote 可用。没有 GitHub remote 时，脚本会按参数创建或添加。
    step("Ensure GitHub remote")
    github_slug = ensure_github_remote(
        repo=repo,
        remote=github_remote,
        github_owner=github_owner,
        github_repo=github_repo,
        github_private=github_private,
        fix_remote=fix_remote,
        confirm_remote_retarget=confirm_github_remote_retarget,
        dry_run=dry_run,
    )
    # 2. Gitee owner 默认取 SSH 登录账号，例如 liangliang2000；仓库名沿用 GitHub 仓库名。
    target = build_gitee_target(
        choose_gitee_owner(gitee_owner, github_slug.owner),
        github_slug.name,
    )
    log(f"GitHub repository: {github_slug.owner}/{github_slug.name}")
    log(f"Gitee target: {target.owner}/{target.name}")

    # 3. 确保 Gitee 仓库存在。默认创建公开仓库；只有传 --private 才创建/允许私有仓库。
    token = os.environ.get(GITEE_TOKEN_ENV)
    gitee_public_after_push = ensure_gitee_repo(
        target=target,
        token=token,
        private=private,
        dry_run=dry_run,
    )
    ensure_gitee_remote(
        repo=repo,
        remote=gitee_remote,
        target=target,
        fix_remote=fix_remote,
        auto_fix_remote=True,
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
    missing_refs: list[str] = []
    fetched_remotes: list[str] = []
    for remote in (github_remote, gitee_remote):
        # 4. 拉取两端远程分支。新仓库还没有该分支时，只记录 warning。
        step(f"Fetch {remote}/{branch}")
        result = run_git(repo, ["fetch", remote, branch], check=False)
        if result.returncode == 0:
            fetched_remotes.append(remote)
        elif is_missing_remote_ref(result):
            missing_refs.append(f"{remote}/{branch}")
            log(f"[WARN] Remote branch does not exist yet: {remote}/{branch}")
        else:
            fetch_warnings.append(f"fetch {remote}: exit code {result.returncode}")

    if not fetched_remotes and fetch_warnings:
        raise SyncError("Both GitHub and Gitee fetch operations failed.")

    ensure_complete_history(repo, github_remote, dry_run=dry_run)

    try:
        for remote in fetched_remotes:
            # 5. 把远程新提交合并进本地。冲突需要用户手动解决后再重跑脚本。
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
        # 6. 最后把当前本地 HEAD 同步推送到 GitHub 和 Gitee。
        step(f"Push HEAD to {remote}/{branch}")
        result = run_git(repo, ["push", remote, f"HEAD:{branch}"], check=False)
        if result.returncode != 0:
            push_failures.append(f"push {remote}: exit code {result.returncode}")

    for remote in (github_remote, gitee_remote):
        step(f"Refresh {remote}/{branch}")
        result = run_git(repo, ["fetch", remote, branch], check=False)
        if result.returncode != 0:
            fetch_warnings.append(f"refresh {remote}: exit code {result.returncode}")

    gitee_push_failed = any(
        failure.startswith(f"push {gitee_remote}:") for failure in push_failures
    )
    if push_failures:
        if gitee_public_after_push and gitee_push_failed:
            log(
                "[WARN] Skipping public visibility retry because the Gitee push failed. "
                "Fix the push failure and re-run."
            )
        for warning in fetch_warnings:
            log(f"[WARN] {warning}")
        for failure in push_failures:
            log(f"[ERROR] {failure}")
        raise SyncError("At least one push failed. Re-run after fixing remote access.")

    if gitee_public_after_push and not gitee_push_failed:
        step("Retry making Gitee repository public after initial push")
        response = get_gitee_repo(target, token)
        if response.status != 200:
            raise SyncError(
                f"Could not read Gitee repository visibility {target.web_url}: "
                f"HTTP {response.status}: {gitee_error_message(response)}"
            )
        response, still_empty = make_gitee_repo_public_if_needed(
            target,
            response,
            token=token,
            private=private,
        )
        if still_empty:
            raise SyncError(
                "Gitee still reports the repository as empty after push. "
                "Check whether the Gitee push created the target branch, then re-run."
            )
        ensure_gitee_repo_visibility(target, response, private=private)

    ensure_gitee_default_branch(
        target=target,
        branch=branch,
        token=token,
        dry_run=dry_run,
    )

    print_final_refs(repo, branch, github_remote, gitee_remote)
    for missing_ref in missing_refs:
        log(f"[WARN] Initialized missing remote branch during push: {missing_ref}")
    for warning in fetch_warnings:
        log(f"[WARN] {warning}")
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


def print_init_gitee(
    repo: Path,
    github_remote: str,
    github_owner: str | None,
    github_repo: str | None,
    gitee_owner: str | None,
) -> None:
    log("GitHub/Gitee initialization checklist")
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

    print("\nGitHub access token:")
    token, token_source = github_token()
    if token:
        print(f"  {token_source} is available")
    else:
        print("  GITHUB_TOKEN or GH_TOKEN is not set, and gh auth token is unavailable")
        print("  Current PowerShell session:")
        print("    $env:GITHUB_TOKEN='your-github-token'")
        print("  Or login with GitHub CLI:")
        print("    gh auth login")
        print("  Persist for your Windows user:")
        print(
            "    [Environment]::SetEnvironmentVariable("
            "'GITHUB_TOKEN','your-github-token','User')"
        )

    print("\nGitee access token:")
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
    print("  Default visibility: public. Use --private only if you want a private Gitee mirror.")

    try:
        repo = ensure_repo(repo)
        github_url = get_remote_url(repo, github_remote)
        if github_url:
            slug = parse_github_remote(github_url)
        elif github_owner:
            slug = RepoSlug(
                owner=github_owner,
                name=github_repo or repo.name,
            )
        else:
            slug = RepoSlug(
                owner=DEFAULT_GITHUB_OWNER,
                name=github_repo or repo.name,
            )
        if slug:
            github_target = build_github_target(slug.owner, slug.name)
            target = build_gitee_target(
                choose_gitee_owner(gitee_owner, slug.owner),
                slug.name,
            )
            print("\nDerived repository mapping:")
            print(f"  GitHub: {github_target.owner}/{github_target.name}")
            print(f"  GitHub remote: {github_target.ssh_url}")
            print(f"  Gitee : {target.owner}/{target.name}")
            print(f"  Gitee remote : {target.ssh_url}")
    except SyncError as exc:
        print(f"\nRepository mapping skipped: {exc}")


def prompt_main_menu() -> str:
    print("\n请选择要执行的功能：")
    print("  1. 创建/补齐 GitHub 公开仓库")
    print("  2. 三方同步本地、GitHub、Gitee")
    print("  0. 退出")

    while True:
        try:
            choice = input("请输入功能编号：").strip()
        except EOFError as exc:
            raise SyncError(
                "没有读取到菜单选择。非交互运行时请使用 --ensure-github 或 --sync。"
            ) from exc

        if choice == "1":
            return MODE_ENSURE_GITHUB
        if choice == "2":
            return MODE_SYNC
        if choice == "0":
            return MODE_EXIT
        print("输入无效，请输入 1、2 或 0。")


def resolve_run_mode(args: argparse.Namespace) -> str:
    if args.ensure_github and args.sync:
        raise SyncError("--ensure-github 和 --sync 只能选择一个。")
    if args.ensure_github:
        return MODE_ENSURE_GITHUB
    if args.sync:
        return MODE_SYNC
    return prompt_main_menu()


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
        help="Branch to synchronize. Defaults to the current branch.",
    )
    parser.add_argument(
        "--github-remote",
        default=DEFAULT_GITHUB_REMOTE,
        help=f"GitHub remote name. Defaults to {DEFAULT_GITHUB_REMOTE}.",
    )
    parser.add_argument(
        "--github-owner",
        default=None,
        help=f"GitHub user or organization. Defaults to {DEFAULT_GITHUB_OWNER}.",
    )
    parser.add_argument(
        "--github-repo",
        default=None,
        help="GitHub repository name. Defaults to the local directory name.",
    )
    parser.add_argument(
        "--github-private",
        action="store_true",
        help="Create the GitHub repository as private. Default is public.",
    )
    parser.add_argument(
        "--gitee-remote",
        default=DEFAULT_GITEE_REMOTE,
        help=f"Gitee remote name. Defaults to {DEFAULT_GITEE_REMOTE}.",
    )
    parser.add_argument(
        "--gitee-owner",
        default=None,
        help=(
            "Gitee user or namespace. Defaults to the detected Gitee SSH "
            "login, then falls back to the GitHub owner."
        ),
    )
    parser.add_argument(
        "--private",
        action="store_true",
        help="Create or accept a private Gitee repository. If omitted, Gitee is public.",
    )
    parser.add_argument(
        "--fix-remote",
        action="store_true",
        help="Update an existing mismatched GitHub or Gitee remote URL.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions and git commands without changing repositories.",
    )
    parser.add_argument(
        "--ensure-github",
        action="store_true",
        help="Only create/check the GitHub repository and origin remote, then exit.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Run the GitHub/Gitee sync directly without showing the menu.",
    )
    parser.add_argument(
        "--init-gitee",
        action="store_true",
        help="Print local GitHub/Gitee SSH/token initialization checklist and exit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        repo = Path(args.repo)
        if args.init_gitee:
            print_init_gitee(
                repo,
                str(args.github_remote),
                args.github_owner,
                args.github_repo,
                args.gitee_owner,
            )
            return 0
        selected_from_menu = not args.ensure_github and not args.sync
        mode = resolve_run_mode(args)
        if mode == MODE_EXIT:
            log("Exit.")
            return 0
        if mode == MODE_ENSURE_GITHUB:
            ensure_github_repository(
                repo=repo,
                github_remote=str(args.github_remote),
                github_owner=args.github_owner,
                github_repo=args.github_repo,
                github_private=bool(args.github_private),
                fix_remote=bool(args.fix_remote),
                confirm_remote_retarget=selected_from_menu,
                dry_run=bool(args.dry_run),
            )
            return 0
        if mode == MODE_SYNC:
            sync_repositories(
                repo=repo,
                branch=args.branch,
                github_remote=str(args.github_remote),
                gitee_remote=str(args.gitee_remote),
                github_owner=args.github_owner,
                github_repo=args.github_repo,
                github_private=bool(args.github_private),
                gitee_owner=args.gitee_owner,
                private=bool(args.private),
                fix_remote=bool(args.fix_remote),
                confirm_github_remote_retarget=selected_from_menu,
                dry_run=bool(args.dry_run),
            )
            return 0
        raise SyncError(f"Unknown run mode: {mode}")
    except SyncError as exc:
        log(f"[ERROR] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
