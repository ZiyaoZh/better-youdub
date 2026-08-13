from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any


class TaskCancelled(RuntimeError):
    """Raised when a cooperative pipeline operation observes cancellation."""


class CancellationContext:
    """Cancellation signal shared by one pipeline execution."""

    def __init__(
        self,
        *,
        task_id: str | None = None,
        execution_id: str | None = None,
        on_checkpoint: Callable[[str | None], None] | None = None,
        on_process_change: Callable[[int | None], None] | None = None,
        on_irreversible_operation: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.task_id = task_id
        self.execution_id = execution_id
        self._event = threading.Event()
        self._lock = threading.RLock()
        self._processes: dict[int, subprocess.Popen[Any]] = {}
        self._on_checkpoint = on_checkpoint
        self._on_process_change = on_process_change
        self._on_irreversible_operation = on_irreversible_operation

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def current_process_pid(self) -> int | None:
        with self._lock:
            return next(reversed(self._processes), None) if self._processes else None

    def request_cancel(self) -> None:
        self._event.set()
        with self._lock:
            processes = list(self._processes.values())
        for process in processes:
            _signal_process_group(process, signal.SIGTERM)

    def checkpoint(self, name: str | None = None) -> None:
        if self._on_checkpoint is not None:
            self._on_checkpoint(name)
        if self.cancelled:
            raise TaskCancelled("Task cancellation was requested")

    def wait(self, seconds: float, name: str | None = None) -> None:
        self.checkpoint(name)
        if seconds > 0 and self._event.wait(seconds):
            self.checkpoint(name)
        self.checkpoint(name)

    def register_process(self, process: subprocess.Popen[Any]) -> None:
        with self._lock:
            self._processes[process.pid] = process
        if self._on_process_change is not None:
            self._on_process_change(process.pid)
        if self.cancelled:
            _signal_process_group(process, signal.SIGTERM)

    def unregister_process(self, process: subprocess.Popen[Any]) -> None:
        with self._lock:
            self._processes.pop(process.pid, None)
            current = next(reversed(self._processes), None) if self._processes else None
        if self._on_process_change is not None:
            self._on_process_change(current)

    def mark_irreversible_operation(self, name: str, **details: Any) -> None:
        if self._on_irreversible_operation is not None:
            self._on_irreversible_operation(name, dict(details))


def run_managed_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    cancellation: CancellationContext | None = None,
    grace_seconds: float = 2.0,
    poll_seconds: float = 0.1,
    cleanup_paths: Iterable[Path] = (),
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command in its own process group and stop it on cancellation."""
    if cancellation is not None:
        cancellation.checkpoint(f"command:start:{command[0] if command else 'unknown'}")

    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
            env=env,
        )
        if cancellation is not None:
            cancellation.register_process(process)
        terminated_for_cancellation = False
        try:
            while process.poll() is None:
                if cancellation is not None and cancellation.cancelled:
                    _terminate_process_group(process, grace_seconds=grace_seconds)
                    terminated_for_cancellation = True
                    raise TaskCancelled("Task cancellation was requested")
                if cancellation is None:
                    time.sleep(poll_seconds)
                else:
                    cancellation._event.wait(poll_seconds)

            if cancellation is not None:
                cancellation.checkpoint(f"command:complete:{command[0] if command else 'unknown'}")
            returncode = process.returncode if process.returncode is not None else process.wait()
        except TaskCancelled:
            if not terminated_for_cancellation:
                _terminate_process_group(process, grace_seconds=grace_seconds)
            _remove_paths(cleanup_paths)
            raise
        finally:
            if cancellation is not None:
                cancellation.unregister_process(process)

        stdout_file.seek(0)
        stderr_file.seek(0)
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout_file.read().decode("utf-8", errors="replace"),
            stderr_file.read().decode("utf-8", errors="replace"),
        )


def _terminate_process_group(process: subprocess.Popen[Any], *, grace_seconds: float) -> None:
    group_active = _signal_process_group(process, signal.SIGTERM)
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while group_active and time.monotonic() < deadline:
        if not _process_group_exists(process):
            break
        time.sleep(0.05)
    # The group leader can exit from SIGTERM while a descendant ignores it.
    # Signal the group even when Popen has already observed that leader exit.
    if _process_group_exists(process):
        _signal_process_group(process, signal.SIGKILL)
    # Do not report resource release before the direct child has been reaped.
    # This wait is normally immediate after SIGKILL, including when SIGTERM
    # already ended the group leader.
    process.wait()


def _signal_process_group(process: subprocess.Popen[Any], sig: signal.Signals) -> bool:
    try:
        os.killpg(process.pid, sig)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        try:
            if process.poll() is not None:
                return False
            if sig == signal.SIGTERM:
                process.terminate()
            elif sig == signal.SIGKILL:
                process.kill()
            return process.poll() is None
        except ProcessLookupError:
            return False


def _process_group_exists(process: subprocess.Popen[Any]) -> bool:
    try:
        os.killpg(process.pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return process.poll() is None


def _remove_paths(paths: Iterable[Path]) -> None:
    for path in paths:
        try:
            if path.is_dir():
                for child in path.iterdir():
                    _remove_paths((child,))
                path.rmdir()
            else:
                path.unlink(missing_ok=True)
        except OSError:
            continue
