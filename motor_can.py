#!/usr/bin/env python3
"""Read and configure ZDT motor homing parameters over RK SocketCAN.

This module deliberately exposes only three read/configuration operations:

* discover the one motor at an addressed production ID from 1 through 32;
* read its homing parameters (command ``0x22``); and
* normalize the unique motor to factory CAN ID 1, write the factory homing
  parameters (command ``0x4C``), and read them back.

It never sends enable, stop, homing, position, or any other motion/control
command.  Multi-frame messages follow the motor firmware implementation: the
first byte of every continuation frame repeats the command byte.
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


# Linux values are kept as fallbacks so the protocol can be unit-tested on a
# non-Linux development machine with an injected socket factory.
AF_CAN = getattr(socket, "AF_CAN", 29)
CAN_RAW = getattr(socket, "CAN_RAW", 1)
CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CAN_EFF_MASK = 0x1FFFFFFF
CAN_FRAME_STRUCT = struct.Struct("=IB3x8s")

FRAME_END = 0x6B
SCAN_MOTOR_ID_MIN = 1
SCAN_MOTOR_ID_MAX = 32
SCAN_PROBE_INTERVAL_S = 0.002
CMD_READ_VERSION = 0x1F
CMD_READ_HOME_PARAMS = 0x22
CMD_READ_STATUS = 0x3A
CMD_WRITE_HOME_PARAMS = 0x4C
CMD_CHANGE_CAN_ID = 0xAE
WRITE_HOME_AUX = 0xAE
CHANGE_CAN_ID_AUX = 0x4B
FACTORY_MOTOR_ID = 1
ACK_OK = 0x02
ACK_CONDITION_ERROR = 0xE2
ACK_FORMAT_ERROR = 0xEE

# These are the only commands this module is permitted to transmit.  All are
# read-only or configuration commands; none can enable or move the motor.
SAFE_TX_COMMANDS = frozenset(
    (
        CMD_READ_VERSION,
        CMD_READ_HOME_PARAMS,
        CMD_READ_STATUS,
        CMD_WRITE_HOME_PARAMS,
        CMD_CHANGE_CAN_ID,
    )
)

HOME_MODE_NAMES = {
    0: "single_turn_nearest",
    1: "single_turn_directional",
    2: "sensorless_collision",
    3: "limit_switch",
    4: "absolute_zero",
    5: "last_power_off_position",
}


class MotorCanError(Exception):
    """Expected SocketCAN/protocol failure with a stable machine code."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass(frozen=True)
class HomingConfig:
    mode: int
    direction: int
    homing_speed_rpm: int
    homing_timeout_ms: int
    collision_speed_rpm: int
    collision_current_ma: int
    collision_time_ms: int
    power_on_auto_homing: bool

    @classmethod
    def from_read_payload(cls, payload: bytes) -> "HomingConfig":
        if len(payload) != 17:
            raise MotorCanError(
                "invalid_readback_length",
                f"0x22 response must contain 17 logical bytes, got {len(payload)}",
                expected_length=17,
                actual_length=len(payload),
            )
        if payload[0] != CMD_READ_HOME_PARAMS or payload[-1] != FRAME_END:
            raise MotorCanError(
                "invalid_readback_payload",
                "0x22 response has an invalid command byte or terminator",
                data_hex=_hex_bytes(payload),
            )

        return cls(
            mode=payload[1],
            direction=payload[2],
            homing_speed_rpm=int.from_bytes(payload[3:5], "big"),
            homing_timeout_ms=int.from_bytes(payload[5:9], "big"),
            collision_speed_rpm=int.from_bytes(payload[9:11], "big"),
            collision_current_ma=int.from_bytes(payload[11:13], "big"),
            collision_time_ms=int.from_bytes(payload[13:15], "big"),
            power_on_auto_homing=bool(payload[15]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "mode_name": HOME_MODE_NAMES.get(self.mode, "unknown"),
            "direction": self.direction,
            "direction_name": "CW" if self.direction == 0 else "CCW",
            "homing_speed_rpm": self.homing_speed_rpm,
            "homing_timeout_ms": self.homing_timeout_ms,
            "collision_speed_rpm": self.collision_speed_rpm,
            "collision_current_ma": self.collision_current_ma,
            "collision_time_ms": self.collision_time_ms,
            "power_on_auto_homing": self.power_on_auto_homing,
        }

    def to_write_payload(self, *, store: bool) -> bytes:
        values = (
            ("mode", self.mode, 0xFF),
            ("direction", self.direction, 0xFF),
            ("homing_speed_rpm", self.homing_speed_rpm, 0xFFFF),
            ("homing_timeout_ms", self.homing_timeout_ms, 0xFFFFFFFF),
            ("collision_speed_rpm", self.collision_speed_rpm, 0xFFFF),
            ("collision_current_ma", self.collision_current_ma, 0xFFFF),
            ("collision_time_ms", self.collision_time_ms, 0xFFFF),
        )
        for name, value, maximum in values:
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
                raise MotorCanError(
                    "invalid_homing_parameter",
                    f"{name} must be an integer in range 0..{maximum}",
                    parameter=name,
                    value=value,
                )

        return b"".join(
            (
                bytes(
                    (
                        CMD_WRITE_HOME_PARAMS,
                        WRITE_HOME_AUX,
                        int(store),
                        self.mode,
                        self.direction,
                    )
                ),
                self.homing_speed_rpm.to_bytes(2, "big"),
                self.homing_timeout_ms.to_bytes(4, "big"),
                self.collision_speed_rpm.to_bytes(2, "big"),
                self.collision_current_ma.to_bytes(2, "big"),
                self.collision_time_ms.to_bytes(2, "big"),
                bytes((int(self.power_on_auto_homing), FRAME_END)),
            )
        )


DEFAULT_HOMING_CONFIG = HomingConfig(
    mode=2,
    direction=0,
    homing_speed_rpm=300,
    homing_timeout_ms=120000,
    collision_speed_rpm=80,
    collision_current_ma=2000,
    collision_time_ms=400,
    power_on_auto_homing=False,
)


def _hex_bytes(data: bytes) -> str:
    return " ".join(f"{value:02X}" for value in data)


def _split_command_payload(payload: bytes) -> List[bytes]:
    """Split a logical ZDT command using the firmware's repeated-code rule."""

    if not payload:
        raise MotorCanError("empty_payload", "cannot transmit an empty CAN payload")
    if payload[0] not in SAFE_TX_COMMANDS:
        raise MotorCanError(
            "unsafe_command_blocked",
            f"command 0x{payload[0]:02X} is not allowed by the configuration module",
            command=payload[0],
        )

    packets = [payload[:8]]
    offset = len(packets[0])
    while offset < len(payload):
        chunk = payload[offset : offset + 7]
        packets.append(bytes((payload[0],)) + chunk)
        offset += len(chunk)
    return packets


class MotorCanConfigurator:
    """Synchronous, serialized RK-side motor configuration client.

    Public methods always return JSON-safe dictionaries.  Expected interface,
    timeout, discovery, or protocol errors are reported with ``ok=false`` and
    an ``error_code`` instead of being raised to daemon callers.
    """

    def __init__(
        self,
        iface: str = "can0",
        logger: Optional[logging.Logger] = None,
        *,
        timeout_s: float = 1.0,
        scan_window_s: float = 0.35,
        quiet_window_timeout_s: float = 2.0,
        socket_factory: Optional[Callable[..., Any]] = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not iface:
            raise ValueError("SocketCAN interface name must not be empty")
        if timeout_s <= 0 or scan_window_s <= 0 or quiet_window_timeout_s <= 0:
            raise ValueError("SocketCAN timeouts must be greater than zero")

        self.iface = iface
        self.logger = logger or logging.getLogger(__name__)
        self.timeout_s = float(timeout_s)
        self.scan_window_s = float(scan_window_s)
        self.quiet_window_timeout_s = float(quiet_window_timeout_s)
        self._socket_factory = socket_factory
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._lock = threading.Lock()

    def scan(self) -> Dict[str, Any]:
        result = self._base_result("scan")
        frames: List[Dict[str, Any]] = result["frames"]
        with self._lock:
            sock = None
            try:
                sock = self._open_socket()
                motors = self._scan_on_socket(sock, frames)
                motor_ids = [item["motor_id"] for item in motors]
                result.update(motor_ids=motor_ids, motors=motors)
                result.update(
                    scan_method="targeted",
                    scan_motor_id_min=SCAN_MOTOR_ID_MIN,
                    scan_motor_id_max=SCAN_MOTOR_ID_MAX,
                )
                if len(motors) != 1:
                    if not motors:
                        raise MotorCanError(
                            "motor_not_found",
                            "no motor answered targeted version queries for IDs 1..32",
                        )
                    raise MotorCanError(
                        "multiple_motors_found",
                        f"expected exactly one motor, found {len(motors)}",
                        motor_ids=motor_ids,
                    )
                result.update(ok=True, motor_id=motor_ids[0])
            except Exception as exc:  # converted to a daemon-safe result below
                self._set_error(result, exc)
            finally:
                self._close_socket(sock)
        return result

    def read_homing_config(self, motor_id: Optional[int] = None) -> Dict[str, Any]:
        result = self._base_result("read_homing_config")
        frames: List[Dict[str, Any]] = result["frames"]
        with self._lock:
            sock = None
            try:
                sock = self._open_socket()
                resolved_id, motors = self._resolve_motor_id(sock, frames, motor_id)
                if motors is not None:
                    result.update(
                        motor_ids=[item["motor_id"] for item in motors],
                        motors=motors,
                        scan_method="targeted",
                        scan_motor_id_min=SCAN_MOTOR_ID_MIN,
                        scan_motor_id_max=SCAN_MOTOR_ID_MAX,
                    )
                poll_boundary = self._wait_for_mcu_poll_boundary(
                    sock, frames, resolved_id
                )
                result.update(
                    mcu_poll_boundary=poll_boundary,
                    poll_boundary_observed=poll_boundary["observed"],
                    motor_status_raw=poll_boundary["motor_status_raw"],
                    driver_enabled=poll_boundary["driver_enabled"],
                )
                config = self._read_homing_on_socket(sock, frames, resolved_id)
                result.update(
                    ok=True,
                    motor_id=resolved_id,
                    config=config.to_dict(),
                )
            except Exception as exc:
                self._set_error(result, exc)
            finally:
                self._close_socket(sock)
        return result

    def apply_default_homing_config(
        self, motor_id: Optional[int] = None
    ) -> Dict[str, Any]:
        result = self._base_result("apply_default_homing_config")
        frames: List[Dict[str, Any]] = result["frames"]
        desired = DEFAULT_HOMING_CONFIG
        result.update(
            desired=desired.to_dict(),
            requested_store=True,
            factory_motor_id=FACTORY_MOTOR_ID,
            scan_method="targeted",
            scan_motor_id_min=SCAN_MOTOR_ID_MIN,
            scan_motor_id_max=SCAN_MOTOR_ID_MAX,
            motor_id_changed=False,
            motor_id_change_attempted=False,
            motor_id_verified=False,
            # 0x22 can verify the live readable parameters, but the protocol
            # offers no independent proof that the asynchronous flash save is
            # durable.  Do not present storage persistence as verified.
            store_persistence_verifiable=False,
            verified=False,
            verification_scope="ack_and_readable_homing_parameters",
        )

        with self._lock:
            sock = None
            try:
                sock = self._open_socket()
                # Automatic production configuration always discovers the
                # unique motor first.  A supplied ID is an expectation, not a
                # way to bypass discovery and accidentally target a bus that
                # contains a different or additional motor.
                requested_id = (
                    self._validate_motor_id(motor_id)
                    if motor_id is not None
                    else None
                )
                resolved_id, motors = self._resolve_motor_id(sock, frames, None)
                result["motor_id"] = resolved_id
                result.update(
                    original_motor_id=resolved_id,
                    detected_motor_id=resolved_id,
                    motor_ids=[item["motor_id"] for item in motors or []],
                    motors=motors or [],
                    scan_method="targeted",
                    scan_motor_id_min=SCAN_MOTOR_ID_MIN,
                    scan_motor_id_max=SCAN_MOTOR_ID_MAX,
                )
                if requested_id is not None:
                    result["requested_motor_id"] = requested_id
                    if requested_id != resolved_id:
                        raise MotorCanError(
                            "requested_motor_id_mismatch",
                            f"requested motor ID {requested_id}, but scan found ID {resolved_id}",
                            requested_motor_id=requested_id,
                            detected_motor_id=resolved_id,
                        )

                if resolved_id != FACTORY_MOTOR_ID:
                    result["motor_id_change_attempted"] = True
                    id_change = self._change_motor_id_on_socket(
                        sock,
                        frames,
                        resolved_id,
                        FACTORY_MOTOR_ID,
                    )
                    result.update(
                        id_change=id_change,
                        id_change_ack=id_change["ack"],
                        id_change_requested_store=True,
                        id_change_persistence_verifiable=False,
                        motor_id_changed=True,
                        motor_id_verified=True,
                        id_change_verified=True,
                        post_change_motor_ids=id_change["motor_ids"],
                        post_change_motors=id_change["motors"],
                        motor_id=FACTORY_MOTOR_ID,
                    )
                    resolved_id = FACTORY_MOTOR_ID
                else:
                    result.update(
                        motor_id=FACTORY_MOTOR_ID,
                        motor_id_verified=True,
                        id_change_verified=False,
                    )

                poll_boundary = self._wait_for_mcu_poll_boundary(
                    sock, frames, resolved_id
                )
                result["mcu_poll_boundary"] = poll_boundary
                result.update(
                    poll_boundary_observed=poll_boundary["observed"],
                    motor_status_raw=poll_boundary["motor_status_raw"],
                    driver_enabled=poll_boundary["driver_enabled"],
                )
                if result["motor_id_changed"] and not poll_boundary["observed"]:
                    raise MotorCanError(
                        "post_change_status_not_confirmed",
                        "motor ID changed to 1, but the MCU status poll was not observed; 0x4C was not sent",
                        motor_id=resolved_id,
                        poll_boundary=poll_boundary,
                    )
                if poll_boundary["driver_enabled"] is True:
                    raise MotorCanError(
                        "driver_enabled",
                        "motor driver is enabled; 0x4C was not sent",
                        motor_id=resolved_id,
                        motor_status_raw=poll_boundary["motor_status_raw"],
                        driver_enabled=True,
                    )

                # Keep the complete read-before/write/ACK/read-after exchange
                # inside the quiet interval immediately following the MCU's
                # periodic status response (or the confirmed silent-bus gap).
                before = self._read_homing_on_socket(sock, frames, resolved_id)
                result["before"] = before.to_dict()

                logical_payload = desired.to_write_payload(store=True)
                write_packets = _split_command_payload(logical_payload)
                if len(write_packets) != 3:
                    raise MotorCanError(
                        "internal_packet_count",
                        "0x4C factory payload must produce exactly three CAN frames",
                        packet_count=len(write_packets),
                    )
                for packet_index, packet_payload in enumerate(write_packets):
                    self._send_frame(
                        sock,
                        frames,
                        (resolved_id << 8) | packet_index,
                        packet_payload,
                    )

                ack = self._wait_for_write_ack(sock, frames, resolved_id)
                result["ack"] = ack
                if ack["result"] != ACK_OK:
                    raise MotorCanError(
                        "write_rejected",
                        f"motor rejected 0x4C with {ack['result_hex']}",
                        ack=ack,
                    )

                # The driver may acknowledge 0x4C before its flash/update
                # work has fully settled.  Also, the MCU's next periodic poll
                # can arrive before a three-packet 0x22 reply completes.  Wait
                # for that poll to finish and start verification in the next
                # quiet window instead of reading back immediately after ACK.
                readback_boundary = self._wait_for_mcu_poll_boundary(
                    sock, frames, resolved_id
                )
                result["readback_poll_boundary"] = readback_boundary
                if readback_boundary["driver_enabled"] is True:
                    raise MotorCanError(
                        "driver_enabled_during_readback",
                        "motor driver became enabled after configuration; 0x22 verification was not sent",
                        motor_id=resolved_id,
                        motor_status_raw=readback_boundary["motor_status_raw"],
                        driver_enabled=True,
                    )

                after = self._read_homing_on_socket(sock, frames, resolved_id)
                result["after"] = after.to_dict()
                if after != desired:
                    mismatches = {
                        key: {"expected": expected, "actual": result["after"][key]}
                        for key, expected in desired.to_dict().items()
                        if result["after"].get(key) != expected
                    }
                    result["mismatches"] = mismatches
                    raise MotorCanError(
                        "readback_mismatch",
                        "0x4C was acknowledged but strict 0x22 readback did not match",
                        mismatches=mismatches,
                    )

                result.update(ok=True, verified=True)
            except Exception as exc:
                self._set_error(result, exc)
            finally:
                self._close_socket(sock)
        return result

    def _base_result(self, operation: str) -> Dict[str, Any]:
        return {
            "ok": False,
            "operation": operation,
            "transport": "socketcan",
            "iface": self.iface,
            "via_mcu": False,
            "frames": [],
        }

    def _set_error(self, result: Dict[str, Any], exc: Exception) -> None:
        result["ok"] = False
        if isinstance(exc, MotorCanError):
            result["error_code"] = exc.code
            result["error"] = str(exc)
            for key, value in exc.details.items():
                result.setdefault(key, value)
            self.logger.warning("motor CAN %s failed: %s", result["operation"], exc)
            return

        if isinstance(exc, OSError):
            result["error_code"] = "socketcan_error"
            result["error"] = str(exc)
            self.logger.warning("motor CAN %s socket error: %s", result["operation"], exc)
            return

        result["error_code"] = "unexpected_error"
        result["error"] = str(exc)
        self.logger.exception("motor CAN %s failed unexpectedly", result["operation"])

    def _open_socket(self) -> Any:
        if self._socket_factory is None:
            if not hasattr(socket, "AF_CAN") or not hasattr(socket, "CAN_RAW"):
                raise MotorCanError(
                    "socketcan_unavailable",
                    "this Python build does not provide SocketCAN support",
                )
            sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        else:
            sock = self._socket_factory(AF_CAN, socket.SOCK_RAW, CAN_RAW)
        sock.bind((self.iface,))
        return sock

    @staticmethod
    def _close_socket(sock: Any) -> None:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _validate_motor_id(motor_id: Any) -> int:
        if isinstance(motor_id, bool) or not isinstance(motor_id, int) or not 1 <= motor_id <= 0xFF:
            raise MotorCanError(
                "invalid_motor_id",
                "motor_id must be an integer in range 1..255",
                motor_id=motor_id,
            )
        return motor_id

    def _resolve_motor_id(
        self,
        sock: Any,
        frames: List[Dict[str, Any]],
        motor_id: Optional[int],
    ) -> Tuple[int, Optional[List[Dict[str, Any]]]]:
        if motor_id is not None:
            return self._validate_motor_id(motor_id), None

        motors = self._scan_on_socket(sock, frames)
        motor_ids = [item["motor_id"] for item in motors]
        if not motors:
            raise MotorCanError(
                "motor_not_found",
                "no motor answered targeted version queries for IDs 1..32",
                motor_ids=motor_ids,
                motors=motors,
            )
        if len(motors) != 1:
            raise MotorCanError(
                "multiple_motors_found",
                f"expected exactly one motor, found {len(motors)}",
                motor_ids=motor_ids,
                motors=motors,
            )
        return motor_ids[0], motors

    def _scan_on_socket(
        self, sock: Any, frames: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        # Some production motor firmware responds to the address-0 query only
        # while configured as ID 1.  Never rely on or send a broadcast frame:
        # probe the agreed production range with addressed, read-only version
        # queries instead.
        query = bytes((CMD_READ_VERSION, FRAME_END))
        for motor_id in range(SCAN_MOTOR_ID_MIN, SCAN_MOTOR_ID_MAX + 1):
            self._send_frame(sock, frames, motor_id << 8, query)
            if motor_id != SCAN_MOTOR_ID_MAX:
                # RK's can0 qdisc is intentionally small (typically 10
                # frames).  Pacing keeps the directed sweep from overflowing
                # it while remaining far faster than the response window.
                self._sleeper(SCAN_PROBE_INTERVAL_S)

        deadline = self._monotonic() + self.scan_window_s
        found: Dict[int, Dict[str, Any]] = {}
        while True:
            received = self._receive_frame(sock, frames, deadline)
            if received is None:
                break
            ext_id, packet_index, data = received
            motor_id = (ext_id >> 8) & 0xFF
            if (
                not SCAN_MOTOR_ID_MIN <= motor_id <= SCAN_MOTOR_ID_MAX
                or packet_index != 0
                or len(data) < 3
                or data[0] != CMD_READ_VERSION
                or data[-1] != FRAME_END
            ):
                continue
            found[motor_id] = {
                "motor_id": motor_id,
                "can_id": motor_id << 8,
                "can_id_hex": f"0x{motor_id << 8:08X}",
                "version_raw_hex": _hex_bytes(data[1:-1]),
                "response_data_hex": _hex_bytes(data),
            }
        return [found[motor_id] for motor_id in sorted(found)]

    def _change_motor_id_on_socket(
        self,
        sock: Any,
        frames: List[Dict[str, Any]],
        old_motor_id: int,
        new_motor_id: int,
    ) -> Dict[str, Any]:
        """Persist a unique motor ID change and prove the live address by scan.

        The motor firmware deliberately acknowledges ``0xAE`` on the old CAN
        address after switching its runtime filters to the new address.  If
        that acknowledgement is lost, do not retry blindly: the command may
        already have taken effect.  An addressed read-only re-scan is the
        authoritative runtime check in both cases.
        """

        old_motor_id = self._validate_motor_id(old_motor_id)
        new_motor_id = self._validate_motor_id(new_motor_id)
        if old_motor_id == new_motor_id:
            raise MotorCanError(
                "motor_id_already_set",
                f"motor ID is already {new_motor_id}",
                motor_id=old_motor_id,
            )

        change: Dict[str, Any] = {
            "old_motor_id": old_motor_id,
            "new_motor_id": new_motor_id,
            "requested_store": True,
            "persistence_verifiable": False,
            "verified": False,
            "ack": {
                "received": False,
                "result": None,
                "result_hex": None,
                "name": "timeout",
                "data_hex": None,
            },
        }

        # MCU firmware polls factory ID 1, so a motor at any other ID has no
        # passive status boundary.  Actively read the old ID and fail closed
        # unless the driver is explicitly reported disabled.
        pre_change_status = self._read_status_on_socket(
            sock,
            frames,
            old_motor_id,
        )
        change["pre_change_status"] = pre_change_status
        if pre_change_status["driver_enabled"]:
            raise MotorCanError(
                "driver_enabled_before_id_change",
                "motor driver is enabled; CAN ID was not changed",
                motor_id=old_motor_id,
                motor_status_raw=pre_change_status["motor_status_raw"],
                driver_enabled=True,
                id_change=change,
            )

        # Avoid changing the driver's filter while another participant is in
        # the middle of a transaction.  No motor-control command is sent.
        if not self._confirm_bus_silence(sock, frames):
            raise MotorCanError(
                "bus_not_quiet_for_id_change",
                "no safe CAN silence window was found; motor ID was not changed",
                id_change=change,
            )

        payload = bytes(
            (
                CMD_CHANGE_CAN_ID,
                CHANGE_CAN_ID_AUX,
                0x01,  # Store permanently.
                new_motor_id,
                FRAME_END,
            )
        )
        self._send_frame(sock, frames, old_motor_id << 8, payload)

        try:
            ack = self._wait_for_change_id_ack(
                sock,
                frames,
                old_motor_id,
            )
        except MotorCanError as exc:
            if exc.code != "change_id_ack_timeout":
                raise
            # The command may have succeeded even though its ACK was lost.
            # Re-scan to report the resulting live state, but fail this
            # transaction closed.  A fresh invocation can safely continue
            # from whichever unique ID is then discovered.
            change["ack_timeout"] = True
            motors = self._scan_on_socket(sock, frames)
            motor_ids = [item["motor_id"] for item in motors]
            change.update(motor_ids=motor_ids, motors=motors)
            raise MotorCanError(
                "change_id_ack_timeout",
                "0xAE acknowledgement was not received; live IDs were re-scanned and 0x4C was not sent",
                id_change=change,
                motor_ids=motor_ids,
                motors=motors,
            )

        change["ack"] = ack
        if ack["result"] != ACK_OK:
            raise MotorCanError(
                "change_id_rejected",
                f"motor rejected 0xAE with {ack['result_hex']}",
                id_change=change,
                ack=ack,
            )

        # Prove that the unique live address changed before any 0x4C frame.
        motors = self._scan_on_socket(sock, frames)
        motor_ids = [item["motor_id"] for item in motors]
        change.update(motor_ids=motor_ids, motors=motors)
        if motor_ids != [new_motor_id]:
            raise MotorCanError(
                "change_id_verification_failed",
                f"expected unique motor ID {new_motor_id} after change, found {motor_ids}",
                id_change=change,
                motor_ids=motor_ids,
                motors=motors,
            )

        change["verified"] = True
        return change

    def _read_status_on_socket(
        self,
        sock: Any,
        frames: List[Dict[str, Any]],
        motor_id: int,
    ) -> Dict[str, Any]:
        motor_id = self._validate_motor_id(motor_id)
        base_id = motor_id << 8
        self._send_frame(
            sock,
            frames,
            base_id,
            bytes((CMD_READ_STATUS, FRAME_END)),
        )
        deadline = self._monotonic() + self.timeout_s
        while True:
            received = self._receive_frame(sock, frames, deadline)
            if received is None:
                raise MotorCanError(
                    "read_status_timeout",
                    "timed out waiting for the motor's 0x3A status response; CAN ID was not changed",
                    motor_id=motor_id,
                )
            ext_id, packet_index, data = received
            if (
                ext_id != base_id
                or packet_index != 0
                or len(data) != 3
                or data[0] != CMD_READ_STATUS
                or data[-1] != FRAME_END
            ):
                continue
            status_raw = data[1]
            return {
                "motor_status_raw": status_raw,
                "driver_enabled": bool(status_raw & 0x01),
                "data_hex": _hex_bytes(data),
            }

    def _read_homing_on_socket(
        self,
        sock: Any,
        frames: List[Dict[str, Any]],
        motor_id: int,
    ) -> HomingConfig:
        motor_id = self._validate_motor_id(motor_id)
        base_id = motor_id << 8
        self._send_frame(
            sock,
            frames,
            base_id,
            bytes((CMD_READ_HOME_PARAMS, FRAME_END)),
        )

        deadline = self._monotonic() + self.timeout_s
        expected_packet = 0
        logical = bytearray()
        while True:
            received = self._receive_frame(sock, frames, deadline)
            if received is None:
                raise MotorCanError(
                    "read_homing_timeout",
                    "timed out waiting for the complete 0x22 response",
                    motor_id=motor_id,
                    expected_packet=expected_packet,
                )
            ext_id, packet_index, data = received
            if (ext_id >> 8) & 0xFF != motor_id:
                continue
            if packet_index > 2:
                continue

            # Ignore unrelated MCU traffic to the same motor.  Once a 0x22
            # sequence has started, however, any packet-index discontinuity or
            # missing repeated command byte invalidates the whole transaction.
            if expected_packet == 0 and data[:1] != bytes((CMD_READ_HOME_PARAMS,)):
                continue
            if packet_index != expected_packet:
                if data[:1] == bytes((CMD_READ_HOME_PARAMS,)):
                    raise MotorCanError(
                        "read_homing_packet_order",
                        f"expected 0x22 packet {expected_packet}, got {packet_index}",
                        motor_id=motor_id,
                        expected_packet=expected_packet,
                        actual_packet=packet_index,
                    )
                continue
            if not data or data[0] != CMD_READ_HOME_PARAMS:
                raise MotorCanError(
                    "read_homing_continuation_code",
                    f"0x22 packet {packet_index} does not repeat command byte 0x22",
                    motor_id=motor_id,
                    packet=packet_index,
                    data_hex=_hex_bytes(data),
                )

            # A two-byte 22 6B frame is a query from another CAN participant,
            # not a motor's multi-packet response.
            if expected_packet == 0 and data == bytes((CMD_READ_HOME_PARAMS, FRAME_END)):
                continue

            logical.extend(data if packet_index == 0 else data[1:])
            expected_packet += 1
            # The 0x22 reply has a fixed 17-byte logical length.  A numeric
            # field may legitimately end an earlier packet with 0x6B, so the
            # terminator byte alone must not be treated as end-of-message.
            if len(logical) == 17:
                return HomingConfig.from_read_payload(bytes(logical))
            if len(logical) > 17 or expected_packet > 2:
                raise MotorCanError(
                    "invalid_readback_termination",
                    "0x22 response did not finish at its fixed 17-byte length",
                    motor_id=motor_id,
                    data_hex=_hex_bytes(bytes(logical)),
                )

    def _wait_for_mcu_poll_boundary(
        self,
        sock: Any,
        frames: List[Dict[str, Any]],
        motor_id: int,
    ) -> Dict[str, Any]:
        base_id = motor_id << 8
        deadline = self._monotonic() + self.quiet_window_timeout_s
        while True:
            received = self._receive_frame(sock, frames, deadline)
            if received is None:
                silence_confirmed = self._confirm_bus_silence(sock, frames)
                if not silence_confirmed:
                    raise MotorCanError(
                        "bus_not_quiet",
                        "MCU 0x3A was not observed and no safe bus-silence window was found; 0x4C was not sent",
                        motor_id=motor_id,
                        poll_boundary_observed=False,
                    )
                return {
                    "observed": False,
                    "command": CMD_READ_STATUS,
                    "command_hex": "0x3A",
                    "motor_status_raw": None,
                    "driver_enabled": None,
                    "bus_silence_confirmed": True,
                    "data_hex": None,
                }
            ext_id, packet_index, data = received
            if (
                ext_id == base_id
                and packet_index == 0
                and len(data) == 3
                and data[0] == CMD_READ_STATUS
                and data[-1] == FRAME_END
            ):
                status_raw = data[1]
                silence_confirmed = self._confirm_bus_silence(sock, frames)
                if not silence_confirmed:
                    raise MotorCanError(
                        "bus_not_quiet_after_status",
                        "motor status was observed but no complete CAN silence window followed; configuration was not sent",
                        motor_id=motor_id,
                        motor_status_raw=status_raw,
                        driver_enabled=bool(status_raw & 0x01),
                    )
                return {
                    "observed": True,
                    "command": CMD_READ_STATUS,
                    "command_hex": "0x3A",
                    "motor_status_raw": status_raw,
                    "driver_enabled": bool(status_raw & 0x01),
                    "bus_silence_confirmed": True,
                    "data_hex": _hex_bytes(data),
                }

    def _confirm_bus_silence(
        self,
        sock: Any,
        frames: List[Dict[str, Any]],
        *,
        silence_s: float = 0.05,
    ) -> bool:
        overall_deadline = self._monotonic() + max(self.timeout_s, silence_s)
        quiet_deadline = self._monotonic() + silence_s
        while True:
            deadline = min(overall_deadline, quiet_deadline)
            received = self._receive_frame(sock, frames, deadline)
            if received is None:
                return self._monotonic() >= quiet_deadline or deadline == quiet_deadline
            if self._monotonic() >= overall_deadline:
                return False
            quiet_deadline = self._monotonic() + silence_s

    def _wait_for_write_ack(
        self,
        sock: Any,
        frames: List[Dict[str, Any]],
        motor_id: int,
    ) -> Dict[str, Any]:
        base_id = motor_id << 8
        deadline = self._monotonic() + self.timeout_s
        while True:
            received = self._receive_frame(sock, frames, deadline)
            if received is None:
                raise MotorCanError(
                    "write_ack_timeout",
                    "timed out waiting for the motor's 0x4C acknowledgement",
                    motor_id=motor_id,
                )
            ext_id, packet_index, data = received
            if (
                ext_id != base_id
                or packet_index != 0
                or len(data) != 3
                or data[0] != CMD_WRITE_HOME_PARAMS
                or data[-1] != FRAME_END
            ):
                continue
            result = data[1]
            names = {
                ACK_OK: "ok",
                ACK_CONDITION_ERROR: "condition_or_parameter_error",
                ACK_FORMAT_ERROR: "format_error",
            }
            return {
                "received": True,
                "result": result,
                "result_hex": f"0x{result:02X}",
                "name": names.get(result, "unknown"),
                "data_hex": _hex_bytes(data),
            }

    def _wait_for_change_id_ack(
        self,
        sock: Any,
        frames: List[Dict[str, Any]],
        old_motor_id: int,
    ) -> Dict[str, Any]:
        old_base_id = old_motor_id << 8
        deadline = self._monotonic() + self.timeout_s
        while True:
            received = self._receive_frame(sock, frames, deadline)
            if received is None:
                raise MotorCanError(
                    "change_id_ack_timeout",
                    "timed out waiting for the motor's 0xAE acknowledgement",
                    old_motor_id=old_motor_id,
                )
            ext_id, packet_index, data = received
            if (
                ext_id != old_base_id
                or packet_index != 0
                or len(data) != 3
                or data[0] != CMD_CHANGE_CAN_ID
                or data[-1] != FRAME_END
            ):
                continue
            result = data[1]
            names = {
                ACK_OK: "ok",
                ACK_CONDITION_ERROR: "condition_or_parameter_error",
                ACK_FORMAT_ERROR: "format_error",
            }
            return {
                "received": True,
                "response_motor_id": (ext_id >> 8) & 0xFF,
                "result": result,
                "result_hex": f"0x{result:02X}",
                "name": names.get(result, "unknown"),
                "data_hex": _hex_bytes(data),
            }

    def _send_frame(
        self,
        sock: Any,
        frames: List[Dict[str, Any]],
        ext_id: int,
        data: bytes,
    ) -> None:
        if not 1 <= len(data) <= 8:
            raise MotorCanError(
                "invalid_can_dlc",
                f"classic CAN payload length must be 1..8, got {len(data)}",
            )
        if data[0] not in SAFE_TX_COMMANDS:
            raise MotorCanError(
                "unsafe_command_blocked",
                f"command 0x{data[0]:02X} is not allowed by the configuration module",
                command=data[0],
            )
        frame = CAN_FRAME_STRUCT.pack(
            ext_id | CAN_EFF_FLAG,
            len(data),
            data.ljust(8, b"\x00"),
        )
        sent = sock.send(frame)
        if sent is not None and sent != len(frame):
            raise MotorCanError(
                "short_can_send",
                f"SocketCAN accepted {sent} of {len(frame)} frame bytes",
            )
        self._record_frame(frames, "tx", ext_id, data)

    def _receive_frame(
        self,
        sock: Any,
        frames: List[Dict[str, Any]],
        deadline: float,
    ) -> Optional[Tuple[int, int, bytes]]:
        while True:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            try:
                packed = sock.recv(CAN_FRAME_STRUCT.size)
            except (socket.timeout, TimeoutError, BlockingIOError):
                return None
            except InterruptedError:
                continue
            if len(packed) != CAN_FRAME_STRUCT.size:
                continue

            raw_can_id, dlc, raw_data = CAN_FRAME_STRUCT.unpack(packed)
            if raw_can_id & (CAN_RTR_FLAG | CAN_ERR_FLAG):
                continue
            if not raw_can_id & CAN_EFF_FLAG:
                continue
            ext_id = raw_can_id & CAN_EFF_MASK
            data = raw_data[: min(int(dlc), 8)]
            self._record_frame(frames, "rx", ext_id, data)
            return ext_id, ext_id & 0xFF, data

    def _record_frame(
        self,
        frames: List[Dict[str, Any]],
        direction: str,
        ext_id: int,
        data: bytes,
    ) -> None:
        entry = {
            "direction": direction,
            "can_id": ext_id,
            "can_id_hex": f"0x{ext_id:08X}",
            "packet": ext_id & 0xFF,
            "dlc": len(data),
            "data_hex": _hex_bytes(data),
        }
        frames.append(entry)
        self.logger.debug(
            "motor CAN %s id=%s dlc=%d data=%s",
            direction.upper(),
            entry["can_id_hex"],
            entry["dlc"],
            entry["data_hex"],
        )


__all__ = [
    "DEFAULT_HOMING_CONFIG",
    "HomingConfig",
    "MotorCanConfigurator",
    "MotorCanError",
]
