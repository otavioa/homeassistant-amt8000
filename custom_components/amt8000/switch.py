"""Switches for AMT 8000: arm policy and per-zone bypass."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .alarm_control_panel import _device_info
from .client import BypassError
from .const import DOMAIN
from .coordinator import Amt8000Coordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Amt8000Coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = [Amt8000BypassSwitch(coordinator, entry)]
    if coordinator.data:
        entities.extend(
            Amt8000ZoneBypassSwitch(coordinator, zone.number, entry)
            for zone in coordinator.data.zones
        )
    async_add_entities(entities)


def _zone_from_coordinator(coordinator: Amt8000Coordinator, zone_number: int):
    if not coordinator.data:
        return None
    for zone in coordinator.data.zones:
        if zone.number == zone_number:
            return zone
    return None


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


class Amt8000ZoneBypassSwitch(CoordinatorEntity[Amt8000Coordinator], SwitchEntity):
    """Anula ou reativa uma zona na central."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: Amt8000Coordinator, zone_number: int, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._zone_number = zone_number
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone_number}_bypass"
        self._attr_name = f"Zone {zone_number} Bypass"
        self._attr_device_info = _device_info(entry)

    @property
    def icon(self) -> str:
        return "mdi:lock-off" if self.is_on else "mdi:lock"

    @property
    def is_on(self) -> bool | None:
        zone = _zone_from_coordinator(self.coordinator, self._zone_number)
        return zone.bypassed if zone else None

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._set_bypass(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._set_bypass(False)

    async def _set_bypass(self, enabled: bool) -> None:
        try:
            await self.coordinator.client.bypass_zones(
                [self._zone_number - 1], enabled=enabled
            )
        except BypassError as exc:
            raise HomeAssistantError("A central recusou o comando de bypass da zona.") from exc
        await self.coordinator.async_request_refresh()
