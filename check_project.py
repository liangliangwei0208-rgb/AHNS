"""
AHNS 项目运行前自检工具。

这个脚本适合你每天正式运行前先执行一次：

    & F:\\anaconda\\envs\\py310\\python.exe .\\check_project.py

它只做检查和提示，不会联网、不拉行情、不生成图片、不写缓存、不发邮件、
不删除文件，也不会帮你自动提交 Git。看到 [WARN] 不一定代表不能运行，
但说明有些地方值得你留意；看到 [ERROR] 则建议先修好再跑主流程。
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tools.configs.workflow_configs import WORKFLOW_STEPS
from tools.paths import (
    CACHE_DIR,
    FUND_ESTIMATE_CACHE,
    FUND_HOLDINGS_CACHE,
    FUND_PURCHASE_LIMIT_CACHE,
    MARK_IMAGE,
    OUTPUT_DIR,
    PROJECT_ROOT,
    SECURITY_RETURN_CACHE,
    relative_path_str,
)


@dataclass
class CheckItem:
    """一条自检结果。

    level 只使用 OK/WARN/ERROR 三种，方便你一眼判断：
    - OK: 当前检查正常。
    - WARN: 不一定影响运行，但建议留意。
    - ERROR: 大概率会影响运行，建议先处理。
    """

    level: str
    title: str
    detail: str


def make_item(level: str, title: str, detail: str) -> CheckItem:
    return CheckItem(level=level, title=title, detail=detail)


def print_section(title: str, description: str) -> None:
    print(f"\n【{title}】")
    print(description)


def print_item(item: CheckItem) -> None:
    print(f"[{item.level}] {item.title}: {item.detail}")


def check_path_exists(path: Path, title: str, *, required: bool = True) -> CheckItem:
    """检查关键文件或目录是否存在。

    required=True 的项目缺失时会给 ERROR；required=False 的项目缺失时只给 WARN。
    例如 cache/mark.jpg 缺失不会导致 Python 直接崩，但 safe 图会少一个 logo 水印。
    """
    if path.exists():
        return make_item("OK", title, relative_path_str(path))

    level = "ERROR" if required else "WARN"
    return make_item(level, title, f"未找到 {relative_path_str(path)}")


def check_python_runtime() -> list[CheckItem]:
    """检查当前 Python 解释器。

    这个项目默认使用 F:\\anaconda\\envs\\py310\\python.exe。这里不强制路径完全一样，
    但会提醒你当前实际用的是哪个 Python，方便排查“VSCode 能跑、Actions 不能跑”
    这类环境差异。
    """
    version = ".".join(str(part) for part in sys.version_info[:3])
    items = [
        make_item("OK", "Python 版本", version),
        make_item("OK", "Python 路径", sys.executable),
    ]
    if sys.version_info < (3, 10):
        items.append(make_item("ERROR", "Python 版本过低", "建议使用 Python 3.10 或以上"))
    return items


def check_runtime_paths() -> list[CheckItem]:
    """检查项目目录和运行目录。

    cache/ 是行情和基金缓存，output/ 是出图目录。这个自检脚本不会主动创建目录，
    因为它的定位是“体检表”，不是“自动修复器”。
    """
    return [
        check_path_exists(PROJECT_ROOT, "项目根目录"),
        check_path_exists(CACHE_DIR, "缓存目录 cache/"),
        check_path_exists(OUTPUT_DIR, "输出目录 output/"),
    ]


def check_key_files() -> list[CheckItem]:
    """检查最常见的本地资源和缓存。

    cache/mark.jpg 是公开 safe 图的居中 logo 水印，准备上传 GitHub Actions 时也要
    确认它能随仓库一起存在。几个 JSON 缓存缺失时，部分只读脚本可能无法生成图片，
    但可以先运行 main.py 重新生成。
    """
    return [
        check_path_exists(MARK_IMAGE, "safe 图 logo 水印 cache/mark.jpg", required=False),
        check_path_exists(FUND_ESTIMATE_CACHE, "基金估算缓存", required=False),
        check_path_exists(FUND_HOLDINGS_CACHE, "基金持仓缓存", required=False),
        check_path_exists(FUND_PURCHASE_LIMIT_CACHE, "基金限购缓存", required=False),
        check_path_exists(SECURITY_RETURN_CACHE, "证券涨跌幅缓存", required=False),
    ]


def check_email_config() -> list[CheckItem]:
    """检查邮件配置是否看起来可用。

    这里只检查环境变量或本地配置文件是否存在，不会读取或打印真实授权码。
    GitHub Actions 里要用 Repository secrets 映射出这些环境变量；本地则可以继续用
    未跟踪的 tools/email_local_config.py。
    """
    account = os.environ.get("QQ_EMAIL_ACCOUNT")
    auth_code = os.environ.get("QQ_EMAIL_AUTH_CODE")
    receiver = os.environ.get("QQ_EMAIL_RECEIVER")
    local_config = PROJECT_ROOT / "tools" / "email_local_config.py"

    items: list[CheckItem] = []
    if account and auth_code:
        detail = "已检测到 QQ_EMAIL_ACCOUNT 和 QQ_EMAIL_AUTH_CODE"
        if receiver:
            detail += "，并设置了 QQ_EMAIL_RECEIVER"
        else:
            detail += "，未设置 QQ_EMAIL_RECEIVER 时通常会发给自己"
        items.append(make_item("OK", "邮箱环境变量", detail))
    else:
        items.append(
            make_item(
                "WARN",
                "邮箱环境变量",
                "未同时检测到 QQ_EMAIL_ACCOUNT 和 QQ_EMAIL_AUTH_CODE",
            )
        )

    if local_config.exists():
        items.append(make_item("OK", "本地邮箱配置", relative_path_str(local_config)))
    else:
        items.append(
            make_item(
                "WARN",
                "本地邮箱配置",
                "未找到 tools/email_local_config.py；如果用环境变量发邮件，可忽略",
            )
        )

    return items


def check_dependencies() -> list[CheckItem]:
    """检查关键依赖能否导入。

    这里只做 import，不会联网请求数据。Pillow 的安装名叫 pillow，但导入名是 PIL，
    所以这里专门写成 `PIL`。

    pykrx 有一个小坑：它导入时可能会打印韩文登录提示。在中文 Windows 终端里，
    这段韩文有时会触发 GBK 编码异常，所以这里单独开一个 UTF-8 子进程检查它。
    这不是为了联网或登录，只是为了确认包本身能导入。
    """
    modules = [
        ("akshare", "A 股/基金公开数据接口"),
        ("pandas", "表格数据处理"),
        ("numpy", "数值计算"),
        ("matplotlib", "表格图和 RSI 图"),
        ("requests", "网页请求"),
        ("yfinance", "美股/指数兜底行情"),
        ("openpyxl", "Excel 读取支持"),
        ("PIL", "Pillow 图片绘制"),
        ("pandas_market_calendars", "海外交易日历"),
    ]

    items: list[CheckItem] = []
    for module_name, purpose in modules:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            items.append(make_item("ERROR", module_name, f"{purpose} 导入失败: {exc}"))
        else:
            items.append(make_item("OK", module_name, purpose))

    pykrx_env = os.environ.copy()
    pykrx_env["PYTHONIOENCODING"] = "utf-8"
    pykrx_env["PYTHONUTF8"] = "1"
    pykrx_result = subprocess.run(
        [sys.executable, "-c", "import pykrx"],
        cwd=str(PROJECT_ROOT),
        env=pykrx_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if pykrx_result.returncode == 0:
        items.append(make_item("OK", "pykrx", "韩国行情"))
    else:
        error_text = (pykrx_result.stderr or pykrx_result.stdout).strip()
        items.append(make_item("ERROR", "pykrx", f"韩国行情导入失败: {error_text}"))
    return items


def check_workflow_config() -> list[CheckItem]:
    """检查 git_main.py 的配置化脚本清单。

    如果你以后新增脚本，最常见的问题是 `script` 路径写错。这里提前帮你检查，
    避免正式运行到一半才发现。
    """
    items: list[CheckItem] = []
    if not WORKFLOW_STEPS:
        return [make_item("ERROR", "总入口脚本清单", "WORKFLOW_STEPS 为空")]

    for index, step in enumerate(WORKFLOW_STEPS, start=1):
        name = str(step.get("name") or f"步骤 {index}")
        script_text = str(step.get("script") or "").strip()
        if not script_text:
            items.append(make_item("ERROR", name, "缺少 script 字段"))
            continue

        script_path = Path(script_text)
        if script_path.is_absolute():
            items.append(make_item("ERROR", name, f"script 不要写绝对路径: {script_text}"))
            continue

        full_path = PROJECT_ROOT / script_path
        if full_path.exists():
            required = bool(step.get("required", True))
            collect_images = bool(step.get("collect_images", True))
            items.append(
                make_item(
                    "OK",
                    f"{index}. {name}",
                    f"{script_text}，required={required}，collect_images={collect_images}",
                )
            )
        else:
            items.append(make_item("ERROR", f"{index}. {name}", f"未找到 {script_text}"))

    return items


def check_git_status() -> list[CheckItem]:
    """检查 Git 当前是否有未提交改动。

    这里不会自动提交，也不会自动清理缓存。看到 cache/ 或 output/ 变化时，通常是运行
    产物；看到 .py 源码变化时，说明你本地还有代码改动没提交。
    """
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return [make_item("WARN", "Git 状态", f"无法运行 git status: {exc}")]

    if result.returncode != 0:
        return [make_item("WARN", "Git 状态", result.stderr.strip() or "git status 执行失败")]

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return [make_item("OK", "Git 状态", "工作区干净")]

    source_changes = []
    runtime_changes = []
    for line in lines:
        path_text = line[3:].strip() if len(line) > 3 else line.strip()
        normalized = path_text.replace("\\", "/")
        if normalized.startswith(("cache/", "output/", "__pycache__/")):
            runtime_changes.append(line)
        else:
            source_changes.append(line)

    items: list[CheckItem] = []
    if source_changes:
        items.append(
            make_item(
                "WARN",
                "源码改动",
                f"检测到 {len(source_changes)} 条非运行产物改动，提交前请确认范围",
            )
        )
    if runtime_changes:
        items.append(
            make_item(
                "WARN",
                "运行产物改动",
                f"检测到 {len(runtime_changes)} 条 cache/output 变化，通常不需要手动处理",
            )
        )
    return items


def run_checks() -> list[CheckItem]:
    sections = [
        (
            "Python 环境",
            "确认当前解释器和版本，排查环境不一致问题。",
            check_python_runtime,
        ),
        (
            "目录结构",
            "确认项目根目录、cache/、output/ 是否存在。",
            check_runtime_paths,
        ),
        (
            "关键资源和缓存",
            "确认水印图片和常用缓存是否存在；缺失时部分只读图可能无法生成。",
            check_key_files,
        ),
        (
            "总入口脚本清单",
            "确认 workflow_configs.py 里的脚本路径都能找到。",
            check_workflow_config,
        ),
        (
            "邮箱配置",
            "只检查配置是否存在，不读取、不打印授权码，也不会发送邮件。",
            check_email_config,
        ),
        (
            "Python 依赖",
            "只做导入检查，不联网、不拉行情。",
            check_dependencies,
        ),
        (
            "Git 状态",
            "只提示当前工作区情况，不自动提交、不自动清理。",
            check_git_status,
        ),
    ]

    all_items: list[CheckItem] = []
    for title, description, checker in sections:
        print_section(title, description)
        items = checker()
        all_items.extend(items)
        for item in items:
            print_item(item)

    return all_items


def main() -> int:
    print("AHNS 运行前自检开始")
    print(f"项目根目录: {PROJECT_ROOT}")
    items = run_checks()

    error_count = sum(1 for item in items if item.level == "ERROR")
    warn_count = sum(1 for item in items if item.level == "WARN")

    print("\n【自检结论】")
    print(f"[SUMMARY] ERROR={error_count}，WARN={warn_count}")
    if error_count:
        print("[ERROR] 建议先处理上面的 ERROR，再运行主流程。")
        return 1

    if warn_count:
        print("[WARN] 没有发现硬错误，可以先预演运行，但请留意上面的 WARN。")
    else:
        print("[OK] 自检通过，适合继续预演运行。")

    print("建议下一步：")
    print(r"& F:\anaconda\envs\py310\python.exe .\git_main.py --no-send")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
