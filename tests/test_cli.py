from __future__ import annotations

from youdub.cli import build_parser


def test_run_task_parser_accepts_synthesis_and_publish_steps() -> None:
    parser = build_parser()

    synthesize = parser.parse_args(["run-task", "task1", "--step", "synthesize"])
    prepare = parser.parse_args(["run-task", "task1", "--step", "prepare-publish"])
    dry_run = parser.parse_args(["run-task", "task1", "--step", "publish-bilibili", "--publish-dry-run"])

    assert synthesize.step == "synthesize"
    assert prepare.step == "prepare-publish"
    assert dry_run.step == "publish-bilibili"
    assert dry_run.publish_dry_run is True
