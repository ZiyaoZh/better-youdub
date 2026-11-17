"""Task-scoped media payload accounting, independent of host/network counters."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
EVENTS_NAME = "traffic.events.jsonl"
REPORT_NAME = "traffic.json"


def record_payload(task_dir: Path, service: str, direction: str, route: str, size: int) -> None:
    """Append completed/observed payloads; never store proxy credentials or URLs."""
    if size <= 0:
        return
    event = {"id": uuid.uuid4().hex, "at": datetime.now(timezone.utc).isoformat(), "service": service,
             "direction": direction, "route": route, "bytes": size}
    try:
        with (task_dir / EVENTS_NAME).open("a", encoding="utf-8") as file:
            file.write(json.dumps(event) + "\n")
    except OSError:
        # Accounting must not cause a successful upload to be submitted again.
        LOGGER.exception("Could not record task traffic in %s", task_dir)


def inherit_download_events(source_dir: Path, task_dir: Path) -> None:
    """Retain source download accounting when ingest copies into a sub task."""
    source = source_dir / EVENTS_NAME
    target = task_dir / EVENTS_NAME
    if source_dir.resolve() == task_dir.resolve() or not source.exists():
        return
    existing = set(target.read_text(encoding="utf-8").splitlines()) if target.exists() else set()
    for line in source.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("service") == "video-source" and line not in existing:
            with target.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
            existing.add(line)


class DownloadPayloadCounter:
    """Count yt-dlp progress deltas across streams and resumes in one staging dir."""

    def __init__(self) -> None:
        self.positions: dict[str, int] = {}
        self.bytes = 0

    def __call__(self, progress: dict[str, Any]) -> None:
        if progress.get("status") not in {"downloading", "finished"}:
            return
        filename = str(progress.get("filename") or progress.get("tmpfilename") or "unknown")
        current = int(progress.get("downloaded_bytes") or 0)
        previous = self.positions.get(filename, 0)
        # A reset means yt-dlp restarted this stream rather than resuming it.
        self.bytes += current - previous if current >= previous else current
        self.positions[filename] = current


def write_traffic_report(task_dir: Path) -> Path:
    totals: dict[str, dict[str, int]] = {
        route: {"download_bytes": 0, "upload_bytes": 0, "total_bytes": 0}
        for route in ("direct", "proxy", "unknown")
    }
    services: dict[str, dict[str, int]] = defaultdict(lambda: {"download_bytes": 0, "upload_bytes": 0})
    invalid_events = 0
    events_path = task_dir / EVENTS_NAME
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
                size = event["bytes"]
                direction = event["direction"]
                route = event["route"]
                service = event["service"]
                if (not isinstance(size, int) or isinstance(size, bool) or size < 0
                        or direction not in {"download", "upload"} or route not in totals
                        or not isinstance(service, str)):
                    raise ValueError("Invalid traffic event")
            except (ValueError, KeyError, TypeError):
                invalid_events += 1
                continue
            totals[route][direction + "_bytes"] += size
            totals[route]["total_bytes"] += size
            services[service][direction + "_bytes"] += size
    download = sum(row["download_bytes"] for row in totals.values())
    upload = sum(row["upload_bytes"] for row in totals.values())
    notes = [
        "仅统计 yt-dlp 进度回调观察到的媒体下载和 B 站返回成功响应的上传有效载荷；不是网卡或账单流量。",
        "累计包含重试前已记录的有效载荷；不含失败上传请求中无法确认的字节及进度回调之间丢失的下载字节。",
        "不含 TLS/TCP、DNS、元信息、下载封面、翻译 API、模型下载、网页访问和浏览器上传/下载流量。",
        "direct/proxy 表示应用选择的链路，无法识别透明代理、上游分流、计费倍率或收费范围。",
        "未启用记录的历史操作、本地导入和缓存复用不推算为网络下载；未记录不代表没有发生流量。",
        "熟肉任务复制源视频时继承原下载记录；多个任务复用同一来源时，不应直接相加作为服务器总流量。",
        "B 站封面按 Base64 编码长度统计，不含表单转义和其他请求字段。",
    ]
    report = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
              "scope": "observed_media_payload", "download_bytes": download,
              "upload_bytes": upload, "total_bytes": download + upload,
              "by_route": totals, "by_service": dict(services),
              "invalid_events": invalid_events, "notes": notes}
    _atomic_write(task_dir / REPORT_NAME, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = ["任务媒体流量统计（有效载荷）", f"生成时间：{report['generated_at']}"]
    for label, size in [("下载", download), ("上传", upload), ("合计", download + upload)]:
        lines.append(f"{label}：{size:,} bytes（{size / 1_000_000_000:.6f} GB）")
    for route, row in totals.items():
        lines.append(f"{route}：下载 {row['download_bytes']:,} bytes，上传 {row['upload_bytes']:,} bytes")
    lines += [f"无效明细行数：{invalid_events}", "", *notes]
    _atomic_write(task_dir / "traffic.txt", "\n".join(lines) + "\n")
    return task_dir / REPORT_NAME


def _atomic_write(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
