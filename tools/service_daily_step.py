"""Service每天一次的本机状态与进程锁；不跨GitHub/Gitee同步。"""
import json
import os
import re
from pathlib import Path
from tools.paths import PROJECT_ROOT

STATE_DIR = PROJECT_ROOT / "cache" / "service_daily_steps"


class DailyStepGuard:
    """持有操作系统锁直到任务结束；进程异常退出后锁自动释放，可再次重试。"""
    def __init__(self, key: str):
        name = re.sub(r"[^a-zA-Z0-9_.-]", "_", key)
        self.state_path = STATE_DIR / f"{name}.json"
        self.lock_path = STATE_DIR / f"{name}.lock"
        self.acquired = False
        self.handle = None

    def __enter__(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.lock_path.open("a+b")
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
        except OSError:
            self.handle.close()
            self.handle = None
        return self

    def completed(self, day: str) -> bool:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))["last_success_date_bj"] == day
        except FileNotFoundError:
            return False
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"[WARN] 每日步骤状态不可读，将在窗口内重试: {error}")
            return False

    def mark_success(self, started_at, finished_at) -> None:
        # 记任务启动的北京时间日期，允许已启动的任务跨过截止时间完成。
        state = {"last_success_date_bj": started_at.date().isoformat(),
                 "started_at_bj": started_at.isoformat(), "last_success_at_bj": finished_at.isoformat()}
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.state_path)

    def __exit__(self, *_):
        if self.handle is not None:
            try:
                if self.acquired:
                    if os.name == "nt":
                        import msvcrt
                        self.handle.seek(0)
                        msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()

