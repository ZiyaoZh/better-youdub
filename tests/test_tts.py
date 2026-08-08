import json
import sys
import types
from pathlib import Path

from youdub import tts, tts_redub
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


def test_load_voxcpm_model_tries_direct_before_system_proxy(monkeypatch) -> None:
    proxy = "socks5h://127.0.0.1:1081"
    captured = {}
    factories = []

    def configure_http_backend(backend_factory=None) -> None:
        factories.append(backend_factory)

    class FakeVoxCPM:
        @classmethod
        def from_pretrained(
            cls,
            model_source: str,
            *,
            load_denoiser: bool,
            device: str,
            optimize: bool,
        ):
            captured["model_source"] = model_source
            captured["load_denoiser"] = load_denoiser
            captured["device"] = device
            captured["optimize"] = optimize
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
    network_router.clear()

    model = tts.load_voxcpm_model(TTSConfig(model="openbmb/VoxCPM2", load_denoiser=True, proxy=proxy))

    assert model is tts._MODEL
    assert captured == {
        "model_source": "openbmb/VoxCPM2",
        "load_denoiser": True,
        "device": "auto",
        "optimize": True,
        "proxies": {},
    }


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
    monkeypatch.setattr(
        "youdub.tts.load_voxcpm_model",
        lambda _config, *, device="auto": _FakeModel(),
    )
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

    class CapturingModel(_FakeModel):
        def generate(self, **kwargs):
            captured_texts.append(kwargs["text"])
            return super().generate(**kwargs)

    monkeypatch.setattr(
        "youdub.tts.load_voxcpm_model",
        lambda _config, *, device="auto": CapturingModel(),
    )
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
    monkeypatch.setattr(
        "youdub.tts.load_voxcpm_model",
        lambda _config, *, device="auto": _FakeModel(),
    )
    monkeypatch.setattr("youdub.tts.unload_voxcpm_model", lambda: unloaded.append(True))

    generate_tts(tmp_path, TTSConfig(min_reference_ms=100, align_audio=False, cache_model=True))

    assert unloaded == []


def test_generate_tts_reloads_on_cpu_after_cuda_oom(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "translation.json").write_text(
        json.dumps(
            [{"start": 0.0, "end": 0.5, "translation": "第一句。"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    devices = []
    unloaded = []

    def fake_split_reference_audio(*_args, **_kwargs) -> Path:
        vocals_dir = tmp_path / "segments" / "vocals"
        vocals_dir.mkdir(parents=True)
        (vocals_dir / "0001.wav").write_bytes(b"reference")
        return vocals_dir

    class FakeSoundFile:
        @staticmethod
        def write(path: str, wav, sample_rate: int) -> None:
            assert wav == b"generated"
            assert sample_rate == 16000
            Path(path).write_bytes(wav)

    class CpuModel:
        tts_model = _FakeTTSModel()

        def generate(self, **kwargs):
            return b"generated"

    class CudaOomModel(CpuModel):
        def generate(self, **kwargs):
            raise RuntimeError("CUDA out of memory while running VoxCPM")

    def fake_load_model(_config, *, device="auto"):
        devices.append(device)
        return CudaOomModel() if device == "auto" else CpuModel()

    monkeypatch.setattr(tts, "split_reference_audio", fake_split_reference_audio)
    monkeypatch.setattr(tts, "audio_duration_ms", lambda _path: 1000.0)
    monkeypatch.setattr(tts, "_soundfile", lambda: FakeSoundFile())
    monkeypatch.setattr(
        tts,
        "write_tts_mix",
        lambda _entries, _tts_dir, task_dir, _config: task_dir / "audio_tts.wav",
    )
    monkeypatch.setattr("youdub.tts.load_voxcpm_model", fake_load_model)
    monkeypatch.setattr("youdub.tts.unload_voxcpm_model", lambda: unloaded.append(True))
    monkeypatch.setattr("youdub.gpu.cleanup_gpu_memory", lambda _label: None)

    output = generate_tts(
        tmp_path,
        TTSConfig(min_reference_ms=100, align_audio=False),
    )

    assert output == tmp_path / "audio_tts.wav"
    assert devices == ["auto", "cpu"]
    assert unloaded == [True, True]
    assert (tmp_path / "segments" / "tts" / "0001.wav").read_bytes() == b"generated"


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
    monkeypatch.setattr(
        "youdub.tts_redub.load_voxcpm_model",
        lambda _config, *, device="auto": _FakeModel(),
    )
    unloaded = []
    monkeypatch.setattr("youdub.tts_redub.unload_voxcpm_model", lambda: unloaded.append(True))

    redub_tts(tmp_path, TTSConfig(min_reference_ms=100, align_audio=False), RedubTTSConfig())

    audio, _sample_rate = sf.read(tts_dir / "0001.wav", dtype="float32")
    assert float(audio.max()) > 0.09
    assert (tmp_path / "segments" / "tts_versions" / "round-001" / "0001.previous.wav").exists()
    assert (tmp_path / "segments" / "tts_versions" / "round-001" / "0001.new.wav").exists()
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

    class CapturingModel(_FakeModel):
        def generate(self, **kwargs):
            captured_texts.append(kwargs["text"])
            return super().generate(**kwargs)

    monkeypatch.setattr(
        "youdub.tts_redub.load_voxcpm_model",
        lambda _config, *, device="auto": CapturingModel(),
    )
    monkeypatch.setattr("youdub.tts_redub.unload_voxcpm_model", lambda: None)

    redub_tts(tmp_path, TTSConfig(min_reference_ms=100, align_audio=False), RedubTTSConfig())

    assert captured_texts == ["走 2杠0杠5 路线。"]
    manifest = json.loads((tmp_path / "segments" / "tts.manifest.json").read_text(encoding="utf-8"))
    assert manifest["segments"]["0001"]["tts_text"] == "走 2杠0杠5 路线。"


def test_redub_tts_resumes_unfinished_segments_on_cpu_after_cuda_oom(
    tmp_path: Path,
    monkeypatch,
) -> None:
    entries = [
        {"start": 0.0, "end": 0.5, "translation": "第一句。"},
        {"start": 0.5, "end": 1.0, "translation": "第二句。"},
    ]
    (tmp_path / "translation.json").write_text(
        json.dumps(entries, ensure_ascii=False),
        encoding="utf-8",
    )
    vocals_dir = tmp_path / "segments" / "vocals"
    tts_dir = tmp_path / "segments" / "tts"
    vocals_dir.mkdir(parents=True)
    tts_dir.mkdir(parents=True)
    for index in range(1, 3):
        (vocals_dir / f"{index:04d}.wav").write_bytes(f"reference-{index}".encode())
        (tts_dir / f"{index:04d}.wav").write_bytes(f"original-{index}".encode())
    (tmp_path / "tts.redub.plan.json").write_text(
        json.dumps(
            {
                "segments": [
                    {"segment_id": 0, "tts_index": 1},
                    {"segment_id": 1, "tts_index": 2},
                ]
            }
        ),
        encoding="utf-8",
    )
    devices = []
    generate_calls = []
    unloaded = []

    class FakeSoundFile:
        @staticmethod
        def write(path: str, wav, sample_rate: int) -> None:
            assert sample_rate == 16000
            Path(path).write_bytes(wav)

    class FakeModel:
        tts_model = _FakeTTSModel()

        def __init__(self, device: str) -> None:
            self.device = device

        def generate(self, **kwargs):
            text = kwargs["text"]
            generate_calls.append((self.device, text))
            if self.device == "auto" and text == "第二句。":
                raise RuntimeError("CUDA out of memory during local redub")
            return f"{self.device}:{text}".encode()

    def fake_load_model(_config, *, device="auto"):
        devices.append(device)
        return FakeModel(device)

    monkeypatch.setattr(tts_redub, "load_voxcpm_model", fake_load_model)
    monkeypatch.setattr(tts_redub, "unload_voxcpm_model", lambda: unloaded.append(True))
    monkeypatch.setattr(tts_redub, "audio_duration_ms", lambda _path: 1000.0)
    monkeypatch.setattr(tts, "audio_duration_ms", lambda _path: 1000.0)
    monkeypatch.setattr(tts_redub, "_soundfile", lambda: FakeSoundFile())
    monkeypatch.setattr(tts_redub, "update_tts_manifest_record", lambda *_args: None)
    monkeypatch.setattr(
        tts_redub,
        "write_tts_mix",
        lambda _entries, _tts_dir, task_dir, _config: task_dir / "audio_tts.wav",
    )
    monkeypatch.setattr("youdub.gpu.cleanup_gpu_memory", lambda _label: None)

    output = redub_tts(
        tmp_path,
        TTSConfig(min_reference_ms=100, align_audio=False),
        RedubTTSConfig(),
    )

    assert output == tmp_path / "audio_tts.wav"
    assert devices == ["auto", "cpu"]
    assert generate_calls == [
        ("auto", "第一句。"),
        ("auto", "第二句。"),
        ("cpu", "第二句。"),
    ]
    assert unloaded == [True, True]
    version_dir = tmp_path / "segments" / "tts_versions" / "round-001"
    assert (version_dir / "0001.previous.wav").read_bytes() == b"original-1"
    history = [
        json.loads(line)
        for line in (tmp_path / "tts.redub.history.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [item["status"] for item in history] == ["success", "success"]
