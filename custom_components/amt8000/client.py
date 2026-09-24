"""Async ISECNet v2 client for Intelbras AMT 8000.

Protocol reference: github.com/caarlos0/homekit-amt8000 (Go reference implementation)
Confirmed against AMT 8000 firmware 3.2.5 via live packet captures.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging

_LOGGER = logging.getLogger(__name__)

_DST = [0x00, 0x00]
_SRC = [0x8F, 0xE0]

_CMD_AUTH       = [0xF0, 0xF0]
_CMD_STATUS     = [0x0B, 0x4A]
_CMD_DEVICES    = [0x0B, 0x50]
_CMD_ARM        = [0x40, 0x1E]
_CMD_BYPASS     = [0x40, 0x1F]
_CMD_PGM        = [0x45, 0xAF]
_CMD_PANIC      = [0x40, 0x1A]
_CMD_SIREN_OFF  = [0x40, 0x19]
_CMD_DISCONNECT = [0xF0, 0xF1]

_SUBCMD_DISARM = 0x00
_SUBCMD_ARM    = 0x01
_SUBCMD_STAY   = 0x02

ALL_PARTITIONS = 0xFF
MAX_PGMS = 16
MAX_SIRENS = 16
# On/off is status payload[137:139] (16 bits). Existence comes from 0x0B50.
_PGM_ON_OFFSET = 137
_PGM_COMM_FAIL_OFFSET = 87
_PGM_TAMPER_OFFSET = 103
_PGM_LOW_BATTERY_OFFSET = 119
# RF siren trouble masks in 0x0B4A (SDK; fault range is dedicated 83–84).
# Tamper/battery: 2 bytes keypads + 2 sirens + 2 repeaters in 97–102 / 113–118.
_SIREN_FAULT_OFFSET = 83
_SIREN_TAMPER_OFFSET = 99
_SIREN_LOW_BATTERY_OFFSET = 115
_NACK = 0xF0FD

PANIC_TYPES = {
    "silent": 0,
    "audible": 1,
    "fire": 2,
    "medical": 3,
}

_STATES = {0: "DISARMED", 1: "PARTIAL", 3: "ARMED"}
_BATTERY = {1: "dead", 2: "low", 3: "middle", 4: "full"}


class CannotConnect(Exception):
    """Raised when the connection to the panel fails."""


class InvalidAuth(Exception):
    """Raised when the panel rejects the password."""


class OpenZones(Exception):
    """Raised when arm is blocked because zones are open."""


class NoStayZones(Exception):
    """Stay arm rejected: the partition has no stay zones configured (NACK 0x36)."""


class BypassError(Exception):
    """Raised when the panel rejects a zone bypass command."""


class PgmError(Exception):
    """Raised when the panel rejects a PGM command."""


class SirenError(Exception):
    """Raised when the panel rejects a siren or panic command."""


@dataclasses.dataclass
class Zone:
    number: int
    enabled: bool
    open: bool
    violated: bool
    bypassed: bool
    tamper: bool
    low_battery: bool

    @property
    def is_open(self) -> bool:
        return self.open


@dataclasses.dataclass
class Partition:
    index: int
    enabled: bool
    armed: bool
    stay: bool
    firing: bool
    fired: bool


@dataclasses.dataclass
class Pgm:
    index: int
    enabled: bool
    on: bool
    tamper: bool
    low_battery: bool
    comm_fail: bool


@dataclasses.dataclass
class Siren:
    """Recorded RF siren (1-based number). Diagnostics from 0x0B4A SDK masks."""

    number: int
    enabled: bool
    fault: bool
    tamper: bool
    low_battery: bool


@dataclasses.dataclass
class PanelStatus:
    model: int
    version: str
    state: str
    siren_live: bool
    zones_firing: bool
    zones_closed: bool
    battery: str
    tamper: bool
    partitions: list[Partition]
    zones: list[Zone]
    pgms: list[Pgm]
    sirens: list[Siren]


class Amt8000Client:
    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._timeout = timeout

    # ── wire helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _checksum(data: list[int]) -> int:
        cs = 0
        for b in data:
            cs ^= b
        return (cs ^ 0xFF) & 0xFF

    @staticmethod
    def _encode_password(pwd: str) -> list[int]:
        """Contact-ID BCD encoding: 0 digit → 0x0A; 4-digit passwords left-padded."""
        digits = [(int(c) % 10 or 0x0A) for c in pwd]
        if len(pwd) == 4:
            digits = [0x0A, 0x0A] + digits
        return digits  # always 6 bytes

    def _packet(self, cmd: list[int], payload: list[int] | None = None) -> bytes:
        payload = payload or []
        length = len(payload) + 2  # 2 cmd bytes count in length field
        data = _DST + _SRC + [0x00, length] + cmd + payload
        data.append(self._checksum(data))
        return bytes(data)

    async def _read_frame(self, reader: asyncio.StreamReader) -> bytes:
        """Read one complete ISECNet v2 frame from the stream."""
        try:
            header = await asyncio.wait_for(reader.readexactly(6), self._timeout)
            length = int.from_bytes(header[4:6], "big")
            body = await asyncio.wait_for(reader.readexactly(length + 1), self._timeout)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError) as exc:
            raise CannotConnect("Incomplete response from panel") from exc
        return header + body

    async def _connect_and_auth(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), self._timeout
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise CannotConnect(f"Cannot connect to {self._host}:{self._port}") from exc

        auth_payload = [0x00] + self._encode_password(self._password) + [0x10]
        writer.write(self._packet(_CMD_AUTH, auth_payload))
        await writer.drain()

        resp = await self._read_frame(reader)
        result = resp[8] if len(resp) > 8 else -1
        if result == 0x01:
            writer.close()
            raise InvalidAuth("Invalid password")
        if result != 0x00:
            writer.close()
            raise CannotConnect(f"Auth rejected: code=0x{result:02X}")

        return reader, writer

    async def _disconnect(self, writer: asyncio.StreamWriter) -> None:
        try:
            writer.write(self._packet(_CMD_DISCONNECT))
            await asyncio.wait_for(writer.drain(), 1.0)
        except Exception:
            pass
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), 1.0)
        except Exception:
            pass

    # ── public API ────────────────────────────────────────────────────────────

    async def get_status(self) -> PanelStatus:
        reader, writer = await self._connect_and_auth()
        try:
            writer.write(self._packet(_CMD_STATUS))
            await writer.drain()
            status_resp = await self._read_frame(reader)
            writer.write(self._packet(_CMD_DEVICES))
            await writer.drain()
            devices_resp = await self._read_frame(reader)
        finally:
            await self._disconnect(writer)

        payload = Amt8000Client._response_payload(status_resp)
        devices_payload = Amt8000Client._response_payload(
            devices_resp, expected=_CMD_DEVICES
        )
        pgm_indexes = Amt8000Client.recorded_pgm_indexes(devices_payload)
        siren_numbers = Amt8000Client.recorded_siren_numbers(devices_payload)
        return self._parse_status(payload, pgm_indexes, siren_numbers)

    async def arm_partition(self, partition_idx: int) -> None:
        await self._arm_cmd(partition_idx, _SUBCMD_ARM)

    async def arm_partition_stay(self, partition_idx: int) -> None:
        """Arm stay (night): perimeter armed, interior zones left open. Operation 0x02."""
        await self._arm_cmd(partition_idx, _SUBCMD_STAY)

    async def disarm_partition(self, partition_idx: int) -> None:
        await self._arm_cmd(partition_idx, _SUBCMD_DISARM)

    async def bypass_zones(self, zone_indices: list[int], enabled: bool = True) -> None:
        """Set zone bypass: enabled=True anula, False reativa."""
        if not zone_indices or any(not 0 <= index < 56 for index in zone_indices):
            raise BypassError("Invalid zone index")

        flag = 0x01 if enabled else 0x00
        reader, writer = await self._connect_and_auth()
        try:
            for zone_index in zone_indices:
                writer.write(self._packet(_CMD_BYPASS, [zone_index, flag]))
                await writer.drain()
                response = await self._read_frame(reader)
                if len(response) < 8:
                    raise BypassError("Invalid bypass response")

                response_cmd = int.from_bytes(response[6:8], "big")
                if response_cmd == 0xF0FD:
                    error_code = response[8] if len(response) > 8 else 0
                    messages = {
                        0xE6: "Bypass denied",
                        0xE8: "Bypass rejected",
                        55: "Bypass permission denied",
                    }
                    raise BypassError(messages.get(error_code, f"Bypass failed: {error_code}"))
        finally:
            await self._disconnect(writer)

    async def set_pgm(self, index: int, enabled: bool) -> None:
        """Turn a PGM output on (enabled=True) or off (enabled=False)."""
        if not 0 <= index < MAX_PGMS:
            raise PgmError("Invalid PGM index")

        flag = 0x01 if enabled else 0x00
        reader, writer = await self._connect_and_auth()
        try:
            writer.write(self._packet(_CMD_PGM, [index, flag]))
            await writer.drain()
            response = await self._read_frame(reader)
            if len(response) < 8:
                raise PgmError("Invalid PGM response")

            response_cmd = int.from_bytes(response[6:8], "big")
            if response_cmd == 0xF0FD:
                error_code = response[8] if len(response) > 8 else 0
                _LOGGER.debug("PGM command NACK: 0x%02X", error_code)
                raise PgmError("PGM command rejected")
        finally:
            await self._disconnect(writer)

    async def siren_off(self) -> None:
        """Silence the active siren while leaving arm state unchanged."""
        await self._siren_command(_CMD_SIREN_OFF)

    async def panic(self, panic_type: str = "audible") -> None:
        """Trigger a panel panic. panic_type: silent|audible|fire|medical."""
        if panic_type not in PANIC_TYPES:
            raise SirenError(f"Invalid panic type: {panic_type}")
        await self._siren_command(_CMD_PANIC, [PANIC_TYPES[panic_type]])

    async def _siren_command(
        self, cmd: list[int], payload: list[int] | None = None
    ) -> None:
        reader, writer = await self._connect_and_auth()
        try:
            writer.write(self._packet(cmd, payload))
            await writer.drain()
            response = await self._read_frame(reader)
            if len(response) < 8:
                raise SirenError("Invalid siren/panic response")

            response_cmd = int.from_bytes(response[6:8], "big")
            if response_cmd == _NACK:
                error_code = response[8] if len(response) > 8 else 0
                _LOGGER.debug("Siren/panic NACK: 0x%02X", error_code)
                raise SirenError("Siren/panic command rejected")
        finally:
            await self._disconnect(writer)

    async def _arm_cmd(self, partition_idx: int, subcmd: int) -> None:
        reader, writer = await self._connect_and_auth()
        try:
            writer.write(self._packet(_CMD_ARM, [partition_idx, subcmd]))
            await writer.drain()
            if subcmd in (_SUBCMD_ARM, _SUBCMD_STAY):
                try:
                    resp = await self._read_frame(reader)
                    if len(resp) >= 8 and int.from_bytes(resp[6:8], "big") == _NACK:
                        error_code = resp[8] if len(resp) > 8 else 0
                        if subcmd == _SUBCMD_STAY and error_code == 0x36:
                            raise NoStayZones("Partition has no stay zones")
                        if error_code == 0x27:
                            raise OpenZones("Cannot arm: zones are open")
                    elif len(resp) > 8 and resp[8] == 0xF0:
                        raise OpenZones("Cannot arm: zones are open")
                except CannotConnect:
                    pass  # panel may close before responding; assume success
        finally:
            await self._disconnect(writer)

    # ── status parser ─────────────────────────────────────────────────────────

    @staticmethod
    def _response_payload(frame: bytes, expected: list[int] | None = None) -> bytes:
        """Extract payload; empty on NACK, short frame, or unexpected command."""
        if len(frame) < 8:
            return b""
        command = int.from_bytes(frame[6:8], "big")
        if command == _NACK:
            return b""
        if expected is not None and command != int.from_bytes(bytes(expected), "big"):
            return b""
        length = int.from_bytes(frame[4:6], "big") - 2
        if length < 0:
            return b""
        return frame[8 : 8 + length]

    @staticmethod
    def recorded_pgm_indexes(payload: bytes) -> list[int]:
        """0-based PGM indexes marked recorded in 0x0B50 (SDK bytes 26–28)."""
        if len(payload) < 26:
            return []
        indexes: list[int] = []
        for bit in range(4):
            if payload[25] & (1 << (4 + bit)):
                indexes.append(bit)
        if len(payload) >= 27:
            for bit in range(8):
                if payload[26] & (1 << bit):
                    indexes.append(bit + 4)
        if len(payload) >= 28:
            for bit in range(4):
                if payload[27] & (1 << bit):
                    indexes.append(bit + 12)
        return indexes

    @staticmethod
    def recorded_siren_numbers(payload: bytes) -> list[int]:
        """1-based RF siren numbers marked recorded in 0x0B50 bytes 23:25."""
        if len(payload) < 24:
            return []
        numbers: list[int] = []
        limit = min(MAX_SIRENS, (len(payload) - 23) * 8)
        for index in range(limit):
            if Amt8000Client._mask_bit(payload, 23, index):
                numbers.append(index + 1)
        return numbers

    @staticmethod
    def _parse_status(
        payload: bytes,
        pgm_indexes: list[int] | None = None,
        siren_numbers: list[int] | None = None,
    ) -> PanelStatus:
        if len(payload) < 143:
            raise CannotConnect(f"Truncated status payload: {len(payload)} bytes (expected 143)")

        status_byte = payload[20]

        partitions: list[Partition] = []
        for i in range(16):
            b = payload[21 + i]
            if b & 0x80:
                partitions.append(Partition(
                    index=i,
                    enabled=True,
                    armed=bool(b & 0x01),
                    stay=bool(b & 0x40),
                    firing=bool(b & 0x04),
                    fired=bool(b & 0x08),
                ))

        zones: list[Zone] = []
        for i in range(56):
            bi, bit = divmod(i, 8)
            if not (payload[12 + bi] & (1 << bit)):
                continue
            zones.append(Zone(
                number=i + 1,
                enabled=True,
                open=bool(payload[38 + bi] & (1 << bit)),
                violated=bool(payload[46 + bi] & (1 << bit)),
                bypassed=bool(payload[54 + bi] & (1 << bit)) if bi < 8 else False,
                tamper=bool(payload[89 + bi] & (1 << bit)) if 89 + bi < 143 else False,
                low_battery=bool(payload[105 + bi] & (1 << bit)) if 105 + bi < 143 else False,
            ))

        return PanelStatus(
            model=payload[0],
            version=f"{payload[1]}.{payload[2]}.{payload[3]}",
            state=_STATES.get((status_byte >> 5) & 0x03, "UNKNOWN"),
            siren_live=bool(status_byte & 0x02),
            zones_firing=bool(status_byte & 0x08),
            zones_closed=bool(status_byte & 0x04),
            battery=_BATTERY.get(payload[134], "unknown"),
            tamper=bool(payload[71] & 0x02),
            partitions=partitions,
            zones=zones,
            pgms=Amt8000Client._parse_pgms(payload, pgm_indexes or []),
            sirens=Amt8000Client._parse_sirens(payload, siren_numbers or []),
        )

    @staticmethod
    def _mask_bit(payload: bytes, offset: int, index: int) -> bool:
        byte_index, bit_index = divmod(index, 8)
        pos = offset + byte_index
        if pos >= len(payload):
            return False
        return bool(payload[pos] & (1 << bit_index))

    @staticmethod
    def _parse_pgms(payload: bytes, indexes: list[int]) -> list[Pgm]:
        """Build recorded PGMs; on/off and trouble bits from 0x0B4A masks."""
        return [
            Pgm(
                index=i,
                enabled=True,
                on=Amt8000Client._mask_bit(payload, _PGM_ON_OFFSET, i),
                tamper=Amt8000Client._mask_bit(payload, _PGM_TAMPER_OFFSET, i),
                low_battery=Amt8000Client._mask_bit(payload, _PGM_LOW_BATTERY_OFFSET, i),
                comm_fail=Amt8000Client._mask_bit(payload, _PGM_COMM_FAIL_OFFSET, i),
            )
            for i in indexes
            if 0 <= i < MAX_PGMS
        ]

    @staticmethod
    def _parse_sirens(payload: bytes, numbers: list[int]) -> list[Siren]:
        """Build recorded RF sirens; trouble bits from 0x0B4A SDK masks."""
        sirens: list[Siren] = []
        for number in numbers:
            if not 1 <= number <= MAX_SIRENS:
                continue
            index = number - 1
            sirens.append(
                Siren(
                    number=number,
                    enabled=True,
                    fault=Amt8000Client._mask_bit(
                        payload, _SIREN_FAULT_OFFSET, index
                    ),
                    tamper=Amt8000Client._mask_bit(
                        payload, _SIREN_TAMPER_OFFSET, index
                    ),
                    low_battery=Amt8000Client._mask_bit(
                        payload, _SIREN_LOW_BATTERY_OFFSET, index
                    ),
                )
            )
        return sirens
