import json
import sys
import types
from pathlib import Path

import pytest

from youdub import tts
from youdub.network import network_router
from youdub.tts import (
    TTSConfig,
    choose_fallback_reference,
    generate_tts,
    load_translation_entries,
    normalize_tower_paths_for_tts,
    split_reference_audio,
    tts_synthesis_text,
    write_tts_mix,
)
from youdub.tts_redub import RedubTTSConfig, redub_tts


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


@pytest.mark.parametrize("module_name", ["core", "stretch"])
def test_stretch_segment_audio_supports_audiostretchy_layouts(tmp_path, monkeypatch, module_name):
    np, sf = _audio_modules()
    package = types.ModuleType("audiostretchy")
    package.__path__ = []
    module = types.ModuleType(f"audiostretchy.{module_name}")
    calls = []

    def stretch_audio(source, output, *, ratio):
        calls.append((source, output, ratio))
        sf.write(output, np.ones(12000, dtype=np.float32) * 0.1, 16000)

    module.stretch_audio = stretch_audio
    monkeypatch.setitem(sys.modules, "audiostretchy", package)
    monkeypatch.setitem(sys.modules, "audiostretchy.core", module if module_name == "core" else None)
    monkeypatch.setitem(sys.modules, "audiostretchy.stretch", module if module_name == "stretch" else None)
    source = tmp_path / "segment.wav"
    cache = tmp_path / "cache"
    cache.mkdir()
    sf.write(source, np.zeros(16000, dtype=np.float32), 16000)

    audio = tts._stretch_segment_audio(source, 0.8, 0.5, cache, TTSConfig())

    assert calls == [(str(source), str(cache / source.name), 0.8)]
    assert len(audio) == 8000


def test_load_voxcpm_model_tries_direct_before_system_proxy(monkeypatch) -> None:
    proxy = "socks5h://127.0.0.1:1081"
    captured = {}
    factories = []

    def configure_http_backend(backend_factory=None) -> None:
        factories.append(backend_factory)

    class FakeVoxCPM:
        @classmethod
        def from_pretrained(cls, model_source: str, *, load_denoiser: bool, device: str):
            captured["model_source"] = model_source
            captured["load_denoiser"] = load_denoiser
            captured["device"] = device
            captured["proxies"] = factories[-1]().proxies
            return object()

    monkeypatch.setitem(sys.modules, "voxcpm", types.SimpleNamespace(VoxCPM=FakeVoxCPM))
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        types.SimpleNamespace(configure_http_backend=configure_http_backend),
    )
    monkeypatch.setattr(tts, "_MODEL", None)
    monkeypatch.setattr(tts, "_MODEL_KEY", None)
    requested_devices = []

    def fake_resolve_device(device: str) -> types.SimpleNamespace:
        requested_devices.append(device)
        return types.SimpleNamespace(torch_name="cuda:2")

    monkeypatch.setattr(tts, "resolve_device", fake_resolve_device)
    network_router.clear()

    model = tts.load_voxcpm_model(
        TTSConfig(model="openbmb/VoxCPM2", load_denoiser=True, proxy=proxy, device="auto")
    )

    assert model is tts._MODEL
    assert captured == {
        "model_source": "openbmb/VoxCPM2",
        "load_denoiser": True,
        "device": "cuda:2",
        "proxies": {},
    }
    assert requested_devices == ["auto"]


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


def test_normalize_tower_paths_for_tts_supports_dash_and_compact_modes() -> None:
    assert normalize_tower_paths_for_tts("走2-0-5和a-b-c路线。") == "走2杠0杠5和a杠b杠c路线。"
    assert normalize_tower_paths_for_tts("走二-零-五路线。") == "走二杠零杠五路线。"
    assert normalize_tower_paths_for_tts("走2 - 0 - 5路线。", "compact") == "走205路线。"
    assert normalize_tower_paths_for_tts("gpt-4o 不应变化，2-0-5 应变化。") == "gpt-4o 不应变化，2杠0杠5 应变化。"


def test_tts_synthesis_text_prefers_explicit_tts_text_then_normalizes() -> None:
    entry = {"translation": "走 2-0-5 路线。", "tts_text": "走 a-b-c 路线。"}

    assert tts_synthesis_text(entry, TTSConfig()) == "走 a杠b杠c 路线。"


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


def test_generate_tts_uses_tower_path_tts_text_and_regenerates_without_matching_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    np, sf = _audio_modules()
    (tmp_path / "translation.json").write_text(
        json.dumps(
            [{"segment_id": 0, "start": 0.0, "end": 0.5, "translation": "走 2-0-5 和 a-b-c 路线。"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sf.write(tmp_path / "audio_vocals.wav", np.ones(16000, dtype=np.float32) * 0.05, 16000)
    tts_dir = tmp_path / "segments" / "tts"
    tts_dir.mkdir(parents=True)
    sf.write(tts_dir / "0001.wav", np.zeros(800, dtype=np.float32), 16000)
    captured_texts = []
    _mock_redub_scoring(monkeypatch)

    class CapturingModel(_FakeModel):
        def generate(self, **kwargs):
            captured_texts.append(kwargs["text"])
            return super().generate(**kwargs)

    monkeypatch.setattr("youdub.tts.load_voxcpm_model", lambda _config: CapturingModel())
    monkeypatch.setattr("youdub.tts.unload_voxcpm_model", lambda: None)

    generate_tts(tmp_path, TTSConfig(min_reference_ms=100, align_audio=False))

    assert captured_texts == ["走 2杠0杠5 和 a杠b杠c 路线。"]
    audio, _sample_rate = sf.read(tts_dir / "0001.wav", dtype="float32")
    assert float(audio.max()) > 0.09
    timings = json.loads((tmp_path / "audio_tts.timings.json").read_text(encoding="utf-8"))
    assert timings[0]["translation"] == "走 2-0-5 和 a-b-c 路线。"
    assert timings[0]["tts_text"] == "走 2杠0杠5 和 a杠b杠c 路线。"
    manifest = json.loads((tmp_path / "segments" / "tts.manifest.json").read_text(encoding="utf-8"))
    assert manifest["segments"]["0001"]["tts_text"] == "走 2杠0杠5 和 a杠b杠c 路线。"


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
    monkeypatch.setattr(tts, "_MODEL_KEY", ("model", False, None, "cpu"))

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


def test_redub_tts_replaces_segment_and_rebuilds_mix(tmp_path: Path, monkeypatch) -> None:
    np, sf = _audio_modules()
    (tmp_path / "translation.json").write_text(
        json.dumps(
            [
                {"start": 0.0, "end": 0.5, "translation": "第一句。"},
                {"start": 0.5, "end": 1.0, "translation": "第二句。"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    vocals_dir = tmp_path / "segments" / "vocals"
    tts_dir = tmp_path / "segments" / "tts"
    vocals_dir.mkdir(parents=True)
    tts_dir.mkdir(parents=True)
    for index in range(1, 3):
        sf.write(vocals_dir / f"{index:04d}.wav", np.ones(16000, dtype=np.float32) * 0.05, 16000)
        sf.write(tts_dir / f"{index:04d}.wav", np.zeros(800, dtype=np.float32), 16000)
    (tmp_path / "tts.redub.plan.json").write_text(
        json.dumps(
            {
                "version": 1,
                "round": 1,
                "max_rounds": 1,
                "segments": [{"segment_id": 0, "tts_index": 1, "translation": "第一句。", "similarity": 0.0}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("youdub.tts_redub.load_voxcpm_model", lambda _config: _FakeModel())
    _mock_redub_scoring(monkeypatch)
    unloaded = []
    monkeypatch.setattr("youdub.tts_redub.unload_voxcpm_model", lambda: unloaded.append(True))

    redub_tts(tmp_path, TTSConfig(min_reference_ms=100, align_audio=False), RedubTTSConfig())

    audio, _sample_rate = sf.read(tts_dir / "0001.wav", dtype="float32")
    assert float(audio.max()) > 0.09
    assert list((tmp_path / "segments" / "tts_versions" / "round-001").glob("attempt-*/0001.previous.wav"))
    assert list((tmp_path / "segments" / "tts_versions" / "round-001").glob("attempt-*/0001.new.wav"))
    assert (tmp_path / "audio_tts.wav").exists()
    history = (tmp_path / "tts.redub.history.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(history) == 1
    assert json.loads(history[0])["status"] == "success"
    assert unloaded == [True]


def test_redub_tts_uses_tower_path_tts_text(tmp_path: Path, monkeypatch) -> None:
    np, sf = _audio_modules()
    (tmp_path / "translation.json").write_text(
        json.dumps(
            [{"segment_id": 0, "start": 0.0, "end": 0.5, "translation": "走 2-0-5 路线。"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    vocals_dir = tmp_path / "segments" / "vocals"
    tts_dir = tmp_path / "segments" / "tts"
    vocals_dir.mkdir(parents=True)
    tts_dir.mkdir(parents=True)
    sf.write(vocals_dir / "0001.wav", np.ones(16000, dtype=np.float32) * 0.05, 16000)
    sf.write(tts_dir / "0001.wav", np.zeros(800, dtype=np.float32), 16000)
    (tmp_path / "tts.redub.plan.json").write_text(
        json.dumps(
            {
                "version": 1,
                "round": 1,
                "max_rounds": 1,
                "segments": [{"segment_id": 0, "tts_index": 1, "translation": "走 2-0-5 路线。"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    captured_texts = []
    _mock_redub_scoring(monkeypatch)

    class CapturingModel(_FakeModel):
        def generate(self, **kwargs):
            captured_texts.append(kwargs["text"])
            return super().generate(**kwargs)

    monkeypatch.setattr("youdub.tts_redub.load_voxcpm_model", lambda _config: CapturingModel())
    monkeypatch.setattr("youdub.tts_redub.unload_voxcpm_model", lambda: None)

    redub_tts(tmp_path, TTSConfig(min_reference_ms=100, align_audio=False), RedubTTSConfig())

    assert captured_texts == ["走 2杠0杠5 路线。"]
    manifest = json.loads((tmp_path / "segments" / "tts.manifest.json").read_text(encoding="utf-8"))
    assert manifest["segments"]["0001"]["tts_text"] == "走 2杠0杠5 路线。"


def _mock_redub_scoring(monkeypatch, scores=None):
    monkeypatch.setattr("youdub.tts_redub.load_redub_scorer", lambda config: object())
    def score(scorer, path, text):
        value = (0.0 if "previous" in path.name else 1.0) if scores is None else next(scores)
        return {"similarity": value, "asr_text": text, "source": "segment_asr"}
    monkeypatch.setattr("youdub.tts_redub.score_redub_audio", score)


@pytest.mark.parametrize("scores,expected,status,count", [
    ([0.4, 0.3, 0.2, 0.1], 0.4, "kept_original", 3),
    ([1.0], 1.0, "kept_original", 0),
    ([0.2, 0.6, 0.8, 0.5], 0.8, "success", 3),
    ([0.8, 0.81, 0.7, 0.6], 0.8, "kept_original", 3),
])
def test_redub_selects_only_better_candidates(tmp_path, monkeypatch, scores, expected, status, count):
    np, sf = _audio_modules()
    (tmp_path / "translation.json").write_text(json.dumps([
        {"start": 0, "end": 1, "translation": "这是一段测试音频。"}]))
    (tmp_path / "tts.redub.plan.json").write_text(json.dumps({"segments": [{"tts_index": 1}]}))
    for kind in ("tts", "vocals"):
        folder = tmp_path / "segments" / kind
        folder.mkdir(parents=True)
        sf.write(folder / "0001.wav", np.zeros(16000, dtype=np.float32), 16000)
    original = (tmp_path / "segments/tts/0001.wav").read_bytes()
    strategies = []
    class Model(_FakeModel):
        def generate(self, **kwargs):
            strategies.append((kwargs["cfg_value"], kwargs["inference_timesteps"]))
            return super().generate(**kwargs)
    monkeypatch.setattr("youdub.tts_redub.load_voxcpm_model", lambda config: Model())
    monkeypatch.setattr("youdub.tts_redub.unload_voxcpm_model", lambda: None)
    _mock_redub_scoring(monkeypatch, iter(scores))
    redub_tts(tmp_path, TTSConfig(align_audio=False), RedubTTSConfig())
    record = json.loads((tmp_path / "tts.redub.history.jsonl").read_text())
    assert record["status"] == status
    assert record["new_quality"]["similarity"] == expected
    assert len(strategies) == count
    assert len(set(strategies)) == count
    assert all(strategy != (2.0, 10) for strategy in strategies)
    if status == "kept_original":
        assert (tmp_path / "segments/tts/0001.wav").read_bytes() == original
    else:
        assert record["strategy"]["cfg_value"] == 1.5


def test_redub_scoring_failure_keeps_original(tmp_path, monkeypatch):
    np, sf = _audio_modules()
    (tmp_path / "translation.json").write_text(json.dumps([
        {"start": 0, "end": 1, "translation": "失败应保留原音频。"}]))
    (tmp_path / "tts.redub.plan.json").write_text(json.dumps({"segments": [{"tts_index": 1}]}))
    for kind in ("tts", "vocals"):
        folder = tmp_path / "segments" / kind
        folder.mkdir(parents=True)
        sf.write(folder / "0001.wav", np.zeros(16000, dtype=np.float32), 16000)
    active = tmp_path / "segments/tts/0001.wav"
    original = active.read_bytes()
    monkeypatch.setattr("youdub.tts_redub.load_redub_scorer", lambda config: object())
    def fail(*args):
        raise RuntimeError("ASR failed")
    monkeypatch.setattr("youdub.tts_redub.score_redub_audio", fail)
    monkeypatch.setattr("youdub.tts_redub.unload_voxcpm_model", lambda: None)
    with pytest.raises(RuntimeError, match="ASR failed"):
        redub_tts(tmp_path, TTSConfig(), RedubTTSConfig())
    assert active.read_bytes() == original
    assert json.loads((tmp_path / "tts.redub.history.jsonl").read_text())["status"] == "failed"


@pytest.mark.parametrize("kwargs", [
    {"candidates": 0}, {"candidates": 6}, {"accept_similarity": 1.1},
    {"min_improvement": 0}, {"accept_similarity": float("nan")},
])
def test_redub_config_rejects_invalid_selection_limits(kwargs):
    with pytest.raises(ValueError):
        RedubTTSConfig(**kwargs)
