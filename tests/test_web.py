from __future__ import annotations

import base64
import json
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from youdub.downloader import DownloadResult
from youdub.locking import TaskLock
from youdub.models import PipelineStep, StepStatus, TaskStatus
from youdub.task_config import WEB_TRANSLATION_BASE_URL_DEFAULT, WEB_TRANSLATION_MODEL_DEFAULT
from youdub.translation import TranslationConfig
from youdub.transcription import WhisperXConfig
from youdub.tts import TTSConfig
from youdub import web as web_module
from youdub.web import create_app


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    web_module._RUNNING.clear()
    web_module._TASK_ALIASES.clear()
    monkeypatch.setenv("YOUDUB_ROOT", str(tmp_path / "videos"))
    monkeypatch.setenv("YOUDUB_TASKS_PATH", str(tmp_path / "tasks" / "tasks.json"))
    monkeypatch.setenv("YOUDUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("YOUDUB_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))
    monkeypatch.setenv("YOUDUB_COOKIES_PATH", str(tmp_path / "cookies" / "cookies.txt"))
    return TestClient(create_app())


def _basic_auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_web_serves_index_static_assets_and_health(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    for path in ("/", "/assets/app.js", "/assets/styles.css", "/api/health", "/api/doctor"):
        response = client.get(path)
        assert response.status_code == 200

    assert client.get("/api/health").json() == {"status": "ok"}


def test_web_basic_auth_protects_static_and_api(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_WEB_USERNAME", "alice")
    monkeypatch.setenv("YOUDUB_WEB_PASSWORD", "secret")
    client = _client(monkeypatch, tmp_path)

    unauthenticated = client.get("/api/health")
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["www-authenticate"] == 'Basic realm="YouDub"'
    assert client.get("/", headers=_basic_auth("alice", "wrong")).status_code == 401
    assert client.get("/api/health", headers=_basic_auth("alice", "secret")).json() == {"status": "ok"}
    assert client.get("/", headers=_basic_auth("alice", "secret")).status_code == 200


def test_web_basic_auth_requires_complete_configuration(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_WEB_USERNAME", "alice")
    monkeypatch.delenv("YOUDUB_WEB_PASSWORD", raising=False)
    client = _client(monkeypatch, tmp_path)

    assert client.get("/api/health", headers=_basic_auth("alice", "secret")).status_code == 401


def test_web_creates_local_task_and_lists_artifacts(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")

    response = client.post("/api/tasks/local", json={"source": str(source), "title": "Web Smoke"})

    assert response.status_code == 201
    task = response.json()
    assert task["title"] == "Web Smoke"
    assert task["artifacts"] == [
        {
            "key": "download-video",
            "name": "download.mp4",
            "size": 5,
            "media_type": "video/mp4",
        }
    ]

    tasks = client.get("/api/tasks").json()["tasks"]
    assert [item["id"] for item in tasks] == [task["id"]]

    artifacts = client.get(f"/api/tasks/{task['id']}/artifacts").json()["artifacts"]
    assert artifacts[0]["key"] == "download-video"
    assert artifacts[0]["url"] == f"/api/tasks/{task['id']}/artifacts/download-video"


def test_web_task_config_defaults_update_and_mask_secrets(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    client = _client(monkeypatch, tmp_path)
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")

    defaults = client.get("/api/task-config/defaults")
    assert defaults.status_code == 200
    assert defaults.json()["config"]["download"]["max_height"] == 0
    assert "auto_run_all_after_download" not in defaults.json()["config"]["download"]
    assert "Bloons TD 6" in defaults.json()["config"]["translation"]["correction_prompt"]
    assert defaults.json()["config"]["translation"]["base_url"] == WEB_TRANSLATION_BASE_URL_DEFAULT
    assert defaults.json()["config"]["translation"]["model"] == WEB_TRANSLATION_MODEL_DEFAULT
    assert defaults.json()["config"]["tts"]["inference_timesteps"] == 15

    task = client.post("/api/tasks/local", json={"source": str(source), "title": "Config Smoke"}).json()
    assert task["config"]["whisperx"]["model_name"] == "large-v2"
    assert task["config"]["translation"]["api_key"] == ""
    assert task["config"]["translation"]["base_url"] == WEB_TRANSLATION_BASE_URL_DEFAULT
    assert task["config"]["translation"]["model"] == WEB_TRANSLATION_MODEL_DEFAULT
    assert task["config"]["tts"]["inference_timesteps"] == 15

    updated = client.put(
        f"/api/tasks/{task['id']}/config",
        json={
            "config": {
                **task["config"],
                "download": {
                    **task["config"]["download"],
                    "proxy": "http://127.0.0.1:7890",
                    "max_height": 720,
                },
                "translation": {
                    **task["config"]["translation"],
                    "api_key": "sk-task",
                    "base_url": "https://example.test/v1",
                    "model": "gpt-task",
                    "segment_extra_prompt": "使用中文主播口吻。",
                    "correction_prompt": "把 tax shooter 视为 Tack Shooter。",
                },
                "whisperx": {
                    **task["config"]["whisperx"],
                    "batch_size": 12,
                },
            }
        },
    )
    assert updated.status_code == 200
    assert updated.json()["config"]["translation"]["api_key"] == "********"
    assert updated.json()["config"]["download"]["max_height"] == 720

    config_path = tmp_path / "tasks" / "tasks.json"
    saved_task = json.loads(config_path.read_text(encoding="utf-8"))[0]
    assert saved_task["config"]["translation"]["api_key"] == "sk-task"
    assert saved_task["config"]["translation"]["segment_extra_prompt"] == "使用中文主播口吻。"
    assert saved_task["config"]["translation"]["correction_prompt"] == "把 tax shooter 视为 Tack Shooter。"
    assert saved_task["config"]["whisperx"]["batch_size"] == 12

    masked_payload = updated.json()["config"]
    masked_payload["translation"]["model"] = "gpt-task-2"
    masked = client.put(f"/api/tasks/{task['id']}/config", json={"config": masked_payload})
    assert masked.status_code == 200
    saved_task = json.loads(config_path.read_text(encoding="utf-8"))[0]
    assert saved_task["config"]["translation"]["api_key"] == "sk-task"
    assert saved_task["config"]["translation"]["model"] == "gpt-task-2"


def test_web_url_task_uses_and_saves_download_config(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    captured = {}

    def fake_download(url: str, root: Path, config: object) -> DownloadResult:
        captured["url"] = url
        captured["root"] = root
        captured["config"] = config
        task_dir = root / "Web" / "20260614 Sample__abc123"
        task_dir.mkdir(parents=True)
        info = {
            "extractor_key": "YouTube",
            "id": "abc123",
            "title": "Sample",
            "uploader": "Web",
            "upload_date": "20260614",
        }
        info_path = task_dir / "download.info.json"
        media_path = task_dir / "download.mp4"
        info_path.write_text(json.dumps(info), encoding="utf-8")
        media_path.write_bytes(b"video")
        return DownloadResult(
            task_dir=task_dir,
            info_path=info_path,
            media_path=media_path,
            cover_path=None,
            info=info,
            source_key="youtube:abc123",
        )

    monkeypatch.setattr(web_module, "download_url_to_artifacts", fake_download)

    response = client.post(
        "/api/tasks/url",
        json={
            "url": "https://example.test/watch?v=abc123",
            "use_cookies": False,
            "cookies_path": "/tmp/custom-cookies.txt",
            "proxy": "http://127.0.0.1:7890",
            "max_height": 720,
            "force_download": True,
        },
    )

    assert response.status_code == 201
    assert captured["url"] == "https://example.test/watch?v=abc123"
    assert captured["config"].cookies_path is None
    assert captured["config"].use_cookies is False
    assert captured["config"].proxy == "http://127.0.0.1:7890"
    assert captured["config"].max_height == 720
    assert captured["config"].force is True
    task = response.json()
    assert task["config"]["download"] == {
        "use_cookies": False,
        "cookies_path": "/tmp/custom-cookies.txt",
        "proxy": "http://127.0.0.1:7890",
        "max_height": 720,
        "force_download": True,
    }


def test_web_can_create_url_draft_before_download(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    def fail_download(*args: object, **kwargs: object) -> None:
        raise AssertionError("draft creation must not download")

    monkeypatch.setattr(web_module, "download_url_to_artifacts", fail_download)

    response = client.post(
        "/api/tasks/url-draft",
        json={
            "url": "https://example.test/watch?v=abc123",
            "max_height": 720,
        },
    )

    assert response.status_code == 201
    task = response.json()
    assert task["source"] == "https://example.test/watch?v=abc123"
    assert task["source_key"] is None
    assert task["status"] == "pending"
    assert task["steps"] == {}
    assert task["artifacts"] == []
    assert Path(task["folder"]).parent == tmp_path / "videos" / "_pending"
    assert task["config"]["download"]["max_height"] == 720


def test_web_url_draft_reuses_existing_task_by_normalized_url(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    first = client.post(
        "/api/tasks/url-draft",
        json={"url": "https://example.test/watch?v=abc123&b=2"},
    ).json()
    second = client.post(
        "/api/tasks/url-draft",
        json={"url": "https://EXAMPLE.test/watch?b=2&v=abc123#ignored"},
    ).json()

    assert second["id"] == first["id"]
    assert len(client.get("/api/tasks").json()["tasks"]) == 1


def test_web_downloads_url_draft_with_saved_config_and_hydrates_same_task(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    draft = client.post(
        "/api/tasks/url-draft",
        json={"url": "https://example.test/watch?v=abc123", "max_height": 720},
    ).json()
    config = draft["config"]
    config["download"]["max_height"] = 360
    config["translation"]["model"] = "gpt-task"
    updated = client.put(f"/api/tasks/{draft['id']}/config", json={"config": config})
    assert updated.status_code == 200
    captured = {}

    def fake_download(url: str, root: Path, config: object) -> DownloadResult:
        captured["url"] = url
        captured["config"] = config
        task_dir = root / "Web" / "20260614 Hydrated__abc123"
        task_dir.mkdir(parents=True)
        info = {
            "extractor_key": "YouTube",
            "id": "abc123",
            "title": "Hydrated",
            "uploader": "Web",
            "upload_date": "20260614",
            "webpage_url": url,
        }
        info_path = task_dir / "download.info.json"
        media_path = task_dir / "download.mp4"
        cover_path = task_dir / "download.webp"
        info_path.write_text(json.dumps(info), encoding="utf-8")
        media_path.write_bytes(b"video")
        cover_path.write_bytes(b"cover")
        return DownloadResult(
            task_dir=task_dir,
            info_path=info_path,
            media_path=media_path,
            cover_path=cover_path,
            info=info,
            source_key="youtube:abc123",
        )

    monkeypatch.setattr(web_module, "download_url_to_artifacts", fake_download)

    response = client.post(f"/api/tasks/{draft['id']}/download")

    assert response.status_code == 200
    assert response.json()["running"] is True
    web_module._RUNNING[draft["id"]].result(timeout=2)
    task = client.get(f"/api/tasks/{draft['id']}").json()
    assert task["id"] == draft["id"]
    assert task["title"] == "Hydrated"
    assert task["source"] == "https://example.test/watch?v=abc123"
    assert task["source_key"] == "youtube:abc123"
    assert task["author"] == "Web"
    assert task["folder"] == str(tmp_path / "videos" / "Web" / "20260614 Hydrated")
    assert task["steps"]["ingest"] == "success"
    assert task["artifacts"][0]["key"] == "download-video"
    assert not Path(draft["folder"]).exists()
    assert task["config"]["download"]["max_height"] == 360
    assert task["config"]["translation"]["model"] == "gpt-task"
    assert captured["url"] == "https://example.test/watch?v=abc123"
    assert captured["config"].max_height == 360


def test_web_url_draft_download_merges_existing_stable_task(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    def fake_initial_download(url: str, root: Path, config: object) -> DownloadResult:
        task_dir = root / "Web" / "20260614 Existing"
        task_dir.mkdir(parents=True, exist_ok=True)
        info = {
            "extractor_key": "YouTube",
            "id": "abc123",
            "title": "Existing",
            "uploader": "Web",
            "upload_date": "20260614",
            "webpage_url": url,
        }
        info_path = task_dir / "download.info.json"
        media_path = task_dir / "download.mp4"
        info_path.write_text(json.dumps(info), encoding="utf-8")
        media_path.write_bytes(b"video")
        return DownloadResult(task_dir, info_path, media_path, None, info, "youtube:abc123")

    monkeypatch.setattr(web_module, "download_url_to_artifacts", fake_initial_download)
    existing = client.post("/api/tasks/url", json={"url": "https://example.test/watch?v=abc123"}).json()
    draft = client.post("/api/tasks/url-draft", json={"url": "https://example.test/other?v=abc123"}).json()

    config = draft["config"]
    config["translation"]["model"] = "gpt-draft"
    assert client.put(f"/api/tasks/{draft['id']}/config", json={"config": config}).status_code == 200
    response = client.post(f"/api/tasks/{draft['id']}/download")

    assert response.status_code == 200
    web_module._RUNNING[draft["id"]].result(timeout=2)
    tasks = client.get("/api/tasks").json()["tasks"]
    assert [task["id"] for task in tasks] == [existing["id"]]
    merged = client.get(f"/api/tasks/{draft['id']}").json()
    assert merged["id"] == existing["id"]
    assert merged["config"]["translation"]["model"] == "gpt-draft"
    assert not Path(draft["folder"]).exists()


def test_web_run_all_downloads_url_draft_before_pipeline_steps(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    draft = client.post(
        "/api/tasks/url-draft",
        json={"url": "https://example.test/watch?v=abc123", "max_height": 720},
    ).json()
    captured = {"steps": []}

    def fake_download(url: str, root: Path, config: object) -> DownloadResult:
        captured["download_url"] = url
        captured["download_max_height"] = config.max_height
        task_dir = root / "Web" / "20260614 RunAll__abc123"
        task_dir.mkdir(parents=True)
        info = {
            "extractor_key": "YouTube",
            "id": "abc123",
            "title": "RunAll",
            "uploader": "Web",
            "upload_date": "20260614",
            "webpage_url": url,
        }
        info_path = task_dir / "download.info.json"
        media_path = task_dir / "download.mp4"
        info_path.write_text(json.dumps(info), encoding="utf-8")
        media_path.write_bytes(b"video")
        return DownloadResult(
            task_dir=task_dir,
            info_path=info_path,
            media_path=media_path,
            cover_path=None,
            info=info,
            source_key="youtube:abc123",
        )

    class FakeRunner:
        def __init__(self, **kwargs: object) -> None:
            pass

        def run_step(self, task, step: PipelineStep, task_lock=None):
            captured["steps"].append((step.value, str(task.folder), str(task_lock.task_dir)))
            task.status = TaskStatus.SUCCESS
            task.mark_step(step, StepStatus.SUCCESS)
            return task

    monkeypatch.setattr(web_module, "download_url_to_artifacts", fake_download)
    monkeypatch.setattr(web_module, "PipelineRunner", FakeRunner)

    response = client.post(f"/api/tasks/{draft['id']}/run-all")

    assert response.status_code == 200
    web_module._RUNNING[draft["id"]].result(timeout=2)
    stable_folder = str(tmp_path / "videos" / "Web" / "20260614 RunAll")
    assert captured["download_url"] == "https://example.test/watch?v=abc123"
    assert captured["download_max_height"] == 720
    assert [item[0] for item in captured["steps"]] == [
        "extract-audio",
        "separate-audio",
        "transcribe",
        "translate",
        "tts",
        "transcribe-tts",
        "subtitle",
        "synthesize",
        "prepare-publish",
    ]
    assert {item[1] for item in captured["steps"]} == {stable_folder}
    assert {item[2] for item in captured["steps"]} == {stable_folder}
    task = client.get(f"/api/tasks/{draft['id']}").json()
    assert task["source_key"] == "youtube:abc123"
    assert task["steps"]["ingest"] == "success"
    assert task["steps"]["prepare-publish"] == "success"


def test_web_run_all_includes_bilibili_only_when_enabled(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")
    task = client.post("/api/tasks/local", json={"source": str(source), "title": "Run All Bili"}).json()
    captured = {"steps": []}

    class FakeRunner:
        def __init__(self, **kwargs: object) -> None:
            captured["bilibili"] = kwargs["bilibili_publish_config"]

        def run_step(self, task, step: PipelineStep, task_lock=None):
            captured["steps"].append(step.value)
            task.status = TaskStatus.SUCCESS
            task.mark_step(step, StepStatus.SUCCESS)
            return task

    monkeypatch.setattr(web_module, "PipelineRunner", FakeRunner)

    web_module._run_all_job(task["id"])
    assert captured["steps"][-1] == "prepare-publish"
    assert "publish-bilibili" not in captured["steps"]

    task = client.get(f"/api/tasks/{task['id']}").json()
    config = task["config"]
    config["workflow"]["include_bilibili_upload"] = True
    config["bilibili"]["dry_run"] = False
    config["bilibili"]["confirm"] = False
    assert client.put(f"/api/tasks/{task['id']}/config", json={"config": config}).status_code == 200
    captured["steps"].clear()

    web_module._run_all_job(task["id"])

    assert captured["steps"][-1] == "publish-bilibili"
    assert captured["bilibili"].dry_run is True
    assert captured["bilibili"].confirm is False


def test_web_run_all_skips_only_steps_with_success_status_and_outputs(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")
    task_payload = client.post("/api/tasks/local", json={"source": str(source), "title": "Skip Complete"}).json()
    store = web_module._store()
    task = store.get(task_payload["id"])
    (task.folder / "audio.wav").write_bytes(b"audio")
    task.mark_step(PipelineStep.EXTRACT_AUDIO, StepStatus.SUCCESS)
    task.mark_step(PipelineStep.SEPARATE_AUDIO, StepStatus.SUCCESS)
    store.update(task)
    captured = {"steps": []}

    class FakeRunner:
        def __init__(self, **kwargs: object) -> None:
            pass

        def run_step(self, task, step: PipelineStep, task_lock=None):
            captured["steps"].append(step.value)
            task.status = TaskStatus.SUCCESS
            task.mark_step(step, StepStatus.SUCCESS)
            return task

    monkeypatch.setattr(web_module, "PipelineRunner", FakeRunner)

    web_module._run_all_job(task.id)

    assert captured["steps"][0] == "separate-audio"
    assert "extract-audio" not in captured["steps"]


def test_web_run_step_requires_force_when_step_outputs_are_complete(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")
    task_payload = client.post("/api/tasks/local", json={"source": str(source), "title": "Force Step"}).json()
    store = web_module._store()
    task = store.get(task_payload["id"])
    (task.folder / "audio.wav").write_bytes(b"audio")
    task.mark_step(PipelineStep.EXTRACT_AUDIO, StepStatus.SUCCESS)
    store.update(task)

    response = client.post(f"/api/tasks/{task.id}/run", json={"step": "extract-audio"})

    assert response.status_code == 409
    assert response.json()["detail"] == "Step is already completed"


def test_web_run_step_allows_success_status_without_required_outputs(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")
    task_payload = client.post("/api/tasks/local", json={"source": str(source), "title": "Missing Output"}).json()
    store = web_module._store()
    task = store.get(task_payload["id"])
    task.mark_step(PipelineStep.EXTRACT_AUDIO, StepStatus.SUCCESS)
    store.update(task)
    started = threading.Event()

    def fake_job(*args: object, **kwargs: object) -> None:
        started.set()

    monkeypatch.setattr(web_module, "_run_step_job", fake_job)

    response = client.post(f"/api/tasks/{task.id}/run", json={"step": "extract-audio"})

    assert response.status_code == 200
    web_module._RUNNING[task.id].result(timeout=2)
    assert started.is_set()


def test_web_force_rerun_cleans_step_and_downstream_outputs(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")
    task_payload = client.post("/api/tasks/local", json={"source": str(source), "title": "Clean Downstream"}).json()
    store = web_module._store()
    task = store.get(task_payload["id"])
    for name in (
        "audio.wav",
        "audio_vocals.wav",
        "audio_instruments.wav",
        "transcript.json",
        "translation.json",
        "audio_tts.wav",
        "subtitles.srt",
        "video.mp4",
        "publish.json",
        "bilibili.dry-run.json",
    ):
        (task.folder / name).write_bytes(b"old")
    for step in (
        PipelineStep.EXTRACT_AUDIO,
        PipelineStep.SEPARATE_AUDIO,
        PipelineStep.TRANSCRIBE,
        PipelineStep.TRANSLATE,
        PipelineStep.TTS,
        PipelineStep.SUBTITLE,
        PipelineStep.SYNTHESIZE,
        PipelineStep.PREPARE_PUBLISH,
        PipelineStep.PUBLISH_BILIBILI,
    ):
        task.mark_step(step, StepStatus.SUCCESS)
    store.update(task)

    class FakeRunner:
        def __init__(self, **kwargs: object) -> None:
            pass

        def run_step(self, task, step: PipelineStep, task_lock=None):
            (task.folder / "audio_vocals.wav").write_bytes(b"new vocals")
            (task.folder / "audio_instruments.wav").write_bytes(b"new instruments")
            task.status = TaskStatus.SUCCESS
            task.mark_step(step, StepStatus.SUCCESS)
            return task

    monkeypatch.setattr(web_module, "PipelineRunner", FakeRunner)

    web_module._run_step_job(task.id, PipelineStep.SEPARATE_AUDIO)
    task = store.get(task.id)

    assert (task.folder / "audio.wav").read_bytes() == b"old"
    assert (task.folder / "audio_vocals.wav").read_bytes() == b"new vocals"
    assert not (task.folder / "transcript.json").exists()
    assert not (task.folder / "translation.json").exists()
    assert not (task.folder / "video.mp4").exists()
    assert task.steps[PipelineStep.SEPARATE_AUDIO.value] == StepStatus.SUCCESS
    assert task.steps[PipelineStep.TRANSLATE.value] == StepStatus.PENDING


def test_web_schedules_without_holding_task_lock(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")
    task = client.post("/api/tasks/local", json={"source": str(source), "title": "Queued"}).json()
    started = threading.Event()
    release = threading.Event()

    def delayed_job(*args: object, **kwargs: object) -> None:
        started.set()
        release.wait(timeout=2)

    monkeypatch.setattr(web_module, "_run_step_job", delayed_job)

    response = client.post(f"/api/tasks/{task['id']}/run", json={"step": "extract-audio"})

    assert response.status_code == 200
    assert response.json()["running"] is True
    assert response.json()["status"] == "running"
    assert response.json()["steps"]["extract-audio"] == "running"
    probe = TaskLock(Path(task["folder"]), "probe").acquire(blocking=False)
    probe.release()
    release.set()
    web_module._RUNNING[task["id"]].result(timeout=2)


def test_web_runs_different_tasks_concurrently(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    first_source = tmp_path / "first.mp4"
    second_source = tmp_path / "second.mp4"
    first_source.write_bytes(b"first")
    second_source.write_bytes(b"second")
    first = client.post("/api/tasks/local", json={"source": str(first_source), "title": "First"}).json()
    second = client.post("/api/tasks/local", json={"source": str(second_source), "title": "Second"}).json()
    started: set[str] = set()
    started_lock = threading.Lock()
    both_started = threading.Event()
    release = threading.Event()

    def delayed_job(task_id: str, *args: object, **kwargs: object) -> None:
        with started_lock:
            started.add(task_id)
            if len(started) == 2:
                both_started.set()
        release.wait(timeout=2)

    monkeypatch.setattr(web_module, "_run_step_job", delayed_job)

    first_response = client.post(f"/api/tasks/{first['id']}/run", json={"step": "extract-audio"})
    second_response = client.post(f"/api/tasks/{second['id']}/run", json={"step": "extract-audio"})

    try:
        assert first_response.status_code == 200
        assert second_response.status_code == 200
        assert both_started.wait(timeout=1)
        assert started == {first["id"], second["id"]}
    finally:
        release.set()
        web_module._RUNNING[first["id"]].result(timeout=2)
        web_module._RUNNING[second["id"]].result(timeout=2)


def test_web_url_task_accepts_cookies_content_without_echoing_it(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    captured = {}

    def fake_download(url: str, root: Path, config: object) -> DownloadResult:
        captured["config"] = config
        task_dir = root / "Web" / "20260614 CookieSample__abc123"
        task_dir.mkdir(parents=True)
        info = {
            "extractor_key": "YouTube",
            "id": "abc123",
            "title": "CookieSample",
            "uploader": "Web",
            "upload_date": "20260614",
        }
        info_path = task_dir / "download.info.json"
        media_path = task_dir / "download.mp4"
        info_path.write_text(json.dumps(info), encoding="utf-8")
        media_path.write_bytes(b"video")
        return DownloadResult(
            task_dir=task_dir,
            info_path=info_path,
            media_path=media_path,
            cover_path=None,
            info=info,
            source_key="youtube:abc123",
        )

    monkeypatch.setattr(web_module, "download_url_to_artifacts", fake_download)
    cookies_content = (
        "# Netscape HTTP Cookie File\n"
        ".youtube.com TRUE / TRUE 1815872581 LOGIN_INFO secret-value\n"
    )

    response = client.post(
        "/api/tasks/url",
        json={
            "url": "https://example.test/watch?v=abc123",
            "use_cookies": True,
            "cookies_path": "",
            "cookies_content": cookies_content,
        },
    )

    assert response.status_code == 201
    cookies_path = tmp_path / "cookies" / "cookies.txt"
    assert cookies_path.read_text(encoding="utf-8") == (
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1815872581\tLOGIN_INFO\tsecret-value\n"
    )
    assert captured["config"].cookies_path == cookies_path
    assert captured["config"].use_cookies is True
    payload = json.dumps(response.json(), ensure_ascii=False)
    assert "secret-value" not in payload
    assert response.json()["config"]["download"]["cookies_path"] == str(cookies_path)


def test_web_task_download_cookies_uses_saved_path_without_echoing_content(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    draft = client.post(
        "/api/tasks/url-draft",
        json={"url": "https://example.test/watch?v=abc123"},
    ).json()
    custom_path = tmp_path / "task-cookies" / "cookies.txt"
    config = draft["config"]
    config["download"]["cookies_path"] = str(custom_path)
    config["download"]["use_cookies"] = True
    assert client.put(f"/api/tasks/{draft['id']}/config", json={"config": config}).status_code == 200

    cookies_content = (
        "# Netscape HTTP Cookie File\n"
        ".youtube.com TRUE / TRUE 1815872581 LOGIN_INFO task-secret\n"
    )
    response = client.post(
        f"/api/tasks/{draft['id']}/download-cookies",
        json={"content": cookies_content},
    )

    assert response.status_code == 200
    assert response.json()["path"] == str(custom_path)
    assert response.json()["exists"] is True
    assert response.json()["cookie_count"] == 1
    assert "content" not in response.json()
    assert "task-secret" not in json.dumps(response.json())
    assert custom_path.read_text(encoding="utf-8") == (
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1815872581\tLOGIN_INFO\ttask-secret\n"
    )
    saved_tasks = (tmp_path / "tasks" / "tasks.json").read_text(encoding="utf-8")
    assert "task-secret" not in saved_tasks


def test_web_run_step_uses_saved_task_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-env")
    monkeypatch.setenv("HF_READ_TOKEN", "hf-env")
    client = _client(monkeypatch, tmp_path)
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")
    task = client.post("/api/tasks/local", json={"source": str(source), "title": "Run Config"}).json()
    config = task["config"]
    config["translation"]["api_key"] = "sk-task"
    config["translation"]["model"] = "gpt-task"
    config["translation"]["target_language"] = "繁體中文"
    config["translation"]["segment_extra_prompt"] = "使用台灣中文口吻。"
    config["translation"]["correction_prompt"] = "把 tax shooter 视为 Tack Shooter。"
    config["whisperx"]["model_name"] = "medium"
    config["whisperx"]["hf_token"] = "hf-task"
    config["tts"]["cfg_value"] = 3.5
    updated = client.put(f"/api/tasks/{task['id']}/config", json={"config": config})
    assert updated.status_code == 200

    captured = {}

    class FakeRunner:
        def __init__(
            self,
            *,
            whisperx_config: WhisperXConfig,
            translation_config: TranslationConfig,
            tts_config: TTSConfig,
            **kwargs: object,
        ) -> None:
            captured["whisperx"] = whisperx_config
            captured["translation"] = translation_config
            captured["tts"] = tts_config

        def run_step(self, task, step: PipelineStep, task_lock=None):
            task.status = TaskStatus.SUCCESS
            task.mark_step(step, StepStatus.SUCCESS)
            return task

    monkeypatch.setattr(web_module, "PipelineRunner", FakeRunner)

    web_module._run_step_job(task["id"], PipelineStep.TRANSLATE)

    assert captured["translation"].api_key == "sk-task"
    assert captured["translation"].model == "gpt-task"
    assert captured["translation"].target_language == "繁體中文"
    assert captured["translation"].segment_extra_prompt == "使用台灣中文口吻。"
    assert captured["translation"].correction_prompt == "把 tax shooter 视为 Tack Shooter。"
    assert captured["whisperx"].model_name == "medium"
    assert captured["whisperx"].hf_token == "hf-task"
    assert captured["tts"].hf_token == "hf-env"
    assert captured["tts"].cfg_value == 3.5


def test_web_bilibili_step_defaults_to_dry_run_until_confirmed(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")
    task = client.post("/api/tasks/local", json={"source": str(source), "title": "Bili Config"}).json()
    captured = {}

    class FakeRunner:
        def __init__(self, **kwargs: object) -> None:
            captured["bilibili"] = kwargs["bilibili_publish_config"]

        def run_step(self, task, step: PipelineStep, task_lock=None):
            task.status = TaskStatus.SUCCESS
            task.mark_step(step, StepStatus.SUCCESS)
            return task

    monkeypatch.setattr(web_module, "PipelineRunner", FakeRunner)

    web_module._run_step_job(task["id"], PipelineStep.PUBLISH_BILIBILI)

    assert captured["bilibili"].dry_run is True
    assert captured["bilibili"].confirm is False


def test_web_bilibili_step_uses_confirmed_real_upload_config(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")
    task = client.post("/api/tasks/local", json={"source": str(source), "title": "Bili Real"}).json()
    config = task["config"]
    config["bilibili"]["sessdata"] = "sess-task"
    config["bilibili"]["bili_jct"] = "jct-task"
    config["bilibili"]["dry_run"] = False
    config["bilibili"]["confirm"] = True
    updated = client.put(f"/api/tasks/{task['id']}/config", json={"config": config})
    assert updated.status_code == 200
    captured = {}

    class FakeRunner:
        def __init__(self, **kwargs: object) -> None:
            captured["bilibili"] = kwargs["bilibili_publish_config"]

        def run_step(self, task, step: PipelineStep, task_lock=None):
            task.status = TaskStatus.SUCCESS
            task.mark_step(step, StepStatus.SUCCESS)
            return task

    monkeypatch.setattr(web_module, "PipelineRunner", FakeRunner)

    web_module._run_step_job(task["id"], PipelineStep.PUBLISH_BILIBILI)

    assert captured["bilibili"].dry_run is False
    assert captured["bilibili"].confirm is True
    assert captured["bilibili"].sessdata == "sess-task"
    assert captured["bilibili"].bili_jct == "jct-task"


def test_web_refuses_to_start_locked_task(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")
    task = client.post("/api/tasks/local", json={"source": str(source), "title": "Web Smoke"}).json()

    with TaskLock(Path(task["folder"]), "existing"):
        response = client.post(f"/api/tasks/{task['id']}/run", json={"step": "extract-audio"})

    assert response.status_code == 409
    assert response.json()["detail"] == "Task is already running"


def test_web_settings_write_runtime_config_and_do_not_echo_secrets(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    openai = client.post(
        "/api/settings/openai",
        json={
            "api_key": "sk-test",
            "base_url": "https://example.test/v1",
            "model": "gpt-test",
        },
    )
    assert openai.status_code == 200
    assert openai.json() == {
        "base_url": "https://example.test/v1",
        "model": "gpt-test",
        "has_api_key": True,
        "api_key": "********",
    }

    config_path = tmp_path / "config" / "youdub.json"
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["openai"]["api_key"] == "sk-test"

    masked = client.post(
        "/api/settings/openai",
        json={"api_key": "********", "base_url": "", "model": ""},
    )
    assert masked.status_code == 200
    assert masked.json()["has_api_key"] is True
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["openai"] == {"api_key": "sk-test"}

    cleared = client.post(
        "/api/settings/openai",
        json={"api_key": "", "base_url": "", "model": ""},
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_api_key"] is False
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["openai"] == {}

    ytdlp = client.post("/api/settings/ytdlp", json={"proxy": "http://127.0.0.1:7890", "max_height": 720})
    assert ytdlp.status_code == 200
    assert ytdlp.json() == {"proxy": "http://127.0.0.1:7890", "max_height": 720}

    ytdlp = client.post("/api/settings/ytdlp", json={"proxy": "", "max_height": 0})
    assert ytdlp.status_code == 200
    assert ytdlp.json() == {"proxy": "", "max_height": 0}

    cookies = client.post(
        "/api/settings/cookies",
        json={
            "content": (
                "# Netscape HTTP Cookie File\n"
                ".youtube.com\tTRUE\t/\tTRUE\t1815872581\tLOGIN_INFO\tsecret\n"
                ".youtube.com\tTRUE\t/\tFALSE\t1815872581\tSID\tsecret2"
            ),
            "clear": False,
        },
    )
    assert cookies.status_code == 200
    assert cookies.json()["exists"] is True
    assert cookies.json()["content"] == ""
    assert cookies.json()["cookie_count"] == 2
    assert cookies.json()["cookie_domains"] == [".youtube.com"]
    assert cookies.json()["cookies_look_valid"] is True
    assert "LOGIN_INFO" in cookies.json()["cookie_names"]
    assert "secret" not in json.dumps(cookies.json())

    cookies_path = tmp_path / "cookies" / "cookies.txt"
    assert cookies_path.read_text(encoding="utf-8") == (
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1815872581\tLOGIN_INFO\tsecret\n"
        ".youtube.com\tTRUE\t/\tFALSE\t1815872581\tSID\tsecret2\n"
    )
