from pathlib import Path

from youdub.config import AppConfig
from youdub.task_config import (
    MASKED_SECRET,
    WEB_TRANSLATION_BASE_URL_DEFAULT,
    WEB_TRANSLATION_MODEL_DEFAULT,
    default_task_config,
    effective_task_config,
    normalize_task_config_update,
    runtime_options_from_task_config,
)


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

    options = runtime_options_from_task_config(config, {})

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


def test_task_config_exposes_web_translation_defaults(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_ROOT", str(tmp_path / "videos"))
    monkeypatch.setenv("YOUDUB_TASKS_PATH", str(tmp_path / "tasks" / "tasks.json"))
    monkeypatch.setenv("YOUDUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("YOUDUB_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)

    config = AppConfig.from_env()
    task_config = default_task_config(config)
    options = runtime_options_from_task_config(config, task_config)

    assert task_config["translation"]["base_url"] == WEB_TRANSLATION_BASE_URL_DEFAULT
    assert task_config["translation"]["model"] == WEB_TRANSLATION_MODEL_DEFAULT
    assert task_config["translation"]["proxy"] == ""
    assert options.translation.base_url == WEB_TRANSLATION_BASE_URL_DEFAULT
    assert options.translation.model == WEB_TRANSLATION_MODEL_DEFAULT
    assert options.translation.proxy is None


def test_task_config_exposes_translation_proxy_default_and_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_ROOT", str(tmp_path / "videos"))
    monkeypatch.setenv("YOUDUB_TASKS_PATH", str(tmp_path / "tasks" / "tasks.json"))
    monkeypatch.setenv("YOUDUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("YOUDUB_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))
    monkeypatch.setenv("YOUDUB_TRANSLATION_PROXY", "socks5h://127.0.0.1:1081")

    config = AppConfig.from_env()
    task_config = default_task_config(config)
    options = runtime_options_from_task_config(config, task_config)

    assert task_config["translation"]["proxy"] == "socks5h://127.0.0.1:1081"
    assert options.translation.proxy == "socks5h://127.0.0.1:1081"

    task_config["translation"]["proxy"] = ""
    options = runtime_options_from_task_config(config, task_config)

    assert options.translation.proxy is None


def test_task_config_exposes_web_tts_defaults(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_ROOT", str(tmp_path / "videos"))
    monkeypatch.setenv("YOUDUB_TASKS_PATH", str(tmp_path / "tasks" / "tasks.json"))
    monkeypatch.setenv("YOUDUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("YOUDUB_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))
    monkeypatch.delenv("YOUDUB_TTS_INFERENCE_TIMESTEPS", raising=False)
    monkeypatch.delenv("VOXCPM_INFERENCE_TIMESTEPS", raising=False)
    monkeypatch.delenv("YOUDUB_TTS_MIN_REFERENCE_MS", raising=False)
    monkeypatch.delenv("VOXCPM_MIN_REFERENCE_MS", raising=False)
    monkeypatch.delenv("YOUDUB_TTS_START_PAD_MS", raising=False)
    monkeypatch.delenv("YOUDUB_TTS_END_PAD_MS", raising=False)

    config = AppConfig.from_env()
    task_config = default_task_config(config)
    options = runtime_options_from_task_config(config, task_config)

    assert task_config["tts"]["inference_timesteps"] == 10
    assert task_config["tts"]["min_reference_ms"] == 1200
    assert task_config["tts"]["start_pad_ms"] == 80
    assert task_config["tts"]["end_pad_ms"] == 160
    assert options.tts.inference_timesteps == 10
    assert options.tts.min_reference_ms == 1200
    assert options.tts.start_pad_ms == 80
    assert options.tts.end_pad_ms == 160


def test_task_config_exposes_tts_redub_defaults(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_ROOT", str(tmp_path / "videos"))
    monkeypatch.setenv("YOUDUB_TASKS_PATH", str(tmp_path / "tasks" / "tasks.json"))
    monkeypatch.setenv("YOUDUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("YOUDUB_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))

    config = AppConfig.from_env()
    task_config = default_task_config(config)
    options = runtime_options_from_task_config(config, task_config)

    assert task_config["workflow"]["enable_tts_redub"] is False
    assert task_config["workflow"]["tts_redub_max_rounds"] == 1
    assert task_config["tts_quality"]["include_review"] is False
    assert task_config["tts_quality"]["max_segments_per_round"] == 50
    assert task_config["redub_tts"]["round"] == 1
    assert options.tts_quality.max_segments_per_round == 50
    assert options.redub_tts.max_rounds == 1


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

    assert updated == {
        "download": {"max_height": 480},
        "translation": {
            "api_key": "sk-current",
            "model": "gpt-updated",
            "segment_extra_prompt": "旧分段提示",
        },
        "tts": {"cfg_value": 3.0},
    }
    effective = effective_task_config(config, updated)
    assert effective["download"]["max_height"] == 480
    assert effective["translation"]["api_key"] == ""
    assert effective["translation"]["model"] == "gpt-updated"
    assert effective["translation"]["segment_extra_prompt"] == "旧分段提示"
    assert effective["tts"]["cfg_value"] == 3.0
