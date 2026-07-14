import sys
import types

from youdub.hf_runtime import huggingface_download_context


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

    with huggingface_download_context("socks5h://127.0.0.1:1081"):
        session = factories[-1]()
        assert session.proxies == {
            "http": "socks5h://127.0.0.1:1081",
            "https": "socks5h://127.0.0.1:1081",
        }
        assert constants.HF_HUB_DISABLE_XET is True

    assert factories[-1] is None
    assert __import__("os").environ["HF_HUB_DISABLE_XET"] == "1"
