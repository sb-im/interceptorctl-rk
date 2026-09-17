import logging
import tempfile
import threading
import unittest
from pathlib import Path

from daemon import ManualOpenAngleController, dispatch
from mcu import (
    CMD_ID_INTERCEPTOR_DOOR_OPEN,
    CMD_SET_INTERCEPTOR,
    DOOR_OPEN_POSITION_90_0P1DEG,
    DOOR_OPEN_POSITION_120_0P1DEG,
    McuClient,
)


class DoorOpenProtocolTest(unittest.TestCase):
    def make_stubbed_client(self):
        client = object.__new__(McuClient)
        captured = {}

        def interceptor_cmd(name, cmd_id, wait, timeout, target_position=None):
            captured.update(
                name=name,
                cmd_id=cmd_id,
                wait=wait,
                timeout=timeout,
                target_position=target_position,
            )
            return {
                "ok": True,
                "accepted": True,
                "target_position": target_position,
            }

        client.interceptor_cmd = interceptor_cmd
        return client, captured

    def make_protocol_client(self, final_event=None):
        client = object.__new__(McuClient)
        client._lock = threading.RLock()
        client._last_motor_target = None
        captured = {}

        def transact(name, cmd_set, cmd_id, payload=b"", timeout=6.0):
            captured.update(
                name=name,
                cmd_set=cmd_set,
                cmd_id=cmd_id,
                payload=payload,
                ack_timeout=timeout,
            )
            return {"ok": True, "result": 0, "data": b"\x00"}

        client.transact = transact
        client._begin_motion = lambda *args, **kwargs: 17
        client.wait_motion_final = lambda motion_id, timeout: final_event
        return client, captured

    def test_90_degree_open_uses_mcu_door_command_and_90_degree_target(self) -> None:
        client, captured = self.make_stubbed_client()

        response = client.door_open(
            wait=True,
            timeout=12.5,
            angle_deg=90,
            firmware_version_code=0x003F,
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["applied_angle_deg"], 90)
        self.assertEqual(captured["cmd_id"], CMD_ID_INTERCEPTOR_DOOR_OPEN)
        self.assertEqual(captured["target_position"], DOOR_OPEN_POSITION_90_0P1DEG)
        self.assertTrue(captured["wait"])
        self.assertEqual(captured["timeout"], 12.5)

    def test_120_degree_open_uses_mcu_door_command_and_120_degree_target(self) -> None:
        client, captured = self.make_stubbed_client()

        response = client.door_open(angle_deg=120, firmware_version_code=0x003F)

        self.assertTrue(response["ok"])
        self.assertEqual(response["applied_angle_deg"], 120)
        self.assertEqual(captured["cmd_id"], CMD_ID_INTERCEPTOR_DOOR_OPEN)
        self.assertEqual(captured["target_position"], DOOR_OPEN_POSITION_120_0P1DEG)

    def test_open_frame_is_interceptor_command_one_with_empty_payload(self) -> None:
        event = {"ok": True, "type": "motion_reached", "final": True, "motion_id": 17}
        client, captured = self.make_protocol_client(event)

        response = client.door_open(
            wait=True,
            timeout=9.0,
            angle_deg=90,
            firmware_version_code=0x003F,
        )

        self.assertTrue(response["ok"])
        self.assertEqual(captured["cmd_set"], CMD_SET_INTERCEPTOR)
        self.assertEqual(captured["cmd_id"], CMD_ID_INTERCEPTOR_DOOR_OPEN)
        self.assertEqual(captured["payload"], b"")
        self.assertEqual(response["motion_id"], 17)
        self.assertEqual(response["motion_event"], event)

    def test_wait_failure_is_preserved(self) -> None:
        event = {
            "ok": True,
            "motion_ok": False,
            "type": "motion_failed",
            "final": True,
            "motion_id": 17,
            "reason": "driver_stall",
        }
        client, _ = self.make_protocol_client(event)

        response = client.door_open(
            wait=True,
            angle_deg=120,
            firmware_version_code=0x003F,
        )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], "driver_stall")

    def test_missing_angle_is_read_from_supported_mcu_before_open(self) -> None:
        client, captured = self.make_stubbed_client()
        client.read_firmware_version = lambda timeout=8.0, attempts=2: {
            "ok": True,
            "version": "0x003f",
            "version_code": 0x003F,
        }
        client.get_manual_open_angle = lambda timeout=2.0: {
            "ok": True,
            "applied_angle_deg": 90,
        }

        response = client.door_open()

        self.assertTrue(response["ok"])
        self.assertEqual(captured["target_position"], DOOR_OPEN_POSITION_90_0P1DEG)

    def test_firmware_003e_is_rejected_without_sending_open(self) -> None:
        client, captured = self.make_stubbed_client()

        response = client.door_open(angle_deg=90, firmware_version_code=0x003E)

        self.assertFalse(response["ok"])
        self.assertIn("requires 0x003f", response["error"])
        self.assertEqual(captured, {})

    def test_invalid_angle_does_not_send_open_command(self) -> None:
        client, captured = self.make_stubbed_client()

        response = client.door_open(angle_deg=100, firmware_version_code=0x003F)

        self.assertFalse(response["ok"])
        self.assertEqual(captured, {})

    def test_open_close_wire_order_and_motion_registration_are_atomic(self) -> None:
        client = object.__new__(McuClient)
        client._lock = threading.RLock()
        client._last_motor_target = None
        open_transact_entered = threading.Event()
        allow_open_ack = threading.Event()
        close_transact_entered = threading.Event()
        motions = []

        def transact(name, cmd_set, cmd_id, payload=b"", timeout=6.0):
            if name == "door_open":
                open_transact_entered.set()
                self.assertTrue(allow_open_ack.wait(1.0))
            elif name == "door_close":
                close_transact_entered.set()
            return {"ok": True, "result": 0, "data": b"\x00"}

        def begin_motion(name, target, speed, accel, timeout, **kwargs):
            motions.append((name, target))
            return len(motions)

        client.transact = transact
        client._begin_motion = begin_motion
        results = {}
        open_thread = threading.Thread(
            target=lambda: results.update(
                open=client.door_open(
                    angle_deg=90,
                    firmware_version_code=0x003F,
                )
            )
        )
        close_thread = threading.Thread(
            target=lambda: results.update(close=client.door_close())
        )

        open_thread.start()
        self.assertTrue(open_transact_entered.wait(1.0))
        close_thread.start()
        self.assertFalse(close_transact_entered.wait(0.05))
        allow_open_ack.set()
        open_thread.join(1.0)
        close_thread.join(1.0)

        self.assertFalse(open_thread.is_alive())
        self.assertFalse(close_thread.is_alive())
        self.assertTrue(results["open"]["ok"])
        self.assertTrue(results["close"]["ok"])
        self.assertEqual(
            motions,
            [
                ("door_open", DOOR_OPEN_POSITION_90_0P1DEG),
                ("door_close", 0),
            ],
        )
        self.assertEqual(client._last_motor_target, 0)


class ControllerMcu:
    def __init__(self, version_code=0x003F, angle=90):
        self.version_code = version_code
        self.angle = angle
        self.version_error = None
        self.calls = []
        self.controller = None
        self.controller_lock_was_held = None

    def read_firmware_version(self, timeout=8.0, attempts=2):
        self.calls.append(("version", timeout, attempts))
        if self.version_error:
            return {"ok": False, "error": self.version_error}
        return {
            "ok": True,
            "version": f"0x{self.version_code:04x}",
            "version_code": self.version_code,
        }

    def set_manual_open_angle(self, angle, timeout=2.0):
        self.calls.append(("set", angle, timeout))
        self.angle = int(angle)
        return {
            "ok": True,
            "button_open_angle_deg": self.angle,
            "applied_angle_deg": self.angle,
        }

    def get_manual_open_angle(self, timeout=2.0, legacy=False):
        self.calls.append(("get", timeout, legacy))
        return {
            "ok": True,
            "button_open_angle_deg": self.angle,
            "applied_angle_deg": self.angle,
        }

    def door_open(
        self,
        wait=False,
        timeout=20.0,
        angle_deg=None,
        firmware_version_code=None,
    ):
        probe = []

        def try_controller_lock():
            acquired = self.controller._io_lock.acquire(timeout=0.05)
            probe.append(acquired)
            if acquired:
                self.controller._io_lock.release()

        thread = threading.Thread(target=try_controller_lock)
        thread.start()
        thread.join()
        self.controller_lock_was_held = probe == [False]
        self.calls.append(("open", wait, timeout, angle_deg, firmware_version_code))
        return {"ok": True, "applied_angle_deg": angle_deg}


class ManualOpenAngleDoorOpenTest(unittest.TestCase):
    def make_controller(self, mcu, desired=90):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        controller = ManualOpenAngleController(
            mcu,
            desired,
            str(Path(temp_dir.name) / "settings.json"),
            logging.getLogger(self.id()),
        )
        mcu.controller = controller
        return controller

    def test_controller_synchronizes_and_holds_angle_lock_through_open(self) -> None:
        mcu = ControllerMcu(version_code=0x003F, angle=120)
        controller = self.make_controller(mcu, desired=90)

        response = controller.door_open(wait=True, timeout=15.0)

        self.assertTrue(response["ok"])
        self.assertTrue(mcu.controller_lock_was_held)
        self.assertEqual(mcu.angle, 90)
        self.assertEqual(mcu.calls[-1], ("open", True, 15.0, 90, 0x003F))

    def test_controller_rejects_003e_without_sending_open(self) -> None:
        mcu = ControllerMcu(version_code=0x003E, angle=90)
        controller = self.make_controller(mcu, desired=90)

        response = controller.door_open()

        self.assertFalse(response["ok"])
        self.assertIn("requires 0x003f", response["error"])
        self.assertFalse(any(call[0] == "open" for call in mcu.calls))

    def test_controller_preserves_version_probe_error_without_sending_open(self) -> None:
        mcu = ControllerMcu()
        mcu.version_error = "ack timeout"
        controller = self.make_controller(mcu, desired=90)

        response = controller.door_open()

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], "ack timeout")
        self.assertFalse(any(call[0] == "open" for call in mcu.calls))


class FakeAngleController:
    def __init__(self):
        self.calls = []

    def door_open(self, wait=False, timeout=20.0):
        self.calls.append((wait, timeout))
        return {"ok": True}


class DoorOpenDispatchTest(unittest.TestCase):
    def test_dispatch_delegates_to_atomic_angle_controller_operation(self) -> None:
        controller = FakeAngleController()

        response = dispatch(
            object(),
            {"cmd": "door_open", "args": {"wait": True, "timeout": 15}},
            controller,
        )

        self.assertTrue(response["ok"])
        self.assertEqual(controller.calls, [(True, 15.0)])


if __name__ == "__main__":
    unittest.main()
