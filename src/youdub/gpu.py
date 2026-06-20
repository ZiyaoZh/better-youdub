from __future__ import annotations

import gc
import logging
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CudaMemorySnapshot:
    label: str
    device: int | None
    allocated: int
    reserved: int
    max_reserved: int

    def as_log_fields(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "device": self.device,
            "allocated_mb": round(self.allocated / 1024 / 1024, 2),
            "reserved_mb": round(self.reserved / 1024 / 1024, 2),
            "max_reserved_mb": round(self.max_reserved / 1024 / 1024, 2),
        }


def cuda_memory_snapshot(label: str = "") -> CudaMemorySnapshot | None:
    torch = _torch_module()
    if torch is None or not _cuda_available(torch):
        return None

    cuda = torch.cuda
    try:
        device = cuda.current_device()
    except Exception:
        device = None

    return CudaMemorySnapshot(
        label=label,
        device=device,
        allocated=_cuda_memory_value(cuda, "memory_allocated", device),
        reserved=_cuda_memory_value(cuda, "memory_reserved", device),
        max_reserved=_cuda_memory_value(cuda, "max_memory_reserved", device),
    )


def cleanup_gpu_memory(label: str = "gpu-cleanup", *, collect_ipc: bool = True) -> None:
    torch = _torch_module()
    if torch is None:
        gc.collect()
        return

    cuda = getattr(torch, "cuda", None)
    if cuda is None or not _cuda_available(torch):
        gc.collect()
        return

    before = cuda_memory_snapshot(f"{label}:before")
    _cuda_call(cuda, "synchronize")
    gc.collect()
    _cuda_call(cuda, "empty_cache")
    if collect_ipc:
        _cuda_call(cuda, "ipc_collect")
    gc.collect()
    after = cuda_memory_snapshot(f"{label}:after")

    if before is not None or after is not None:
        LOGGER.info(
            "CUDA memory cleanup",
            extra={
                "cuda_before": before.as_log_fields() if before else None,
                "cuda_after": after.as_log_fields() if after else None,
            },
        )


def _torch_module() -> Any | None:
    try:
        import torch
    except Exception:
        return None
    return torch


def _cuda_available(torch: Any) -> bool:
    cuda = getattr(torch, "cuda", None)
    if cuda is None or not hasattr(cuda, "is_available"):
        return False
    try:
        return bool(cuda.is_available())
    except Exception:
        return False


def _cuda_memory_value(cuda: Any, name: str, device: int | None) -> int:
    function = getattr(cuda, name, None)
    if function is None:
        return 0
    try:
        return int(function(device))
    except TypeError:
        return int(function())
    except Exception:
        return 0


def _cuda_call(cuda: Any, name: str) -> None:
    function = getattr(cuda, name, None)
    if function is None:
        return
    try:
        function()
    except Exception:
        LOGGER.debug("Ignoring CUDA cleanup failure from %s", name, exc_info=True)
