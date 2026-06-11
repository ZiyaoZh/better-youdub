---
name: youdub-linux-migration
description: Use when working on the Linux/containerized YouDub migration project under /workspace, including migration planning, dependency and Docker changes, path portability, smoke tests, and permission hygiene for the new project derived from /tmp/YouDub2026.
---

# YouDub Linux Migration Skill

Use this skill when working on the new Linux/containerized YouDub project derived from `/tmp/YouDub2026`.

## Scope

The old repository at `/tmp/YouDub2026` is read-only reference material. Do not edit it unless the user explicitly changes the migration strategy. Do not preserve old implementation choices when a cleaner Linux/container-native design is better.

The new project lives under `/workspace`.

## Required Workflow

1. Read the relevant docs before implementation:
   - `/workspace/docs/assessment.md`
   - `/workspace/docs/migration-sop.md`
   - `/workspace/docs/container-strategy.md`
   - `/workspace/docs/dependency-sync.md`
2. Keep secrets out of code, Dockerfiles, examples, and committed docs.
3. Treat the current container as a development/planning environment, not the final app runtime.
4. When adding Python dependencies, update the appropriate requirements file before changing Dockerfiles.
5. When adding apt/system dependencies, update the app Dockerfile and document why the dependency is needed.
6. Prefer one app runtime environment inside the Docker image. Split into multiple worker images only if dependency conflicts require it.
7. Validate each migration step with concrete output files.
8. Do not implement automated web scraping, anti-bot bypass, cookie refresh, or crawler-style ingest. The first implementation accepts local media files.
9. After creating or editing files under `/workspace`, ensure ownership matches the host-mounted workspace owner, not root.

## Migration Order

1. Create new project skeleton in `/workspace`.
2. Move configuration into environment variables and `.env.example`.
3. Port path handling from Windows strings to `pathlib.Path`.
4. Build CPU container for quick checks.
5. Build GPU container for Demucs, WhisperX, and TTS.
6. Run short-video smoke tests.
7. Only then migrate upload and browser-cookie refresh features.

Fixed test video identifier:

```text
https://www.youtube.com/watch?v=6o68Fg2-bhM
```

Use a local, legally prepared sample file for automated tests, for example `data/samples/6o68Fg2-bhM.mp4`.

## Key Decisions

- Do not deploy inside the existing Codex container.
- Build a separate YouDub app image.
- Use volumes for videos, tasks, cookies, logs, models, and caches.
- Do not bake large models into the normal app image.
- Keep `tasks.json` single-writer in phase 1; move to SQLite/PostgreSQL before multi-worker deployment.

## Permission Hygiene

The agent process may run as root inside the container, while `/workspace` is a host-mounted directory. Do not leave root-owned files in the project.

Required closeout after file changes:

```bash
stat -c '%u:%g %n' /workspace
find /workspace -maxdepth 3 -not -path '/workspace/.git/*' -printf '%u:%g %p\n' | sort | head -200
```

If files are root-owned, repair ownership using the owner/group from `/workspace`. In the current environment that is:

```bash
chown -R 1064:1065 /workspace
```

If the workspace owner changes, use `stat -c '%u:%g' /workspace` first and apply that value instead of assuming `1064:1065`.

## Dependency Rules

- Do not install packages manually in a running container without updating dependency files.
- Pin Git dependencies to commits.
- Keep PyTorch/CUDA install logic explicit.
- Remove or gate Windows-only packages when building Linux images:
  - `pywin32`
  - `WMI`
  - `pyreadline3`
  - `win32_setctime`
  - `shadowcopy`

## Smoke Tests

After Docker or dependency changes, run:

```bash
python -V
python -m compileall app
ffmpeg -version
ffprobe -version
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
python -c "import yt_dlp, openai, librosa, soundfile"
```

For GPU image work, also validate CUDA with:

```bash
docker run --rm --gpus all youdub-app:gpu python -c "import torch; print(torch.cuda.is_available())"
```
