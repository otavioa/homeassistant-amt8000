"""DataUpdateCoordinator for AMT 8000."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import Amt8000Client, CannotConnect, InvalidAuth, PanelStatus
from .const import DOMAIN, EVENT_ALARM_TRIGGERED, SCAN_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class Amt8000Coordinator(DataUpdateCoordinator[PanelStatus]):
    def __init__(self, hass: HomeAssistant, client: Amt8000Client) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.client = client
        self._prev_firing = False
        self._consecutive_failures = 0
        self._last_status: PanelStatus | None = None
        self.allow_open_zone_bypass = False

    async def _async_update_data(self) -> PanelStatus:
        try:
            status = await self.client.get_status()
        except InvalidAuth as exc:
            # Auth failures are always fatal — wrong password won't self-heal.
            raise UpdateFailed(f"Authentication failed: {exc}") from exc
        except CannotConnect as exc:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                raise UpdateFailed(f"Cannot reach panel: {exc}") from exc
            _LOGGER.debug(
                "Panel unreachable (attempt %d/3): %s", self._consecutive_failures, exc
            )
            if self._last_status is not None:
                return self._last_status
            raise UpdateFailed(f"Cannot reach panel: {exc}") from exc

        self._consecutive_failures = 0
        self._last_status = status

        # Rising edge on partition firing → HA bus event for automations
        firing = any(p.firing for p in status.partitions)
        if firing and not self._prev_firing:
            self.hass.bus.async_fire(
                EVENT_ALARM_TRIGGERED,
                {"partitions_firing": [p.index for p in status.partitions if p.firing]},
            )
        self._prev_firing = firing

        return status
