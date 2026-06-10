from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    root: Path
    tasks_path: Path
    log_dir: Path
    models_dir: Path

    @classmethod
    def from_env(cls) -> "AppConfig":
        root = Path(os.getenv("YOUDUB_ROOT", "/data/videos"))
        return cls(
            root=root,
            tasks_path=Path(os.getenv("YOUDUB_TASKS_PATH", "/data/tasks/tasks.json")),
            log_dir=Path(os.getenv("YOUDUB_LOG_DIR", "/data/logs")),
            models_dir=Path(os.getenv("YOUDUB_MODELS_DIR", "/models")),
        )

    def ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.tasks_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)

