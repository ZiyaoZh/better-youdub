from __future__ import annotations

from youdub import network
from youdub.network import NetworkRouter


def test_network_router_switches_to_the_other_route_after_each_failure() -> None:
    router = NetworkRouter()
    proxy = "socks5h://127.0.0.1:1081"
    calls: list[str] = []

    def direct_fails(route):
        calls.append(route.name)
        if route.name == "direct":
            raise RuntimeError("direct timeout")
        return "proxied"

    assert router.run(network.VIDEO_SERVICE, proxy, direct_fails) == "proxied"
    assert calls == ["direct", "proxy"]
    assert router.routes(network.VIDEO_SERVICE, proxy)[0].name == "proxy"

    calls.clear()

    def proxy_fails(route):
        calls.append(route.name)
        if route.name == "proxy":
            raise RuntimeError("proxy timeout")
        return "direct"

    assert router.run(network.VIDEO_SERVICE, proxy, proxy_fails) == "direct"
    assert calls == ["proxy", "direct"]
    assert router.routes(network.VIDEO_SERVICE, proxy)[0].name == "direct"


def test_network_probe_selects_proxy_when_direct_is_unhealthy(monkeypatch) -> None:
    router = NetworkRouter()
    proxy = "socks5h://user:secret@127.0.0.1:1081"

    def fake_probe(url, route):
        if route.name == "direct":
            raise RuntimeError(f"direct cannot reach {url}")
        return 18.25

    monkeypatch.setattr(network, "_probe_route", fake_probe)

    payload = router.probe(proxy, force=True)

    assert payload["proxy_configured"] is True
    assert payload["last_probe_at"]
    assert len(payload["services"]) == 4
    assert all(service["preferred_route"] == "proxy" for service in payload["services"])
    assert all(service["routes"]["direct"]["status"] == "unhealthy" for service in payload["services"])
    assert all(service["routes"]["proxy"]["status"] == "healthy" for service in payload["services"])
    assert proxy not in str(payload)
    assert "secret" not in str(payload)


def test_network_snapshot_marks_proxy_unconfigured() -> None:
    payload = NetworkRouter().snapshot(None)

    assert payload["proxy_configured"] is False
    assert all(service["preferred_route"] == "direct" for service in payload["services"])
    assert all(service["routes"]["proxy"]["status"] == "unconfigured" for service in payload["services"])


def test_network_router_discards_proxy_health_when_system_proxy_changes() -> None:
    router = NetworkRouter()
    old_proxy = "socks5h://127.0.0.1:1081"
    new_proxy = "http://127.0.0.1:7890"
    direct, proxied = router.routes(network.TRANSLATION_SERVICE, old_proxy)
    router.record_failure(network.TRANSLATION_SERVICE, direct, "direct timeout")
    router.record_success(network.TRANSLATION_SERVICE, proxied, latency_ms=10)

    routes = router.routes(network.TRANSLATION_SERVICE, new_proxy)
    payload = router.snapshot(new_proxy)
    translation = next(item for item in payload["services"] if item["key"] == network.TRANSLATION_SERVICE)

    assert routes[0].name == "direct"
    assert translation["routes"]["proxy"]["status"] == "unknown"
    assert translation["routes"]["proxy"]["successes"] == 0
