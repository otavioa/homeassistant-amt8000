import unittest
from contextlib import redirect_stdout
from io import StringIO

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
        status = self.client._parse_status(bytes(self.payload), [0])
        data = amt8000_tool.status_to_dict(status)

        self.assertEqual(data["model"], "0x9A")
        self.assertEqual(data["firmware"], "4.1.2")
        self.assertNotIn("password", data)
        self.assertEqual(
            data["pgms"],
            [
                {"number": 1, "index": 0, "enabled": True, "on": False, "tamper": False, "low_battery": False, "comm_fail": False},
            ],
        )

    def test_status_print_includes_pgms(self) -> None:
        self.payload[137] = 0x01
        status = self.client._parse_status(bytes(self.payload), [0])
        output = StringIO()
        with redirect_stdout(output):
            amt8000_tool.print_status(status)
        text = output.getvalue()
        self.assertIn("PGMs:", text)
        self.assertIn("PGM 1: ligada", text)
        self.assertNotIn("PGM 2:", text)

    def test_status_print_includes_pgm_trouble_flags(self) -> None:
        self.payload[103] = 0x01
        self.payload[119] = 0x01
        self.payload[87] = 0x01
        status = self.client._parse_status(bytes(self.payload), [0])
        output = StringIO()
        with redirect_stdout(output):
            amt8000_tool.print_status(status)
        text = output.getvalue()
        self.assertIn("tamper", text)
        self.assertIn("bateria baixa", text)
        self.assertIn("falha rádio", text)

    def test_raw_status_dump_labels_pgm_on_mask(self) -> None:
        self.payload[137] = 0x01
        frame = self.client._packet([0x0B, 0x4A], list(self.payload))
        output = StringIO()
        with redirect_stdout(output):
            amt8000_tool.print_status_frame_details(frame)
        text = output.getvalue()
        self.assertIn("payload[137:138] PGMs ligadas", text)
        self.assertIn("-> 1", text)
        self.assertIn("payload[19]      não mapeado:", text)
        self.assertNotIn("candidato PGM", text)

    def test_invalid_frame_is_rejected(self) -> None:
        frame = self.client._packet([0x0B, 0x4A], list(self.payload))
        invalid_frame = frame[:-1] + bytes([frame[-1] ^ 0xFF])

        self.assertFalse(amt8000_tool.frame_is_valid(invalid_frame))

    def test_low_risk_probe_packets(self) -> None:
        mac_packet = self.client._packet(list(amt8000_tool.GET_MAC_COMMAND), [0x00])
        keep_alive_packet = self.client._packet(list(amt8000_tool.KEEP_ALIVE_COMMAND))

        self.assertEqual(mac_packet[6:9], bytes([0x3F, 0xAA, 0x00]))
        self.assertEqual(keep_alive_packet[6:8], bytes([0xF0, 0xF7]))

    def test_devices_command_packet_has_no_payload(self) -> None:
        packet = self.client._packet(list(amt8000_tool.DEVICES_COMMAND))

        self.assertEqual(packet[6:8], bytes([0x0B, 0x50]))
        self.assertEqual(int.from_bytes(packet[4:6], "big"), 2)

    def test_recorded_devices_decode_sdk_example(self) -> None:
        payload = bytes.fromhex(
            "03 00 00 00 00 00 00 00 00 00 00 00 00 0F 00 00 00 00 00 00 00 05 00 01 00 11 01 02 00"
        )
        decoded = amt8000_tool.decode_recorded_devices(payload)

        self.assertEqual(decoded["keyfobs"], [0, 1])
        self.assertEqual(decoded["sensors"], [1, 2, 3, 4])
        self.assertEqual(decoded["keypads"], [1, 3])
        self.assertEqual(decoded["sirens"], [1])
        self.assertEqual(decoded["repeaters"], [1])
        self.assertEqual(decoded["pgms"], [1, 5, 14])

    def test_recorded_devices_print_lists_pgms(self) -> None:
        payload = bytes.fromhex(
            "03 00 00 00 00 00 00 00 00 00 00 00 00 0F 00 00 00 00 00 00 00 05 00 01 00 11 01 02 00"
        )
        output = StringIO()
        with redirect_stdout(output):
            amt8000_tool.print_recorded_devices(payload)
        text = output.getvalue()
        self.assertIn("PGMs: 1, 5, 14", text)
        self.assertIn("sensores (zonas): 1, 2, 3, 4", text)

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

    def test_arm_parser_does_not_expose_force_mode(self) -> None:
        parser = amt8000_tool.build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["arm", "--partition", "1", "--mode", "force"])

    def test_bypass_clear_dry_run_uses_disable_flag(self) -> None:
        parser = amt8000_tool.build_parser()
        args = parser.parse_args(["bypass", "--zone", "3", "--clear"])
        self.assertTrue(args.clear)
        self.assertFalse(args.execute)

        packet = self.client._packet(list(amt8000_tool.BYPASS_COMMAND), [2, 0x00])
        self.assertEqual(packet[6:10], bytes([0x40, 0x1F, 2, 0x00]))
        self.assertIn("remover", amt8000_tool.describe_request(amt8000_tool.BYPASS_COMMAND, bytes([2, 0x00])))

    def test_auth_trace_masks_password_bytes(self) -> None:
        client = amt8000_tool.Amt8000Client("127.0.0.1", 9009, "1234")
        payload = [0x00] + client._encode_password("1234") + [0x10]
        frame = client._packet([0xF0, 0xF0], payload)
        output = StringIO()

        with redirect_stdout(output):
            amt8000_tool.print_auth_trace(frame, payload)

        self.assertIn("password: ** ** ** ** ** **", output.getvalue())
        self.assertNotIn("0A 0A 01 02 03 04", output.getvalue())


if __name__ == "__main__":
    unittest.main()
