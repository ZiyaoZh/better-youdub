import json
from pathlib import Path

from youdub.models import PipelineStep, StepStatus, Task, TaskStatus
from youdub.storage import TaskStore


def test_task_store_roundtrip(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    task = Task(id="abc123", title="demo", source="/tmp/demo.mp4", folder=tmp_path / "task")

    store.add(task)

    loaded = store.get("abc123")
    assert loaded.id == task.id
    assert loaded.folder == task.folder


def test_task_store_upsert_preserves_existing_progress(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    folder = tmp_path / "task"
    existing = Task(
        id="abc123",
        title="old",
        source="https://example.test/old",
        folder=folder,
        source_key="youtube:demo123",
        author="Old Author",
        status=TaskStatus.SUCCESS,
    )
    existing.mark_step(PipelineStep.TRANSCRIBE, StepStatus.SUCCESS)
    store.add(existing)

    incoming = Task(
        id="abc123",
        title="new",
        source="https://example.test/new",
        folder=folder,
        source_key="youtube:demo123",
        author="New Author",
    )
    merged = store.upsert(incoming)

    assert merged.title == "new"
    assert merged.author == "New Author"
    assert merged.status == TaskStatus.SUCCESS
    assert merged.steps[PipelineStep.TRANSCRIBE.value] == StepStatus.SUCCESS

    metadata = json.loads((folder / "task.json").read_text(encoding="utf-8"))
    assert metadata["source_key"] == "youtube:demo123"
    assert metadata["author"] == "New Author"
