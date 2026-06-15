from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class StepStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineStep(str, Enum):
    INGEST = "ingest"
    EXTRACT_AUDIO = "extract-audio"
    SEPARATE_AUDIO = "separate-audio"
    TRANSCRIBE = "transcribe"
    TRANSCRIBE_WHISPER = "transcribe-whisper"
    TRANSCRIBE_ALIGN = "transcribe-align"
    TRANSCRIBE_DIARIZE = "transcribe-diarize"
    TRANSLATE = "translate"
    TTS = "tts"
    TRANSCRIBE_TTS = "transcribe-tts"
    SUBTITLE = "subtitle"
    SYNTHESIZE = "synthesize"
    PREPARE_PUBLISH = "prepare-publish"
    PUBLISH_BILIBILI = "publish-bilibili"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Task:
    id: str
    title: str
    source: str
    folder: Path
    source_key: str | None = None
    author: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    steps: dict[str, StepStatus] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    error: str | None = None
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "source": self.source,
            "folder": str(self.folder),
            "source_key": self.source_key,
            "author": self.author,
            "status": self.status.value,
            "steps": {key: value.value for key, value in self.steps.items()},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "config": self.config,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        return cls(
            id=data["id"],
            title=data["title"],
            source=data["source"],
            folder=Path(data["folder"]),
            source_key=data.get("source_key"),
            author=data.get("author"),
            status=TaskStatus(data.get("status", TaskStatus.PENDING)),
            steps={
                key: StepStatus(value)
                for key, value in data.get("steps", {}).items()
            },
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
            error=data.get("error"),
            config=data.get("config") if isinstance(data.get("config"), dict) else {},
        )

    def mark_step(self, step: PipelineStep, status: StepStatus) -> None:
        self.steps[step.value] = status
        self.updated_at = utc_now()
