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


def _create_voxcpm_snapshot(hf_home: Path, commit: str = "abc123") -> Path:
    repository = hf_home / "hub" / "models--openbmb--VoxCPM2"
    snapshot = repository / "snapshots" / commit
    snapshot.mkdir(parents=True)
    (repository / "refs").mkdir()
    (repository / "refs" / "main").write_text(f"{commit}\n", encoding="utf-8")
    for name in ("config.json", "model.safetensors", "audiovae.pth", "tokenizer.json"):
        (snapshot / name).write_text(name, encoding="utf-8")
    return snapshot


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
    assert "Skywarden/skywarden as 天卫" in task_config["translation"]["correction_prompt"]
    assert "Vortex as 漩涡" in task_config["translation"]["correction_prompt"]
    assert task_config["whisperx"]["hf_token"] == ""
    assert task_config["demucs"]["device"] == "auto"
    assert task_config["tts"]["device"] == "auto"

    options = runtime_options_from_task_config(config, {})

    assert options.translation.api_key == "sk-env"
    assert options.translation.model == "gpt-env"
    assert "Bloons TD 6" in options.translation.correction_prompt
    assert "Skywarden/skywarden as 天卫" in options.translation.correction_prompt
    assert "Vortex as 漩涡" in options.translation.correction_prompt
    assert options.whisperx.hf_token == "hf_env"
    assert options.tts.hf_token == "hf_env"


def test_task_config_passes_demucs_and_tts_devices_to_runtime_options(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))
    config = AppConfig.from_env()

    options = runtime_options_from_task_config(
        config,
        {
            "demucs": {"device": "cuda:1"},
            "tts": {"device": "cuda:2"},
        },
    )

    assert options.demucs.device == "cuda:1"
    assert options.tts.device == "cuda:2"


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
    assert "network" not in task_config
    assert "proxy" not in task_config["translation"]
    assert options.translation.base_url == WEB_TRANSLATION_BASE_URL_DEFAULT
    assert options.translation.model == WEB_TRANSLATION_MODEL_DEFAULT
    assert options.translation.proxy is None


def test_task_config_keeps_scheduled_publish_out_of_platform_options(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))
    config = AppConfig.from_env()

    defaults = default_task_config(config)
    updated = normalize_task_config_update(
        config,
        {},
        {"publish": {"scheduled_at": "2026-08-03T09:30:00+08:00"}},
    )
    options = runtime_options_from_task_config(config, updated)

    assert defaults["publish"]["scheduled_at"] == ""
    assert updated == {"publish": {"scheduled_at": "2026-08-03T09:30:00+08:00"}}
    assert not hasattr(options.publish, "scheduled_at")
    assert not hasattr(options.bilibili, "scheduled_at")


def test_task_config_uses_system_network_proxy_for_network_clients(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_ROOT", str(tmp_path / "videos"))
    monkeypatch.setenv("YOUDUB_TASKS_PATH", str(tmp_path / "tasks" / "tasks.json"))
    monkeypatch.setenv("YOUDUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("YOUDUB_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))
    monkeypatch.setenv("YOUDUB_NETWORK_PROXY", "socks5h://127.0.0.1:1081")

    config = AppConfig.from_env()
    task_config = default_task_config(config)
    options = runtime_options_from_task_config(config, task_config)

    assert "network" not in task_config
    assert options.translation.proxy == "socks5h://127.0.0.1:1081"
    assert options.whisperx.proxy == "socks5h://127.0.0.1:1081"
    assert options.tts.proxy == "socks5h://127.0.0.1:1081"
    assert options.bilibili.proxy == "socks5h://127.0.0.1:1081"

    options = runtime_options_from_task_config(config, {"network": {"proxy": ""}})

    assert options.translation.proxy == "socks5h://127.0.0.1:1081"
    assert options.whisperx.proxy == "socks5h://127.0.0.1:1081"
    assert options.tts.proxy == "socks5h://127.0.0.1:1081"


def test_task_config_exposes_bilibili_proxy_default_and_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_ROOT", str(tmp_path / "videos"))
    monkeypatch.setenv("YOUDUB_TASKS_PATH", str(tmp_path / "tasks" / "tasks.json"))
    monkeypatch.setenv("YOUDUB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("YOUDUB_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))
    monkeypatch.setenv("BILI_PROXY", "http://127.0.0.1:7890")

    config = AppConfig.from_env()
    task_config = default_task_config(config)
    options = runtime_options_from_task_config(config, task_config)

    assert "proxy" not in task_config["bilibili"]
    assert options.bilibili.proxy == "http://127.0.0.1:7890"

    options = runtime_options_from_task_config(
        config,
        {"network": {"proxy": "socks5h://127.0.0.1:1081"}},
    )

    assert options.bilibili.proxy == "http://127.0.0.1:7890"


def test_task_config_ignores_legacy_task_proxy_fields(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))
    config = AppConfig.from_env()

    effective = effective_task_config(
        config,
        {"translation": {"proxy": "socks5h://127.0.0.1:1081"}},
    )
    options = runtime_options_from_task_config(
        config,
        {"translation": {"proxy": "socks5h://127.0.0.1:1081"}},
    )

    assert "network" not in effective
    assert "proxy" not in effective["translation"]
    assert options.translation.proxy is None
    assert options.whisperx.proxy is None

    download_proxy = effective_task_config(
        config,
        {
            "translation": {"proxy": ""},
            "download": {"proxy": "http://127.0.0.1:7890"},
        },
    )
    assert "network" not in download_proxy
    assert "proxy" not in download_proxy["download"]

    monkeypatch.setenv("YOUDUB_NETWORK_PROXY", "socks5h://127.0.0.1:1081")
    config = AppConfig.from_env()
    legacy_empty_proxy = effective_task_config(
        config,
        {"translation": {"proxy": ""}, "download": {"proxy": ""}},
    )
    assert "network" not in legacy_empty_proxy
    options = runtime_options_from_task_config(config, legacy_empty_proxy)
    assert options.translation.proxy == "socks5h://127.0.0.1:1081"


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
    assert task_config["tts"]["tower_path_pronunciation"] == "dash"
    assert options.tts.inference_timesteps == 10
    assert options.tts.min_reference_ms == 1200
    assert options.tts.start_pad_ms == 80
    assert options.tts.end_pad_ms == 160
    assert options.tts.tower_path_pronunciation == "dash"


def test_task_config_defaults_to_complete_cached_voxcpm_snapshot(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "huggingface"))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("YOUDUB_TTS_MODEL_DIR", raising=False)
    monkeypatch.delenv("VOXCPM_MODEL_DIR", raising=False)
    snapshot = _create_voxcpm_snapshot(tmp_path / "huggingface")
    config = AppConfig.from_env()

    task_config = default_task_config(config)
    options = runtime_options_from_task_config(config, {})

    assert task_config["tts"]["model_dir"] == str(snapshot)
    assert options.tts.model_dir == snapshot


def test_task_config_migrates_legacy_empty_tts_model_dir_to_cached_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YOUDUB_CONFIG_PATH", str(tmp_path / "config" / "youdub.json"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "huggingface"))
    monkeypatch.delenv("YOUDUB_TTS_MODEL_DIR", raising=False)
    monkeypatch.delenv("VOXCPM_MODEL_DIR", raising=False)
    snapshot = _create_voxcpm_snapshot(tmp_path / "huggingface")
    config = AppConfig.from_env()
    defaults = default_task_config(config)
    legacy_tts = {**defaults["tts"], "model_dir": ""}

    migrated = effective_task_config(config, {"tts": legacy_tts})
    explicit_remote = effective_task_config(config, {"tts": {"model_dir": ""}})

    assert migrated["tts"]["model_dir"] == str(snapshot)
    assert explicit_remote["tts"]["model_dir"] == ""


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
