from __future__ import annotations

import argparse
import inspect
import json
import os
import time
import traceback
from pathlib import Path

from .config import AppConfig
from .models import PipelineStep, Task
from .pipeline import PipelineRunner
from .task_config import dry_run_bilibili_options, runtime_options_from_task_config


class _AdoptedTaskLock:
    """Signals that the WebUI parent already owns the task lock."""


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one isolated YouDub pipeline step")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--step", type=PipelineStep, required=True)
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()

    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Worker input must be a task object")
        task = Task.from_dict(data)
        options = runtime_options_from_task_config(AppConfig.from_env(), task.config)
        if args.step == PipelineStep.PUBLISH_BILIBILI and not options.bilibili.dry_run and not options.bilibili.confirm:
            options = dry_run_bilibili_options(options)
        cancellation = _WorkerCancellation(args.state)
        runner = PipelineRunner(
            demucs_config=options.demucs,
            whisperx_config=options.whisperx,
            translation_config=options.translation,
            tts_config=options.tts,
            synthesis_config=options.synthesis,
            publish_config=options.publish,
            bilibili_publish_config=options.bilibili,
            tts_quality_config=options.tts_quality,
            redub_tts_config=options.redub_tts,
            cancellation=cancellation,
        )
        result = _run_step_with_adopted_lock(runner, task, args.step)
        _write_result(args.output, {"task": result.to_dict()})
        return 0
    except BaseException as exc:
        _write_result(args.output, {"error": str(exc), "traceback": traceback.format_exc()})
        return 1


def _write_result(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_worker_state(path: Path | None, operation: str, details: dict[str, object] | None = None) -> None:
    if path is None:
        return
    with path.open("w", encoding="utf-8") as file:
        json.dump({"irreversible_operation": operation, **(details or {})}, file)
        file.flush()
        os.fsync(file.fileno())


class _WorkerCancellation:
    """Checkpoint adapter that leaves all child work in the worker process group."""

    propagate_to_operations = False

    def __init__(self, state_path: Path | None) -> None:
        self._state_path = state_path

    def checkpoint(self, _name: str | None = None) -> None:
        return

    def wait(self, seconds: float, _name: str | None = None) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def mark_irreversible_operation(self, name: str, **details: object) -> None:
        _write_worker_state(self._state_path, name, details)


def _run_step_with_adopted_lock(runner: PipelineRunner, task: Task, step: PipelineStep) -> Task:
    run_step = runner.run_step
    try:
        parameters = inspect.signature(run_step).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "task_lock" in parameters:
        return run_step(task, step, task_lock=_AdoptedTaskLock())
    return run_step(task, step)


if __name__ == "__main__":
    raise SystemExit(main())
