from __future__ import annotations

import sys
from typing import Any


def echo(*args: Any, sep: str = " ", end: str = "\n", flush: bool = True) -> None:
    """Print to standard output with flush by default."""
    print(*args, sep=sep, end=end, flush=flush)


def warning(*args: Any, sep: str = " ", end: str = "\n", flush: bool = True) -> None:
    """Print warning message to stderr without terminating execution."""
    print(*args, sep=sep, end=end, file=sys.stderr, flush=flush)


def error(
    *args: Any, sep: str = " ", end: str = "\n", code: int = 1, flush: bool = True
) -> None:
    """Print to standard error and exit with specified code."""
    print(*args, sep=sep, end=end, file=sys.stderr, flush=flush)
    sys.exit(code)
