from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from youdub.downloader import DownloadResult
from youdub.locking import TaskLock
from youdub.models import PipelineStep, StepStatus, TaskStatus
from youdub.translation import TranslationConfig
from youdub.transcription import WhisperXConfig
from youdub.tts import TTSConfig
from youdub import web as web_module
from youdub.web import create_app


def _client(monkeypatch, tmp_path: Path) -> TestClient:
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
    client = _client(monkeypatch, tmp_path)
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")

    defaults = client.get("/api/task-config/defaults")
    assert defaults.status_code == 200
    assert defaults.json()["config"]["download"]["max_height"] == 0

    task = client.post("/api/tasks/local", json={"source": str(source), "title": "Config Smoke"}).json()
    assert task["config"]["whisperx"]["model_name"] == "large-v2"
    assert task["config"]["translation"]["api_key"] == ""

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
