"""
git_main.py

AHNS 项目总控入口：
1. 运行 main.py 生成市场和基金估算图；
2. 运行 safe_fund.py、safe_holidays.py、sum_holidays.py 生成公开展示图；
3. 运行 kepu 科普脚本，按日期条件生成节后说明图和每周限额图；
4. 按 workflow_configs.py 中的 run_window_bj 追加实时观察流程；
5. 收集本次新建或更新的图片并发送邮件。

本地默认使用 tools.email_send.py 中的邮箱配置；GitHub Actions 可通过
QQ_EMAIL_ACCOUNT、QQ_EMAIL_AUTH_CODE、QQ_EMAIL_RECEIVER 环境变量覆盖。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as datetime_time
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from tools.email_send import send_email
from tools.configs.workflow_configs import WORKFLOW_STEPS
from tools.paths import OUTPUT_DIR, PROJECT_ROOT, relative_path_str


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
BJ_TZ = ZoneInfo("Asia/Shanghai")
SCRIPT_OUTPUT_TAIL_LINES = 80
RISK_NOTE = (
    "个人公开数据建模复盘，不收费、不荐基、不带单、不拉群，不构成任何投资建议。\n"
    "非实时净值，最终以基金公司公告为准。"
)


@dataclass
class ImageState:
    mtime_ns: int
    size: int


@dataclass
class WorkflowStep:
    """总入口中的一个脚本步骤。

    这里把配置文件里的普通字典转换成结构化对象，是为了让后面的代码更清楚：
    `name` 用来给人看，`script_path` 用来真正运行，`required` 用于
    标记必要性日志，`collect_images` 控制邮件收图。
    """

    name: str
    script_path: Path
    required: bool
    collect_images: bool
    args: tuple[str, ...]
    run_window_start_bj: datetime_time | None = None
    run_window_end_bj: datetime_time | None = None

    @property
    def has_run_window(self) -> bool:
        return self.run_window_start_bj is not None and self.run_window_end_bj is not None

    @property
    def run_window_text(self) -> str:
        if not self.has_run_window:
            return "全天"
        assert self.run_window_start_bj is not None
        assert self.run_window_end_bj is not None
        return f"{self.run_window_start_bj.strftime('%H:%M')}-{self.run_window_end_bj.strftime('%H:%M')}"


@dataclass
class ScriptResult:
    step_name: str
    script_name: str
    script_path: Path
    return_code: int
    elapsed_seconds: float
    changed_images: list[Path]
    collect_images: bool
    output_tail: list[str]
    error_message: str = ""

    @property
    def success(self) -> bool:
        return self.return_code == 0


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def relative_text(path: Path) -> str:
    return relative_path_str(path.resolve())


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"

    minutes = int(seconds // 60)
    remain = int(round(seconds % 60))
    return f"{minutes}m {remain:02d}s"


def format_file_size(size_bytes: int) -> str:
    mb = size_bytes / 1024 / 1024
    if mb >= 1:
        return f"{mb:.2f} MB"

    kb = size_bytes / 1024
    return f"{kb:.1f} KB"


def total_file_size(paths: Iterable[Path]) -> int:
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def snapshot_images(output_dir: Path = OUTPUT_DIR) -> dict[Path, ImageState]:
    """记录 output/ 目录中每张图片的修改时间和大小。

    git_main.py 不提前规定“每个脚本一定会生成哪张图”，而是运行脚本前后各拍一次
    快照，再比较哪些图片发生了变化。这样节假日图、限额图这类“有条件才出图”的
    脚本也能自然处理：今天没出图就是没有变化，不代表报错。
    """
    if not output_dir.exists():
        return {}

    snapshot: dict[Path, ImageState] = {}
    for path in output_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue

        stat = path.stat()
        snapshot[path.resolve()] = ImageState(mtime_ns=stat.st_mtime_ns, size=stat.st_size)

    return snapshot


def changed_images(
    before: dict[Path, ImageState],
    after: dict[Path, ImageState],
) -> list[Path]:
    """比较两次快照，找出本次新建或更新的图片。"""
    changed = [
        path
        for path, state in after.items()
        if before.get(path) != state
    ]
    return sorted(changed, key=lambda item: (after[item].mtime_ns, str(item).lower()))


def parse_hhmm_time(value: object) -> datetime_time:
    text = str(value or "").strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return datetime_time(hour, minute)
    except Exception as exc:
        raise ValueError(f"运行窗口时间格式必须是 HH:MM，当前为: {text!r}") from exc


def parse_run_window_bj(value: object) -> tuple[datetime_time | None, datetime_time | None]:
    if value in (None, "", False):
        return None, None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"run_window_bj 必须是 (start, end)，当前为: {value!r}")
    return parse_hhmm_time(value[0]), parse_hhmm_time(value[1])


def resolve_workflow_steps(
    workflow_steps: Iterable[dict[str, object]] | None = None,
) -> list[WorkflowStep]:
    """把配置文件里的脚本清单转换成可运行步骤。

    维护提示：
    - 如果这里报“缺少必要脚本”，一般是 `workflow_configs.py` 里的 `script`
      路径写错了。
    - 这里会拒绝绝对路径，是为了保证本地和 GitHub Actions 都能用同一份配置。
    """
    steps: list[WorkflowStep] = []
    missing: list[str] = []
    raw_steps = WORKFLOW_STEPS if workflow_steps is None else workflow_steps

    for index, item in enumerate(raw_steps, start=1):
        name = str(item.get("name") or f"步骤 {index}")
        script_text = str(item.get("script") or "").strip()
        if not script_text:
            raise ValueError(f"workflow_configs.py 第 {index} 项缺少 script")

        script_path = Path(script_text)
        if script_path.is_absolute():
            raise ValueError(
                f"workflow_configs.py 第 {index} 项 script 不要写绝对路径: {script_text}"
            )

        resolved_script = PROJECT_ROOT / script_path
        required = bool(item.get("required", True))
        collect_images = bool(item.get("collect_images", True))
        args_raw = item.get("args") or []
        if isinstance(args_raw, str):
            args = (args_raw,)
        else:
            args = tuple(str(arg) for arg in args_raw)
        run_window_start_bj, run_window_end_bj = parse_run_window_bj(item.get("run_window_bj"))

        if not resolved_script.exists():
            missing.append(f"{name}({script_text})")
            continue

        steps.append(
            WorkflowStep(
                name=name,
                script_path=resolved_script,
                required=required,
                collect_images=collect_images,
                args=args,
                run_window_start_bj=run_window_start_bj,
                run_window_end_bj=run_window_end_bj,
            )
        )

    if missing:
        raise FileNotFoundError("缺少必要脚本: " + "、".join(missing))

    return steps


def coerce_beijing_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=BJ_TZ)
    return value.astimezone(BJ_TZ)


def time_in_closed_window(current: datetime_time, start: datetime_time, end: datetime_time) -> bool:
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def select_workflow_steps_for_time(
    steps: list[WorkflowStep],
    current_time: datetime | None = None,
) -> list[WorkflowStep]:
    """按配置中的北京时间窗口追加实时观察步骤。

    无运行窗口的步骤属于日常完整流程，始终运行；带窗口的步骤只在命中
    对应北京时间窗口时追加运行。
    """
    now_bj = coerce_beijing_datetime(current_time or datetime.now(BJ_TZ))
    current = now_bj.time().replace(microsecond=0)
    matching_window_steps = [
        step
        for step in steps
        if step.has_run_window
        and step.run_window_start_bj is not None
        and step.run_window_end_bj is not None
        and time_in_closed_window(current, step.run_window_start_bj, step.run_window_end_bj)
    ]
    return [step for step in steps if not step.has_run_window or step in matching_window_steps]


def stream_script_output(script_path: Path, args: tuple[str, ...] = ()) -> tuple[int, list[str]]:
    """运行单个脚本，并把子脚本输出实时打印出来。

    这里继续使用当前 Python 解释器，也就是你运行 git_main.py 时用的那个环境。
    因此本地建议仍然是：
    `& F:\\anaconda\\envs\\py310\\python.exe .\\git_main.py --no-send`
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    command = [sys.executable, str(script_path), *args]
    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    output_tail: list[str] = []

    assert process.stdout is not None
    for line in process.stdout:
        text = line.rstrip()
        print(f"[{script_path.name}] {text}", flush=True)
        output_tail.append(text)
        if len(output_tail) > SCRIPT_OUTPUT_TAIL_LINES:
            output_tail = output_tail[-SCRIPT_OUTPUT_TAIL_LINES:]

    return process.wait(), output_tail


def run_script(step: WorkflowStep) -> ScriptResult:
    """运行一个配置步骤，并记录它本次生成或更新的图片。

    注意：有些脚本本来就不是每天都出图，例如 safe_holidays.py 和
    sum_holidays.py。只要退出码是 0，即使没有检测到新图片，也表示这一步正常完成。
    """
    script_path = step.script_path
    arg_text = "" if not step.args else " " + " ".join(step.args)
    log(f"开始运行 {step.name}: {relative_text(script_path)}{arg_text}")
    before = snapshot_images()
    started = time.perf_counter()
    output_tail: list[str] = []
    error_message = ""
    try:
        return_code, output_tail = stream_script_output(script_path, step.args)
    except Exception as exc:
        return_code = -1
        error_message = repr(exc)
        log(f"{step.name} 启动或运行监控异常: {error_message}")
    elapsed = time.perf_counter() - started
    after = snapshot_images()
    images = changed_images(before, after)

    if return_code != 0:
        log(
            f"{step.name} 运行失败，退出码 {return_code}，耗时 {format_duration(elapsed)}"
        )
    elif images:
        log(
            f"{step.name} 运行完成，耗时 {format_duration(elapsed)}，"
            f"生成或更新图片 {len(images)} 张"
        )
        for image in images:
            log(f"  - {relative_text(image)}")
    else:
        log(f"{step.name} 运行完成，耗时 {format_duration(elapsed)}，本次未检测到新图片")

    if images and not step.collect_images:
        log(f"{step.name} 的 collect_images=False，本次图片只生成，不加入邮件候选")

    return ScriptResult(
        step_name=step.name,
        script_name=script_path.name,
        script_path=script_path,
        return_code=return_code,
        elapsed_seconds=elapsed,
        changed_images=images,
        collect_images=step.collect_images,
        output_tail=output_tail,
        error_message=error_message,
    )


def unique_images(results: Iterable[ScriptResult]) -> list[Path]:
    """汇总本次要发送的图片，并去重。

    只要 `collect_images=True` 就进入邮件候选，即使脚本最终失败，也把它已经生成
    或更新的图片同步发出去，方便和失败日志一起排查。
    """
    images: list[Path] = []
    seen: set[Path] = set()

    for result in results:
        if not result.collect_images:
            continue

        for image in result.changed_images:
            resolved = image.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            images.append(resolved)

    return images


def build_email_text(
    *,
    started_at: datetime,
    finished_at: datetime,
    results: list[ScriptResult],
    images: list[Path],
) -> str:
    lines = [
        "【AHNS 每日市场图自动生成】",
        f"开始时间：{started_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"完成时间：{finished_at.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "【运行结果】",
    ]

    for result in results:
        status = "成功" if result.success else f"失败(退出码 {result.return_code})"
        collect_note = "" if result.collect_images else "，不纳入邮件图片"
        lines.append(
            f"- {result.step_name}({result.script_name}): {status}，"
            f"耗时 {format_duration(result.elapsed_seconds)}，"
            f"生成或更新图片 {len(result.changed_images)} 张{collect_note}"
        )

    changed_image_logs = format_changed_image_logs(results)
    if changed_image_logs:
        lines.extend(["", *changed_image_logs])

    failure_logs = format_failure_logs(results)
    if failure_logs:
        lines.extend(["", *failure_logs])

    lines.extend(["", "【本次发送图片】"])
    if images:
        for index, image in enumerate(images, start=1):
            lines.append(f"{index}. {relative_text(image)}")
    else:
        lines.append("本次没有纳入邮件发送的图片。")

    lines.extend(["", "【提示】", RISK_NOTE])
    return "\n".join(lines)


def format_changed_image_logs(results: Iterable[ScriptResult]) -> list[str]:
    lines = ["【本次生成/更新图片】"]
    found = False
    for result in results:
        if not result.changed_images:
            continue
        found = True
        send_note = "纳入邮件" if result.collect_images else "仅生成，不纳入邮件"
        status = "成功" if result.success else f"失败(退出码 {result.return_code})"
        lines.append(f"- {result.step_name}({result.script_name})：{status}，{send_note}")
        for image in result.changed_images:
            lines.append(f"  {relative_text(image)}")

    if not found:
        lines.append("本次未检测到新建或更新的图片。")
    return lines


def format_failure_logs(results: Iterable[ScriptResult]) -> list[str]:
    failed_results = [result for result in results if not result.success]
    if not failed_results:
        return []

    lines = ["【失败日志】"]
    for result in failed_results:
        lines.append(
            f"- {result.step_name}({result.script_name}) 退出码 {result.return_code}，"
            f"耗时 {format_duration(result.elapsed_seconds)}"
        )
        if result.error_message:
            lines.append(f"  监控异常：{result.error_message}")
        if result.output_tail:
            lines.append(f"  最近 {len(result.output_tail)} 行输出：")
            for item in result.output_tail:
                lines.append(f"    {item}")
        else:
            lines.append("  无子脚本输出。")
    return lines


def print_failure_logs(results: Iterable[ScriptResult]) -> None:
    failure_logs = format_failure_logs(results)
    if not failure_logs:
        log("全部脚本运行成功，未记录失败日志")
        return

    print("\n" + "\n".join(failure_logs), flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 AHNS 全流程并发送本次生成的图片")
    parser.add_argument(
        "--no-send",
        action="store_true",
        help="只运行脚本并展示将发送的图片，不发送邮件",
    )
    parser.add_argument(
        "--receiver",
        default=None,
        help="临时指定收件邮箱；不传则使用 QQ_EMAIL_RECEIVER 或本地默认收件人",
    )
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    entry_name: str = "git_main.py",
    workflow_label: str = "GitHub Actions",
    workflow_steps: Iterable[dict[str, object]] | None = None,
) -> int:
    args = parse_args(argv)
    started_at = datetime.now(BJ_TZ)

    log(f"{entry_name} 开始运行，当前流程: {workflow_label}")
    if args.no_send:
        log("当前为预演模式：会运行全部脚本，但不会发送邮件")

    steps = resolve_workflow_steps(workflow_steps)
    now_bj = datetime.now(BJ_TZ)
    all_steps = steps
    steps = select_workflow_steps_for_time(steps, current_time=now_bj)
    log(f"当前北京时间: {now_bj.strftime('%Y-%m-%d %H:%M:%S')}")
    configured_windows = [
        f"{step.name}:{step.run_window_text}"
        for step in all_steps
        if step.has_run_window
    ]
    if configured_windows:
        log("配置化实时观察窗口: " + "；".join(configured_windows))
    daily_steps = [step for step in steps if not step.has_run_window]
    realtime_steps = [step for step in steps if step.has_run_window]
    if realtime_steps:
        log(
            "当前命中实时观察窗口，完整日流程照常运行，并追加实时观察步骤: "
            + " -> ".join(step.name for step in realtime_steps)
        )
    else:
        log("当前未命中实时观察窗口，仅运行完整日流程/非窗口步骤")
    if daily_steps:
        log("完整日流程步骤: " + " -> ".join(step.name for step in daily_steps))
    log("实际运行顺序: " + " -> ".join(step.name for step in steps))

    results: list[ScriptResult] = []
    for step in steps:
        result = run_script(step)
        results.append(result)

        if not result.success:
            required_note = "必要步骤" if step.required else "非必要步骤"
            log(f"[WARN] {step.name} 是{required_note}，失败已记录，继续运行后续步骤")

    images = unique_images(results)
    finished_at = datetime.now(BJ_TZ)
    has_failures = any(not result.success for result in results)
    email_text = build_email_text(
        started_at=started_at,
        finished_at=finished_at,
        results=results,
        images=images,
    )

    image_total_size = total_file_size(images)
    log(f"本次共检测到新建或更新图片 {len(images)} 张，总大小 {format_file_size(image_total_size)}")
    for image in images:
        try:
            size_text = format_file_size(image.stat().st_size)
        except OSError:
            size_text = "大小未知"
        log(f"待发送图片: {relative_text(image)} ({size_text})")

    if not images and not has_failures:
        log("本次没有可发送图片，跳过邮件发送")
        print_failure_logs(results)
        return 0

    if args.no_send:
        log("预演模式结束，未发送邮件")
        print("\n" + email_text, flush=True)
        return 1 if has_failures else 0

    subject_status = "部分失败" if has_failures else "成功"
    subject = f"AHNS 每日市场图自动生成({subject_status}) - {finished_at.strftime('%Y-%m-%d %H:%M')}"
    if images:
        log(f"开始发送邮件：图片 {len(images)} 张，总大小 {format_file_size(image_total_size)}")
    else:
        log("检测到失败日志但没有可发送图片，发送纯文本错误日志邮件")
    email_started = time.perf_counter()
    try:
        send_email(
            subject=subject,
            text=email_text,
            image_paths=images,
            to_email=args.receiver,
            embed_images=True,
            attach_images=True,
            timeout=240,
        )
    except Exception:
        log(
            "邮件发送失败：如果 SMTP 登录正常，常见原因是邮件体积较大、网络较慢或服务端中途断开。"
            "当前仍按“正文内嵌 + 附件”发送，可稍后重试。"
        )
        print_failure_logs(results)
        raise

    log(f"邮件发送完成，耗时 {format_duration(time.perf_counter() - email_started)}")
    print_failure_logs(results)
    return 1 if has_failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"[ERROR] {exc}")
        raise SystemExit(1)
