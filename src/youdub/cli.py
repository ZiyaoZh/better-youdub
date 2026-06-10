from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import AppConfig
from .constants import TEST_VIDEO_URL
from .ingest import create_task_from_local_media
from .media import require_binary
from .models import PipelineStep
from .pipeline import PipelineRunner
from .storage import TaskStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="youdub")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Check local runtime prerequisites")

    create_task = subparsers.add_parser("create-task", help="Create a task from a local media file")
    create_task.add_argument("--source", required=True, type=Path)
    create_task.add_argument("--title")

    show_task = subparsers.add_parser("show-task", help="Show a task as JSON")
    show_task.add_argument("task_id")

    run_task = subparsers.add_parser("run-task", help="Run one pipeline step for a task")
    run_task.add_argument("task_id")
    run_task.add_argument(
        "--step",
        choices=[
            PipelineStep.EXTRACT_AUDIO.value,
            PipelineStep.SEPARATE_AUDIO.value,
        ],
        default=PipelineStep.EXTRACT_AUDIO.value,
    )

    subparsers.add_parser("test-video", help="Print the fixed test video identifier")
    return parser


def cmd_doctor(config: AppConfig) -> int:
    config.ensure_dirs()
    checks = {
        "root": str(config.root),
        "tasks_path": str(config.tasks_path),
        "log_dir": str(config.log_dir),
        "models_dir": str(config.models_dir),
        "ffmpeg": require_binary("ffmpeg"),
    }
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0


def cmd_create_task(config: AppConfig, args: argparse.Namespace) -> int:
    config.ensure_dirs()
    task = create_task_from_local_media(args.source, config.root, args.title)
    TaskStore(config.tasks_path).add(task)
    print(json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_show_task(config: AppConfig, args: argparse.Namespace) -> int:
    task = TaskStore(config.tasks_path).get(args.task_id)
    print(json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_run_task(config: AppConfig, args: argparse.Namespace) -> int:
    store = TaskStore(config.tasks_path)
    task = store.get(args.task_id)
    step = PipelineStep(args.step)
    try:
        task = PipelineRunner().run_step(task, step)
    finally:
        store.update(task)
    print(json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = AppConfig.from_env()

    try:
        if args.command == "doctor":
            return cmd_doctor(config)
        if args.command == "create-task":
            return cmd_create_task(config, args)
        if args.command == "show-task":
            return cmd_show_task(config, args)
        if args.command == "run-task":
            return cmd_run_task(config, args)
        if args.command == "test-video":
            print(TEST_VIDEO_URL)
            return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
