#!/usr/bin/env python3
import argparse
import logging
import struct
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import serial
import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel


SOF = 0x55
PACKAGE_VERSION = 0x01
MIN_PACKAGE_SIZE = 13
MAX_PACKAGE_SIZE = 256
DEVICE_APP = 1
DEVICE_SBDOCK = 2

CMD_SET_COMMON = 0
CMD_SET_MOTOR = 1
CMD_SET_INTERCEPTOR = 12

CMD_ID_COMMON_DEBUG = 1
CMD_ID_COMMON_GET_VERSION = 3
CMD_ID_COMMON_ERROR_MSG = 4

CMD_ID_MOTOR_STOP = 3
CMD_ID_MOTOR_RELEASE_STOP = 9

CMD_ID_INTERCEPTOR_DOOR_OPEN = 1
CMD_ID_INTERCEPTOR_DOOR_CLOSE = 2
CMD_ID_INTERCEPTOR_GET_STATUS = 5
CMD_ID_INTERCEPTOR_POWER_SET_PARAM = 6
CMD_ID_INTERCEPTOR_POWER_OUTPUT = 7
CMD_ID_INTERCEPTOR_POWER_QUERY = 8

ACTION_NAMES = {
    0: "idle",
    1: "door_open",
    2: "door_close",
}

STATE_NAMES = {
    0: "unknown",
    1: "moving",
    2: "open",
    3: "closed",
    4: "out",
    5: "in",
    6: "error",
}


def crc16_modbus(buf: bytes) -> int:
    crc = 0xFFFF
    for b in buf:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def build_packet(seq: int, cmd_set: int, cmd_id: int, payload: bytes = b"") -> bytes:
    length = MIN_PACKAGE_SIZE + len(payload)
    if length > MAX_PACKAGE_SIZE:
        raise ValueError("packet too large")

    buf = bytearray(length)
    version_length = (length << 6) | PACKAGE_VERSION
    buf[0] = SOF
    buf[1] = version_length & 0xFF
    buf[2] = (version_length >> 8) & 0xFF
    buf[3] = buf[0] ^ buf[1] ^ buf[2]
    buf[4] = DEVICE_APP
    buf[5] = DEVICE_SBDOCK
    struct.pack_into("<H", buf, 6, seq & 0xFFFF)
    buf[8] = 0xC0
    buf[9] = cmd_set & 0xFF
    buf[10] = cmd_id & 0xFF
    buf[11 : 11 + len(payload)] = payload
    struct.pack_into("<H", buf, length - 2, crc16_modbus(buf[:-2]))
    return bytes(buf)


def decode_packet(buf: bytes) -> Dict[str, Any]:
    version_length = buf[1] | (buf[2] << 8)
    flags = buf[8]
    return {
        "version": version_length & 0x3F,
        "length": version_length >> 6,
        "sender": buf[4],
        "receiver": buf[5],
        "seq": struct.unpack_from("<H", buf, 6)[0],
        "cmd_type": (flags >> 7) & 1,
        "need_ack": (flags >> 6) & 1,
        "cmd_set": buf[9],
        "cmd_id": buf[10],
        "data": buf[11:-2],
        "raw": buf,
    }


def read_packet(ser: serial.Serial, deadline: float) -> Optional[Dict[str, Any]]:
    while time.monotonic() < deadline:
        first = ser.read(1)
        if not first or first[0] != SOF:
            continue

        head = first + ser.read(3)
        if len(head) != 4:
            continue
        if (head[0] ^ head[1] ^ head[2]) != head[3]:
            continue

        version_length = head[1] | (head[2] << 8)
        length = version_length >> 6
        if length < MIN_PACKAGE_SIZE or length > MAX_PACKAGE_SIZE:
            continue

        rest = ser.read(length - 4)
        if len(rest) != length - 4:
            continue

        packet = head + rest
        expected = struct.unpack_from("<H", packet, length - 2)[0]
        actual = crc16_modbus(packet[:-2])
        if expected != actual:
            continue
        return decode_packet(packet)
    return None


def packet_message(packet: Dict[str, Any]) -> Optional[str]:
    if packet["cmd_set"] == CMD_SET_COMMON and packet["cmd_id"] in (CMD_ID_COMMON_DEBUG, CMD_ID_COMMON_ERROR_MSG):
        return packet["data"].rstrip(b"\x00").decode(errors="replace")
    return None


def parse_status(data: bytes) -> Dict[str, Any]:
    if len(data) < 33:
        return {"raw": data.hex(), "parse_error": "status payload too short"}

    axis_pos, _reserved_pos = struct.unpack_from("<ii", data, 4)
    p = 16
    set_volt, set_curr, out_volt, out_curr, temp, alarm = struct.unpack_from("<HHhhhH", data, p)
    raw_len = data[p + 16]
    raw_start = p + 17
    raw = data[raw_start : raw_start + min(raw_len, 32)]

    active = data[0]
    axis_state = data[1]
    return {
        "motion": {
            "active": active,
            "active_name": ACTION_NAMES.get(active, f"unknown_{active}"),
            "axis_state": axis_state,
            "axis_state_name": STATE_NAMES.get(axis_state, f"unknown_{axis_state}"),
            "axis_pos": axis_pos,
            "axis_motor_status": data[12],
            "last_result": data[14],
        },
        "power": {
            "set_volt": set_volt,
            "set_curr": set_curr,
            "output_volt": out_volt,
            "output_curr": out_curr,
            "temperature": temp,
            "alarm": alarm,
            "output_enabled": bool(data[p + 12]),
            "is_communicated": bool(data[p + 13]),
            "last_service": data[p + 14],
            "last_error": data[p + 15],
            "raw": raw.hex(),
        },
    }


class McuClient:
    def __init__(self, port: str, baud: int, logger: logging.Logger):
        self.port = port
        self.baud = baud
        self.logger = logger
        self._ser: Optional[serial.Serial] = None
        self._seq = 1
        self._lock = threading.RLock()
        self.last_messages: List[str] = []

    def open(self) -> None:
        if self._ser and self._ser.is_open:
            return
        self._ser = serial.Serial(self.port, self.baud, timeout=0.08, write_timeout=1.0)
        self.logger.info("opened MCU serial %s @ %s", self.port, self.baud)

    def transact(self, name: str, cmd_set: int, cmd_id: int, payload: bytes = b"", timeout: float = 6.0) -> Dict[str, Any]:
        with self._lock:
            self.open()
            assert self._ser is not None
            packet = build_packet(self._seq, cmd_set, cmd_id, payload)
            self._seq = (self._seq + 1) & 0xFFFF

            messages: List[str] = []
            self._ser.reset_input_buffer()
            self.logger.info("tx %s set=%s id=%s payload=%s", name, cmd_set, cmd_id, payload.hex())
            self._ser.write(packet)
            self._ser.flush()

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                frame = read_packet(self._ser, deadline)
                if frame is None:
                    break
                msg = packet_message(frame)
                if msg:
                    messages.append(msg)
                    self.last_messages.append(msg)
                    self.last_messages = self.last_messages[-80:]
                    self.logger.info("mcu: %s", msg)
                if frame["cmd_set"] == cmd_set and frame["cmd_id"] == cmd_id and frame["cmd_type"] == 0:
                    data = frame["data"]
                    return {
                        "ok": True,
                        "name": name,
                        "cmd_set": cmd_set,
                        "cmd_id": cmd_id,
                        "data": data,
                        "data_hex": data.hex(),
                        "result": data[0] if data else None,
                        "messages": messages,
                    }

            return {
                "ok": False,
                "name": name,
                "cmd_set": cmd_set,
                "cmd_id": cmd_id,
                "error": "ack timeout",
                "messages": messages,
            }

    def get_version(self) -> Dict[str, Any]:
        resp = self.transact("get_version", CMD_SET_COMMON, CMD_ID_COMMON_GET_VERSION)
        if resp["ok"] and len(resp["data"]) >= 2:
            version = struct.unpack_from("<H", resp["data"], 0)[0]
            resp["version"] = f"0x{version:04x}"
            resp["version_int"] = version
        return resp

    def release_stop(self) -> Dict[str, Any]:
        return self.transact("release_stop", CMD_SET_MOTOR, CMD_ID_MOTOR_RELEASE_STOP)

    def stop_motors(self) -> Dict[str, Any]:
        return self.transact("stop_motors", CMD_SET_MOTOR, CMD_ID_MOTOR_STOP)

    def get_status(self) -> Dict[str, Any]:
        resp = self.transact("get_status", CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_GET_STATUS, timeout=8.0)
        if resp["ok"]:
            resp["status"] = parse_status(resp["data"])
            resp.pop("data", None)
        return resp

    def interceptor_cmd(self, name: str, cmd_id: int, wait: bool, timeout: float) -> Dict[str, Any]:
        ack = self.transact(name, CMD_SET_INTERCEPTOR, cmd_id)
        result: Dict[str, Any] = {"ack": self._public_ack(ack)}
        if not ack["ok"] or ack.get("result") != 0 or not wait:
            return result

        deadline = time.monotonic() + timeout
        latest = None
        while time.monotonic() < deadline:
            time.sleep(0.25)
            status_resp = self.get_status()
            latest = status_resp.get("status") if status_resp.get("ok") else None
            if latest and latest["motion"]["active"] == 0:
                result["status"] = latest
                return result
        result["status"] = latest
        result["wait_error"] = "motion timeout"
        return result

    def power_query(self, temperature: bool = False) -> Dict[str, Any]:
        payload = b"\x01" if temperature else b""
        ack = self.transact("power_query_temp" if temperature else "power_query_status",
                            CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_POWER_QUERY, payload)
        time.sleep(1.2)
        status = self.get_status()
        return {"ack": self._public_ack(ack), "status": status.get("status"), "status_ok": status.get("ok", False)}

    def power_set(self, voltage: int, current: int) -> Dict[str, Any]:
        payload = struct.pack("<HH", voltage, current)
        ack = self.transact("power_set", CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_POWER_SET_PARAM, payload)
        time.sleep(1.2)
        status = self.get_status()
        return {"ack": self._public_ack(ack), "status": status.get("status"), "status_ok": status.get("ok", False)}

    def power_output(self, enabled: bool) -> Dict[str, Any]:
        ack = self.transact("power_output", CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_POWER_OUTPUT,
                            b"\x01" if enabled else b"\x00")
        time.sleep(0.8)
        status = self.get_status()
        return {"ack": self._public_ack(ack), "status": status.get("status"), "status_ok": status.get("ok", False)}

    @staticmethod
    def _public_ack(resp: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in resp.items() if k not in {"data"}}


class AircraftTransferRequest(BaseModel):
    payload_hex: str
    timeout_ms: int = 500


def make_app(client: McuClient) -> FastAPI:
    app = FastAPI(title="interceptorctl", version="0.1")

    @app.get("/")
    def root():
        return {"ok": True, "name": "interceptorctl", "serial": client.port, "baud": client.baud}

    @app.get("/health")
    def health():
        return {"ok": True, "serial": client.port, "last_messages": client.last_messages[-10:]}

    @app.get("/mcu/version")
    def version():
        return client.get_version()

    @app.get("/status")
    def status():
        return client.get_status()

    @app.post("/motor/stop")
    @app.get("/motor/stop")
    def motor_stop():
        return client.stop_motors()

    @app.post("/motor/release_stop")
    @app.get("/motor/release_stop")
    def motor_release_stop():
        return client.release_stop()

    @app.post("/door/open")
    @app.get("/door/open")
    def door_open(wait: bool = True, timeout: float = Query(20, ge=0, le=120)):
        return client.interceptor_cmd("door_open", CMD_ID_INTERCEPTOR_DOOR_OPEN, wait, timeout)

    @app.post("/door/close")
    @app.get("/door/close")
    def door_close(wait: bool = True, timeout: float = Query(20, ge=0, le=120)):
        return client.interceptor_cmd("door_close", CMD_ID_INTERCEPTOR_DOOR_CLOSE, wait, timeout)

    @app.get("/power/status")
    def power_status():
        return client.power_query(False)

    @app.get("/power/temp")
    def power_temp():
        return client.power_query(True)

    @app.post("/power/set")
    @app.get("/power/set")
    def power_set(voltage: int = Query(..., ge=0, le=65535), current: int = Query(..., ge=0, le=65535)):
        return client.power_set(voltage, current)

    @app.post("/power/on")
    @app.get("/power/on")
    def power_on():
        return client.power_output(True)

    @app.post("/power/off")
    @app.get("/power/off")
    def power_off():
        return client.power_output(False)

    @app.post("/aircraft/transfer")
    def aircraft_transfer(_: AircraftTransferRequest):
        return JSONResponse(
            status_code=501,
            content={
                "ok": False,
                "error": "aircraft UART4 passthrough command is not implemented in MCU firmware yet",
            },
        )

    return app


def configure_logging(log_path: str) -> logging.Logger:
    logger = logging.getLogger("interceptorctl")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    return logger


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7245)
    parser.add_argument("--serial", default="/dev/mcu")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--log", default="/home/orangepi/interceptorctl/interceptorctl.log")
    args = parser.parse_args()

    logger = configure_logging(args.log)
    client = McuClient(args.serial, args.baud, logger)
    app = make_app(client)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
