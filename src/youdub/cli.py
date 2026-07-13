from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import AppConfig
from .constants import TEST_VIDEO_URL
from .downloader import download_url_to_artifacts, supported_js_runtimes
from .ingest import create_task_from_download_artifacts, create_task_from_local_media
from .media import require_binary
from .models import PipelineStep
from .pipeline import PipelineRunner
from .synthesis import ffmpeg_has_filter
from .storage import TaskStore
from .task_config import (
    default_task_config,
    download_config_from_task_config,
    merge_task_config_overrides,
    runtime_options_from_task_config,
    sparse_task_config,
)


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
        default=argparse.SUPPRESS,
        help="Optional Netscape cookies.txt path; defaults to YOUDUB_COOKIES_PATH",
    )
    create_url_task.add_argument(
        "--no-cookies",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Ignore YOUDUB_COOKIES_PATH and do not pass cookies to yt-dlp",
    )
    create_url_task.add_argument(
        "--proxy",
        default=argparse.SUPPRESS,
        help="Optional yt-dlp proxy URL; defaults to YOUDUB_YTDLP_PROXY",
    )
    create_url_task.add_argument(
        "--max-height",
        type=int,
        default=argparse.SUPPRESS,
        help="Preferred maximum video height; use 0 for no height limit",
    )
    create_url_task.add_argument(
        "--force-download",
        action="store_true",
        default=argparse.SUPPRESS,
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
            PipelineStep.INSPECT_TTS.value,
            PipelineStep.REDUB_TTS.value,
            PipelineStep.SYNTHESIZE.value,
            PipelineStep.PREPARE_PUBLISH.value,
            PipelineStep.PUBLISH_BILIBILI.value,
        ],
        default=PipelineStep.EXTRACT_AUDIO.value,
    )
    run_task.add_argument(
        "--whisper-model",
        default=argparse.SUPPRESS,
        help="WhisperX model name for transcription steps",
    )
    run_task.add_argument(
        "--whisper-device",
        default=argparse.SUPPRESS,
        help="WhisperX device: auto, cuda, or cpu",
    )
    run_task.add_argument(
        "--whisper-batch-size",
        type=int,
        default=argparse.SUPPRESS,
        help="WhisperX batch size",
    )
    run_task.add_argument(
        "--whisper-language",
        default=argparse.SUPPRESS,
        help="Optional WhisperX language code, for example zh or en",
    )
    run_task.add_argument(
        "--whisper-initial-prompt",
        default=argparse.SUPPRESS,
        help="Optional Whisper initial prompt for transcription decoding",
    )
    run_task.add_argument(
        "--no-diarization",
        action="store_false",
        dest="diarization",
        default=argparse.SUPPRESS,
        help="Skip speaker diarization and assign SPEAKER_00 to all segments",
    )
    run_task.add_argument(
        "--min-speakers",
        type=int,
        default=argparse.SUPPRESS,
    )
    run_task.add_argument(
        "--max-speakers",
        type=int,
        default=argparse.SUPPRESS,
    )
    run_task.add_argument(
        "--translation-language",
        default=argparse.SUPPRESS,
        help="Target language for translation output",
    )
    run_task.add_argument(
        "--translation-batch-size",
        type=int,
        default=argparse.SUPPRESS,
        help="Number of transcript segments per translation request",
    )
    run_task.add_argument(
        "--translation-extra-prompt",
        default=argparse.SUPPRESS,
        help="Additional prompt applied to all translation model requests",
    )
    run_task.add_argument(
        "--translation-summary-extra-prompt",
        default=argparse.SUPPRESS,
        help="Additional prompt applied to summary translation",
    )
    run_task.add_argument(
        "--translation-context-extra-prompt",
        default=argparse.SUPPRESS,
        help="Additional prompt applied to translation context generation",
    )
    run_task.add_argument(
        "--translation-segment-extra-prompt",
        default=argparse.SUPPRESS,
        help="Additional prompt applied to segment translation",
    )
    run_task.add_argument(
        "--translation-correction-prompt",
        default=argparse.SUPPRESS,
        help="Prompt for glossary, ASR correction, and special translation fixes",
    )
    run_task.add_argument(
        "--tts-model",
        default=argparse.SUPPRESS,
        help="VoxCPM2 Hugging Face model id for TTS",
    )
    run_task.add_argument(
        "--tts-model-dir",
        type=Path,
        default=argparse.SUPPRESS,
        help="Optional local VoxCPM2 model directory; bypasses Hugging Face download when set",
    )
    run_task.add_argument(
        "--tts-load-denoiser",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Load VoxCPM2 denoiser during TTS",
    )
    run_task.add_argument(
        "--tts-cfg-value",
        type=float,
        default=argparse.SUPPRESS,
        help="VoxCPM2 classifier-free guidance value",
    )
    run_task.add_argument(
        "--tts-inference-timesteps",
        type=int,
        default=argparse.SUPPRESS,
        help="VoxCPM2 inference timesteps",
    )
    run_task.add_argument(
        "--tts-min-reference-ms",
        type=int,
        default=argparse.SUPPRESS,
        help="Minimum vocal reference length before falling back to a longer reference",
    )
    run_task.add_argument(
        "--tts-start-pad-ms",
        type=int,
        default=argparse.SUPPRESS,
        help="Milliseconds of source vocal audio to prepend to each TTS reference segment",
    )
    run_task.add_argument(
        "--tts-end-pad-ms",
        type=int,
        default=argparse.SUPPRESS,
        help="Milliseconds of source vocal audio to append to each TTS reference segment",
    )
    run_task.add_argument(
        "--no-tts-align-audio",
        action="store_false",
        dest="tts_align_audio",
        default=argparse.SUPPRESS,
        help="Disable time-stretch alignment when mixing TTS segments",
    )
    run_task.add_argument(
        "--tts-cache-model",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Keep VoxCPM2 loaded after TTS for faster subsequent runs",
    )
    run_task.add_argument(
        "--tts-tower-path-pronunciation",
        choices=["dash", "compact", "off"],
        default=argparse.SUPPRESS,
        help="How TTS should pronounce tower path strings like 2-0-5",
    )
    run_task.add_argument(
        "--tts-stretch-base-min",
        type=float,
        default=argparse.SUPPRESS,
        help="Minimum global TTS stretch ratio",
    )
    run_task.add_argument(
        "--tts-stretch-base-max",
        type=float,
        default=argparse.SUPPRESS,
        help="Maximum global TTS stretch ratio",
    )
    run_task.add_argument(
        "--tts-stretch-local-min",
        type=float,
        default=argparse.SUPPRESS,
        help="Minimum per-segment TTS stretch correction",
    )
    run_task.add_argument(
        "--tts-stretch-local-max",
        type=float,
        default=argparse.SUPPRESS,
        help="Maximum per-segment TTS stretch correction",
    )
    run_task.add_argument(
        "--tts-quality-include-review",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Include review-severity TTS quality segments in the redub plan",
    )
    run_task.add_argument(
        "--tts-quality-max-segments-per-round",
        type=int,
        default=argparse.SUPPRESS,
        help="Maximum TTS segments to redub in one round",
    )
    run_task.add_argument(
        "--tts-quality-max-task-hard-ratio",
        type=float,
        default=argparse.SUPPRESS,
        help="Hard-fail ratio above which the report flags the task for review",
    )
    run_task.add_argument(
        "--tts-redub-round",
        type=int,
        default=argparse.SUPPRESS,
        help="Current TTS redub round",
    )
    run_task.add_argument(
        "--tts-redub-max-rounds",
        type=int,
        default=argparse.SUPPRESS,
        help="Maximum TTS redub rounds",
    )
    run_task.add_argument(
        "--no-burn-subtitles",
        action="store_false",
        dest="burn_subtitles",
        default=argparse.SUPPRESS,
        help="Do not burn subtitles into the synthesized video",
    )
    run_task.add_argument(
        "--synthesis-tts-volume",
        type=float,
        default=argparse.SUPPRESS,
        help="TTS voice volume used during final audio mix",
    )
    run_task.add_argument(
        "--synthesis-instruments-volume",
        type=float,
        default=argparse.SUPPRESS,
        help="Background/instrument audio volume used during final audio mix",
    )
    run_task.add_argument(
        "--synthesis-preset",
        default=argparse.SUPPRESS,
        help="libx264 preset used for final video rendering",
    )
    run_task.add_argument(
        "--synthesis-crf",
        type=int,
        default=argparse.SUPPRESS,
        help="libx264 CRF used for final video rendering",
    )
    run_task.add_argument(
        "--subtitle-language",
        default=argparse.SUPPRESS,
        help="Subtitle style language key, currently zh or en",
    )
    run_task.add_argument(
        "--subtitle-font",
        default=argparse.SUPPRESS,
        help="Optional font family for burned subtitles",
    )
    run_task.add_argument(
        "--publish-title-max-chars",
        type=int,
        default=argparse.SUPPRESS,
        help="Maximum generated publish title length",
    )
    run_task.add_argument(
        "--publish-max-tags",
        type=int,
        default=argparse.SUPPRESS,
        help="Maximum generated publish tag count",
    )
    run_task.add_argument(
        "--publish-max-tag-chars",
        type=int,
        default=argparse.SUPPRESS,
        help="Maximum length for each generated publish tag",
    )
    run_task.add_argument(
        "--publish-dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Validate Bilibili publish metadata without uploading",
    )
    run_task.add_argument(
        "--publish-force",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Upload again even if bilibili.json already exists",
    )
    run_task.add_argument(
        "--publish-confirm",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Required for a real Bilibili upload",
    )
    run_task.add_argument(
        "--bilibili-tid",
        type=int,
        default=argparse.SUPPRESS,
        help="Bilibili category id for upload",
    )
    run_task.add_argument(
        "--bilibili-original",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Mark Bilibili submission as original",
    )
    run_task.add_argument(
        "--bilibili-source",
        default=argparse.SUPPRESS,
        help="Optional Bilibili转载来源; defaults to source URL when not original",
    )
    run_task.add_argument(
        "--no-bilibili-watermark",
        action="store_false",
        dest="bilibili_watermark",
        default=argparse.SUPPRESS,
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
        "ytdlp_js_runtimes": sorted(supported_js_runtimes()),
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
    task_config = _create_url_task_cli_overrides(config, args)
    download_config = download_config_from_task_config(config, task_config)
    result = download_url_to_artifacts(args.url, config.root, download_config)
    task = create_task_from_download_artifacts(
        source=result.media_path,
        info_path=result.info_path,
        root=config.root,
        cover_path=result.cover_path,
    )
    task.config = task_config
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
    cli_overrides = _run_task_cli_overrides(args)
    runtime_config = merge_task_config_overrides(task.config, cli_overrides)
    options = runtime_options_from_task_config(config, runtime_config)
    try:
        task = PipelineRunner(
            whisperx_config=options.whisperx,
            translation_config=options.translation,
            tts_config=options.tts,
            synthesis_config=options.synthesis,
            publish_config=options.publish,
            bilibili_publish_config=options.bilibili,
            tts_quality_config=options.tts_quality,
            redub_tts_config=options.redub_tts,
        ).run_step(task, step)
    finally:
        store.update(task)
    print(json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _create_url_task_cli_overrides(config: AppConfig, args: argparse.Namespace) -> dict[str, object]:
    effective = default_task_config(config)
    download = effective["download"]
    if hasattr(args, "cookies"):
        download["use_cookies"] = True
        download["cookies_path"] = str(args.cookies)
    if hasattr(args, "no_cookies"):
        download["use_cookies"] = False
        download["cookies_path"] = ""
    if hasattr(args, "proxy"):
        download["proxy"] = args.proxy
    if hasattr(args, "max_height"):
        download["max_height"] = args.max_height
    if hasattr(args, "force_download"):
        download["force_download"] = True
    return sparse_task_config(default_task_config(config), effective)


def _run_task_cli_overrides(args: argparse.Namespace) -> dict[str, object]:
    overrides: dict[str, object] = {}
    _set_cli_override(overrides, args, "whisper_model", "whisperx", "model_name")
    _set_cli_override(overrides, args, "whisper_device", "whisperx", "device")
    _set_cli_override(overrides, args, "whisper_batch_size", "whisperx", "batch_size")
    _set_cli_override(overrides, args, "whisper_language", "whisperx", "language")
    _set_cli_override(overrides, args, "whisper_initial_prompt", "whisperx", "initial_prompt")
    _set_cli_override(overrides, args, "diarization", "whisperx", "diarization")
    _set_cli_override(overrides, args, "min_speakers", "whisperx", "min_speakers")
    _set_cli_override(overrides, args, "max_speakers", "whisperx", "max_speakers")

    _set_cli_override(overrides, args, "translation_language", "translation", "target_language")
    _set_cli_override(overrides, args, "translation_batch_size", "translation", "batch_size")
    _set_cli_override(overrides, args, "translation_extra_prompt", "translation", "extra_prompt")
    _set_cli_override(overrides, args, "translation_summary_extra_prompt", "translation", "summary_extra_prompt")
    _set_cli_override(overrides, args, "translation_context_extra_prompt", "translation", "context_extra_prompt")
    _set_cli_override(overrides, args, "translation_segment_extra_prompt", "translation", "segment_extra_prompt")
    _set_cli_override(overrides, args, "translation_correction_prompt", "translation", "correction_prompt")

    _set_cli_override(overrides, args, "tts_model", "tts", "model")
    _set_cli_override(overrides, args, "tts_model_dir", "tts", "model_dir", transform=_path_text)
    _set_cli_override(overrides, args, "tts_load_denoiser", "tts", "load_denoiser")
    _set_cli_override(overrides, args, "tts_cfg_value", "tts", "cfg_value")
    _set_cli_override(overrides, args, "tts_inference_timesteps", "tts", "inference_timesteps")
    _set_cli_override(overrides, args, "tts_min_reference_ms", "tts", "min_reference_ms")
    _set_cli_override(overrides, args, "tts_start_pad_ms", "tts", "start_pad_ms")
    _set_cli_override(overrides, args, "tts_end_pad_ms", "tts", "end_pad_ms")
    _set_cli_override(overrides, args, "tts_align_audio", "tts", "align_audio")
    _set_cli_override(overrides, args, "tts_cache_model", "tts", "cache_model")
    _set_cli_override(overrides, args, "tts_tower_path_pronunciation", "tts", "tower_path_pronunciation")
    _set_cli_override(overrides, args, "tts_stretch_base_min", "tts", "stretch_base_min")
    _set_cli_override(overrides, args, "tts_stretch_base_max", "tts", "stretch_base_max")
    _set_cli_override(overrides, args, "tts_stretch_local_min", "tts", "stretch_local_min")
    _set_cli_override(overrides, args, "tts_stretch_local_max", "tts", "stretch_local_max")

    _set_cli_override(overrides, args, "tts_quality_include_review", "tts_quality", "include_review")
    _set_cli_override(overrides, args, "tts_quality_max_segments_per_round", "tts_quality", "max_segments_per_round")
    _set_cli_override(overrides, args, "tts_quality_max_task_hard_ratio", "tts_quality", "max_task_hard_ratio")
    _set_cli_override(overrides, args, "tts_redub_round", "tts_quality", "round")
    _set_cli_override(overrides, args, "tts_redub_round", "redub_tts", "round")
    _set_cli_override(overrides, args, "tts_redub_max_rounds", "workflow", "tts_redub_max_rounds")

    _set_cli_override(overrides, args, "burn_subtitles", "synthesis", "burn_subtitles")
    _set_cli_override(overrides, args, "synthesis_tts_volume", "synthesis", "tts_volume")
    _set_cli_override(overrides, args, "synthesis_instruments_volume", "synthesis", "instruments_volume")
    _set_cli_override(overrides, args, "synthesis_preset", "synthesis", "video_preset")
    _set_cli_override(overrides, args, "synthesis_crf", "synthesis", "video_crf")
    _set_cli_override(overrides, args, "subtitle_language", "synthesis", "subtitle_language")
    _set_cli_override(overrides, args, "subtitle_font", "synthesis", "subtitle_font")

    _set_cli_override(overrides, args, "publish_title_max_chars", "publish", "max_title_chars")
    _set_cli_override(overrides, args, "publish_max_tags", "publish", "max_tags")
    _set_cli_override(overrides, args, "publish_max_tag_chars", "publish", "max_tag_chars")
    _set_cli_override(overrides, args, "publish_dry_run", "bilibili", "dry_run")
    _set_cli_override(overrides, args, "publish_force", "bilibili", "force")
    _set_cli_override(overrides, args, "publish_confirm", "bilibili", "confirm")
    _set_cli_override(overrides, args, "bilibili_tid", "bilibili", "tid")
    _set_cli_override(overrides, args, "bilibili_original", "bilibili", "original")
    _set_cli_override(overrides, args, "bilibili_source", "bilibili", "source")
    _set_cli_override(overrides, args, "bilibili_watermark", "bilibili", "watermark")
    return overrides


def _set_cli_override(
    overrides: dict[str, object],
    args: argparse.Namespace,
    attr: str,
    section: str,
    field: str,
    *,
    transform=None,
) -> None:
    if not hasattr(args, attr):
        return
    value = getattr(args, attr)
    if transform is not None:
        value = transform(value)
    section_values = overrides.setdefault(section, {})
    if isinstance(section_values, dict):
        section_values[field] = value


def _path_text(value: object) -> str:
    return str(value) if value is not None else ""


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
