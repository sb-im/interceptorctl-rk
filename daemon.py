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
from pathlib import Path
from typing import Any, Dict

from mcu import McuClient


DEFAULT_SOCKET = "/tmp/interceptorctl.sock"
DEFAULT_LOG = "/home/orangepi/interceptorctl/logs/interceptorctl.log"
DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_MAX_FILES = 50


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

    def __init__(self, socket_path: str, client: McuClient, logger: logging.Logger):
        self.client = client
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
            response = dispatch(self.server.client, request)
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


def dispatch(client: McuClient, request: Dict[str, Any]) -> Dict[str, Any]:
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
    args = parser.parse_args()
    log_max_files = args.log_backup_count if args.log_backup_count is not None else args.log_max_files

    if not hasattr(socket, "AF_UNIX"):
        parser.error("Unix socket daemon must run on Linux")

    logger = configure_logging(args.log, args.log_max_bytes, log_max_files)
    ensure_socket_available(args.socket)
    Path(args.socket).parent.mkdir(parents=True, exist_ok=True)

    client = McuClient(args.serial, args.baud, logger)
    server = InterceptorServer(args.socket, client, logger)
    os.chmod(args.socket, 0o666)

    def stop(_signum: int, _frame: Any) -> None:
        logger.info("stopping daemon")
        raise SystemExit

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    logger.info(
        "interceptorctl daemon started serial=%s baud=%s socket=%s log=%s max_bytes=%s max_files=%s",
        args.serial,
        args.baud,
        args.socket,
        args.log,
        args.log_max_bytes,
        log_max_files,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        client.close()
        try:
            Path(args.socket).unlink()
        except FileNotFoundError:
            pass
        logger.info("interceptorctl daemon stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
