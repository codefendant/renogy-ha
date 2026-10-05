"""Verify that unvalidated warning labels cannot masquerade as confirmed alarms."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "riv_diagnostic_protocol_test",
    Path(__file__).resolve().parents[1]
    / "custom_components/renogy/riv_diagnostic_protocol.py",
)
assert spec and spec.loader
protocol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol)


@pytest.mark.parametrize(
    ("mask", "candidates", "bits"),
    [
        (
            50,
            ["AC input undervoltage", "AC input overvoltage", "Battery overvoltage"],
            [1, 4, 5],
        ),
        (0, [], []),
        (128, ["Unknown warning bits 0x0080"], [7]),
    ],
)
def test_warning_mask_retains_raw_bits_and_unverified_candidates(
    mask, candidates, bits
):
    """Preserve zero, known bits and unknown bits without asserting an alarm."""
    snapshot = protocol.parse_snapshot({4393: mask}, {})
    assert snapshot["riv_warning_mask"] == mask
    assert snapshot["riv_active_warnings"] == f"Unverified (0x{mask:04X})"
    attrs = snapshot["riv_diagnostics"]
    assert attrs["warning_decode_status"] == "unverified_for_model"
    assert attrs["protocol_warning_candidates"] == candidates
    assert attrs["warning_bits_set"] == bits
    assert attrs["raw_registers"]["4393"] == mask


@pytest.mark.parametrize("words", [{}, {4393: 65535}])
def test_missing_or_unsupported_warning_mask_does_not_report_clear(words):
    """A failed or unsupported read cannot become a zero-warning reading."""
    snapshot = protocol.parse_snapshot(words, {"4393": "read failed"})
    assert "riv_active_warnings" not in snapshot
    assert "riv_warning_mask" not in snapshot
    assert "protocol_warning_candidates" not in snapshot["riv_diagnostics"]
