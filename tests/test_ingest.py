import json
from pathlib import Path

from youdub.ingest import (
    TASK_METADATA_NAME,
    create_pending_url_task,
    create_task_from_download_artifacts,
    create_task_from_local_media,
    slugify,
    stable_task_id,
    task_folder_from_download_info,
)
from youdub.models import PipelineStep, StepStatus


def test_slugify_keeps_readable_text() -> None:
    assert slugify(" A/B: demo  视频 ") == "AB demo 视频"


def test_create_task_from_local_media(tmp_path: Path) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"not a real video")

    task = create_task_from_local_media(source, tmp_path / "videos", "sample title")

    assert task.folder.exists()
    assert (task.folder / "download.mp4").read_bytes() == b"not a real video"
    assert task.steps[PipelineStep.INGEST.value] == StepStatus.SUCCESS


def test_create_pending_url_task_creates_configurable_placeholder(tmp_path: Path) -> None:
    task = create_pending_url_task("https://example.test/watch?v=demo123", tmp_path / "videos")

    assert task.source == "https://example.test/watch?v=demo123"
    assert task.title == "URL draft"
    assert task.folder.parent == tmp_path / "videos" / "_pending"
    assert task.steps == {}
    assert task.source_key is None


def test_create_task_from_download_artifacts_uses_stable_folder(tmp_path: Path) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"not a real video")
    info = {
        "extractor": "youtube",
        "id": "demo123",
        "title": "Sample / Title",
        "uploader": "Sample Author",
        "upload_date": "20240102",
        "webpage_url": "https://example.test/watch?v=demo123",
    }
    info_path = tmp_path / "download.info.json"
    info_path.write_text(json.dumps(info), encoding="utf-8")
    cover_path = tmp_path / "download.webp"
    cover_path.write_bytes(b"cover")

    task = create_task_from_download_artifacts(
        source=source,
        info_path=info_path,
        root=tmp_path / "videos",
        cover_path=cover_path,
    )

    expected_folder = tmp_path / "videos" / "Sample Author" / "20240102 Sample Title"
    assert task.id == stable_task_id("youtube:demo123")
    assert task.source_key == "youtube:demo123"
    assert task.author == "Sample Author"
    assert task.folder == expected_folder
    assert (expected_folder / "download.mp4").read_bytes() == b"not a real video"
    assert json.loads((expected_folder / "download.info.json").read_text(encoding="utf-8"))["id"] == "demo123"
    assert (expected_folder / "download.webp").read_bytes() == b"cover"

    second = create_task_from_download_artifacts(
        source=source,
        info_path=info_path,
        root=tmp_path / "videos",
        cover_path=cover_path,
    )
    assert second.folder == task.folder
    assert second.id == task.id


def test_create_task_from_download_artifacts_accepts_files_already_in_task_folder(tmp_path: Path) -> None:
    info = {
        "extractor": "youtube",
        "id": "demo123",
        "title": "Sample Title",
        "uploader": "Sample Author",
        "upload_date": "20240102",
        "webpage_url": "https://example.test/watch?v=demo123",
    }
    task_dir = tmp_path / "videos" / "Sample Author" / "20240102 Sample Title"
    task_dir.mkdir(parents=True)
    source = task_dir / "download.mp4"
    source.write_bytes(b"video")
    info_path = task_dir / "download.info.json"
    info_path.write_text(json.dumps(info), encoding="utf-8")
    cover_path = task_dir / "download.webp"
    cover_path.write_bytes(b"cover")

    task = create_task_from_download_artifacts(
        source=source,
        info_path=info_path,
        root=tmp_path / "videos",
        cover_path=cover_path,
    )

    assert task.folder == task_dir
    assert source.read_bytes() == b"video"
    assert cover_path.read_bytes() == b"cover"


def test_task_folder_from_download_info_avoids_foreign_collision(tmp_path: Path) -> None:
    info = {
        "extractor": "youtube",
        "id": "demo123",
        "title": "Sample Title",
        "uploader": "Sample Author",
        "upload_date": "20240102",
    }
    existing = tmp_path / "videos" / "Sample Author" / "20240102 Sample Title"
    existing.mkdir(parents=True)
    (existing / TASK_METADATA_NAME).write_text(
        json.dumps({"source_key": "youtube:other"}),
        encoding="utf-8",
    )

    folder = task_folder_from_download_info(info, tmp_path / "videos")

    assert folder.name == "20240102 Sample Title__demo123"


def test_same_video_task_types_have_independent_identity_and_outputs(tmp_path):
    import json
    from youdub.models import TaskType, Task
    from youdub.ingest import create_task_from_download_artifacts
    from youdub.storage import TaskStore
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    info = tmp_path / "source.info.json"
    info.write_text(json.dumps({"id": "same-video", "extractor_key": "YouTube", "title": "Same video", "uploader": "Demo"}))
    root = tmp_path / "videos"
    dub = create_task_from_download_artifacts(source, info, root)
    sub = create_task_from_download_artifacts(source, info, root, task_type=TaskType.SUB)
    assert dub.id != sub.id and dub.folder != sub.folder
    assert dub.source_key != sub.source_key
    (dub.folder / "video.mp4").write_bytes(b"dubbed-output")
    assert not (sub.folder / "video.mp4").exists()
    store = TaskStore(tmp_path / "tasks.json")
    store.upsert(dub)
    store.upsert(sub)
    assert store.get(sub.id).task_type == TaskType.SUB
    assert store.upsert(sub).task_type == TaskType.SUB
    legacy = dub.to_dict()
    legacy.pop("task_type")
    assert Task.from_dict(legacy).task_type == TaskType.DUB
