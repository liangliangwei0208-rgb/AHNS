"""
Synchronize the local AHNS repository with GitHub and Gitee.

This script is intended to be run on the main development computer after
editing code. It keeps the local branch, GitHub remote, and Gitee remote on the
same branch without rewriting history.
GitHub is expected to use the local SakuraCat proxy; Gitee should stay direct.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


DEFAULT_BRANCH = "main"
DEFAULT_GITHUB_REMOTE = "origin"
DEFAULT_GITEE_REMOTE = "gitee"
REMOTE_RETRY_ATTEMPTS = 3
GITHUB_PROXY_DISABLE_CONFIG = "http.https://github.com.proxy="
CACHE_CONFLICT_EXACT_PATHS = {
    "cache/fund_estimate_return_cache.json",
    "cache/security_return_cache.json",
}
CACHE_INDEX_DAILY_PATTERN = re.compile(r"^cache/[^/]+_index_daily\.csv$")


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


def looks_like_network_failure(result: GitResult) -> bool:
    output = f"{result.stdout}\n{result.stderr}".lower()
    network_markers = (
        "unable to connect",
        "failed to connect",
        "could not connect",
        "connection closed",
        "connection reset",
        "timed out",
        "timeout",
        "ssl",
        "proxy",
        "relay host",
    )
    return any(marker in output for marker in network_markers)


def remote_failure_message(action: str, label: str, result: GitResult) -> str:
    reason = "疑似代理/网络瞬时失败" if looks_like_network_failure(result) else "远程操作失败"
    return f"{action} {label}: exit code {result.returncode}（{reason}）"


def run_remote_git_with_retry(
    repo: Path,
    args: Sequence[str],
    *,
    action: str,
    label: str,
    attempts: int = REMOTE_RETRY_ATTEMPTS,
) -> GitResult:
    result = GitResult(1, "", "")
    for attempt in range(1, attempts + 1):
        result = run_git(repo, args, check=False)
        if result.returncode == 0:
            return result
        if not looks_like_network_failure(result):
            return result
        if attempt >= attempts:
            break

        delay_seconds = attempt * 2
        log(
            f"[WARN] {action} {label} 疑似代理/网络瞬时失败，"
            f"{delay_seconds}s 后重试 ({attempt + 1}/{attempts})。"
        )
        time.sleep(delay_seconds)

    if label == "GitHub" and looks_like_network_failure(result):
        log("[WARN] GitHub 代理重试仍失败，尝试直连 GitHub 一次。")
        return run_git(repo, ["-c", GITHUB_PROXY_DISABLE_CONFIG, *args], check=False)
    return result


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


def collect_final_refs(
    repo: Path,
    branch: str,
    github_remote: str,
    gitee_remote: str,
) -> dict[str, str | None]:
    refs = [
        ("local", "HEAD"),
        (github_remote, remote_ref(github_remote, branch)),
        (gitee_remote, remote_ref(gitee_remote, branch)),
    ]
    commits: dict[str, str | None] = {}
    for label, ref in refs:
        try:
            commits[label] = git_output(repo, ["rev-parse", "--verify", ref])
        except SyncError:
            commits[label] = None
    return commits


def print_final_refs(commits: dict[str, str | None]) -> None:
    log("最终提交位置:")
    for label, commit in commits.items():
        display = commit[:12] if commit else "不可用"
        print(f"  {label}: {display}")


def final_refs_are_aligned(commits: dict[str, str | None]) -> bool:
    values = list(commits.values())
    return bool(values) and all(values) and len(set(values)) == 1


def normalize_git_path(path: str) -> str:
    return path.replace("\\", "/").strip()


def is_auto_merge_cache_path(path: str) -> bool:
    normalized = normalize_git_path(path)
    return normalized in CACHE_CONFLICT_EXACT_PATHS or bool(
        CACHE_INDEX_DAILY_PATTERN.match(normalized)
    )


def unmerged_paths(repo: Path) -> list[str]:
    output = git_output(repo, ["diff", "--name-only", "--diff-filter=U"])
    return [normalize_git_path(line) for line in output.splitlines() if line.strip()]


def git_stage_text(repo: Path, stage: int, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f":{stage}:{path}"],
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise SyncError(f"读取 Git stage {stage} 失败: {path}: {message}")
    return result.stdout or ""


def parse_datetime_like(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        pass
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if match:
        try:
            return datetime.fromisoformat(match.group(0))
        except ValueError:
            return None
    return None


def latest_datetime_from_record(record: dict[str, Any]) -> datetime | None:
    candidates = [
        record.get("run_time_bj"),
        record.get("updated_at"),
        record.get("fetched_at_bj"),
        record.get("fetched_at"),
        record.get("generated_at_bj"),
        record.get("trade_date"),
        record.get("valuation_anchor_date"),
        record.get("valuation_date"),
        record.get("date"),
    ]
    parsed = [dt for value in candidates if (dt := parse_datetime_like(value))]
    return max(parsed) if parsed else None


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def cache_status_rank(status: Any, *, estimate: bool = False) -> int:
    text = str(status or "").strip().lower()
    if estimate:
        return {
            "failed": 0,
            "missing": 0,
            "pending": 0,
            "stale": 1,
            "partial": 2,
            "intraday": 2,
            "afterhours": 2,
            "premarket": 2,
            "night": 2,
            "complete": 3,
            "traded": 3,
            "closed": 3,
            "final": 3,
        }.get(text, 0)
    return {
        "failed": 0,
        "missing": 0,
        "pending": 0,
        "stale": 1,
        "partial": 2,
        "intraday": 2,
        "afterhours": 2,
        "premarket": 2,
        "night": 2,
        "traded": 3,
        "closed": 3,
        "complete": 3,
    }.get(text, 0)


def record_has_value(record: dict[str, Any], *, estimate: bool = False) -> bool:
    if estimate:
        return safe_float(record.get("estimate_return_pct")) is not None
    value_type = str(record.get("value_type") or "return_pct").strip().lower()
    if value_type == "level":
        return safe_float(record.get("value")) is not None
    return (
        safe_float(record.get("return_pct")) is not None
        or safe_float(record.get("value")) is not None
    )


def market_bad_count(record: dict[str, Any]) -> int:
    statuses = record.get("market_status")
    if not isinstance(statuses, dict):
        return 999
    bad_statuses = {"pending", "missing", "stale", "failed"}
    return sum(1 for value in statuses.values() if str(value).strip().lower() in bad_statuses)


def choose_cache_record(
    ours: Any,
    theirs: Any,
    *,
    estimate: bool = False,
) -> Any:
    if not isinstance(ours, dict):
        return theirs
    if not isinstance(theirs, dict):
        return ours

    ours_has_value = record_has_value(ours, estimate=estimate)
    theirs_has_value = record_has_value(theirs, estimate=estimate)
    if theirs_has_value != ours_has_value:
        return theirs if theirs_has_value else ours

    ours_rank = cache_status_rank(ours.get("data_status") or ours.get("status"), estimate=estimate)
    theirs_rank = cache_status_rank(theirs.get("data_status") or theirs.get("status"), estimate=estimate)
    if theirs_rank != ours_rank:
        return theirs if theirs_rank > ours_rank else ours

    if estimate:
        ours_score = safe_float(ours.get("completeness_score"))
        theirs_score = safe_float(theirs.get("completeness_score"))
        ours_score = -1.0 if ours_score is None else ours_score
        theirs_score = -1.0 if theirs_score is None else theirs_score
        if abs(theirs_score - ours_score) > 1e-9:
            return theirs if theirs_score > ours_score else ours

        ours_bad = market_bad_count(ours)
        theirs_bad = market_bad_count(theirs)
        if theirs_bad != ours_bad:
            return theirs if theirs_bad < ours_bad else ours

    ours_time = latest_datetime_from_record(ours)
    theirs_time = latest_datetime_from_record(theirs)
    if ours_time and theirs_time and theirs_time != ours_time:
        return theirs if theirs_time > ours_time else ours
    if theirs_time and not ours_time:
        return theirs
    if ours_time and not theirs_time:
        return ours

    return theirs


def merge_dict_section(
    ours: dict[str, Any],
    theirs: dict[str, Any],
    *,
    estimate: bool = False,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key in sorted(set(ours) | set(theirs)):
        if key in ours and key in theirs:
            merged[key] = choose_cache_record(ours[key], theirs[key], estimate=estimate)
        elif key in theirs:
            merged[key] = theirs[key]
        else:
            merged[key] = ours[key]
    return merged


def merge_index_daily_csv(ours_text: str, theirs_text: str) -> str:
    def parse_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise SyncError("CSV 缓存缺少表头，无法自动合并。")
        rows = [dict(row) for row in reader if row.get(reader.fieldnames[0])]
        return list(reader.fieldnames), rows

    ours_fields, ours_rows = parse_csv(ours_text)
    theirs_fields, theirs_rows = parse_csv(theirs_text)
    fields = list(ours_fields)
    for field in theirs_fields:
        if field not in fields:
            fields.append(field)
    date_field = fields[0]

    merged_rows: dict[str, tuple[int, int, dict[str, str]]] = {}
    for side_order, rows in enumerate((ours_rows, theirs_rows)):
        for row in rows:
            date_value = str(row.get(date_field) or "").strip()
            if not date_value:
                continue
            completeness = sum(1 for field in fields if str(row.get(field) or "").strip())
            current = merged_rows.get(date_value)
            candidate = (completeness, side_order, {field: row.get(field, "") for field in fields})
            if current is None or candidate[:2] >= current[:2]:
                merged_rows[date_value] = candidate

    def date_sort_key(value: str) -> tuple[int, Any]:
        parsed = parse_datetime_like(value)
        return (0, parsed) if parsed else (1, value)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for date_value in sorted(merged_rows, key=date_sort_key):
        writer.writerow(merged_rows[date_value][2])
    return output.getvalue()


def merge_fund_estimate_cache(ours_text: str, theirs_text: str) -> str:
    ours = json.loads(ours_text)
    theirs = json.loads(theirs_text)
    if not isinstance(ours, dict) or not isinstance(theirs, dict):
        raise SyncError("基金估算缓存不是 JSON object，无法自动合并。")

    ours_time = parse_datetime_like(ours.get("updated_at"))
    theirs_time = parse_datetime_like(theirs.get("updated_at"))
    base = dict(theirs if (theirs_time or datetime.min) >= (ours_time or datetime.min) else ours)
    for key, value in ours.items():
        base.setdefault(key, value)

    base["records"] = merge_dict_section(
        ours.get("records") if isinstance(ours.get("records"), dict) else {},
        theirs.get("records") if isinstance(theirs.get("records"), dict) else {},
        estimate=True,
    )
    base["benchmark_records"] = merge_dict_section(
        ours.get("benchmark_records") if isinstance(ours.get("benchmark_records"), dict) else {},
        theirs.get("benchmark_records") if isinstance(theirs.get("benchmark_records"), dict) else {},
        estimate=False,
    )
    return json.dumps(base, ensure_ascii=False, indent=2) + "\n"


def merge_security_return_cache(ours_text: str, theirs_text: str) -> str:
    ours = json.loads(ours_text)
    theirs = json.loads(theirs_text)
    if not isinstance(ours, dict) or not isinstance(theirs, dict):
        raise SyncError("证券行情缓存不是 JSON object，无法自动合并。")
    merged = merge_dict_section(ours, theirs, estimate=False)
    return json.dumps(merged, ensure_ascii=False, indent=2) + "\n"


def merge_cache_conflict_file(repo: Path, path: str) -> None:
    ours_text = git_stage_text(repo, 2, path)
    theirs_text = git_stage_text(repo, 3, path)
    if CACHE_INDEX_DAILY_PATTERN.match(path):
        merged_text = merge_index_daily_csv(ours_text, theirs_text)
    elif path == "cache/fund_estimate_return_cache.json":
        merged_text = merge_fund_estimate_cache(ours_text, theirs_text)
    elif path == "cache/security_return_cache.json":
        merged_text = merge_security_return_cache(ours_text, theirs_text)
    else:
        raise SyncError(f"不支持自动合并的缓存文件: {path}")

    target = repo / Path(path)
    target.write_text(merged_text, encoding="utf-8", newline="")
    run_git(repo, ["add", path])
    log(f"已自动合并缓存冲突: {path}")


def merge_head_exists(repo: Path) -> bool:
    git_dir_text = git_output(repo, ["rev-parse", "--git-dir"])
    git_dir = Path(git_dir_text)
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    return (git_dir / "MERGE_HEAD").exists()


def resolve_cache_conflicts(repo: Path, *, commit_merge: bool) -> bool:
    repo = ensure_repo(repo)
    paths = unmerged_paths(repo)
    if not paths:
        log("当前没有未解决的 Git 冲突。")
        return False

    unsupported = [path for path in paths if not is_auto_merge_cache_path(path)]
    if unsupported:
        log("检测到非缓存冲突，停止自动处理：")
        for path in unsupported:
            log(f"  - {path}")
        raise SyncError("存在非运行缓存冲突，请人工解决。")

    step("自动合并运行缓存冲突")
    for path in paths:
        merge_cache_conflict_file(repo, path)

    remaining = unmerged_paths(repo)
    if remaining:
        raise SyncError("缓存自动合并后仍有未解决冲突: " + ", ".join(remaining))

    if commit_merge and merge_head_exists(repo):
        step("提交已解决的 merge")
        run_git(repo, ["commit", "--no-edit"])
    return True


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
        result = run_remote_git_with_retry(
            repo,
            ["fetch", remote, branch],
            action="fetch",
            label=label,
        )
        if result.returncode == 0:
            fetched_remotes.append(remote)
        else:
            remote_failures.append(remote_failure_message("fetch", label, result))

    if not fetched_remotes:
        raise SyncError("GitHub 和 Gitee 都拉取失败。" + "; ".join(remote_failures))

    try:
        for remote in fetched_remotes:
            label = "GitHub" if remote == github_remote else "Gitee"
            step(f"合并 {label} 最新提交到本地")
            result = run_git(repo, ["merge", "--no-edit", remote_ref(remote, branch)], check=False)
            if result.returncode == 0:
                continue
            if unmerged_paths(repo):
                log("检测到 merge 冲突，尝试按运行缓存白名单自动合并。")
                resolve_cache_conflicts(repo, commit_merge=True)
                continue
            raise SyncError(
                f"git merge --no-edit {remote_ref(remote, branch)} failed with "
                f"exit code {result.returncode}"
            )
    except SyncError:
        print(
            "\n合并已停止，通常是真实源码/配置冲突或不支持自动合并的缓存冲突。"
            "请手动解决冲突，然后执行：\n"
            "  git add <resolved files>\n"
            "  git commit\n"
            "  python sync_repos.py\n",
            file=sys.stderr,
        )
        raise

    for remote in (github_remote, gitee_remote):
        label = "GitHub" if remote == github_remote else "Gitee"
        step(f"推送同步结果到 {label}")
        result = run_remote_git_with_retry(
            repo,
            ["push", remote, f"HEAD:{branch}"],
            action="push",
            label=label,
        )
        if result.returncode != 0:
            remote_failures.append(remote_failure_message("push", label, result))

    refresh_failures: list[str] = []
    for remote in (github_remote, gitee_remote):
        label = "GitHub" if remote == github_remote else "Gitee"
        step(f"刷新 {label} 远程提交位置")
        result = run_remote_git_with_retry(
            repo,
            ["fetch", remote, branch],
            action="refresh",
            label=label,
        )
        if result.returncode != 0:
            refresh_failures.append(remote_failure_message("refresh", label, result))

    all_failures = [*remote_failures, *refresh_failures]
    final_commits = collect_final_refs(repo, branch, github_remote, gitee_remote)
    refs_aligned = final_refs_are_aligned(final_commits)
    print_final_refs(final_commits)
    if all_failures:
        if refs_aligned and not refresh_failures:
            log("仓库同步完成：本地、GitHub、Gitee 已对齐；中途出现过临时远程失败。")
            for failure in all_failures:
                log(f"[WARN] {failure}")
            return

        log("仓库同步部分完成：至少一个远程操作失败。")
        for failure in all_failures:
            log(f"[WARN] {failure}")
        if not refs_aligned:
            log("[ERROR] 最终提交位置未对齐，请检查远程网络或真实合并冲突。")
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
    parser.add_argument(
        "--resolve-cache-conflicts",
        action="store_true",
        help=(
            "Resolve the current in-progress merge only if all unmerged files "
            "are known runtime cache files. No fetch or push is performed."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if bool(args.resolve_cache_conflicts):
            resolve_cache_conflicts(Path(args.repo), commit_merge=True)
            return 0
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
