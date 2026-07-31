import logging
import unittest

from mcu import (
    MOTOR_CAN_ID,
    MotorCanListener,
    close_homing_zero_verified,
)


class MotorHomingMonitorTest(unittest.TestCase):
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
