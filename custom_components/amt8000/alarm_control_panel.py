"""Alarm control panel entities — one per user partition (1-N)."""
from __future__ import annotations

from homeassistant.components.persistent_notification import (
    DOMAIN as PERSISTENT_NOTIFICATION_DOMAIN,
)
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import ALL_PARTITIONS, BypassError, OpenZones
from .const import AGGREGATE_PARTITION_IDX, DOMAIN
from .coordinator import Amt8000Coordinator

import logging
_LOGGER = logging.getLogger(__name__)


def _open_zones(coordinator: Amt8000Coordinator) -> list:
    return [zone for zone in coordinator.data.zones if zone.open and not zone.bypassed]


async def _notify_open_zones(
    entity: CoordinatorEntity[Amt8000Coordinator],
    partition_idx: int,
    zones: list,
    message: str,
) -> None:
    zone_names = ", ".join(f"Zona {zone.number}" for zone in zones)
    await entity.hass.services.async_call(
        PERSISTENT_NOTIFICATION_DOMAIN,
        "create",
        {
            "title": "Zonas abertas",
            "message": f"{message}\n\n{zone_names}",
            "notification_id": f"{DOMAIN}_open_zones_{entity._entry.entry_id}_{partition_idx}",
        },
    )


async def _dismiss_open_zone_notification(
    entity: CoordinatorEntity[Amt8000Coordinator], partition_idx: int
) -> None:
    await entity.hass.services.async_call(
        PERSISTENT_NOTIFICATION_DOMAIN,
        "dismiss",
        {
            "notification_id": f"{DOMAIN}_open_zones_{entity._entry.entry_id}_{partition_idx}",
        },
    )


async def _arm_with_open_zone_policy(
    entity: CoordinatorEntity[Amt8000Coordinator], partition_idx: int
) -> None:
    zones = _open_zones(entity.coordinator)
    if not zones:
        await _notify_open_zones(
            entity,
            partition_idx,
            [],
            "A central recusou o arme por zona aberta, mas o último status não as identificou.",
        )
        return

    if not entity.coordinator.allow_open_zone_bypass:
        await _notify_open_zones(
            entity,
            partition_idx,
            zones,
            "O arme foi bloqueado. Ative 'Allow Open Zone Bypass' para permitir o bypass automático.",
        )
        return

    try:
        await entity.coordinator.client.bypass_zones([zone.number - 1 for zone in zones])
        await entity.coordinator.client.arm_partition(partition_idx)
    except BypassError as exc:
        _LOGGER.warning("Automatic open-zone bypass was rejected: %s", exc)
        await _notify_open_zones(
            entity,
            partition_idx,
            zones,
            "O bypass automático foi recusado pela central.",
        )
        return
    except OpenZones:
        _LOGGER.warning("The panel still reports open zones after automatic bypass")
        await _notify_open_zones(
            entity,
            partition_idx,
            zones,
            "A central ainda identificou zonas abertas após o bypass.",
        )
        return
    except Exception:
        _LOGGER.exception("Unexpected error during automatic open-zone bypass")
        await _notify_open_zones(
            entity,
            partition_idx,
            zones,
            "Não foi possível concluir o bypass automático. Consulte os logs.",
        )
        return

    await _dismiss_open_zone_notification(entity, partition_idx)
    await entity.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: Amt8000Coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[AlarmControlPanelEntity] = [
        Amt8000PartitionPanel(coordinator, p.index, entry)
        for p in coordinator.data.partitions
        if p.index != AGGREGATE_PARTITION_IDX
    ]
    entities.append(Amt8000MasterPanel(coordinator, entry))
    async_add_entities(entities)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}")},
        name="Intelbras AMT 8000",
        manufacturer="Intelbras",
        model="AMT 8000",
    )


class Amt8000PartitionPanel(CoordinatorEntity[Amt8000Coordinator], AlarmControlPanelEntity):
    _attr_has_entity_name = True
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY
    _attr_code_arm_required = False
    _attr_code_disarm_required = False

    def __init__(
        self, coordinator: Amt8000Coordinator, partition_idx: int, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._partition_idx = partition_idx
        self._entry = entry
        # User-visible partition number: skip the aggregate at index 0
        self._attr_unique_id = f"{entry.entry_id}_partition_{partition_idx}"
        self._attr_name = f"Partition {partition_idx}"
        self._attr_device_info = _device_info(entry)

    def _partition(self):
        if not self.coordinator.data:
            return None
        for p in self.coordinator.data.partitions:
            if p.index == self._partition_idx:
                return p
        return None

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        p = self._partition()
        if p is None:
            return None
        if p.firing:
            return AlarmControlPanelState.TRIGGERED
        if p.armed:
            return AlarmControlPanelState.ARMED_AWAY
        return AlarmControlPanelState.DISARMED

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        try:
            await self.coordinator.client.arm_partition(self._partition_idx)
        except OpenZones:
            _LOGGER.warning("Partition %d: arm blocked — open zones", self._partition_idx)
            await _arm_with_open_zone_policy(self, self._partition_idx)
            return
        await self.coordinator.async_request_refresh()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self.coordinator.client.disarm_partition(self._partition_idx)
        await self.coordinator.async_request_refresh()


class Amt8000MasterPanel(CoordinatorEntity[Amt8000Coordinator], AlarmControlPanelEntity):
    """Virtual panel that arms/disarms all user partitions at once."""

    _attr_has_entity_name = True
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY
    _attr_code_arm_required = False
    _attr_code_disarm_required = False

    def __init__(self, coordinator: Amt8000Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_all"
        self._attr_name = "All Partitions"
        self._attr_device_info = _device_info(entry)

    def _real_partitions(self):
        if not self.coordinator.data:
            return []
        return [
            p for p in self.coordinator.data.partitions
            if p.index != AGGREGATE_PARTITION_IDX
        ]

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        parts = self._real_partitions()
        if not parts:
            return None
        if any(p.firing for p in parts):
            return AlarmControlPanelState.TRIGGERED
        if all(p.armed for p in parts):
            return AlarmControlPanelState.ARMED_AWAY
        if any(p.armed for p in parts):
            return AlarmControlPanelState.ARMED_HOME
        return AlarmControlPanelState.DISARMED

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        try:
            await self.coordinator.client.arm_partition(ALL_PARTITIONS)
        except OpenZones:
            _LOGGER.warning("Master arm blocked — open zones")
            await _arm_with_open_zone_policy(self, ALL_PARTITIONS)
            return
        await self.coordinator.async_request_refresh()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self.coordinator.client.disarm_partition(ALL_PARTITIONS)
        await self.coordinator.async_request_refresh()
