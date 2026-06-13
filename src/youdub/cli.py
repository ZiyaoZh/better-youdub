from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import AppConfig
from .constants import TEST_VIDEO_URL
from .downloader import DownloadConfig, download_url_to_artifacts
from .ingest import create_task_from_download_artifacts, create_task_from_local_media
from .media import require_binary
from .models import PipelineStep
from .pipeline import PipelineRunner
from .publishing import BilibiliPublishConfig, PublishPackageConfig
from .synthesis import SynthesisConfig, ffmpeg_has_filter
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

    create_url_task = subparsers.add_parser(
        "create-url-task",
        help="Download one URL with yt-dlp and create or reuse a task",
    )
    create_url_task.add_argument("--url", required=True)
    create_url_task.add_argument(
        "--cookies",
        type=Path,
        help="Optional Netscape cookies.txt path; defaults to YOUDUB_COOKIES_PATH",
    )
    create_url_task.add_argument(
        "--no-cookies",
        action="store_true",
        help="Ignore YOUDUB_COOKIES_PATH and do not pass cookies to yt-dlp",
    )
    create_url_task.add_argument(
        "--proxy",
        help="Optional yt-dlp proxy URL; defaults to YOUDUB_YTDLP_PROXY",
    )
    create_url_task.add_argument(
        "--max-height",
        type=int,
        help="Preferred maximum video height for the first yt-dlp format candidate",
    )
    create_url_task.add_argument(
        "--force-download",
        action="store_true",
        help="Download media again even if download.mp4 already exists",
    )

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
            PipelineStep.SYNTHESIZE.value,
            PipelineStep.PREPARE_PUBLISH.value,
            PipelineStep.PUBLISH_BILIBILI.value,
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
    run_task.add_argument(
        "--no-burn-subtitles",
        action="store_false",
        dest="burn_subtitles",
        default=_bool_env("YOUDUB_BURN_SUBTITLES", True),
        help="Do not burn subtitles into the synthesized video",
    )
    run_task.add_argument(
        "--synthesis-tts-volume",
        type=float,
        default=float(os.getenv("YOUDUB_SYNTHESIS_TTS_VOLUME", "1.0")),
        help="TTS voice volume used during final audio mix",
    )
    run_task.add_argument(
        "--synthesis-instruments-volume",
        type=float,
        default=float(os.getenv("YOUDUB_SYNTHESIS_INSTRUMENTS_VOLUME", "0.30")),
        help="Background/instrument audio volume used during final audio mix",
    )
    run_task.add_argument(
        "--synthesis-preset",
        default=os.getenv("YOUDUB_SYNTHESIS_PRESET", "fast"),
        help="libx264 preset used for final video rendering",
    )
    run_task.add_argument(
        "--synthesis-crf",
        type=int,
        default=int(os.getenv("YOUDUB_SYNTHESIS_CRF", "23")),
        help="libx264 CRF used for final video rendering",
    )
    run_task.add_argument(
        "--subtitle-language",
        default=os.getenv("YOUDUB_SUBTITLE_LANGUAGE", "zh"),
        help="Subtitle style language key, currently zh or en",
    )
    run_task.add_argument(
        "--subtitle-font",
        default=_optional_str_env("YOUDUB_SUBTITLE_FONT"),
        help="Optional font family for burned subtitles",
    )
    run_task.add_argument(
        "--publish-title-max-chars",
        type=int,
        default=int(os.getenv("YOUDUB_PUBLISH_TITLE_MAX_CHARS", "80")),
        help="Maximum generated publish title length",
    )
    run_task.add_argument(
        "--publish-max-tags",
        type=int,
        default=int(os.getenv("YOUDUB_PUBLISH_MAX_TAGS", "10")),
        help="Maximum generated publish tag count",
    )
    run_task.add_argument(
        "--publish-max-tag-chars",
        type=int,
        default=int(os.getenv("YOUDUB_PUBLISH_MAX_TAG_CHARS", "20")),
        help="Maximum length for each generated publish tag",
    )
    run_task.add_argument(
        "--publish-dry-run",
        action="store_true",
        default=_bool_env("YOUDUB_PUBLISH_DRY_RUN", False),
        help="Validate Bilibili publish metadata without uploading",
    )
    run_task.add_argument(
        "--publish-force",
        action="store_true",
        default=_bool_env("YOUDUB_PUBLISH_FORCE", False),
        help="Upload again even if bilibili.json already exists",
    )
    run_task.add_argument(
        "--publish-confirm",
        action="store_true",
        default=_bool_env("YOUDUB_PUBLISH_CONFIRM", False),
        help="Required for a real Bilibili upload",
    )
    run_task.add_argument(
        "--bilibili-tid",
        type=int,
        default=_optional_int_env("BILI_TID") or 201,
        help="Bilibili category id for upload",
    )
    run_task.add_argument(
        "--bilibili-original",
        action="store_true",
        default=_bool_env("BILI_ORIGINAL", False),
        help="Mark Bilibili submission as original",
    )
    run_task.add_argument(
        "--bilibili-source",
        default=_optional_str_env("BILI_SOURCE"),
        help="Optional Bilibili转载来源; defaults to source URL when not original",
    )
    run_task.add_argument(
        "--no-bilibili-watermark",
        action="store_false",
        dest="bilibili_watermark",
        default=_bool_env("BILI_WATERMARK", True),
        help="Disable Bilibili watermark flag",
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
        "cookies_path": str(config.cookies_path) if config.cookies_path is not None else None,
        "cookies_configured": _existing_nonempty_file(config.cookies_path),
        "ytdlp_proxy_configured": config.ytdlp_proxy is not None,
        "download_max_height": config.download_max_height,
        "huggingface_token_configured": config.secrets.huggingface.token is not None,
        "openai_api_key_configured": config.secrets.openai.api_key is not None,
        "openai_base_url_configured": config.secrets.openai.base_url is not None,
        "ffmpeg": require_binary("ffmpeg"),
        "ffprobe": require_binary("ffprobe"),
        "ffmpeg_subtitles_filter": ffmpeg_has_filter("subtitles"),
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


def cmd_create_url_task(config: AppConfig, args: argparse.Namespace) -> int:
    config.ensure_dirs()
    cookies_path = None if args.no_cookies else (args.cookies or config.cookies_path)
    download_config = DownloadConfig(
        cookies_path=cookies_path,
        proxy=args.proxy if args.proxy is not None else config.ytdlp_proxy,
        max_height=args.max_height if args.max_height is not None else config.download_max_height,
        force=args.force_download,
        use_cookies=not args.no_cookies,
    )
    result = download_url_to_artifacts(args.url, config.root, download_config)
    task = create_task_from_download_artifacts(
        source=result.media_path,
        info_path=result.info_path,
        root=config.root,
        cover_path=result.cover_path,
    )
    task = TaskStore(config.tasks_path).upsert(task)
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
    synthesis_config = SynthesisConfig(
        burn_subtitles=args.burn_subtitles,
        tts_volume=args.synthesis_tts_volume,
        instruments_volume=args.synthesis_instruments_volume,
        video_preset=args.synthesis_preset,
        video_crf=args.synthesis_crf,
        subtitle_language=args.subtitle_language,
        subtitle_font=args.subtitle_font,
    )
    publish_config = PublishPackageConfig(
        max_title_chars=args.publish_title_max_chars,
        max_tags=args.publish_max_tags,
        max_tag_chars=args.publish_max_tag_chars,
    )
    bilibili_publish_config = BilibiliPublishConfig(
        sessdata=_optional_str_env("BILI_SESSDATA"),
        bili_jct=_optional_str_env("BILI_BILI_JCT"),
        tid=args.bilibili_tid,
        original=args.bilibili_original,
        source=args.bilibili_source,
        watermark=args.bilibili_watermark,
        dry_run=args.publish_dry_run,
        force=args.publish_force,
        confirm=args.publish_confirm,
    )
    try:
        task = PipelineRunner(
            whisperx_config=whisperx_config,
            translation_config=translation_config,
            tts_config=tts_config,
            synthesis_config=synthesis_config,
            publish_config=publish_config,
            bilibili_publish_config=bilibili_publish_config,
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


def _existing_nonempty_file(path: Path | None) -> bool:
    return path is not None and path.exists() and path.is_file() and path.stat().st_size > 0


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
        if args.command == "create-url-task":
            return cmd_create_url_task(config, args)
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
