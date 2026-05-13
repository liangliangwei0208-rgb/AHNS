"""
git_main.py 的运行流程配置。

这个文件只维护“总入口每天按什么顺序运行哪些脚本”。以后你想调整每日流程时，
优先改这里，不要先去改 `git_main.py` 的主逻辑。

常见维护方式：
- 想调整运行顺序：移动下面列表里的整段字典。
- 想新增脚本：复制一段字典，改 `name` 和 `script`。
- 想临时不把某个脚本生成的图片发邮件：把 `collect_images` 改成 False。
- 想让某个非关键脚本失败也不中断流程：把 `required` 改成 False。

注意：
- `script` 一律写相对项目根目录的路径，例如 `safe_fund.py` 或
  `kepu/kepu_xiane.py`，不要写 Windows 绝对路径。
- 脚本因为日期条件没有生成图片是正常情况，例如节假日图、补更新图、限额表；
  只要脚本退出码是 0，就不算失败。
- 这里不要放邮箱密码、SMTP 授权码等敏感信息。
"""

from __future__ import annotations


# 每一项就是 git_main.py 的一个运行步骤。
# 字段解释：
# - name: 日志里给人看的名称。这里可以写中文，方便定位是哪一步。
# - script: 脚本路径。必须是相对项目根目录的路径，不要写绝对路径。
# - required: True 表示这一步失败时总流程中断；False 表示只警告并继续。
# - collect_images: True 表示收集这一步本次新生成/更新的图片用于邮件发送。
WORKFLOW_STEPS = [
    {
        "name": "科普首图",
        "script": "kepu/first_pic.py",
        "required": True,
        "collect_images": True,
    },
    {
        "name": "主行情与基金估算",
        "script": "main.py",
        "required": True,
        "collect_images": True,
    },
    {
        "name": "安全版海外基金图",
        "script": "safe_fund.py",
        "required": True,
        "collect_images": True,
    },
    {
        "name": "安全版海外节假日图",
        "script": "safe_holidays.py",
        "required": True,
        "collect_images": True,
    },
    {
        "name": "详细版海外节假日图",
        "script": "holidays.py",
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
        "name": "海外基金限额科普图",
        "script": "kepu/kepu_xiane.py",
        "required": True,
        "collect_images": True,
    },
    {
        "name": "盘前海外基金观察图",
        "script": "premarket_fund.py",
        "required": True,
        "collect_images": True,
    },
]


__all__ = ["WORKFLOW_STEPS"]
