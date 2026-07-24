"""
service_gui.py

小电脑服务器的一键运行界面。

这个界面只负责“按钮触发”和“日志展示”。Service 流程与限购/持仓缓存强刷
都交给 service_runner.py 处理，避免 GUI 里再维护一套同步逻辑。
"""
from __future__ import annotations

import argparse
import traceback
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import TextIO

import service_runner


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_WINDOW_TITLE = "AHNS 小电脑一键运行"
LOG_DIR = PROJECT_ROOT / "logs"
GUI_LOG_PATH = LOG_DIR / "service_gui.log"
GUI_LOCK_PATH = LOG_DIR / "service_gui.lock"


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_gui_log(message: str) -> None:
    """把 GUI 自身异常写入本地日志，避免无声退出。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with GUI_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{timestamp()}] {message.rstrip()}\n")
    except Exception:
        print(message, file=sys.stderr, flush=True)


def acquire_single_instance_lock() -> TextIO | None:
    """用 Windows 文件锁避免登录自启动和手动双击打开多个 GUI。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = GUI_LOCK_PATH.open("a+", encoding="utf-8")
    lock_file.seek(0, 2)
    if lock_file.tell() == 0:
        lock_file.write("1")
        lock_file.flush()
    lock_file.seek(0)

    try:
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock_file.close()
        return None
    except Exception as exc:
        write_gui_log(f"单实例锁初始化失败，继续启动 GUI：{exc}")
    return lock_file


class ServiceGuiApp:
    """Tkinter 小界面，后台运行 service_runner.py 并实时显示输出。"""

    def __init__(self, root: tk.Tk, args: argparse.Namespace) -> None:
        self.root = root
        self.args = args
        self.output_queue: queue.Queue[tuple[str, str | int]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.running = False
        self.active_task_name = ""

        self.status_var = tk.StringVar(value="空闲")
        self.command_var = tk.StringVar(value=self._format_command(self._service_runner_command()))

        self._build_ui()
        self.root.report_callback_exception = self._handle_tk_exception
        self.root.after(120, self._drain_output_queue)

    def _build_ui(self) -> None:
        self.root.title(DEFAULT_WINDOW_TITLE)
        self.root.geometry("1080x650")
        self.root.minsize(900, 500)

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

        self.refresh_cache_button = ttk.Button(
            button_row,
            text="强制刷新限购+持仓缓存",
            command=self.start_fund_cache_refresh,
            style="AhnsBig.TButton",
            width=24,
        )
        self.refresh_cache_button.pack(side=tk.LEFT, padx=(18, 0))

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
            wraplength=1010,
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

    def _service_runner_command(self) -> list[str]:
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

    def _refresh_cache_runner_command(self) -> list[str]:
        command = [
            sys.executable,
            str(PROJECT_ROOT / "service_runner.py"),
            "--python-exe",
            str(self.args.python_exe),
            "--primary-remote",
            str(self.args.primary_remote),
            "--refresh-fund-limit-cache",
        ]
        if self.args.skip_git:
            command.append("--skip-git")
        return command

    @staticmethod
    def _format_command(command: list[str]) -> str:
        return " ".join(f'"{part}"' if " " in str(part) else str(part) for part in command)

    def _append_log(self, text: str) -> None:
        try:
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, text)
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        except Exception as exc:
            write_gui_log(f"写入界面日志失败：{exc}")

    def _set_running_state(self, running: bool) -> None:
        try:
            self.running = running
            self.run_button.configure(state=tk.DISABLED if running else tk.NORMAL)
            self.refresh_cache_button.configure(state=tk.DISABLED if running else tk.NORMAL)
            self.exit_button.configure(text="运行中不可退出" if running else "退出")
            self.exit_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        except Exception as exc:
            write_gui_log(f"更新按钮状态失败：{exc}")

    def start_service_run(self) -> None:
        self._start_task("运行 service_main", self._service_runner_command())

    def start_fund_cache_refresh(self) -> None:
        """独立维护任务：强刷限购和前十大持仓，不生成图片或发送邮件。"""
        self._start_task("强制刷新限购+持仓缓存", self._refresh_cache_runner_command())

    def _start_task(self, task_name: str, command: list[str]) -> None:
        try:
            if self.running:
                return
            self.active_task_name = task_name
            self.command_var.set(self._format_command(command))
            self._set_running_state(True)
            self.status_var.set(f"运行中：{task_name}")
            self._append_log(f"\n========== 开始：{task_name} ==========\n")
            self._append_log(self.command_var.get() + "\n\n")

            self.worker = threading.Thread(target=self._run_worker, args=(command,), daemon=True)
            self.worker.start()
        except Exception as exc:
            self._handle_gui_error("启动后台运行线程失败", exc)
            self._finish_run(1)

    def _run_worker(self, command: list[str]) -> None:
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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

        try:
            assert process.stdout is not None
            for line in process.stdout:
                self.output_queue.put(("line", line))
            exit_code = process.wait()
            self.output_queue.put(("done", int(exit_code)))
        except Exception as exc:
            self.output_queue.put(("error", f"读取运行日志失败：{exc}\n"))
            self.output_queue.put(("done", 1))

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
        except Exception as exc:
            self._handle_gui_error("刷新运行日志失败", exc)
        finally:
            try:
                self.root.after(120, self._drain_output_queue)
            except Exception as exc:
                write_gui_log(f"重新安排日志刷新失败：{exc}")

    def _finish_run(self, exit_code: int) -> None:
        try:
            task_name = self.active_task_name or "任务"
            self._set_running_state(False)
            if exit_code == 0:
                self.status_var.set("成功")
                self._append_log(f"\n========== {task_name} 成功 ==========\n")
            else:
                self.status_var.set(f"失败，退出码 {exit_code}")
                self._append_log(f"\n========== {task_name} 失败，退出码 {exit_code} ==========\n")
            self.active_task_name = ""
        except Exception as exc:
            self._handle_gui_error("更新运行结果失败", exc)

    def on_close(self) -> None:
        try:
            if self.running:
                messagebox.showinfo("正在运行", "当前 AHNS 任务仍在运行，请等待结束后再关闭。")
                return
            self.root.destroy()
        except Exception as exc:
            self._handle_gui_error("关闭窗口失败", exc)

    def _handle_gui_error(self, title: str, exc: BaseException) -> None:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        write_gui_log(f"{title}：{detail}")
        self._append_log(f"\n[GUI-ERROR] {title}：{exc}\n")

    def _handle_tk_exception(
        self,
        exc_type: type[BaseException],
        exc: BaseException,
        tb: object,
    ) -> None:
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        write_gui_log(f"Tk 回调异常：{detail}")
        self._append_log(f"\n[GUI-ERROR] 界面回调异常：{exc}\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AHNS 小电脑一键运行图形界面")
    parser.add_argument(
        "--python-exe",
        default=service_runner.DEFAULT_SERVICE_PYTHON,
        help=f"运行服务或缓存刷新任务的 Python，默认 {service_runner.DEFAULT_SERVICE_PYTHON}",
    )
    parser.add_argument(
        "--primary-remote",
        default=service_runner.DEFAULT_PRIMARY_REMOTE,
        help=f"同步使用的 Git remote，默认 {service_runner.DEFAULT_PRIMARY_REMOTE}",
    )
    parser.add_argument(
        "--skip-git",
        action="store_true",
        help="跳过 pull/commit/push，适合调试 service 或缓存刷新任务",
    )
    parser.add_argument(
        "--no-send",
        action="store_true",
        help="透传给 service_main.py：运行流程但不发送邮件",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    lock_file = acquire_single_instance_lock()
    if lock_file is None:
        write_gui_log("已有 AHNS Service GUI 正在运行，本次启动跳过。")
        return 0

    try:
        args = parse_args(argv)
        write_gui_log("AHNS Service GUI 启动。")
        root = tk.Tk()
        ServiceGuiApp(root, args)
        root.mainloop()
        write_gui_log("AHNS Service GUI 正常退出。")
        return 0
    except Exception as exc:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        write_gui_log(f"AHNS Service GUI 异常退出：{detail}")
        try:
            messagebox.showerror("AHNS Service GUI 异常", str(exc))
        except Exception:
            pass
        return 1
    finally:
        lock_file.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
