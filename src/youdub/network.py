from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar
from urllib.parse import urlparse


VIDEO_SERVICE = "video-source"
TRANSLATION_SERVICE = "translation"
HUGGINGFACE_SERVICE = "huggingface"
BILIBILI_SERVICE = "bilibili"

DEFAULT_TRANSLATION_URL = "https://api.uiuihao.com/v1"
_MIN_PROBE_INTERVAL_SECONDS = 30.0
_PROBE_TIMEOUT_SECONDS = 4.0
_MAX_ERROR_LENGTH = 240

_T = TypeVar("_T")


@dataclass(frozen=True)
class NetworkRoute:
    name: str
    proxy: str | None


@dataclass(frozen=True)
class _ServiceProbe:
    key: str
    label: str
    url: str


def _service_probes(translation_url: str | None) -> tuple[_ServiceProbe, ...]:
    return (
        _ServiceProbe(VIDEO_SERVICE, "视频源", "https://www.youtube.com/generate_204"),
        _ServiceProbe(
            TRANSLATION_SERVICE,
            "翻译 API",
            (translation_url or DEFAULT_TRANSLATION_URL).rstrip("/"),
        ),
        _ServiceProbe(HUGGINGFACE_SERVICE, "Hugging Face", "https://huggingface.co/api/whoami-v2"),
        _ServiceProbe(BILIBILI_SERVICE, "Bilibili", "https://member.bilibili.com/"),
    )


class NetworkRouter:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._probe_lock = threading.Lock()
        self._states: dict[str, dict[str, Any]] = {}
        self._last_probe_monotonic = 0.0
        self._last_probe_at: str | None = None

    def clear(self) -> None:
        with self._lock:
            self._states.clear()
            self._last_probe_monotonic = 0.0
            self._last_probe_at = None

    def routes(self, service: str, proxy: str | None) -> tuple[NetworkRoute, ...]:
        clean_proxy = _clean_proxy(proxy)
        with self._lock:
            state = self._service_state(service)
            self._sync_proxy_state(state, clean_proxy)
            preferred = state["preferred_route"] if clean_proxy else "direct"
        direct = NetworkRoute("direct", None)
        if clean_proxy is None:
            return (direct,)
        proxied = NetworkRoute("proxy", clean_proxy)
        return (proxied, direct) if preferred == "proxy" else (direct, proxied)

    def run(
        self,
        service: str,
        proxy: str | None,
        operation: Callable[[NetworkRoute], _T],
    ) -> _T:
        last_error: Exception | None = None
        for route in self.routes(service, proxy):
            started = time.monotonic()
            try:
                result = operation(route)
            except Exception as exc:
                last_error = exc
                self.record_failure(
                    service,
                    route,
                    exc,
                    latency_ms=_elapsed_ms(started),
                    proxy_configured=bool(_clean_proxy(proxy)),
                )
                continue
            self.record_success(service, route, latency_ms=_elapsed_ms(started))
            return result
        if last_error is None:
            raise RuntimeError(f"No network route is available for {service}")
        raise last_error

    def record_success(self, service: str, route: NetworkRoute, *, latency_ms: float | None = None) -> None:
        with self._lock:
            state = self._service_state(service)
            route_state = state["routes"][route.name]
            route_state.update(
                {
                    "status": "healthy",
                    "latency_ms": _round_latency(latency_ms),
                    "last_checked_at": _utc_now(),
                    "last_error": None,
                    "successes": route_state["successes"] + 1,
                }
            )
            state["preferred_route"] = route.name

    def record_failure(
        self,
        service: str,
        route: NetworkRoute,
        error: Exception | str,
        *,
        latency_ms: float | None = None,
        proxy_configured: bool = True,
    ) -> None:
        with self._lock:
            state = self._service_state(service)
            route_state = state["routes"][route.name]
            route_state.update(
                {
                    "status": "unhealthy",
                    "latency_ms": _round_latency(latency_ms),
                    "last_checked_at": _utc_now(),
                    "last_error": _safe_error(error, route.proxy),
                    "failures": route_state["failures"] + 1,
                }
            )
            if proxy_configured:
                state["preferred_route"] = "proxy" if route.name == "direct" else "direct"
            else:
                state["preferred_route"] = "direct"

    def snapshot(self, proxy: str | None, translation_url: str | None = None) -> dict[str, Any]:
        clean_proxy = _clean_proxy(proxy)
        probes = _service_probes(translation_url)
        with self._lock:
            services = []
            for probe in probes:
                state = self._service_state(probe.key)
                self._sync_proxy_state(state, clean_proxy)
                direct = dict(state["routes"]["direct"])
                proxied = dict(state["routes"]["proxy"])
                if clean_proxy is None:
                    proxied.update(
                        {
                            "status": "unconfigured",
                            "latency_ms": None,
                            "last_checked_at": None,
                            "last_error": None,
                        }
                    )
                services.append(
                    {
                        "key": probe.key,
                        "label": probe.label,
                        "target": urlparse(probe.url).netloc,
                        "preferred_route": state["preferred_route"] if clean_proxy else "direct",
                        "routes": {"direct": direct, "proxy": proxied},
                    }
                )
            return {
                "proxy_configured": clean_proxy is not None,
                "last_probe_at": self._last_probe_at,
                "services": services,
            }

    def probe(
        self,
        proxy: str | None,
        translation_url: str | None = None,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        with self._probe_lock:
            now = time.monotonic()
            if not force and now - self._last_probe_monotonic < _MIN_PROBE_INTERVAL_SECONDS:
                return self.snapshot(proxy, translation_url)

            clean_proxy = _clean_proxy(proxy)
            work = []
            for probe in _service_probes(translation_url):
                with self._lock:
                    self._sync_proxy_state(self._service_state(probe.key), clean_proxy)
                work.append((probe, NetworkRoute("direct", None)))
                if clean_proxy:
                    work.append((probe, NetworkRoute("proxy", clean_proxy)))

            results: dict[str, dict[str, bool]] = {}
            with ThreadPoolExecutor(max_workers=len(work), thread_name_prefix="youdub-network-probe") as executor:
                futures = {
                    executor.submit(_probe_route, probe.url, route): (probe, route)
                    for probe, route in work
                }
                for future in as_completed(futures):
                    probe, route = futures[future]
                    try:
                        latency_ms = future.result()
                    except Exception as exc:
                        self._record_probe_failure(probe.key, route, exc)
                        healthy = False
                    else:
                        self._record_probe_success(probe.key, route, latency_ms)
                        healthy = True
                    results.setdefault(probe.key, {})[route.name] = healthy

            with self._lock:
                for probe in _service_probes(translation_url):
                    route_results = results.get(probe.key, {})
                    state = self._service_state(probe.key)
                    if route_results.get("direct"):
                        state["preferred_route"] = "direct"
                    elif clean_proxy and route_results.get("proxy"):
                        state["preferred_route"] = "proxy"
                self._last_probe_monotonic = time.monotonic()
                self._last_probe_at = _utc_now()
            return self.snapshot(proxy, translation_url)

    def _record_probe_success(self, service: str, route: NetworkRoute, latency_ms: float) -> None:
        with self._lock:
            state = self._service_state(service)["routes"][route.name]
            state.update(
                {
                    "status": "healthy",
                    "latency_ms": _round_latency(latency_ms),
                    "last_checked_at": _utc_now(),
                    "last_error": None,
                    "successes": state["successes"] + 1,
                }
            )

    def _record_probe_failure(self, service: str, route: NetworkRoute, error: Exception) -> None:
        with self._lock:
            state = self._service_state(service)["routes"][route.name]
            state.update(
                {
                    "status": "unhealthy",
                    "latency_ms": None,
                    "last_checked_at": _utc_now(),
                    "last_error": _safe_error(error, route.proxy),
                    "failures": state["failures"] + 1,
                }
            )

    def _service_state(self, service: str) -> dict[str, Any]:
        return self._states.setdefault(
            service,
            {
                "preferred_route": "direct",
                "proxy_value": None,
                "routes": {
                    "direct": _empty_route_state(),
                    "proxy": _empty_route_state(),
                },
            },
        )

    @staticmethod
    def _sync_proxy_state(state: dict[str, Any], proxy: str | None) -> None:
        if state["proxy_value"] == proxy:
            return
        state["proxy_value"] = proxy
        state["routes"]["proxy"] = _empty_route_state()
        if state["preferred_route"] == "proxy":
            state["preferred_route"] = "direct"


def _probe_route(url: str, route: NetworkRoute) -> float:
    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise RuntimeError("httpx is required for network health probes") from exc

    started = time.monotonic()
    with httpx.Client(
        proxy=route.proxy,
        trust_env=False,
        timeout=_PROBE_TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        with client.stream(
            "GET",
            url,
            headers={"Accept": "*/*", "Range": "bytes=0-0", "User-Agent": "better-youdub-health/1"},
        ) as response:
            if response.status_code >= 500:
                raise RuntimeError(f"HTTP {response.status_code}")
    return _elapsed_ms(started)


def _empty_route_state() -> dict[str, Any]:
    return {
        "status": "unknown",
        "latency_ms": None,
        "last_checked_at": None,
        "last_error": None,
        "successes": 0,
        "failures": 0,
    }


def _clean_proxy(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _safe_error(error: Exception | str, proxy: str | None) -> str:
    value = str(error).strip() or type(error).__name__
    if proxy:
        value = value.replace(proxy, "<proxy>")
    return value[:_MAX_ERROR_LENGTH]


def _round_latency(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


def _elapsed_ms(started: float) -> float:
    return (time.monotonic() - started) * 1000.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


network_router = NetworkRouter()
