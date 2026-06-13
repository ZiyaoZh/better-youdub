from pathlib import Path

import pytest

from youdub.locking import TaskLock, TaskLockBusy, task_is_locked


def test_task_lock_rejects_second_holder(tmp_path: Path) -> None:
    assert task_is_locked(tmp_path) is False
    assert not (tmp_path / ".task.lock").exists()

    with TaskLock(tmp_path, "first"):
        assert task_is_locked(tmp_path) is True
        with pytest.raises(TaskLockBusy):
            TaskLock(tmp_path, "second").acquire()

    assert task_is_locked(tmp_path) is False
    assert (tmp_path / ".task.lock").exists()
