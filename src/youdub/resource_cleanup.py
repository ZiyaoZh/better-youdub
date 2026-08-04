from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .ingest import TASK_METADATA_NAME
from .locking import TASK_LOCK_NAME

TASK_RECORD_NAMES = frozenset({TASK_METADATA_NAME, TASK_LOCK_NAME})


@dataclass(frozen=True)
class ResourceUsage:
    bytes: int = 0
    files: int = 0

    def __add__(self, other: "ResourceUsage") -> "ResourceUsage":
        return ResourceUsage(bytes=self.bytes + other.bytes, files=self.files + other.files)

    def to_dict(self) -> dict[str, int]:
        return {"bytes": self.bytes, "files": self.files}


def task_folder_is_managed(folder: Path, root: Path) -> bool:
    try:
        resolved_root = root.resolve()
        resolved_folder = folder.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved_folder != resolved_root and resolved_root in resolved_folder.parents


def task_resource_usage(folder: Path) -> ResourceUsage:
    return directory_resource_usage(folder, preserve=TASK_RECORD_NAMES)


def purge_task_resources(folder: Path) -> ResourceUsage:
    return purge_directory_contents(folder, preserve=TASK_RECORD_NAMES)


def directory_resource_usage(folder: Path, *, preserve: frozenset[str] = frozenset()) -> ResourceUsage:
    if not folder.exists() or not folder.is_dir():
        return ResourceUsage()
    usage = ResourceUsage()
    for child in folder.iterdir():
        if child.name in preserve:
            continue
        usage += path_resource_usage(child)
    return usage


def purge_directory_contents(folder: Path, *, preserve: frozenset[str] = frozenset()) -> ResourceUsage:
    before = directory_resource_usage(folder, preserve=preserve)
    if not folder.exists() or not folder.is_dir():
        return before
    for child in folder.iterdir():
        if child.name in preserve:
            continue
        if child.is_symlink() or not child.is_dir():
            child.unlink(missing_ok=True)
        else:
            shutil.rmtree(child)
    after = directory_resource_usage(folder, preserve=preserve)
    return ResourceUsage(
        bytes=max(0, before.bytes - after.bytes),
        files=max(0, before.files - after.files),
    )


def path_resource_usage(path: Path) -> ResourceUsage:
    try:
        if path.is_symlink() or path.is_file():
            return ResourceUsage(bytes=path.lstat().st_size, files=1)
        if not path.is_dir():
            return ResourceUsage()
    except OSError:
        return ResourceUsage()

    total_bytes = 0
    total_files = 0
    for current, directories, files in os.walk(path, followlinks=False):
        current_path = Path(current)
        for name in files:
            child = current_path / name
            try:
                total_bytes += child.lstat().st_size
                total_files += 1
            except OSError:
                continue
        for name in directories:
            child = current_path / name
            if not child.is_symlink():
                continue
            try:
                total_bytes += child.lstat().st_size
                total_files += 1
            except OSError:
                continue
    return ResourceUsage(bytes=total_bytes, files=total_files)
