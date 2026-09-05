"""Support for Renogy BLE select entities."""

from __future__ import annotations

from typing import Optional, cast

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .availability import is_entity_available
from .ble import RenogyActiveBluetoothCoordinator, RenogyBLEDevice
from .const import (
    ATTR_MANUFACTURER,
    CONF_DEVICE_TYPE,
    CONF_INVERTER_PROFILE,
    DCC_BATTERY_TYPE_VALUES,
    DCC_BATTERY_TYPES,
    DCC_MAX_CURRENT_OPTIONS,
    DCC_MAX_CURRENT_TO_DEVICE,
    DEFAULT_DEVICE_TYPE,
    DEFAULT_INVERTER_PROFILE,
    DOMAIN,
    LOGGER,
    RIV4835CSH1S_INVERTER_PROFILE,
    DCCRegister,
    DeviceType,
)

# Human-readable names for display
BATTERY_TYPE_DISPLAY_NAMES = {
    "custom": "Custom",
    "open": "Open (Flooded)",
    "sealed": "Sealed (AGM)",
    "gel": "Gel",
    "lithium": "Lithium",
}


# Max charging current options for display (in amps)
MAX_CURRENT_OPTIONS = [f"{amp}A" for amp in DCC_MAX_CURRENT_OPTIONS]

# Mapping from display string to amps
MAX_CURRENT_DISPLAY_TO_AMPS = {f"{amp}A": amp for amp in DCC_MAX_CURRENT_OPTIONS}

# Program 01 values 0/1/2 were hardware-validated for readback. Only the UTI/SBU
# F06 writes have been hardware-validated so far; SOL remains visible for truthful
# live state but is intentionally blocked as a write target in this test branch.
RIV_OUTPUT_PRIORITY_BY_RAW = {0: "SOL", 1: "UTI", 2: "SBU"}
RIV_OUTPUT_PRIORITY_TO_RAW = {
    value: key for key, value in RIV_OUTPUT_PRIORITY_BY_RAW.items()
}
RIV_OUTPUT_PRIORITY_OPTIONS = ["SOL", "UTI", "SBU"]


DCC_SELECT_ENTITIES = (
    SelectEntityDescription(
        key="battery_type",
        name="Battery Type",
        entity_category=EntityCategory.CONFIG,
    ),
    SelectEntityDescription(
        key="max_charging_current",
        name="Max Charging Current",
        entity_category=EntityCategory.CONFIG,
    ),
)

RIV_SELECT_ENTITIES = (
    SelectEntityDescription(
        key="output_priority",
        name="Output Priority",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Renogy BLE select entities."""
    LOGGER.debug(
        "Setting up Renogy BLE select entities for entry: %s", config_entry.entry_id
    )

    renogy_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = renogy_data["coordinator"]

    device_type = config_entry.data.get(CONF_DEVICE_TYPE, DEFAULT_DEVICE_TYPE)
    device = coordinator.device
    entities = []

    if device_type == DeviceType.DCC.value:
        LOGGER.debug("Setting up select entities for DCC device")
        for description in DCC_SELECT_ENTITIES:
            if description.key == "battery_type":
                entity = RenogyBatteryTypeSelect(
                    coordinator=coordinator,
                    device=device,
                    description=description,
                    device_type=device_type,
                )
            elif description.key == "max_charging_current":
                entity = RenogyMaxCurrentSelect(
                    coordinator=coordinator,
                    device=device,
                    description=description,
                    device_type=device_type,
                )
            else:
                continue
            entities.append(entity)

    elif device_type == DeviceType.INVERTER.value:
        profile = config_entry.data.get(CONF_INVERTER_PROFILE, DEFAULT_INVERTER_PROFILE)
        if profile != RIV4835CSH1S_INVERTER_PROFILE:
            LOGGER.debug(
                "Skipping inverter select entities for non-RIV4835CSH1S profile: %s",
                profile,
            )
            return

        LOGGER.debug("Setting up RIV4835CSH1S output-priority select entity")
        entities.append(
            RenogyOutputPrioritySelect(
                coordinator=coordinator,
                device=device,
                description=RIV_SELECT_ENTITIES[0],
                device_type=device_type,
            )
        )

    else:
        LOGGER.debug(
            "Skipping select entities for unsupported device type: %s", device_type
        )
        return

    if entities:
        LOGGER.debug("Adding %s select entities", len(entities))
        async_add_entities(entities)


class RenogyOutputPrioritySelect(SelectEntity):
    """RIV4835CSH1S Program 01 output-priority select with live readback."""

    entity_description: SelectEntityDescription
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: RenogyActiveBluetoothCoordinator,
        device: Optional[RenogyBLEDevice],
        description: SelectEntityDescription,
        device_type: str = DEFAULT_DEVICE_TYPE,
    ) -> None:
        """Initialize the RIV4835 output-priority select."""
        self.coordinator = coordinator
        self._device = device
        self.entity_description = description
        self._attr_options = RIV_OUTPUT_PRIORITY_OPTIONS
        self._attr_current_option = None

        if device:
            self._attr_unique_id = f"{device.address}_{description.key}"
            self._attr_name = cast("str | None", description.name)
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, device.address)},
                name=device.name,
                manufacturer=ATTR_MANUFACTURER,
                model=RIV4835CSH1S_INVERTER_PROFILE,
            )
        else:
            self._attr_unique_id = f"{coordinator.address}_{description.key}"
            self._attr_name = cast("str | None", description.name)
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, coordinator.address)},
                name=f"Renogy {device_type.upper()}",
                manufacturer=ATTR_MANUFACTURER,
                model=RIV4835CSH1S_INVERTER_PROFILE,
            )

    @property
    def suggested_object_id(self) -> str | None:
        """Preserve the legacy entity component before name resolution."""
        if self._device is None:
            return f"Renogy {self._attr_name}"
        return super().suggested_object_id

    @property
    def available(self) -> bool:
        """Return if the underlying inverter is available."""
        return is_entity_available(self.coordinator, self._device)

    @property
    def current_option(self) -> str | None:
        """Return only the last authoritative Program 01 readback."""
        return self._attr_current_option

    async def async_update(self) -> None:
        """Refresh Program 01 directly from hardware register 0x1159."""
        from homeassistant.exceptions import HomeAssistantError

        from .riv4835_output_priority import async_read_output_priority

        if not self._device and self.coordinator.device:
            self._device = self.coordinator.device

        try:
            raw = await async_read_output_priority(self.coordinator)
        except HomeAssistantError as err:
            # A normal coordinator poll can briefly own the BLE connection. Keep
            # the last successful hardware readback in that case and retry on the
            # next coordinator update rather than flashing an unknown state.
            if "coordinator is busy" in str(err).lower():
                LOGGER.debug("Deferred RIV4835 Output Priority refresh: %s", err)
                return

            self._attr_current_option = None
            LOGGER.warning("Unable to refresh RIV4835 Output Priority: %s", err)
            return

        self._attr_current_option = RIV_OUTPUT_PRIORITY_BY_RAW[raw]

    async def async_select_option(self, option: str) -> None:
        """Write UTI/SBU and accept state only after authoritative readback."""
        from homeassistant.exceptions import HomeAssistantError

        from .riv4835_output_priority import async_write_output_priority

        if option not in self._attr_options:
            raise HomeAssistantError(f"Unknown Output Priority option: {option}")

        # SOL/raw=0 readback is hardware-validated, but an F06 write of raw=0 has
        # not yet been validated on the user's RIV4835CSH1S. Keep it visible so a
        # manual LCD change is represented truthfully, but refuse an unvalidated write.
        if option == "SOL":
            raise HomeAssistantError(
                "SOL write is intentionally disabled in this test branch because "
                "Program 01 raw=0 has not yet been hardware-validated with F06."
            )

        target = RIV_OUTPUT_PRIORITY_TO_RAW[option]
        verified = await async_write_output_priority(self.coordinator, target)
        self._attr_current_option = RIV_OUTPUT_PRIORITY_BY_RAW[verified]
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Refresh initially and after each normal coordinator update."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )
        self.async_schedule_update_ha_state(force_refresh=True)

    def _handle_coordinator_update(self) -> None:
        """Schedule a fresh hardware readback after a normal inverter poll."""
        if not self._device and self.coordinator.device:
            self._device = self.coordinator.device
        self.async_schedule_update_ha_state(force_refresh=True)


class RenogyBatteryTypeSelect(SelectEntity):
    """Representation of a Renogy battery type select entity."""

    entity_description: SelectEntityDescription
    # Friendly name = device name + entity name, so UI device renames cascade.
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RenogyActiveBluetoothCoordinator,
        device: Optional[RenogyBLEDevice],
        description: SelectEntityDescription,
        device_type: str = DEFAULT_DEVICE_TYPE,
    ) -> None:
        """Initialize the select entity."""
        self.coordinator = coordinator
        self._device = device
        self.entity_description = description
        self._attr_options = list(BATTERY_TYPE_DISPLAY_NAMES.values())
        self._attr_current_option = None

        # Device-dependent properties
        if device:
            self._attr_unique_id = f"{device.address}_{description.key}"
            self._attr_name = cast("str | None", description.name)
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, device.address)},
                name=device.name,
                manufacturer=ATTR_MANUFACTURER,
                model=f"Renogy {device_type.upper()}",
            )
        else:
            self._attr_unique_id = f"{coordinator.address}_{description.key}"
            self._attr_name = cast("str | None", description.name)
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, coordinator.address)},
                name=f"Renogy {device_type.upper()}",
                manufacturer=ATTR_MANUFACTURER,
            )

    @property
    def suggested_object_id(self) -> str | None:
        """Preserve the legacy entity component before name resolution."""
        if self._device is None:
            return f"Renogy {self._attr_name}"
        return super().suggested_object_id

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return is_entity_available(self.coordinator, self._device)

    @property
    def current_option(self) -> str | None:
        """Return the current selected option."""
        if self._attr_current_option is not None:
            return self._attr_current_option

        data = None
        if self._device and self._device.parsed_data:
            data = self._device.parsed_data
        elif self.coordinator.data:
            data = self.coordinator.data

        if not data:
            return None

        # Get the battery type value from data
        battery_type = data.get("battery_type")
        if battery_type is None:
            return None

        # If it's already a string, convert to display name
        if isinstance(battery_type, str):
            display_name = BATTERY_TYPE_DISPLAY_NAMES.get(battery_type.lower())
            if display_name:
                self._attr_current_option = display_name
                return display_name

        # If it's an integer, convert to display name
        if isinstance(battery_type, int):
            type_key = DCC_BATTERY_TYPES.get(battery_type)
            if type_key:
                display_name = BATTERY_TYPE_DISPLAY_NAMES.get(type_key)
                if display_name:
                    self._attr_current_option = display_name
                    return display_name

        return None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        # Find the key for this display name
        type_key = None
        for key, display in BATTERY_TYPE_DISPLAY_NAMES.items():
            if display == option:
                type_key = key
                break

        if type_key is None:
            LOGGER.error("Unknown battery type option: %s", option)
            return

        # Get the device value for this type
        device_value = DCC_BATTERY_TYPE_VALUES.get(type_key)
        if device_value is None:
            LOGGER.error("No device value for battery type: %s", type_key)
            return

        LOGGER.info(
            "Setting battery type to %s (device value: %s, register: 0x%04X)",
            option,
            device_value,
            DCCRegister.BATTERY_TYPE,
        )

        # Write to device via coordinator
        success = await self.coordinator.async_write_register(
            DCCRegister.BATTERY_TYPE, device_value
        )

        if success:
            # Update local value
            self._attr_current_option = option
            self.async_write_ha_state()
            LOGGER.info("Successfully set battery type to %s", option)
        else:
            LOGGER.error("Failed to set battery type to %s", option)

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Clear cached value to force a refresh
        self._attr_current_option = None

        # Update device reference if needed
        if not self._device and self.coordinator.device:
            self._device = self.coordinator.device

        self.async_write_ha_state()


class RenogyMaxCurrentSelect(SelectEntity):
    """Representation of a Renogy max charging current select entity."""

    entity_description: SelectEntityDescription
    # Friendly name = device name + entity name, so UI device renames cascade.
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RenogyActiveBluetoothCoordinator,
        device: Optional[RenogyBLEDevice],
        description: SelectEntityDescription,
        device_type: str = DEFAULT_DEVICE_TYPE,
    ) -> None:
        """Initialize the select entity."""
        self.coordinator = coordinator
        self._device = device
        self.entity_description = description
        self._attr_options = MAX_CURRENT_OPTIONS
        self._attr_current_option = None

        # Device-dependent properties
        if device:
            self._attr_unique_id = f"{device.address}_{description.key}"
            self._attr_name = cast("str | None", description.name)
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, device.address)},
                name=device.name,
                manufacturer=ATTR_MANUFACTURER,
                model=f"Renogy {device_type.upper()}",
            )
        else:
            self._attr_unique_id = f"{coordinator.address}_{description.key}"
            self._attr_name = cast("str | None", description.name)
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, coordinator.address)},
                name=f"Renogy {device_type.upper()}",
                manufacturer=ATTR_MANUFACTURER,
            )

    @property
    def suggested_object_id(self) -> str | None:
        """Preserve the legacy entity component before name resolution."""
        if self._device is None:
            return f"Renogy {self._attr_name}"
        return super().suggested_object_id

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return is_entity_available(self.coordinator, self._device)

    @property
    def current_option(self) -> str | None:
        """Return the current selected option."""
        if self._attr_current_option is not None:
            return self._attr_current_option

        data = None
        if self._device and self._device.parsed_data:
            data = self._device.parsed_data
        elif self.coordinator.data:
            data = self.coordinator.data

        if not data:
            return None

        # Get the max charging current value from data (in amps after scale)
        current_amps = data.get("max_charging_current")
        if current_amps is None:
            return None

        # Convert to integer and find closest valid option
        try:
            current_int = int(round(float(current_amps)))
            # Find the closest valid option
            if current_int in DCC_MAX_CURRENT_OPTIONS:
                display = f"{current_int}A"
                self._attr_current_option = display
                return display
        except ValueError, TypeError:
            pass

        return None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        # Get the amp value from display string
        amp_value = MAX_CURRENT_DISPLAY_TO_AMPS.get(option)
        if amp_value is None:
            LOGGER.error("Unknown max current option: %s", option)
            return

        # Get the device value (centiamps)
        device_value = DCC_MAX_CURRENT_TO_DEVICE.get(amp_value)
        if device_value is None:
            LOGGER.error("No device value for current: %sA", amp_value)
            return

        LOGGER.info(
            "Setting max charging current to %s (device value: %s, register: 0x%04X)",
            option,
            device_value,
            DCCRegister.MAX_CHARGING_CURRENT,
        )

        # Write to device via coordinator
        success = await self.coordinator.async_write_register(
            DCCRegister.MAX_CHARGING_CURRENT, device_value
        )

        if success:
            # Update local value
            self._attr_current_option = option
            self.async_write_ha_state()
            LOGGER.info("Successfully set max charging current to %s", option)
        else:
            LOGGER.error("Failed to set max charging current to %s", option)

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Clear cached value to force a refresh
        self._attr_current_option = None

        # Update device reference if needed
        if not self._device and self.coordinator.device:
            self._device = self.coordinator.device

        self.async_write_ha_state()
