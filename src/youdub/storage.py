from __future__ import annotations

import json
import tempfile
from pathlib import Path

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

    def get(self, task_id: str) -> Task:
        for task in self.load_all():
            if task.id == task_id:
                return task
        raise KeyError(f"Task not found: {task_id}")

    def update(self, task: Task) -> None:
        tasks = self.load_all()
        for index, existing in enumerate(tasks):
            if existing.id == task.id:
                tasks[index] = task
                self.save_all(tasks)
                return
        raise KeyError(f"Task not found: {task.id}")

