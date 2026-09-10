import unittest

import amt8000_tool


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = amt8000_tool.Amt8000Client("127.0.0.1", 9009, "")
        self.payload = bytearray(143)
        self.payload[0] = 0x9A
        self.payload[1:4] = bytes([4, 1, 2])
        self.payload[12] = 0x01  # zona 1 habilitada
        self.payload[38] = 0x01  # zona 1 aberta
        self.payload[21] = 0x81  # partição 0 habilitada e armada

    def test_status_frame_validation_and_decoding(self) -> None:
        frame = self.client._packet([0x0B, 0x4A], list(self.payload))
        status = self.client._parse_status(frame[8:-1])

        self.assertTrue(amt8000_tool.frame_is_valid(frame))
        self.assertEqual(status.model, 0x9A)
        self.assertEqual(status.version, "4.1.2")
        self.assertEqual(status.zones[0].number, 1)
        self.assertTrue(status.zones[0].open)
        self.assertEqual(status.partitions[0].index, 0)

    def test_status_json_is_safe_and_uses_hex_model(self) -> None:
        status = self.client._parse_status(bytes(self.payload))
        data = amt8000_tool.status_to_dict(status)

        self.assertEqual(data["model"], "0x9A")
        self.assertEqual(data["firmware"], "4.1.2")
        self.assertNotIn("password", data)

    def test_invalid_frame_is_rejected(self) -> None:
        frame = self.client._packet([0x0B, 0x4A], list(self.payload))
        invalid_frame = frame[:-1] + bytes([frame[-1] ^ 0xFF])

        self.assertFalse(amt8000_tool.frame_is_valid(invalid_frame))

    def test_low_risk_probe_packets(self) -> None:
        mac_packet = self.client._packet(list(amt8000_tool.GET_MAC_COMMAND), [0x00])
        keep_alive_packet = self.client._packet(list(amt8000_tool.KEEP_ALIVE_COMMAND))

        self.assertEqual(mac_packet[6:9], bytes([0x3F, 0xAA, 0x00]))
        self.assertEqual(keep_alive_packet[6:8], bytes([0xF0, 0xF7]))

    def test_mac_response_is_decoded_from_response_payload(self) -> None:
        response = self.client._packet(
            list(amt8000_tool.GET_MAC_COMMAND),
            [0x00, 0x10, 0x22, 0x33, 0x44, 0x55, 0x66],
        )

        self.assertEqual(amt8000_tool.decode_mac_response(response), "10:22:33:44:55:66")

    def test_extended_command_payloads(self) -> None:
        arm_stay = self.client._packet(list(amt8000_tool.ARM_COMMAND), [1, 0x02])
        panic = self.client._packet(list(amt8000_tool.PANIC_COMMAND), [0x03])
        siren_off = self.client._packet(list(amt8000_tool.SIREN_OFF_COMMAND))
        pgm = self.client._packet(list(amt8000_tool.PGM_COMMAND), [2, 0x01])

        self.assertEqual(arm_stay[6:10], bytes([0x40, 0x1E, 1, 0x02]))
        self.assertEqual(panic[6:9], bytes([0x40, 0x1A, 0x03]))
        self.assertEqual(siren_off[6:8], bytes([0x40, 0x19]))
        self.assertEqual(pgm[6:10], bytes([0x45, 0xAF, 2, 0x01]))

    def test_request_description_decodes_control_payloads(self) -> None:
        self.assertIn("armar stay", amt8000_tool.describe_request(amt8000_tool.ARM_COMMAND, bytes([1, 2])))
        self.assertIn("tipo=médico", amt8000_tool.describe_request(amt8000_tool.PANIC_COMMAND, bytes([3])))
        self.assertIn("estado=ligado", amt8000_tool.describe_request(amt8000_tool.PGM_COMMAND, bytes([2, 1])))


if __name__ == "__main__":
    unittest.main()
