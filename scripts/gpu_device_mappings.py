"""Print a local Compose override with explicit NVIDIA character devices.

Run on the GPU host, or pipe this file into Python in an existing GPU
container. JSON is also valid YAML. No CUDA/NVML initialization is needed.
"""
from __future__ import annotations

import json
import stat
from pathlib import Path


def device_override(device_root: Path = Path("/dev")) -> dict:
    paths = sorted(device_root.glob("nvidia*"))
    paths += sorted((device_root / "nvidia-caps").glob("*"))
    devices = [path for path in paths if stat.S_ISCHR(path.stat().st_mode)]
    names = {path.name for path in devices}
    if not {"nvidiactl", "nvidia-uvm"} <= names or not any(
        name.removeprefix("nvidia").isdigit() for name in names
    ):
        raise RuntimeError("NVIDIA GPU/control/UVM devices are missing; run on the GPU host or in a GPU container")
    # Docker stores these in HostConfig.Devices and reapplies their permissions
    # on cgroup updates, unlike devices injected only by the NVIDIA legacy hook.
    return {"services": {"youdub-gpu": {"devices": [
        f"{path}:{path}:rwm" for path in devices
    ]}}}


if __name__ == "__main__":
    print(json.dumps(device_override(), indent=2))
