from __future__ import annotations

import sys
import types

from youdub import gpu


class _FakeCuda:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def is_available(self) -> bool:
        return True

    def current_device(self) -> int:
        return 0

    def memory_allocated(self, device=None) -> int:
        return 1024

    def memory_reserved(self, device=None) -> int:
        return 2048

    def max_memory_reserved(self, device=None) -> int:
        return 4096

    def synchronize(self) -> None:
        self.calls.append("synchronize")

    def empty_cache(self) -> None:
        self.calls.append("empty_cache")

    def ipc_collect(self) -> None:
        self.calls.append("ipc_collect")


class _DeviceSelectionCuda:
    def is_available(self) -> bool:
        return True

    def device_count(self) -> int:
        return 3

    def mem_get_info(self, _index: int) -> tuple[int, int]:
        return (10, 20)


def test_cleanup_gpu_memory_calls_torch_cuda_cleanup(monkeypatch) -> None:
    fake_cuda = _FakeCuda()
    fake_torch = types.SimpleNamespace(cuda=fake_cuda)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    gpu.cleanup_gpu_memory("test")

    assert fake_cuda.calls == ["synchronize", "empty_cache", "ipc_collect"]


def test_cuda_memory_snapshot_is_none_without_cuda(monkeypatch) -> None:
    fake_cuda = types.SimpleNamespace(is_available=lambda: False)
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=fake_cuda))

    assert gpu.cuda_memory_snapshot("test") is None


def test_resolve_device_rotates_equal_memory_auto_choices(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "torch",
        types.SimpleNamespace(cuda=_DeviceSelectionCuda()),
    )
    monkeypatch.setattr(gpu, "_NEXT_TIE_BREAKER", 0)

    selected = [gpu.resolve_device("auto").index for _ in range(4)]

    assert selected == [0, 1, 2, 0]


def test_resolve_device_avoids_excluded_gpu_when_possible(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "torch",
        types.SimpleNamespace(cuda=_DeviceSelectionCuda()),
    )
    monkeypatch.setattr(gpu, "_NEXT_TIE_BREAKER", 0)

    selected = gpu.resolve_device("cuda", excluded_indices={0, 1})

    assert selected == gpu.ResolvedDevice("cuda", 2)
