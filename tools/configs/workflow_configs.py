"""
总入口运行流程配置。

这个文件只维护“总入口每天按什么顺序运行哪些脚本”。以后你想调整每日流程时，
优先改这里，不要先去改 `git_main.py` / `service_main.py` 的主逻辑。

常见维护方式：
- 想调整运行顺序：移动下面列表里的整段字典。
- 想新增脚本：复制一段字典，改 `name` 和 `script`。
- 想临时不把某个脚本生成的图片发邮件：把 `collect_images` 改成 False。
- 脚本失败不会中断总流程；`required` 仅用于日志里标记必要性，最后统一汇总失败日志。
- 想控制某个脚本只在固定北京时间运行：配置 `run_window_bj=("HH:MM", "HH:MM")`。

注意：
- `script` 一律写相对项目根目录的路径，例如 `safe_fund.py` 或
  `kepu/kepu_sum_holidays.py`，不要写 Windows 绝对路径。
- 脚本因为日期条件没有生成图片是正常情况，例如节假日图、补更新图、限额表；
  只要脚本退出码是 0，就不算失败。
- 这里不要放邮箱密码、SMTP 授权码等敏感信息。
"""

from __future__ import annotations


# 每一项就是总入口的一个运行步骤。
# 字段解释：
# - name: 日志里给人看的名称。这里可以写中文，方便定位是哪一步。
# - script: 脚本路径。必须是相对项目根目录的路径，不要写绝对路径。
# - required: True 表示日志中标为必要步骤；失败也会继续运行后续步骤，并在最后汇总。
# - collect_images: True 表示收集这一步本次新生成/更新的图片用于邮件发送。
# - run_window_bj: 可选，北京时间闭区间；支持跨午夜窗口，例如 ("22:40", "02:00")。
#   命中窗口时才运行该步骤；实时观察脚本仍会和日常完整流程同轮运行。
# - args: 可选，运行脚本时追加的参数；实时观察由总入口控制窗口，因此这里传 --force。
COMMON_WORKFLOW_STEPS = [
    {
        "name": "主行情与基金估算",
        "script": "main.py",
        "required": True,
        "collect_images": True,
    },
    {
        "name": "基金持仓变化图",
        "script": "fund_holding_change.py",
        "required": False,
        "collect_images": True,
        "args": ["--auto"],
    },
    {
        "name": "安全版海外基金图",
        "script": "safe_fund.py",
        "required": True,
        "collect_images": True,
        "run_window_bj": ("06:00", "13:40"),
    },
    {
        "name": "安全版海外节假日图",
        "script": "safe_holidays.py",
        "required": True,
        "collect_images": True,
    },
    {
        "name": "节后补更新观察图",
        "script": "sum_holidays.py",
        "required": True,
        "collect_images": True,
    },
    {
        "name": "节后补更新科普图",
        "script": "kepu/kepu_sum_holidays.py",
        "required": True,
        "collect_images": True,
    },
    {
        "name": "海外基金限购表格图",
        "script": "kepu/kepu_xiane.py",
        "required": True,
        "collect_images": True,
        "args": ["--table-only"],
    },
    {
        "name": "盘前海外基金观察图",
        "script": "premarket_fund.py",
        "required": False,
        "collect_images": True,
        "run_window_bj": ("17:30", "21:00"),
        "args": ["--force"],
    },
    {
        "name": "盘中海外基金观察图",
        "script": "intraday_fund.py",
        "required": False,
        "collect_images": True,
        "run_window_bj": ("22:40", "01:30"),
        "args": ["--force"],
    },
    {
        "name": "盘后海外基金观察图",
        "script": "afterhours_fund.py",
        "required": False,
        "collect_images": True,
        "run_window_bj": ("08:00", "11:29"),
        "args": ["--force"],
    },
]

FUTU_NIGHT_WORKFLOW_STEPS = [
    {
        "name": "富途夜盘海外基金观察图",
        "script": "futu_night_fund.py",
        "required": False,
        "collect_images": True,
        "run_window_bj": ("11:30", "16:30"),
        "args": ["--force"],
    },
]

GITHUB_WORKFLOW_STEPS = [*COMMON_WORKFLOW_STEPS]
SERVICE_WORKFLOW_STEPS = [*COMMON_WORKFLOW_STEPS, *FUTU_NIGHT_WORKFLOW_STEPS]

# 兼容旧导入路径；git_main.py 默认使用 GitHub Actions 流程。
WORKFLOW_STEPS = GITHUB_WORKFLOW_STEPS

__all__ = [
    "COMMON_WORKFLOW_STEPS",
    "FUTU_NIGHT_WORKFLOW_STEPS",
    "GITHUB_WORKFLOW_STEPS",
    "SERVICE_WORKFLOW_STEPS",
    "WORKFLOW_STEPS",
]
