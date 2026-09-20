#!/usr/bin/env python3
"""Configure and trigger the M400 MCU ping-pong LED test effect."""

import argparse
import json
import logging
import os
import struct
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Optional, Tuple


CMD_SET_LED = 5
CMD_ID_LED_STYLE = 1
CMD_ID_LED_PING_PONG_CONFIG = 3
CMD_ID_LED_PING_PONG_GET = 4
PING_PONG_STYLE = 12
CONFIG_FORMAT = "<BHH9B"
CONFIG_SIZE = struct.calcsize(CONFIG_FORMAT)
SERVICE = "interceptorctl.service"

BALL_SIZE_MIN = 2
BALL_SIZE_MAX = 4
SPEED_MS_MIN = 10
SPEED_MS_MAX = 1000
DURATION_S_MIN = 1
DURATION_S_MAX = 3600


@dataclass(frozen=True)
class PingPongConfig:
    ball_size: int
    speed_ms: int
    duration_s: int
    ball_color: Tuple[int, int, int]
    paddle_color: Tuple[int, int, int]
    hit_color: Tuple[int, int, int]

    @classmethod
    def from_payload(cls, payload: bytes) -> "PingPongConfig":
        if len(payload) != CONFIG_SIZE:
            raise RuntimeError(
                f"MCU returned {len(payload)} config bytes, expected {CONFIG_SIZE}; "
                "firmware 0x0012 or newer is required"
            )
        values = struct.unpack(CONFIG_FORMAT, payload)
        return cls(
            ball_size=values[0],
            speed_ms=values[1],
            duration_s=values[2],
            ball_color=tuple(values[3:6]),
            paddle_color=tuple(values[6:9]),
            hit_color=tuple(values[9:12]),
        )

    def to_payload(self) -> bytes:
        validate_config(self)
        return struct.pack(
            CONFIG_FORMAT,
            self.ball_size,
            self.speed_ms,
            self.duration_s,
            *self.ball_color,
            *self.paddle_color,
            *self.hit_color,
        )

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ball_color"] = color_text(self.ball_color)
        data["paddle_color"] = color_text(self.paddle_color)
        data["hit_color"] = color_text(self.hit_color)
        return data


def parse_color(value: str) -> Tuple[int, int, int]:
    text = value.strip()
    if text.startswith("#") and len(text) == 7:
        try:
            return tuple(int(text[index:index + 2], 16) for index in (1, 3, 5))
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid RGB color: {value}") from exc

    try:
        channels = tuple(int(item.strip()) for item in text.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid RGB color: {value}") from exc
    if len(channels) != 3 or any(channel < 0 or channel > 255 for channel in channels):
        raise argparse.ArgumentTypeError(
            f"color must be R,G,B with values 0..255, or #RRGGBB: {value}"
        )
    return channels


def color_text(color: Tuple[int, int, int]) -> str:
    return f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}"


def ranged_int(name: str, minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"{name} must be {minimum}..{maximum}")
        return number

    return parse


def validate_config(config: PingPongConfig) -> None:
    if not BALL_SIZE_MIN <= config.ball_size <= BALL_SIZE_MAX:
        raise ValueError(f"ball size must be {BALL_SIZE_MIN}..{BALL_SIZE_MAX}")
    if not SPEED_MS_MIN <= config.speed_ms <= SPEED_MS_MAX:
        raise ValueError(f"speed-ms must be {SPEED_MS_MIN}..{SPEED_MS_MAX}")
    if not DURATION_S_MIN <= config.duration_s <= DURATION_S_MAX:
        raise ValueError(f"duration-s must be {DURATION_S_MIN}..{DURATION_S_MAX}")


def sudo_prefix() -> list:
    return [] if hasattr(os, "geteuid") and os.geteuid() == 0 else ["sudo", "-n"]


def service_is_active() -> bool:
    return subprocess.run(
        ["systemctl", "is-active", "--quiet", SERVICE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def set_service(action: str) -> None:
    subprocess.run(sudo_prefix() + ["systemctl", action, SERVICE], check=True)


def require_ok(response: dict, operation: str, expected_result: Optional[int] = None) -> bytes:
    if not response.get("ok"):
        raise RuntimeError(f"{operation} failed: {response.get('error', 'no MCU response')}")
    data = response.get("data", b"")
    if expected_result is not None and (not data or data[0] != expected_result):
        actual = data[0] if data else None
        raise RuntimeError(f"{operation} rejected by MCU: result={actual}")
    return data


def read_config(client) -> PingPongConfig:
    response = client.transact(
        "m400_ping_pong_get",
        CMD_SET_LED,
        CMD_ID_LED_PING_PONG_GET,
        timeout=2.0,
    )
    return PingPongConfig.from_payload(require_ok(response, "read config"))


def write_config(client, config: PingPongConfig) -> None:
    response = client.transact(
        "m400_ping_pong_config",
        CMD_SET_LED,
        CMD_ID_LED_PING_PONG_CONFIG,
        config.to_payload(),
        timeout=2.0,
    )
    require_ok(response, "write config", expected_result=1)


def trigger(client) -> None:
    response = client.transact(
        "m400_ping_pong_trigger",
        CMD_SET_LED,
        CMD_ID_LED_STYLE,
        bytes([PING_PONG_STYLE]),
        timeout=2.0,
    )
    require_ok(response, "trigger effect", expected_result=1)


def add_tuning_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ball-size",
        type=ranged_int("ball-size", BALL_SIZE_MIN, BALL_SIZE_MAX),
        help="ball length: 2..4 LEDs",
    )
    parser.add_argument(
        "--speed-ms",
        type=ranged_int("speed-ms", SPEED_MS_MIN, SPEED_MS_MAX),
        help="milliseconds per LED step: 10..1000; smaller is faster",
    )
    parser.add_argument(
        "--duration-s",
        type=ranged_int("duration-s", DURATION_S_MIN, DURATION_S_MAX),
        help="effect duration in seconds: 1..3600",
    )
    parser.add_argument("--ball-color", type=parse_color, help="ball RGB, for example 255,255,255 or #FFFFFF")
    parser.add_argument("--paddle-color", type=parse_color, help="steady paddle RGB")
    parser.add_argument("--hit-color", type=parse_color, help="paddle hit-flash RGB")


def apply_overrides(config: PingPongConfig, args: argparse.Namespace) -> PingPongConfig:
    updates = {}
    for name in ("ball_size", "speed_ms", "duration_s", "ball_color", "paddle_color", "hit_color"):
        value = getattr(args, name, None)
        if value is not None:
            updates[name] = value
    updated = replace(config, **updates)
    validate_config(updated)
    return updated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Tune and trigger the M400 69-LED ping-pong effect.",
        epilog=(
            "Example: sudo ./tools/m400_pingpong.py run --ball-size 3 "
            "--speed-ms 45 --duration-s 30 --ball-color '#FFFFFF' "
            "--paddle-color '#001860' --hit-color '#00A0FF'"
        ),
    )
    parser.add_argument("--port", default="/dev/mcu", help="MCU serial device (default: /dev/mcu)")
    parser.add_argument("--baud", type=int, default=115200, help="serial baud rate (default: 115200)")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--no-service-control",
        action="store_true",
        help="do not stop/restart interceptorctl.service; use only when the serial port is free",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="merge options with current MCU config, then trigger")
    add_tuning_options(run_parser)
    config_parser = subparsers.add_parser("config", help="merge options with current MCU config without triggering")
    add_tuning_options(config_parser)
    subparsers.add_parser("show", help="show the current MCU config")
    subparsers.add_parser("trigger", help="trigger with the current MCU config")
    return parser


def print_result(result: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    config = result.get("config")
    if config:
        print(
            "config: "
            f"ball_size={config['ball_size']} "
            f"speed_ms={config['speed_ms']} "
            f"duration_s={config['duration_s']} "
            f"ball={config['ball_color']} "
            f"paddle={config['paddle_color']} "
            f"hit={config['hit_color']}"
        )
    if result.get("triggered"):
        print("ping-pong effect triggered")
    if result.get("service_restored"):
        print(f"service restored: {SERVICE} active")


def main() -> int:
    args = build_parser().parse_args()
    script_path = Path(__file__).resolve()
    app_candidates = [
        script_path.parents[1],
        Path(os.environ.get("INTERCEPTOR_APP_DIR", "/home/orangepi/interceptorctl")),
    ]
    app_dir = next((path for path in app_candidates if (path / "mcu.py").is_file()), None)
    if app_dir is None:
        raise SystemExit(
            "cannot find mcu.py; set INTERCEPTOR_APP_DIR to the interceptorctl directory"
        )
    sys.path.insert(0, str(app_dir))
    os.environ["INTERCEPTOR_CAN_IFACE"] = "none"
    from mcu import McuClient

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    service_was_active = False
    service_restored = False
    client = None
    result = {"ok": True, "action": args.command}

    try:
        if not args.no_service_control:
            service_was_active = service_is_active()
            if service_was_active:
                set_service("stop")

        client = McuClient(args.port, args.baud, logging.getLogger("m400_pingpong"))
        if args.command == "show":
            result["config"] = read_config(client).to_dict()
        elif args.command == "trigger":
            trigger(client)
            result["triggered"] = True
        else:
            current = read_config(client)
            requested = apply_overrides(current, args)
            write_config(client, requested)
            verified = read_config(client)
            if verified != requested:
                raise RuntimeError(f"MCU config readback mismatch: requested={requested}, actual={verified}")
            result["config"] = verified.to_dict()
            if args.command == "run":
                trigger(client)
                result["triggered"] = True
    except Exception as exc:
        result = {"ok": False, "action": args.command, "error": str(exc)}
    finally:
        if client is not None:
            client.close()
        if service_was_active:
            try:
                set_service("start")
                service_restored = service_is_active()
            except Exception as exc:
                result["ok"] = False
                result["service_error"] = str(exc)
        result["service_restored"] = service_restored

    print_result(result, args.json)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
