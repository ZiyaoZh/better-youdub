from types import SimpleNamespace

from youdub.translation import (
    CONTEXT_OUTPUT,
    SEGMENTS_OUTPUT,
    TranslationConfig,
    _chat_json,
    align_translation_parts,
    ensure_segment_translations,
    ensure_translation_context,
    _normalize_translation_context_response,
    SourceClause,
    _segment_translation_response_schema,
    _summary_response_schema,
    _normalize_summary_response,
    _translate_batch,
    build_translation_entries,
    split_translation_text,
)


def _response(content: str):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
            )
        ]
    )


class _FakeCompletions:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.scripted:
            raise AssertionError("No scripted response left")
        result = self.scripted.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeClient:
    def __init__(self, scripted):
        self.chat = SimpleNamespace(completions=_FakeCompletions(scripted))


def test_split_translation_text_prefers_punctuation() -> None:
    assert split_translation_text("你好，世界。今天天气不错。") == [
        "你好，世界。",
        "今天天气不错。",
    ]


def test_split_translation_text_does_not_emit_punctuation_only_parts() -> None:
    assert split_translation_text(
        "《气球塔防6》，一款通过放置猴子来击破气球、保卫世界免受气球入侵的游戏。"
    ) == [
        "《气球塔防6》，",
        "一款通过放置猴子来击破气球、",
        "保卫世界免受气球入侵的游戏。",
    ]
    assert split_translation_text(
        "除了因为地图看起来和感受上不同于之前而击败它之外，还有其他目的吗？"
    ) == [
        "除了因为地图看起来和感受上不同于之前而击败它之外，",
        "还有其他目的吗？",
    ]


def test_split_translation_text_merges_short_leading_fragments() -> None:
    assert split_translation_text("很快，你会解锁升级：更锋利的飞镖、更快的攻击。", max_chars=12) == [
        "很快，你会解锁升级：",
        "更锋利的飞镖、",
        "更快的攻击。",
    ]


def test_build_translation_entries_aligns_with_source_clause_timings() -> None:
    translated_segments = [
        {
            "segment_id": 0,
            "start": 0.0,
            "end": 10.0,
            "speaker": "SPEAKER_00",
            "text": "Hello, world. Nice day.",
            "translation": "你好，世界。今天天气不错。",
        }
    ]
    diarized = {
        "segments": [
            {
                "start": 0.0,
                "end": 5.0,
                "speaker": "SPEAKER_00",
                "words": [
                    {"word": "Hello,", "start": 0.0, "end": 2.0},
                    {"word": "world.", "start": 2.0, "end": 5.0},
                ],
            },
            {
                "start": 5.0,
                "end": 10.0,
                "speaker": "SPEAKER_00",
                "words": [
                    {"word": "Nice", "start": 5.0, "end": 7.0},
                    {"word": "day.", "start": 7.0, "end": 10.0},
                ],
            },
        ]
    }

    output = build_translation_entries(translated_segments, diarized)

    assert output == [
        {
            "segment_id": 0,
            "part_id": 0,
            "start": 0.0,
            "end": 5.0,
            "speaker": "SPEAKER_00",
            "text": "Hello, world.",
            "source_text": "Hello, world.",
            "translation": "你好，世界。",
        },
        {
            "segment_id": 0,
            "part_id": 1,
            "start": 5.0,
            "end": 10.0,
            "speaker": "SPEAKER_00",
            "text": "Nice day.",
            "source_text": "Nice day.",
            "translation": "今天天气不错。",
        },
    ]


def test_align_translation_parts_falls_back_for_invalid_short_timings() -> None:
    output = align_translation_parts(
        translated_parts=["第一段，", "第二段。"],
        source_clauses=[
            SourceClause("First,", 0.0, 0.02),
            SourceClause("Second.", 0.02, 2.0),
        ],
        segment_start=0.0,
        segment_end=2.0,
        source_text="First, Second.",
    )

    assert output == [
        {"translation": "第一段，", "source_text": "First, Second.", "start": 0.0, "end": 1.0},
        {"translation": "第二段。", "source_text": "First, Second.", "start": 1.0, "end": 2.0},
    ]


def test_chat_json_prefers_json_schema_response_format(monkeypatch) -> None:
    monkeypatch.setattr("youdub.translation.time.sleep", lambda *_args: None)
    client = _FakeClient([_response('{"title":"标题","summary":"摘要","tags":["标签"]}')])
    config = TranslationConfig(
        api_key="sk-test",
        model="gpt-test",
        max_retries=1,
    )

    result = _chat_json(
        client,
        config,
        [{"role": "user", "content": "demo"}],
        schema_name="summary_translation",
        schema=_summary_response_schema(),
        normalize=_normalize_summary_response,
    )

    assert result == {"title": "标题", "summary": "摘要", "tags": ["标签"]}
    assert client.chat.completions.calls[0]["response_format"]["type"] == "json_schema"


def test_chat_json_falls_back_when_json_schema_unsupported(monkeypatch) -> None:
    monkeypatch.setattr("youdub.translation.time.sleep", lambda *_args: None)
    client = _FakeClient(
        [
            RuntimeError("response_format json_schema is unsupported by this provider"),
            _response('{"title":"标题","summary":"摘要","tags":["标签"]}'),
        ]
    )
    config = TranslationConfig(
        api_key="sk-test",
        model="gpt-test",
        max_retries=1,
    )

    result = _chat_json(
        client,
        config,
        [{"role": "user", "content": "demo"}],
        schema_name="summary_translation",
        schema=_summary_response_schema(),
        normalize=_normalize_summary_response,
    )

    assert result["summary"] == "摘要"
    assert client.chat.completions.calls[0]["response_format"]["type"] == "json_schema"
    assert client.chat.completions.calls[1]["response_format"]["type"] == "json_object"


def test_translate_batch_retries_after_incomplete_json(monkeypatch) -> None:
    monkeypatch.setattr("youdub.translation.time.sleep", lambda *_args: None)
    client = _FakeClient(
        [
            _response('{"segments":[{"segment_id":0,"translation":"第一句"}]}'),
            _response(
                '{"segments":['
                '{"segment_id":0,"translation":"第一句"},'
                '{"segment_id":1,"translation":"第二句"}'
                ']}'
            ),
        ]
    )
    config = TranslationConfig(
        api_key="sk-test",
        model="gpt-test",
        max_retries=2,
        retry_backoff_seconds=0,
    )
    batch = [
        {"segment_id": 0, "text": "First"},
        {"segment_id": 1, "text": "Second"},
    ]

    translated = _translate_batch(
        client=client,
        info={"title": "Demo", "uploader": "Author"},
        summary={"title": "标题", "author": "作者", "summary": "摘要"},
        context={"content_summary": "上下文", "glossary": [], "corrections": []},
        batch=batch,
        config=config,
    )

    assert translated == [
        {"segment_id": 0, "translation": "第一句"},
        {"segment_id": 1, "translation": "第二句"},
    ]
    assert len(client.chat.completions.calls) == 2
    assert client.chat.completions.calls[0]["response_format"]["type"] == "json_schema"


def test_translate_batch_includes_context_terms(monkeypatch) -> None:
    monkeypatch.setattr("youdub.translation.time.sleep", lambda *_args: None)
    client = _FakeClient([_response('{"segments":[{"segment_id":0,"translation":"飞镖猴。"}]}')])
    config = TranslationConfig(api_key="sk-test", model="gpt-test", max_retries=1)

    translated = _translate_batch(
        client=client,
        info={"title": "Demo", "uploader": "Author"},
        summary={"title": "标题", "author": "作者", "summary": "摘要"},
        context={
            "content_summary": "游戏说明。",
            "glossary": [{"source": "Dart Monkey", "target": "飞镖猴"}],
            "corrections": [{"wrong": "tax shooter", "correct": "Tack Shooter"}],
        },
        batch=[{"segment_id": 0, "text": "Dart Monkey."}],
        config=config,
    )

    payload = client.chat.completions.calls[0]["messages"][1]["content"]
    assert translated == [{"segment_id": 0, "translation": "飞镖猴。"}]
    assert "Dart Monkey" in payload
    assert "Tack Shooter" in payload


def test_translate_batch_rejects_punctuation_only_translation(monkeypatch) -> None:
    monkeypatch.setattr("youdub.translation.time.sleep", lambda *_args: None)
    client = _FakeClient([_response('{"segments":[{"segment_id":0,"translation":"、"}]}')])
    config = TranslationConfig(api_key="sk-test", model="gpt-test", max_retries=1, force_json_output=False)

    try:
        _translate_batch(
            client=client,
            info={"title": "Demo", "uploader": "Author"},
            summary={"title": "标题", "author": "作者", "summary": "摘要"},
            context={"content_summary": "", "glossary": [], "corrections": []},
            batch=[{"segment_id": 0, "text": "Comma."}],
            config=config,
        )
    except RuntimeError as exc:
        assert "Punctuation-only translation" in str(exc)
    else:
        raise AssertionError("punctuation-only translation should fail")


def test_normalize_translation_context_accepts_hotword_aliases() -> None:
    result = _normalize_translation_context_response(
        {
            "summary": "摘要",
            "hotwords": [{"src": "Dart Monkey", "dst": "飞镖猴"}],
            "corrections": [{"wrong": "tax shooter", "correct": "Tack Shooter"}],
        }
    )

    assert result == {
        "content_summary": "摘要",
        "glossary": [{"source": "Dart Monkey", "target": "飞镖猴"}],
        "corrections": [{"wrong": "tax shooter", "correct": "Tack Shooter"}],
    }


def test_ensure_translation_context_writes_cache(tmp_path) -> None:
    client = _FakeClient(
        [
            _response(
                '{"content_summary":"摘要","glossary":[{"source":"Dart Monkey","target":"飞镖猴"}],'
                '"corrections":[{"wrong":"tax shooter","correct":"Tack Shooter"}]}'
            )
        ]
    )
    config = TranslationConfig(api_key="sk-test", model="gpt-test", max_retries=1)
    transcript = [{"start": 0.0, "end": 1.0, "text": "Dart Monkey."}]

    context = ensure_translation_context(
        tmp_path,
        {"title": "Demo", "uploader": "Author"},
        {"summary": "旧摘要"},
        transcript,
        client,
        config,
    )

    assert context["status"] == "success"
    assert context["content_summary"] == "摘要"
    assert context["glossary"] == [{"source": "Dart Monkey", "target": "飞镖猴"}]
    assert (tmp_path / CONTEXT_OUTPUT).exists()


def test_segment_translation_cache_uses_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("youdub.translation.time.sleep", lambda *_args: None)
    client = _FakeClient([_response('{"segments":[{"segment_id":0,"translation":"飞镖猴。"}]}')])
    config = TranslationConfig(api_key="sk-test", model="gpt-test", max_retries=1)
    info = {"title": "Demo", "uploader": "Author"}
    summary = {"title": "标题", "author": "作者", "summary": "摘要"}
    context = {"content_summary": "上下文", "glossary": [], "corrections": []}
    transcript = [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "Dart Monkey."}]

    first = ensure_segment_translations(tmp_path, info, summary, context, transcript, client, config)
    second = ensure_segment_translations(tmp_path, info, summary, context, transcript, client, config)
    cache = __import__("json").loads((tmp_path / SEGMENTS_OUTPUT).read_text(encoding="utf-8"))

    assert first == second
    assert cache["schema_version"] == 2
    assert cache["prompt_version"] == "translation-v2"
    assert cache["segments"][0]["translation"] == "飞镖猴。"
    assert len(client.chat.completions.calls) == 1
