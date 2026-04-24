"""Tests for the paths module."""

from __future__ import annotations

from pathlib import Path

import pytest

from kensa.paths import (
    REPORT_DIR,
    RESULT_DIR,
    ROOT,
    RUN_DIR,
    SCENARIO_DIR,
    TRACE_DIR,
    judge_prompt_path,
    latest_capture_manifest,
    latest_manifest,
    latest_report_link,
    manifest_path,
    report_path,
    results_path,
)


class TestConstants:
    def test_root_is_dotkensa(self) -> None:
        assert Path(".kensa") == ROOT

    def test_subdirs_under_root(self) -> None:
        for subdir in (SCENARIO_DIR, TRACE_DIR, RUN_DIR, RESULT_DIR, REPORT_DIR):
            assert subdir.parent == ROOT


class TestPathHelpers:
    def test_manifest_path(self) -> None:
        p = manifest_path("20260327T120000")
        assert p == RUN_DIR / "20260327T120000.json"

    def test_results_path(self) -> None:
        p = results_path("abc123")
        assert p == RESULT_DIR / "abc123.json"

    def test_report_path_default_html(self) -> None:
        p = report_path("abc123")
        assert p == REPORT_DIR / "abc123.html"

    def test_report_path_custom_ext(self) -> None:
        p = report_path("abc123", ext="md")
        assert p == REPORT_DIR / "abc123.md"

    def test_latest_report_link(self) -> None:
        assert latest_report_link() == REPORT_DIR / "latest.html"


class TestJudgePromptPath:
    def test_returns_yaml_path(self) -> None:
        p = judge_prompt_path("accuracy")
        assert p.name == "accuracy.yaml"

    def test_path_traversal_rejected(self) -> None:
        with pytest.raises(ValueError, match="escapes judges directory"):
            judge_prompt_path("../../etc/passwd")


class TestLatestManifest:
    def test_no_runs_dir_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError, match="No runs found"):
            latest_manifest()

    def test_empty_runs_dir_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".kensa" / "runs").mkdir(parents=True)
        with pytest.raises(FileNotFoundError, match="No run manifests"):
            latest_manifest()

    def test_returns_latest_by_sort(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        runs = tmp_path / ".kensa" / "runs"
        runs.mkdir(parents=True)
        (runs / "20260101T000000.json").write_text('{"kind":"eval"}')
        (runs / "20260327T120000.json").write_text('{"kind":"eval"}')
        assert latest_manifest().name == "20260327T120000.json"

    def test_latest_manifest_skips_capture_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        runs = tmp_path / ".kensa" / "runs"
        runs.mkdir(parents=True)
        (runs / "20260327T120000.json").write_text('{"kind":"eval"}')
        (runs / "20260327T120001.json").write_text('{"kind":"capture"}')
        assert latest_manifest().name == "20260327T120000.json"

    def test_latest_capture_manifest_returns_newest_capture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        runs = tmp_path / ".kensa" / "runs"
        runs.mkdir(parents=True)
        (runs / "20260327T120000.json").write_text('{"kind":"eval"}')
        (runs / "20260327T120001.json").write_text('{"kind":"capture"}')
        (runs / "20260327T120002.json").write_text('{"kind":"capture"}')
        assert latest_capture_manifest().name == "20260327T120002.json"
