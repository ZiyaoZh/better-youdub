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


def test_is_cuda_oom_error_detects_messages_and_nested_causes(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace())
    nested = RuntimeError("model load failed")
    nested.__cause__ = RuntimeError("CUDA error: out of memory")

    assert gpu.is_cuda_oom_error(RuntimeError("CUDA out of memory. Tried to allocate 2 GiB"))
    assert gpu.is_cuda_oom_error(RuntimeError("CUDA failed with error out of memory"))
    assert gpu.is_cuda_oom_error(RuntimeError("CUBLAS_STATUS_ALLOC_FAILED"))
    assert gpu.is_cuda_oom_error(nested)
    assert not gpu.is_cuda_oom_error(MemoryError("host memory exhausted"))
    assert not gpu.is_cuda_oom_error(RuntimeError("CUDA kernel launch failed"))


def test_run_with_cuda_oom_fallback_retries_once_on_cpu(monkeypatch) -> None:
    devices = []
    cleanup_calls = []

    def operation(device: str) -> str:
        devices.append(device)
        if device == "cuda":
            raise RuntimeError("CUDA out of memory")
        return "completed"

    monkeypatch.setattr(gpu, "cleanup_gpu_memory", lambda label: cleanup_calls.append(label))

    result = gpu.run_with_cuda_oom_fallback(
        operation,
        device="cuda",
        label="test-inference",
    )

    assert result == "completed"
    assert devices == ["cuda", "cpu"]
    assert cleanup_calls == ["test-inference:cuda-oom"]


def test_run_with_cuda_oom_fallback_does_not_mask_other_errors() -> None:
    def operation(_device: str) -> str:
        raise RuntimeError("invalid model checkpoint")

    try:
        gpu.run_with_cuda_oom_fallback(operation, device="cuda", label="test-inference")
    except RuntimeError as error:
        assert str(error) == "invalid model checkpoint"
    else:
        raise AssertionError("Expected the original error")
