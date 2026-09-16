import json
import logging
import tempfile
import unittest
from pathlib import Path

from cli import build_parser, command_from_args
from daemon import (
    ManualOpenAngleController,
    dispatch,
    persist_manual_open_angle,
    resolve_manual_open_angle,
)
from mcu import (
    CMD_ID_INTERCEPTOR_MANUAL_OPEN_ANGLE,
    CMD_ID_INTERCEPTOR_MANUAL_OPEN_ANGLE_GET,
    CMD_SET_INTERCEPTOR,
    McuClient,
)


class FakeMcu:
    def __init__(self, version_code: int = 0x003E, angle: int = 90):
        self.version_code = version_code
        self.angle = angle
        self.calls = []
        self.version_error = None
        self.angle_error = None
        self.readback_override = None

    def read_firmware_version(self, timeout=8.0, attempts=2):
        self.calls.append(("version", timeout, attempts))
        if self.version_error:
            return {"ok": False, "error": self.version_error}
        return {
            "ok": True,
            "version": f"0x{self.version_code:04x}",
            "version_code": self.version_code,
        }

    def get_manual_open_angle(self, timeout=2.0, legacy=False):
        self.calls.append(("get", timeout, legacy))
        if self.angle_error:
            return {"ok": False, "error": self.angle_error}
        angle = self.angle if self.readback_override is None else self.readback_override
        return {
            "ok": True,
            "button_open_angle_deg": angle,
            "applied_angle_deg": angle,
            "command_id": (
                CMD_ID_INTERCEPTOR_MANUAL_OPEN_ANGLE
                if legacy
                else CMD_ID_INTERCEPTOR_MANUAL_OPEN_ANGLE_GET
            ),
        }

    def set_manual_open_angle(self, angle, timeout=2.0):
        self.calls.append(("set", angle, timeout))
        if self.angle_error:
            return {"ok": False, "error": self.angle_error}
        self.angle = int(angle)
        return {
            "ok": True,
            "button_open_angle_deg": self.angle,
            "applied_angle_deg": self.angle,
        }


class ManualOpenAngleProtocolTest(unittest.TestCase):
    def test_set_encodes_command_22_and_one_byte_angle(self) -> None:
        client = object.__new__(McuClient)
        captured = {}

        def transact(name, cmd_set, cmd_id, payload=b"", timeout=6.0):
            captured.update(
                name=name,
                cmd_set=cmd_set,
                cmd_id=cmd_id,
                payload=payload,
                timeout=timeout,
            )
            return {"ok": True, "data": bytes((0, 120)), "result": 0}

        client.transact = transact
        response = client.set_manual_open_angle(120)

        self.assertTrue(response["ok"])
        self.assertEqual(response["button_open_angle_deg"], 120)
        self.assertEqual(captured["cmd_set"], CMD_SET_INTERCEPTOR)
        self.assertEqual(captured["cmd_id"], CMD_ID_INTERCEPTOR_MANUAL_OPEN_ANGLE)
        self.assertEqual(captured["payload"], b"\x78")

    def test_get_uses_dedicated_command_23_and_empty_payload(self) -> None:
        client = object.__new__(McuClient)
        captured = {}

        def transact(name, cmd_set, cmd_id, payload=b"", timeout=6.0):
            captured.update(name=name, cmd_set=cmd_set, cmd_id=cmd_id, payload=payload)
            return {"ok": True, "data": bytes((0, 90)), "result": 0}

        client.transact = transact
        response = client.get_manual_open_angle()

        self.assertTrue(response["ok"])
        self.assertEqual(response["button_open_angle_deg"], 90)
        self.assertEqual(response["command_id"], CMD_ID_INTERCEPTOR_MANUAL_OPEN_ANGLE_GET)
        self.assertEqual(captured["cmd_id"], CMD_ID_INTERCEPTOR_MANUAL_OPEN_ANGLE_GET)
        self.assertEqual(captured["payload"], b"")

    def test_legacy_get_keeps_command_22_compatibility(self) -> None:
        client = object.__new__(McuClient)
        captured = {}

        def transact(name, cmd_set, cmd_id, payload=b"", timeout=6.0):
            captured.update(cmd_id=cmd_id, payload=payload)
            return {"ok": True, "data": bytes((0, 90)), "result": 0}

        client.transact = transact
        response = client.get_manual_open_angle(legacy=True)

        self.assertTrue(response["ok"])
        self.assertEqual(captured["cmd_id"], CMD_ID_INTERCEPTOR_MANUAL_OPEN_ANGLE)
        self.assertEqual(captured["payload"], b"")

    def test_invalid_angle_is_rejected_before_serial_write(self) -> None:
        client = object.__new__(McuClient)
        client.transact = lambda *args, **kwargs: self.fail("serial command must not be sent")

        response = client.set_manual_open_angle(100)

        self.assertFalse(response["ok"])
        self.assertIn("90 or 120", response["error"])


class ManualOpenAngleConfigurationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(self.id())

    def test_resolution_priority_and_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = str(Path(temp_dir) / "settings.json")
            persist_manual_open_angle(settings, 120)

            self.assertEqual(
                resolve_manual_open_angle(settings, self.logger),
                (120, "settings_file"),
            )
            self.assertEqual(
                resolve_manual_open_angle(settings, self.logger, environment_value="90"),
                (90, "environment"),
            )
            self.assertEqual(
                resolve_manual_open_angle(settings, self.logger, environment_value="bad"),
                (120, "settings_file"),
            )
            self.assertEqual(
                resolve_manual_open_angle(
                    str(Path(temp_dir) / "missing.json"),
                    self.logger,
                ),
                (90, "default"),
            )

    def test_persistence_preserves_other_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Path(temp_dir) / "settings.json"
            settings.write_text('{"other_setting":true}\n', encoding="utf-8")

            persist_manual_open_angle(str(settings), 120)

            self.assertEqual(
                json.loads(settings.read_text(encoding="utf-8")),
                {"other_setting": True, "button_open_angle_deg": 120},
            )

    def test_old_firmware_is_detected_without_sending_command_22(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mcu = FakeMcu(version_code=0x003C)
            controller = ManualOpenAngleController(
                mcu,
                120,
                str(Path(temp_dir) / "settings.json"),
                self.logger,
            )

            status = controller._sync_once()

            self.assertFalse(status["supported"])
            self.assertEqual(status["status"], "unsupported_firmware")
            self.assertEqual([call[0] for call in mcu.calls], ["version"])

    def test_initial_sync_explicitly_sends_default_angle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mcu = FakeMcu(angle=90)
            controller = ManualOpenAngleController(
                mcu,
                90,
                str(Path(temp_dir) / "settings.json"),
                self.logger,
            )

            status = controller._sync_once()

            self.assertTrue(status["applied"])
            self.assertEqual([call[0] for call in mcu.calls], ["version", "set", "get"])
            self.assertEqual(mcu.calls[1][1], 90)
            self.assertEqual(mcu.calls[-1][2], False)
            self.assertEqual(status["mcu_readback_command_id"], 23)

    def test_sync_reapplies_after_mcu_returns_to_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mcu = FakeMcu(angle=90)
            controller = ManualOpenAngleController(
                mcu,
                120,
                str(Path(temp_dir) / "settings.json"),
                self.logger,
            )

            first = controller._sync_once()
            self.assertTrue(first["applied"])
            self.assertEqual(mcu.angle, 120)

            mcu.angle = 90  # Simulate an independent MCU reset on the same serial device.
            second = controller._sync_once()

            self.assertTrue(second["applied"])
            self.assertEqual(mcu.angle, 120)
            self.assertEqual([call[0] for call in mcu.calls].count("set"), 2)

    def test_firmware_003d_uses_legacy_command_22_readback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mcu = FakeMcu(version_code=0x003D, angle=90)
            controller = ManualOpenAngleController(
                mcu,
                120,
                str(Path(temp_dir) / "settings.json"),
                self.logger,
            )

            status = controller._sync_once()

            self.assertTrue(status["applied"])
            self.assertEqual(status["mcu_readback_command_id"], 22)
            get_call = next(call for call in mcu.calls if call[0] == "get")
            self.assertTrue(get_call[2])

    def test_set_is_not_persisted_when_mcu_readback_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = str(Path(temp_dir) / "settings.json")
            mcu = FakeMcu(angle=90)
            controller = ManualOpenAngleController(mcu, 90, settings, self.logger)
            controller._sync_once()
            mcu.readback_override = 90

            response = controller.set_angle(120)

            self.assertFalse(response["ok"])
            self.assertIn("readback mismatch", response["error"])
            self.assertFalse(Path(settings).exists())

    def test_background_thread_stops_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = ManualOpenAngleController(
                FakeMcu(angle=90),
                90,
                str(Path(temp_dir) / "settings.json"),
                self.logger,
                verify_interval=60.0,
            )

            controller.start()
            controller.stop()

            self.assertIsNotNone(controller._thread)
            self.assertFalse(controller._thread.is_alive())

    def test_api_set_persists_and_dispatch_gets_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = str(Path(temp_dir) / "settings.json")
            mcu = FakeMcu(angle=90)
            controller = ManualOpenAngleController(mcu, 90, settings, self.logger)

            response = dispatch(
                mcu,
                {"cmd": "manual_open_angle_set", "args": {"angle": 120}},
                controller,
            )

            self.assertTrue(response["ok"])
            self.assertTrue(response["persisted"])
            self.assertEqual(response["button_open_angle_deg"], 120)
            document = json.loads(Path(settings).read_text(encoding="utf-8"))
            self.assertEqual(document, {"button_open_angle_deg": 120})

            status = dispatch(
                mcu,
                {"cmd": "manual_open_angle_get", "args": {"refresh": False}},
                controller,
            )
            self.assertTrue(status["ok"])
            self.assertEqual(status["applied_angle_deg"], 120)

    def test_cli_contract(self) -> None:
        parser = build_parser()

        query = command_from_args(parser.parse_args(["door", "angle"]))
        update = command_from_args(parser.parse_args(["door", "angle", "120"]))

        self.assertEqual(query, ("manual_open_angle_get", {}))
        self.assertEqual(update, ("manual_open_angle_set", {"angle": 120}))


if __name__ == "__main__":
    unittest.main()
