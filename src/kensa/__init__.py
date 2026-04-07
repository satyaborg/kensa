"""kensa — the open source agent evals harness."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("kensa")
except PackageNotFoundError:
    __version__ = "0.0.0"

from kensa.exporter import instrument

__all__ = ["instrument"]
