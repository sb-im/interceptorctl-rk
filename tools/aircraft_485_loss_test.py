#!/usr/bin/env python3
"""Measure the dock aircraft RS-485 passthrough packet-loss rate.

The RK daemon sends a fixed-size test request through the MCU UART4/RS-485
port.  A USB-RS485 adapter on the same RK acts as the aircraft: it validates
each request and returns a deterministic response.  Normal traffic is logged
without payload bytes; failed transactions retain the original request and
response hex for diagnosis.
"""

from __future__ import annotations

import argparse
import json
import logging
import secrets
import socket
import struct
import sys
import threading
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set


DEFAULT_SOCKET = "/tmp/interceptorctl.sock"
DEFAULT_PORT = "/dev/ttyACM0"
FRAME_MAGIC = b"ITC4"
FRAME_VERSION = 1
FRAME_KIND_REQUEST = 1
FRAME_KIND_RESPONSE = 2
FRAME_BODY = struct.Struct("<4sBBIIQI")
FRAME_CRC = struct.Struct("<I")
FRAME_SIZE = FRAME_BODY.size + FRAME_CRC.size


class FrameError(ValueError):
    """A test frame is incomplete or corrupt."""


@dataclass(frozen=True)
class TestFrame:
    kind: int
    sequence: int
    session: int
    timestamp_us: int
    pattern: int

    def encode(self) -> bytes:
        body = FRAME_BODY.pack(
            FRAME_MAGIC,
            FRAME_VERSION,
            self.kind,
            self.sequence,
            self.session,
            self.timestamp_us,
            self.pattern,
        )
        return body + FRAME_CRC.pack(zlib.crc32(body) & 0xFFFFFFFF)

    @classmethod
    def decode(cls, raw: bytes) -> "TestFrame":
        if len(raw) != FRAME_SIZE:
            raise FrameError(f"length={len(raw)}, expected={FRAME_SIZE}")
        body = raw[: FRAME_BODY.size]
        received_crc = FRAME_CRC.unpack(raw[FRAME_BODY.size :])[0]
        expected_crc = zlib.crc32(body) & 0xFFFFFFFF
        if received_crc != expected_crc:
            raise FrameError(
                f"crc=0x{received_crc:08x}, expected=0x{expected_crc:08x}"
            )
        magic, version, kind, sequence, session, timestamp_us, pattern = FRAME_BODY.unpack(body)
        if magic != FRAME_MAGIC:
            raise FrameError(f"magic={magic.hex()}, expected={FRAME_MAGIC.hex()}")
        if version != FRAME_VERSION:
            raise FrameError(f"version={version}, expected={FRAME_VERSION}")
        if kind not in (FRAME_KIND_REQUEST, FRAME_KIND_RESPONSE):
            raise FrameError(f"invalid kind={kind}")
        expected_pattern = frame_pattern(kind, sequence, session)
        if pattern != expected_pattern:
            raise FrameError(
                f"pattern=0x{pattern:08x}, expected=0x{expected_pattern:08x}"
            )
        return cls(kind, sequence, session, timestamp_us, pattern)


def frame_pattern(kind: int, sequence: int, session: int) -> int:
    rotated_session = ((session << 7) | (session >> 25)) & 0xFFFFFFFF
    return (0xA5C35A7E ^ (kind << 24) ^ sequence ^ rotated_session) & 0xFFFFFFFF


def make_frame(kind: int, sequence: int, session: int, timestamp_us: int) -> bytes:
    return TestFrame(
        kind=kind,
        sequence=sequence,
        session=session,
        timestamp_us=timestamp_us,
        pattern=frame_pattern(kind, sequence, session),
    ).encode()


def response_for(request: TestFrame) -> bytes:
    return make_frame(
        FRAME_KIND_RESPONSE,
        request.sequence,
        request.session,
        request.timestamp_us,
    )


class DaemonClient:
    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path

    def request(self, command: str, args: Optional[Dict[str, Any]], timeout_s: float) -> Dict[str, Any]:
        payload = json.dumps(
            {"cmd": command, "args": args or {}}, ensure_ascii=False
        ).encode("utf-8") + b"\n"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout_s)
            sock.connect(self.socket_path)
            sock.sendall(payload)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            sock.close()
        raw = b"".join(chunks).decode("utf-8").strip()
        if not raw:
            raise RuntimeError("empty response from interceptorctl daemon")
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise RuntimeError("interceptorctl daemon returned non-object JSON")
        return response


class AircraftSimulator:
    def __init__(
        self,
        port: str,
        baud: int,
        idle_ms: int,
        session: int,
        logger: logging.Logger,
    ) -> None:
        self.port = port
        self.baud = baud
        self.idle_s = max(idle_ms, 1) / 1000.0
        self.session = session
        self.logger = logger
        self._serial = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._received: Set[int] = set()
        self._replied: Set[int] = set()
        self.invalid_frames = 0
        self.fatal_error: Optional[str] = None

    def start(self) -> None:
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("pyserial is required: sudo apt install python3-serial") from exc

        try:
            self._serial = serial.Serial(
                self.port,
                self.baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=min(self.idle_s, 0.01),
                write_timeout=1.0,
                exclusive=True,
            )
            self._serial.reset_input_buffer()
        except Exception as exc:
            raise RuntimeError(f"cannot open simulated-aircraft port {self.port}: {exc}") from exc

        self._thread = threading.Thread(
            target=self._run,
            name="aircraft-485-simulator",
            daemon=True,
        )
        self._thread.start()
        self.logger.info(
            "event=AIRCRAFT_READY port=%s baud=%d format=8N1 frame_bytes=%d",
            self.port,
            self.baud,
            FRAME_SIZE,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def received(self, sequence: int) -> bool:
        with self._lock:
            return sequence in self._received

    def replied(self, sequence: int) -> bool:
        with self._lock:
            return sequence in self._replied

    def _run(self) -> None:
        pending = bytearray()
        last_rx = 0.0
        try:
            while not self._stop.is_set():
                chunk = self._serial.read(256)
                now = time.monotonic()
                if chunk:
                    pending.extend(chunk)
                    last_rx = now
                    continue
                if pending and now - last_rx >= self.idle_s:
                    raw = bytes(pending)
                    pending.clear()
                    self._handle_frame(raw)
        except Exception as exc:
            self.fatal_error = str(exc)
            self.logger.exception("event=AIRCRAFT_FATAL error=%s", exc)

    def _handle_frame(self, raw: bytes) -> None:
        try:
            frame = TestFrame.decode(raw)
            if frame.kind != FRAME_KIND_REQUEST:
                raise FrameError(f"kind={frame.kind}, expected request")
            if frame.session != self.session:
                raise FrameError(
                    f"session=0x{frame.session:08x}, expected=0x{self.session:08x}"
                )
        except FrameError as exc:
            with self._lock:
                self.invalid_frames += 1
            self.logger.error(
                "event=AIRCRAFT_RX_ERROR reason=%s raw_rx_hex=%s",
                exc,
                raw.hex(),
            )
            return

        with self._lock:
            self._received.add(frame.sequence)
        self.logger.info(
            "event=AIRCRAFT_RX_OK seq=%d bytes=%d", frame.sequence, len(raw)
        )

        response = response_for(frame)
        try:
            self._serial.write(response)
            self._serial.flush()
        except Exception as exc:
            self.logger.error(
                "event=AIRCRAFT_TX_ERROR seq=%d reason=%s request_hex=%s response_hex=%s",
                frame.sequence,
                exc,
                raw.hex(),
                response.hex(),
            )
            raise
        with self._lock:
            self._replied.add(frame.sequence)
        self.logger.info(
            "event=AIRCRAFT_TX_OK seq=%d bytes=%d", frame.sequence, len(response)
        )


@dataclass
class TestStats:
    attempted: int = 0
    received_ok: int = 0
    timeouts: int = 0
    data_errors: int = 0
    daemon_errors: int = 0
    forward_path_lost: int = 0
    return_path_lost: int = 0
    unclassified_lost: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def record_loss_path(self, simulator: AircraftSimulator, sequence: int) -> None:
        if not simulator.received(sequence):
            self.forward_path_lost += 1
        elif simulator.replied(sequence):
            self.return_path_lost += 1
        else:
            self.unclassified_lost += 1

    def summary(self, simulator: AircraftSimulator, session: int) -> Dict[str, Any]:
        lost = self.attempted - self.received_ok
        loss_rate = (lost * 100.0 / self.attempted) if self.attempted else 0.0
        latency = self.latencies_ms
        return {
            "session": f"0x{session:08x}",
            "frame_bytes": FRAME_SIZE,
            "attempted": self.attempted,
            "received_ok": self.received_ok,
            "lost": lost,
            "loss_rate_percent": round(loss_rate, 6),
            "timeouts": self.timeouts,
            "data_errors": self.data_errors,
            "daemon_errors": self.daemon_errors,
            "forward_path_lost": self.forward_path_lost,
            "return_path_lost": self.return_path_lost,
            "unclassified_lost": self.unclassified_lost,
            "simulator_invalid_frames": simulator.invalid_frames,
            "latency_ms_min": round(min(latency), 3) if latency else None,
            "latency_ms_avg": round(sum(latency) / len(latency), 3) if latency else None,
            "latency_ms_max": round(max(latency), 3) if latency else None,
        }


def configure_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("aircraft_485_loss_test")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def safe_response_text(response: Dict[str, Any]) -> str:
    return json.dumps(response, ensure_ascii=False, separators=(",", ":"))


def run_test(args: argparse.Namespace, logger: logging.Logger) -> tuple[TestStats, Dict[str, Any], Optional[str]]:
    client = DaemonClient(args.socket)
    session = args.session if args.session is not None else secrets.randbits(32)
    simulator = AircraftSimulator(
        args.port,
        args.baud,
        args.serial_idle_ms,
        session,
        logger,
    )
    stats = TestStats()
    fatal_error: Optional[str] = None

    version = client.request("version", {}, timeout_s=3.0)
    logger.info("event=MCU_VERSION response=%s", safe_response_text(version))
    if not version.get("ok"):
        raise RuntimeError(f"cannot read MCU version: {version.get('error', version)}")

    simulator.start()
    try:
        for sequence in range(1, args.count + 1):
            if simulator.fatal_error:
                fatal_error = f"simulated-aircraft serial failure: {simulator.fatal_error}"
                break

            request = make_frame(
                FRAME_KIND_REQUEST,
                sequence,
                session,
                time.time_ns() // 1000,
            )
            request_frame = TestFrame.decode(request)
            expected_response = response_for(request_frame)
            stats.attempted += 1
            logger.info("event=RK_TX seq=%d bytes=%d", sequence, len(request))
            started = time.monotonic()
            try:
                response = client.request(
                    "aircraft_transfer",
                    {
                        "tx_hex": request.hex(),
                        "timeout_ms": args.timeout_ms,
                        "idle_ms": args.idle_ms,
                    },
                    timeout_s=(args.timeout_ms / 1000.0) + 3.0,
                )
            except Exception as exc:
                elapsed_ms = (time.monotonic() - started) * 1000.0
                stats.daemon_errors += 1
                stats.record_loss_path(simulator, sequence)
                logger.error(
                    "event=RK_RX_ERROR seq=%d latency_ms=%.3f reason=%s "
                    "request_hex=%s expected_response_hex=%s actual_response_hex=",
                    sequence,
                    elapsed_ms,
                    exc,
                    request.hex(),
                    expected_response.hex(),
                )
                if isinstance(exc, (FileNotFoundError, ConnectionRefusedError)):
                    fatal_error = str(exc)
                    break
            else:
                elapsed_ms = (time.monotonic() - started) * 1000.0
                raw_hex = str(response.get("rx_hex") or "")
                try:
                    actual_response = bytes.fromhex(raw_hex)
                except ValueError:
                    actual_response = b""

                if response.get("ok") and actual_response == expected_response:
                    try:
                        decoded = TestFrame.decode(actual_response)
                        if decoded.sequence != sequence or decoded.session != session:
                            raise FrameError("response sequence/session mismatch")
                    except FrameError as exc:
                        stats.data_errors += 1
                        stats.record_loss_path(simulator, sequence)
                        logger.error(
                            "event=RK_RX_ERROR seq=%d latency_ms=%.3f reason=%s "
                            "request_hex=%s expected_response_hex=%s actual_response_hex=%s",
                            sequence,
                            elapsed_ms,
                            exc,
                            request.hex(),
                            expected_response.hex(),
                            actual_response.hex(),
                        )
                    else:
                        stats.received_ok += 1
                        stats.latencies_ms.append(elapsed_ms)
                        logger.info(
                            "event=RK_RX_OK seq=%d bytes=%d latency_ms=%.3f",
                            sequence,
                            len(actual_response),
                            elapsed_ms,
                        )
                else:
                    reason = str(response.get("error") or "response content mismatch")
                    if response.get("result_name") == "timeout" or "timeout" in reason.lower():
                        stats.timeouts += 1
                    elif response.get("ok"):
                        stats.data_errors += 1
                    else:
                        stats.daemon_errors += 1
                    stats.record_loss_path(simulator, sequence)
                    logger.error(
                        "event=RK_RX_ERROR seq=%d latency_ms=%.3f reason=%s "
                        "request_hex=%s expected_response_hex=%s actual_response_hex=%s daemon_response=%s",
                        sequence,
                        elapsed_ms,
                        reason,
                        request.hex(),
                        expected_response.hex(),
                        actual_response.hex(),
                        safe_response_text(response),
                    )
                    if "response too short" in reason.lower():
                        fatal_error = (
                            "MCU aircraft passthrough response is incompatible; "
                            "verify that the updated firmware is running"
                        )
                        break

            if args.progress_every and stats.attempted % args.progress_every == 0:
                partial = stats.summary(simulator, session)
                logger.info(
                    "event=PROGRESS attempted=%d ok=%d lost=%d loss_rate_percent=%.6f",
                    partial["attempted"],
                    partial["received_ok"],
                    partial["lost"],
                    partial["loss_rate_percent"],
                )
            if sequence < args.count and args.interval_ms:
                time.sleep(args.interval_ms / 1000.0)
    finally:
        simulator.stop()

    summary = stats.summary(simulator, session)
    return stats, summary, fatal_error


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return number


def nonnegative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("value must be zero or greater")
    return number


def parse_session(value: str) -> int:
    number = int(value, 0)
    if not 0 <= number <= 0xFFFFFFFF:
        raise argparse.ArgumentTypeError("session must fit in uint32")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Measure aircraft RS-485 passthrough loss with a USB-RS485 adapter "
            "acting as a simulated aircraft."
        )
    )
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"USB-RS485 port (default: {DEFAULT_PORT})")
    parser.add_argument("--baud", type=positive_int, default=115200, help="serial baud rate (default: 115200)")
    parser.add_argument("--socket", default=DEFAULT_SOCKET, help=f"daemon socket (default: {DEFAULT_SOCKET})")
    parser.add_argument("--count", type=positive_int, default=1000, help="packet count (default: 1000)")
    parser.add_argument("--interval-ms", type=nonnegative_int, default=50, help="delay between packets (default: 50)")
    parser.add_argument("--timeout-ms", type=positive_int, default=1000, help="MCU reply timeout (default: 1000)")
    parser.add_argument("--idle-ms", type=positive_int, default=30, help="MCU response idle gap (default: 30)")
    parser.add_argument("--serial-idle-ms", type=positive_int, default=5, help="simulated-aircraft request idle gap (default: 5)")
    parser.add_argument("--progress-every", type=nonnegative_int, default=100, help="progress interval; 0 disables (default: 100)")
    parser.add_argument("--session", type=parse_session, help="fixed uint32 session ID for repeatability")
    parser.add_argument(
        "--log",
        type=Path,
        help="log path (default: logs/aircraft_485_loss_YYYYmmdd_HHMMSS.log)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    log_path = args.log or Path("logs") / datetime.now().strftime(
        "aircraft_485_loss_%Y%m%d_%H%M%S.log"
    )
    logger = configure_logger(log_path)
    logger.info(
        "event=TEST_START count=%d port=%s baud=%d timeout_ms=%d idle_ms=%d log=%s",
        args.count,
        args.port,
        args.baud,
        args.timeout_ms,
        args.idle_ms,
        log_path,
    )
    try:
        _stats, summary, fatal_error = run_test(args, logger)
    except KeyboardInterrupt:
        logger.warning("event=TEST_INTERRUPTED reason=keyboard_interrupt")
        return 130
    except Exception as exc:
        logger.exception("event=TEST_FATAL error=%s", exc)
        return 2

    logger.info("event=TEST_SUMMARY json=%s", json.dumps(summary, ensure_ascii=False, sort_keys=True))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"log: {log_path}")
    if fatal_error:
        logger.error("event=TEST_FATAL error=%s", fatal_error)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
