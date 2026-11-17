from __future__ import annotations

import sys
import types
import errno
import importlib.util
import stat
from pathlib import Path

import pytest

from youdub import gpu


class _FakeCuda:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def is_initialized(self) -> bool:
        return True

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


@pytest.mark.parametrize("requested", ["auto", "cuda", "cuda:2"])
def test_resolve_device_reports_lost_container_permissions(monkeypatch, requested) -> None:
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False),
    ))

    def denied(*args):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(gpu.os, "open", denied)
    with pytest.raises(RuntimeError, match="cgroup permissions"):
        gpu.resolve_device(requested)


def test_resolve_device_auto_still_uses_cpu_without_gpu(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False),
    ))

    def missing(*args):
        raise FileNotFoundError(errno.ENOENT, "No such file or directory")

    monkeypatch.setattr(gpu.os, "open", missing)
    assert gpu.resolve_device("auto") == gpu.ResolvedDevice("cpu")
    with pytest.raises(RuntimeError, match="no CUDA device is available"):
        gpu.resolve_device("cuda")


def test_device_override_includes_all_character_devices(monkeypatch, tmp_path) -> None:
    spec = importlib.util.spec_from_file_location(
        "gpu_device_mappings", Path(__file__).parents[1] / "scripts/gpu_device_mappings.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dev = tmp_path / "dev"
    dev.mkdir()
    for name in ("nvidiactl", "nvidia0", "nvidia4", "nvidia-uvm", "nvidia-uvm-tools"):
        (dev / name).touch()
    (dev / "nvidia-caps").mkdir()
    (dev / "nvidia-caps/nvidia-cap1").touch()
    original_stat = Path.stat

    def device_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path.is_relative_to(dev) and stat.S_ISREG(result.st_mode):
            return types.SimpleNamespace(st_mode=stat.S_IFCHR | 0o666)
        return result

    monkeypatch.setattr(Path, "stat", device_stat)
    mappings = module.device_override(dev)["services"]["youdub-gpu"]["devices"]
    assert len(mappings) == 6
    assert f"{dev / 'nvidia4'}:{dev / 'nvidia4'}:rwm" in mappings
    assert f"{dev / 'nvidia-caps/nvidia-cap1'}:{dev / 'nvidia-caps/nvidia-cap1'}:rwm" in mappings
    (dev / "nvidia-uvm").unlink()
    with pytest.raises(RuntimeError, match="devices are missing"):
        module.device_override(dev)


def test_cleanup_does_not_initialize_idle_cuda(monkeypatch) -> None:
    class IdleCuda:
        def is_initialized(self):
            return False

        def __getattr__(self, name):
            raise AssertionError(f"Idle CUDA must not be touched: {name}")

    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=IdleCuda()))
    gpu.cleanup_gpu_memory("idle-parent")
    assert gpu.cuda_memory_snapshot("idle-parent") is None


def test_isolated_device_probe_preserves_exclusions(monkeypatch) -> None:
    import json
    import subprocess

    def probe(command, **kwargs):
        assert command[:3] == [sys.executable, "-m", "youdub.gpu"]
        assert command[3] == "auto"
        assert json.loads(command[4]) == [0, 2]
        return subprocess.CompletedProcess(command, 0, '{"name":"cuda","index":1}', '')

    monkeypatch.setattr(gpu.subprocess, "run", probe)
    assert gpu.resolve_device_isolated("auto", excluded_indices={2, 0}) == gpu.ResolvedDevice("cuda", 1)


def test_isolated_device_probe_reports_errors(monkeypatch) -> None:
    import subprocess

    monkeypatch.setattr(gpu.subprocess, "run", lambda *args, **kwargs:
        subprocess.CompletedProcess(args, 1, '', 'CUDA device access denied'))
    with pytest.raises(RuntimeError, match="CUDA device access denied"):
        gpu.resolve_device_isolated("cuda")
