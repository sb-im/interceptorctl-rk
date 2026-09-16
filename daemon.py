#!/usr/bin/env python3
import argparse
import json
import logging
import logging.handlers
import os
import signal
import socket
import socketserver
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from mcu import MANUAL_OPEN_ANGLE_MIN_FIRMWARE, MANUAL_OPEN_ANGLES_DEG, McuClient


DEFAULT_SOCKET = "/tmp/interceptorctl.sock"
DEFAULT_LOG = "/home/orangepi/interceptorctl/logs/interceptorctl.log"
DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_MAX_FILES = 50
DEFAULT_SETTINGS_FILE = str(Path.home() / ".config" / "interceptorctl" / "settings.json")
MANUAL_OPEN_ANGLE_DEFAULT = 90
MANUAL_OPEN_ANGLE_RETRY_S = 1.0
MANUAL_OPEN_ANGLE_VERIFY_S = 5.0
MANUAL_OPEN_ANGLE_UNSUPPORTED_RECHECK_S = 60.0


def parse_manual_open_angle(value: Any) -> Optional[int]:
    try:
        angle = int(value)
    except (TypeError, ValueError):
        return None
    return angle if angle in MANUAL_OPEN_ANGLES_DEG else None


def load_persisted_manual_open_angle(settings_file: str, logger: logging.Logger) -> Optional[int]:
    path = Path(settings_file).expanduser()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        logger.warning("cannot read manual open angle settings %s: %s", path, exc)
        return None
    if not isinstance(document, dict):
        logger.warning("manual open angle settings %s must contain a JSON object", path)
        return None
    value = document.get("button_open_angle_deg", document.get("manual_open_angle_deg"))
    angle = parse_manual_open_angle(value)
    if angle is None:
        logger.warning("ignoring invalid button_open_angle_deg=%r in %s", value, path)
    return angle


def resolve_manual_open_angle(
    settings_file: str,
    logger: logging.Logger,
    command_line_value: Any = None,
    environment_value: Any = None,
) -> Tuple[int, str]:
    if command_line_value is not None:
        angle = parse_manual_open_angle(command_line_value)
        if angle is not None:
            return angle, "command_line"
        logger.warning("invalid command-line manual open angle %r; ignoring it", command_line_value)

    if environment_value is not None and str(environment_value).strip():
        angle = parse_manual_open_angle(environment_value)
        if angle is not None:
            return angle, "environment"
        logger.warning(
            "invalid INTERCEPTOR_MANUAL_OPEN_ANGLE=%r; expected 90 or 120",
            environment_value,
        )

    angle = load_persisted_manual_open_angle(settings_file, logger)
    if angle is not None:
        return angle, "settings_file"
    return MANUAL_OPEN_ANGLE_DEFAULT, "default"


def persist_manual_open_angle(settings_file: str, angle: int) -> None:
    angle = parse_manual_open_angle(angle)
    if angle is None:
        raise ValueError("manual open angle must be 90 or 120")
    path = Path(settings_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    document: Dict[str, Any] = {}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot preserve existing settings {path}: {exc}") from exc
    else:
        if not isinstance(existing, dict):
            raise ValueError(f"existing settings {path} must contain a JSON object")
        document.update(existing)
    document["button_open_angle_deg"] = angle
    temp_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_name = temp_file.name
            json.dump(document, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass


class ManualOpenAngleController:
    """Keep the MCU physical-button angle synchronized without owning the serial port."""

    def __init__(
        self,
        client: McuClient,
        angle: int,
        settings_file: str,
        logger: logging.Logger,
        source: str = "default",
        retry_interval: float = MANUAL_OPEN_ANGLE_RETRY_S,
        verify_interval: float = MANUAL_OPEN_ANGLE_VERIFY_S,
        unsupported_recheck_interval: float = MANUAL_OPEN_ANGLE_UNSUPPORTED_RECHECK_S,
    ):
        parsed_angle = parse_manual_open_angle(angle)
        if parsed_angle is None:
            raise ValueError("manual open angle must be 90 or 120")
        self.client = client
        self.settings_file = str(Path(settings_file).expanduser())
        self.logger = logger
        self.retry_interval = max(0.1, float(retry_interval))
        self.verify_interval = max(0.2, float(verify_interval))
        self.unsupported_recheck_interval = max(1.0, float(unsupported_recheck_interval))
        self._state_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._desired_angle = parsed_angle
        self._applied_angle: Optional[int] = None
        self._supported: Optional[bool] = None
        self._firmware_version: Optional[str] = None
        self._last_error: Optional[str] = None
        self._source = source
        self._persisted = source == "settings_file"

    def start(self) -> None:
        if self._thread is not None:
            return
        self._wake.set()
        self._thread = threading.Thread(
            target=self._run,
            name="manual-open-angle-config",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)

    def _snapshot(self) -> Dict[str, Any]:
        with self._state_lock:
            desired = self._desired_angle
            applied = self._applied_angle
            supported = self._supported
            last_error = self._last_error
            if supported is False:
                status = "unsupported_firmware"
            elif supported is None:
                status = "pending" if not last_error else "unavailable"
            elif applied == desired:
                status = "applied"
            else:
                status = "pending"
            result: Dict[str, Any] = {
                "ok": True,
                "button_open_angle_deg": desired,
                "configured_angle_deg": desired,
                "applied_angle_deg": applied,
                "applied": supported is True and applied == desired,
                "supported": supported,
                "firmware_version": self._firmware_version,
                "status": status,
                "source": self._source,
                "persisted": self._persisted,
                "settings_file": self.settings_file,
            }
            if last_error:
                result["last_error"] = last_error
            return result

    def _set_state(self, **changes: Any) -> None:
        with self._state_lock:
            for name, value in changes.items():
                setattr(self, f"_{name}", value)

    def _probe_firmware(self) -> bool:
        response = self.client.read_firmware_version(timeout=1.0, attempts=1)
        if not response.get("ok"):
            error = response.get("error", "firmware version unavailable")
            self._set_state(supported=None, applied_angle=None, last_error=error)
            self.logger.warning("manual open angle waiting for MCU: %s", error)
            return False
        version_code = int(response["version_code"])
        version = str(response["version"])
        supported = version_code >= MANUAL_OPEN_ANGLE_MIN_FIRMWARE
        if not supported:
            error = (
                f"MCU {version} does not support manual open angle; "
                f"requires 0x{MANUAL_OPEN_ANGLE_MIN_FIRMWARE:04x} or newer"
            )
            self._set_state(
                supported=False,
                firmware_version=version,
                applied_angle=None,
                last_error=error,
            )
            self.logger.warning(error)
            return False
        self._set_state(supported=True, firmware_version=version, last_error=None)
        return True

    def _sync_once(self, force_probe: bool = False) -> Dict[str, Any]:
        with self._io_lock:
            with self._state_lock:
                supported = self._supported
                desired = self._desired_angle
            if supported is False and not force_probe:
                return self._snapshot()
            apply_after_probe = force_probe or supported is None
            if force_probe or supported is None:
                if not self._probe_firmware():
                    return self._snapshot()

            # Startup/reconnect must explicitly send the selected value even
            # when it matches the MCU's power-on default.
            if apply_after_probe:
                response = self.client.set_manual_open_angle(desired, timeout=1.0)
                if not response.get("ok"):
                    error = response.get("error", "manual open angle set failed")
                    self._set_state(supported=None, applied_angle=None, last_error=error)
                    self.logger.warning("manual open angle startup apply %s failed: %s", desired, error)
                    return self._snapshot()
                applied = int(response["button_open_angle_deg"])
                self._set_state(applied_angle=applied, last_error=None)
                self.logger.info("manual open angle startup apply: %s degrees", applied)
                return self._snapshot()

            current = self.client.get_manual_open_angle(timeout=1.0)
            if not current.get("ok"):
                error = current.get("error", "manual open angle query failed")
                self._set_state(supported=None, applied_angle=None, last_error=error)
                self.logger.warning("manual open angle query failed: %s", error)
                return self._snapshot()

            applied = int(current["button_open_angle_deg"])
            self._set_state(applied_angle=applied, last_error=None)
            if applied == desired:
                return self._snapshot()

            response = self.client.set_manual_open_angle(desired, timeout=1.0)
            if not response.get("ok"):
                error = response.get("error", "manual open angle set failed")
                self._set_state(supported=None, applied_angle=applied, last_error=error)
                self.logger.warning("manual open angle apply %s failed: %s", desired, error)
                return self._snapshot()
            applied = int(response["button_open_angle_deg"])
            self._set_state(applied_angle=applied, last_error=None)
            self.logger.info("manual open angle applied: %s degrees", applied)
            return self._snapshot()

    def get_status(self, refresh: bool = True) -> Dict[str, Any]:
        if refresh:
            self._sync_once()
        return self._snapshot()

    def set_angle(self, angle: Any) -> Dict[str, Any]:
        parsed_angle = parse_manual_open_angle(angle)
        if parsed_angle is None:
            return {"ok": False, "error": "manual open angle must be 90 or 120"}
        with self._io_lock:
            with self._state_lock:
                supported = self._supported
            if supported is not True and not self._probe_firmware():
                response = self._snapshot()
                response["ok"] = False
                response["error"] = response.get("last_error", "manual open angle is unavailable")
                return response

            response = self.client.set_manual_open_angle(parsed_angle, timeout=2.0)
            if not response.get("ok"):
                error = response.get("error", "manual open angle set failed")
                self._set_state(supported=None, last_error=error)
                result = self._snapshot()
                result["ok"] = False
                result["error"] = error
                return result

            applied = int(response["button_open_angle_deg"])
            self._set_state(
                desired_angle=parsed_angle,
                applied_angle=applied,
                last_error=None,
                source="settings_file",
            )
            try:
                persist_manual_open_angle(self.settings_file, parsed_angle)
            except (OSError, ValueError) as exc:
                error = f"angle applied but settings could not be saved: {exc}"
                self._set_state(persisted=False, last_error=error)
                result = self._snapshot()
                result["ok"] = False
                result["error"] = error
                return result
            self._set_state(persisted=True)
            self.logger.info(
                "manual open angle changed to %s degrees and saved to %s",
                parsed_angle,
                self.settings_file,
            )
            self._wake.set()
            return self._snapshot()

    def _run(self) -> None:
        delay = 0.0
        while not self._stop.is_set():
            self._wake.wait(delay)
            self._wake.clear()
            if self._stop.is_set():
                break
            force_probe = self._snapshot().get("supported") is False
            status = self._sync_once(force_probe=force_probe)
            if status.get("supported") is False:
                delay = self.unsupported_recheck_interval
            elif status.get("applied"):
                delay = self.verify_interval
            else:
                delay = self.retry_interval


def configure_logging(log_path: str, max_bytes: int, max_files: int) -> logging.Logger:
    logger = logging.getLogger("interceptorctl")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    max_files = max(1, int(max_files))
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=max(1024 * 1024, int(max_bytes)),
        backupCount=max_files - 1,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    return logger


def short_json(value: Any, limit: int = 600) -> str:
    text = json.dumps(json_ready(value), ensure_ascii=False, separators=(",", ":"))
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def ensure_socket_available(path: str) -> None:
    sock_path = Path(path)
    if not sock_path.exists():
        return
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.2)
        probe.connect(path)
    except OSError:
        sock_path.unlink()
        return
    finally:
        probe.close()
    raise RuntimeError(f"daemon already listens on {path}")


class UnixStreamServer(socketserver.TCPServer):
    address_family = getattr(socket, "AF_UNIX", socket.AF_INET)


class InterceptorServer(socketserver.ThreadingMixIn, UnixStreamServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        socket_path: str,
        client: McuClient,
        angle_controller: ManualOpenAngleController,
        logger: logging.Logger,
    ):
        self.client = client
        self.angle_controller = angle_controller
        self.logger = logger
        super().__init__(socket_path, RequestHandler)


class RequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        line = self.rfile.readline(64 * 1024)
        if not line:
            return
        request: Dict[str, Any] = {}
        try:
            request = json.loads(line.decode("utf-8"))
            self.server.logger.info(
                "api request cmd=%s args=%s",
                request.get("cmd"),
                short_json(request.get("args") or {}),
            )
            if request.get("cmd") == "motion_events_subscribe":
                self.handle_motion_events(request.get("args") or {})
                return
            response = dispatch(self.server.client, request, self.server.angle_controller)
        except Exception as exc:
            self.server.logger.exception("request failed")
            response = {"ok": False, "error": str(exc)}
        self.server.logger.info(
            "api response cmd=%s ok=%s result=%s error=%s",
            request.get("cmd"),
            response.get("ok"),
            response.get("result"),
            response.get("error"),
        )
        self.wfile.write((json.dumps(json_ready(response), ensure_ascii=False) + "\n").encode("utf-8"))

    def write_json_line(self, value: Dict[str, Any]) -> None:
        self.wfile.write((json.dumps(json_ready(value), ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()

    def handle_motion_events(self, args: Dict[str, Any]) -> None:
        after_event_id = int(args.get("after_event_id", args.get("last_event_id", 0)) or 0)
        heartbeat_s = max(0.2, min(float(args.get("heartbeat_s", 1.0)), 30.0))
        self.server.logger.info("motion event subscriber connected after_event_id=%s", after_event_id)
        try:
            self.write_json_line({
                "ok": True,
                "type": "subscribed",
                "last_event_id": self.server.client.latest_motion_event_id(),
            })
            while True:
                event = self.server.client.wait_motion_event(after_event_id, heartbeat_s)
                if event is None:
                    self.write_json_line({"ok": True, "type": "heartbeat", "last_event_id": after_event_id})
                    continue
                after_event_id = int(event.get("event_id", after_event_id))
                self.write_json_line(event)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.server.logger.info("motion event subscriber disconnected last_event_id=%s", after_event_id)


def json_ready(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {k: json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [json_ready(v) for v in value]
    return value


def dispatch(
    client: McuClient,
    request: Dict[str, Any],
    angle_controller: Optional[ManualOpenAngleController] = None,
) -> Dict[str, Any]:
    cmd = request.get("cmd")
    args = request.get("args") or {}

    if cmd == "ping":
        return {"ok": True, "name": "interceptorctl-daemon"}
    if cmd == "version":
        return client.get_version()
    if cmd == "status":
        return client.get_status()
    if cmd == "motor_status":
        return client.get_motor_status()
    if cmd == "stop_status":
        return client.get_stop_status()
    if cmd == "ups_status":
        return client.get_ups_status()
    if cmd in {"env_status", "environment_status"}:
        return client.get_env_status()
    if cmd == "led_status":
        return client.get_led_status()
    if cmd == "led_set":
        if "mask" in args:
            return client.led_set_mask(int(args["mask"]))
        return client.led_set_group(str(args["group"]), str(args["color"]))
    if cmd in {"switch_status", "button_status"}:
        return client.get_switch_status()
    if cmd == "ac_status":
        return client.get_ac_status()
    if cmd == "ac_control":
        return client.ac_control(
            args["action"],
            int(args.get("value", 0)),
            bool(args.get("wait", True)),
            float(args.get("timeout", 3.0)),
        )
    if cmd == "motor_stop":
        return client.stop_motors()
    if cmd == "motor_release_stop":
        return client.release_stop()
    if cmd == "door_open":
        return client.door_open(bool(args.get("wait", False)), float(args.get("timeout", 20.0)))
    if cmd == "door_close":
        return client.door_close(bool(args.get("wait", False)), float(args.get("timeout", 20.0)))
    if cmd in {"manual_open_angle_get", "button_open_angle_get"}:
        if angle_controller is None:
            return {"ok": False, "error": "manual open angle controller is unavailable"}
        return angle_controller.get_status(refresh=bool(args.get("refresh", True)))
    if cmd in {"manual_open_angle_set", "button_open_angle_set"}:
        if angle_controller is None:
            return {"ok": False, "error": "manual open angle controller is unavailable"}
        return angle_controller.set_angle(args.get("angle", args.get("button_open_angle_deg")))
    if cmd == "motor_trapezoid":
        return client.motor_trapezoid(
            str(args["target"]),
            int(args["position"]),
            int(args["speed"]),
            int(args["accel"]),
            bool(args.get("wait", False)),
            float(args.get("timeout", 20.0)),
        )
    if cmd == "motor_home":
        return client.motor_home(
            str(args.get("target", "door")),
            bool(args.get("wait", False)),
            float(args.get("timeout", 60.0)),
        )
    if cmd == "motor_home_stop":
        return client.motor_home_stop(str(args.get("target", "door")))
    if cmd == "motor_enable":
        return client.motor_enable(str(args["target"]), bool(args["enabled"]))
    if cmd == "power_status":
        return client.power_query(False)
    if cmd == "power_fault":
        return client.power_fault_status()
    if cmd == "power_temp":
        # Backward-compatible alias. power_status already includes temperature.
        return client.power_query(False)
    if cmd == "power_set":
        return client.power_set(int(args["voltage"]), int(args["current"]))
    if cmd == "power_on":
        return client.power_output(True)
    if cmd == "power_off":
        return client.power_output(False)
    if cmd == "power_raw_transfer":
        return client.power_raw_transfer(
            bytes.fromhex(str(args.get("tx_hex", ""))),
            int(args.get("timeout_ms", 1000)),
            int(args.get("idle_ms", 20)),
        )
    if cmd == "aircraft_transfer":
        return client.aircraft_transfer(
            bytes.fromhex(str(args.get("tx_hex", ""))),
            int(args.get("timeout_ms", 1000)),
            int(args.get("idle_ms", 30)),
        )
    if cmd == "aircraft_read":
        return client.aircraft_read(
            int(args.get("timeout_ms", 1000)),
            int(args.get("max_len", 220)),
        )
    return {"ok": False, "error": f"unknown command: {cmd}"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Single-owner MCU daemon for the interceptor dock")
    parser.add_argument("--serial", default="/dev/mcu")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--socket", default=DEFAULT_SOCKET)
    parser.add_argument("--log", default=DEFAULT_LOG)
    parser.add_argument("--log-max-bytes", type=int, default=DEFAULT_LOG_MAX_BYTES)
    parser.add_argument("--log-max-files", type=int, default=DEFAULT_LOG_MAX_FILES)
    parser.add_argument("--log-backup-count", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--manual-open-angle",
        type=int,
        choices=MANUAL_OPEN_ANGLES_DEG,
        default=None,
        help="initial physical-button open angle; overrides environment and saved settings",
    )
    parser.add_argument(
        "--settings-file",
        default=os.environ.get("INTERCEPTOR_SETTINGS_FILE", DEFAULT_SETTINGS_FILE),
        help=f"persistent runtime settings file, default: {DEFAULT_SETTINGS_FILE}",
    )
    args = parser.parse_args()
    log_max_files = args.log_backup_count if args.log_backup_count is not None else args.log_max_files

    if not hasattr(socket, "AF_UNIX"):
        parser.error("Unix socket daemon must run on Linux")

    logger = configure_logging(args.log, args.log_max_bytes, log_max_files)
    ensure_socket_available(args.socket)
    Path(args.socket).parent.mkdir(parents=True, exist_ok=True)

    manual_open_angle, manual_open_angle_source = resolve_manual_open_angle(
        args.settings_file,
        logger,
        command_line_value=args.manual_open_angle,
        environment_value=os.environ.get("INTERCEPTOR_MANUAL_OPEN_ANGLE"),
    )
    client = McuClient(args.serial, args.baud, logger)
    angle_controller = ManualOpenAngleController(
        client,
        manual_open_angle,
        args.settings_file,
        logger,
        source=manual_open_angle_source,
    )
    server = InterceptorServer(args.socket, client, angle_controller, logger)
    os.chmod(args.socket, 0o666)

    def stop(_signum: int, _frame: Any) -> None:
        logger.info("stopping daemon")
        raise SystemExit

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    logger.info(
        "interceptorctl daemon started serial=%s baud=%s socket=%s log=%s max_bytes=%s max_files=%s "
        "manual_open_angle=%s source=%s settings=%s",
        args.serial,
        args.baud,
        args.socket,
        args.log,
        args.log_max_bytes,
        log_max_files,
        manual_open_angle,
        manual_open_angle_source,
        args.settings_file,
    )
    try:
        angle_controller.start()
        server.serve_forever()
    finally:
        server.server_close()
        angle_controller.stop()
        client.close()
        try:
            Path(args.socket).unlink()
        except FileNotFoundError:
            pass
        logger.info("interceptorctl daemon stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
