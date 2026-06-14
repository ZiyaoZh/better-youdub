from __future__ import annotations

import json
import hashlib
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUMMARY_OUTPUT = "summary.json"
CONTEXT_OUTPUT = "translation.context.json"
SEGMENTS_OUTPUT = "translation.segments.json"
FINAL_OUTPUT = "translation.json"
TASK_SUMMARY_FIELDS = ("title", "author", "summary", "tags")
SUMMARY_SCHEMA_VERSION = 1
SUMMARY_PROMPT_VERSION = "summary-v2"
CONTEXT_SCHEMA_VERSION = 1
SEGMENTS_SCHEMA_VERSION = 2
CONTEXT_PROMPT_VERSION = "translation-context-v1"
TRANSLATION_PROMPT_VERSION = "translation-v2"
_ALL_SPLIT_PUNCTUATION = ",，、;；:：.!?。！？"
_SOURCE_SENTENCE_ENDINGS = ".!?。！？;；"
MIN_TRANSLATION_PART_DURATION = 0.2

DEFAULT_TRANSLATION_EXTRA_PROMPT = ""
DEFAULT_SUMMARY_EXTRA_PROMPT = (
    "Translate and localize the video title, summary, and tags into natural target-language wording. "
    "Prefer concise titles suitable for publishing. Tags should be short, searchable phrases rather than sentences."
)
DEFAULT_CONTEXT_EXTRA_PROMPT = (
    "Build a practical glossary for this exact video. Include recurring proper nouns, domain terms, game terms, "
    "technical terms, acronyms, and names whose translation must stay consistent. "
    "For ASR corrections, only include high-confidence mistakes that are strongly supported by the full context."
)
DEFAULT_SEGMENT_EXTRA_PROMPT = (
    "Write the translation as spoken dubbing copy from a native creator's point of view. "
    "Use idiomatic, natural, concise wording in the target language. Avoid translationese. "
    "Do not add audience-addressing filler, explanations, labels, markdown, LaTeX, or special formatting."
)
DEFAULT_CORRECTION_PROMPT = (
    "If the video is about Bloons TD 6 or related games, apply these term preferences. "
    "Keep MOAB, BFB, ZOMG, DDT, BAD, and Ninja Kiwi in English. Translate paragon as 模范, "
    "Bloonarius as 充气机, Lych as 巫妖, Vortex as 空气大师, DreadBloon as 恐怖气球, "
    "Phayze as 菲兹, BLASTAPOPOULOS as 轰炸飞艇, diamondback as 菱背, Blons as 金发女郎, "
    "CHIMPS as 超猩猩模式, pops as 击破数, Popsaiden as 波塞冬, and Bloons TD Battles 2 as 气球塔防对战2. "
    "first targeting, strong targeting, and last targeting should be translated as 第一个目标, 强力目标, and 最后一个目标. "
    "When ASR says tax or tag in a tower context, it is likely Tack and should be translated as 图钉. "
    "Hero names: Quincy=昆西, Gwendolin=格温多琳, Striker Jones=先锋琼斯, Obyn Greenfoot=奥本, "
    "Rosalia=罗莎莉娅, Captain Churchill=上尉丘吉尔, Benjamin=本杰明, Pat Fusty=帕特, "
    "Ezili=艾泽里, Adora=阿多拉, Etienne=艾蒂安, Sauda=萨乌达, Admiral Brickell=海军上将布里克尔, "
    "Psi=灵机, Geraldo=杰拉尔多, Corvus=科沃斯, Silas=西拉斯, Dan D'Monke=丹. "
    "Tower names: Dart Monkey=毛毛, Boomerang Monkey=回旋镖猴, Bomb Shooter=大炮, Tack Shooter=图钉塔, "
    "Ice Monkey=冰猴, Glue Gunner=胶水猴, Desperado=亡命猴, Sniper Monkey=狙击猴, "
    "Monkey Sub=潜水艇猴, Monkey Buccaneer=海盗猴, Monkey Ace=王牌飞行员, Heli Pilot=直升机, "
    "Mortar Monkey=迫击炮猴, Dartling Gunner=机枪猴, Wizard Monkey=法师猴, Super Monkey=超猴, "
    "Ninja Monkey=忍者猴, Alchemist=炼金术士, Druid=德鲁伊, Mer Monkey=人鱼猴, Banana Farm=香蕉农场, "
    "Spike Factory=刺钉工厂, Monkey Village=猴村, Engineer Monkey=工程师猴, Beast Handler=驯兽大师. "
    "Do not add or preserve unnecessary audience callouts such as 兄弟们, 家人们, or 朋友们 unless they are essential source meaning."
)


@dataclass(frozen=True)
class TranslationConfig:
    api_key: str | None
    model: str | None
    base_url: str | None = None
    target_language: str = "简体中文"
    batch_size: int = 20
    timeout_seconds: float = 240.0
    max_retries: int = 4
    retry_backoff_seconds: float = 1.0
    retry_backoff_multiplier: float = 2.0
    retry_max_backoff_seconds: float = 8.0
    force_json_output: bool = True
    temperature: float = 0.0
    extra_prompt: str = DEFAULT_TRANSLATION_EXTRA_PROMPT
    summary_extra_prompt: str = DEFAULT_SUMMARY_EXTRA_PROMPT
    context_extra_prompt: str = DEFAULT_CONTEXT_EXTRA_PROMPT
    segment_extra_prompt: str = DEFAULT_SEGMENT_EXTRA_PROMPT
    correction_prompt: str = DEFAULT_CORRECTION_PROMPT

    def validate(self) -> None:
        if not self.api_key:
            raise ValueError("OpenAI API key is required for translation")
        if not self.model:
            raise ValueError("OpenAI model is required for translation")
        if self.batch_size <= 0:
            raise ValueError("Translation batch size must be positive")
        if self.max_retries <= 0:
            raise ValueError("Translation max retries must be positive")
        if self.retry_backoff_seconds < 0:
            raise ValueError("Translation retry backoff must be non-negative")
        if self.retry_backoff_multiplier < 1.0:
            raise ValueError("Translation retry multiplier must be at least 1.0")
        if self.retry_max_backoff_seconds < 0:
            raise ValueError("Translation retry max backoff must be non-negative")


@dataclass(frozen=True)
class SourceClause:
    text: str
    start: float
    end: float


class TranslationResponseError(ValueError):
    """Raised when a model response cannot be normalized into the expected JSON payload."""


def translate_task(task_dir: Path, config: TranslationConfig) -> Path:
    config.validate()
    info = _read_json_object(task_dir / "download.info.json")
    transcript = _read_json_list(task_dir / "transcript.json")

    client = _create_openai_client(config)
    summary = ensure_summary(task_dir, info, transcript, client, config)
    context = ensure_translation_context(task_dir, info, summary, transcript, client, config)
    translated_segments = ensure_segment_translations(
        task_dir,
        info,
        summary,
        context,
        transcript,
        client,
        config,
    )
    final_entries = build_tts_translation_entries(translated_segments)
    return _write_json(task_dir / FINAL_OUTPUT, final_entries)


def build_tts_translation_entries(translated_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in translated_segments:
        translation_text = _clean_text(item.get("translation"))
        if not translation_text:
            continue

        segment_id = int(item["segment_id"])
        source_text = str(item.get("text", "")).strip()
        output.append(
            {
                "segment_id": segment_id,
                "part_id": 0,
                "start": round(float(item["start"]), 3),
                "end": round(float(item["end"]), 3),
                "speaker": str(item.get("speaker", "SPEAKER_00")),
                "text": source_text,
                "source_text": source_text,
                "translation": translation_text,
            }
        )
    return output


def ensure_summary(
    task_dir: Path,
    info: dict[str, Any],
    transcript: list[dict[str, Any]],
    client: Any,
    config: TranslationConfig,
) -> dict[str, Any]:
    summary_path = task_dir / SUMMARY_OUTPUT
    source_hash = _summary_source_hash(info, transcript, config)
    prompt_hash = _summary_prompt_hash(config)
    if summary_path.exists():
        summary = _read_json_object(summary_path)
        if _valid_summary(summary, source_hash, prompt_hash, config):
            return summary

    author = _author_from_info(info)
    payload = {
        "title": _title_from_info(info),
        "author": author,
        "description": str(info.get("description") or "").strip(),
        "tags": _string_list(info.get("tags"), limit=20),
        "categories": _string_list(info.get("categories"), limit=10),
        "transcript_excerpt": _transcript_excerpt(transcript),
        "target_language": config.target_language,
    }
    messages = [
        {
            "role": "system",
            "content": _prompt_with_optional_sections(
                (
                    "You translate video metadata for a dubbing pipeline. "
                    "Return one JSON object with keys: title, summary, tags. "
                    "title and summary must be natural spoken-style text in the target language. "
                    "tags must be a JSON array of short strings in the target language. "
                    "Do not add markdown or commentary."
                ),
                ("Global translation instructions", config.extra_prompt),
                ("Summary-specific instructions", config.summary_extra_prompt),
                ("Correction and glossary rules", config.correction_prompt),
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, indent=2),
        },
    ]
    result = _chat_json(
        client,
        config,
        messages,
        schema_name="summary_translation",
        schema=_summary_response_schema(),
        normalize=_normalize_summary_response,
    )

    title = _clean_text(result.get("title")) or _title_from_info(info)
    summary_text = _clean_text(result.get("summary"))
    if not summary_text:
        raise ValueError("Summary translation returned an empty summary")

    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "prompt_version": SUMMARY_PROMPT_VERSION,
        "target_language": config.target_language,
        "model": config.model,
        "source_hash": source_hash,
        "prompt_hash": prompt_hash,
        "title": title,
        "author": author,
        "summary": summary_text,
        "tags": _string_list(result.get("tags"), limit=20) or _string_list(info.get("tags"), limit=20),
    }
    return _write_json(summary_path, summary)


def ensure_translation_context(
    task_dir: Path,
    info: dict[str, Any],
    summary: dict[str, Any],
    transcript: list[dict[str, Any]],
    client: Any,
    config: TranslationConfig,
) -> dict[str, Any]:
    context_path = task_dir / CONTEXT_OUTPUT
    source_hash = _translation_context_source_hash(info, summary, transcript, config)
    prompt_hash = _translation_context_prompt_hash(config)
    if context_path.exists():
        existing = _read_json_object(context_path)
        if _valid_translation_context(existing, source_hash, prompt_hash, config):
            return existing

    payload = {
        "title": _title_from_info(info),
        "author": _author_from_info(info),
        "description": str(info.get("description") or "").strip(),
        "summary": str(summary.get("summary") or "").strip(),
        "tags": _string_list(info.get("tags"), limit=20),
        "categories": _string_list(info.get("categories"), limit=10),
        "target_language": config.target_language,
        "transcript": _full_transcript_text(transcript),
    }
    messages = [
        {
            "role": "system",
            "content": _prompt_with_optional_sections(
                (
                    "You prepare context for a video subtitle translation pipeline. "
                    "Read the metadata and full transcript, then return one JSON object with keys: "
                    "content_summary, glossary, corrections. "
                    "content_summary must be 3-5 concise sentences in the target language. "
                    "glossary must contain useful recurring names, brands, game terms, technical terms, "
                    "and acronyms as objects with source and target. If a term should remain unchanged, "
                    "set target equal to source. "
                    "corrections must contain only high-confidence ASR mistakes as objects with wrong and correct. "
                    "Do not include ordinary words, speculative corrections, markdown, or commentary."
                ),
                ("Global translation instructions", config.extra_prompt),
                ("Context-building instructions", config.context_extra_prompt),
                ("Correction and glossary rules", config.correction_prompt),
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, indent=2),
        },
    ]
    try:
        result = _chat_json(
            client,
            config,
            messages,
            schema_name="translation_context",
            schema=_translation_context_response_schema(),
            normalize=_normalize_translation_context_response,
        )
        status = "success"
        error = None
    except Exception as exc:
        result = {"content_summary": "", "glossary": [], "corrections": []}
        status = "failed"
        error = str(exc)

    context = {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "prompt_version": CONTEXT_PROMPT_VERSION,
        "status": status,
        "target_language": config.target_language,
        "source_hash": source_hash,
        "prompt_hash": prompt_hash,
        "content_summary": result["content_summary"],
        "glossary": result["glossary"],
        "corrections": result["corrections"],
    }
    if error:
        context["error"] = error
    return _write_json(context_path, context)


def ensure_segment_translations(
    task_dir: Path,
    info: dict[str, Any],
    summary: dict[str, Any],
    context: dict[str, Any],
    transcript: list[dict[str, Any]],
    client: Any,
    config: TranslationConfig,
) -> list[dict[str, Any]]:
    output_path = task_dir / SEGMENTS_OUTPUT
    context_hash = _stable_hash(_translation_context_for_prompt(context))
    prompt_hash = _segment_translation_prompt_hash(config)
    existing = _load_existing_segment_translations(output_path, context_hash, prompt_hash, config)
    complete: dict[int, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []

    for segment_id, segment in enumerate(transcript):
        record = {
            "segment_id": segment_id,
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "speaker": str(segment.get("speaker", "SPEAKER_00")),
            "text": str(segment.get("text", "")).strip(),
        }
        prior = existing.get(segment_id)
        if _matches_segment(prior, record) and _clean_text(prior.get("translation")):
            complete[segment_id] = prior
        else:
            pending.append(record)

    for batch in _chunked(pending, config.batch_size):
        translated = _translate_batch(client, info, summary, context, batch, config)
        for item in translated:
            segment_id = item["segment_id"]
            if segment_id not in {segment["segment_id"] for segment in batch}:
                raise ValueError(f"Unexpected segment id in translation response: {segment_id}")
            source = next(segment for segment in batch if segment["segment_id"] == segment_id)
            complete[segment_id] = {
                **source,
                "translation": item["translation"],
            }
        _write_json(
            output_path,
            _segment_cache_payload(complete, context_hash, prompt_hash, config),
        )

    missing = [
        segment["segment_id"]
        for segment in pending
        if segment["segment_id"] not in complete
    ]
    if missing:
        raise ValueError(f"Missing translations for segment ids: {missing}")

    ordered = [complete[index] for index in range(len(transcript))]
    _write_json(output_path, _segment_cache_payload(complete, context_hash, prompt_hash, config))
    return ordered


def build_translation_entries(
    translated_segments: list[dict[str, Any]],
    diarized: dict[str, Any],
) -> list[dict[str, Any]]:
    detailed_segments = diarized.get("segments")
    if not isinstance(detailed_segments, list):
        raise ValueError("WhisperX diarized output does not contain a segment list")

    output: list[dict[str, Any]] = []
    for item in translated_segments:
        translation_text = _clean_text(item.get("translation"))
        if not translation_text:
            continue

        segment_id = int(item["segment_id"])
        start = float(item["start"])
        end = float(item["end"])
        speaker = str(item.get("speaker", "SPEAKER_00"))
        source_text = str(item.get("text", "")).strip()
        translated_parts = split_translation_text(translation_text)
        source_clauses = extract_source_clauses(
            source_text=source_text,
            segment_start=start,
            segment_end=end,
            speaker=speaker,
            detailed_segments=detailed_segments,
        )
        aligned_parts = align_translation_parts(
            translated_parts=translated_parts,
            source_clauses=source_clauses,
            segment_start=start,
            segment_end=end,
            source_text=source_text,
        )

        for part_id, part in enumerate(aligned_parts):
            output.append(
                {
                    "segment_id": segment_id,
                    "part_id": part_id,
                    "start": round(part["start"], 3),
                    "end": round(part["end"], 3),
                    "speaker": speaker,
                    "text": part["source_text"],
                    "source_text": part["source_text"],
                    "translation": part["translation"],
                }
            )
    return output


def split_translation_text(text: str, max_chars: int = 24) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []

    pieces = _split_text_attaching_punctuation(normalized, r"([。！？!?；;])")
    refined: list[str] = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if len(piece) <= max_chars:
            refined.append(piece)
            continue
        refined.extend(_split_text_attaching_punctuation(piece, r"([，,、：:])"))

    final_parts: list[str] = []
    for piece in refined:
        piece = piece.strip()
        if not piece:
            continue
        if len(piece) <= max_chars:
            final_parts.append(piece)
            continue
        final_parts.extend(_chunk_text(piece, max_chars))

    return _merge_short_translation_parts([part for part in (part.strip() for part in final_parts) if part], max_chars)


def extract_source_clauses(
    source_text: str,
    segment_start: float,
    segment_end: float,
    speaker: str,
    detailed_segments: list[dict[str, Any]],
) -> list[SourceClause]:
    words = _collect_words_for_segment(
        detailed_segments=detailed_segments,
        segment_start=segment_start,
        segment_end=segment_end,
        speaker=speaker,
    )
    if not words:
        return _fallback_source_clauses(source_text, segment_start, segment_end)

    clauses: list[SourceClause] = []
    buffer: list[dict[str, Any]] = []
    for word in words:
        if not isinstance(word, dict):
            continue
        if "start" not in word or "end" not in word:
            continue
        text = str(word.get("word") or "").strip()
        if not text:
            continue
        buffer.append(
            {
                "word": text,
                "start": float(word["start"]),
                "end": float(word["end"]),
            }
        )
        if text[-1] in _SOURCE_SENTENCE_ENDINGS:
            clauses.append(_source_clause_from_words(buffer))
            buffer = []

    if buffer:
        clauses.append(_source_clause_from_words(buffer))

    return clauses or _fallback_source_clauses(source_text, segment_start, segment_end)


def align_translation_parts(
    translated_parts: list[str],
    source_clauses: list[SourceClause],
    segment_start: float,
    segment_end: float,
    source_text: str,
) -> list[dict[str, Any]]:
    if not translated_parts:
        return []
    if not source_clauses:
        return _proportional_parts(
            translated_parts=translated_parts,
            source_text=source_text,
            start=segment_start,
            end=segment_end,
        )

    if len(source_clauses) >= len(translated_parts):
        output: list[dict[str, Any]] = []
        clause_count = len(source_clauses)
        part_count = len(translated_parts)
        for part_index, translated in enumerate(translated_parts):
            start_index = math.floor(part_index * clause_count / part_count)
            end_index = math.floor((part_index + 1) * clause_count / part_count) - 1
            end_index = max(end_index, start_index)
            group = source_clauses[start_index : end_index + 1]
            output.append(
                {
                    "translation": translated,
                    "source_text": _join_clause_text(group),
                    "start": group[0].start,
                    "end": group[-1].end,
                }
            )
        output[0]["start"] = segment_start
        output[-1]["end"] = segment_end
        return _normalize_aligned_part_times(output, translated_parts, source_text, segment_start, segment_end)

    output: list[dict[str, Any]] = []
    part_count = len(translated_parts)
    clause_count = len(source_clauses)
    for clause_index, clause in enumerate(source_clauses):
        part_start = math.floor(clause_index * part_count / clause_count)
        part_end = math.floor((clause_index + 1) * part_count / clause_count)
        assigned_parts = translated_parts[part_start:part_end]
        if not assigned_parts:
            continue
        output.extend(
            _proportional_parts(
                translated_parts=assigned_parts,
                source_text=clause.text,
                start=clause.start,
                end=clause.end,
            )
        )

    if output:
        output[0]["start"] = segment_start
        output[-1]["end"] = segment_end
    return _normalize_aligned_part_times(output, translated_parts, source_text, segment_start, segment_end)


def _translate_batch(
    client: Any,
    info: dict[str, Any],
    summary: dict[str, Any],
    context: dict[str, Any],
    batch: list[dict[str, Any]],
    config: TranslationConfig,
) -> list[dict[str, Any]]:
    title = str(summary.get("title") or _title_from_info(info))
    author = str(summary.get("author") or _author_from_info(info))
    video_summary = str(context.get("content_summary") or summary.get("summary") or "").strip()
    payload = {
        "title": title,
        "author": author,
        "summary": video_summary,
        "glossary": _translation_context_terms(context, "glossary"),
        "corrections": _translation_context_terms(context, "corrections"),
        "target_language": config.target_language,
        "segments": [
            {
                "segment_id": item["segment_id"],
                "text": item["text"],
            }
            for item in batch
        ],
    }
    messages = [
        {
            "role": "system",
            "content": _prompt_with_optional_sections(
                (
                    "You translate spoken transcript segments for dubbing. "
                    "Return only a JSON object with one key named segments. "
                    "segments must be an array of objects, and each object must contain "
                    "segment_id and translation. "
                    "translation must be complete, natural, concise, punctuated, and suitable for speech synthesis. "
                    "Translate one source segment into exactly one target-language string. "
                    "Use the supplied glossary consistently. Apply supplied ASR corrections silently before translation. "
                    "Preserve names, brands, jargon, obvious acronyms, code, commands, paths, URLs, versions, and file names when appropriate. "
                    "Do not return empty strings, punctuation-only strings, markdown, labels, or explanations."
                ),
                ("Global translation instructions", config.extra_prompt),
                ("Segment translation instructions", config.segment_extra_prompt),
                ("Correction and glossary rules", config.correction_prompt),
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, indent=2),
        },
    ]
    return _chat_json(
        client,
        config,
        messages,
        schema_name="segment_translation_batch",
        schema=_segment_translation_response_schema(),
        normalize=lambda raw: _normalize_segment_translation_response(raw, batch),
    )


def _chat_json(
    client: Any,
    config: TranslationConfig,
    messages: list[dict[str, str]],
    schema_name: str,
    schema: dict[str, Any],
    normalize: Callable[[Any], Any],
) -> Any:
    last_error: Exception | None = None
    for response_format in _response_format_candidates(schema_name, schema, config.force_json_output):
        for attempt in range(1, config.max_retries + 1):
            request_messages = _messages_for_attempt(messages, attempt, schema_name)
            request_kwargs = _chat_request_kwargs(
                config=config,
                messages=request_messages,
                response_format=response_format,
            )
            try:
                response = client.chat.completions.create(**request_kwargs)
                raw = _parse_json_response(response)
                return normalize(raw)
            except Exception as exc:
                last_error = exc
                if response_format is not None and _response_format_unsupported(exc):
                    break
                if attempt >= config.max_retries:
                    continue
                _sleep_before_retry(config, attempt)

    if last_error is None:
        raise RuntimeError(f"Translation request failed before sending: {schema_name}")
    raise RuntimeError(f"Translation request failed for {schema_name}: {last_error}") from last_error


def _create_openai_client(config: TranslationConfig) -> Any:
    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "The openai package is required for translation. Add it to the runtime dependencies."
        ) from exc

    kwargs: dict[str, Any] = {"api_key": config.api_key}
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return OpenAI(**kwargs)


def _chat_request_kwargs(
    config: TranslationConfig,
    messages: list[dict[str, str]],
    response_format: dict[str, Any] | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "timeout": config.timeout_seconds,
        "temperature": config.temperature,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format
    return kwargs


def _response_format_candidates(
    schema_name: str,
    schema: dict[str, Any],
    force_json_output: bool,
) -> list[dict[str, Any] | None]:
    if not force_json_output:
        return [None]
    return [
        {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        },
        {"type": "json_object"},
        None,
    ]


def _messages_for_attempt(
    messages: list[dict[str, str]],
    attempt: int,
    schema_name: str,
) -> list[dict[str, str]]:
    if attempt <= 1:
        return messages
    return messages + [
        {
            "role": "user",
            "content": (
                f"The previous response did not satisfy the required JSON for {schema_name}. "
                "Return only valid JSON. Do not include markdown, prose, or code fences."
            ),
        }
    ]


def _sleep_before_retry(config: TranslationConfig, attempt: int) -> None:
    delay = config.retry_backoff_seconds * (config.retry_backoff_multiplier ** max(attempt - 1, 0))
    delay = min(delay, config.retry_max_backoff_seconds)
    if delay > 0:
        time.sleep(delay)


def _response_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        raise ValueError("Model response did not contain choices")

    message = choices[0].message
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    raise ValueError("Unsupported response content type")


def _extract_embedded_json(text: str) -> Any:
    decoder = json.JSONDecoder()
    starts = [index for index, char in enumerate(text) if char in "[{"]
    for start in starts:
        try:
            value, _ = decoder.raw_decode(text[start:])
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError(f"Response does not contain valid JSON: {text}")


def _parse_json_response(response: Any) -> Any:
    content = _response_text(response)
    if not content:
        raise TranslationResponseError("Model response content is empty")
    return _extract_embedded_json(content)


def _response_format_unsupported(exc: Exception) -> bool:
    message = str(exc).lower()
    if "response_format" not in message and "json_schema" not in message and "json_object" not in message:
        return False
    unsupported_markers = (
        "unsupported",
        "not support",
        "unknown parameter",
        "invalid parameter",
        "extra inputs are not permitted",
        "not permitted",
        "not allowed",
        "does not support",
        "invalid value",
    )
    return any(marker in message for marker in unsupported_markers)


def _summary_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["title", "summary", "tags"],
    }


def _translation_context_response_schema() -> dict[str, Any]:
    term_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source": {"type": "string"},
            "target": {"type": "string"},
        },
        "required": ["source", "target"],
    }
    correction_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "wrong": {"type": "string"},
            "correct": {"type": "string"},
        },
        "required": ["wrong", "correct"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "content_summary": {"type": "string"},
            "glossary": {"type": "array", "items": term_schema},
            "corrections": {"type": "array", "items": correction_schema},
        },
        "required": ["content_summary", "glossary", "corrections"],
    }


def _segment_translation_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "segment_id": {"type": "integer"},
                        "translation": {"type": "string"},
                    },
                    "required": ["segment_id", "translation"],
                },
            }
        },
        "required": ["segments"],
    }


def _normalize_translation_context_response(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise TranslationResponseError("Translation context did not return a JSON object")
    return {
        "content_summary": _clean_text(result.get("content_summary") or result.get("summary")),
        "glossary": _normalize_term_list(result.get("glossary") or result.get("hotwords"), "source", "target"),
        "corrections": _normalize_term_list(result.get("corrections"), "wrong", "correct"),
    }


def _normalize_summary_response(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise TranslationResponseError("Summary translation did not return a JSON object")
    summary_text = _clean_text(result.get("summary"))
    if not summary_text:
        raise TranslationResponseError("Summary translation returned an empty summary")
    title = _clean_text(result.get("title"))
    if not title:
        raise TranslationResponseError("Summary translation returned an empty title")
    tags = _string_list(result.get("tags"), limit=20)
    return {
        "title": title,
        "summary": summary_text,
        "tags": tags,
    }


def _normalize_segment_translation_response(
    result: Any,
    batch: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        items = result.get("segments") or result.get("translations")
    else:
        items = result
    if not isinstance(items, list):
        raise TranslationResponseError("Segment translation did not return a JSON array")

    expected_ids = [int(item["segment_id"]) for item in batch]
    translated_by_id: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if "segment_id" not in item:
            continue
        try:
            segment_id = int(item["segment_id"])
        except Exception as exc:
            raise TranslationResponseError(f"Invalid segment id in translation response: {item}") from exc
        translation = _clean_text(item.get("translation"))
        _validate_translation_text(translation, segment_id)
        translated_by_id[segment_id] = translation

    missing = [segment_id for segment_id in expected_ids if segment_id not in translated_by_id]
    unexpected = [segment_id for segment_id in translated_by_id if segment_id not in expected_ids]
    if missing or unexpected:
        raise TranslationResponseError(
            f"Incomplete batch translation. expected={expected_ids} "
            f"missing={missing} unexpected={sorted(unexpected)}"
        )

    return [
        {
            "segment_id": segment_id,
            "translation": translated_by_id[segment_id],
        }
        for segment_id in expected_ids
    ]


def _load_existing_segment_translations(
    path: Path,
    context_hash: str,
    prompt_hash: str,
    config: TranslationConfig,
) -> dict[int, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if isinstance(data, list):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("schema_version") != SEGMENTS_SCHEMA_VERSION:
        return {}
    if data.get("prompt_version") != TRANSLATION_PROMPT_VERSION:
        return {}
    if data.get("target_language") != config.target_language:
        return {}
    if data.get("model") != config.model:
        return {}
    if data.get("context_hash") != context_hash:
        return {}
    if data.get("prompt_hash") != prompt_hash:
        return {}
    items = data.get("segments")
    if not isinstance(items, list):
        return {}
    existing: dict[int, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            segment_id = int(item["segment_id"])
        except Exception:
            continue
        existing[segment_id] = item
    return existing


def _segment_cache_payload(
    complete: dict[int, dict[str, Any]],
    context_hash: str,
    prompt_hash: str,
    config: TranslationConfig,
) -> dict[str, Any]:
    return {
        "schema_version": SEGMENTS_SCHEMA_VERSION,
        "prompt_version": TRANSLATION_PROMPT_VERSION,
        "target_language": config.target_language,
        "model": config.model,
        "context_hash": context_hash,
        "prompt_hash": prompt_hash,
        "segments": [complete[index] for index in sorted(complete)],
    }


def _matches_segment(existing: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if existing is None:
        return False
    return (
        int(existing.get("segment_id", -1)) == current["segment_id"]
        and _clean_text(existing.get("text")) == current["text"]
        and abs(float(existing.get("start", -1.0)) - current["start"]) < 1e-6
        and abs(float(existing.get("end", -1.0)) - current["end"]) < 1e-6
        and str(existing.get("speaker", "")) == current["speaker"]
    )


def _collect_words_for_segment(
    detailed_segments: list[dict[str, Any]],
    segment_start: float,
    segment_end: float,
    speaker: str,
) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for segment in detailed_segments:
        if not isinstance(segment, dict):
            continue
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", 0.0))
        if end < segment_start - 1e-3:
            continue
        if start > segment_end + 1e-3:
            break
        segment_speaker = str(segment.get("speaker", speaker))
        if segment_speaker != speaker:
            continue
        for word in segment.get("words", []):
            if not isinstance(word, dict):
                continue
            if "start" not in word or "end" not in word:
                continue
            word_start = float(word["start"])
            word_end = float(word["end"])
            if word_end < segment_start - 1e-3 or word_start > segment_end + 1e-3:
                continue
            words.append(word)
    return words


def _source_clause_from_words(words: list[dict[str, Any]]) -> SourceClause:
    text = " ".join(str(word["word"]) for word in words).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return SourceClause(
        text=text,
        start=float(words[0]["start"]),
        end=float(words[-1]["end"]),
    )


def _fallback_source_clauses(
    source_text: str,
    segment_start: float,
    segment_end: float,
) -> list[SourceClause]:
    parts = _split_text_attaching_punctuation(source_text, r"([,，;；:：.!?。！？])")
    parts = [part.strip() for part in parts if part.strip()]
    if not parts:
        return [SourceClause(text=source_text, start=segment_start, end=segment_end)]

    total_length = sum(max(len(part), 1) for part in parts)
    current = segment_start
    duration = max(segment_end - segment_start, 0.0)
    clauses: list[SourceClause] = []
    for index, part in enumerate(parts):
        fraction = max(len(part), 1) / total_length
        next_time = segment_end if index == len(parts) - 1 else current + duration * fraction
        clauses.append(SourceClause(text=part, start=current, end=next_time))
        current = next_time
    return clauses


def _proportional_parts(
    translated_parts: list[str],
    source_text: str,
    start: float,
    end: float,
) -> list[dict[str, Any]]:
    duration = max(end - start, 0.0)
    total_weight = sum(max(len(part), 1) for part in translated_parts)
    current = start
    output: list[dict[str, Any]] = []
    for index, part in enumerate(translated_parts):
        fraction = max(len(part), 1) / total_weight
        next_time = end if index == len(translated_parts) - 1 else current + duration * fraction
        output.append(
            {
                "translation": part,
                "source_text": source_text,
                "start": current,
                "end": next_time,
            }
        )
        current = next_time
    return output


def _normalize_aligned_part_times(
    parts: list[dict[str, Any]],
    translated_parts: list[str],
    source_text: str,
    segment_start: float,
    segment_end: float,
) -> list[dict[str, Any]]:
    if not parts:
        return parts
    if _has_invalid_part_timing(parts, segment_start, segment_end):
        return _proportional_parts(
            translated_parts=translated_parts,
            source_text=source_text,
            start=segment_start,
            end=segment_end,
        )
    parts[0]["start"] = segment_start
    parts[-1]["end"] = segment_end
    return parts


def _has_invalid_part_timing(
    parts: list[dict[str, Any]],
    segment_start: float,
    segment_end: float,
) -> bool:
    previous_end = segment_start
    for index, part in enumerate(parts):
        start = float(part.get("start", segment_start))
        end = float(part.get("end", segment_end))
        if start < segment_start - 1e-3 or end > segment_end + 1e-3:
            return True
        if start < previous_end - 1e-3:
            return True
        if end <= start:
            return True
        if len(parts) > 1 and end - start < MIN_TRANSLATION_PART_DURATION:
            return True
        previous_end = end
        if index == len(parts) - 1 and abs(end - segment_end) > 1e-3:
            return True
    return False


def _split_text_attaching_punctuation(text: str, pattern: str) -> list[str]:
    pieces = re.split(pattern, text)
    output: list[str] = []
    buffer = ""
    for piece in pieces:
        if piece is None or piece == "":
            continue
        if len(piece) == 1 and piece in _ALL_SPLIT_PUNCTUATION:
            buffer += piece
            continue
        if buffer:
            output.append(buffer)
        buffer = piece
    if buffer:
        output.append(buffer)
    return _merge_punctuation_only_parts(output)


def _merge_punctuation_only_parts(parts: list[str]) -> list[str]:
    merged: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if _is_punctuation_only(part):
            if merged:
                merged[-1] += part
            continue
        merged.append(part)
    return merged


def _is_punctuation_only(text: str) -> bool:
    return bool(text) and all(char in _ALL_SPLIT_PUNCTUATION for char in text)


def _merge_short_translation_parts(parts: list[str], max_chars: int) -> list[str]:
    merged: list[str] = []
    pending = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if pending:
            part = f"{pending}{part}"
            pending = ""
        if _should_merge_with_next(part) and len(part) < max_chars:
            pending = part
            continue
        merged.append(part)
    if pending:
        if merged and len(merged[-1]) + len(pending) <= max_chars:
            merged[-1] += pending
        else:
            merged.append(pending)
    return merged


def _should_merge_with_next(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if len(compact) <= 2:
        return True
    return len(compact) <= 4 and compact[-1:] in {"，", ",", "、", "：", ":", "；", ";"}


def _chunk_text(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        split_at = max_chars
        if " " in remaining[: max_chars + 1]:
            split_at = remaining.rfind(" ", 0, max_chars + 1)
            split_at = split_at if split_at > 0 else max_chars
        elif len(remaining) > split_at and remaining[split_at] in _ALL_SPLIT_PUNCTUATION:
            split_at += 1
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return _rebalance_short_tail_chunks(_merge_punctuation_only_parts([chunk for chunk in chunks if chunk]), max_chars)


def _rebalance_short_tail_chunks(chunks: list[str], max_chars: int) -> list[str]:
    if len(chunks) < 2:
        return chunks
    short_tail_limit = max(4, max_chars // 3)
    if len(chunks[-1]) > short_tail_limit:
        return chunks

    combined = f"{chunks[-2]}{chunks[-1]}".strip()
    if len(combined) <= max_chars:
        return chunks[:-2] + [combined]

    chunk_count = (len(combined) + max_chars - 1) // max_chars
    base_size, larger_chunks = divmod(len(combined), chunk_count)
    balanced: list[str] = []
    cursor = 0
    for index in range(chunk_count):
        size = base_size + (1 if index >= chunk_count - larger_chunks else 0)
        chunk = combined[cursor : cursor + size].strip()
        if chunk:
            balanced.append(chunk)
        cursor += size
    return chunks[:-2] + balanced


def _transcript_excerpt(transcript: list[dict[str, Any]], window: int = 3) -> list[str]:
    texts = [str(item.get("text", "")).strip() for item in transcript if str(item.get("text", "")).strip()]
    if len(texts) <= window * 2:
        return texts
    return texts[:window] + texts[-window:]


def _full_transcript_text(transcript: list[dict[str, Any]]) -> str:
    return "\n".join(
        str(item.get("text", "")).strip()
        for item in transcript
        if str(item.get("text", "")).strip()
    )


def _summary_source_hash(
    info: dict[str, Any],
    transcript: list[dict[str, Any]],
    config: TranslationConfig,
) -> str:
    return _stable_hash(
        {
            "title": _title_from_info(info),
            "author": _author_from_info(info),
            "description": str(info.get("description") or "").strip(),
            "tags": _string_list(info.get("tags"), limit=20),
            "categories": _string_list(info.get("categories"), limit=10),
            "target_language": config.target_language,
            "transcript_excerpt": _transcript_excerpt(transcript),
        }
    )


def _summary_prompt_hash(config: TranslationConfig) -> str:
    return _stable_hash(
        {
            "prompt_version": SUMMARY_PROMPT_VERSION,
            "extra_prompt": _clean_text(config.extra_prompt),
            "summary_extra_prompt": _clean_text(config.summary_extra_prompt),
            "correction_prompt": _clean_text(config.correction_prompt),
        }
    )


def _translation_context_source_hash(
    info: dict[str, Any],
    summary: dict[str, Any],
    transcript: list[dict[str, Any]],
    config: TranslationConfig,
) -> str:
    return _stable_hash(
        {
            "title": _title_from_info(info),
            "author": _author_from_info(info),
            "description": str(info.get("description") or "").strip(),
            "translated_title": _clean_text(summary.get("title")),
            "translated_summary": _clean_text(summary.get("summary")),
            "translated_tags": _string_list(summary.get("tags"), limit=20),
            "tags": _string_list(info.get("tags"), limit=20),
            "categories": _string_list(info.get("categories"), limit=10),
            "target_language": config.target_language,
            "transcript": _full_transcript_text(transcript),
        }
    )


def _translation_context_prompt_hash(config: TranslationConfig) -> str:
    return _stable_hash(
        {
            "prompt_version": CONTEXT_PROMPT_VERSION,
            "extra_prompt": _clean_text(config.extra_prompt),
            "context_extra_prompt": _clean_text(config.context_extra_prompt),
            "correction_prompt": _clean_text(config.correction_prompt),
        }
    )


def _segment_translation_prompt_hash(config: TranslationConfig) -> str:
    return _stable_hash(
        {
            "prompt_version": TRANSLATION_PROMPT_VERSION,
            "extra_prompt": _clean_text(config.extra_prompt),
            "segment_extra_prompt": _clean_text(config.segment_extra_prompt),
            "correction_prompt": _clean_text(config.correction_prompt),
        }
    )


def _valid_translation_context(
    context: dict[str, Any],
    source_hash: str,
    prompt_hash: str,
    config: TranslationConfig,
) -> bool:
    return (
        context.get("schema_version") == CONTEXT_SCHEMA_VERSION
        and context.get("prompt_version") == CONTEXT_PROMPT_VERSION
        and context.get("target_language") == config.target_language
        and context.get("source_hash") == source_hash
        and context.get("prompt_hash") == prompt_hash
        and context.get("status") == "success"
        and isinstance(context.get("glossary"), list)
        and isinstance(context.get("corrections"), list)
    )


def _translation_context_for_prompt(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "content_summary": _clean_text(context.get("content_summary")),
        "glossary": _translation_context_terms(context, "glossary"),
        "corrections": _translation_context_terms(context, "corrections"),
    }


def _translation_context_terms(context: dict[str, Any], key: str) -> list[dict[str, str]]:
    value = context.get(key)
    return value if isinstance(value, list) else []


def _normalize_term_list(
    value: Any,
    source_key: str,
    target_key: str,
    limit: int = 100,
) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    aliases = {
        "source": ("source", "src"),
        "target": ("target", "dst"),
        "wrong": ("wrong",),
        "correct": ("correct",),
    }
    for item in value:
        if not isinstance(item, dict):
            continue
        source = _first_clean_value(item, aliases[source_key])
        target = _first_clean_value(item, aliases[target_key])
        if not source or not target or source == target and source_key == "wrong":
            continue
        pair = (source, target)
        if pair in seen:
            continue
        seen.add(pair)
        normalized.append({source_key: source, target_key: target})
        if len(normalized) >= limit:
            break
    return normalized


def _first_clean_value(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _clean_text(item.get(key))
        if value:
            return value
    return ""


def _validate_translation_text(text: str, segment_id: int) -> None:
    if not text:
        raise TranslationResponseError(f"Empty translation for segment id: {segment_id}")
    if _is_punctuation_only(text):
        raise TranslationResponseError(f"Punctuation-only translation for segment id: {segment_id}")
    if text.startswith(("{", "[")) and text.endswith(("}", "]")):
        raise TranslationResponseError(f"Nested JSON translation for segment id: {segment_id}")


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_summary(
    summary: dict[str, Any],
    source_hash: str,
    prompt_hash: str,
    config: TranslationConfig,
) -> bool:
    if not all(field in summary for field in TASK_SUMMARY_FIELDS):
        return False
    if not _clean_text(summary.get("title")):
        return False
    if not _clean_text(summary.get("author")):
        return False
    if not _clean_text(summary.get("summary")):
        return False
    return (
        isinstance(summary.get("tags"), list)
        and summary.get("schema_version") == SUMMARY_SCHEMA_VERSION
        and summary.get("prompt_version") == SUMMARY_PROMPT_VERSION
        and summary.get("target_language") == config.target_language
        and summary.get("source_hash") == source_hash
        and summary.get("prompt_hash") == prompt_hash
    )


def _title_from_info(info: dict[str, Any]) -> str:
    for key in ("title", "fulltitle", "display_id", "id"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "untitled"


def _author_from_info(info: dict[str, Any]) -> str:
    for key in ("uploader", "channel", "author"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Unknown"


def _string_list(value: Any, limit: int | None = None) -> list[str]:
    if isinstance(value, str):
        output = [item.strip() for item in re.split(r"[,，\n]", value) if item.strip()]
        return output[:limit] if limit is not None else output
    if not isinstance(value, list):
        return []
    output = [str(item).strip() for item in value if str(item).strip()]
    if limit is not None:
        return output[:limit]
    return output


def _join_clause_text(clauses: list[SourceClause]) -> str:
    text = " ".join(clause.text for clause in clauses).strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", text)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.startswith("```") and text.endswith("```"):
        text = text.strip("`").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"', "“", "”"}:
        text = text[1:-1].strip()
    if text.startswith("翻译：") or text.startswith("翻译:"):
        text = text.split(":", 1)[-1].split("：", 1)[-1].strip()
    return re.sub(r"\s+", " ", text)


def _prompt_with_optional_sections(base: str, *sections: tuple[str, str | None]) -> str:
    content = base.strip()
    for title, prompt in sections:
        prompt = _clean_multiline_prompt(prompt)
        if not prompt:
            continue
        content = f"{content}\n\n{title}:\n{prompt}"
    return content


def _clean_multiline_prompt(value: str | None) -> str:
    if value is None:
        return ""
    lines = [line.rstrip() for line in str(value).strip().splitlines()]
    return "\n".join(lines).strip()


def _chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _read_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON list in {path}")
    return data


def _write_json(path: Path, data: Any) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return data
