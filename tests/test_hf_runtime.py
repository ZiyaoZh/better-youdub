import os
import sys
import types

from youdub import network
from youdub.hf_runtime import (
    cached_huggingface_snapshot,
    cached_huggingface_snapshot_at,
    huggingface_download_context,
    run_huggingface_download,
)
from youdub.network import network_router


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
    assert cached_huggingface_snapshot_at(
        hf_home / "hub",
        "openbmb/VoxCPM2",
        required_files=("config.json", "model.safetensors"),
    ) == snapshot


def test_huggingface_download_context_uses_proxy_and_disables_xet(monkeypatch) -> None:
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
    monkeypatch.setenv("HTTP_PROXY", "http://existing-http-proxy:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://existing-proxy:7890")
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.setenv("http_proxy", "http://existing-lower-http-proxy:7890")
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.setenv("all_proxy", "socks5h://existing-all-proxy:1080")

    with huggingface_download_context("socks5h://127.0.0.1:1081"):
        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            assert os.environ[name] == "socks5h://127.0.0.1:1081"
        session = factories[-1]()
        assert session.proxies == {
            "http": "socks5h://127.0.0.1:1081",
            "https": "socks5h://127.0.0.1:1081",
        }
        assert session.adapters["https://"].max_retries.total == 0
        assert session.adapters["https://"].max_retries.read == 0
        assert constants.HF_HUB_DISABLE_XET is True

    assert factories[-1] is None
    assert os.environ["HF_HUB_DISABLE_XET"] == "1"
    assert os.environ["HTTP_PROXY"] == "http://existing-http-proxy:7890"
    assert os.environ["HTTPS_PROXY"] == "http://existing-proxy:7890"
    assert "ALL_PROXY" not in os.environ
    assert os.environ["http_proxy"] == "http://existing-lower-http-proxy:7890"
    assert "https_proxy" not in os.environ
    assert os.environ["all_proxy"] == "socks5h://existing-all-proxy:1080"


def test_huggingface_download_context_without_proxy_preserves_environment(monkeypatch) -> None:
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


def test_huggingface_download_context_restores_proxy_environment_after_error(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace())
    monkeypatch.setenv("HTTPS_PROXY", "http://existing-proxy:7890")
    monkeypatch.delenv("https_proxy", raising=False)

    try:
        with huggingface_download_context("socks5h://127.0.0.1:1081"):
            raise RuntimeError("download failed")
    except RuntimeError:
        pass

    assert os.environ["HTTPS_PROXY"] == "http://existing-proxy:7890"
    assert "https_proxy" not in os.environ


def test_huggingface_download_automatically_falls_back_to_proxy(monkeypatch) -> None:
    proxy = "socks5h://127.0.0.1:1081"
    calls = []
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace())
    monkeypatch.setenv("HTTPS_PROXY", "http://existing-proxy:7890")
    network_router.clear()

    def operation():
        selected = os.getenv("HTTPS_PROXY")
        calls.append(selected)
        if selected is None:
            raise RuntimeError("direct unavailable")
        return "loaded"

    assert run_huggingface_download(proxy, operation, prefer_proxy=False) == "loaded"
    assert calls == [None, proxy]
    assert os.environ["HTTPS_PROXY"] == "http://existing-proxy:7890"


def test_huggingface_download_prefers_configured_proxy(monkeypatch) -> None:
    proxy = "socks5h://127.0.0.1:1081"
    calls = []
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace())
    network_router.clear()

    def operation():
        calls.append(os.getenv("HTTPS_PROXY"))
        return "loaded"

    assert run_huggingface_download(proxy, operation, prefer_proxy=True) == "loaded"
    assert calls == [proxy]


def test_huggingface_download_retries_direct_only_after_proxy_failure(monkeypatch) -> None:
    proxy = "socks5h://127.0.0.1:1081"
    calls = []
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace())
    network_router.clear()
    network_router.record_failure(
        network.HUGGINGFACE_SERVICE,
        network.NetworkRoute("proxy", proxy),
        "health probe timeout",
    )

    def operation():
        selected = os.getenv("HTTPS_PROXY")
        calls.append(selected)
        if selected == proxy:
            raise RuntimeError("proxy download failed")
        return "loaded directly"

    assert run_huggingface_download(proxy, operation, prefer_proxy=True) == "loaded directly"
    assert calls == [proxy, None]
