import logging
import unittest

from mcu import (
    MOTOR_CAN_ID,
    MotorCanListener,
    close_homing_zero_verified,
)


class MotorHomingMonitorTest(unittest.TestCase):
    def test_listener_treats_live_and_latched_stall_as_unsafe(self) -> None:
        for status in (0x04, 0x08, 0x0C):
            with self.subTest(status=status):
                listener = MotorCanListener("can0", logging.getLogger("test"))
                self.assertTrue(
                    listener._handle_frame(
                        MOTOR_CAN_ID,
                        3,
                        bytes((0x3A, status, 0x6B, 0, 0, 0, 0, 0)),
                        now=10.0,
                    )
                )
                self.assertTrue(listener.snapshot()["driver_stall"])

        listener = MotorCanListener("can0", logging.getLogger("test"))
        listener._handle_frame(
            MOTOR_CAN_ID,
            3,
            bytes.fromhex("3a 00 6b 00 00 00 00 00"),
            now=10.0,
        )
        self.assertFalse(listener.snapshot()["driver_stall"])

    def test_listener_records_zero_ack_and_following_position(self) -> None:
        listener = MotorCanListener("can0", logging.getLogger("test"))

        self.assertTrue(
            listener._handle_frame(
                MOTOR_CAN_ID,
                3,
                bytes.fromhex("0a 02 6b 00 00 00 00 00"),
                now=10.0,
            )
        )
        self.assertTrue(
            listener._handle_frame(
                MOTOR_CAN_ID,
                7,
                bytes.fromhex("36 00 00 00 00 05 6b 00"),
                now=10.1,
            )
        )
        self.assertTrue(
            listener._handle_frame(
                MOTOR_CAN_ID,
                3,
                bytes.fromhex("3a 02 6b 00 00 00 00 00"),
                now=10.2,
            )
        )

        snapshot = listener.snapshot()
        self.assertEqual(snapshot["zeroed_seen_time"], 10.0)
        self.assertEqual(snapshot["position_seen_time"], 10.1)
        self.assertEqual(snapshot["status_seen_time"], 10.2)
        self.assertEqual(snapshot["position"], 5)
        self.assertFalse(snapshot["driver_enabled"])
        self.assertEqual(snapshot["zeroed_rx_count"], 1)

    def test_completion_requires_fresh_zero_and_fresh_position(self) -> None:
        base = {
            "zeroed_seen_time": 10.0,
            "position_seen_time": 10.1,
            "status_seen_time": 10.2,
            "position": 5,
            "driver_enabled": False,
        }
        self.assertTrue(close_homing_zero_verified(base, 9.0))

        stale_zero = dict(base, zeroed_seen_time=8.0)
        self.assertFalse(close_homing_zero_verified(stale_zero, 9.0))

        stale_position = dict(base, position_seen_time=9.9)
        self.assertFalse(close_homing_zero_verified(stale_position, 9.0))

        stale_status = dict(base, status_seen_time=10.05)
        self.assertFalse(close_homing_zero_verified(stale_status, 9.0))

        still_enabled = dict(base, driver_enabled=True)
        self.assertFalse(close_homing_zero_verified(still_enabled, 9.0))

        outside_tolerance = dict(base, position=21)
        self.assertFalse(close_homing_zero_verified(outside_tolerance, 9.0))


if __name__ == "__main__":
    unittest.main()
