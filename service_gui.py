"""
service_gui.py

小电脑服务器的一键运行界面。

这个界面只负责“按钮触发”和“日志展示”，真正的同步、运行、提交、推送仍由
service_runner.py 处理，避免 GUI 里再维护一套业务逻辑。
"""
from __future__ import annotations

import argparse
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

import service_runner


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_WINDOW_TITLE = "AHNS 小电脑一键运行"


class ServiceGuiApp:
    """Tkinter 小界面，后台运行 service_runner.py 并实时显示输出。"""

    def __init__(self, root: tk.Tk, args: argparse.Namespace) -> None:
        self.root = root
        self.args = args
        self.output_queue: queue.Queue[tuple[str, str | int]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.running = False

        self.status_var = tk.StringVar(value="空闲")
        self.command_var = tk.StringVar(value=self._format_runner_command())

        self._build_ui()
        self.root.after(120, self._drain_output_queue)

    def _build_ui(self) -> None:
        self.root.title(DEFAULT_WINDOW_TITLE)
        self.root.geometry("920x620")
        self.root.minsize(760, 480)

        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)

        style = ttk.Style(self.root)
        style.configure("AhnsBig.TButton", font=("Microsoft YaHei UI", 13, "bold"), padding=(18, 10))

        title = ttk.Label(frame, text="AHNS 小电脑一键运行", font=("Microsoft YaHei UI", 18, "bold"))
        title.pack(anchor="w")

        status_row = ttk.Frame(frame)
        status_row.pack(fill=tk.X, pady=(10, 8))

        ttk.Label(status_row, text="当前状态：", font=("Microsoft YaHei UI", 11, "bold")).pack(side=tk.LEFT)
        self.status_label = ttk.Label(status_row, textvariable=self.status_var, font=("Microsoft YaHei UI", 11))
        self.status_label.pack(side=tk.LEFT)

        button_row = ttk.Frame(frame)
        button_row.pack(fill=tk.X, pady=(0, 10))

        self.run_button = ttk.Button(
            button_row,
            text="立即运行 service_main",
            command=self.start_service_run,
            style="AhnsBig.TButton",
            width=20,
        )
        self.run_button.pack(side=tk.LEFT)

        self.exit_button = ttk.Button(
            button_row,
            text="退出",
            command=self.on_close,
            style="AhnsBig.TButton",
            width=12,
        )
        self.exit_button.pack(side=tk.LEFT, padx=(18, 0))

        command_box = ttk.LabelFrame(frame, text="将执行的命令")
        command_box.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(
            command_box,
            textvariable=self.command_var,
            wraplength=860,
            justify=tk.LEFT,
            font=("Consolas", 9),
        ).pack(anchor="w", padx=8, pady=8)

        log_box = ttk.LabelFrame(frame, text="运行日志")
        log_box.pack(fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(
            log_box,
            wrap=tk.WORD,
            font=("Consolas", 10),
            state=tk.DISABLED,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _runner_command(self) -> list[str]:
        command = [
            sys.executable,
            str(PROJECT_ROOT / "service_runner.py"),
            "--python-exe",
            str(self.args.python_exe),
            "--primary-remote",
            str(self.args.primary_remote),
        ]
        if self.args.no_send:
            command.append("--no-send")
        if self.args.skip_git:
            command.append("--skip-git")
        return command

    def _format_runner_command(self) -> str:
        return " ".join(f'"{part}"' if " " in str(part) else str(part) for part in self._runner_command())

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _set_running_state(self, running: bool) -> None:
        self.running = running
        self.run_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.exit_button.configure(text="运行中不可退出" if running else "退出")
        self.exit_button.configure(state=tk.DISABLED if running else tk.NORMAL)

    def start_service_run(self) -> None:
        if self.running:
            return
        self._set_running_state(True)
        self.status_var.set("运行中")
        self._append_log("\n========== 开始运行 ==========\n")
        self._append_log(self._format_runner_command() + "\n\n")

        self.worker = threading.Thread(target=self._run_worker, daemon=True)
        self.worker.start()

    def _run_worker(self) -> None:
        command = self._runner_command()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as exc:
            self.output_queue.put(("error", f"启动失败：{exc}\n"))
            self.output_queue.put(("done", 1))
            return

        assert process.stdout is not None
        for line in process.stdout:
            self.output_queue.put(("line", line))
        exit_code = process.wait()
        self.output_queue.put(("done", int(exit_code)))

    def _drain_output_queue(self) -> None:
        try:
            while True:
                kind, payload = self.output_queue.get_nowait()
                if kind in {"line", "error"}:
                    self._append_log(str(payload))
                elif kind == "done":
                    self._finish_run(int(payload))
        except queue.Empty:
            pass
        self.root.after(120, self._drain_output_queue)

    def _finish_run(self, exit_code: int) -> None:
        self._set_running_state(False)
        if exit_code == 0:
            self.status_var.set("成功")
            self._append_log("\n========== 运行成功 ==========\n")
        else:
            self.status_var.set(f"失败，退出码 {exit_code}")
            self._append_log(f"\n========== 运行失败，退出码 {exit_code} ==========\n")

    def on_close(self) -> None:
        if self.running:
            messagebox.showinfo("正在运行", "service_main.py 仍在运行，请等待结束后再关闭。")
            return
        self.root.destroy()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AHNS 小电脑一键运行图形界面")
    parser.add_argument(
        "--python-exe",
        default=service_runner.DEFAULT_SERVICE_PYTHON,
        help=f"运行 service_main.py 的 Python，默认 {service_runner.DEFAULT_SERVICE_PYTHON}",
    )
    parser.add_argument(
        "--primary-remote",
        default=service_runner.DEFAULT_PRIMARY_REMOTE,
        help=f"同步使用的 Git remote，默认 {service_runner.DEFAULT_PRIMARY_REMOTE}",
    )
    parser.add_argument(
        "--skip-git",
        action="store_true",
        help="只运行 service_main.py，不执行 pull/commit/push，适合调试",
    )
    parser.add_argument(
        "--no-send",
        action="store_true",
        help="透传给 service_main.py：运行流程但不发送邮件",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = tk.Tk()
    ServiceGuiApp(root, args)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
