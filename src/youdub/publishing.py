from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import math
import mimetypes
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .media import CommandError, require_binary

FINAL_VIDEO = "video.mp4"
SUMMARY = "summary.json"
DOWNLOAD_INFO = "download.info.json"
PUBLISH_JSON = "publish.json"
PUBLISH_MARKDOWN = "publish.md"
BILIBILI_JSON = "bilibili.json"
BILIBILI_DRY_RUN_JSON = "bilibili.dry-run.json"
COVER_OUTPUT = "cover.jpg"
COVER_CANDIDATES = ("download.jpg", "download.jpeg", "download.png", "download.webp")
BILIBILI_UPLOAD_RETRIES = 5
BILIBILI_UPLOAD_RETRY_DELAY_SECONDS = 10.0
BILIBILI_ACCEPT_ENCODING = "gzip, deflate"
BILIBILI_HTTP_TOTAL_TIMEOUT_SECONDS = 300.0
BILIBILI_HTTP_CONNECT_TIMEOUT_SECONDS = 30.0
BILIBILI_HTTP_READ_TIMEOUT_SECONDS = 120.0
BILIBILI_WEB_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "Chrome/131.0.0.0 Safari/537.36"
)
BILIBILI_UPLOAD_PROFILE = "ugcupos/bup"
BILIBILI_DEFAULT_PREUPLOAD_QUERY = "zone=cs&upcdn=bldsa&probe_version=20221109"

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublishPackageConfig:
    max_title_chars: int = 80
    max_tags: int = 10
    max_tag_chars: int = 20

    def validate(self) -> None:
        if self.max_title_chars <= 0:
            raise ValueError("Publish title length must be positive")
        if self.max_tags <= 0:
            raise ValueError("Publish tag count must be positive")
        if self.max_tag_chars <= 0:
            raise ValueError("Publish tag length must be positive")


@dataclass(frozen=True)
class BilibiliPublishConfig:
    sessdata: str | None = None
    bili_jct: str | None = None
    tid: int = 201
    original: bool = False
    source: str | None = None
    watermark: bool = True
    proxy: str | None = None
    dry_run: bool = False
    force: bool = False
    confirm: bool = False

    @classmethod
    def from_env(cls) -> "BilibiliPublishConfig":
        return cls(
            sessdata=_clean_text(os.getenv("BILI_SESSDATA")),
            bili_jct=_clean_text(os.getenv("BILI_BILI_JCT")),
            tid=_int_env("BILI_TID", 201),
            original=_bool_env("BILI_ORIGINAL", False),
            source=_clean_text(os.getenv("BILI_SOURCE")),
            watermark=_bool_env("BILI_WATERMARK", True),
            proxy=_clean_text(
                os.getenv("BILI_PROXY")
                or os.getenv("YOUDUB_BILIBILI_PROXY")
                or os.getenv("YOUDUB_TRANSLATION_PROXY")
            ),
            dry_run=_bool_env("YOUDUB_PUBLISH_DRY_RUN", False),
            force=_bool_env("YOUDUB_PUBLISH_FORCE", False),
            confirm=_bool_env("YOUDUB_PUBLISH_CONFIRM", False),
        )

    def validate_for_upload(self) -> None:
        if self.dry_run:
            return
        if not self.confirm:
            raise ValueError("Bilibili upload requires --publish-confirm or --publish-dry-run")
        if not self.sessdata:
            raise ValueError("BILI_SESSDATA is required for Bilibili upload")
        if not self.bili_jct:
            raise ValueError("BILI_BILI_JCT is required for Bilibili upload")
        if self.tid <= 0:
            raise ValueError("Bilibili tid must be positive")


@dataclass(frozen=True)
class _BilibiliPreupload:
    auth: str
    biz_id: int
    chunk_size: int
    endpoint: str
    upos_uri: str


@dataclass(frozen=True)
class _BilibiliUploadMeta:
    upload_id: str


@dataclass(frozen=True)
class _BilibiliUploadedVideo:
    filename_no_suffix: str
    cid: int
    upload_id: str
    upos_uri: str


def prepare_publish_package(task_dir: Path, config: PublishPackageConfig | None = None) -> Path:
    config = config or PublishPackageConfig()
    config.validate()
    task_dir = task_dir.resolve()
    video_path = _require_file(task_dir / FINAL_VIDEO)
    summary = _read_json_object(task_dir / SUMMARY)
    info = _read_json_object(task_dir / DOWNLOAD_INFO)
    cover_path = ensure_cover(task_dir)

    author = _author(summary, info)
    original_title = _clean_text(info.get("title") or info.get("fulltitle")) or "untitled"
    translated_title = _clean_text(summary.get("title")) or original_title
    title = _truncate(_join_title(translated_title, author), config.max_title_chars)
    tags = _publish_tags(summary, author, config)
    source_url = _clean_text(info.get("webpage_url") or info.get("original_url")) or ""
    generated_at = datetime.now(timezone.utc).isoformat()

    package = {
        "schema_version": 1,
        "status": "ready",
        "video_path": video_path.relative_to(task_dir).as_posix(),
        "cover_path": cover_path.relative_to(task_dir).as_posix(),
        "title": title,
        "description": _description(summary, info, author, source_url),
        "tags": tags,
        "source_url": source_url,
        "original_title": original_title,
        "author": author,
        "generated_at": generated_at,
    }
    publish_path = task_dir / PUBLISH_JSON
    _write_json(publish_path, package)
    _write_publish_markdown(task_dir / PUBLISH_MARKDOWN, package)
    return publish_path


def ensure_cover(task_dir: Path) -> Path:
    output = task_dir / COVER_OUTPUT
    if output.exists() and output.stat().st_size > 0:
        return output

    for name in COVER_CANDIDATES:
        candidate = task_dir / name
        if candidate.exists() and candidate.is_file():
            _convert_cover(candidate, output)
            return output

    _extract_video_cover(_require_file(task_dir / FINAL_VIDEO), output)
    return output


def publish_to_bilibili(task_dir: Path, config: BilibiliPublishConfig | None = None) -> Path:
    config = config or BilibiliPublishConfig.from_env()
    config.validate_for_upload()
    task_dir = task_dir.resolve()
    publish_path = task_dir / PUBLISH_JSON
    if not publish_path.exists():
        prepare_publish_package(task_dir)
    package = _read_json_object(publish_path)
    _validate_publish_package(task_dir, package)

    if config.dry_run:
        payload = {
            "schema_version": 1,
            "status": "dry_run",
            "platform": "bilibili",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "title": package["title"],
            "tags": package["tags"],
            "video_path": package["video_path"],
            "cover_path": package["cover_path"],
        }
        return _write_json(task_dir / BILIBILI_DRY_RUN_JSON, payload)

    result_path = task_dir / BILIBILI_JSON
    if result_path.exists() and not config.force:
        existing = _read_json_object(result_path)
        if existing.get("bvid") or existing.get("aid"):
            return result_path

    result = asyncio.run(_upload_bilibili(task_dir, package, config))
    return _write_json(result_path, result)


async def _upload_bilibili(
    task_dir: Path,
    package: dict[str, Any],
    config: BilibiliPublishConfig,
) -> dict[str, Any]:
    source = config.source or package.get("source_url") or None
    video_path = (task_dir / str(package["video_path"])).resolve()
    cover_path = (task_dir / str(package["cover_path"])).resolve()
    last_error: Exception | None = None

    for attempt in range(1, BILIBILI_UPLOAD_RETRIES + 1):
        try:
            async with _BilibiliWebUploader(config) as uploader:
                LOGGER.info("Bilibili upload started")
                cover_url = await uploader.upload_cover(cover_path)
                uploaded, upload_debug = await uploader.upload_video_file(video_path)
                submit_result = await uploader.add_archive(
                    package=package,
                    config=config,
                    source=source,
                    uploaded=uploaded,
                    cover_url=cover_url,
                )
                return _bilibili_upload_result(
                    _bilibili_submit_result(
                        submit_result,
                        cover_url=cover_url,
                        upload_debug=upload_debug,
                    )
                )
        except Exception as exc:
            last_error = exc
            LOGGER.exception("Bilibili upload attempt %s/%s failed", attempt, BILIBILI_UPLOAD_RETRIES)
            if attempt < BILIBILI_UPLOAD_RETRIES:
                await asyncio.sleep(BILIBILI_UPLOAD_RETRY_DELAY_SECONDS)

    raise RuntimeError(f"Bilibili upload failed after {BILIBILI_UPLOAD_RETRIES} attempts") from last_error


def _bilibili_headers_without_brotli(headers: Any) -> dict[str, Any]:
    patched = dict(headers or {})
    for key in list(patched):
        if key.lower() == "accept-encoding":
            del patched[key]
    patched["Accept-Encoding"] = BILIBILI_ACCEPT_ENCODING
    return patched


class _BilibiliWebUploader:
    def __init__(self, config: BilibiliPublishConfig) -> None:
        self.config = config
        self._bili: Any = None
        self._upos: Any = None
        self._probe_query: str | None = None
        self._proxy_uses_connector = False

    async def __aenter__(self) -> "_BilibiliWebUploader":
        try:
            import aiohttp
        except ImportError as exc:
            raise RuntimeError(
                "aiohttp is required for Bilibili Web upload. "
                "Install project runtime dependencies before uploading."
            ) from exc

        timeout = aiohttp.ClientTimeout(
            total=BILIBILI_HTTP_TOTAL_TIMEOUT_SECONDS,
            connect=BILIBILI_HTTP_CONNECT_TIMEOUT_SECONDS,
            sock_connect=BILIBILI_HTTP_CONNECT_TIMEOUT_SECONDS,
            sock_read=BILIBILI_HTTP_READ_TIMEOUT_SECONDS,
        )
        common_headers = _bilibili_headers_without_brotli(
            {
                "User-Agent": BILIBILI_WEB_USER_AGENT,
                "Accept": "application/json, text/plain, */*",
            }
        )
        bili_session_kwargs: dict[str, Any] = {
            "timeout": timeout,
            "headers": {
                **common_headers,
                "Cookie": _bilibili_cookie(self.config),
                "Origin": "https://member.bilibili.com",
                "Referer": "https://member.bilibili.com/",
            },
            "trust_env": True,
        }
        upos_session_kwargs: dict[str, Any] = {
            "timeout": timeout,
            "headers": common_headers,
            "trust_env": True,
        }
        connector_spec = _bilibili_proxy_connector_spec(_bilibili_proxy(self.config))
        if connector_spec is not None:
            try:
                from aiohttp_socks import ProxyConnector
            except ImportError as exc:
                raise RuntimeError(
                    "aiohttp-socks is required for Bilibili SOCKS proxy upload. "
                    "Install project runtime dependencies before uploading."
                ) from exc
            connector_url, rdns = connector_spec
            bili_session_kwargs["connector"] = ProxyConnector.from_url(connector_url, rdns=rdns)
            upos_session_kwargs["connector"] = ProxyConnector.from_url(connector_url, rdns=rdns)
            self._proxy_uses_connector = True

        self._bili = aiohttp.ClientSession(**bili_session_kwargs)
        self._upos = aiohttp.ClientSession(**upos_session_kwargs)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._bili is not None:
            await self._bili.close()
        if self._upos is not None:
            await self._upos.close()

    async def upload_cover(self, image_path: Path) -> str:
        _require_file(image_path)
        mime, _ = mimetypes.guess_type(str(image_path))
        if not mime or not mime.startswith("image/"):
            mime = "image/jpeg"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = await self._request_json(
            self._bili,
            "POST",
            "https://member.bilibili.com/x/vu/web/cover/up",
            params={"ts": _milliseconds()},
            data={
                "csrf": self._csrf,
                "cover": f"data:{mime};base64,{encoded}",
            },
        )
        _bilibili_code_ok(payload)
        url = _as_dict(payload.get("data")).get("url")
        url = str(url or "").strip()
        if not url:
            raise RuntimeError("Bilibili cover upload returned empty url")
        return _normalize_bilibili_url(url)

    async def upload_video_file(self, video_path: Path) -> tuple[_BilibiliUploadedVideo, dict[str, Any]]:
        _require_file(video_path)
        file_size = video_path.stat().st_size
        if file_size <= 0:
            raise ValueError("Bilibili upload video file is empty")

        preupload = await self._preupload_video(video_path.name, file_size)
        upload_meta = await self._post_video_meta(preupload, file_size)
        upload_url = _bilibili_upload_url(preupload)
        chunk_size = int(preupload.chunk_size)
        chunk_count = int(math.ceil(file_size / float(chunk_size)))
        parts: list[dict[str, Any]] = []

        with video_path.open("rb") as file:
            for chunk_index in range(chunk_count):
                start = chunk_index * chunk_size
                chunk = file.read(chunk_size)
                if not chunk:
                    break
                end = start + len(chunk)
                put_kwargs: dict[str, Any] = {
                    "params": {
                        "partNumber": str(chunk_index + 1),
                        "uploadId": upload_meta.upload_id,
                        "chunk": str(chunk_index),
                        "chunks": str(chunk_count),
                        "size": str(len(chunk)),
                        "start": str(start),
                        "end": str(end),
                        "total": str(file_size),
                    },
                    "headers": {
                        "X-Upos-Auth": preupload.auth,
                        "Content-Type": "application/octet-stream",
                    },
                    "data": chunk,
                }
                proxy = _bilibili_proxy(self.config)
                if proxy is not None and not self._proxy_uses_connector:
                    put_kwargs["proxy"] = proxy
                try:
                    async with self._upos.put(upload_url, **put_kwargs) as response:
                        text = await response.text()
                        if response.status != 200:
                            raise RuntimeError(
                                f"Bilibili chunk upload failed (status={response.status} body={text[:200]})"
                            )
                        etag = (
                            response.headers.get("ETag")
                            or response.headers.get("Etag")
                            or response.headers.get("etag")
                            or ""
                        ).strip().strip('"')
                except TimeoutError as exc:
                    raise RuntimeError(
                        f"Bilibili chunk upload timed out at chunk {chunk_index + 1}/{chunk_count}. "
                        "If this container cannot reach Bilibili UPOS directly, set BILI_PROXY or HTTPS_PROXY."
                    ) from exc
                if not etag:
                    etag = hashlib.md5(chunk).hexdigest()  # noqa: S324 - UPOS multipart ETag fallback.
                parts.append({"partNumber": chunk_index + 1, "eTag": etag})
                LOGGER.info("Bilibili upload chunk %s/%s completed", chunk_index + 1, chunk_count)

        end_upload = await self._request_json(
            self._upos,
            "POST",
            upload_url,
            params={
                "output": "json",
                "name": video_path.name,
                "profile": BILIBILI_UPLOAD_PROFILE,
                "uploadId": upload_meta.upload_id,
                "biz_id": str(preupload.biz_id),
            },
            headers={"X-Upos-Auth": preupload.auth},
            json={"parts": parts},
        )
        if end_upload.get("OK") != 1:
            raise RuntimeError(f"Bilibili end upload failed: {_safe_bilibili_json(end_upload)}")

        filename_no_suffix = _filename_no_suffix_from_upos_uri(preupload.upos_uri)
        if not filename_no_suffix:
            raise RuntimeError("Bilibili upload could not derive filename from upos_uri")
        uploaded = _BilibiliUploadedVideo(
            filename_no_suffix=filename_no_suffix,
            cid=preupload.biz_id,
            upload_id=upload_meta.upload_id,
            upos_uri=preupload.upos_uri,
        )
        return uploaded, {
            "biz_id": preupload.biz_id,
            "chunk_size": preupload.chunk_size,
            "endpoint": preupload.endpoint,
            "upos_uri": preupload.upos_uri,
            "upload_id": upload_meta.upload_id,
            "chunks": len(parts),
        }

    async def add_archive(
        self,
        *,
        package: dict[str, Any],
        config: BilibiliPublishConfig,
        source: Any,
        uploaded: _BilibiliUploadedVideo,
        cover_url: str,
    ) -> dict[str, Any]:
        tags = [str(tag).strip() for tag in list(package["tags"])[:10] if str(tag).strip()]
        if not tags:
            raise ValueError("Bilibili upload requires at least one tag")
        body = {
            "videos": [
                {
                    "filename": uploaded.filename_no_suffix,
                    "title": str(package["title"]),
                    "desc": "",
                    "cid": uploaded.cid,
                }
            ],
            "cover": cover_url or None,
            "cover43": "",
            "title": str(package["title"]),
            "copyright": 1 if config.original else 2,
            "source": None if config.original else str(source or ""),
            "tid": int(config.tid),
            "tag": ",".join(tags),
            "desc_format_id": 9999,
            "desc": str(package["description"]),
            "recreate": -1,
            "dynamic": str(package["title"]),
            "interactive": 0,
            "act_reserve_create": 0,
            "no_disturbance": 0,
            "no_reprint": 0,
            "subtitle": {"open": 0, "lan": ""},
            "dolby": 0,
            "lossless_music": 0,
            "up_selection_reply": False,
            "up_close_reply": False,
            "up_close_danmu": False,
            "web_os": 1,
            "watermark": {"state": 1 if config.watermark else 0},
            "csrf": self._csrf,
        }
        payload = await self._request_json(
            self._bili,
            "POST",
            "https://member.bilibili.com/x/vu/web/add/v3",
            params={"csrf": self._csrf, "ts": _milliseconds()},
            json=_drop_none(body),
        )
        _bilibili_code_ok(payload)
        return payload

    @property
    def _csrf(self) -> str:
        return str(self.config.bili_jct or "").strip()

    async def _preupload_video(self, filename: str, file_size: int) -> _BilibiliPreupload:
        probe_query = await self._preupload_probe_query()
        url = "https://member.bilibili.com/preupload"
        if probe_query:
            url = f"{url}?{probe_query}"
        payload = await self._request_json(
            self._bili,
            "GET",
            url,
            params={
                "name": filename,
                "r": "upos",
                "profile": BILIBILI_UPLOAD_PROFILE,
                "ssl": "0",
                "version": "2.14.0",
                "build": "2140000",
                "size": str(file_size),
            },
        )
        if payload.get("OK") != 1:
            raise RuntimeError(f"Bilibili preupload failed: {_safe_bilibili_json(payload)}")
        return _parse_bilibili_preupload(payload)

    async def _post_video_meta(self, preupload: _BilibiliPreupload, file_size: int) -> _BilibiliUploadMeta:
        payload = await self._request_json(
            self._upos,
            "POST",
            _bilibili_upload_url(preupload),
            params={
                "uploads": "",
                "output": "json",
                "profile": BILIBILI_UPLOAD_PROFILE,
                "filesize": str(file_size),
                "partsize": str(preupload.chunk_size),
                "biz_id": str(preupload.biz_id),
            },
            headers={"X-Upos-Auth": preupload.auth},
        )
        if payload.get("OK") != 1:
            raise RuntimeError(f"Bilibili post video meta failed: {_safe_bilibili_json(payload)}")
        upload_id = str(payload.get("upload_id") or "").strip()
        if not upload_id:
            raise RuntimeError("Bilibili post video meta returned empty upload_id")
        return _BilibiliUploadMeta(upload_id=upload_id)

    async def _preupload_probe_query(self) -> str:
        if self._probe_query is not None:
            return self._probe_query
        try:
            payload = await self._request_json(
                self._bili,
                "GET",
                "https://member.bilibili.com/preupload",
                params={"r": "probe"},
            )
            self._probe_query = _bilibili_probe_query(payload) or BILIBILI_DEFAULT_PREUPLOAD_QUERY
        except Exception:
            self._probe_query = BILIBILI_DEFAULT_PREUPLOAD_QUERY
        return self._probe_query

    async def _request_json(self, session: Any, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        proxy = _bilibili_proxy(self.config)
        if proxy is not None and not self._proxy_uses_connector and "proxy" not in kwargs:
            kwargs["proxy"] = proxy
        try:
            context = session.request(method, url, **kwargs)
            async with context as response:
                text = await response.text()
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"Bilibili HTTP response is not JSON (status={response.status} body={text[:200]})"
                    ) from exc
                if not isinstance(payload, dict):
                    raise RuntimeError(f"Bilibili HTTP response is not a JSON object: {type(payload).__name__}")
                if response.status >= 400:
                    raise RuntimeError(
                        f"Bilibili HTTP error (status={response.status} body={_safe_bilibili_json(payload)})"
                    )
                return payload
        except TimeoutError as exc:
            proxy_hint = "with configured proxy" if proxy else "without configured proxy"
            raise RuntimeError(
                f"Bilibili HTTP request timed out {proxy_hint}: {method} {url}. "
                "If this container cannot reach member.bilibili.com directly, set BILI_PROXY or HTTPS_PROXY."
            ) from exc


def _bilibili_cookie(config: BilibiliPublishConfig) -> str:
    return f"SESSDATA={config.sessdata}; bili_jct={config.bili_jct}"


def _bilibili_proxy(config: BilibiliPublishConfig) -> str | None:
    return _clean_text(
        config.proxy
        or os.getenv("BILI_PROXY")
        or os.getenv("YOUDUB_BILIBILI_PROXY")
        or os.getenv("YOUDUB_TRANSLATION_PROXY")
    )


def _bilibili_proxy_connector_spec(proxy: str | None) -> tuple[str, bool] | None:
    value = _clean_text(proxy)
    if value is None:
        return None
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in {"socks4", "socks4a", "socks5", "socks5h"}:
        return None
    rdns = scheme in {"socks4a", "socks5h"}
    normalized_scheme = {"socks4a": "socks4", "socks5h": "socks5"}.get(scheme, scheme)
    normalized = urlunsplit(
        (
            normalized_scheme,
            parsed.netloc,
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )
    return normalized, rdns


def _milliseconds() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _drop_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _normalize_bilibili_url(value: str) -> str:
    if value.startswith("//"):
        return f"https:{value}"
    return value


def _bilibili_probe_query(payload: dict[str, Any]) -> str:
    lines = payload.get("lines")
    if payload.get("OK") != 1 or not isinstance(lines, list):
        return ""
    candidates = [line for line in lines if isinstance(line, dict)]
    for line in candidates:
        if str(line.get("os") or "") == "upos":
            query = str(line.get("query") or "").strip()
            if query:
                return query
    for line in candidates:
        query = str(line.get("query") or "").strip()
        if query:
            return query
    return ""


def _parse_bilibili_preupload(payload: dict[str, Any]) -> _BilibiliPreupload:
    try:
        biz_id = int(payload.get("biz_id") or 0)
        chunk_size = int(payload.get("chunk_size") or 0)
    except Exception as exc:
        raise RuntimeError("Bilibili preupload returned invalid numeric fields") from exc
    preupload = _BilibiliPreupload(
        auth=str(payload.get("auth") or "").strip(),
        biz_id=biz_id,
        chunk_size=chunk_size,
        endpoint=str(payload.get("endpoint") or "").strip(),
        upos_uri=str(payload.get("upos_uri") or "").strip(),
    )
    if not preupload.auth:
        raise RuntimeError("Bilibili preupload returned empty auth")
    if preupload.biz_id <= 0:
        raise RuntimeError("Bilibili preupload returned invalid biz_id")
    if preupload.chunk_size <= 0:
        raise RuntimeError("Bilibili preupload returned invalid chunk_size")
    if not preupload.endpoint:
        raise RuntimeError("Bilibili preupload returned empty endpoint")
    if not preupload.upos_uri:
        raise RuntimeError("Bilibili preupload returned empty upos_uri")
    return preupload


def _bilibili_upload_url(preupload: _BilibiliPreupload) -> str:
    endpoint = preupload.endpoint.strip()
    upos_uri = preupload.upos_uri.strip()
    if not endpoint.startswith("//"):
        raise RuntimeError(f"Bilibili preupload endpoint is invalid: {endpoint}")
    if not upos_uri.startswith("upos://"):
        raise RuntimeError(f"Bilibili preupload upos_uri is invalid: {upos_uri}")
    return f"https:{endpoint}/{upos_uri.removeprefix('upos://')}"


def _filename_no_suffix_from_upos_uri(upos_uri: str) -> str:
    filename = Path(upos_uri.replace("upos://", "", 1)).name
    if "." not in filename:
        return filename
    return filename.rsplit(".", 1)[0]


def _safe_bilibili_json(payload: dict[str, Any]) -> dict[str, Any]:
    safe = dict(payload)
    for key in ("auth", "fetch_headers", "post_auth", "put_auth"):
        safe.pop(key, None)
    return safe


def _bilibili_code_ok(payload: dict[str, Any]) -> None:
    if payload.get("code") == 0:
        return
    raise RuntimeError(f"Bilibili API error: {_safe_bilibili_json(payload)}")


def _bilibili_submit_result(
    result: dict[str, Any],
    *,
    cover_url: str,
    upload_debug: dict[str, Any],
) -> dict[str, Any]:
    data = _as_dict(result.get("data"))
    aid = data.get("aid")
    bvid = data.get("bvid")
    if aid is None and not bvid:
        raise RuntimeError(f"Bilibili submit succeeded but returned no aid/bvid: {_safe_bilibili_json(result)}")
    return {
        "aid": aid,
        "bvid": bvid,
        "cover_url": cover_url or None,
        "video": upload_debug,
        "result": data,
    }


def _bilibili_upload_result(result: Any) -> dict[str, Any]:
    payload = dict(result) if isinstance(result, dict) else {"result": result}
    payload.setdefault("schema_version", 1)
    payload["status"] = "uploaded"
    payload["platform"] = "bilibili"
    payload["uploaded_at"] = datetime.now(timezone.utc).isoformat()
    return payload


def _validate_publish_package(task_dir: Path, package: dict[str, Any]) -> None:
    for key in ("title", "description", "tags", "video_path", "cover_path"):
        if key not in package:
            raise ValueError(f"publish.json is missing required field: {key}")
    if not isinstance(package["tags"], list) or not package["tags"]:
        raise ValueError("publish.json must contain non-empty tags")
    _require_file(task_dir / str(package["video_path"]))
    _require_file(task_dir / str(package["cover_path"]))


def _description(summary: dict[str, Any], info: dict[str, Any], author: str, source_url: str) -> str:
    original_title = _clean_text(info.get("title") or info.get("fulltitle")) or "未知"
    publish_time = _format_upload_date(_clean_text(info.get("upload_date")) or "")
    summary_text = _clean_text(summary.get("summary")) or ""
    generated_date = datetime.now(timezone.utc).date().isoformat()
    parts = [
        f"原标题：{original_title}",
        f"作者：{author}",
    ]
    if source_url:
        parts.append(f"链接：{source_url}")
    parts.extend(
        [
            f"视频发布日期：{publish_time or '未知'}",
            "",
            summary_text,
            "",
            f"上传日期：{generated_date}",
        ]
    )
    return "\n".join(parts).strip()


def _publish_tags(summary: dict[str, Any], author: str, config: PublishPackageConfig) -> list[str]:
    values = [author, "AI", *_string_list(summary.get("tags")), "中文配音"]
    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = _truncate(_clean_text(value) or "", config.max_tag_chars)
        if not tag or tag in seen:
            continue
        tags.append(tag)
        seen.add(tag)
        if len(tags) >= config.max_tags:
            break
    return tags


def _write_publish_markdown(path: Path, package: dict[str, Any]) -> Path:
    content = "\n".join(
        [
            f"# {package['title']}",
            "",
            "## Description",
            "",
            str(package["description"]),
            "",
            "## Tags",
            "",
            ", ".join(str(item) for item in package["tags"]),
            "",
        ]
    )
    path.write_text(content, encoding="utf-8")
    return path


def _convert_cover(source: Path, output: Path) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source.resolve()),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output.resolve()),
    ]
    _run_command(command)


def _extract_video_cover(video_path: Path, output: Path) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path.resolve()),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output.resolve()),
    ]
    _run_command(command)


def _author(summary: dict[str, Any], info: dict[str, Any]) -> str:
    for value in (summary.get("author"), info.get("uploader"), info.get("channel"), info.get("author")):
        text = _clean_text(value)
        if text:
            return text
    return "Unknown"


def _join_title(title: str, author: str) -> str:
    if not author or author == "Unknown" or author in title:
        return title
    return f"{title} - {author}"


def _format_upload_date(value: str) -> str:
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _truncate(value: str, max_chars: int) -> str:
    value = value.strip()
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 1)].rstrip() + "…"


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value not in {"0", "false", "False", "no", "NO"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    return int(value)


def _read_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _require_file(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise ValueError(f"Expected file: {path}")
    return path


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    require_binary(command[0])
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CommandError(command, result.returncode, result.stderr)
    return result
