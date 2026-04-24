"""Capture a real agent invocation and persist its trace."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from kensa.models import RunKind, RunManifest
from kensa.paths import RUN_DIR, TRACE_DIR, manifest_path
from kensa.runner import (
    build_pythonpath,
    load_dotenv,
    read_spans,
    warn_existing_sitecustomize,
    write_sitecustomize,
    write_trace,
)

STDOUT_TAIL_CHARS = 2_000
STDERR_TAIL_CHARS = 500
_TAIL_BUFFER_LINES = 400


def _run_id(timestamp: datetime) -> str:
    return timestamp.strftime("%Y%m%dT%H%M%S%f")[:18]


def _tail(text: str, limit: int) -> str:
    return text[-limit:] if text else ""


def _relay_stream(stream: TextIO, sink: TextIO, chunks: deque[str]) -> None:
    try:
        for chunk in iter(stream.readline, ""):
            if not chunk:
                break
            sink.write(chunk)
            sink.flush()
            chunks.append(chunk)
    finally:
        stream.close()


def capture_command(command: list[str], captured_input: str | None = None) -> RunManifest:
    """Run ``command`` once with tracing enabled and persist a capture manifest.

    ``command`` is passed verbatim (no heuristic splitting). When
    ``captured_input`` is provided it is appended as the final argv element
    for the subprocess run, matching how ``kensa run`` appends ``scenario.input``.
    """
    timestamp = datetime.now(tz=timezone.utc)
    run_id = _run_id(timestamp)
    argv = [*command, captured_input] if captured_input is not None else list(command)

    warn_existing_sitecustomize()

    with tempfile.TemporaryDirectory(prefix="kensa_capture_") as tmp_dir:
        env = os.environ.copy()
        env.update(load_dotenv())
        env["KENSA_TRACE_DIR"] = tmp_dir
        write_sitecustomize(tmp_dir)
        env["PYTHONPATH"] = build_pythonpath(tmp_dir, env)

        stdout_chunks: deque[str] = deque(maxlen=_TAIL_BUFFER_LINES)
        stderr_chunks: deque[str] = deque(maxlen=_TAIL_BUFFER_LINES)
        start = time.monotonic()

        try:
            process = subprocess.Popen(
                argv,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            exit_code = 127
            stdout = ""
            stderr = str(exc)
            duration = time.monotonic() - start
        except OSError as exc:
            exit_code = 126
            stdout = ""
            stderr = str(exc)
            duration = time.monotonic() - start
        else:
            threads: list[threading.Thread] = []
            if process.stdout is not None:
                threads.append(
                    threading.Thread(
                        target=_relay_stream,
                        args=(process.stdout, sys.stdout, stdout_chunks),
                        daemon=True,
                    )
                )
            if process.stderr is not None:
                threads.append(
                    threading.Thread(
                        target=_relay_stream,
                        args=(process.stderr, sys.stderr, stderr_chunks),
                        daemon=True,
                    )
                )
            for thread in threads:
                thread.start()
            exit_code = process.wait()
            for thread in threads:
                thread.join()
            duration = time.monotonic() - start
            stdout = "".join(stdout_chunks)
            stderr = "".join(stderr_chunks)

        spans = read_spans(Path(tmp_dir))

    trace_path: str | None = None
    if spans:
        trace_file = TRACE_DIR / f"{run_id}.jsonl"
        write_trace(spans, trace_file)
        trace_path = str(trace_file)

    manifest = RunManifest(
        run_id=run_id,
        timestamp=timestamp,
        kind=RunKind.CAPTURE,
        command=list(command),
        captured_input=captured_input,
        trace_path=trace_path,
        exit_code=exit_code,
        duration_seconds=round(duration, 2),
        stdout=_tail(stdout, STDOUT_TAIL_CHARS),
        stderr=_tail(stderr, STDERR_TAIL_CHARS),
        span_count=len(spans),
    )

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    manifest_file = manifest_path(run_id)
    manifest_file.write_text(manifest.model_dump_json(indent=2, exclude_none=True))
    return manifest
