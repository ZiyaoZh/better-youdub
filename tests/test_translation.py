from types import SimpleNamespace

from youdub.translation import (
    TranslationConfig,
    _chat_json,
    _normalize_segment_translation_response,
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
        batch=batch,
        config=config,
    )

    assert translated == [
        {"segment_id": 0, "translation": "第一句"},
        {"segment_id": 1, "translation": "第二句"},
    ]
    assert len(client.chat.completions.calls) == 2
    assert client.chat.completions.calls[0]["response_format"]["type"] == "json_schema"
