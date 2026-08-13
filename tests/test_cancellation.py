from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from youdub.cancellation import CancellationContext, TaskCancelled, run_managed_command
from youdub import pipeline_worker


def _is_process_active(pid: int) -> bool:
    """Return false for absent processes and Linux zombies awaiting reaping."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    state = stat.rsplit(")", maxsplit=1)[1].lstrip().split(maxsplit=1)[0]
    return state != "Z"


def test_managed_command_cancels_process_group_and_removes_partial_output(tmp_path: Path) -> None:
    output = tmp_path / "partial.txt"
    context = CancellationContext()
    started = threading.Event()
    requested_at: list[float] = []

    def cancel() -> None:
        while not (tmp_path / "child.pid").exists():
            time.sleep(0.01)
        started.set()
        requested_at.append(time.monotonic())
        context.request_cancel()

    thread = threading.Thread(target=cancel, daemon=True)
    thread.start()

    with pytest.raises(TaskCancelled):
        run_managed_command(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; import subprocess, sys, time; "
                    "child = subprocess.Popen([sys.executable, '-c', "
                    "\"import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)\"]); "
                    "Path('child.pid').write_text(str(child.pid)); "
                    "Path('partial.txt').write_text('partial'); time.sleep(30)"
                ),
            ],
            cwd=tmp_path,
            cancellation=context,
            cleanup_paths=(output,),
            grace_seconds=0.2,
            poll_seconds=0.01,
        )

    assert started.is_set()
    assert requested_at
    # The child deliberately ignores SIGTERM, so one grace period is expected.
    # Re-entering termination from the exception handler would double this.
    assert time.monotonic() - requested_at[0] < 0.35
    assert context.current_process_pid is None
    assert not output.exists()
    child_pid = int((tmp_path / "child.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if not _is_process_active(child_pid):
            break
        time.sleep(0.01)
    else:
        raise AssertionError("managed process group left a child process running")


def test_cancellation_wait_returns_immediately_when_requested() -> None:
    context = CancellationContext()
    context.request_cancel()

    started = time.monotonic()
    with pytest.raises(TaskCancelled):
        context.wait(30)

    assert time.monotonic() - started < 0.1


def test_worker_irreversible_state_is_written(tmp_path: Path) -> None:
    state_path = tmp_path / "worker-state.json"

    pipeline_worker._write_worker_state(state_path, "bilibili:add_archive")

    assert state_path.read_text(encoding="utf-8") == '{"irreversible_operation": "bilibili:add_archive"}'
