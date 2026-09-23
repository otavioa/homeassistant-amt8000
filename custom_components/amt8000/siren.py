"""Siren entity — live state, silence, and panic tones."""
from __future__ import annotations

from typing import Any

from homeassistant.components.siren import SirenEntity, SirenEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .alarm_control_panel import _device_info
from .client import PANIC_TYPES, SirenError
from .const import DOMAIN
from .coordinator import Amt8000Coordinator

# Tone name → protocol panic type (same keys as PANIC_TYPES).
_AVAILABLE_TONES = list(PANIC_TYPES.keys())


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Amt8000Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([Amt8000Siren(coordinator, entry)])


class Amt8000Siren(CoordinatorEntity[Amt8000Coordinator], SirenEntity):
    """Global panel siren: sounding state, silence, and panic."""

    _attr_has_entity_name = True
    _attr_name = "Siren"
    _attr_available_tones = _AVAILABLE_TONES
    _attr_supported_features = (
        SirenEntityFeature.TURN_ON
        | SirenEntityFeature.TURN_OFF
        | SirenEntityFeature.TONES
    )

    def __init__(self, coordinator: Amt8000Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_siren"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.siren_live

    async def async_turn_on(self, **kwargs: Any) -> None:
        tone = kwargs.get("tone") or "audible"
        if isinstance(tone, int):
            # Dict-style tones are not used; reject unexpected numeric keys.
            raise HomeAssistantError("Tom de pânico inválido.")
        if tone not in PANIC_TYPES:
            raise HomeAssistantError(f"Tom de pânico inválido: {tone}")
        try:
            await self.coordinator.client.panic(tone)
        except SirenError as exc:
            raise HomeAssistantError("A central recusou o comando de pânico.") from exc
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.client.siren_off()
        except SirenError as exc:
            raise HomeAssistantError("A central recusou o comando de silenciar.") from exc
        await self.coordinator.async_request_refresh()
