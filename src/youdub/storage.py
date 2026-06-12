from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .ingest import TASK_METADATA_NAME
from .models import Task


class TaskStore:
    def __init__(self, path: Path):
        self.path = path

    def load_all(self) -> list[Task]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
        if not isinstance(raw, list):
            raise ValueError(f"Expected task list in {self.path}")
        return [Task.from_dict(item) for item in raw]

    def save_all(self, tasks: list[Task]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [task.to_dict() for task in tasks]
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            delete=False,
        ) as temp:
            json.dump(payload, temp, ensure_ascii=False, indent=2)
            temp.write("\n")
            temp_path = Path(temp.name)
        temp_path.replace(self.path)

    def add(self, task: Task) -> None:
        tasks = self.load_all()
        if any(existing.id == task.id for existing in tasks):
            raise ValueError(f"Task already exists: {task.id}")
        tasks.append(task)
        self.save_all(tasks)
        self._write_task_metadata(task)

    def get(self, task_id: str) -> Task:
        for task in self.load_all():
            if task.id == task_id:
                return task
        raise KeyError(f"Task not found: {task_id}")

    def find_by_source_key(self, source_key: str) -> Task | None:
        for task in self.load_all():
            if task.source_key == source_key:
                return task
        return None

    def update(self, task: Task) -> None:
        tasks = self.load_all()
        for index, existing in enumerate(tasks):
            if existing.id == task.id:
                tasks[index] = task
                self.save_all(tasks)
                self._write_task_metadata(task)
                return
        raise KeyError(f"Task not found: {task.id}")

    def upsert(self, task: Task) -> Task:
        tasks = self.load_all()
        for index, existing in enumerate(tasks):
            if existing.id == task.id:
                merged = self._merge(existing, task)
                tasks[index] = merged
                self.save_all(tasks)
                self._write_task_metadata(merged)
                return merged

        tasks.append(task)
        self.save_all(tasks)
        self._write_task_metadata(task)
        return task

    def _merge(self, existing: Task, incoming: Task) -> Task:
        return Task(
            id=existing.id,
            title=incoming.title,
            source=incoming.source,
            folder=incoming.folder,
            source_key=incoming.source_key or existing.source_key,
            author=incoming.author or existing.author,
            status=existing.status,
            steps=dict(existing.steps),
            created_at=existing.created_at,
            updated_at=existing.updated_at,
            error=existing.error,
        )

    def _write_task_metadata(self, task: Task) -> None:
        task.folder.mkdir(parents=True, exist_ok=True)
        metadata_path = task.folder / TASK_METADATA_NAME
        with metadata_path.open("w", encoding="utf-8") as file:
            json.dump(task.to_dict(), file, ensure_ascii=False, indent=2)
            file.write("\n")
