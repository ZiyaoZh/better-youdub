# YouDub Linux

Linux/container-native rewrite scaffold for a video localization pipeline.

The old Windows project at `/tmp/YouDub2026` is reference material only. This project favors clean Linux behavior, reproducible dependencies, explicit runtime paths, and container deployment over preserving the old implementation shape.

## Current Scope

Implemented in this scaffold:

- Environment-based configuration
- Local media ingest into a task directory
- JSON task storage with atomic writes
- CLI commands for doctor checks, task creation, task display, and running the first media step
- FFmpeg audio extraction from imported media
- Demucs-backed audio separation command wiring, with explicit dependency checks
- Docker and dependency layout for later CPU/GPU expansion

Not implemented in this scaffold:

- Automated website scraping or cookie refresh
- Upload automation
- WhisperX, translation, TTS, and final synthesis execution
- Bundled Demucs/GPU runtime dependencies

## Fixed Test Video Identifier

```text
https://www.youtube.com/watch?v=6o68Fg2-bhM
```

Automated tests and local runs should use a local, legally prepared media file, for example:

```text
data/samples/6o68Fg2-bhM.mp4
```

The local sample for this workspace is expected at that path. The `data/`
directory is runtime data and is ignored by Git.

## Local Usage

```bash
python3 -m youdub.cli doctor
python3 -m youdub.cli create-task --source data/samples/6o68Fg2-bhM.mp4 --title 6o68Fg2-bhM
python3 -m youdub.cli run-task <task-id> --step extract-audio
python3 -m youdub.cli run-task <task-id> --step separate-audio
python3 -m youdub.cli show-task <task-id>
```

`extract-audio` requires `ffmpeg`. `separate-audio` requires a `demucs`
executable on `PATH`; the current base development environment may not include
it until GPU/runtime dependencies are installed through the project dependency
files and Docker image.

When running without installation, set:

```bash
export PYTHONPATH="$PWD/src"
```

## Runtime Paths

Defaults are suitable for containers:

- `YOUDUB_ROOT=/data/videos`
- `YOUDUB_TASKS_PATH=/data/tasks/tasks.json`
- `YOUDUB_LOG_DIR=/data/logs`
- `YOUDUB_MODELS_DIR=/models`

For local development, override them:

```bash
export YOUDUB_ROOT="$PWD/data/videos"
export YOUDUB_TASKS_PATH="$PWD/data/tasks/tasks.json"
export YOUDUB_LOG_DIR="$PWD/data/logs"
```
