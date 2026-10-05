"""Compatibility diagnostics using the real library in an isolated process."""

import subprocess
import sys
from pathlib import Path


def test_read_only_reader_isolates_timeouts_and_invalid_frames() -> None:
    """Keep the test independent of other modules' Home Assistant import stubs."""
    script = r"""
import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

root = Path.cwd()
pkg = types.ModuleType("custom_components")
pkg.__path__ = [str(root / "custom_components")]
sys.modules[pkg.__name__] = pkg
pkg = types.ModuleType("custom_components.renogy")
pkg.__path__ = [str(root / "custom_components/renogy")]
sys.modules[pkg.__name__] = pkg
const = types.ModuleType("custom_components.renogy.const")
const.RIV4835CSH1S_INVERTER_PROFILE = "RIV4835CSH1S"
sys.modules[const.__name__] = const
reader = importlib.import_module("custom_components.renogy.riv_diagnostic_reader")
protocol = importlib.import_module("custom_components.renogy.riv_diagnostic_protocol")
from renogy_ble.ble import RenogyBleClient, modbus_crc

for method in ("_prepare_session", "_ensure_session_ready",
               "_read_modbus_register", "_close_session"):
    assert callable(getattr(RenogyBleClient, method))

def frame(count, raw=0):
    payload = bytes([0x20, 3, count * 2]) + raw.to_bytes(2, "big") * count
    return payload + bytes(modbus_crc(payload))

async def main():
    client = RenogyBleClient(transport_mode="persistent_session")
    device = MagicMock(device_type="inverter", model_hint="RIV4835CSH1S")
    device.parsed_data = {"battery_voltage": 50.2, "cell_voltages": [3.3] * 16}
    sessions = []
    reads = []
    closes = []
    async def prepare(_device):
        session = MagicMock()
        session.lock = asyncio.Lock()
        session.client.read_gatt_char = AsyncMock()
        sessions.append(session)
        return session
    async def read(session, **kw):
        assert kw["function_code"] == 3
        assert kw["device_id"] == 0x20
        reads.append(kw)
        if len(reads) == 1:
            return None
        if len(reads) == 2:
            assert closes[0] == (sessions[0], True)
            return frame(kw["word_count"])[:-1] + b"X"
        assert (sessions[1], True) in closes
        return frame(kw["word_count"])
    async def close(address, name, session, *, remove):
        closes.append((session, remove))
    client._prepare_session = prepare
    client._ensure_session_ready = AsyncMock()
    client._read_modbus_register = read
    client._close_session = close
    reader.asyncio.sleep = AsyncMock()
    snapshot = await reader.async_read_diagnostics(client, device)
    assert len(reads) == len(protocol.READ_BLOCKS)
    assert "riv_active_warnings" not in snapshot
    assert "riv_active_faults" not in snapshot
    assert "4393" in snapshot["riv_diagnostics"]["read_errors"]
    assert "4398" in snapshot["riv_diagnostics"]["read_errors"]
    assert snapshot["riv_program_04"] == 0
    assert len(device.parsed_data["cell_voltages"]) == 16
    assert device.parsed_data["battery_voltage"] == 50.2
    try:
        await reader.async_read_diagnostics(client,
            MagicMock(device_type="inverter", model_hint="other"))
    except ValueError:
        pass
    else:
        raise AssertionError("Missing model guard")

    # Cancellation must discard the session before its lock is released.
    client._read_modbus_register = AsyncMock(side_effect=asyncio.CancelledError())
    try:
        await reader.async_read_diagnostics(client, device)
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("Cancelled task continued")
    assert closes[-1] == (sessions[-1], True)

asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
