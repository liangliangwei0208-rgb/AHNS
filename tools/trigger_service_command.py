"""
GitHub Actions helper for requesting one small-server AHNS service run.

The workflow stays intentionally small: this module updates service_command.json,
commits it on top of Gitee main, tries several Git push variants, and finally
falls back to the Gitee contents API if Git push is unavailable from GitHub's
runner network.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMMAND_FILE = PROJECT_ROOT / "service_command.json"
DEFAULT_GITEE_REPOSITORY = "https://gitee.com/liangliang2000/AHNS.git"
DEFAULT_GITEE_API_URL = (
    "https://gitee.com/api/v5/repos/liangliang2000/AHNS/contents/service_command.json"
)
DEFAULT_GITEE_USERNAME = "liangliang2000"
DEFAULT_BRANCH = "main"
GITEE_REMOTE_NAME = "gitee-command"
COMMIT_MESSAGE = "Request service run from GitHub [skip ci]"
BJ_TZ = timezone(timedelta(hours=8))


class TriggerCommandError(RuntimeError):
    """Readable workflow helper error."""


def log(message: str) -> None:
    print(f"[TRIGGER-SERVICE] {message}", flush=True)


def mask_secret(secret: str) -> None:
    if secret:
        print(f"::add-mask::{secret}", flush=True)


def truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_optional_fund_code(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    digits = "".join(char for char in text if char.isdigit())
    if not digits:
        raise TriggerCommandError(f"holding_fund_code must contain digits, got: {text!r}")
    if len(digits) > 6:
        raise TriggerCommandError(f"holding_fund_code must be one 6-digit fund code, got: {text!r}")
    return digits.zfill(6)


def now_bj_iso() -> str:
    return datetime.now(tz=BJ_TZ).isoformat(timespec="seconds")


def run(
    args: Sequence[str],
    *,
    cwd: Path = PROJECT_ROOT,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    log("$ " + " ".join(args))
    result = subprocess.run(
        list(args),
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    if check and result.returncode != 0:
        raise TriggerCommandError(
            f"{' '.join(args)} failed with exit code {result.returncode}"
        )
    return result


def git(args: Sequence[str], *, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], check=check, env=env)


def git_output(args: Sequence[str], *, env: dict[str, str] | None = None) -> str:
    log("$ git " + " ".join(args))
    result = subprocess.run(
        ["git", *args],
        cwd=str(PROJECT_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise TriggerCommandError(
            f"git {' '.join(args)} failed with exit code {result.returncode}: {detail}"
        )
    return (result.stdout or "").strip()


def build_git_env(username: str, token: str) -> dict[str, str]:
    askpass_path = Path(tempfile.gettempdir()) / "ahns-gitee-askpass.sh"
    askpass_path.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        f"  *Username*) printf '%s\\n' '{username}' ;;\n"
        "  *Password*) printf '%s\\n' \"${GITEE_TOKEN}\" ;;\n"
        "  *) printf '\\n' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    try:
        askpass_path.chmod(0o700)
    except OSError:
        # Windows local tests do not need executable chmod.
        pass

    env = os.environ.copy()
    env["GIT_ASKPASS"] = str(askpass_path)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GITEE_TOKEN"] = token
    return env


def safe_remote_url(remote_url: str) -> str:
    return remote_url.replace("\n", "").strip()


def configure_gitee_remote(remote_url: str, git_env: dict[str, str]) -> None:
    git(["remote", "remove", GITEE_REMOTE_NAME], check=False, env=git_env)
    git(["remote", "add", GITEE_REMOTE_NAME, safe_remote_url(remote_url)], env=git_env)


def checkout_latest_gitee(branch: str, git_env: dict[str, str]) -> None:
    log(f"Fetching latest Gitee {branch}.")
    git(["-c", "http.version=HTTP/1.1", "fetch", "--verbose", GITEE_REMOTE_NAME, branch], env=git_env)
    git(["checkout", "-B", "gitee-command", "FETCH_HEAD"], env=git_env)
    log(f"Loaded Gitee {branch} at {git_output(['rev-parse', '--short=12', 'HEAD'], env=git_env)}.")


def command_payload(
    *,
    no_send: bool,
    receiver: str,
    holding_fund_code: str,
    message: str,
    actor: str,
    run_id: str,
) -> dict[str, object]:
    requested_at = now_bj_iso()
    request_message = f"requested from GitHub Actions by {actor or 'unknown'}, run_id={run_id or 'unknown'}"
    if message:
        request_message = f"{request_message}: {message}"
    return {
        "run_flag": 1,
        "no_send": bool(no_send),
        "receiver": receiver.strip(),
        "holding_change_fund_code": holding_fund_code,
        "status": "requested",
        "requested_at_bj": requested_at,
        "last_message": request_message[:500],
    }


def update_command_file(
    command_file: Path,
    *,
    no_send: bool,
    receiver: str,
    holding_fund_code: str,
    message: str,
    actor: str,
    run_id: str,
) -> dict[str, object]:
    try:
        command = json.loads(command_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TriggerCommandError(f"Command file not found: {command_file}") from exc
    except json.JSONDecodeError as exc:
        raise TriggerCommandError(f"Command file is not valid JSON: {exc}") from exc
    if not isinstance(command, dict):
        raise TriggerCommandError("Command file root must be a JSON object.")

    updates = command_payload(
        no_send=no_send,
        receiver=receiver,
        holding_fund_code=holding_fund_code,
        message=message,
        actor=actor,
        run_id=run_id,
    )
    command.update(updates)
    command_file.write_text(
        json.dumps(command, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    log(
        "Command updated: "
        f"run_flag=1, status=requested, no_send={updates['no_send']}, "
        f"receiver_set={bool(updates['receiver'])}, "
        f"holding_fund_code={updates['holding_change_fund_code'] or '(auto)'}, "
        f"requested_at_bj={updates['requested_at_bj']}"
    )
    return command


def commit_command_file(command_file: Path, git_env: dict[str, str]) -> bool:
    git(["config", "user.name", "github-actions[bot]"], env=git_env)
    git(["config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], env=git_env)
    git(["add", "--", command_file.relative_to(PROJECT_ROOT).as_posix()], env=git_env)
    diff_result = git(["diff", "--cached", "--quiet"], check=False, env=git_env)
    if diff_result.returncode == 0:
        log("service_command.json did not change; commit skipped.")
        return False
    git(["commit", "-m", COMMIT_MESSAGE], env=git_env)
    log(f"Command request commit: {git_output(['rev-parse', '--short=12', 'HEAD'], env=git_env)}.")
    return True


def push_attempt(label: str, args: Sequence[str], git_env: dict[str, str]) -> bool:
    print(f"::group::{label}", flush=True)
    result = git(args, check=False, env=git_env)
    if result.returncode == 0:
        log(f"{label}: success.")
        print("::endgroup::", flush=True)
        return True
    print(f"::warning::{label}: failed with exit code {result.returncode}", flush=True)
    print("::endgroup::", flush=True)
    return False


def recreate_commit_on_latest_gitee(
    *,
    branch: str,
    command_file: Path,
    git_env: dict[str, str],
    no_send: bool,
    receiver: str,
    holding_fund_code: str,
    message: str,
    actor: str,
    run_id: str,
) -> bool:
    print("::group::Refresh latest Gitee main and recreate command commit", flush=True)
    try:
        checkout_latest_gitee(branch, git_env)
        update_command_file(
            command_file,
            no_send=no_send,
            receiver=receiver,
            holding_fund_code=holding_fund_code,
            message=message,
            actor=actor,
            run_id=run_id,
        )
        created = commit_command_file(command_file, git_env)
        print("::endgroup::", flush=True)
        return created
    except Exception as exc:
        print(f"::warning::Could not recreate command commit on latest Gitee main: {exc}", flush=True)
        print("::endgroup::", flush=True)
        return False


def api_json_request(
    url: str,
    *,
    method: str = "GET",
    data: dict[str, str] | None = None,
    timeout: int = 60,
) -> dict[str, object]:
    encoded_data = None
    request_url = url
    if method.upper() == "GET" and data:
        request_url = f"{url}?{urllib.parse.urlencode(data)}"
    elif data is not None:
        encoded_data = urllib.parse.urlencode(data).encode("utf-8")

    request = urllib.request.Request(request_url, data=encoded_data, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise TriggerCommandError(f"Gitee API HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise TriggerCommandError(f"Gitee API network error: {exc}") from exc


def update_with_gitee_api(
    *,
    api_url: str,
    branch: str,
    token: str,
    command_file: Path,
    no_send: bool,
    receiver: str,
    holding_fund_code: str,
    message: str,
    actor: str,
    run_id: str,
) -> bool:
    print("::group::Fallback: update service_command.json through Gitee API", flush=True)
    try:
        current = api_json_request(
            api_url,
            data={"access_token": token, "ref": branch},
        )
        sha = str(current.get("sha") or "").strip()
        raw_content = str(current.get("content") or "")
        if not sha or not raw_content:
            raise TriggerCommandError("Gitee API response is missing sha or content.")

        decoded = base64.b64decode("".join(raw_content.split()))
        command_file.write_bytes(decoded)
        update_command_file(
            command_file,
            no_send=no_send,
            receiver=receiver,
            holding_fund_code=holding_fund_code,
            message=message,
            actor=actor,
            run_id=run_id,
        )
        encoded = base64.b64encode(command_file.read_bytes()).decode("ascii")
        updated = api_json_request(
            api_url,
            method="PUT",
            data={
                "access_token": token,
                "content": encoded,
                "message": COMMIT_MESSAGE,
                "branch": branch,
                "sha": sha,
                "committer[name]": "github-actions[bot]",
                "committer[email]": "41898282+github-actions[bot]@users.noreply.github.com",
            },
        )
        commit = updated.get("commit") if isinstance(updated, dict) else None
        commit_sha = commit.get("sha") if isinstance(commit, dict) else ""
        log(f"Gitee API update succeeded. commit={str(commit_sha)[:12] or 'unknown'}")
        print("::endgroup::", flush=True)
        return True
    except Exception as exc:
        print(f"::warning::Gitee API fallback failed: {exc}", flush=True)
        print("::endgroup::", flush=True)
        return False


def deliver_command_request(
    *,
    branch: str,
    command_file: Path,
    repository: str,
    username: str,
    token: str,
    api_url: str,
    no_send: bool,
    receiver: str,
    holding_fund_code: str,
    message: str,
    actor: str,
    run_id: str,
) -> None:
    mask_secret(token)
    if not token:
        raise TriggerCommandError("Missing GitHub Actions secret: GITEE_PRIVATE_CODE")

    log(f"Actor: {actor or 'unknown'}")
    log(f"GitHub run id: {run_id or 'unknown'}")
    log(f"no_send: {no_send}")
    log(f"receiver: {'provided' if receiver else 'default'}")
    log(f"holding_fund_code: {holding_fund_code or '(auto)'}")
    log(f"Gitee repository: {repository}")

    git_env = build_git_env(username, token)
    configure_gitee_remote(repository, git_env)
    checkout_latest_gitee(branch, git_env)
    update_command_file(
        command_file,
        no_send=no_send,
        receiver=receiver,
        holding_fund_code=holding_fund_code,
        message=message,
        actor=actor,
        run_id=run_id,
    )
    commit_created = commit_command_file(command_file, git_env)
    if not commit_created:
        return

    attempts = [
        ("Push attempt 1: default HTTPS", ["push", GITEE_REMOTE_NAME, f"HEAD:{branch}"]),
        (
            "Push attempt 2: force HTTP/1.1",
            ["-c", "http.version=HTTP/1.1", "push", GITEE_REMOTE_NAME, f"HEAD:{branch}"],
        ),
    ]
    for label, args in attempts:
        if push_attempt(label, args, git_env):
            return
        time.sleep(5)

    recreate_commit_on_latest_gitee(
        branch=branch,
        command_file=command_file,
        git_env=git_env,
        no_send=no_send,
        receiver=receiver,
        holding_fund_code=holding_fund_code,
        message=message,
        actor=actor,
        run_id=run_id,
    )
    if push_attempt(
        "Push attempt 3: refreshed latest Gitee main",
        ["-c", "http.version=HTTP/1.1", "push", GITEE_REMOTE_NAME, f"HEAD:{branch}"],
        git_env,
    ):
        return

    time.sleep(10)
    if push_attempt(
        "Push attempt 4: relaxed HTTP timeout guard",
        [
            "-c",
            "http.version=HTTP/1.1",
            "-c",
            "http.lowSpeedLimit=0",
            "-c",
            "http.lowSpeedTime=999999",
            "push",
            GITEE_REMOTE_NAME,
            f"HEAD:{branch}",
        ],
        git_env,
    ):
        return

    if update_with_gitee_api(
        api_url=api_url,
        branch=branch,
        token=token,
        command_file=command_file,
        no_send=no_send,
        receiver=receiver,
        holding_fund_code=holding_fund_code,
        message=message,
        actor=actor,
        run_id=run_id,
    ):
        return

    raise TriggerCommandError(
        "Failed to deliver service_command.json request to Gitee after all Git and API attempts."
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Request a small-server AHNS run by updating Gitee service_command.json."
    )
    parser.add_argument("--branch", default=os.environ.get("GITEE_BRANCH", DEFAULT_BRANCH))
    parser.add_argument("--command-file", default=str(DEFAULT_COMMAND_FILE))
    parser.add_argument("--gitee-repository", default=os.environ.get("GITEE_REPOSITORY", DEFAULT_GITEE_REPOSITORY))
    parser.add_argument("--gitee-api-url", default=os.environ.get("GITEE_API_URL", DEFAULT_GITEE_API_URL))
    parser.add_argument("--gitee-username", default=os.environ.get("GITEE_USERNAME", DEFAULT_GITEE_USERNAME))
    parser.add_argument("--token-env", default="GITEE_TOKEN")
    parser.add_argument("--no-send", default=os.environ.get("INPUT_NO_SEND", "false"))
    parser.add_argument("--receiver", default=os.environ.get("INPUT_RECEIVER", ""))
    parser.add_argument("--holding-fund-code", default=os.environ.get("INPUT_HOLDING_FUND_CODE", ""))
    parser.add_argument("--message", default=os.environ.get("INPUT_MESSAGE", ""))
    parser.add_argument("--actor", default=os.environ.get("GITHUB_ACTOR", "unknown"))
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "unknown"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        deliver_command_request(
            branch=str(args.branch),
            command_file=Path(args.command_file).resolve(),
            repository=str(args.gitee_repository),
            username=str(args.gitee_username),
            token=os.environ.get(str(args.token_env), ""),
            api_url=str(args.gitee_api_url),
            no_send=truthy(args.no_send),
            receiver=str(args.receiver or ""),
            holding_fund_code=normalize_optional_fund_code(args.holding_fund_code),
            message=str(args.message or ""),
            actor=str(args.actor or ""),
            run_id=str(args.run_id or ""),
        )
    except Exception as exc:
        print(f"::error::{exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
