import json
from pathlib import Path

from youdub.config import AppConfig, SecretsConfig


def test_secrets_config_reads_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("HF_READ_TOKEN", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    path = tmp_path / "youdub.json"
    path.write_text(
        json.dumps(
            {
                "huggingface": {"token": "hf_file"},
                "openai": {
                    "api_key": "sk_file",
                    "base_url": "https://api.example.test/v1",
                    "model": "gpt-test",
                },
            }
        ),
        encoding="utf-8",
    )

    config = SecretsConfig.from_file_and_env(path)

    assert config.huggingface.token == "hf_file"
    assert config.openai.api_key == "sk_file"
    assert config.openai.base_url == "https://api.example.test/v1"
    assert config.openai.model == "gpt-test"


def test_secrets_config_env_overrides_file(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "youdub.json"
    path.write_text(
        json.dumps(
            {
                "huggingface": {"token": "hf_file"},
                "openai": {"api_key": "sk_file", "base_url": "file", "model": "file"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HF_READ_TOKEN", "hf_env")
    monkeypatch.setenv("OPENAI_API_KEY", "sk_env")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example.test/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-env")

    config = SecretsConfig.from_file_and_env(path)

    assert config.huggingface.token == "hf_env"
    assert config.openai.api_key == "sk_env"
    assert config.openai.base_url == "https://env.example.test/v1"
    assert config.openai.model == "gpt-env"


def test_app_config_reads_config_path_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HF_READ_TOKEN", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    config_path = tmp_path / "config" / "youdub.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps({"huggingface": {"token": "hf_file"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YOUDUB_ROOT", str(tmp_path / "videos"))
    monkeypatch.setenv("YOUDUB_TASKS_PATH", str(tmp_path / "tasks" / "tasks.json"))
    monkeypatch.setenv("YOUDUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("YOUDUB_MODELS_DIR", str(tmp_path / "models"))

    config = AppConfig.from_env()

    assert config.config_path == config_path
    assert config.secrets.huggingface.token == "hf_file"


def test_app_config_download_max_height_defaults_to_unlimited(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config" / "youdub.json"
    config_path.parent.mkdir()
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YOUDUB_DOWNLOAD_MAX_HEIGHT", "")

    config = AppConfig.from_env()

    assert config.download_max_height == 0


def test_app_config_download_max_height_file_and_env_precedence(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config" / "youdub.json"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps({"ytdlp": {"max_height": 0}}), encoding="utf-8")
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YOUDUB_DOWNLOAD_MAX_HEIGHT", "")

    config = AppConfig.from_env()

    assert config.download_max_height == 0

    monkeypatch.setenv("YOUDUB_DOWNLOAD_MAX_HEIGHT", "720")

    config = AppConfig.from_env()

    assert config.download_max_height == 720


def test_app_config_reads_translation_proxy_from_file_and_env(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config" / "youdub.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps({"translation": {"proxy": "socks5h://127.0.0.1:1081"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("YOUDUB_TRANSLATION_PROXY", raising=False)

    config = AppConfig.from_env()

    assert config.translation_prompts.proxy == "socks5h://127.0.0.1:1081"

    monkeypatch.setenv("YOUDUB_TRANSLATION_PROXY", "http://127.0.0.1:18080")

    config = AppConfig.from_env()

    assert config.translation_prompts.proxy == "http://127.0.0.1:18080"
