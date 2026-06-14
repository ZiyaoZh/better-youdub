from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import shutil
import tempfile
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import AppConfig
from .downloader import download_url_to_artifacts, supported_js_runtimes
from .ingest import create_pending_url_task, create_task_from_download_artifacts, create_task_from_local_media
from .locking import TaskLock, TaskLockBusy, task_is_locked
from .models import PipelineStep, StepStatus, Task, TaskStatus
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
    "subtitles": ("subtitles.srt", "application/x-subrip"),
    "bilibili-dry-run": ("bilibili.dry-run.json", "application/json"),
}

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="youdub-web")
_LOCK = threading.Lock()
_RUNNING: dict[str, Future[Any]] = {}


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
            "ffmpeg_subtitles_filter": ffmpeg_has_filter("subtitles"),
        }

    @app.get("/api/tasks")
    def list_tasks() -> dict[str, Any]:
        tasks = _store().load_all()
        tasks.sort(key=lambda task: task.updated_at, reverse=True)
        return {"tasks": [_task_payload(task) for task in tasks]}

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
        with _LOCK:
            future = _RUNNING.get(task_id)
            if future is not None and not future.done():
                raise HTTPException(status_code=409, detail="Task is already running")
            task_lock = _acquire_task_lock_for_web(task, f"web-run-step:{payload.step.value}")
            _RUNNING[task_id] = _EXECUTOR.submit(_run_step_job, task.id, payload.step, task_lock)
        return _task_payload(task)

    @app.post("/api/tasks/{task_id}/run-all")
    def run_all(task_id: str) -> dict[str, Any]:
        task = _get_task(task_id)
        _schedule_run_all_for_task(task, "web-run-all")
        return _task_payload(task)

    @app.post("/api/tasks/{task_id}/download")
    def download_existing_url_task(task_id: str) -> dict[str, Any]:
        task = _get_task(task_id)
        _validated_url(task.source)
        _schedule_download_for_task(task, "web-download-url")
        return _task_payload(task)

    @app.delete("/api/tasks/{task_id}", status_code=204)
    def delete_task(task_id: str) -> None:
        task = _get_task(task_id)
        future = _RUNNING.get(task_id)
        if (future is not None and not future.done()) or task_is_locked(task.folder):
            raise HTTPException(status_code=409, detail="Cannot delete a running task")
        tasks = [item for item in _store().load_all() if item.id != task.id]
        _store().save_all(tasks)
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
    try:
        return _store().get(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


def _task_payload(task: Task) -> dict[str, Any]:
    data = task.to_dict()
    data["running"] = _task_running(task.id)
    data["artifacts"] = _artifact_summary(task)
    data["config"] = public_task_config(_config(), task.config)
    return data


def _artifact_summary(task: Task) -> list[dict[str, Any]]:
    artifacts = []
    for key, (name, media_type) in ARTIFACTS.items():
        path = task.folder / name
        if path.exists() and path.is_file():
            artifacts.append({"key": key, "name": name, "size": path.stat().st_size, "media_type": media_type})
    return artifacts


def _task_running(task_id: str) -> bool:
    future = _RUNNING.get(task_id)
    if future is not None and not future.done():
        return True
    try:
        task = _store().get(task_id)
    except KeyError:
        return False
    return task_is_locked(task.folder)


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
        task_lock = _acquire_task_lock_for_web(task, label)
        _RUNNING[task.id] = _EXECUTOR.submit(_run_all_job, task.id, task_lock)


def _schedule_download_for_task(task: Task, label: str) -> None:
    with _LOCK:
        future = _RUNNING.get(task.id)
        if future is not None and not future.done():
            raise HTTPException(status_code=409, detail="Task is already running")
        task_lock = _acquire_task_lock_for_web(task, label)
        _RUNNING[task.id] = _EXECUTOR.submit(_download_url_job, task.id, task_lock)


def _download_url_job(task_id: str, task_lock: TaskLock | None = None) -> None:
    store = _store()
    task = store.get(task_id)
    try:
        task.status = TaskStatus.RUNNING
        task.error = None
        task.mark_step(PipelineStep.INGEST, StepStatus.RUNNING)
        store.update(task)

        config = _config()
        result = download_url_to_artifacts(
            _validated_url(task.source),
            config.root,
            download_config_from_task_config(config, task.config),
        )
        incoming = create_task_from_download_artifacts(
            source=result.media_path,
            info_path=result.info_path,
            root=config.root,
            cover_path=result.cover_path,
        )
        task = _downloaded_task_payload(task, incoming)
        store.update(task)
    except Exception as exc:
        task.status = TaskStatus.FAILED
        task.error = str(exc)
        task.mark_step(PipelineStep.INGEST, StepStatus.FAILED)
        store.update(task)
        raise
    finally:
        if task_lock is not None:
            task_lock.release()


def _downloaded_task_payload(existing: Task, incoming: Task) -> Task:
    merged = Task(
        id=existing.id,
        title=incoming.title,
        source=incoming.source,
        folder=incoming.folder,
        source_key=incoming.source_key,
        author=incoming.author,
        status=TaskStatus.PENDING,
        steps=dict(existing.steps),
        created_at=existing.created_at,
        error=None,
        config=dict(existing.config),
    )
    merged.mark_step(PipelineStep.INGEST, StepStatus.SUCCESS)
    return merged


def _run_step_job(
    task_id: str,
    step: PipelineStep,
    task_lock: TaskLock | None = None,
    *,
    release_lock: bool = True,
) -> None:
    store = _store()
    task = store.get(task_id)
    try:
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
        ).run_step(task, step, task_lock=task_lock)
    finally:
        try:
            store.update(task)
        finally:
            if release_lock and task_lock is not None:
                task_lock.release()


def _run_all_job(task_id: str, task_lock: TaskLock | None = None) -> None:
    try:
        task_lock = _download_for_run_all_if_needed(task_id, task_lock)
        for step in (
            PipelineStep.EXTRACT_AUDIO,
            PipelineStep.SEPARATE_AUDIO,
            PipelineStep.TRANSCRIBE,
            PipelineStep.TRANSLATE,
            PipelineStep.TTS,
            PipelineStep.TRANSCRIBE_TTS,
            PipelineStep.SUBTITLE,
            PipelineStep.SYNTHESIZE,
            PipelineStep.PREPARE_PUBLISH,
        ):
            _run_step_job(task_id, step, task_lock=task_lock, release_lock=False)
    finally:
        if task_lock is not None:
            task_lock.release()


def _download_for_run_all_if_needed(task_id: str, task_lock: TaskLock | None) -> TaskLock | None:
    task = _store().get(task_id)
    if (task.folder / "download.mp4").exists():
        return task_lock
    _validated_url(task.source)
    _download_url_job(task_id, task_lock=task_lock)
    task = _store().get(task_id)
    return TaskLock(task.folder, "web-run-all").acquire(blocking=False)


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
