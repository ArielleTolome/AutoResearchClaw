"""Regression check for the angle-fatigue intel loader.

load_intel_from_baserow() imported a BaserowClient class that baserow_sink
never exported, so every cycle logged "Baserow load failed" and silently fell
back to local files. The loader must now read through baserow_sink's reader
and fall back cleanly when there is nothing scoreable to read.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from angle_fatigue import load_intel_from_baserow  # noqa: E402
from baserow_sink import read_intel_ads  # noqa: E402


def test_read_without_credentials_returns_no_rows():
    """Unconfigured Baserow must return no rows instead of raising."""
    assert read_intel_ads({}, "auto_insurance") == []
    assert read_intel_ads({"baserow": {"enabled": True}}, "auto_insurance") == []


def test_loader_falls_back_without_reporting_a_baserow_failure():
    """With Baserow disabled the loader must not hit the dead import path."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rows = load_intel_from_baserow({}, "auto_insurance")
    out = buf.getvalue()
    assert "Baserow load failed" not in out, out
    assert "cannot import name" not in out, out
    assert isinstance(rows, list)


if __name__ == "__main__":
    test_read_without_credentials_returns_no_rows()
    test_loader_falls_back_without_reporting_a_baserow_failure()
    print("angle-fatigue loader ok")
