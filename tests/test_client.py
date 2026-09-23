import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path


def _load_client_module():
    client_path = Path(__file__).resolve().parents[1] / "custom_components" / "amt8000" / "client.py"
    spec = importlib.util.spec_from_file_location("amt8000_client_under_test", client_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {client_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


client_mod = _load_client_module()
Amt8000Client = client_mod.Amt8000Client
BypassError = client_mod.BypassError
PgmError = client_mod.PgmError
SirenError = client_mod.SirenError


class _Writer:
    def __init__(self) -> None:
        self.packets: list[bytes] = []

    def write(self, packet: bytes) -> None:
        self.packets.append(packet)

    async def drain(self) -> None:
        return None


class ClientTests(unittest.TestCase):
    def test_bypass_packet_uses_zero_based_zone_and_enable_flag(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        packet = client._packet([0x40, 0x1F], [29, 0x01])
        self.assertEqual(packet[6:10], bytes([0x40, 0x1F, 29, 0x01]))

    def test_bypass_rejects_invalid_zone_before_connecting(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        with self.assertRaises(BypassError):
            asyncio.run(client.bypass_zones([56]))

    def _stub_command_io(
        self, client: Amt8000Client, response: bytes | list[bytes] | None = None
    ) -> _Writer:
        writer = _Writer()
        client._disconnected = False
        if isinstance(response, list):
            frames: list[bytes] | None = list(response)
            repeating = None
        else:
            frames = None
            repeating = response or bytes([0, 0, 0, 0, 0, 3, 0xF0, 0xFE, 0])

        async def connect_and_auth():
            return object(), writer

        async def read_frame(_reader):
            if frames is not None:
                if not frames:
                    raise AssertionError("unexpected extra read")
                return frames.pop(0)
            return repeating

        async def disconnect(_writer):
            client._disconnected = True

        client._connect_and_auth = connect_and_auth
        client._read_frame = read_frame
        client._disconnect = disconnect
        return writer

    def test_bypass_sends_each_zone_and_disconnects(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        writer = self._stub_command_io(client)

        asyncio.run(client.bypass_zones([0, 29]))

        self.assertEqual(writer.packets[0][6:10], bytes([0x40, 0x1F, 0, 0x01]))
        self.assertEqual(writer.packets[1][6:10], bytes([0x40, 0x1F, 29, 0x01]))
        self.assertTrue(client._disconnected)

    def test_bypass_clear_sends_disable_flag(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        writer = self._stub_command_io(client)

        asyncio.run(client.bypass_zones([3], enabled=False))

        self.assertEqual(writer.packets[0][6:10], bytes([0x40, 0x1F, 3, 0x00]))
        self.assertTrue(client._disconnected)

    def test_pgm_packet_uses_index_and_state_flag(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        packet = client._packet([0x45, 0xAF], [2, 0x01])
        self.assertEqual(packet[6:10], bytes([0x45, 0xAF, 2, 0x01]))

    def test_pgm_rejects_invalid_index_before_connecting(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        with self.assertRaises(PgmError):
            asyncio.run(client.set_pgm(16, True))

    def test_pgm_sends_on_and_disconnects(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        writer = self._stub_command_io(client)

        asyncio.run(client.set_pgm(0, True))

        self.assertEqual(writer.packets[0][6:10], bytes([0x45, 0xAF, 0, 0x01]))
        self.assertTrue(client._disconnected)

    def test_pgm_accepts_index_15(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        writer = self._stub_command_io(client)

        asyncio.run(client.set_pgm(15, True))

        self.assertEqual(writer.packets[0][6:10], bytes([0x45, 0xAF, 15, 0x01]))

    def test_pgm_off_sends_disable_flag(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        writer = self._stub_command_io(client)

        asyncio.run(client.set_pgm(1, False))

        self.assertEqual(writer.packets[0][6:10], bytes([0x45, 0xAF, 1, 0x00]))
        self.assertTrue(client._disconnected)

    def test_pgm_nack_raises(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        self._stub_command_io(client, bytes([0, 0, 0, 0, 0, 3, 0xF0, 0xFD, 0xE6]))

        with self.assertRaises(PgmError):
            asyncio.run(client.set_pgm(0, True))

    def test_parse_pgms_without_indexes_is_empty(self) -> None:
        payload = bytearray(143)
        pgms = Amt8000Client._parse_pgms(bytes(payload), [])
        self.assertEqual(pgms, [])

    def test_recorded_pgm_indexes_from_live_capture(self) -> None:
        payload = bytes.fromhex(
            "02 00 00 00 00 00 00 00 00 00 00 00 00 FF 00 00 00 00 00 00 00 00 00 01 00 10 00 00 00"
        )
        self.assertEqual(Amt8000Client.recorded_pgm_indexes(payload), [0])

    def test_recorded_pgm_indexes_from_sdk_example(self) -> None:
        payload = bytes.fromhex(
            "03 00 00 00 00 00 00 00 00 00 00 00 00 0F 00 00 00 00 00 00 00 05 00 01 00 11 01 02 00"
        )
        self.assertEqual(Amt8000Client.recorded_pgm_indexes(payload), [0, 4, 13])

    def test_recorded_pgm_indexes_short_payload_is_empty(self) -> None:
        self.assertEqual(Amt8000Client.recorded_pgm_indexes(b"\x00" * 25), [])

    def test_parse_pgms_reads_on_mask_at_137(self) -> None:
        payload = bytearray(143)
        payload[137] = 0x01
        pgms = Amt8000Client._parse_pgms(bytes(payload), [0])
        self.assertEqual([(pgm.index, pgm.on) for pgm in pgms], [(0, True)])

    def test_parse_pgms_reads_on_mask_at_138(self) -> None:
        payload = bytearray(143)
        payload[138] = 0x01
        self.assertTrue(Amt8000Client._mask_bit(bytes(payload), 137, 8))
        self.assertFalse(Amt8000Client._mask_bit(bytes(payload), 137, 0))
        pgms = Amt8000Client._parse_pgms(bytes(payload), [8])
        self.assertEqual([(pgm.index, pgm.on) for pgm in pgms], [(8, True)])

    def test_parse_pgms_ignores_on_bit_when_not_recorded(self) -> None:
        payload = bytearray(143)
        payload[137] = 0x01
        payload[138] = 0x01
        pgms = Amt8000Client._parse_pgms(bytes(payload), [0])
        self.assertEqual([(pgm.index, pgm.on) for pgm in pgms], [(0, True)])

    def test_parse_pgms_trouble_bits_default_false(self) -> None:
        payload = bytearray(143)
        pgm = Amt8000Client._parse_pgms(bytes(payload), [0])[0]
        self.assertFalse(pgm.tamper)
        self.assertFalse(pgm.low_battery)
        self.assertFalse(pgm.comm_fail)

    def test_parse_pgms_reads_trouble_masks_for_index_0(self) -> None:
        payload = bytearray(143)
        payload[87] = 0x01
        payload[103] = 0x01
        payload[119] = 0x01
        pgm = Amt8000Client._parse_pgms(bytes(payload), [0])[0]
        self.assertTrue(pgm.comm_fail)
        self.assertTrue(pgm.tamper)
        self.assertTrue(pgm.low_battery)

    def test_parse_pgms_reads_trouble_masks_for_index_8(self) -> None:
        payload = bytearray(143)
        payload[88] = 0x01
        payload[104] = 0x01
        payload[120] = 0x01
        pgm = Amt8000Client._parse_pgms(bytes(payload), [8])[0]
        self.assertEqual(pgm.index, 8)
        self.assertTrue(pgm.comm_fail)
        self.assertTrue(pgm.tamper)
        self.assertTrue(pgm.low_battery)
        healthy = Amt8000Client._parse_pgms(bytes(payload), [0])[0]
        self.assertFalse(healthy.comm_fail)
        self.assertFalse(healthy.tamper)
        self.assertFalse(healthy.low_battery)

    def test_get_status_sends_devices_command_and_filters_pgms(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        status_payload = bytearray(143)
        status_payload[137] = 0x01
        devices_payload = bytes.fromhex(
            "02 00 00 00 00 00 00 00 00 00 00 00 00 FF 00 00 00 00 00 00 00 00 00 01 00 10 00 00 00"
        )
        writer = self._stub_command_io(
            client,
            [
                client._packet([0x0B, 0x4A], list(status_payload)),
                client._packet([0x0B, 0x50], list(devices_payload)),
            ],
        )

        status = asyncio.run(client.get_status())

        self.assertEqual(writer.packets[0][6:8], bytes([0x0B, 0x4A]))
        self.assertEqual(writer.packets[1][6:8], bytes([0x0B, 0x50]))
        self.assertEqual([(pgm.index, pgm.on) for pgm in status.pgms], [(0, True)])
        self.assertEqual([s.number for s in status.sirens], [1])
        self.assertTrue(client._disconnected)

    def test_get_status_nack_on_devices_yields_no_pgms(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        status_payload = bytearray(143)
        status_payload[137] = 0x01
        self._stub_command_io(
            client,
            [
                client._packet([0x0B, 0x4A], list(status_payload)),
                client._packet([0xF0, 0xFD], [0x00]),
            ],
        )

        status = asyncio.run(client.get_status())

        self.assertEqual(status.pgms, [])
        self.assertEqual(status.sirens, [])

    def test_recorded_siren_numbers_from_live_capture(self) -> None:
        payload = bytes.fromhex(
            "02 00 00 00 00 00 00 00 00 00 00 00 00 FF 00 00 00 00 00 00 00 00 00 01 00 10 00 00 00"
        )
        self.assertEqual(Amt8000Client.recorded_siren_numbers(payload), [1])

    def test_recorded_siren_numbers_short_payload_is_empty(self) -> None:
        self.assertEqual(Amt8000Client.recorded_siren_numbers(b"\x00" * 23), [])

    def test_parse_sirens_trouble_bits(self) -> None:
        payload = bytearray(143)
        payload[83] = 0x01
        payload[99] = 0x01
        payload[115] = 0x01
        siren = Amt8000Client._parse_sirens(bytes(payload), [1])[0]
        self.assertEqual(siren.number, 1)
        self.assertTrue(siren.fault)
        self.assertTrue(siren.tamper)
        self.assertTrue(siren.low_battery)

    def test_siren_off_sends_command(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        writer = self._stub_command_io(client)

        asyncio.run(client.siren_off())

        self.assertEqual(writer.packets[0][6:8], bytes([0x40, 0x19]))
        self.assertTrue(client._disconnected)

    def test_panic_sends_type_byte(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        writer = self._stub_command_io(client)

        asyncio.run(client.panic("medical"))

        self.assertEqual(writer.packets[0][6:9], bytes([0x40, 0x1A, 0x03]))
        self.assertTrue(client._disconnected)

    def test_panic_rejects_invalid_type(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        with self.assertRaises(SirenError):
            asyncio.run(client.panic("unknown"))

    def test_siren_nack_raises(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        self._stub_command_io(client, bytes([0, 0, 0, 0, 0, 3, 0xF0, 0xFD, 0xE6]))

        with self.assertRaises(SirenError):
            asyncio.run(client.siren_off())


if __name__ == "__main__":
    unittest.main()
