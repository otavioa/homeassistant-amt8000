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

from .client import ALL_PARTITIONS, BypassError, NoStayZones, OpenZones
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
    *,
    title: str = "Zonas abertas",
) -> None:
    zone_names = ", ".join(f"Zona {zone.number}" for zone in zones)
    await entity.hass.services.async_call(
        PERSISTENT_NOTIFICATION_DOMAIN,
        "create",
        {
            "title": title,
            "message": f"{message}\n\n{zone_names}",
            "notification_id": f"{DOMAIN}_open_zones_{entity._entry.entry_id}_{partition_idx}",
        },
    )


async def _notify_no_stay_zones(
    entity: CoordinatorEntity[Amt8000Coordinator], partition_idx: int
) -> None:
    await entity.hass.services.async_call(
        PERSISTENT_NOTIFICATION_DOMAIN,
        "create",
        {
            "title": "Modo noturno",
            "message": (
                "O modo noturno foi recusado. Nenhuma zona da partição está "
                "configurada como zona stay (perímetro)."
            ),
            "notification_id": f"{DOMAIN}_no_stay_zones_{entity._entry.entry_id}_{partition_idx}",
        },
    )


async def _dismiss_no_stay_notification(
    entity: CoordinatorEntity[Amt8000Coordinator], partition_idx: int
) -> None:
    await entity.hass.services.async_call(
        PERSISTENT_NOTIFICATION_DOMAIN,
        "dismiss",
        {
            "notification_id": f"{DOMAIN}_no_stay_zones_{entity._entry.entry_id}_{partition_idx}",
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
    entity: CoordinatorEntity[Amt8000Coordinator],
    partition_idx: int,
    zones: list | None = None,
    *,
    stay: bool = False,
) -> None:
    zones = _open_zones(entity.coordinator) if zones is None else zones
    title = "Modo noturno" if stay else "Zonas abertas"
    if not zones:
        message = (
            "O modo noturno foi recusado. Há zona aberta no perímetro, mas o último status não as identificou."
            if stay
            else "A central recusou o arme por zona aberta, mas o último status não as identificou."
        )
        await _notify_open_zones(entity, partition_idx, [], message, title=title)
        return

    if not entity.coordinator.allow_open_zone_bypass:
        message = (
            "O modo noturno foi recusado. Há zona aberta no perímetro. "
            "Ative 'Allow Open Zone Bypass' para permitir o bypass automático."
            if stay
            else "O arme foi bloqueado. Ative 'Allow Open Zone Bypass' para permitir o bypass automático."
        )
        await _notify_open_zones(entity, partition_idx, zones, message, title=title)
        return

    try:
        await entity.coordinator.client.bypass_zones([zone.number - 1 for zone in zones])
        if stay:
            await entity.coordinator.client.arm_partition_stay(partition_idx)
        else:
            await entity.coordinator.client.arm_partition(partition_idx)
    except BypassError as exc:
        _LOGGER.warning("Automatic open-zone bypass was rejected: %s", exc)
        await _notify_open_zones(
            entity,
            partition_idx,
            zones,
            "O bypass automático foi recusado pela central.",
            title=title,
        )
        return
    except OpenZones:
        _LOGGER.warning("The panel still reports open zones after automatic bypass")
        message = (
            "O modo noturno foi recusado. A central ainda identificou zonas abertas após o bypass."
            if stay
            else "A central ainda identificou zonas abertas após o bypass."
        )
        await _notify_open_zones(entity, partition_idx, zones, message, title=title)
        return
    except NoStayZones:
        _LOGGER.warning("Night arm rejected: partition has no stay zones")
        await _notify_no_stay_zones(entity, partition_idx)
        return
    except Exception:
        _LOGGER.exception("Unexpected error during automatic open-zone bypass")
        await _notify_open_zones(
            entity,
            partition_idx,
            zones,
            "Não foi possível concluir o bypass automático. Consulte os logs.",
            title=title,
        )
        return

    await _dismiss_open_zone_notification(entity, partition_idx)
    if stay:
        await _dismiss_no_stay_notification(entity, partition_idx)
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
    entities.append(Amt8000Panel(coordinator, entry))
    async_add_entities(entities)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}")},
        name="Intelbras AMT 8000",
        manufacturer="Intelbras",
        model="AMT 8000",
    )


class Amt8000PartitionPanel(CoordinatorEntity[Amt8000Coordinator], AlarmControlPanelEntity):
    """Arms away, arms night (stay) and disarms one user partition."""

    _attr_has_entity_name = True
    _attr_translation_key = "partition"
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_NIGHT
    )
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
        if p.stay:
            return AlarmControlPanelState.ARMED_NIGHT
        if p.armed:
            return AlarmControlPanelState.ARMED_AWAY
        return AlarmControlPanelState.DISARMED

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._arm(stay=False)

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._arm(stay=True)

    async def _arm(self, stay: bool) -> None:
        zones = _open_zones(self.coordinator)
        if zones:
            await _arm_with_open_zone_policy(self, self._partition_idx, zones, stay=stay)
            return

        try:
            if stay:
                await self.coordinator.client.arm_partition_stay(self._partition_idx)
            else:
                await self.coordinator.client.arm_partition(self._partition_idx)
        except OpenZones:
            _LOGGER.warning(
                "Partition %d: %s blocked — open zones",
                self._partition_idx,
                "night arm" if stay else "arm",
            )
            await self.coordinator.async_request_refresh()
            await _arm_with_open_zone_policy(self, self._partition_idx, stay=stay)
            return
        except NoStayZones:
            _LOGGER.warning(
                "Partition %d: night arm rejected — no stay zones", self._partition_idx
            )
            await _notify_no_stay_zones(self, self._partition_idx)
            await self.coordinator.async_request_refresh()
            return
        if stay:
            await _dismiss_no_stay_notification(self, self._partition_idx)
        await self.coordinator.async_request_refresh()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self.coordinator.client.disarm_partition(self._partition_idx)
        await self.coordinator.async_request_refresh()


class Amt8000Panel(CoordinatorEntity[Amt8000Coordinator], AlarmControlPanelEntity):
    """Arms away, arms night (stay) and disarms every user partition (0xFF)."""

    _attr_has_entity_name = True
    _attr_translation_key = "panel"
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_NIGHT
    )
    _attr_code_arm_required = False
    _attr_code_disarm_required = False

    def __init__(self, coordinator: Amt8000Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_panel"
        self._attr_name = "Panel"
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
        if all(p.stay for p in parts):
            return AlarmControlPanelState.ARMED_NIGHT
        if all(p.armed and not p.stay for p in parts):
            return AlarmControlPanelState.ARMED_AWAY
        if any(p.armed or p.stay for p in parts):
            return AlarmControlPanelState.ARMED_HOME
        return AlarmControlPanelState.DISARMED

    @property
    def extra_state_attributes(self) -> dict:
        status = self.coordinator.data
        if status is None:
            return {}
        return {
            "battery": status.battery,
            "tamper": status.tamper,
            "model": f"0x{status.model:02X}",
            "firmware": status.version,
        }

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._arm(stay=False)

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._arm(stay=True)

    async def _arm(self, stay: bool) -> None:
        zones = _open_zones(self.coordinator)
        if zones:
            await _arm_with_open_zone_policy(self, ALL_PARTITIONS, zones, stay=stay)
            return

        try:
            if stay:
                await self.coordinator.client.arm_partition_stay(ALL_PARTITIONS)
            else:
                await self.coordinator.client.arm_partition(ALL_PARTITIONS)
        except OpenZones:
            _LOGGER.warning("Panel %s blocked — open zones", "night arm" if stay else "arm")
            await self.coordinator.async_request_refresh()
            await _arm_with_open_zone_policy(self, ALL_PARTITIONS, stay=stay)
            return
        except NoStayZones:
            _LOGGER.warning("Panel night arm rejected: no stay zones")
            await _notify_no_stay_zones(self, ALL_PARTITIONS)
            await self.coordinator.async_request_refresh()
            return
        if stay:
            await _dismiss_no_stay_notification(self, ALL_PARTITIONS)
        await self.coordinator.async_request_refresh()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self.coordinator.client.disarm_partition(ALL_PARTITIONS)
        await self.coordinator.async_request_refresh()
