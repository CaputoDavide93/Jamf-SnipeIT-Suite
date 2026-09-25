"""
Warn when the local interpreter differs from the one production pins.

On 2026-08-03 a missing `from typing import Optional` passed a full local run
on Python 3.14 and then crashed every container. Python 3.14 defers annotation
evaluation (PEP 649); the pinned 3.12 runtime evaluates eagerly and raised
NameError at import. A green local suite is not evidence the image will boot.

The pinned version is read from the Dockerfile so this cannot drift.
"""
import re
import sys
from pathlib import Path

import pytest

_DOCKERFILE = Path(__file__).parent.parent / "Dockerfile"


def _pinned_version() -> str | None:
    try:
        text = _DOCKERFILE.read_text(encoding="utf-8")
    except OSError:
        return None
    # e.g. FROM python:3.12-slim AS builder
    match = re.search(r"^FROM\s+python:(\d+\.\d+)", text, re.MULTILINE)
    return match.group(1) if match else None


def pytest_report_header(config) -> str:
    pinned = _pinned_version()
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    if pinned and pinned != running:
        return (
            f"WARNING: running Python {running} but the image pins {pinned}. "
            f"Annotation and stdlib differences can hide failures that only "
            f"appear in the container — gate deploys with scripts/verify-image.sh."
        )
    return f"python parity: {running} matches the image pin"


@pytest.fixture(scope="session", autouse=True)
def _warn_on_interpreter_drift():
    pinned = _pinned_version()
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    if pinned and pinned != running:
        import warnings
        warnings.warn(
            f"Local Python {running} != image pin {pinned}; "
            f"run scripts/verify-image.sh before deploying.",
            RuntimeWarning,
            stacklevel=1,
        )
    yield
