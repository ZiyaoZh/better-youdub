import os
import sys
import types

from youdub.hf_runtime import cached_huggingface_snapshot, huggingface_download_context


def test_cached_huggingface_snapshot_resolves_complete_main_revision(monkeypatch, tmp_path) -> None:
    hf_home = tmp_path / "huggingface"
    repository = hf_home / "hub" / "models--openbmb--VoxCPM2"
    snapshot = repository / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (repository / "refs").mkdir()
    (repository / "refs" / "main").write_text("abc123\n", encoding="utf-8")
    for name in ("config.json", "model.safetensors"):
        (snapshot / name).write_text(name, encoding="utf-8")
    monkeypatch.setenv("HF_HOME", str(hf_home))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)

    assert cached_huggingface_snapshot(
        "openbmb/VoxCPM2",
        required_files=("config.json", "model.safetensors"),
    ) == snapshot
    assert cached_huggingface_snapshot(
        "openbmb/VoxCPM2",
        required_files=("config.json", "missing.bin"),
    ) is None


def test_huggingface_download_context_uses_task_proxy_and_disables_xet(monkeypatch) -> None:
    factories = []
    constants = types.SimpleNamespace(HF_HUB_DISABLE_XET=False)

    def configure_http_backend(backend_factory=None) -> None:
        factories.append(backend_factory)

    fake_hub = types.SimpleNamespace(
        configure_http_backend=configure_http_backend,
        constants=constants,
    )
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://existing-proxy:7890")

    with huggingface_download_context("socks5h://127.0.0.1:1081"):
        session = factories[-1]()
        assert session.proxies == {
            "http": "socks5h://127.0.0.1:1081",
            "https": "socks5h://127.0.0.1:1081",
        }
        assert session.adapters["https://"].max_retries.total == 4
        assert session.adapters["https://"].max_retries.read == 4
        assert constants.HF_HUB_DISABLE_XET is True
        assert os.environ["HTTPS_PROXY"] == "http://existing-proxy:7890"

    assert factories[-1] is None
    assert os.environ["HF_HUB_DISABLE_XET"] == "1"
    assert os.environ["HTTPS_PROXY"] == "http://existing-proxy:7890"


def test_huggingface_download_context_without_task_proxy_preserves_environment(monkeypatch) -> None:
    factories = []

    def configure_http_backend(backend_factory=None) -> None:
        factories.append(backend_factory)

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        types.SimpleNamespace(configure_http_backend=configure_http_backend),
    )
    monkeypatch.setenv("HTTPS_PROXY", "http://existing-proxy:7890")

    with huggingface_download_context(None):
        assert os.environ["HTTPS_PROXY"] == "http://existing-proxy:7890"
        assert factories[-1]().proxies == {}

    assert os.environ["HTTPS_PROXY"] == "http://existing-proxy:7890"
