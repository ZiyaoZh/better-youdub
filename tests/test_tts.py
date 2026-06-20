import json
from pathlib import Path

from youdub import tts
from youdub.tts import (
    TTSConfig,
    choose_fallback_reference,
    generate_tts,
    load_translation_entries,
    split_reference_audio,
    write_tts_mix,
)


def _audio_modules():
    import pytest

    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    return np, sf


class _FakeTTSModel:
    sample_rate = 16000


class _FakeModel:
    tts_model = _FakeTTSModel()

    def generate(self, **kwargs):
        np, _sf = _audio_modules()
        assert kwargs["reference_wav_path"]
        assert kwargs["text"]
        return np.ones(800, dtype=np.float32) * 0.1


def test_load_translation_entries_accepts_current_list_format(tmp_path: Path) -> None:
    path = tmp_path / "translation.json"
    path.write_text(
        json.dumps(
            [
                {
                    "start": 1.0,
                    "end": 2.5,
                    "translation": "你好，世界。",
                    "source_text": "Hello world.",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    entries = load_translation_entries(path)

    assert entries == [
        {
            "start": 1.0,
            "end": 2.5,
            "translation": "你好，世界。",
            "source_text": "Hello world.",
        }
    ]


def test_split_reference_audio_and_fallback(tmp_path: Path) -> None:
    np, sf = _audio_modules()
    vocals = tmp_path / "audio_vocals.wav"
    samples = np.zeros(32000, dtype=np.float32)
    sf.write(vocals, samples, 16000)
    entries = [
        {"start": 0.1, "end": 0.2, "translation": "短句。"},
        {"start": 0.2, "end": 1.7, "translation": "长句。"},
    ]

    output_dir = split_reference_audio(vocals, entries, tmp_path, start_pad_ms=0, end_pad_ms=0)
    fallback = choose_fallback_reference(output_dir, min_reference_ms=1000)

    assert (output_dir / "0001.wav").exists()
    assert (output_dir / "0002.wav").exists()
    assert fallback == output_dir / "0002.wav"


def test_generate_tts_writes_segments_mix_and_timings(tmp_path: Path, monkeypatch) -> None:
    np, sf = _audio_modules()
    (tmp_path / "translation.json").write_text(
        json.dumps(
            [
                {"start": 0.0, "end": 0.5, "translation": "第一句。"},
                {"start": 0.7, "end": 1.2, "translation": "第二句。"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sf.write(tmp_path / "audio_vocals.wav", np.ones(32000, dtype=np.float32) * 0.05, 16000)
    unloaded = []
    monkeypatch.setattr("youdub.tts.load_voxcpm_model", lambda _config: _FakeModel())
    monkeypatch.setattr("youdub.tts.unload_voxcpm_model", lambda: unloaded.append(True))

    output = generate_tts(tmp_path, TTSConfig(min_reference_ms=100, align_audio=False))

    assert output == tmp_path / "audio_tts.wav"
    assert output.exists()
    assert (tmp_path / "segments" / "tts" / "0001.wav").exists()
    assert (tmp_path / "segments" / "tts" / "0002.wav").exists()
    timings = json.loads((tmp_path / "audio_tts.timings.json").read_text(encoding="utf-8"))
    assert [item["translation"] for item in timings] == ["第一句。", "第二句。"]
    assert unloaded == [True]


def test_generate_tts_can_keep_model_cached(tmp_path: Path, monkeypatch) -> None:
    np, sf = _audio_modules()
    (tmp_path / "translation.json").write_text(
        json.dumps([{"start": 0.0, "end": 0.5, "translation": "第一句。"}], ensure_ascii=False),
        encoding="utf-8",
    )
    sf.write(tmp_path / "audio_vocals.wav", np.ones(16000, dtype=np.float32) * 0.05, 16000)
    unloaded = []
    monkeypatch.setattr("youdub.tts.load_voxcpm_model", lambda _config: _FakeModel())
    monkeypatch.setattr("youdub.tts.unload_voxcpm_model", lambda: unloaded.append(True))

    generate_tts(tmp_path, TTSConfig(min_reference_ms=100, align_audio=False, cache_model=True))

    assert unloaded == []


def test_unload_voxcpm_model_clears_cached_model(monkeypatch) -> None:
    cleanup_calls = []
    monkeypatch.setattr(tts, "cleanup_gpu_memory", lambda label: cleanup_calls.append(label))
    monkeypatch.setattr(tts, "_MODEL", object())
    monkeypatch.setattr(tts, "_MODEL_KEY", ("model", False, None))

    assert tts.unload_voxcpm_model("test-unload") is True

    assert tts._MODEL is None
    assert tts._MODEL_KEY is None
    assert cleanup_calls == ["test-unload"]


def test_write_tts_mix_aligns_long_segments_without_accumulating_drift(tmp_path: Path, monkeypatch) -> None:
    np, sf = _audio_modules()
    tts_dir = tmp_path / "segments" / "tts"
    tts_dir.mkdir(parents=True)
    entries = [
        {"start": 0.0, "end": 1.0, "translation": "第一句。"},
        {"start": 1.0, "end": 2.0, "translation": "第二句。"},
        {"start": 2.0, "end": 3.0, "translation": "第三句。"},
    ]
    for index in range(1, 4):
        sf.write(tts_dir / f"{index:04d}.wav", np.ones(16000, dtype=np.float32) * 0.1, 10000)

    def fake_stretch(segment_path: Path, ratio: float, target_duration: float, cache_dir: Path, config: TTSConfig):
        sample_rate = 10000
        return np.ones(int(round(target_duration * sample_rate)), dtype=np.float32) * 0.1

    monkeypatch.setattr("youdub.tts._stretch_segment_audio", fake_stretch)

    write_tts_mix(entries, tts_dir, tmp_path, TTSConfig())

    timings = json.loads((tmp_path / "audio_tts.timings.json").read_text(encoding="utf-8"))
    assert timings[-1]["actual_end"] < 3.6
    assert timings[-1]["actual_end"] < sum(item["raw_duration"] for item in timings)
    assert all(item["alignment_status"] in {"stretched", "overflow"} for item in timings)
    assert all(item["stretch_ratio"] < 1.0 for item in timings)
