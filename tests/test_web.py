from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from youdub.locking import TaskLock
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
