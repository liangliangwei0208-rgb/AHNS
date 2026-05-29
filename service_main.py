"""
service_main.py

AHNS 小电脑/远程服务器总控入口。

它复用 git_main.py 的运行、图片收集、失败汇总和邮件发送逻辑，但使用
包含富途夜盘观察的 Service 流程。适合在已启动 Futu OpenD 的本地小电脑
或远程服务器上运行。
"""
from __future__ import annotations

from git_main import main as run_workflow_main
from tools.configs.workflow_configs import SERVICE_WORKFLOW_STEPS


def main(argv: list[str] | None = None) -> int:
    return run_workflow_main(
        argv,
        entry_name="service_main.py",
        workflow_label="Service 小电脑",
        workflow_steps=SERVICE_WORKFLOW_STEPS,
    )


if __name__ == "__main__":
    raise SystemExit(main())
