"""Configuration switches for the AMT 8000 integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.switch import SwitchEntity

from .const import DOMAIN
from .coordinator import Amt8000Coordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Amt8000Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([Amt8000BypassSwitch(coordinator, entry)])


class Amt8000BypassSwitch(
    CoordinatorEntity[Amt8000Coordinator], RestoreEntity, SwitchEntity
):
    """Allow automatic bypass of open zones when arming."""

    _attr_has_entity_name = True
    _attr_name = "Allow Open Zone Bypass"

    def __init__(self, coordinator: Amt8000Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_allow_open_zone_bypass"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}")},
            name="Intelbras AMT 8000",
            manufacturer="Intelbras",
            model="AMT 8000",
        )
        self._is_on = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._is_on = last_state.state == "on"
        self.coordinator.allow_open_zone_bypass = self._is_on

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def async_turn_on(self, **kwargs: object) -> None:
        self._is_on = True
        self.coordinator.allow_open_zone_bypass = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: object) -> None:
        self._is_on = False
        self.coordinator.allow_open_zone_bypass = False
        self.async_write_ha_state()
