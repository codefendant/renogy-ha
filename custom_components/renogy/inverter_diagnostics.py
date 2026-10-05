"""Read-only LCD settings supported by the companion library diagnostics reader."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntityDescription
from homeassistant.helpers.entity import EntityCategory

# Program 28 already has a number entity; Program 01's pending select is unchanged.
LCD_PROGRAMS = {
    1: ("Output Priority", None),
    2: ("Output Frequency", "Hz"),
    3: ("AC Input Voltage Range", None),
    4: ("Battery to Utility Setpoint", "V"),
    5: ("Utility to Battery Setpoint", "V"),
    6: ("Battery Charging Mode", None),
    7: ("Maximum Total Charging Current", "A"),
    8: ("Battery Type", None),
    9: ("Boost Charge Voltage", "V"),
    10: ("Boost Charge Duration", "min"),
    11: ("Float Charge Voltage", "V"),
    12: ("Low Voltage Load Disconnect", "V"),
    13: ("Overdischarge Delay", "s"),
    14: ("Low Voltage Warning", "V"),
    15: ("Discharge Limit Voltage", "V"),
    17: ("Equalization Voltage", "V"),
    18: ("Equalization Duration", "min"),
    19: ("Equalization Timeout", "min"),
    20: ("Equalization Interval", "d"),
    22: ("Power Saving Mode", None),
    25: ("Buzzer Alarm", None),
    35: ("Low Voltage Disconnect Recovery", "V"),
    37: ("Boost Return Setpoint", "V"),
    39: ("Maximum AC Input Current", "A"),
}

DIAGNOSTIC_DESCRIPTIONS = tuple(
    SensorEntityDescription(
        key=f"riv_program_{program:02d}",
        name=f"LCD {program:02d} {name}",
        native_unit_of_measurement=unit,
        suggested_display_precision=(
            1 if unit in {"V", "A"} else 2 if unit == "Hz" else 0 if unit else None
        ),
        device_class=(
            SensorDeviceClass.VOLTAGE
            if unit == "V"
            else SensorDeviceClass.CURRENT
            if unit == "A"
            else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    )
    for program, (name, unit) in LCD_PROGRAMS.items()
) + tuple(
    SensorEntityDescription(
        key=key, name=name, entity_category=EntityCategory.DIAGNOSTIC
    )
    for key, name in (
        ("riv_operating_state", "Operating State"),
        ("riv_active_faults", "Active Faults"),
        ("riv_fault_count", "Active Fault Count"),
        ("riv_warning_mask", "Warning Mask"),
        ("riv_active_warnings", "Active Warnings"),
    )
)
