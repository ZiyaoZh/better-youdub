from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


_HUGGINGFACE_DOWNLOAD_LOCK = threading.RLock()


def prepare_huggingface_environment() -> None:
    # The Xet client does not reliably honor the per-task SOCKS proxy and can
    # retry an unreachable CAS endpoint for hours. Regular Hub HTTP downloads
    # are resumable and use the configured requests session.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


@contextmanager
def huggingface_download_context(proxy: str | None) -> Iterator[None]:
    prepare_huggingface_environment()
    proxy = _clean_optional_text(proxy)

    with _HUGGINGFACE_DOWNLOAD_LOCK:
        try:
            import huggingface_hub
        except ModuleNotFoundError:
            yield
            return

        _sync_xet_setting_if_already_imported(huggingface_hub)
        configure_http_backend = getattr(huggingface_hub, "configure_http_backend", None)
        if callable(configure_http_backend):
            configure_http_backend(backend_factory=lambda: _requests_session(proxy))
        try:
            yield
        finally:
            if callable(configure_http_backend):
                configure_http_backend()


def _requests_session(proxy: str | None) -> Any:
    import requests

    session = requests.Session()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def _sync_xet_setting_if_already_imported(huggingface_hub: Any) -> None:
    constants = getattr(huggingface_hub, "constants", None)
    if constants is not None and hasattr(constants, "HF_HUB_DISABLE_XET"):
        value = os.environ.get("HF_HUB_DISABLE_XET", "1").strip().lower()
        constants.HF_HUB_DISABLE_XET = value not in {"0", "false", "no", "off"}


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
