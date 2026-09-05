"""RIV4835CSH1S Program 01 output-priority transactions."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from renogy_ble.ble import (
    INVERTER_COMMAND_TIMEOUT,
    INVERTER_DEVICE_ID,
    INVERTER_INIT_CHAR_UUID,
    INVERTER_INIT_DELAY,
)

from .const import LOGGER, RIV4835CSH1SRegister

OUTPUT_PRIORITY_BY_RAW = {0: "SOL", 1: "UTI", 2: "SBU"}
OUTPUT_PRIORITY_TO_RAW = {value: key for key, value in OUTPUT_PRIORITY_BY_RAW.items()}


async def _prepare_locked_session(coordinator: Any) -> tuple[Any, Any, Any]:
    """Resolve the current Renogy device/client and prepare its BLE session."""
    client = getattr(coordinator, "_ble_client", None)
    if client is None:
        raise HomeAssistantError("Renogy BLE client is unavailable.")

    service_info_fn = getattr(coordinator, "_service_info_for_operation", None)
    service_info = service_info_fn() if callable(service_info_fn) else None

    if service_info is not None:
        update_device_fn = getattr(coordinator, "_update_device_from_service_info", None)
        if not callable(update_device_fn):
            raise HomeAssistantError(
                "Renogy coordinator cannot refresh BLE device context."
            )
        device = update_device_fn(service_info)
    else:
        device = getattr(coordinator, "device", None)

    if device is None:
        raise HomeAssistantError(
            "Renogy inverter has not been discovered yet. Wait for a normal poll and retry."
        )

    prepare_session = getattr(client, "_prepare_session", None)
    ensure_session_ready = getattr(client, "_ensure_session_ready", None)
    if not callable(prepare_session) or not callable(ensure_session_ready):
        raise HomeAssistantError(
            "Installed renogy-ble does not expose the session methods required for "
            "RIV4835 output-priority control."
        )

    session = await prepare_session(device)
    return client, device, session


async def _init_inverter_session(client: Any, device: Any, session: Any) -> None:
    """Initialize the inverter BLE session before a Modbus transaction."""
    ensure_session_ready = getattr(client, "_ensure_session_ready", None)
    if not callable(ensure_session_ready):
        raise HomeAssistantError("Renogy BLE session preparation is unavailable.")

    await ensure_session_ready(device, session)
    if session.client is None:
        raise HomeAssistantError("Renogy BLE session is not connected.")

    await asyncio.sleep(INVERTER_INIT_DELAY)
    try:
        await session.client.read_gatt_char(INVERTER_INIT_CHAR_UUID)
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("RIV4835 output-priority inverter init read failed: %s", exc)


async def _read_from_session(client: Any, device: Any, session: Any) -> int:
    """Read exactly one Program 01 register value from the active session."""
    read_modbus_register = getattr(client, "_read_modbus_register", None)
    if not callable(read_modbus_register):
        raise HomeAssistantError(
            "Installed renogy-ble does not expose the validated Modbus read method."
        )

    response = await read_modbus_register(
        session,
        device_id=INVERTER_DEVICE_ID,
        function_code=0x03,
        register=RIV4835CSH1SRegister.OUTPUT_PRIORITY,
        word_count=1,
        cmd_name="RIV4835 Program 01 output-priority read",
        device_name=device.name,
        timeout=INVERTER_COMMAND_TIMEOUT,
        retries=1,
    )

    if response is None:
        raise HomeAssistantError(
            "No valid Modbus response received for Program 01 register 0x1159."
        )
    if len(response) < 7 or response[1] != 0x03 or response[2] != 0x02:
        raise HomeAssistantError(
            f"Unexpected Modbus response for Program 01: {response.hex()}"
        )

    raw = int.from_bytes(response[3:5], "big", signed=False)
    if raw not in OUTPUT_PRIORITY_BY_RAW:
        raise HomeAssistantError(
            f"Unexpected Program 01 raw value {raw}; refusing to infer a mode."
        )
    return raw


async def _close_session_if_needed(client: Any, device: Any, session: Any) -> None:
    """Close an intermittent or desynchronized session after the transaction."""
    close_session = getattr(client, "_close_session", None)
    if not callable(close_session):
        return

    desynchronized = bool(getattr(session, "desynchronized", False))
    transport_mode = getattr(
        client,
        "transport_mode",
        getattr(client, "_transport_mode", "per_operation"),
    )
    if desynchronized:
        await close_session(device.address, device.name, session, remove=True)
    elif transport_mode != "persistent_session":
        await close_session(device.address, device.name, session, remove=False)


async def async_read_output_priority(coordinator: Any) -> int:
    """Read Program 01 with the coordinator connection lock held."""
    if getattr(coordinator, "_connection_in_progress", False):
        raise HomeAssistantError(
            "Renogy coordinator is busy. Wait for the current poll to finish and retry."
        )

    connection_lock = getattr(coordinator, "_connection_lock", None)
    if connection_lock is None:
        raise HomeAssistantError("Renogy coordinator connection lock is unavailable.")

    async with connection_lock:
        coordinator._connection_in_progress = True
        client = device = session = None
        try:
            client, device, session = await _prepare_locked_session(coordinator)
            async with session.lock:
                await _init_inverter_session(client, device, session)
                raw = await _read_from_session(client, device, session)
                LOGGER.debug(
                    "RIV4835 Program 01 readback register=0x1159 raw=%d decoded=%s",
                    raw,
                    OUTPUT_PRIORITY_BY_RAW[raw],
                )
                return raw
        finally:
            try:
                if client is not None and device is not None and session is not None:
                    await _close_session_if_needed(client, device, session)
            finally:
                coordinator._connection_in_progress = False


async def async_write_output_priority(coordinator: Any, target: int) -> int:
    """Write a hardware-validated Program 01 target and verify live readback."""
    # Only the UTI <-> SBU writes have been hardware-validated on this inverter.
    if target not in {1, 2}:
        raise HomeAssistantError(
            "Program 01 write is limited to hardware-validated values 1=UTI and 2=SBU."
        )

    if getattr(coordinator, "_connection_in_progress", False):
        raise HomeAssistantError(
            "Renogy coordinator is busy. Wait for the current poll to finish and retry."
        )

    connection_lock = getattr(coordinator, "_connection_lock", None)
    if connection_lock is None:
        raise HomeAssistantError("Renogy coordinator connection lock is unavailable.")

    async with connection_lock:
        coordinator._connection_in_progress = True
        client = device = session = None
        try:
            client, device, session = await _prepare_locked_session(coordinator)
            async with session.lock:
                await _init_inverter_session(client, device, session)
                current = await _read_from_session(client, device, session)
                if current == target:
                    return current

                write_single_register = getattr(client, "write_single_register", None)
                if not callable(write_single_register):
                    raise HomeAssistantError(
                        "Installed renogy-ble does not expose validated single-register writes."
                    )

                # The library method acquires the same session lock internally, so use
                # the already-validated coordinator write path instead of nesting it.
            # Release the BLE session lock before coordinator.async_write_register().
            success = await coordinator.async_write_register(
                RIV4835CSH1SRegister.OUTPUT_PRIORITY, target
            )
            if not success:
                raise HomeAssistantError(
                    f"Program 01 write to {OUTPUT_PRIORITY_BY_RAW[target]} was not acknowledged."
                )

            # The coordinator write path refreshes the device. Perform one independent
            # authoritative F03 readback before reporting the selected state.
            await asyncio.sleep(1.0)
        finally:
            # coordinator.async_write_register() manages its own BLE transaction; any
            # session prepared for the pre-read must be closed before the verify read.
            try:
                if client is not None and device is not None and session is not None:
                    await _close_session_if_needed(client, device, session)
            finally:
                coordinator._connection_in_progress = False

    verified = await async_read_output_priority(coordinator)
    if verified != target:
        raise HomeAssistantError(
            "Program 01 write was acknowledged but live readback did not match: "
            f"target={target}/{OUTPUT_PRIORITY_BY_RAW[target]}, "
            f"readback={verified}/{OUTPUT_PRIORITY_BY_RAW[verified]}."
        )

    LOGGER.info(
        "RIV4835 Program 01 verified write register=0x1159 raw=%d decoded=%s",
        verified,
        OUTPUT_PRIORITY_BY_RAW[verified],
    )
    return verified
