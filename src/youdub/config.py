from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected string config value, got {type(value).__name__}")
    value = value.strip()
    return value or None


def _int_or_default(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return default
        return int(cleaned)
    raise ValueError(f"Expected integer config value, got {type(value).__name__}")


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected object for config section: {name}")
    return value


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class HuggingFaceConfig:
    token: str | None = None


@dataclass(frozen=True)
class SecretsConfig:
    openai: OpenAIConfig
    huggingface: HuggingFaceConfig

    @classmethod
    def from_file_and_env(cls, path: Path) -> "SecretsConfig":
        data = _load_json_object(path)
        openai = _section(data, "openai")
        huggingface = _section(data, "huggingface")

        return cls(
            openai=OpenAIConfig(
                api_key=_clean(os.getenv("OPENAI_API_KEY"))
                or _clean(openai.get("api_key")),
                base_url=_clean(os.getenv("OPENAI_BASE_URL"))
                or _clean(os.getenv("OPENAI_API_BASE"))
                or _clean(openai.get("base_url")),
                model=_clean(os.getenv("OPENAI_MODEL"))
                or _clean(os.getenv("MODEL_NAME"))
                or _clean(openai.get("model")),
            ),
            huggingface=HuggingFaceConfig(
                token=_clean(os.getenv("HF_READ_TOKEN"))
                or _clean(os.getenv("HF_TOKEN"))
                or _clean(huggingface.get("token")),
            ),
        )


@dataclass(frozen=True)
class AppConfig:
    root: Path
    tasks_path: Path
    log_dir: Path
    models_dir: Path
    config_path: Path
    cookies_path: Path | None
    ytdlp_proxy: str | None
    download_max_height: int
    secrets: SecretsConfig

    @classmethod
    def from_env(cls) -> "AppConfig":
        root = Path(os.getenv("YOUDUB_ROOT", "/data/videos"))
        config_path = Path(os.getenv("YOUDUB_CONFIG_PATH", "/data/config/youdub.json"))
        data = _load_json_object(config_path)
        ytdlp = _section(data, "ytdlp")
        cookies_value = _clean(os.getenv("YOUDUB_COOKIES_PATH"))
        return cls(
            root=root,
            tasks_path=Path(os.getenv("YOUDUB_TASKS_PATH", "/data/tasks/tasks.json")),
            log_dir=Path(os.getenv("YOUDUB_LOG_DIR", "/data/logs")),
            models_dir=Path(os.getenv("YOUDUB_MODELS_DIR", "/models")),
            config_path=config_path,
            cookies_path=Path(cookies_value) if cookies_value else None,
            ytdlp_proxy=_clean(os.getenv("YOUDUB_YTDLP_PROXY")) or _clean(ytdlp.get("proxy")),
            download_max_height=_int_or_default(
                _clean(os.getenv("YOUDUB_DOWNLOAD_MAX_HEIGHT")) or ytdlp.get("max_height"),
                0,
            ),
            secrets=SecretsConfig.from_file_and_env(config_path),
        )

    def ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.tasks_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        if self.cookies_path is not None:
            self.cookies_path.parent.mkdir(parents=True, exist_ok=True)
