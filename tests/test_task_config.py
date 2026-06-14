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
    assert "Bloons TD 6" in task_config["translation"]["correction_prompt"]
    assert task_config["whisperx"]["hf_token"] == ""

    options = runtime_options_from_task_config(config, task_config)

    assert options.translation.api_key == "sk-env"
    assert options.translation.model == "gpt-env"
    assert "Bloons TD 6" in options.translation.correction_prompt
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
    task_config["translation"]["segment_extra_prompt"] = "使用中文主播口吻。"
    task_config["translation"]["correction_prompt"] = "把 tax shooter 视为 Tack Shooter。"
    task_config["whisperx"]["hf_token"] = "hf_task"

    options = runtime_options_from_task_config(config, task_config)

    assert options.translation.api_key == "sk-task"
    assert options.translation.segment_extra_prompt == "使用中文主播口吻。"
    assert options.translation.correction_prompt == "把 tax shooter 视为 Tack Shooter。"
    assert options.whisperx.hf_token == "hf_task"
    assert options.tts.hf_token == "hf_env"


def test_task_config_loads_translation_prompts_from_runtime_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_ROOT", str(tmp_path / "videos"))
    monkeypatch.setenv("YOUDUB_TASKS_PATH", str(tmp_path / "tasks" / "tasks.json"))
    monkeypatch.setenv("YOUDUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("YOUDUB_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))
    config_path = tmp_path / "config" / "youdub.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        """
{
  "translation": {
    "extra_prompt": "全局提示",
    "summary_extra_prompt": "摘要提示",
    "context_extra_prompt": "上下文提示",
    "segment_extra_prompt": "分段提示",
    "correction_prompt": "纠错提示"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config = AppConfig.from_env()
    options = runtime_options_from_task_config(config, default_task_config(config))

    assert options.translation.extra_prompt == "全局提示"
    assert options.translation.summary_extra_prompt == "摘要提示"
    assert options.translation.context_extra_prompt == "上下文提示"
    assert options.translation.segment_extra_prompt == "分段提示"
    assert options.translation.correction_prompt == "纠错提示"


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
    current["translation"]["segment_extra_prompt"] = "旧分段提示"
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
    assert updated["translation"]["segment_extra_prompt"] == "旧分段提示"
    assert updated["tts"]["cfg_value"] == 3.0
