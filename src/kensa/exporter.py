"""JSONL span exporter for OTel + instrument() convenience function.

Agents call instrument() to configure OpenTelemetry to write spans
as JSONL to $KENSA_TRACE_DIR and auto-instrument detected SDKs.
"""

from __future__ import annotations

import json
import os
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult


class JSONLSpanExporter(SpanExporter):
    """Exports OTel spans as JSONL to a file."""

    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with open(self.output_path, "a") as f:
            for span in spans:
                span_dict: dict[str, Any] = self._span_to_dict(span)
                f.write(json.dumps(span_dict) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    @staticmethod
    def _span_to_dict(span: ReadableSpan) -> dict[str, Any]:
        """Convert a ReadableSpan to a JSON-serializable dict."""
        ctx = span.get_span_context()
        parent = span.parent

        result: dict[str, Any] = {
            "name": span.name,
            "trace_id": format(ctx.trace_id, "032x"),
            "span_id": format(ctx.span_id, "016x"),
            "parent_span_id": format(parent.span_id, "016x") if parent else None,
            "start_time": span.start_time,
            "end_time": span.end_time,
            "status": {
                "status_code": span.status.status_code.name,
            },
            "attributes": dict(span.attributes) if span.attributes else {},
        }
        return result


def _setup_tracing(trace_dir: str) -> TracerProvider:
    """Configure OTel to export spans as JSONL to trace_dir."""
    output_path = Path(trace_dir) / "spans.jsonl"
    exporter = JSONLSpanExporter(output_path)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return provider


def _try_instrumentor(module_path: str, class_name: str) -> str | None:
    """Try to activate an OTel instrumentor. Returns label on success, None on ImportError."""
    import importlib

    label = module_path.rsplit(".", 1)[-1]
    try:
        mod = importlib.import_module(module_path)
        getattr(mod, class_name)().instrument()
        return label
    except ImportError:
        return None
    except Exception as e:
        warnings.warn(f"{label} instrumentor failed: {e}", stacklevel=2)
        return None


_instrumented = False


def instrument(trace_dir: str | None = None) -> None:
    """One-call instrumentation: set up OTel tracing and instrument detected SDKs.

    No-ops gracefully when KENSA_TRACE_DIR is not set, so agents can run
    both inside and outside the eval harness without code changes.

    Idempotent: repeated calls within the same process are no-ops. This lets
    the runner's sitecustomize injection call ``instrument()`` automatically
    while still supporting agents that keep the explicit call.
    """
    global _instrumented
    if _instrumented:
        return
    resolved = trace_dir or os.environ.get("KENSA_TRACE_DIR")
    if not resolved:
        return
    _setup_tracing(resolved)

    activated = [
        label
        for label in (
            _try_instrumentor("openinference.instrumentation.anthropic", "AnthropicInstrumentor"),
            _try_instrumentor("openinference.instrumentation.openai", "OpenAIInstrumentor"),
            _try_instrumentor("openinference.instrumentation.langchain", "LangChainInstrumentor"),
        )
        if label is not None
    ]

    if not activated:
        warnings.warn(
            "kensa: instrument() found no instrumentors. Traces will have no LLM spans. "
            "Install one: uv add kensa[anthropic] (or: pip install kensa[anthropic])",
            stacklevel=2,
        )

    _instrumented = True
