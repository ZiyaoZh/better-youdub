from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path

from .config import AppConfig
from .downloader import download_url_to_artifacts
from .models import Task
from .task_config import download_config_from_task_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an isolated YouDub URL download")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Worker input must be a task object")
        task = Task.from_dict(data)
        config = AppConfig.from_env()
        result = download_url_to_artifacts(
            task.source,
            config.root,
            download_config_from_task_config(config, task.config),
        )
        _write_result(
            args.output,
            {
                "task_dir": str(result.task_dir),
                "info_path": str(result.info_path),
                "media_path": str(result.media_path),
                "cover_path": str(result.cover_path) if result.cover_path is not None else None,
                "source_key": result.source_key,
            },
        )
        return 0
    except BaseException as exc:
        _write_result(args.output, {"error": str(exc), "traceback": traceback.format_exc()})
        return 1


def _write_result(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
