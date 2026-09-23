"""Event entity — fires when a partition starts firing (alarm triggered)."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .alarm_control_panel import _device_info
from .const import DOMAIN
from .coordinator import Amt8000Coordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Amt8000Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([Amt8000AlarmEvent(coordinator, entry)])


class Amt8000AlarmEvent(CoordinatorEntity[Amt8000Coordinator], EventEntity):
    _attr_has_entity_name = True
    _attr_event_types = ["alarm_triggered"]

    def __init__(self, coordinator: Amt8000Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_alarm_event"
        self._attr_name = "Alarm"
        self._attr_device_info = _device_info(entry)
        self._prev_firing = False

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.coordinator.data:
            firing_indexes = [
                p.index for p in self.coordinator.data.partitions if p.firing
            ]
            firing = bool(firing_indexes)
            if firing and not self._prev_firing:
                self._trigger_event(
                    "alarm_triggered", {"partitions_firing": firing_indexes}
                )
                self.async_write_ha_state()
            self._prev_firing = firing
        super()._handle_coordinator_update()
