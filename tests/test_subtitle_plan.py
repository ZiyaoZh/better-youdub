import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from youdub.subtitle_plan import (
    PLAN_OUTPUT,
    PLAN_PROMPT_VERSION,
    SubtitlePlanError,
    SubtitleTimingError,
    ensure_subtitle_plan,
    model_group_schema,
    normalize_subtitle_groups,
    tokenize_source,
)


class Layout:
    def __init__(self, max_tokens=5, max_target=24, min_duration=0):
        self.max_tokens = max_tokens
        self.max_target = max_target
        self.min_duration = min_duration

    def prompt_budget(self):
        return {"max_tokens": self.max_tokens, "max_target": self.max_target, "min_duration": self.min_duration}

    def feedback_for_groups(self, source_text, groups, start, end):
        errors = []
        for group in groups:
            if group["source_end"] - group["source_start"] > self.max_tokens:
                errors.append("Source line exceeds pixel width")
            if len(group["translation"]) > self.max_target:
                errors.append("Translation exceeds pixel width")
        if end - start < self.min_duration:
            errors.append("Reading duration is too short")
        return errors


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def _entry(source="One two three four.", target="一二三四。", **kwargs):
    return {"segment_id": 0, "part_id": 0, "start": 0, "end": 4, "speaker": "SPEAKER_00", "source_text": source, "translation": target, **kwargs}


def _groups(*pieces):
    return [{"source_start": start, "source_end": end, "translation": target} for start, end, target in pieces]


def _words(task_dir, source="One two three four.", *, times=None, speaker="SPEAKER_00"):
    words = []
    for index, word in enumerate(source.split()):
        start, end = times[index] if times else (index, index + 0.6)
        words.append({"word": word, "start": start, "end": end})
    data = {"language": "en", "segments": [{"speaker": speaker, "words": words}]}
    _write(task_dir / "transcript.diarized.json", data)
    return data


def _fake_model(monkeypatch, scripted):
    from youdub import translation
    calls = []
    responses = iter(scripted)
    monkeypatch.setattr(translation, "_create_openai_client", lambda config: object())

    def chat(client, config, messages, *, normalize, **kwargs):
        calls.append((messages, kwargs))
        return normalize(next(responses))

    monkeypatch.setattr(translation, "_chat_json", chat)
    return SimpleNamespace(model="fake", target_language="简体中文"), calls


def test_tokens_use_whitespace_indices_and_exact_character_spans():
    source = "  Doc's\tnew micro-chip,\nworks.  "
    tokens = tokenize_source(source)
    assert [token["text"] for token in tokens] == ["Doc's", "new", "micro-chip,", "works."]
    assert [source[token["start_char"]:token["end_char"]] for token in tokens] == [token["text"] for token in tokens]
    assert model_group_schema()["type"] == "array"


@pytest.mark.parametrize("groups", [
    _groups((0, 2, "一二"), (3, 4, "三四。")),
    _groups((0, 3, "一二"), (2, 4, "三四。")),
    _groups((0, 3, "一二三四。")),
    _groups((0, 2, "一二"), (2, 4, "遗漏。")),
    _groups((0, 2, "一二三四。"), (2, 4, " ")),
    [{"source_start": False, "source_end": 4, "translation": "一二三四。"}],
])
def test_normalizer_rejects_gaps_overlap_missing_target_and_boolean_indices(groups):
    with pytest.raises(SubtitlePlanError):
        normalize_subtitle_groups(groups, "One two three four.", "一二三四。")


def test_normalizer_keeps_complete_translation_and_source_substrings():
    groups = normalize_subtitle_groups(_groups((0, 2, " 一二 "), (2, 4, "三四。")), "One  two\tthree four.", "一 二三四。")
    assert groups[0]["source_text"] == "One  two"
    assert "".join(group["translation"] for group in groups) == "一二三四。"


def test_short_sentence_needs_neither_model_nor_word_timings(tmp_path):
    cue, = ensure_subtitle_plan(tmp_path, [_entry()], layout=Layout())
    assert (cue["start"], cue["end"], cue["timing_source"]) == (0, 4, "original-speech")
    plan = json.loads((tmp_path / PLAN_OUTPUT).read_text())
    assert plan["status"] == "success"
    assert plan["prompt_version"] == PLAN_PROMPT_VERSION


def test_fast_original_speech_keeps_timing_and_cached_reading_warning(tmp_path, monkeypatch):
    from youdub import translation
    from youdub.subtitle_layout import SubtitleLayout

    def unexpected_model(config):
        pytest.fail("Immutable source speech speed must not trigger translation repairs")

    monkeypatch.setattr(translation, "_create_openai_client", unexpected_model)
    entry = _entry("This original sentence is spoken very quickly.", "快说。", end=1)
    layout = SubtitleLayout()
    first = ensure_subtitle_plan(tmp_path, [entry], layout=layout)
    assert [(cue["start"], cue["end"]) for cue in first] == [(0, 1)]
    for _ in range(2):
        plan = json.loads((tmp_path / PLAN_OUTPUT).read_text())
        assert plan["status"] == "success"
        assert any("42" in warning for warning in plan["entries"]["0:0"]["warnings"])
        assert ensure_subtitle_plan(tmp_path, [entry], layout=layout) == first


def test_group_boundaries_follow_source_words_and_pause_midpoint(tmp_path):
    _words(tmp_path, times=[(0.1, 0.5), (0.6, 1.0), (2.0, 2.4), (2.5, 3.1)])
    entry = _entry(subtitle_groups=_groups((0, 2, "一二"), (2, 4, "三四。")))
    cues = ensure_subtitle_plan(tmp_path, [entry], layout=Layout(max_tokens=2))
    assert [(cue["start"], cue["end"]) for cue in cues] == [(0, 1.5), (1.5, 4)]
    assert [cue["source_text"] for cue in cues] == ["One two", "three four."]
    assert all(cue["alignment_confidence"] == 1 for cue in cues)


def test_missing_words_for_multiple_groups_is_not_timed_by_target_length(tmp_path):
    entry = _entry(subtitle_groups=_groups((0, 2, "一二"), (2, 4, "三四。")))
    with pytest.raises(SubtitleTimingError, match="require source word timings"):
        ensure_subtitle_plan(tmp_path, [entry], layout=Layout(max_tokens=2))
    plan = json.loads((tmp_path / PLAN_OUTPUT).read_text())
    assert plan["status"] == "error"
    assert "forced alignment" in plan["entries"]["0:0"]["diagnostics"][-1]["error"]


def test_long_sentence_without_model_fails_with_actionable_diagnostic(tmp_path):
    with pytest.raises(SubtitlePlanError, match="configure the translation model"):
        ensure_subtitle_plan(tmp_path, [_entry()], layout=Layout(max_tokens=2))


def test_model_groups_complete_fixed_translation_then_can_rewrite_for_semantics(tmp_path, monkeypatch):
    _words(tmp_path)
    config, calls = _fake_model(monkeypatch, [
        {"subtitle_translation": "一二三四。", "groups": _groups((0, 4, "一二三四。"))},
        {"subtitle_translation": "一二，三四。", "groups": _groups((0, 2, "一二，"), (2, 4, "三四。"))},
    ])
    _write(tmp_path / "translation.context.json", {"glossary": {"One": "一"}})
    entry = _entry()
    cues = ensure_subtitle_plan(tmp_path, [entry], config, layout=Layout(max_tokens=2))
    assert [cue["translation"] for cue in cues] == ["一二，", "三四。"]
    assert entry["translation"] == "一二三四。"
    payloads = [json.loads(messages[1]["content"]) for messages, _ in calls]
    assert [payload["allow_complete_sentence_rewrite"] for payload in payloads] == [False, True]
    assert payloads[1]["validation_feedback"]
    assert payloads[0]["context"]["glossary_and_context"]["glossary"] == {"One": "一"}
    assert "verb or infinitive" in calls[0][0][0]["content"]
    assert "to activate" in calls[0][0][0]["content"]
    plan = json.loads((tmp_path / PLAN_OUTPUT).read_text())
    assert plan["entries"]["0:0"]["subtitle_translation"] == "一二，三四。"


def test_readability_uses_actual_word_timing_not_token_count_estimates(tmp_path):
    _words(tmp_path, times=[(0.1, 0.3), (0.4, 0.6), (3.5, 3.7), (3.8, 3.9)])
    entry = _entry(subtitle_groups=_groups((0, 2, "一二"), (2, 4, "三四。")))
    # Both real groups have >=1.9 seconds, despite a long central silence.
    cues = ensure_subtitle_plan(tmp_path, [entry], layout=Layout(max_tokens=2, min_duration=1.9))
    assert cues[0]["end"] == 2.05


def test_small_asr_spelling_correction_is_mapped_through_character_anchors(tmp_path):
    _words(tmp_path, "The tax tower wins.")
    entry = _entry("The Tack tower wins.", "图钉塔赢了。", subtitle_groups=_groups((0, 3, "图钉塔"), (3, 4, "赢了。")))
    cues = ensure_subtitle_plan(tmp_path, [entry], layout=Layout(max_tokens=3))
    assert cues[0]["source_text"] == "The Tack tower"
    assert 0.8 <= cues[0]["alignment_confidence"] < 1


def test_word_count_changes_do_not_require_equal_token_counts(tmp_path):
    _words(tmp_path, "The micro chip works.")
    entry = _entry("The microchip works.", "微芯片能用。", subtitle_groups=_groups((0, 2, "微芯片"), (2, 3, "能用。")))
    cues = ensure_subtitle_plan(tmp_path, [entry], layout=Layout(max_tokens=2))
    assert cues[0]["end"] == 2.8
    assert cues[0]["alignment_confidence"] == 1


def test_boundary_inside_original_word_is_repaired_by_regrouping(tmp_path, monkeypatch):
    _words(tmp_path, "The microchip works.", times=[(0, .4), (.5, 1.5), (2, 3)])
    entry = _entry("The micro chip works.", "微芯片能用。", subtitle_groups=_groups((0, 2, "微芯"), (2, 4, "片能用。")))
    config, calls = _fake_model(monkeypatch, [{"subtitle_translation": "微芯片能用。", "groups": _groups((0, 3, "微芯片"), (3, 4, "能用。"))}])
    cues = ensure_subtitle_plan(tmp_path, [entry], config, layout=Layout(max_tokens=3))
    assert len(cues) == 2
    assert "inside one original audio word" in json.loads(calls[0][0][1]["content"])["validation_feedback"][0]


def test_unrelated_corrected_source_is_rejected_instead_of_faking_word_times(tmp_path):
    _words(tmp_path, "An unrelated recording here.")
    entry = _entry(subtitle_groups=_groups((0, 2, "一二"), (2, 4, "三四。")))
    with pytest.raises(SubtitleTimingError, match="confidence"):
        ensure_subtitle_plan(tmp_path, [entry], layout=Layout(max_tokens=2))


def test_speaker_filter_avoids_simultaneous_other_voice_words(tmp_path):
    data = _words(tmp_path)
    data["segments"].append({"speaker": "SPEAKER_01", "words": [{"word": "Different", "start": 1.1, "end": 1.2}]})
    _write(tmp_path / "transcript.diarized.json", data)
    entry = _entry(subtitle_groups=_groups((0, 2, "一二"), (2, 4, "三四。")))
    cues = ensure_subtitle_plan(tmp_path, [entry], layout=Layout(max_tokens=2))
    assert cues[0]["alignment_confidence"] == 1


def test_model_repair_is_bounded_and_persists_failure(tmp_path, monkeypatch):
    response = {"subtitle_translation": "一二三四。", "groups": _groups((0, 4, "一二三四。"))}
    config, calls = _fake_model(monkeypatch, [response, response, response])
    with pytest.raises(SubtitlePlanError, match="after 2 repairs"):
        ensure_subtitle_plan(tmp_path, [_entry()], config, layout=Layout(max_tokens=2))
    assert len(calls) == 3
    assert json.loads((tmp_path / PLAN_OUTPUT).read_text())["status"] == "error"


def test_invalid_model_source_coverage_gets_precise_local_repair_feedback(tmp_path, monkeypatch):
    _words(tmp_path)
    config, calls = _fake_model(monkeypatch, [
        {"subtitle_translation": "一二三四。", "groups": _groups((0, 1, "一二"), (2, 4, "三四。"))},
        {"subtitle_translation": "一二三四。", "groups": _groups((0, 2, "一二"), (2, 4, "三四。"))},
    ])
    cues = ensure_subtitle_plan(tmp_path, [_entry()], config, layout=Layout(max_tokens=2))
    assert len(cues) == 2
    payload = json.loads(calls[1][0][1]["content"])
    assert "consecutive source tokens" in payload["validation_feedback"][0]
    assert payload["sentence_timing"]["duration"] == 4


def test_cache_reuses_model_plan_and_binds_layout_source_and_word_data(tmp_path, monkeypatch):
    _words(tmp_path)
    response = {"subtitle_translation": "一二三四。", "groups": _groups((0, 2, "一二"), (2, 4, "三四。"))}
    config, calls = _fake_model(monkeypatch, [response, response, response])
    entry = _entry()
    first = ensure_subtitle_plan(tmp_path, [entry], config, layout=Layout(max_tokens=2))
    assert len(calls) == 1
    assert ensure_subtitle_plan(tmp_path, [entry], config, layout=Layout(max_tokens=2)) == first
    assert len(calls) == 1
    ensure_subtitle_plan(tmp_path, [entry], config, layout=Layout(max_tokens=2, max_target=22))
    assert len(calls) == 2
    _words(tmp_path, times=[(0, .3), (.5, 1), (2.5, 3), (3.1, 3.9)])
    changed = ensure_subtitle_plan(tmp_path, [entry], config, layout=Layout(max_tokens=2, max_target=22))
    assert len(calls) == 3
    assert changed[0]["end"] != first[0]["end"]


def test_cached_cues_are_recomputed_and_tampered_groups_are_rejected(tmp_path):
    entry = _entry()
    ensure_subtitle_plan(tmp_path, [entry], layout=Layout())
    plan = json.loads((tmp_path / PLAN_OUTPUT).read_text())
    plan["entries"]["0:0"]["cues"][0]["end"] = 900
    _write(tmp_path / PLAN_OUTPUT, plan)
    assert ensure_subtitle_plan(tmp_path, [entry], layout=Layout())[0]["end"] == 4


def test_incremental_failure_preserves_unvisited_successful_entries(tmp_path):
    entries = [_entry(segment_id=index, start=index * 4, end=index * 4 + 4) for index in range(3)]
    ensure_subtitle_plan(tmp_path, entries, layout=Layout())
    entries[1]["source_text"] = "Too many source words for this subtitle."
    with pytest.raises(SubtitlePlanError):
        ensure_subtitle_plan(tmp_path, entries, layout=Layout())
    plan = json.loads((tmp_path / PLAN_OUTPUT).read_text())
    assert plan["entries"]["2:0"]["status"] == "complete"


def test_corrected_source_forced_alignment_runs_once_and_is_cached(tmp_path, monkeypatch):
    from youdub import transcription
    _words(tmp_path, "Completely unrelated sounds here.")
    (tmp_path / "audio_vocals.wav").write_bytes(b"test audio")
    entry = _entry(subtitle_groups=_groups((0, 2, "一二"), (2, 4, "三四。")))
    calls = []

    def align(task_dir, config, *, audio_name, whisper_name, output_name):
        calls.append(json.loads((task_dir / whisper_name).read_text()))
        result = {"language": "en", "segments": [{"words": [
            {"word": word, "start": index, "end": index + .6}
            for index, word in enumerate(entry["source_text"].split())
        ]}]}
        _write(task_dir / output_name, result)
        return task_dir / output_name

    monkeypatch.setattr(transcription, "run_align", align)
    alignment_config = SimpleNamespace(language="en", device="cpu", models_dir=Path("models"))
    first = ensure_subtitle_plan(tmp_path, [entry], layout=Layout(max_tokens=2), alignment_config=alignment_config)
    second = ensure_subtitle_plan(tmp_path, [entry], layout=Layout(max_tokens=2), alignment_config=alignment_config)
    assert first == second
    assert len(calls) == 1
    assert calls[0]["segments"][0]["text"] == entry["source_text"]
    meta = json.loads((tmp_path / "subtitles.source-aligned.meta.json").read_text())
    assert len(meta["source_hash"]) == 64


def test_overlapping_sentence_timings_fail_explicitly(tmp_path):
    with pytest.raises(SubtitleTimingError, match="overlaps"):
        ensure_subtitle_plan(tmp_path, [_entry(), _entry(segment_id=1, start=3, end=5)], layout=Layout())
