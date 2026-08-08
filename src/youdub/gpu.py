from __future__ import annotations

import gc
import logging
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")
_CUDA_OOM_MARKERS = (
    "cuda out of memory",
    "cuda error: out of memory",
    "cuda failed with error out of memory",
    "cublas_status_alloc_failed",
    "cudnn_status_alloc_failed",
)


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


def is_cuda_oom_error(error: BaseException) -> bool:
    """Return whether an exception or one of its causes reports CUDA OOM."""
    torch = _torch_module()
    oom_types: tuple[type[BaseException], ...] = ()
    if torch is not None:
        candidates = [
            getattr(getattr(torch, "cuda", None), "OutOfMemoryError", None),
        ]
        oom_types = tuple(
            candidate
            for candidate in candidates
            if isinstance(candidate, type) and issubclass(candidate, BaseException)
        )

    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if oom_types and isinstance(current, oom_types):
            return True
        message = str(current).lower()
        if any(marker in message for marker in _CUDA_OOM_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def run_with_cuda_oom_fallback(
    operation: Callable[[str], _T],
    *,
    device: str,
    label: str,
) -> _T:
    """Run on the requested device, retrying once on CPU after CUDA OOM."""
    try:
        return operation(device)
    except Exception as error:
        if not _can_fallback_to_cpu(device) or not is_cuda_oom_error(error):
            raise
        LOGGER.warning("%s hit CUDA OOM; retrying inference on CPU", label)
        _clear_exception_tracebacks(error)
        cleanup_gpu_memory(f"{label}:cuda-oom")
        return operation("cpu")


def _can_fallback_to_cpu(device: str) -> bool:
    normalized = str(device).strip().lower()
    return normalized == "auto" or normalized == "cuda" or normalized.startswith("cuda:")


def _clear_exception_tracebacks(error: BaseException) -> None:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        next_error = current.__cause__ or current.__context__
        current.__traceback__ = None
        current = next_error


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
