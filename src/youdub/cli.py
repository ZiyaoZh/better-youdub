from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import AppConfig
from .constants import TEST_VIDEO_URL
from .ingest import create_task_from_download_artifacts, create_task_from_local_media
from .media import require_binary
from .models import PipelineStep
from .pipeline import PipelineRunner
from .storage import TaskStore
from .tts import TTSConfig
from .translation import TranslationConfig
from .transcription import WhisperXConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="youdub")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Check local runtime prerequisites")

    create_task = subparsers.add_parser("create-task", help="Create a task from a local media file")
    create_task.add_argument("--source", required=True, type=Path)
    create_task.add_argument("--title")

    create_download_task = subparsers.add_parser(
        "create-download-task",
        help="Create or reuse a task from local media plus download metadata",
    )
    create_download_task.add_argument("--source", required=True, type=Path)
    create_download_task.add_argument("--info", required=True, type=Path)
    create_download_task.add_argument("--cover", type=Path)

    show_task = subparsers.add_parser("show-task", help="Show a task as JSON")
    show_task.add_argument("task_id")

    run_task = subparsers.add_parser("run-task", help="Run one pipeline step for a task")
    run_task.add_argument("task_id")
    run_task.add_argument(
        "--step",
        choices=[
            PipelineStep.EXTRACT_AUDIO.value,
            PipelineStep.SEPARATE_AUDIO.value,
            PipelineStep.TRANSCRIBE.value,
            PipelineStep.TRANSCRIBE_WHISPER.value,
            PipelineStep.TRANSCRIBE_ALIGN.value,
            PipelineStep.TRANSCRIBE_DIARIZE.value,
            PipelineStep.TRANSLATE.value,
            PipelineStep.TTS.value,
            PipelineStep.TRANSCRIBE_TTS.value,
            PipelineStep.SUBTITLE.value,
        ],
        default=PipelineStep.EXTRACT_AUDIO.value,
    )
    run_task.add_argument(
        "--whisper-model",
        default=os.getenv("YOUDUB_WHISPER_MODEL", "large-v2"),
        help="WhisperX model name for transcription steps",
    )
    run_task.add_argument(
        "--whisper-device",
        default=os.getenv("YOUDUB_WHISPER_DEVICE", "auto"),
        help="WhisperX device: auto, cuda, or cpu",
    )
    run_task.add_argument(
        "--whisper-batch-size",
        type=int,
        default=int(os.getenv("YOUDUB_WHISPER_BATCH_SIZE", "32")),
        help="WhisperX batch size",
    )
    run_task.add_argument(
        "--whisper-language",
        default=_optional_str_env("YOUDUB_WHISPER_LANGUAGE"),
        help="Optional WhisperX language code, for example zh or en",
    )
    run_task.add_argument(
        "--whisper-initial-prompt",
        default=_optional_str_env("YOUDUB_WHISPER_INITIAL_PROMPT"),
        help="Optional Whisper initial prompt for transcription decoding",
    )
    run_task.add_argument(
        "--no-diarization",
        action="store_false",
        dest="diarization",
        default=os.getenv("YOUDUB_WHISPER_DIARIZATION", "1") not in {"0", "false", "False"},
        help="Skip speaker diarization and assign SPEAKER_00 to all segments",
    )
    run_task.add_argument(
        "--min-speakers",
        type=int,
        default=_optional_int_env("YOUDUB_WHISPER_MIN_SPEAKERS"),
    )
    run_task.add_argument(
        "--max-speakers",
        type=int,
        default=_optional_int_env("YOUDUB_WHISPER_MAX_SPEAKERS"),
    )
    run_task.add_argument(
        "--translation-language",
        default=os.getenv("YOUDUB_TRANSLATION_LANGUAGE", "简体中文"),
        help="Target language for translation output",
    )
    run_task.add_argument(
        "--translation-batch-size",
        type=int,
        default=int(os.getenv("YOUDUB_TRANSLATION_BATCH_SIZE", "20")),
        help="Number of transcript segments per translation request",
    )
    run_task.add_argument(
        "--tts-model",
        default=os.getenv("YOUDUB_TTS_MODEL", os.getenv("VOXCPM_MODEL", "openbmb/VoxCPM2")),
        help="VoxCPM2 Hugging Face model id for TTS",
    )
    run_task.add_argument(
        "--tts-model-dir",
        type=Path,
        default=_optional_path_env("YOUDUB_TTS_MODEL_DIR") or _optional_path_env("VOXCPM_MODEL_DIR"),
        help="Optional local VoxCPM2 model directory; bypasses Hugging Face download when set",
    )
    run_task.add_argument(
        "--tts-load-denoiser",
        action="store_true",
        default=_bool_env("YOUDUB_TTS_LOAD_DENOISER", _bool_env("VOXCPM_LOAD_DENOISER", False)),
        help="Load VoxCPM2 denoiser during TTS",
    )
    run_task.add_argument(
        "--tts-cfg-value",
        type=float,
        default=float(os.getenv("YOUDUB_TTS_CFG_VALUE", os.getenv("VOXCPM_CFG_VALUE", "2.0"))),
        help="VoxCPM2 classifier-free guidance value",
    )
    run_task.add_argument(
        "--tts-inference-timesteps",
        type=int,
        default=int(os.getenv("YOUDUB_TTS_INFERENCE_TIMESTEPS", os.getenv("VOXCPM_INFERENCE_TIMESTEPS", "10"))),
        help="VoxCPM2 inference timesteps",
    )
    run_task.add_argument(
        "--tts-min-reference-ms",
        type=int,
        default=int(os.getenv("YOUDUB_TTS_MIN_REFERENCE_MS", os.getenv("VOXCPM_MIN_REFERENCE_MS", "1200"))),
        help="Minimum vocal reference length before falling back to a longer reference",
    )
    run_task.add_argument(
        "--no-tts-align-audio",
        action="store_false",
        dest="tts_align_audio",
        default=_bool_env("YOUDUB_TTS_ALIGN_AUDIO", True),
        help="Disable time-stretch alignment when mixing TTS segments",
    )
    run_task.add_argument(
        "--tts-stretch-base-min",
        type=float,
        default=float(os.getenv("YOUDUB_TTS_STRETCH_BASE_MIN", "0.8")),
        help="Minimum global TTS stretch ratio",
    )
    run_task.add_argument(
        "--tts-stretch-base-max",
        type=float,
        default=float(os.getenv("YOUDUB_TTS_STRETCH_BASE_MAX", "1.2")),
        help="Maximum global TTS stretch ratio",
    )
    run_task.add_argument(
        "--tts-stretch-local-min",
        type=float,
        default=float(os.getenv("YOUDUB_TTS_STRETCH_LOCAL_MIN", "0.9")),
        help="Minimum per-segment TTS stretch correction",
    )
    run_task.add_argument(
        "--tts-stretch-local-max",
        type=float,
        default=float(os.getenv("YOUDUB_TTS_STRETCH_LOCAL_MAX", "1.1")),
        help="Maximum per-segment TTS stretch correction",
    )

    subparsers.add_parser("test-video", help="Print the fixed test video identifier")
    return parser


def cmd_doctor(config: AppConfig) -> int:
    config.ensure_dirs()
    checks = {
        "root": str(config.root),
        "tasks_path": str(config.tasks_path),
        "log_dir": str(config.log_dir),
        "models_dir": str(config.models_dir),
        "config_path": str(config.config_path),
        "huggingface_token_configured": config.secrets.huggingface.token is not None,
        "openai_api_key_configured": config.secrets.openai.api_key is not None,
        "openai_base_url_configured": config.secrets.openai.base_url is not None,
        "ffmpeg": require_binary("ffmpeg"),
    }
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0


def cmd_create_task(config: AppConfig, args: argparse.Namespace) -> int:
    config.ensure_dirs()
    task = create_task_from_local_media(args.source, config.root, args.title)
    TaskStore(config.tasks_path).add(task)
    print(json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_create_download_task(config: AppConfig, args: argparse.Namespace) -> int:
    config.ensure_dirs()
    store = TaskStore(config.tasks_path)
    task = create_task_from_download_artifacts(
        source=args.source,
        info_path=args.info,
        root=config.root,
        cover_path=args.cover,
    )
    task = store.upsert(task)
    print(json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_show_task(config: AppConfig, args: argparse.Namespace) -> int:
    task = TaskStore(config.tasks_path).get(args.task_id)
    print(json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_run_task(config: AppConfig, args: argparse.Namespace) -> int:
    store = TaskStore(config.tasks_path)
    task = store.get(args.task_id)
    step = PipelineStep(args.step)
    whisperx_config = WhisperXConfig(
        models_dir=config.models_dir,
        model_name=args.whisper_model,
        device=args.whisper_device,
        batch_size=args.whisper_batch_size,
        diarization=args.diarization,
        min_speakers=args.min_speakers,
        max_speakers=args.max_speakers,
        hf_token=config.secrets.huggingface.token,
        language=args.whisper_language,
        initial_prompt=args.whisper_initial_prompt,
    )
    translation_config = TranslationConfig(
        api_key=config.secrets.openai.api_key,
        base_url=config.secrets.openai.base_url,
        model=config.secrets.openai.model,
        target_language=args.translation_language,
        batch_size=args.translation_batch_size,
        max_retries=int(os.getenv("YOUDUB_TRANSLATION_MAX_RETRIES", "4")),
        retry_backoff_seconds=float(os.getenv("YOUDUB_TRANSLATION_RETRY_BACKOFF_SECONDS", "1")),
        retry_backoff_multiplier=float(os.getenv("YOUDUB_TRANSLATION_RETRY_BACKOFF_MULTIPLIER", "2")),
        retry_max_backoff_seconds=float(os.getenv("YOUDUB_TRANSLATION_RETRY_MAX_BACKOFF_SECONDS", "8")),
        force_json_output=os.getenv("YOUDUB_TRANSLATION_FORCE_JSON_OUTPUT", "1") not in {"0", "false", "False"},
        temperature=float(os.getenv("YOUDUB_TRANSLATION_TEMPERATURE", "0")),
    )
    tts_config = TTSConfig(
        model=args.tts_model,
        model_dir=args.tts_model_dir,
        hf_token=config.secrets.huggingface.token,
        load_denoiser=args.tts_load_denoiser,
        cfg_value=args.tts_cfg_value,
        inference_timesteps=args.tts_inference_timesteps,
        min_reference_ms=args.tts_min_reference_ms,
        align_audio=args.tts_align_audio,
        stretch_base_min=args.tts_stretch_base_min,
        stretch_base_max=args.tts_stretch_base_max,
        stretch_local_min=args.tts_stretch_local_min,
        stretch_local_max=args.tts_stretch_local_max,
    )
    try:
        task = PipelineRunner(
            whisperx_config=whisperx_config,
            translation_config=translation_config,
            tts_config=tts_config,
        ).run_step(task, step)
    finally:
        store.update(task)
    print(json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _optional_int_env(name: str) -> int | None:
    value = os.getenv(name)
    if not value:
        return None
    return int(value)


def _optional_path_env(name: str) -> Path | None:
    value = os.getenv(name)
    if not value:
        return None
    return Path(value)


def _optional_str_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value not in {"0", "false", "False"}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = AppConfig.from_env()

    try:
        if args.command == "doctor":
            return cmd_doctor(config)
        if args.command == "create-task":
            return cmd_create_task(config, args)
        if args.command == "create-download-task":
            return cmd_create_download_task(config, args)
        if args.command == "show-task":
            return cmd_show_task(config, args)
        if args.command == "run-task":
            return cmd_run_task(config, args)
        if args.command == "test-video":
            print(TEST_VIDEO_URL)
            return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
