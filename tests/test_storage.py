import json
from pathlib import Path

from youdub.models import PipelineStep, StepStatus, Task, TaskStatus
from youdub.storage import TaskStore


def test_task_store_roundtrip(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    task = Task(
        id="abc123",
        title="demo",
        source="/tmp/demo.mp4",
        folder=tmp_path / "task",
        identity="11111111-1111-4111-8111-111111111111",
    )

    store.add(task)

    loaded = store.get("abc123")
    assert loaded.id == task.id
    assert loaded.folder == task.folder
    assert loaded.identity == task.identity


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
        identity="11111111-1111-4111-8111-111111111111",
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
        identity="22222222-2222-4222-8222-222222222222",
    )
    merged = store.upsert(incoming)

    assert merged.title == "new"
    assert merged.author == "New Author"
    assert merged.identity == "11111111-1111-4111-8111-111111111111"
    assert merged.status == TaskStatus.SUCCESS
    assert merged.steps[PipelineStep.TRANSCRIBE.value] == StepStatus.SUCCESS

    metadata = json.loads((folder / "task.json").read_text(encoding="utf-8"))
    assert metadata["source_key"] == "youtube:demo123"
    assert metadata["author"] == "New Author"
    assert metadata["identity"] == "11111111-1111-4111-8111-111111111111"
