"""Tests for the OTel JSONL exporter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kensa.exporter import JSONLSpanExporter, instrument


class TestJSONLSpanExporter:
    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        output = tmp_path / "nested" / "dir" / "spans.jsonl"
        exporter = JSONLSpanExporter(output)
        assert exporter.output_path.parent.exists()

    def test_shutdown_noop(self, tmp_path: Path) -> None:
        exporter = JSONLSpanExporter(tmp_path / "spans.jsonl")
        exporter.shutdown()  # Should not raise

    def test_force_flush_returns_true(self, tmp_path: Path) -> None:
        exporter = JSONLSpanExporter(tmp_path / "spans.jsonl")
        assert exporter.force_flush() is True


class TestInstrument:
    def test_instrument_noop_without_trace_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KENSA_TRACE_DIR", raising=False)
        instrument()  # Should not raise

    def test_instrument_with_explicit_trace_dir(self, tmp_path: Path) -> None:
        instrument(trace_dir=str(tmp_path))  # Should not raise

    def test_instrument_with_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KENSA_TRACE_DIR", str(tmp_path))
        instrument()  # Should not raise

    def test_instrument_warns_no_instrumentors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """instrument() warns when no SDK instrumentors activate."""
        import importlib
        import warnings

        monkeypatch.setenv("KENSA_TRACE_DIR", str(tmp_path))

        real_import = importlib.import_module

        def mock_import(name: str, package: str | None = None) -> object:
            if "openinference.instrumentation" in name:
                raise ImportError(f"mocked: {name}")
            return real_import(name, package)

        monkeypatch.setattr(importlib, "import_module", mock_import)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            instrument()
        warning_messages = [str(x.message) for x in w]
        assert any("no instrumentors" in m.lower() for m in warning_messages)

    def test_export_writes_jsonl(self, tmp_path: Path) -> None:
        """Integration test: export spans via the exporter directly."""
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        output_file = tmp_path / "spans.jsonl"
        exporter = JSONLSpanExporter(output_file)
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test_span"):
            pass

        provider.force_flush()

        assert output_file.exists()
        lines = output_file.read_text().strip().split("\n")
        assert len(lines) >= 1
        span_data = json.loads(lines[0])
        assert span_data["name"] == "test_span"
        assert "trace_id" in span_data
        assert "span_id" in span_data
