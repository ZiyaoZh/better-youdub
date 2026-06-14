from pathlib import Path

from youdub.config import AppConfig
from youdub.task_config import MASKED_SECRET, default_task_config, normalize_task_config_update, runtime_options_from_task_config


def test_task_config_empty_secret_defaults_fall_back_to_runtime_secrets(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_ROOT", str(tmp_path / "videos"))
    monkeypatch.setenv("YOUDUB_TASKS_PATH", str(tmp_path / "tasks" / "tasks.json"))
    monkeypatch.setenv("YOUDUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("YOUDUB_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-env")
    monkeypatch.setenv("HF_READ_TOKEN", "hf_env")

    config = AppConfig.from_env()
    task_config = default_task_config(config)
    assert task_config["translation"]["api_key"] == ""
    assert task_config["whisperx"]["hf_token"] == ""

    options = runtime_options_from_task_config(config, task_config)

    assert options.translation.api_key == "sk-env"
    assert options.translation.model == "gpt-env"
    assert options.whisperx.hf_token == "hf_env"
    assert options.tts.hf_token == "hf_env"


def test_task_config_secret_overrides_runtime_secrets(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_ROOT", str(tmp_path / "videos"))
    monkeypatch.setenv("YOUDUB_TASKS_PATH", str(tmp_path / "tasks" / "tasks.json"))
    monkeypatch.setenv("YOUDUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("YOUDUB_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("HF_READ_TOKEN", "hf_env")

    config = AppConfig.from_env()
    task_config = default_task_config(config)
    task_config["translation"]["api_key"] = "sk-task"
    task_config["whisperx"]["hf_token"] = "hf_task"

    options = runtime_options_from_task_config(config, task_config)

    assert options.translation.api_key == "sk-task"
    assert options.whisperx.hf_token == "hf_task"
    assert options.tts.hf_token == "hf_env"


def test_task_config_partial_update_preserves_sections_and_masked_secrets(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_ROOT", str(tmp_path / "videos"))
    monkeypatch.setenv("YOUDUB_TASKS_PATH", str(tmp_path / "tasks" / "tasks.json"))
    monkeypatch.setenv("YOUDUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("YOUDUB_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))

    config = AppConfig.from_env()
    current = default_task_config(config)
    current["translation"]["api_key"] = "sk-current"
    current["translation"]["model"] = "gpt-current"
    current["tts"]["cfg_value"] = 3.0

    updated = normalize_task_config_update(
        config,
        current,
        {
            "download": {"max_height": 480},
            "translation": {"api_key": MASKED_SECRET, "model": "gpt-updated"},
        },
    )

    assert updated["download"]["max_height"] == 480
    assert updated["translation"]["api_key"] == "sk-current"
    assert updated["translation"]["model"] == "gpt-updated"
    assert updated["tts"]["cfg_value"] == 3.0
