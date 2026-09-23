"""Binary sensors — zones and recorded RF sirens (diagnostics)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .alarm_control_panel import _device_info
from .const import DOMAIN
from .coordinator import Amt8000Coordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Amt8000Coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [
        Amt8000ZoneSensor(coordinator, zone.number, entry)
        for zone in coordinator.data.zones
    ]
    entities.extend(
        Amt8000ExternalSirenSensor(coordinator, siren.number, entry)
        for siren in coordinator.data.sirens
    )
    async_add_entities(entities)


class Amt8000ZoneSensor(CoordinatorEntity[Amt8000Coordinator], BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: Amt8000Coordinator, zone_number: int, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._zone_number = zone_number
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone_number}"
        self._attr_name = f"Zone {zone_number}"
        self._attr_device_info = _device_info(entry)

    def _zone(self):
        if not self.coordinator.data:
            return None
        for z in self.coordinator.data.zones:
            if z.number == self._zone_number:
                return z
        return None

    @property
    def device_class(self) -> BinarySensorDeviceClass | None:
        dc = self._entry.options.get(f"zone_{self._zone_number}_device_class", "door")
        return BinarySensorDeviceClass(dc) if dc else None

    @property
    def is_on(self) -> bool | None:
        z = self._zone()
        return z.is_open if z else None

    @property
    def extra_state_attributes(self) -> dict:
        z = self._zone()
        if z is None:
            return {}
        return {
            "violated": z.violated,
            "bypassed": z.bypassed,
            "tamper": z.tamper,
            "low_battery": z.low_battery,
            "zone_status": "Anulado" if z.bypassed else "Ativo",
        }


class Amt8000ExternalSirenSensor(CoordinatorEntity[Amt8000Coordinator], BinarySensorEntity):
    """RF siren registered in 0x0B50 — diagnostics in attributes (no problem class)."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: Amt8000Coordinator, siren_number: int, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._siren_number = siren_number
        self._attr_unique_id = f"{entry.entry_id}_siren_rf_{siren_number}"
        self._attr_name = f"Siren {siren_number}"
        self._attr_device_info = _device_info(entry)

    def _siren(self):
        if not self.coordinator.data:
            return None
        for s in self.coordinator.data.sirens:
            if s.number == self._siren_number:
                return s
        return None

    @property
    def is_on(self) -> bool | None:
        # Entity exists only when registered; on = present/registered (not sounding).
        return self._siren() is not None

    @property
    def extra_state_attributes(self) -> dict:
        s = self._siren()
        if s is None:
            return {}
        return {
            "number": s.number,
            "fault": s.fault,
            "tamper": s.tamper,
            "low_battery": s.low_battery,
        }
