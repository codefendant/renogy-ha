"""Regression tests for importing the production Communication Hub module."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_real_hub_module_imports_in_clean_interpreter() -> None:
    """Import the production Hub module without any test module stubs."""
    repo_root = Path(__file__).resolve().parents[1]
    hub_path = repo_root / "custom_components" / "renogy" / "hub.py"
    script = f"""
import importlib
from pathlib import Path

module = importlib.import_module("custom_components.renogy.hub")
assert Path(module.__file__).resolve() == Path({str(hub_path)!r}).resolve()
assert hasattr(module, "RenogyHubBatteryManager")
assert hasattr(module, "RenogyHubBatteryState")
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
