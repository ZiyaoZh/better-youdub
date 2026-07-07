from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import AppConfig
from .downloader import download_url_to_artifacts, supported_js_runtimes
from .gpu import cleanup_gpu_memory
from .ingest import create_pending_url_task, create_task_from_download_artifacts, create_task_from_local_media
from .locking import TaskLock, TaskLockBusy, task_is_locked
from .models import PipelineStep, StepStatus, Task, TaskStatus, utc_now
from .pipeline import PipelineRunner
from .storage import TaskStore
from .synthesis import ffmpeg_has_filter
from .task_config import (
    default_task_config,
    download_config_from_task_config,
    dry_run_bilibili_options,
    normalize_task_config_update,
    public_task_config,
    runtime_options_from_task_config,
)

ALLOWED_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".flv", ".wmv"}
ARTIFACTS: dict[str, tuple[str, str]] = {
    "download-video": ("download.mp4", "video/mp4"),
    "final-video": ("video.mp4", "video/mp4"),
    "cover": ("cover.jpg", "image/jpeg"),
    "publish-json": ("publish.json", "application/json"),
    "publish-markdown": ("publish.md", "text/markdown"),
    "summary": ("summary.json", "application/json"),
    "transcript": ("transcript.json", "application/json"),
    "translation": ("translation.json", "application/json"),
    "tts-quality": ("tts.quality.json", "application/json"),
    "tts-redub-plan": ("tts.redub.plan.json", "application/json"),
    "subtitles": ("subtitles.srt", "application/x-subrip"),
    "bilibili-dry-run": ("bilibili.dry-run.json", "application/json"),
}


_EXECUTOR = ThreadPoolExecutor(max_workers=5, thread_name_prefix="youdub-web")
_GPU_EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix="youdub-gpu")
_DUBBING_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="youdub-dubbing")
_LOCK = threading.RLock()
_RUNNING: dict[str, Future[Any]] = {}
_TASK_ALIASES: dict[str, str] = {}
_TERMINATING: set[str] = set()

GPU_STEPS = {
    PipelineStep.SEPARATE_AUDIO,
    PipelineStep.TRANSCRIBE,
    PipelineStep.TRANSCRIBE_WHISPER,
    PipelineStep.TRANSCRIBE_ALIGN,
    PipelineStep.TRANSCRIBE_DIARIZE,
    PipelineStep.TTS,
    PipelineStep.TRANSCRIBE_TTS,
    PipelineStep.REDUB_TTS,
}
DUBBING_STEPS = {
    PipelineStep.TTS,
    PipelineStep.REDUB_TTS,
}

DEFAULT_TASK_LIST_LIMIT = 20
MAX_TASK_LIST_LIMIT = 100
TASK_TERMINATED_MESSAGE = "任务已终止"


class TaskTerminationRequested(RuntimeError):
    pass


class UrlTaskRequest(BaseModel):
    url: str
    use_cookies: bool = True
    cookies_path: str | None = None
    cookies_content: str | None = None
    proxy: str | None = None
    max_height: int | None = None
    force_download: bool = False


class LocalTaskRequest(BaseModel):
    source: str
    title: str | None = None


class RunStepRequest(BaseModel):
    step: PipelineStep
    force: bool = False


class RunRequest(BaseModel):
    force: bool = False


class TaskConfigUpdate(BaseModel):
    config: dict[str, Any]


class CookieUpdate(BaseModel):
    content: str | None = None
    clear: bool = False


class OpenAISettingsUpdate(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""


class YtdlpSettingsUpdate(BaseModel):
    proxy: str = ""
    max_height: int = 0


def create_app() -> FastAPI:
    app = FastAPI(title="YouDub WebUI")
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    static_dir = Path(__file__).resolve().parent / "web_static"
    _install_auth_middleware(app)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/doctor")
    def doctor() -> dict[str, Any]:
        config = _config()
        config.ensure_dirs()
        return {
            "root": str(config.root),
            "tasks_path": str(config.tasks_path),
            "log_dir": str(config.log_dir),
            "models_dir": str(config.models_dir),
            "config_path": str(config.config_path),
            "cookies_path": str(config.cookies_path) if config.cookies_path else None,
            "cookies_configured": _nonempty_file(config.cookies_path),
            "ytdlp_proxy_configured": bool(config.ytdlp_proxy),
            "ytdlp_js_runtimes": sorted(supported_js_runtimes()),
            "download_max_height": config.download_max_height,
            "huggingface_token_configured": config.secrets.huggingface.token is not None,
            "openai_api_key_configured": config.secrets.openai.api_key is not None,
            "openai_base_url_configured": config.secrets.openai.base_url is not None,
            "ffmpeg_subtitles_filter": _ffmpeg_has_filter("subtitles"),
        }

    @app.get("/api/system")
    def system_status() -> dict[str, Any]:
        return _system_status_payload()

    @app.get("/api/tasks")
    def list_tasks(
        offset: int = Query(0, ge=0),
        limit: int = Query(DEFAULT_TASK_LIST_LIMIT, ge=1, le=MAX_TASK_LIST_LIMIT),
    ) -> dict[str, Any]:
        tasks = _store().load_all()
        tasks.sort(key=lambda task: task.updated_at, reverse=True)
        total = len(tasks)
        page = tasks[offset : offset + limit]
        return {
            "tasks": [_task_list_payload(task) for task in page],
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(page) < total,
        }

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, Any]:
        return _task_payload(_get_task(task_id))

    @app.post("/api/tasks/url", status_code=201)
    def create_url_task(payload: UrlTaskRequest) -> dict[str, Any]:
        config = _config()
        config.ensure_dirs()
        url = _validated_url(payload.url)
        _save_url_cookies_content(config, payload)
        try:
            result = download_url_to_artifacts(
                url,
                config.root,
                _download_config_from_url_payload(config, payload),
            )
        except TaskLockBusy as exc:
            raise HTTPException(status_code=409, detail="Task is already running") from exc
        task = create_task_from_download_artifacts(
            source=result.media_path,
            info_path=result.info_path,
            root=config.root,
            cover_path=result.cover_path,
        )
        task.config = _task_config_for_url_payload(config, payload)
        task = _store().upsert(task)
        return _task_payload(task)

    @app.post("/api/tasks/url-draft", status_code=201)
    def create_url_draft_task(payload: UrlTaskRequest) -> dict[str, Any]:
        config = _config()
        config.ensure_dirs()
        url = _validated_url(payload.url)
        _save_url_cookies_content(config, payload)
        existing = _find_task_by_source_url(url)
        if existing is not None:
            return _task_payload(existing)
        task = create_pending_url_task(url, config.root)
        task.config = _task_config_for_url_payload(config, payload)
        _store().add(task)
        return _task_payload(task)

    @app.post("/api/tasks/local", status_code=201)
    def create_local_task(payload: LocalTaskRequest) -> dict[str, Any]:
        config = _config()
        task = create_task_from_local_media(Path(payload.source), config.root, payload.title)
        task.config = default_task_config(config)
        _store().add(task)
        return _task_payload(task)

    @app.post("/api/tasks/upload", status_code=201)
    def upload_task(file: UploadFile = File(...), title: str = Form("")) -> dict[str, Any]:
        original_name = Path(file.filename or "").name.strip()
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_VIDEO_SUFFIXES:
            raise HTTPException(status_code=422, detail="Unsupported video file type")
        config = _config()
        config.ensure_dirs()
        upload_dir = config.root / "_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = upload_dir / f"{uuid.uuid4().hex}{suffix}"
        try:
            with upload_path.open("wb") as output:
                shutil.copyfileobj(file.file, output)
            if upload_path.stat().st_size <= 0:
                raise HTTPException(status_code=422, detail="Uploaded file is empty")
            task = create_task_from_local_media(upload_path, config.root, title or Path(original_name).stem)
            task.config = default_task_config(config)
            _store().add(task)
            return _task_payload(task)
        finally:
            file.file.close()

    @app.post("/api/tasks/{task_id}/run")
    def run_step(task_id: str, payload: RunStepRequest) -> dict[str, Any]:
        task = _get_task(task_id)
        if _step_completed(task, payload.step) and not payload.force:
            raise HTTPException(status_code=409, detail="Step is already completed")
        with _LOCK:
            future = _RUNNING.get(task_id)
            if future is not None and not future.done():
                raise HTTPException(status_code=409, detail="Task is already running")
            if task_is_locked(task.folder):
                raise HTTPException(status_code=409, detail="Task is already running")
            _mark_task_scheduled(task, payload.step)
            _track_running(
                task_id,
                _submit_step_job(task.id, payload.step, f"web-run-step:{payload.step.value}"),
            )
        return _task_payload(task)

    @app.post("/api/tasks/{task_id}/run-all")
    def run_all(task_id: str) -> dict[str, Any]:
        task = _get_task(task_id)
        _schedule_run_all_for_task(task, "web-run-all")
        return _task_payload(task)

    @app.post("/api/tasks/{task_id}/download")
    def download_existing_url_task(task_id: str, payload: RunRequest | None = None) -> dict[str, Any]:
        task = _get_task(task_id)
        _validated_url(task.source)
        force = bool(payload.force) if payload is not None else False
        if _step_completed(task, PipelineStep.INGEST) and not force:
            raise HTTPException(status_code=409, detail="Step is already completed")
        _schedule_download_for_task(task, "web-download-url", force=force)
        return _task_payload(task)

    @app.post("/api/tasks/{task_id}/terminate")
    def terminate_task(task_id: str) -> dict[str, Any]:
        task = _get_task(task_id)
        with _LOCK:
            future = _unfinished_future_for_task(task_id) or _unfinished_future_for_task(task.id)
            if future is None:
                if task_is_locked(task.folder):
                    raise HTTPException(status_code=409, detail="Task is running outside this WebUI process")
                raise HTTPException(status_code=409, detail="Task is not running")
            _request_task_termination(task_id, task.id)
            canceled = future.cancel()
            task = _mark_task_terminated(_store().get(task.id))
            _store().update(task)
            if canceled or future.done():
                _clear_running_future(task_id, future)
        return _task_payload(task)

    @app.delete("/api/tasks/{task_id}", status_code=204)
    def delete_task(task_id: str) -> None:
        task = _get_task(task_id)
        future = _RUNNING.get(task_id)
        if (future is not None and not future.done()) or task_is_locked(task.folder):
            raise HTTPException(status_code=409, detail="Cannot delete a running task")
        _store().delete(task.id)
        return None

    @app.get("/api/task-config/defaults")
    def get_task_config_defaults() -> dict[str, Any]:
        return {"config": public_task_config(_config(), {})}

    @app.get("/api/tasks/{task_id}/config")
    def get_task_config(task_id: str) -> dict[str, Any]:
        task = _get_task(task_id)
        return {"config": public_task_config(_config(), task.config)}

    @app.put("/api/tasks/{task_id}/config")
    def save_task_config(task_id: str, payload: TaskConfigUpdate) -> dict[str, Any]:
        task = _get_task(task_id)
        if task_is_locked(task.folder) or _task_running(task.id):
            raise HTTPException(status_code=409, detail="Cannot edit a running task")
        task.config = normalize_task_config_update(_config(), task.config, payload.config)
        _store().update(task)
        return {"config": public_task_config(_config(), task.config)}

    @app.post("/api/tasks/{task_id}/download-cookies")
    def save_task_download_cookies(task_id: str, payload: CookieUpdate) -> dict[str, Any]:
        task = _get_task(task_id)
        if task_is_locked(task.folder) or _task_running(task.id):
            raise HTTPException(status_code=409, detail="Cannot edit a running task")
        content = _clean_text(payload.content)
        if not content:
            return _task_cookies_payload(task)
        path = _task_download_cookies_path(task)
        if path is None:
            raise HTTPException(status_code=422, detail="Cookies path is not configured")
        _write_cookies_file(path, content)
        _ensure_task_download_cookies_path(task, path)
        return _task_cookies_payload(task)

    @app.get("/api/tasks/{task_id}/artifacts")
    def list_artifacts(task_id: str) -> dict[str, Any]:
        task = _get_task(task_id)
        artifacts = []
        for key, (name, media_type) in ARTIFACTS.items():
            path = task.folder / name
            if path.exists() and path.is_file():
                artifacts.append(
                    {
                        "key": key,
                        "name": name,
                        "size": path.stat().st_size,
                        "media_type": media_type,
                        "url": f"/api/tasks/{task.id}/artifacts/{key}",
                    }
                )
        return {"artifacts": artifacts}

    @app.get("/api/tasks/{task_id}/artifacts/{artifact_key}")
    def get_artifact(task_id: str, artifact_key: str, download: bool = False) -> FileResponse:
        task = _get_task(task_id)
        if artifact_key not in ARTIFACTS:
            raise HTTPException(status_code=404, detail="Artifact not found")
        name, media_type = ARTIFACTS[artifact_key]
        path = task.folder / name
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact not available")
        return FileResponse(
            path,
            media_type=media_type,
            filename=name if download else None,
        )

    @app.get("/api/settings/cookies")
    def get_cookies() -> dict[str, Any]:
        path = _config().cookies_path
        summary = _cookies_summary(path)
        return {
            "exists": _nonempty_file(path),
            "size": path.stat().st_size if _nonempty_file(path) else 0,
            "path": str(path) if path else None,
            "content": "",
            **summary,
        }

    @app.post("/api/settings/cookies")
    def save_cookies(payload: CookieUpdate) -> dict[str, Any]:
        path = _config().cookies_path
        if path is None:
            raise HTTPException(status_code=422, detail="YOUDUB_COOKIES_PATH is not configured")
        content = payload.content if payload.content is not None else None
        if content:
            _write_cookies_file(path, content)
        elif payload.clear and path.exists():
            path.unlink()
        return get_cookies()

    @app.get("/api/settings/openai")
    def get_openai_settings() -> dict[str, Any]:
        config = _config()
        return {
            "base_url": config.secrets.openai.base_url or "",
            "model": config.secrets.openai.model or "",
            "has_api_key": bool(config.secrets.openai.api_key),
            "api_key": "********" if config.secrets.openai.api_key else "",
        }

    @app.post("/api/settings/openai")
    def save_openai_settings(payload: OpenAISettingsUpdate) -> dict[str, Any]:
        config = _config()
        data = _read_runtime_config(config.config_path)
        openai = data.setdefault("openai", {})
        _set_or_clear(openai, "base_url", payload.base_url)
        _set_or_clear(openai, "model", payload.model)
        api_key = payload.api_key.strip()
        if api_key != "********":
            _set_or_clear(openai, "api_key", api_key)
        _write_runtime_config(config.config_path, data)
        return get_openai_settings()

    @app.get("/api/settings/ytdlp")
    def get_ytdlp_settings() -> dict[str, Any]:
        config = _config()
        return {"proxy": config.ytdlp_proxy or "", "max_height": config.download_max_height}

    @app.post("/api/settings/ytdlp")
    def save_ytdlp_settings(payload: YtdlpSettingsUpdate) -> dict[str, Any]:
        data = _read_runtime_config(_config().config_path)
        ytdlp = data.setdefault("ytdlp", {})
        ytdlp["proxy"] = payload.proxy.strip()
        ytdlp["max_height"] = int(payload.max_height)
        _write_runtime_config(_config().config_path, data)
        return get_ytdlp_settings()

    app.mount("/assets", StaticFiles(directory=static_dir), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return app


def _install_auth_middleware(app: FastAPI) -> None:
    username = _optional_env("YOUDUB_WEB_USERNAME")
    password = _optional_env("YOUDUB_WEB_PASSWORD")
    enabled = username is not None or password is not None
    complete = username is not None and password is not None

    if not enabled:
        return

    @app.middleware("http")
    async def require_basic_auth(request: Request, call_next: Any) -> Response:
        if not complete:
            return _auth_required_response()
        provided = _basic_auth_credentials(request.headers.get("authorization"))
        if provided is None:
            return _auth_required_response()
        provided_username, provided_password = provided
        if not (
            secrets.compare_digest(provided_username, username)
            and secrets.compare_digest(provided_password, password)
        ):
            return _auth_required_response()
        return await call_next(request)


def _basic_auth_credentials(header: str | None) -> tuple[str, str] | None:
    if not header:
        return None
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, separator, password = decoded.partition(":")
    if not separator:
        return None
    return username, password


def _auth_required_response() -> Response:
    return Response(
        "Authentication required",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="YouDub"'},
    )


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _ffmpeg_has_filter(name: str) -> bool:
    try:
        return ffmpeg_has_filter(name)
    except FileNotFoundError:
        return False


def _system_status_payload() -> dict[str, Any]:
    config = _config()
    return {
        "cpu": {"percent": _cpu_percent()},
        "memory": _memory_usage_payload(),
        "gpu_memory": _gpu_memory_usage_payload(),
        "disk": _disk_usage_payload(config.root),
    }


def _cpu_percent() -> float | None:
    first = _read_cpu_sample()
    if first is None:
        return None
    time.sleep(0.05)
    second = _read_cpu_sample()
    if second is None:
        return None
    idle_delta = second["idle"] - first["idle"]
    total_delta = second["total"] - first["total"]
    if total_delta <= 0:
        return None
    return _round_percent((total_delta - idle_delta) / total_delta * 100)


def _read_cpu_sample() -> dict[str, int] | None:
    try:
        first_line = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return None
    parts = first_line.split()
    if not parts or parts[0] != "cpu":
        return None
    try:
        values = [int(value) for value in parts[1:]]
    except ValueError:
        return None
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return {"idle": idle, "total": sum(values)}


def _memory_usage_payload() -> dict[str, Any]:
    meminfo = _read_meminfo()
    total_kib = meminfo.get("MemTotal")
    available_kib = meminfo.get("MemAvailable")
    if total_kib is None or available_kib is None or total_kib <= 0:
        return _resource_usage_payload(None, None)
    total = total_kib * 1024
    used = max(0, (total_kib - available_kib) * 1024)
    return _resource_usage_payload(used, total)


def _read_meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        lines = Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        key, separator, rest = line.partition(":")
        if not separator:
            continue
        parts = rest.strip().split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0])
        except ValueError:
            continue
    return values


def _gpu_memory_usage_payload() -> dict[str, Any]:
    for reader in (
        _gpu_memory_usage_from_nvidia_smi,
        _gpu_memory_usage_from_pynvml,
        _gpu_memory_usage_from_torch,
    ):
        payload = reader()
        if payload["available"]:
            return payload
    return _resource_usage_payload(None, None, available=False)


def _gpu_memory_usage_from_nvidia_smi() -> dict[str, Any]:
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return _resource_usage_payload(None, None, available=False)
    try:
        result = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _resource_usage_payload(None, None, available=False)
    if result.returncode != 0:
        return _resource_usage_payload(None, None, available=False)
    used_mib = 0
    total_mib = 0
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 2:
            continue
        try:
            used_mib += int(parts[0])
            total_mib += int(parts[1])
        except ValueError:
            continue
    if total_mib <= 0:
        return _resource_usage_payload(None, None, available=False)
    return _resource_usage_payload(used_mib * 1024 * 1024, total_mib * 1024 * 1024)


def _gpu_memory_usage_from_pynvml() -> dict[str, Any]:
    try:
        import pynvml  # type: ignore[import-not-found]
    except ImportError:
        return _resource_usage_payload(None, None, available=False)
    try:
        pynvml.nvmlInit()
        try:
            count = pynvml.nvmlDeviceGetCount()
            used = 0
            total = 0
            for index in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                used += int(info.used)
                total += int(info.total)
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return _resource_usage_payload(None, None, available=False)
    if total <= 0:
        return _resource_usage_payload(None, None, available=False)
    return _resource_usage_payload(used, total)


def _gpu_memory_usage_from_torch() -> dict[str, Any]:
    try:
        import torch  # type: ignore[import-not-found]
    except ImportError:
        return _resource_usage_payload(None, None, available=False)
    cuda = getattr(torch, "cuda", None)
    if cuda is None or not hasattr(cuda, "is_available") or not hasattr(cuda, "mem_get_info"):
        return _resource_usage_payload(None, None, available=False)
    try:
        if not cuda.is_available():
            return _resource_usage_payload(None, None, available=False)
        count = int(cuda.device_count()) if hasattr(cuda, "device_count") else 1
        used = 0
        total = 0
        for index in range(max(1, count)):
            free_bytes, total_bytes = cuda.mem_get_info(index)
            total_bytes = int(total_bytes)
            free_bytes = int(free_bytes)
            used += max(0, total_bytes - free_bytes)
            total += total_bytes
    except Exception:
        return _resource_usage_payload(None, None, available=False)
    if total <= 0:
        return _resource_usage_payload(None, None, available=False)
    return _resource_usage_payload(used, total)


def _disk_usage_payload(path: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(_existing_disk_usage_path(path))
    except OSError:
        return {
            **_resource_usage_payload(None, None, available=False),
            "path": str(path),
        }
    return {
        **_resource_usage_payload(usage.used, usage.total),
        "path": str(path),
    }


def _existing_disk_usage_path(path: Path) -> Path:
    current = path
    while True:
        try:
            if current.exists():
                return current
        except OSError:
            pass
        parent = current.parent
        if parent == current:
            return Path("/")
        current = parent


def _resource_usage_payload(
    used_bytes: int | None,
    total_bytes: int | None,
    *,
    available: bool | None = None,
) -> dict[str, Any]:
    if available is None:
        available = used_bytes is not None and total_bytes is not None
    percent = None
    if used_bytes is not None and total_bytes is not None and total_bytes > 0:
        percent = _round_percent(used_bytes / total_bytes * 100)
    return {
        "available": available,
        "used_bytes": used_bytes,
        "total_bytes": total_bytes,
        "percent": percent,
    }


def _round_percent(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "youdub.web:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )


def _config() -> AppConfig:
    return AppConfig.from_env()


def _store() -> TaskStore:
    return TaskStore(_config().tasks_path)


def _download_config_from_url_payload(config: AppConfig, payload: UrlTaskRequest):
    return download_config_from_task_config(config, _task_config_for_url_payload(config, payload))


def _task_config_for_url_payload(config: AppConfig, payload: UrlTaskRequest) -> dict[str, Any]:
    task_config = default_task_config(config)
    cookies_path = payload.cookies_path if payload.cookies_path is not None else task_config["download"]["cookies_path"]
    if _clean_text(payload.cookies_content) and not _clean_text(cookies_path):
        cookies_path = str(config.cookies_path) if config.cookies_path is not None else ""
    task_config["download"] = {
        **task_config["download"],
        "use_cookies": payload.use_cookies,
        "cookies_path": cookies_path,
        "proxy": payload.proxy if payload.proxy is not None else task_config["download"]["proxy"],
        "max_height": payload.max_height if payload.max_height is not None else task_config["download"]["max_height"],
        "force_download": payload.force_download,
    }
    return task_config


def _find_task_by_source_url(url: str) -> Task | None:
    normalized_url = _normalize_source_url(url)
    for task in _store().load_all():
        if _normalize_source_url(task.source) == normalized_url:
            return task
    return None


def _normalize_source_url(url: str) -> str:
    parsed = urlparse(url.strip())
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    path = parsed.path or "/"
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            "",
            query,
            "",
        )
    )


def _save_url_cookies_content(config: AppConfig, payload: UrlTaskRequest) -> None:
    content = _clean_text(payload.cookies_content)
    if not content:
        return
    if config.cookies_path is None:
        raise HTTPException(status_code=422, detail="YOUDUB_COOKIES_PATH is not configured")
    _write_cookies_file(config.cookies_path, content)


def _write_cookies_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_normalize_cookies_content(content), encoding="utf-8")


def _get_task(task_id: str) -> Task:
    task_id = _TASK_ALIASES.get(task_id, task_id)
    try:
        return _store().get(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


def _task_payload(task: Task) -> dict[str, Any]:
    data = task.to_dict()
    data["display_status"] = _task_display_status(task)
    data["queued"] = _task_queued(task.id)
    data["running"] = _task_running(task.id)
    data["terminating"] = _task_terminating(task.id)
    data["artifacts"] = _artifact_summary(task)
    data["config"] = public_task_config(_config(), task.config)
    data["step_completion"] = _step_completion_summary(task)
    return data


def _task_list_payload(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "source": task.source,
        "author": task.author,
        "status": task.status.value,
        "display_status": _task_display_status(task),
        "queued": _task_queued(task.id),
        "running": _task_running(task.id),
        "terminating": _task_terminating(task.id),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "error": task.error,
        "active_step": _active_step(task),
    }


def _active_step(task: Task) -> str | None:
    for status in (StepStatus.RUNNING, StepStatus.QUEUED):
        for step, step_status in task.steps.items():
            if step_status == status:
                return step
    return None


def _task_display_status(task: Task) -> str:
    if _task_terminating(task.id):
        return "terminating"
    if task.status == TaskStatus.FAILED and task.error == TASK_TERMINATED_MESSAGE:
        return "terminated"
    if (
        task.status == TaskStatus.SUCCESS
        and _step_completed(task, PipelineStep.PREPARE_PUBLISH)
        and not _bilibili_upload_completed(task)
    ):
        return "pending-upload"
    return task.status.value


def _bilibili_upload_completed(task: Task) -> bool:
    step_success = task.steps.get(PipelineStep.PUBLISH_BILIBILI.value) == StepStatus.SUCCESS
    return step_success and _has_step_resource(task.folder / "bilibili.json")


def _artifact_summary(task: Task) -> list[dict[str, Any]]:
    artifacts = []
    for key, (name, media_type) in ARTIFACTS.items():
        path = task.folder / name
        if path.exists() and path.is_file():
            artifacts.append({"key": key, "name": name, "size": path.stat().st_size, "media_type": media_type})
    return artifacts


def _task_running(task_id: str) -> bool:
    if _unfinished_future_for_task(task_id) is not None:
        return True
    try:
        task = _store().get(task_id)
    except KeyError:
        return False
    return task_is_locked(task.folder)


def _task_terminating(task_id: str) -> bool:
    return _termination_requested(task_id) and _unfinished_future_for_task(task_id) is not None


def _task_queued(task_id: str) -> bool:
    future = _unfinished_future_for_task(task_id)
    if future is None:
        return False
    try:
        task = _store().get(task_id)
    except KeyError:
        return False
    return not task_is_locked(task.folder)


def _unfinished_future_for_task(task_id: str) -> Future[Any] | None:
    future = _RUNNING.get(task_id)
    if future is not None and not future.done():
        return future
    for source_id, target_id in _TASK_ALIASES.items():
        if target_id != task_id:
            continue
        future = _RUNNING.get(source_id)
        if future is not None and not future.done():
            return future
    return None


def _acquire_task_lock_for_web(task: Task, label: str) -> TaskLock:
    try:
        return TaskLock(task.folder, label).acquire(blocking=False)
    except TaskLockBusy as exc:
        raise HTTPException(status_code=409, detail="Task is already running") from exc


def _schedule_run_all_for_task(task: Task, label: str) -> None:
    with _LOCK:
        future = _RUNNING.get(task.id)
        if future is not None and not future.done():
            raise HTTPException(status_code=409, detail="Task is already running")
        if task_is_locked(task.folder):
            raise HTTPException(status_code=409, detail="Task is already running")
        _mark_task_scheduled(task, _first_run_all_step(task))
        _track_running(task.id, _EXECUTOR.submit(_run_all_job, task.id, label))


def _schedule_download_for_task(task: Task, label: str, *, force: bool = False) -> None:
    with _LOCK:
        future = _RUNNING.get(task.id)
        if future is not None and not future.done():
            raise HTTPException(status_code=409, detail="Task is already running")
        if task_is_locked(task.folder):
            raise HTTPException(status_code=409, detail="Task is already running")
        _mark_task_scheduled(task, PipelineStep.INGEST)
        _track_running(
            task.id,
            _EXECUTOR.submit(_download_url_job, task.id, label, force=force),
        )


def _submit_step_job(task_id: str, step: PipelineStep, label: str) -> Future[Any]:
    return _executor_for_step(step).submit(_run_step_job, task_id, step, label)


def _track_running(task_id: str, future: Future[Any]) -> Future[Any]:
    _RUNNING[task_id] = future
    future.add_done_callback(lambda completed: _clear_running_future(task_id, completed))
    if future.done():
        _clear_running_future(task_id, future)
    return future


def _clear_running_future(task_id: str, future: Future[Any]) -> None:
    with _LOCK:
        for runtime_id in _runtime_task_ids(task_id):
            if _RUNNING.get(runtime_id) is future:
                _RUNNING.pop(runtime_id, None)
        _clear_task_termination(task_id)


def _request_task_termination(*task_ids: str) -> None:
    for task_id in task_ids:
        _TERMINATING.update(_runtime_task_ids(task_id))


def _clear_task_termination(task_id: str) -> None:
    for runtime_id in _runtime_task_ids(task_id):
        _TERMINATING.discard(runtime_id)


def _termination_requested(task_id: str) -> bool:
    return any(runtime_id in _TERMINATING for runtime_id in _runtime_task_ids(task_id))


def _raise_if_termination_requested(task_id: str) -> None:
    if _termination_requested(task_id):
        raise TaskTerminationRequested()


def _runtime_task_ids(task_id: str) -> set[str]:
    ids = {task_id}
    changed = True
    while changed:
        changed = False
        for source_id, target_id in _TASK_ALIASES.items():
            if source_id in ids or target_id in ids:
                before = len(ids)
                ids.add(source_id)
                ids.add(target_id)
                changed = changed or len(ids) != before
    return ids


def _mark_task_terminated(task: Task, step: PipelineStep | None = None) -> Task:
    task.status = TaskStatus.FAILED
    task.error = TASK_TERMINATED_MESSAGE
    if step is not None:
        task.mark_step(step, StepStatus.FAILED)
    for step_key, step_status in list(task.steps.items()):
        if step_status in {StepStatus.QUEUED, StepStatus.RUNNING}:
            task.steps[step_key] = StepStatus.FAILED
    task.updated_at = utc_now()
    return task


def _executor_for_step(step: PipelineStep) -> ThreadPoolExecutor:
    if _step_requires_dubbing_exclusivity(step):
        return _DUBBING_EXECUTOR
    if _step_uses_gpu(step):
        return _GPU_EXECUTOR
    return _EXECUTOR


def _step_uses_gpu(step: PipelineStep) -> bool:
    return step in GPU_STEPS


def _step_requires_dubbing_exclusivity(step: PipelineStep) -> bool:
    return step in DUBBING_STEPS


def _mark_task_scheduled(task: Task, step: PipelineStep | None = None) -> None:
    task.status = TaskStatus.QUEUED
    task.error = None
    if step is not None:
        task.mark_step(step, StepStatus.QUEUED)
    _store().update(task)


def _first_run_all_step(task: Task) -> PipelineStep | None:
    if not _step_completed(task, PipelineStep.INGEST):
        return PipelineStep.INGEST
    for step in _run_all_steps_for_task(task):
        if not _step_completed(task, step):
            return step
    return None


def _download_url_job(
    task_id: str,
    label: str = "web-download-url",
    task_lock: TaskLock | None = None,
    *,
    force: bool = False,
) -> str:
    store = _store()
    task = store.get(task_id)
    acquired_here = False
    try:
        _raise_if_termination_requested(task_id)
        if task_lock is None:
            task_lock = TaskLock(task.folder, label).acquire(blocking=False)
            acquired_here = True
        _raise_if_termination_requested(task_id)
        if force:
            _clear_step_outputs(task, PipelineStep.INGEST)
        task.status = TaskStatus.RUNNING
        task.error = None
        task.mark_step(PipelineStep.INGEST, StepStatus.RUNNING)
        store.update(task)
        _raise_if_termination_requested(task_id)

        config = _config()
        result = download_url_to_artifacts(
            _validated_url(task.source),
            config.root,
            download_config_from_task_config(config, task.config),
        )
        _raise_if_termination_requested(task_id)
        incoming = create_task_from_download_artifacts(
            source=result.media_path,
            info_path=result.info_path,
            root=config.root,
            cover_path=result.cover_path,
        )
        task = _merge_downloaded_task(store, task, incoming)
        store.update(task)
        _alias_running_future(task_id, task.id)
        _raise_if_termination_requested(task.id)
        _cleanup_pending_task_dir(config.root, task_lock.task_dir, task.folder)
        return task.id
    except TaskTerminationRequested:
        try:
            task = store.get(_TASK_ALIASES.get(task_id, task_id))
            store.update(_mark_task_terminated(task, PipelineStep.INGEST))
        except KeyError:
            pass
        raise
    except Exception as exc:
        try:
            task = store.get(_TASK_ALIASES.get(task_id, task_id))
            task.status = TaskStatus.FAILED
            task.error = str(exc)
            task.mark_step(PipelineStep.INGEST, StepStatus.FAILED)
            store.update(task)
        except KeyError:
            pass
        raise
    finally:
        if acquired_here and task_lock is not None:
            task_lock.release()


def _merge_downloaded_task(store: TaskStore, existing: Task, incoming: Task) -> Task:
    stable = store.find_by_source_key(incoming.source_key) if incoming.source_key else None
    if stable is not None and stable.id != existing.id:
        merged = _downloaded_task_payload(stable, incoming, config=existing.config)
        _TASK_ALIASES[existing.id] = stable.id
        store.update(merged)
        store.delete(existing.id)
        return merged
    return _downloaded_task_payload(existing, incoming)


def _downloaded_task_payload(existing: Task, incoming: Task, config: dict[str, Any] | None = None) -> Task:
    steps = dict(existing.steps)
    steps.update(incoming.steps)
    merged = Task(
        id=existing.id,
        title=incoming.title,
        source=incoming.source,
        folder=incoming.folder,
        source_key=incoming.source_key,
        author=incoming.author,
        status=TaskStatus.PENDING,
        steps=steps,
        created_at=existing.created_at,
        error=None,
        config=dict(config if config is not None else existing.config),
    )
    merged.mark_step(PipelineStep.INGEST, StepStatus.SUCCESS)
    return merged


def _alias_running_future(source_id: str, target_id: str) -> None:
    if source_id == target_id:
        return
    future = _RUNNING.get(source_id)
    if future is not None:
        _RUNNING[target_id] = future
    if source_id in _TERMINATING:
        _TERMINATING.add(target_id)


def _cleanup_pending_task_dir(root: Path, old_folder: Path, new_folder: Path) -> None:
    pending_root = (root / "_pending").resolve()
    old_resolved = old_folder.resolve()
    if old_resolved == new_folder.resolve():
        return
    if pending_root not in old_resolved.parents:
        return
    shutil.rmtree(old_resolved, ignore_errors=True)


def _run_step_job(
    task_id: str,
    step: PipelineStep,
    task_lock: TaskLock | str | None = None,
    *,
    release_lock: bool = True,
) -> None:
    store = _store()
    task = store.get(task_id)
    acquired_here = False
    try:
        _raise_if_termination_requested(task_id)
        if isinstance(task_lock, str):
            task_lock = TaskLock(task.folder, task_lock).acquire(blocking=False)
            acquired_here = True
        _raise_if_termination_requested(task_id)
        if step != PipelineStep.REDUB_TTS or _redub_plan_has_segments(task):
            _clear_step_outputs(task, step)
        task.status = TaskStatus.RUNNING
        task.error = None
        task.mark_step(step, StepStatus.RUNNING)
        store.update(task)
        _raise_if_termination_requested(task_id)
        options = runtime_options_from_task_config(_config(), task.config)
        if step == PipelineStep.PUBLISH_BILIBILI and not options.bilibili.dry_run and not options.bilibili.confirm:
            options = dry_run_bilibili_options(options)
        task = PipelineRunner(
            whisperx_config=options.whisperx,
            translation_config=options.translation,
            tts_config=options.tts,
            synthesis_config=options.synthesis,
            publish_config=options.publish,
            bilibili_publish_config=options.bilibili,
            tts_quality_config=options.tts_quality,
            redub_tts_config=options.redub_tts,
        ).run_step(task, step, task_lock=task_lock)
        _raise_if_termination_requested(task_id)
    except TaskTerminationRequested:
        task = _mark_task_terminated(task, step)
        raise
    except Exception as exc:
        task.status = TaskStatus.FAILED
        task.error = str(exc)
        task.mark_step(step, StepStatus.FAILED)
        raise
    finally:
        try:
            store.update(task)
        finally:
            if _step_uses_gpu(step):
                cleanup_gpu_memory(f"web-step:{step.value}")
            if release_lock and isinstance(task_lock, TaskLock):
                task_lock.release()


def _run_all_job(task_id: str, task_lock: TaskLock | None = None) -> None:
    if isinstance(task_lock, str):
        label = task_lock
        task_lock = None
    else:
        label = "web-run-all"
    try:
        task_id, task_lock = _download_for_run_all_if_needed(task_id, task_lock, label)
        _raise_if_termination_requested(task_id)
        for step in _run_all_steps(task_id):
            task = _store().get(task_id)
            _raise_if_termination_requested(task_id)
            if _step_completed(task, step):
                continue
            _run_step_for_run_all(task_id, step, task_lock)
            _raise_if_termination_requested(task_id)
        task = _store().get(task_id)
        task.status = TaskStatus.SUCCESS
        task.error = None
        _store().update(task)
    except TaskTerminationRequested:
        task = _store().get(_TASK_ALIASES.get(task_id, task_id))
        _store().update(_mark_task_terminated(task))
        raise
    finally:
        if task_lock is not None:
            task_lock.release()


def _run_step_for_run_all(task_id: str, step: PipelineStep, task_lock: TaskLock | None) -> None:
    if _step_requires_dubbing_exclusivity(step):
        _run_step_for_run_all_on_executor(_DUBBING_EXECUTOR, task_id, step, task_lock)
        return
    if _step_uses_gpu(step):
        _run_step_for_run_all_on_executor(_GPU_EXECUTOR, task_id, step, task_lock)
        return
    _run_step_job(task_id, step, task_lock=task_lock, release_lock=False)


def _run_step_for_run_all_on_executor(
    executor: ThreadPoolExecutor,
    task_id: str,
    step: PipelineStep,
    task_lock: TaskLock | None,
) -> None:
    _mark_run_all_step_queued(task_id, step)
    executor.submit(
        _run_step_job,
        task_id,
        step,
        task_lock=task_lock,
        release_lock=False,
    ).result()


def _mark_run_all_step_queued(task_id: str, step: PipelineStep) -> None:
    task = _store().get(task_id)
    task.error = None
    task.mark_step(step, StepStatus.QUEUED)
    _store().update(task)


def _download_for_run_all_if_needed(
    task_id: str,
    task_lock: TaskLock | None,
    label: str,
) -> tuple[str, TaskLock | None]:
    task = _store().get(task_id)
    if _step_completed(task, PipelineStep.INGEST):
        if task_lock is None:
            task_lock = TaskLock(task.folder, label).acquire(blocking=False)
        return task.id, task_lock
    _validated_url(task.source)
    new_task_id = _download_url_job(task_id, label=label, task_lock=task_lock)
    if task_lock is not None:
        task_lock.release()
    task = _store().get(new_task_id)
    return task.id, TaskLock(task.folder, label).acquire(blocking=False)


def _run_all_steps(task_id: str) -> tuple[PipelineStep, ...]:
    return _run_all_steps_for_task(_store().get(task_id))


def _run_all_steps_for_task(task: Task) -> tuple[PipelineStep, ...]:
    steps = [
        PipelineStep.EXTRACT_AUDIO,
        PipelineStep.SEPARATE_AUDIO,
        PipelineStep.TRANSCRIBE,
        PipelineStep.TRANSLATE,
        PipelineStep.TTS,
        PipelineStep.TRANSCRIBE_TTS,
        PipelineStep.SUBTITLE,
        PipelineStep.SYNTHESIZE,
        PipelineStep.PREPARE_PUBLISH,
    ]
    workflow = task.config.get("workflow") if isinstance(task.config, dict) else None
    enable_tts_redub = isinstance(workflow, dict) and bool(workflow.get("enable_tts_redub"))
    if enable_tts_redub:
        steps.insert(steps.index(PipelineStep.SYNTHESIZE), PipelineStep.INSPECT_TTS)
        steps.insert(steps.index(PipelineStep.SYNTHESIZE), PipelineStep.REDUB_TTS)
        steps.insert(steps.index(PipelineStep.SYNTHESIZE), PipelineStep.TRANSCRIBE_TTS)
        steps.insert(steps.index(PipelineStep.SYNTHESIZE), PipelineStep.SUBTITLE)
    include_bilibili_upload = isinstance(workflow, dict) and bool(workflow.get("include_bilibili_upload"))
    if include_bilibili_upload:
        steps.append(PipelineStep.PUBLISH_BILIBILI)
    return tuple(steps)


STEP_OUTPUTS: dict[PipelineStep, tuple[str, ...]] = {
    PipelineStep.INGEST: ("download.mp4",),
    PipelineStep.EXTRACT_AUDIO: ("audio.wav",),
    PipelineStep.SEPARATE_AUDIO: ("audio_vocals.wav", "audio_instruments.wav"),
    PipelineStep.TRANSCRIBE_WHISPER: ("transcript.whisper.json",),
    PipelineStep.TRANSCRIBE_ALIGN: ("transcript.aligned.json",),
    PipelineStep.TRANSCRIBE_DIARIZE: ("transcript.diarized.json", "transcript.json"),
    PipelineStep.TRANSCRIBE: (
        "transcript.whisper.json",
        "transcript.aligned.json",
        "transcript.diarized.json",
        "transcript.json",
    ),
    PipelineStep.TRANSLATE: (
        "summary.json",
        "translation.context.json",
        "translation.segments.json",
        "translation.json",
    ),
    PipelineStep.TTS: ("audio_tts.wav", "audio_tts.timings.json", "segments/vocals", "segments/tts"),
    PipelineStep.TRANSCRIBE_TTS: (
        "audio_tts.transcript.whisper.json",
        "audio_tts.transcript.aligned.json",
        "audio_tts.transcript.json",
    ),
    PipelineStep.SUBTITLE: ("subtitles.segments.json", "subtitles.srt"),
    PipelineStep.INSPECT_TTS: ("tts.quality.json", "tts.redub.plan.json"),
    PipelineStep.REDUB_TTS: ("audio_tts.wav", "audio_tts.timings.json"),
    PipelineStep.SYNTHESIZE: ("video.mp4",),
    PipelineStep.PREPARE_PUBLISH: ("publish.json", "publish.md", "cover.jpg"),
    PipelineStep.PUBLISH_BILIBILI: ("bilibili.dry-run.json", "bilibili.json"),
}

STEP_CLEANUP_GROUPS: tuple[tuple[tuple[PipelineStep, ...], tuple[str, ...]], ...] = (
    ((PipelineStep.INGEST,), ("download.mp4", "download.info.json", "download.webp", "download.jpg", "download.jpeg", "download.png")),
    ((PipelineStep.EXTRACT_AUDIO,), ("audio.wav",)),
    ((PipelineStep.SEPARATE_AUDIO,), ("audio_vocals.wav", "audio_instruments.wav", "demucs")),
    ((PipelineStep.TRANSCRIBE, PipelineStep.TRANSCRIBE_WHISPER), ("transcript.whisper.json",)),
    ((PipelineStep.TRANSCRIBE, PipelineStep.TRANSCRIBE_ALIGN), ("transcript.aligned.json",)),
    ((PipelineStep.TRANSCRIBE, PipelineStep.TRANSCRIBE_DIARIZE), ("transcript.diarized.json", "transcript.json", "SPEAKER")),
    ((PipelineStep.TRANSLATE,), ("summary.json", "translation.context.json", "translation.segments.json", "translation.json")),
    ((PipelineStep.TTS,), ("audio_tts.wav", "audio_tts.timings.json", "segments/vocals", "segments/tts", "segments/stretched")),
    (
        (PipelineStep.TRANSCRIBE_TTS,),
        ("audio_tts.transcript.whisper.json", "audio_tts.transcript.aligned.json", "audio_tts.transcript.json"),
    ),
    ((PipelineStep.SUBTITLE,), ("subtitles.segments.json", "subtitles.srt")),
    ((PipelineStep.INSPECT_TTS,), ("tts.quality.json", "tts.redub.plan.json")),
    ((PipelineStep.SYNTHESIZE,), ("audio_mixed.m4a", "video.mp4")),
    ((PipelineStep.PREPARE_PUBLISH,), ("publish.json", "publish.md", "cover.jpg")),
    ((PipelineStep.PUBLISH_BILIBILI,), ("bilibili.dry-run.json", "bilibili.json")),
)


def _step_completion_summary(task: Task) -> dict[str, dict[str, Any]]:
    return {step.value: _step_completion_for_task(task, step) for step in STEP_OUTPUTS}


def _step_completion_for_task(task: Task, step: PipelineStep) -> dict[str, Any]:
    outputs = _step_outputs_for_task(task, step)
    missing = _missing_step_resources(task, step)
    completed = len(outputs) - len(missing)
    total = len(outputs)
    unit = "artifact"
    progress = _step_segment_progress(task, step)
    if progress is not None:
        completed, total, unit = progress
    percent = round(completed / total * 100) if total else 0
    step_status = task.steps.get(step.value)
    step_started = step_status is not None and step_status != StepStatus.PENDING
    show_progress = total > 1 and (completed > 0 or step_started)
    return {
        "complete": step_status == StepStatus.SUCCESS and not missing,
        "missing": missing,
        "completed": completed,
        "total": total,
        "percent": percent,
        "unit": unit,
        "show_progress": show_progress,
    }


def _step_segment_progress(task: Task, step: PipelineStep) -> tuple[int, int, str] | None:
    if step != PipelineStep.TTS:
        return None
    total = _translation_entry_count(task.folder / "translation.json")
    if total <= 1:
        return None
    completed = _count_numbered_wavs(task.folder / "segments" / "tts", total)
    return completed, total, "segment"


def _translation_entry_count(path: Path) -> int:
    if not path.exists() or not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if isinstance(data, dict):
        data = data.get("translation")
    return len(data) if isinstance(data, list) else 0


def _count_numbered_wavs(directory: Path, total: int) -> int:
    if total <= 0 or not directory.exists() or not directory.is_dir():
        return 0
    count = 0
    for index in range(1, total + 1):
        if _has_step_resource(directory / f"{index:04d}.wav"):
            count += 1
    return count


def _step_completed(task: Task, step: PipelineStep) -> bool:
    return task.steps.get(step.value) == StepStatus.SUCCESS and not _missing_step_resources(task, step)


def _missing_step_resources(task: Task, step: PipelineStep) -> list[str]:
    outputs = _step_outputs_for_task(task, step)
    return [name for name in outputs if not _has_step_resource(task.folder / name)]


def _step_outputs_for_task(task: Task, step: PipelineStep) -> tuple[str, ...]:
    if step == PipelineStep.PUBLISH_BILIBILI:
        bilibili = task.config.get("bilibili") if isinstance(task.config, dict) else None
        dry_run = not isinstance(bilibili, dict) or bool(bilibili.get("dry_run", True)) or not bool(bilibili.get("confirm"))
        return ("bilibili.dry-run.json",) if dry_run else ("bilibili.json",)
    return STEP_OUTPUTS.get(step, ())


def _has_step_resource(path: Path) -> bool:
    if path.is_file():
        return path.stat().st_size > 0
    if path.is_dir():
        return any(path.iterdir())
    return False


def _clear_step_outputs(task: Task, step: PipelineStep) -> None:
    if step == PipelineStep.INSPECT_TTS:
        _remove_task_resource(task.folder, "tts.quality.json")
        _remove_task_resource(task.folder, "tts.redub.plan.json")
        if step.value in task.steps:
            task.mark_step(step, StepStatus.PENDING)
        task.status = TaskStatus.PENDING
        task.error = None
        return
    if step == PipelineStep.REDUB_TTS:
        _clear_redub_downstream_outputs(task)
        return
    start = _cleanup_start_index(step)
    if start is None:
        return
    affected_steps: set[PipelineStep] = set()
    for group_steps, resources in STEP_CLEANUP_GROUPS[start:]:
        affected_steps.update(group_steps)
        for resource in resources:
            _remove_task_resource(task.folder, resource)

    for affected in affected_steps:
        if affected.value in task.steps:
            task.mark_step(affected, StepStatus.PENDING)
    task.status = TaskStatus.PENDING
    task.error = None


def _cleanup_start_index(step: PipelineStep) -> int | None:
    for index, (steps, _resources) in enumerate(STEP_CLEANUP_GROUPS):
        if step in steps:
            return index
    return None


def _clear_redub_downstream_outputs(task: Task) -> None:
    resources = (
        "audio_tts.transcript.whisper.json",
        "audio_tts.transcript.aligned.json",
        "audio_tts.transcript.json",
        "subtitles.segments.json",
        "subtitles.srt",
        "audio_mixed.m4a",
        "video.mp4",
        "publish.json",
        "publish.md",
        "cover.jpg",
        "bilibili.dry-run.json",
        "bilibili.json",
    )
    for resource in resources:
        _remove_task_resource(task.folder, resource)
    for affected in (
        PipelineStep.REDUB_TTS,
        PipelineStep.TRANSCRIBE_TTS,
        PipelineStep.SUBTITLE,
        PipelineStep.SYNTHESIZE,
        PipelineStep.PREPARE_PUBLISH,
        PipelineStep.PUBLISH_BILIBILI,
    ):
        if affected.value in task.steps:
            task.mark_step(affected, StepStatus.PENDING)
    task.status = TaskStatus.PENDING
    task.error = None


def _redub_plan_has_segments(task: Task) -> bool:
    path = task.folder / "tts.redub.plan.json"
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True
    segments = data.get("segments") if isinstance(data, dict) else None
    return isinstance(segments, list) and bool(segments)


def _remove_task_resource(task_dir: Path, resource: str) -> None:
    path = task_dir / resource
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink()


def _nonempty_file(path: Path | None) -> bool:
    return path is not None and path.exists() and path.is_file() and path.stat().st_size > 0


def _read_runtime_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="Runtime config must be a JSON object")
    return data


def _write_runtime_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _set_or_clear(data: dict[str, Any], key: str, value: str) -> None:
    value = value.strip()
    if value:
        data[key] = value
    else:
        data.pop(key, None)


def _task_download_cookies_path(task: Task) -> Path | None:
    config = _config()
    public_config = public_task_config(config, task.config)
    download = public_config.get("download")
    cookies_path = download.get("cookies_path") if isinstance(download, dict) else None
    text = _clean_text(cookies_path)
    if text:
        return Path(text)
    return config.cookies_path


def _ensure_task_download_cookies_path(task: Task, path: Path) -> None:
    config = _config()
    normalized = normalize_task_config_update(config, task.config, task.config)
    download = normalized.setdefault("download", {})
    if not _clean_text(download.get("cookies_path")):
        download["cookies_path"] = str(path)
    download["use_cookies"] = True
    task.config = normalized
    _store().update(task)


def _task_cookies_payload(task: Task) -> dict[str, Any]:
    path = _task_download_cookies_path(task)
    return {
        "path": str(path) if path else None,
        "exists": _nonempty_file(path),
        "size": path.stat().st_size if _nonempty_file(path) else 0,
        **_cookies_summary(path),
    }


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _validated_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        raise HTTPException(status_code=422, detail="URL is required")
    if urlparse(url).scheme.lower() not in {"http", "https"}:
        raise HTTPException(status_code=422, detail="Only http:// and https:// video URLs are supported")
    return url


def _normalize_cookies_content(content: str) -> str:
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not content.strip():
        return ""

    normalized_lines: list[str] = []
    for line in content.split("\n"):
        line = line.lstrip("\ufeff")
        if not line:
            normalized_lines.append(line)
            continue
        if line.startswith("#"):
            normalized_lines.append(line)
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            parts = line.split(maxsplit=6)
        normalized_lines.append("\t".join(parts) if len(parts) == 7 else line)

    normalized = "\n".join(normalized_lines).rstrip("\n")
    return normalized + "\n"


def _cookies_summary(path: Path | None) -> dict[str, Any]:
    if not _nonempty_file(path):
        return _empty_cookies_summary()

    domains: set[str] = set()
    names: set[str] = set()
    count = 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return _empty_cookies_summary()

    for line in lines:
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            parts = line.split(maxsplit=6)
        if len(parts) != 7:
            continue
        count += 1
        domains.add(parts[0])
        names.add(parts[5])

    return {
        "cookie_domains": sorted(domains)[:20],
        "cookie_names": sorted(names)[:50],
        "cookie_count": count,
        "cookies_look_valid": count > 0,
        "cookies_parser_count": _mozilla_cookie_count(path),
    }


def _empty_cookies_summary() -> dict[str, Any]:
    return {
        "cookie_domains": [],
        "cookie_names": [],
        "cookie_count": 0,
        "cookies_look_valid": False,
        "cookies_parser_count": 0,
    }


def _mozilla_cookie_count(path: Path) -> int:
    # MozillaCookieJar is close to what yt-dlp ultimately expects for Netscape files.
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as temp:
        temp_path = Path(temp.name)
        temp.write(path.read_text(encoding="utf-8", errors="replace"))
    try:
        jar = MozillaCookieJar(str(temp_path))
        jar.load(ignore_discard=True, ignore_expires=True)
        return len(jar)
    except Exception:
        return 0
    finally:
        temp_path.unlink(missing_ok=True)
