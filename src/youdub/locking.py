from __future__ import annotations

import fcntl
import os
from pathlib import Path

from .models import utc_now

TASK_LOCK_NAME = ".task.lock"


class TaskLockBusy(RuntimeError):
    def __init__(self, task_dir: Path):
        self.task_dir = task_dir
        super().__init__(f"Task is already running: {task_dir}")


class TaskLock:
    def __init__(self, task_dir: Path, label: str = "task"):
        self.task_dir = task_dir
        self.label = label
        self.path = task_dir / TASK_LOCK_NAME
        self._file = None
        self.acquired = False

    def acquire(self, *, blocking: bool = False) -> "TaskLock":
        if self.acquired:
            return self
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+", encoding="utf-8")
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(self._file.fileno(), flags)
        except BlockingIOError as exc:
            self._file.close()
            self._file = None
            raise TaskLockBusy(self.task_dir) from exc
        self.acquired = True
        self._file.seek(0)
        self._file.truncate()
        self._file.write(f"pid={os.getpid()}\nlabel={self.label}\nacquired_at={utc_now()}\n")
        self._file.flush()
        os.fsync(self._file.fileno())
        return self

    def release(self) -> None:
        if self._file is None:
            self.acquired = False
            return
        try:
            if self.acquired:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None
            self.acquired = False

    def __enter__(self) -> "TaskLock":
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()


def task_is_locked(task_dir: Path) -> bool:
    lock_path = task_dir / TASK_LOCK_NAME
    if not lock_path.exists():
        return False
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    return False
