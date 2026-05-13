"""
git_main.py

AHNS 项目总控入口：
1. 运行 kepu/first_pic.py 生成第一张说明图；
2. 运行 main.py 生成市场和基金估算图；
3. 运行 safe_fund.py、safe_holidays.py、holidays.py、sum_holidays.py 生成公开展示图；
4. 运行 kepu 科普脚本，按日期条件生成节后说明图和每周限额图；
5. 北京时间 17:30 后切换为只运行 premarket_fund.py 盘前观察；
6. 收集本次新建或更新的图片并发送邮件。

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
PREMARKET_SCRIPT_NAME = "premarket_fund.py"
PREMARKET_ONLY_SWITCH_TIME_BJ = datetime_time(17, 30)
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
    `name` 用来给人看，`script_path` 用来真正运行，`required` 和
    `collect_images` 分别控制失败处理和邮件收图。
    """

    name: str
    script_path: Path
    required: bool
    collect_images: bool


@dataclass
class ScriptResult:
    step_name: str
    script_name: str
    script_path: Path
    return_code: int
    elapsed_seconds: float
    changed_images: list[Path]
    collect_images: bool

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


def resolve_workflow_steps() -> list[WorkflowStep]:
    """把配置文件里的脚本清单转换成可运行步骤。

    维护提示：
    - 如果这里报“缺少必要脚本”，一般是 `workflow_configs.py` 里的 `script`
      路径写错了。
    - 这里会拒绝绝对路径，是为了保证本地和 GitHub Actions 都能用同一份配置。
    """
    steps: list[WorkflowStep] = []
    missing: list[str] = []

    for index, item in enumerate(WORKFLOW_STEPS, start=1):
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

        if not resolved_script.exists():
            missing.append(f"{name}({script_text})")
            continue

        steps.append(
            WorkflowStep(
                name=name,
                script_path=resolved_script,
                required=required,
                collect_images=collect_images,
            )
        )

    if missing:
        raise FileNotFoundError("缺少必要脚本: " + "、".join(missing))

    return steps


def coerce_beijing_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=BJ_TZ)
    return value.astimezone(BJ_TZ)


def select_workflow_steps_for_time(
    steps: list[WorkflowStep],
    current_time: datetime | None = None,
) -> list[WorkflowStep]:
    """按北京时间 17:30 切换每日流程和盘前观察流程。"""
    now_bj = coerce_beijing_datetime(current_time or datetime.now(BJ_TZ))
    premarket_indexes = [
        index
        for index, step in enumerate(steps)
        if step.script_path.name.lower() == PREMARKET_SCRIPT_NAME
    ]

    if not premarket_indexes:
        raise ValueError(f"workflow_configs.py 缺少 {PREMARKET_SCRIPT_NAME} 步骤")
    if len(premarket_indexes) > 1:
        raise ValueError(f"workflow_configs.py 里存在多个 {PREMARKET_SCRIPT_NAME} 步骤")

    premarket_index = premarket_indexes[0]
    if now_bj.time() < PREMARKET_ONLY_SWITCH_TIME_BJ:
        return steps[:premarket_index]

    return [steps[premarket_index]]


def stream_script_output(script_path: Path) -> int:
    """运行单个脚本，并把子脚本输出实时打印出来。

    这里继续使用当前 Python 解释器，也就是你运行 git_main.py 时用的那个环境。
    因此本地建议仍然是：
    `& F:\\anaconda\\envs\\py310\\python.exe .\\git_main.py --no-send`
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    command = [sys.executable, str(script_path)]
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

    assert process.stdout is not None
    for line in process.stdout:
        print(f"[{script_path.name}] {line.rstrip()}", flush=True)

    return process.wait()


def run_script(step: WorkflowStep) -> ScriptResult:
    """运行一个配置步骤，并记录它本次生成或更新的图片。

    注意：有些脚本本来就不是每天都出图，例如 safe_holidays.py 和
    sum_holidays.py。只要退出码是 0，即使没有检测到新图片，也表示这一步正常完成。
    """
    script_path = step.script_path
    log(f"开始运行 {step.name}: {relative_text(script_path)}")
    before = snapshot_images()
    started = time.perf_counter()
    return_code = stream_script_output(script_path)
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
    )


def unique_images(results: Iterable[ScriptResult]) -> list[Path]:
    """汇总本次要发送的图片，并去重。

    只有 `collect_images=True` 且脚本成功的步骤才会进入邮件候选。这样你以后想让某个
    脚本“正常生成但不发邮件”，只需要改 workflow_configs.py，不用动邮件逻辑。
    """
    images: list[Path] = []
    seen: set[Path] = set()

    for result in results:
        if not result.success or not result.collect_images:
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

    lines.extend(["", "【本次发送图片】"])
    if images:
        for index, image in enumerate(images, start=1):
            lines.append(f"{index}. {relative_text(image)}")
    else:
        lines.append("本次未检测到新建或更新的图片。")

    lines.extend(["", "【提示】", RISK_NOTE])
    return "\n".join(lines)


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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started_at = datetime.now(BJ_TZ)

    log("git_main.py 开始运行")
    if args.no_send:
        log("当前为预演模式：会运行全部脚本，但不会发送邮件")

    steps = resolve_workflow_steps()
    now_bj = datetime.now(BJ_TZ)
    steps = select_workflow_steps_for_time(steps, current_time=now_bj)
    switch_text = PREMARKET_ONLY_SWITCH_TIME_BJ.strftime("%H:%M")
    log(f"当前北京时间: {now_bj.strftime('%Y-%m-%d %H:%M:%S')}")
    if now_bj.time() < PREMARKET_ONLY_SWITCH_TIME_BJ:
        log(f"流程切换规则: {switch_text} 前运行盘前观察之前的每日流程")
    else:
        log(f"流程切换规则: {switch_text} 后只运行盘前海外基金观察图")
    log("实际运行顺序: " + " -> ".join(step.name for step in steps))

    results: list[ScriptResult] = []
    for step in steps:
        result = run_script(step)
        results.append(result)

        if not result.success and step.required:
            raise RuntimeError(
                f"{step.name} 是 required=True 的必要步骤，已中断总流程。"
                "如果你确认这一步可以失败后继续，请在 workflow_configs.py 里改 required=False。"
            )

        if not result.success:
            log(f"[WARN] {step.name} 是非必要步骤，失败后继续运行后续步骤")

    images = unique_images(results)
    finished_at = datetime.now(BJ_TZ)
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

    if not images:
        log("本次没有可发送图片，跳过邮件发送")
        return 0

    if args.no_send:
        log("预演模式结束，未发送邮件")
        print("\n" + email_text, flush=True)
        return 0

    subject = f"AHNS 每日市场图自动生成 - {finished_at.strftime('%Y-%m-%d %H:%M')}"
    log(f"开始发送邮件：图片 {len(images)} 张，总大小 {format_file_size(image_total_size)}")
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
        raise

    log(f"邮件发送完成，耗时 {format_duration(time.perf_counter() - email_started)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"[ERROR] {exc}")
        raise SystemExit(1)
