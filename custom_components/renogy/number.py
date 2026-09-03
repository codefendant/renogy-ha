"""v18-compatible Renogy writable number platform with RIV4835 Program 28."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_DEVICE_TYPE,
    DEFAULT_DEVICE_TYPE,
    DOMAIN,
    LOGGER,
    DeviceType,
)
from .number_legacy import (
    RenogyNumberEntity,
    RenogyNumberEntityDescription,
    async_setup_entry as async_setup_legacy,
)

# RIV4835CSH1S Program 28: Maximum AC Charging Current.
# Validated on physical hardware: 0 A disables utility battery charging while
# retaining AC bypass; 10 A produces approximately 10 A line charging.
RIV4835_PROGRAM_28_REGISTER = 0x1146
RIV4835_MODBUS_DEVICE_ID = 0x20

RIV4835_PROGRAM_28 = RenogyNumberEntityDescription(
    key="inverter_charge_current",
    name="Maximum AC Charging Current",
    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    device_class=NumberDeviceClass.CURRENT,
    native_min_value=0.0,
    native_max_value=40.0,
    native_step=1.0,
    mode=NumberMode.BOX,
    entity_category=EntityCategory.CONFIG,
    register=RIV4835_PROGRAM_28_REGISTER,
    scale=10.0,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up v18 number entities while preserving legacy DCC behavior."""
    device_type = config_entry.data.get(CONF_DEVICE_TYPE, DEFAULT_DEVICE_TYPE)

    if device_type == DeviceType.DCC.value:
        await async_setup_legacy(hass, config_entry, async_add_entities)
        return

    if device_type != DeviceType.INVERTER.value:
        LOGGER.debug(
            "Skipping number entities for unsupported v18 device type: %s",
            device_type,
        )
        return

    # v18 predates inverter-profile configuration, so this compatibility branch
    # exposes Program 28 to legacy inverter entries. The upstream implementation
    # remains gated specifically to the RIV4835CSH1S profile.
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    entity = RIV4835Program28NumberEntity(
        coordinator=coordinator,
        device=coordinator.device,
        description=RIV4835_PROGRAM_28,
        device_type=device_type,
    )
    LOGGER.debug("Adding v18 RIV4835 Program 28 number entity")
    async_add_entities([entity])


class RIV4835Program28NumberEntity(RenogyNumberEntity):
    """Legacy-v18 wrapper for RIV4835CSH1S Program 28."""

    async def async_set_native_value(self, value: float) -> None:
        """Write Program 28 using the inverter's validated Modbus slave ID."""
        if not 0.0 <= value <= 40.0:
            LOGGER.error("RIV4835 Program 28 value out of range: %s A", value)
            return

        ble_client = getattr(self.coordinator, "_ble_client", None)
        if ble_client is None:
            LOGGER.error("Cannot write RIV4835 Program 28: BLE client unavailable")
            return

        # v18 constructed generic RenogyBleClient instances with the universal
        # 0xFF ID. Current upstream correctly constructs inverter clients with
        # INVERTER_DEVICE_ID (0x20). Apply that correction immediately before
        # every legacy write so register 0x1146 is addressed to the inverter.
        setattr(ble_client, "_device_id", RIV4835_MODBUS_DEVICE_ID)

        await super().async_set_native_value(value)

    def _handle_coordinator_update(self) -> None:
        """Retain the last successful write until v18 gains register readback."""
        if not self._device and self.coordinator.device:
            self._device = self.coordinator.device

        # Do not clear _attr_native_value here. The v18 RIV poll does not read
        # register 0x1146, so clearing it would make the entity Unknown after
        # every normal coordinator refresh.
        self.async_write_ha_state()
