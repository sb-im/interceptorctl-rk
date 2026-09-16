import logging
import socket
import unittest

from motor_can import (
    ACK_OK,
    CAN_EFF_FLAG,
    CAN_EFF_MASK,
    CAN_FRAME_STRUCT,
    DEFAULT_HOMING_CONFIG,
    MotorCanConfigurator,
    SCAN_MOTOR_ID_MAX,
    SCAN_MOTOR_ID_MIN,
    _split_command_payload,
)


TIMEOUT = object()


def can_frame(ext_id: int, data: bytes) -> bytes:
    return CAN_FRAME_STRUCT.pack(
        ext_id | CAN_EFF_FLAG,
        len(data),
        data.ljust(8, b"\x00"),
    )


def response_packets(motor_id: int, logical: bytes) -> list[bytes]:
    base_id = motor_id << 8
    packets = [logical[:8]]
    offset = 8
    while offset < len(logical):
        chunk = logical[offset : offset + 7]
        packets.append(bytes((logical[0],)) + chunk)
        offset += len(chunk)
    return [can_frame(base_id | index, data) for index, data in enumerate(packets)]


CURRENT_LOGICAL = bytes.fromhex(
    "22 00 00 00 1E 00 00 27 10 01 2C 03 20 00 3C 00 6B"
)
DESIRED_LOGICAL = bytes.fromhex(
    "22 02 00 01 2C 00 01 D4 C0 00 50 07 D0 01 90 00 6B"
)
STATUS_DISABLED = can_frame(0x0100, bytes.fromhex("3A 02 6B"))
STATUS_ENABLED = can_frame(0x0100, bytes.fromhex("3A 03 6B"))
STATUS_DISABLED_7 = can_frame(0x0700, bytes.fromhex("3A 02 6B"))
WRITE_ACK = can_frame(0x0100, bytes((0x4C, ACK_OK, 0x6B)))
VERSION_ID_1 = can_frame(0x0100, bytes.fromhex("1F 00 07 13 14 6B"))
VERSION_ID_2 = can_frame(0x0200, bytes.fromhex("1F 00 07 13 14 6B"))
VERSION_ID_7 = can_frame(0x0700, bytes.fromhex("1F 00 07 13 14 6B"))
CHANGE_ID_ACK_OLD_7 = can_frame(0x0700, bytes((0xAE, ACK_OK, 0x6B)))


class FakeSocket:
    def __init__(self, receive_script: list[object]):
        self.receive_script = list(receive_script)
        self.sent: list[bytes] = []
        self.bound = None
        self.timeout = None
        self.closed = False

    def bind(self, address) -> None:
        self.bound = address

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def send(self, frame: bytes) -> int:
        self.sent.append(frame)
        return len(frame)

    def recv(self, _size: int) -> bytes:
        if not self.receive_script:
            raise socket.timeout()
        item = self.receive_script.pop(0)
        if item is TIMEOUT:
            raise socket.timeout()
        assert isinstance(item, bytes)
        return item

    def close(self) -> None:
        self.closed = True


def test_logger() -> logging.Logger:
    logger = logging.getLogger("test.motor_can")
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


def configurator(fake: FakeSocket) -> MotorCanConfigurator:
    return MotorCanConfigurator(
        "can0",
        test_logger(),
        timeout_s=0.1,
        scan_window_s=0.1,
        quiet_window_timeout_s=0.1,
        socket_factory=lambda *_args: fake,
    )


def unpack_sent(fake: FakeSocket) -> list[tuple[int, bytes]]:
    result = []
    for packed in fake.sent:
        raw_can_id, dlc, raw = CAN_FRAME_STRUCT.unpack(packed)
        result.append((raw_can_id & CAN_EFF_MASK, raw[:dlc]))
    return result


def expected_scan_queries() -> list[tuple[int, bytes]]:
    return [
        (motor_id << 8, bytes.fromhex("1F 6B"))
        for motor_id in range(SCAN_MOTOR_ID_MIN, SCAN_MOTOR_ID_MAX + 1)
    ]


class MotorCanPacketTest(unittest.TestCase):
    def test_factory_payload_uses_repeated_command_continuations(self) -> None:
        logical = DEFAULT_HOMING_CONFIG.to_write_payload(store=True)
        self.assertEqual(
            logical,
            bytes.fromhex(
                "4C AE 01 02 00 01 2C 00 01 D4 C0 00 50 07 D0 01 90 00 6B"
            ),
        )
        self.assertEqual(
            _split_command_payload(logical),
            [
                bytes.fromhex("4C AE 01 02 00 01 2C 00"),
                bytes.fromhex("4C 01 D4 C0 00 50 07 D0"),
                bytes.fromhex("4C 01 90 00 6B"),
            ],
        )

    def test_scan_requires_exactly_one_motor(self) -> None:
        fake = FakeSocket(
            [can_frame(0x0100, bytes.fromhex("1F 00 07 13 14 6B")), TIMEOUT]
        )
        result = configurator(fake).scan()

        self.assertTrue(result["ok"])
        self.assertEqual(result["motor_id"], 1)
        self.assertEqual(result["motor_ids"], [1])
        self.assertEqual(result["scan_method"], "targeted")
        self.assertEqual(result["scan_motor_id_min"], 1)
        self.assertEqual(result["scan_motor_id_max"], 32)
        self.assertEqual(unpack_sent(fake), expected_scan_queries())
        self.assertFalse(any(ext_id == 0 for ext_id, _ in unpack_sent(fake)))
        self.assertTrue(fake.closed)

    def test_scan_rejects_more_than_one_motor(self) -> None:
        fake = FakeSocket(
            [
                can_frame(0x0100, bytes.fromhex("1F 00 07 13 14 6B")),
                can_frame(0x0700, bytes.fromhex("1F 00 07 13 14 6B")),
                TIMEOUT,
            ]
        )
        result = configurator(fake).scan()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "multiple_motors_found")
        self.assertEqual(result["motor_ids"], [1, 7])

    def test_scan_detects_id_two_using_only_addressed_queries(self) -> None:
        fake = FakeSocket([VERSION_ID_2, TIMEOUT])

        result = configurator(fake).scan()

        self.assertTrue(result["ok"])
        self.assertEqual(result["motor_id"], 2)
        self.assertEqual(result["motor_ids"], [2])
        self.assertEqual(unpack_sent(fake), expected_scan_queries())
        self.assertFalse(any(ext_id == 0 for ext_id, _ in unpack_sent(fake)))

    def test_scan_ignores_response_outside_ids_one_through_32(self) -> None:
        fake = FakeSocket(
            [can_frame(0x2100, bytes.fromhex("1F 00 07 13 14 6B")), TIMEOUT]
        )

        result = configurator(fake).scan()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "motor_not_found")
        self.assertEqual(result["motor_ids"], [])
        self.assertEqual(unpack_sent(fake), expected_scan_queries())

    def test_read_reassembles_repeated_command_packets(self) -> None:
        fake = FakeSocket(
            [STATUS_DISABLED, TIMEOUT, *response_packets(1, CURRENT_LOGICAL)]
        )
        result = configurator(fake).read_homing_config(1)

        self.assertTrue(result["ok"])
        self.assertTrue(result["poll_boundary_observed"])
        self.assertFalse(result["driver_enabled"])
        self.assertEqual(
            result["config"],
            {
                "mode": 0,
                "mode_name": "single_turn_nearest",
                "direction": 0,
                "direction_name": "CW",
                "homing_speed_rpm": 30,
                "homing_timeout_ms": 10000,
                "collision_speed_rpm": 300,
                "collision_current_ma": 800,
                "collision_time_ms": 60,
                "power_on_auto_homing": False,
            },
        )
        self.assertEqual(
            unpack_sent(fake), [(0x0100, bytes.fromhex("22 6B"))]
        )

    def test_read_ignores_malformed_long_status_frame(self) -> None:
        malformed_status = can_frame(0x0100, bytes.fromhex("3A 02 00 6B"))
        fake = FakeSocket(
            [
                malformed_status,
                STATUS_DISABLED,
                TIMEOUT,
                *response_packets(1, CURRENT_LOGICAL),
            ]
        )

        result = configurator(fake).read_homing_config(1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["motor_status_raw"], 0x02)

    def test_read_does_not_treat_field_value_6b_as_early_terminator(self) -> None:
        logical = bytes.fromhex(
            "22 00 00 00 1E 00 00 6B 10 01 2C 03 20 00 3C 00 6B"
        )
        fake = FakeSocket([STATUS_DISABLED, TIMEOUT, *response_packets(1, logical)])

        result = configurator(fake).read_homing_config(1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["config"]["homing_timeout_ms"], 0x00006B10)

    def test_read_rejects_out_of_order_fragment(self) -> None:
        packets = response_packets(1, CURRENT_LOGICAL)
        fake = FakeSocket([STATUS_DISABLED, TIMEOUT, packets[0], packets[2]])
        result = configurator(fake).read_homing_config(1)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "read_homing_packet_order")
        self.assertEqual(result["expected_packet"], 1)
        self.assertEqual(result["actual_packet"], 2)

    def test_read_rejects_document_style_continuation_without_command(self) -> None:
        packets = response_packets(1, CURRENT_LOGICAL)
        wrong_packet_one = can_frame(0x0101, bytes.fromhex("10 01 2C 03 20 00 3C 00"))
        fake = FakeSocket([STATUS_DISABLED, TIMEOUT, packets[0], wrong_packet_one])
        result = configurator(fake).read_homing_config(1)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "read_homing_continuation_code")


class MotorCanApplyTest(unittest.TestCase):
    def test_apply_waits_for_disabled_poll_then_ack_and_strict_readback(self) -> None:
        fake = FakeSocket(
            [
                VERSION_ID_1,
                TIMEOUT,
                STATUS_DISABLED,
                TIMEOUT,
                *response_packets(1, CURRENT_LOGICAL),
                WRITE_ACK,
                STATUS_DISABLED,
                TIMEOUT,
                *response_packets(1, DESIRED_LOGICAL),
            ]
        )
        result = configurator(fake).apply_default_homing_config(1)

        self.assertTrue(result["ok"])
        self.assertTrue(result["verified"])
        self.assertTrue(result["requested_store"])
        self.assertFalse(result["store_persistence_verifiable"])
        self.assertEqual(result["ack"]["result"], ACK_OK)
        self.assertFalse(result["driver_enabled"])
        self.assertTrue(result["readback_poll_boundary"]["observed"])
        self.assertEqual(result["after"], result["desired"])

        sent = unpack_sent(fake)
        self.assertEqual(
            sent,
            [
                *expected_scan_queries(),
                (0x0100, bytes.fromhex("22 6B")),
                (0x0100, bytes.fromhex("4C AE 01 02 00 01 2C 00")),
                (0x0101, bytes.fromhex("4C 01 D4 C0 00 50 07 D0")),
                (0x0102, bytes.fromhex("4C 01 90 00 6B")),
                (0x0100, bytes.fromhex("22 6B")),
            ],
        )
        self.assertTrue(all(data[0] in (0x1F, 0x22, 0x4C) for _, data in sent))
        self.assertFalse(result["motor_id_changed"])
        self.assertTrue(result["motor_id_verified"])
        self.assertFalse(any(data[0] == 0xAE for _, data in sent))
        self.assertTrue(any(frame["direction"] == "rx" for frame in result["frames"]))

    def test_apply_refuses_to_write_while_driver_is_enabled(self) -> None:
        fake = FakeSocket([VERSION_ID_1, TIMEOUT, STATUS_ENABLED])
        result = configurator(fake).apply_default_homing_config(1)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "driver_enabled")
        self.assertTrue(result["driver_enabled"])
        self.assertEqual(result["motor_status_raw"], 0x03)
        self.assertEqual(unpack_sent(fake), expected_scan_queries())

    def test_apply_can_use_confirmed_silent_bus_without_mcu_poll(self) -> None:
        fake = FakeSocket(
            [
                VERSION_ID_1,
                TIMEOUT,
                TIMEOUT,
                TIMEOUT,
                *response_packets(1, CURRENT_LOGICAL),
                WRITE_ACK,
                TIMEOUT,
                TIMEOUT,
                *response_packets(1, DESIRED_LOGICAL),
            ]
        )
        result = configurator(fake).apply_default_homing_config(1)

        self.assertTrue(result["ok"])
        self.assertFalse(result["poll_boundary_observed"])
        self.assertIsNone(result["motor_status_raw"])
        self.assertIsNone(result["driver_enabled"])
        self.assertTrue(result["mcu_poll_boundary"]["bus_silence_confirmed"])

    def test_acknowledged_but_different_readback_is_failure(self) -> None:
        fake = FakeSocket(
            [
                VERSION_ID_1,
                TIMEOUT,
                STATUS_DISABLED,
                TIMEOUT,
                *response_packets(1, CURRENT_LOGICAL),
                WRITE_ACK,
                STATUS_DISABLED,
                TIMEOUT,
                *response_packets(1, CURRENT_LOGICAL),
            ]
        )
        result = configurator(fake).apply_default_homing_config(1)

        self.assertFalse(result["ok"])
        self.assertFalse(result["verified"])
        self.assertEqual(result["error_code"], "readback_mismatch")
        self.assertEqual(result["ack"]["result"], ACK_OK)
        self.assertIn("mode", result["mismatches"])

    def test_apply_changes_non_factory_id_then_rescans_before_configuration(self) -> None:
        fake = FakeSocket(
            [
                VERSION_ID_7,
                TIMEOUT,
                STATUS_DISABLED_7,
                TIMEOUT,
                CHANGE_ID_ACK_OLD_7,
                VERSION_ID_1,
                TIMEOUT,
                STATUS_DISABLED,
                TIMEOUT,
                *response_packets(1, CURRENT_LOGICAL),
                WRITE_ACK,
                STATUS_DISABLED,
                TIMEOUT,
                *response_packets(1, DESIRED_LOGICAL),
            ]
        )

        result = configurator(fake).apply_default_homing_config()

        self.assertTrue(result["ok"])
        self.assertEqual(result["original_motor_id"], 7)
        self.assertEqual(result["motor_id"], 1)
        self.assertTrue(result["motor_id_changed"])
        self.assertTrue(result["motor_id_verified"])
        self.assertTrue(result["id_change"]["verified"])
        self.assertEqual(result["id_change_ack"]["response_motor_id"], 7)
        self.assertEqual(result["post_change_motor_ids"], [1])

        sent = unpack_sent(fake)
        self.assertEqual(
            sent,
            [
                *expected_scan_queries(),
                (0x0700, bytes.fromhex("3A 6B")),
                (0x0700, bytes.fromhex("AE 4B 01 01 6B")),
                *expected_scan_queries(),
                (0x0100, bytes.fromhex("22 6B")),
                (0x0100, bytes.fromhex("4C AE 01 02 00 01 2C 00")),
                (0x0101, bytes.fromhex("4C 01 D4 C0 00 50 07 D0")),
                (0x0102, bytes.fromhex("4C 01 90 00 6B")),
                (0x0100, bytes.fromhex("22 6B")),
            ],
        )

    def test_apply_fails_closed_when_id_change_ack_was_lost(self) -> None:
        fake = FakeSocket(
            [
                VERSION_ID_7,
                TIMEOUT,
                STATUS_DISABLED_7,
                TIMEOUT,
                TIMEOUT,
                VERSION_ID_1,
                TIMEOUT,
            ]
        )

        result = configurator(fake).apply_default_homing_config()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "change_id_ack_timeout")
        self.assertTrue(result["id_change"]["ack_timeout"])
        self.assertEqual(result["id_change"]["motor_ids"], [1])
        self.assertFalse(any(data[0] == 0x4C for _, data in unpack_sent(fake)))

    def test_apply_stops_when_id_change_is_rejected(self) -> None:
        reject = can_frame(0x0700, bytes.fromhex("AE E2 6B"))
        fake = FakeSocket(
            [VERSION_ID_7, TIMEOUT, STATUS_DISABLED_7, TIMEOUT, reject]
        )

        result = configurator(fake).apply_default_homing_config()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "change_id_rejected")
        sent = unpack_sent(fake)
        self.assertEqual(sent[-1], (0x0700, bytes.fromhex("AE 4B 01 01 6B")))
        self.assertFalse(any(data[0] == 0x4C for _, data in sent))

    def test_apply_does_not_change_id_when_old_id_driver_is_enabled(self) -> None:
        status_enabled_7 = can_frame(0x0700, bytes.fromhex("3A 03 6B"))
        fake = FakeSocket([VERSION_ID_7, TIMEOUT, status_enabled_7])

        result = configurator(fake).apply_default_homing_config()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "driver_enabled_before_id_change")
        self.assertTrue(result["driver_enabled"])
        sent = unpack_sent(fake)
        self.assertEqual(
            sent,
            [
                *expected_scan_queries(),
                (0x0700, bytes.fromhex("3A 6B")),
            ],
        )
        self.assertFalse(any(data[0] in (0xAE, 0x4C) for _, data in sent))

    def test_apply_does_not_change_id_when_old_id_status_times_out(self) -> None:
        fake = FakeSocket([VERSION_ID_7, TIMEOUT, TIMEOUT])

        result = configurator(fake).apply_default_homing_config()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "read_status_timeout")
        sent = unpack_sent(fake)
        self.assertFalse(any(data[0] in (0xAE, 0x4C) for _, data in sent))

    def test_apply_stops_when_post_change_scan_does_not_find_id_one(self) -> None:
        fake = FakeSocket(
            [
                VERSION_ID_7,
                TIMEOUT,
                STATUS_DISABLED_7,
                TIMEOUT,
                CHANGE_ID_ACK_OLD_7,
                VERSION_ID_7,
                TIMEOUT,
            ]
        )

        result = configurator(fake).apply_default_homing_config()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "change_id_verification_failed")
        self.assertEqual(result["motor_ids"], [7])
        self.assertFalse(any(data[0] == 0x4C for _, data in unpack_sent(fake)))

    def test_apply_stops_when_changed_motor_is_enabled_at_id_one(self) -> None:
        fake = FakeSocket(
            [
                VERSION_ID_7,
                TIMEOUT,
                STATUS_DISABLED_7,
                TIMEOUT,
                CHANGE_ID_ACK_OLD_7,
                VERSION_ID_1,
                TIMEOUT,
                STATUS_ENABLED,
            ]
        )

        result = configurator(fake).apply_default_homing_config()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "driver_enabled")
        self.assertTrue(result["motor_id_changed"])
        self.assertFalse(any(data[0] == 0x4C for _, data in unpack_sent(fake)))

    def test_apply_ignores_recovery_traffic_until_full_silence(self) -> None:
        position_query = can_frame(0x0100, bytes.fromhex("36 6B"))
        position_reply = can_frame(0x0100, bytes.fromhex("36 00 00 00 00 00 6B"))
        fake = FakeSocket(
            [
                VERSION_ID_1,
                TIMEOUT,
                STATUS_DISABLED,
                position_query,
                position_reply,
                TIMEOUT,
                *response_packets(1, CURRENT_LOGICAL),
                WRITE_ACK,
                STATUS_DISABLED,
                TIMEOUT,
                *response_packets(1, DESIRED_LOGICAL),
            ]
        )

        result = configurator(fake).apply_default_homing_config()

        self.assertTrue(result["ok"])
        first_read_index = next(
            index
            for index, frame in enumerate(result["frames"])
            if frame["direction"] == "tx" and frame["data_hex"] == "22 6B"
        )
        recovery_reply_index = next(
            index
            for index, frame in enumerate(result["frames"])
            if frame["direction"] == "rx" and frame["data_hex"] == "36 00 00 00 00 00 6B"
        )
        self.assertLess(recovery_reply_index, first_read_index)

    def test_apply_scans_even_with_explicit_expected_id(self) -> None:
        fake = FakeSocket([VERSION_ID_7, TIMEOUT])

        result = configurator(fake).apply_default_homing_config(1)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "requested_motor_id_mismatch")
        self.assertEqual(result["requested_motor_id"], 1)
        self.assertEqual(result["detected_motor_id"], 7)
        self.assertEqual(unpack_sent(fake), expected_scan_queries())

    def test_apply_never_writes_when_initial_scan_finds_multiple_motors(self) -> None:
        fake = FakeSocket([VERSION_ID_1, VERSION_ID_7, TIMEOUT])

        result = configurator(fake).apply_default_homing_config()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "multiple_motors_found")
        self.assertEqual(result["motor_ids"], [1, 7])
        sent = unpack_sent(fake)
        self.assertEqual(sent, expected_scan_queries())


if __name__ == "__main__":
    unittest.main()
