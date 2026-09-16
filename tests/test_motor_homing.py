import logging
import threading
import unittest

from cli import build_parser, command_from_args
from daemon import dispatch
from mcu import (
    MOTOR_CAN_ID,
    MotorCanListener,
    close_homing_zero_verified,
)


class FakeMotorCanConfigurator:
    iface = "can-test"

    def __init__(self) -> None:
        self.calls = []

    def scan(self):
        self.calls.append(("scan", None))
        return {"ok": True, "iface": self.iface, "motor_id": 7, "motor_ids": [7]}

    def read_homing_config(self, motor_id=None):
        self.calls.append(("read", motor_id))
        return {"ok": True, "iface": self.iface, "motor_id": motor_id or 7, "config": {"mode": 2}}

    def apply_default_homing_config(self, motor_id=None):
        self.calls.append(("apply", motor_id))
        return {"ok": True, "iface": self.iface, "motor_id": motor_id or 7, "verified": True}


class FakeMotorCanClient:
    def __init__(self, configurator=None) -> None:
        self._lock = threading.RLock()
        self._motor_can_configurator = configurator
        self.logger = logging.getLogger("test-motor-can-daemon")


class FailingMotorCanConfigurator(FakeMotorCanConfigurator):
    def scan(self):
        raise OSError("CAN interface is down")


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


class MotorCanApiContractTest(unittest.TestCase):
    def test_cli_scan_read_and_auto_contract(self) -> None:
        parser = build_parser()

        scan = command_from_args(parser.parse_args(["motor", "scan"]))
        read_scan = command_from_args(parser.parse_args(["motor", "config", "read"]))
        read_id = command_from_args(parser.parse_args(["motor", "config", "read", "--id", "0x07"]))
        auto_id = command_from_args(parser.parse_args(["motor", "config", "auto", "--id", "7"]))

        self.assertEqual(scan, ("motor_can_scan", {}))
        self.assertEqual(read_scan, ("motor_homing_config_get", {}))
        self.assertEqual(read_id, ("motor_homing_config_get", {"motor_id": 7}))
        self.assertEqual(auto_id, ("motor_homing_config_apply", {"motor_id": 7}))

    def test_daemon_dispatch_uses_shared_configurator(self) -> None:
        configurator = FakeMotorCanConfigurator()
        client = FakeMotorCanClient(configurator)

        scan = dispatch(client, {"cmd": "motor_can_scan"})
        read = dispatch(client, {"cmd": "motor_homing_config_get", "args": {"motor_id": 7}})
        auto = dispatch(client, {"cmd": "motor_homing_config_apply", "args": {"motor_id": 7}})

        self.assertTrue(scan["ok"])
        self.assertEqual(read["motor_id"], 7)
        self.assertTrue(auto["verified"])
        self.assertEqual(configurator.calls, [("scan", None), ("read", 7), ("apply", 7)])

    def test_daemon_returns_json_error_when_configurator_is_unavailable(self) -> None:
        response = dispatch(FakeMotorCanClient(), {"cmd": "motor_homing_config_get"})

        self.assertFalse(response["ok"])
        self.assertEqual(response["transport"], "socketcan")
        self.assertIn("unavailable", response["error"])

    def test_daemon_converts_unexpected_can_exception_to_json_error(self) -> None:
        with self.assertLogs("test-motor-can-daemon", level="ERROR"):
            response = dispatch(
                FakeMotorCanClient(FailingMotorCanConfigurator()),
                {"cmd": "motor_can_scan"},
            )

        self.assertFalse(response["ok"])
        self.assertEqual(response["operation"], "scan")
        self.assertFalse(response["via_mcu"])
        self.assertIn("interface is down", response["error"])


if __name__ == "__main__":
    unittest.main()
