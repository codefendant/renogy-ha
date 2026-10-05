"""Compatibility reader using the installed Renogy library's validated transport.

This local adapter is for testing protocol diagnostics on patched installations.
It leaves the installed library and its Communication Hub support untouched.
"""

from __future__ import annotations

import asyncio
from typing import Any

from renogy_ble.ble import (
    INVERTER_DEVICE_ID,
    INVERTER_INIT_CHAR_UUID,
    INVERTER_INIT_DELAY,
    INVERTER_INTER_COMMAND_DELAY,
    modbus_crc,
)

from .const import RIV4835CSH1S_INVERTER_PROFILE
from .riv_diagnostic_protocol import READ_BLOCKS, parse_snapshot


def _decode_words(response: bytes, count: int) -> list[int]:
    """Validate framing again before exposing optional diagnostic values."""
    if (
        len(response) != 5 + count * 2
        or response[:3] != bytes((INVERTER_DEVICE_ID, 3, count * 2))
        or response[-2:] != bytes(modbus_crc(response[:-2]))
    ):
        raise ValueError("Invalid diagnostic Modbus response")
    return [
        int.from_bytes(response[3 + i * 2 : 5 + i * 2], "big") for i in range(count)
    ]


async def async_read_diagnostics(client: Any, device: Any) -> dict[str, Any]:
    """Read fresh registers through the existing session lock, using only FC03."""
    if (
        device.device_type != "inverter"
        or device.model_hint != RIV4835CSH1S_INVERTER_PROFILE
    ):
        raise ValueError("Diagnostics require the RIV4835CSH1S inverter profile")
    for method in (
        "_prepare_session",
        "_ensure_session_ready",
        "_read_modbus_register",
        "_close_session",
    ):
        if not callable(getattr(client, method, None)):
            raise RuntimeError(f"Installed library lacks required transport: {method}")

    words: dict[int, int] = {}
    errors: dict[str, str] = {}
    for register, count in READ_BLOCKS:
        # A reconnect after a timeout prevents late frames contaminating a later read.
        session = await client._prepare_session(device)
        async with session.lock:
            completed = False
            try:
                await client._ensure_session_ready(device, session)
                if session.client is None:
                    raise RuntimeError("BLE session is not connected")
                await asyncio.sleep(INVERTER_INIT_DELAY)
                try:
                    await session.client.read_gatt_char(INVERTER_INIT_CHAR_UUID)
                except Exception:
                    pass
                response = await client._read_modbus_register(
                    session,
                    device_id=INVERTER_DEVICE_ID,
                    function_code=0x03,
                    register=register,
                    word_count=count,
                    cmd_name=f"RIV read-only diagnostics {register}",
                    device_name=device.name,
                    timeout=2.0,
                    retries=1,
                )
                if response is None:
                    raise TimeoutError("No valid diagnostic response")
                values = _decode_words(response, count)
                words.update(
                    {register + index: raw for index, raw in enumerate(values)}
                )
                completed = True
            except Exception as exc:
                errors[str(register)] = str(exc)
            finally:
                if (
                    not completed
                    or getattr(client, "_transport_mode", None) != "persistent_session"
                ):
                    await client._close_session(
                        device.address, device.name, session, remove=not completed
                    )
        await asyncio.sleep(INVERTER_INTER_COMMAND_DELAY)
    return parse_snapshot(words, errors)
