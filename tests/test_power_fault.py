import unittest

from mcu import (
    build_modbus_read_holding,
    decode_power_fault_registers,
    parse_modbus_read_holding,
)


class PowerFaultTest(unittest.TestCase):
    def test_build_read_holding_frame(self) -> None:
        frame = build_modbus_read_holding(0x01, 0x0022, 1)
        self.assertEqual(frame.hex(), "0103002200012400")

    def test_parse_field_response(self) -> None:
        response = bytes.fromhex("01 03 04 5a 19 97 b5 96 ab")
        self.assertEqual(parse_modbus_read_holding(response, 0x01, 2), [0x5A19, 0x97B5])

    def test_decode_ac_failure(self) -> None:
        registers = {
            0x0002: 23065,
            0x0003: 38837,
            0x0005: 0,
            0x0006: 0,
            0x0008: 307,
            0x0009: 290,
            0x000A: 305,
            0x000B: 101,
            0x001C: 1,
            0x001D: 0,
            0x001E: 2000,
            0x001F: 0,
            0x0020: 24000,
            0x0021: 0,
            0x0022: 0,
            0x002E: 0,
            0x002F: 0,
            0x0030: 0,
            0x0031: 20,
            0x0032: 0,
            0x0033: 20,
            0x0034: 306,
            0x0035: 304,
            0x0036: 0,
            0x0037: 0,
            0x0038: 0x0400,
        }

        fault = decode_power_fault_registers(registers)

        self.assertTrue(fault["has_fault"])
        self.assertEqual(fault["input"]["ac_voltage_state"], "normal")
        self.assertEqual(fault["input"]["pfc_status_name"], "self_test_failed")
        self.assertEqual(fault["alarm"]["hex"], "0x00000400")
        self.assertEqual(fault["alarm"]["active_names"], ["ac_failure_alarm"])
        self.assertEqual(fault["control"]["set_voltage_mv"], 24000)
        self.assertEqual(fault["control"]["set_current_ma"], 2000)

    def test_reject_bad_crc(self) -> None:
        response = bytes.fromhex("01 03 04 5a 19 97 b5 00 00")
        with self.assertRaisesRegex(ValueError, "CRC"):
            parse_modbus_read_holding(response, 0x01, 2)


if __name__ == "__main__":
    unittest.main()
