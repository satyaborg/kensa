"""Fixture agent for capture tests. Relies on the sitecustomize bootstrap for instrumentation."""

from __future__ import annotations

import json
import sys

from opentelemetry import trace


def main() -> None:
    message = sys.argv[1] if len(sys.argv) > 1 else ""
    tracer = trace.get_tracer("tests.capture_agent")
    with tracer.start_as_current_span(
        "fixture.capture",
        attributes={
            "openinference.span.kind": "LLM",
            "input.value": json.dumps({"messages": [{"role": "user", "content": message}]}),
            "output.value": "captured",
        },
    ):
        print(f"captured: {message}")


if __name__ == "__main__":
    main()
