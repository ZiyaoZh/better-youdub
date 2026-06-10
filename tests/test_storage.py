from pathlib import Path

from youdub.models import Task
from youdub.storage import TaskStore


def test_task_store_roundtrip(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    task = Task(id="abc123", title="demo", source="/tmp/demo.mp4", folder=tmp_path / "task")

    store.add(task)

    loaded = store.get("abc123")
    assert loaded.id == task.id
    assert loaded.folder == task.folder

