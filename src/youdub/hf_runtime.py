from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


_HUGGINGFACE_DOWNLOAD_LOCK = threading.RLock()
_RETRYABLE_STATUS_CODES = (429, 500, 502, 503, 504)


def prepare_huggingface_environment() -> None:
    # The Xet client does not reliably honor the per-task SOCKS proxy and can
    # retry an unreachable CAS endpoint for hours. Regular Hub HTTP downloads
    # are resumable and use the configured requests session.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


def cached_huggingface_snapshot(
    model_id: str,
    *,
    required_files: tuple[str, ...] = (),
) -> Path | None:
    model_id = model_id.strip().strip("/")
    model_parts = model_id.split("/")
    if not model_id or any(part in {"", ".", ".."} for part in model_parts):
        return None

    hub_cache = _configured_huggingface_hub_cache()
    if hub_cache is None:
        return None

    repository = hub_cache / f"models--{'--'.join(model_parts)}"
    try:
        commit = (repository / "refs" / "main").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    if not commit or Path(commit).name != commit or commit in {".", ".."}:
        return None

    snapshot = repository / "snapshots" / commit
    if not snapshot.is_dir():
        return None
    if any(not (snapshot / name).is_file() for name in required_files):
        return None
    return snapshot


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
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    adapter = HTTPAdapter(
        max_retries=Retry(
            total=4,
            connect=4,
            read=4,
            status=4,
            backoff_factor=0.5,
            status_forcelist=_RETRYABLE_STATUS_CODES,
            allowed_methods=frozenset({"GET", "HEAD"}),
        )
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def _sync_xet_setting_if_already_imported(huggingface_hub: Any) -> None:
    constants = getattr(huggingface_hub, "constants", None)
    if constants is not None and hasattr(constants, "HF_HUB_DISABLE_XET"):
        value = os.environ.get("HF_HUB_DISABLE_XET", "1").strip().lower()
        constants.HF_HUB_DISABLE_XET = value not in {"0", "false", "no", "off"}


def _configured_huggingface_hub_cache() -> Path | None:
    hub_cache = _clean_optional_text(os.getenv("HF_HUB_CACHE"))
    if hub_cache:
        return Path(hub_cache).expanduser()
    hf_home = _clean_optional_text(os.getenv("HF_HOME"))
    if hf_home:
        return Path(hf_home).expanduser() / "hub"
    return None


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
