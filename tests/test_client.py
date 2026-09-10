import asyncio
import unittest

from custom_components.amt8000.client import Amt8000Client, BypassError


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

    def test_bypass_sends_each_zone_and_disconnects(self) -> None:
        client = Amt8000Client("127.0.0.1", 9009, "1234")
        writer = _Writer()
        disconnected = False

        async def connect_and_auth():
            return object(), writer

        async def read_frame(_reader):
            return bytes([0, 0, 0, 0, 0, 3, 0xF0, 0xFE, 0])

        async def disconnect(_writer):
            nonlocal disconnected
            disconnected = True

        client._connect_and_auth = connect_and_auth
        client._read_frame = read_frame
        client._disconnect = disconnect

        asyncio.run(client.bypass_zones([0, 29]))

        self.assertEqual(writer.packets[0][6:10], bytes([0x40, 0x1F, 0, 0x01]))
        self.assertEqual(writer.packets[1][6:10], bytes([0x40, 0x1F, 29, 0x01]))
        self.assertTrue(disconnected)
