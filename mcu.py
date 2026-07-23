#!/usr/bin/env python3
import logging
import os
import socket
import struct
import threading
import time
from typing import Any, Dict, List, Optional

import serial


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
CMD_ID_MOTOR_CALIB = 5
CMD_ID_MOTOR_RELEASE_STOP = 9

CMD_ID_INTERCEPTOR_DOOR_OPEN = 1
CMD_ID_INTERCEPTOR_DOOR_CLOSE = 2
CMD_ID_INTERCEPTOR_GET_STATUS = 5
CMD_ID_INTERCEPTOR_POWER_SET_PARAM = 6
CMD_ID_INTERCEPTOR_POWER_OUTPUT = 7
CMD_ID_INTERCEPTOR_POWER_QUERY = 8
CMD_ID_INTERCEPTOR_MOTOR_TRAPEZOID = 9
CMD_ID_INTERCEPTOR_AIRCRAFT_TRANSFER = 10
CMD_ID_INTERCEPTOR_STOP_STATUS = 11
CMD_ID_INTERCEPTOR_UPS_STATUS = 12
CMD_ID_INTERCEPTOR_POWER_RAW_TRANSFER = 13
CMD_ID_INTERCEPTOR_MOTOR_ENABLE = 14
CMD_ID_INTERCEPTOR_AIRCRAFT_READ = 15
CMD_ID_INTERCEPTOR_ENV_STATUS = 16
CMD_ID_INTERCEPTOR_LED_SET = 17
CMD_ID_INTERCEPTOR_LED_STATUS = 18
CMD_ID_INTERCEPTOR_AC_STATUS = 19
CMD_ID_INTERCEPTOR_AC_CONTROL = 20
CMD_ID_INTERCEPTOR_SWITCH_STATUS = 21

DOOR_OPEN_POSITION_0P1DEG = 10000
DOOR_CLOSE_POSITION_0P1DEG = 437000
DOOR_OPEN_SPEED_0P1RPM = 15000
DOOR_OPEN_ACCEL_RPM_S = 2000
DOOR_CLOSE_SPEED_0P1RPM = 7000
DOOR_CLOSE_ACCEL_RPM_S = 2000
RK_OBSERVED_REACHED_TOLERANCE_0P1DEG = 20
MOTION_EVENT_BACKLOG = 200
MOTION_ASYNC_TIMEOUT_S = 30.0
MOTION_MONITOR_PERIOD_S = 0.05
MOTION_CAN_LOST_TIMEOUT_S = 1.0
MOTOR_CAN_ID = 0x0100
CAN_EFF_MASK = 0x1FFFFFFF
CAN_FRAME_STRUCT = struct.Struct("=IB3x8s")

CMD_SET_NAMES = {
    CMD_SET_COMMON: "common",
    CMD_SET_MOTOR: "motor",
    CMD_SET_INTERCEPTOR: "interceptor",
}

CMD_NAMES = {
    (CMD_SET_COMMON, CMD_ID_COMMON_DEBUG): "debug",
    (CMD_SET_COMMON, CMD_ID_COMMON_GET_VERSION): "get_version",
    (CMD_SET_COMMON, CMD_ID_COMMON_ERROR_MSG): "error_message",
    (CMD_SET_MOTOR, CMD_ID_MOTOR_STOP): "motor_stop",
    (CMD_SET_MOTOR, CMD_ID_MOTOR_CALIB): "motor_calib",
    (CMD_SET_MOTOR, CMD_ID_MOTOR_RELEASE_STOP): "motor_release_stop",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_DOOR_OPEN): "door_open",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_DOOR_CLOSE): "door_close",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_GET_STATUS): "get_status",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_POWER_SET_PARAM): "power_set",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_POWER_OUTPUT): "power_output",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_POWER_QUERY): "power_query",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_MOTOR_TRAPEZOID): "motor_trapezoid",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_AIRCRAFT_TRANSFER): "aircraft_transfer",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_STOP_STATUS): "stop_status",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_UPS_STATUS): "ups_status",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_POWER_RAW_TRANSFER): "power_raw_transfer",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_MOTOR_ENABLE): "motor_enable",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_AIRCRAFT_READ): "aircraft_read",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_ENV_STATUS): "env_status",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_LED_SET): "led_set",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_LED_STATUS): "led_status",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_AC_STATUS): "ac_status",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_AC_CONTROL): "ac_control",
    (CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_SWITCH_STATUS): "switch_status",
}

MOTOR_TARGET_NAMES = {
    0: "door",
}

ACTION_NAMES = {
    0: "idle",
    1: "door_open",
    2: "door_close",
    5: "door_move",
    7: "manual_opening",
    8: "manual_closing",
}

MANUAL_ACTION_NAMES = {
    0: "none",
    1: "manual_opening",
    2: "manual_closing",
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

POWER_ERROR_NAMES = {
    0: "ok",
    1: "bad_len",
    2: "busy",
    3: "timeout",
    4: "overflow",
    5: "tx_fail",
    6: "crc",
    7: "slave",
    8: "function",
    9: "exception",
    10: "echo",
    11: "byte_count",
}

AIRCRAFT_RESULT_NAMES = {
    0: "ok",
    1: "bad_len",
    2: "busy",
    3: "timeout",
    4: "overflow",
    5: "tx_fail",
}

ENV_RESULT_NAMES = {
    0: "ok",
    1: "no_device",
    2: "tx_fail",
    3: "rx_fail",
    4: "temperature_crc",
    5: "humidity_crc",
    6: "not_ready",
}

LED_RESULT_NAMES = {
    0: "ok",
    1: "no_device",
    2: "write_fail",
    3: "read_fail",
    4: "invalid_param",
    5: "not_ready",
}

AC_RESULT_NAMES = {
    0: "ok",
    1: "busy",
    2: "invalid_param",
    3: "tx_fail",
    4: "timeout",
    5: "overflow",
    6: "crc",
    7: "bad_addr",
    8: "bad_function",
    9: "exception",
    10: "bad_length",
}

AC_CONTROL_ACTIONS = {
    "remote_power": 1,
    "power": 1,
    "force_cool": 2,
    "cool": 2,
    "force_heat": 3,
    "heat": 3,
    "run_mode": 4,
    "mode": 4,
    "humidity": 5,
    "monitor_humidity": 5,
    "cool_start_temp": 6,
    "cool_temp": 6,
    "cool_start": 6,
    "cool_diff": 7,
    "heat_start_temp": 8,
    "heat_temp": 8,
    "heat_start": 8,
    "heat_diff": 9,
    "dehumid_setpoint": 10,
    "dehumid": 10,
}

AC_CONTROL_NAMES = {
    0: "none",
    1: "remote_power",
    2: "force_cool",
    3: "force_heat",
    4: "run_mode",
    5: "humidity",
    6: "cool_start_temp",
    7: "cool_diff",
    8: "heat_start_temp",
    9: "heat_diff",
    10: "dehumid_setpoint",
}

AC_DEVICE_STATUS_NAMES = {
    0: "unknown",
    1: "standby",
    2: "running",
    3: "fault",
}

AC_ALARM_NAMES = [
    "high_temp",
    "indoor_fan_fault",
    "outdoor_fan_fault",
    "compressor_fault",
    "return_air_sensor_fault",
    "high_pressure",
    "low_temp",
    "dc_overvoltage",
    "dc_undervoltage",
    "evaporator_sensor_fault",
    "condenser_sensor_fault",
    "ambient_sensor_fault",
    "evaporator_frost",
    "frequent_high_pressure",
]

LED_GROUP_BITS = {
    "jc": (0, 1),
    "cd": (2, 3),
    "wz": (4, 5),
    "dp": (6, 7),
}

LED_COLOR_BITS = {
    "off": 0,
    "red": 1,
    "green": 2,
    "both": 3,
    "yellow": 3,
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
        raise ValueError(f"packet too large: {length} > {MAX_PACKAGE_SIZE}")

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


def parse_power_status(data: bytes) -> Dict[str, Any]:
    if len(data) < 17:
        return {"raw": data.hex(), "parse_error": "power payload too short"}

    set_volt, set_curr, out_volt, out_curr, temp, alarm = struct.unpack_from("<HHhhhH", data, 0)
    raw_len = data[16]
    raw_start = 17
    raw = data[raw_start : raw_start + min(raw_len, 32)]
    return {
        "set_volt": set_volt,
        "set_curr": set_curr,
        "output_volt": out_volt,
        "output_curr": out_curr,
        "temperature": temp,
        "alarm": alarm,
        "output_enabled": bool(data[12]),
        "is_communicated": bool(data[13]),
        "last_service": data[14],
        "last_error": data[15],
        "raw": raw.hex(),
    }


def parse_stop_status(data: bytes) -> Dict[str, Any]:
    if len(data) < 3:
        return {"raw": data.hex(), "parse_error": "stop status payload too short"}
    hardware_stop = bool(data[0])
    soft_stop = bool(data[1])
    active = bool(data[2])
    return {
        "hardware_stop": hardware_stop,
        "soft_stop": soft_stop,
        "active": active,
        "hardware_stop_raw": data[0],
        "soft_stop_raw": data[1],
        "active_raw": data[2],
    }


def parse_ups_status(data: bytes) -> Dict[str, Any]:
    if len(data) < 11:
        return {"raw": data.hex(), "parse_error": "ups status payload too short"}

    volt, curr, temp, status, output_status, software_version, hardware_version, request_power_off = struct.unpack_from(
        "<HHhBBBBB", data, 0
    )
    return {
        "volt": volt,
        "curr": curr,
        "temp": temp,
        "status": status,
        "output_status": output_status,
        "software_version": software_version,
        "hardware_version": hardware_version,
        "request_power_off": request_power_off,
        "is_communicated": status != 0xFF,
        "raw": data[:11].hex(),
    }


def parse_env_status(data: bytes) -> Dict[str, Any]:
    if len(data) < 14:
        return {"raw": data.hex(), "parse_error": "environment status payload too short"}

    temperature, humidity, raw_temperature, raw_humidity, address, is_communicated, last_error, last_hal_status, sample_count = struct.unpack_from(
        "<hHHHBBBBH", data, 0
    )
    return {
        "temperature": temperature,
        "humidity": humidity,
        "raw_temperature": raw_temperature,
        "raw_humidity": raw_humidity,
        "address": address,
        "is_communicated": bool(is_communicated),
        "last_error": last_error,
        "last_hal_status": last_hal_status,
        "sample_count": sample_count,
        "raw": data[:14].hex(),
    }


def parse_led_status(data: bytes) -> Dict[str, Any]:
    if len(data) < 10:
        return {"raw": data.hex(), "parse_error": "LED status payload too short"}

    output, input_value, polarity, config, address, is_communicated, last_error, last_hal_status, write_count = struct.unpack_from(
        "<BBBBBBBBH", data, 0
    )
    return {
        "mask": output,
        "input": input_value,
        "polarity": polarity,
        "config": config,
        "address": address,
        "is_communicated": bool(is_communicated),
        "last_error": last_error,
        "last_hal_status": last_hal_status,
        "write_count": write_count,
        "raw": data[:10].hex(),
    }


def parse_switch_status(data: bytes) -> Dict[str, Any]:
    if len(data) < 5:
        return {"raw": data.hex(), "parse_error": "switch status payload too short"}

    top, bottom, cover_button, active_mask, raw_level_mask = struct.unpack_from("<BBBBB", data, 0)
    manual_action = data[5] if len(data) >= 6 else 0
    return {
        "top": bool(top),
        "bottom": bool(bottom),
        "cover_button": bool(cover_button),
        "psw1": bool(top),
        "psw2": bool(bottom),
        "psw3": bool(cover_button),
        "active_mask": active_mask,
        "raw_level_mask": raw_level_mask,
        "manual_action": manual_action,
        "manual_action_name": MANUAL_ACTION_NAMES.get(manual_action, f"unknown_{manual_action}"),
        "active_low": True,
        "raw": data[:6].hex(),
    }


AC_STATUS_STRUCT = struct.Struct("<BBBBBBBBHBBBBBBhhhh" + "H" * 9 + "hhhh" + "H" * 7)


def parse_ac_status(data: bytes) -> Dict[str, Any]:
    if len(data) < AC_STATUS_STRUCT.size:
        return {"raw": data.hex(), "parse_error": "AC status payload too short"}

    values = AC_STATUS_STRUCT.unpack_from(data, 0)
    keys = [
        "address",
        "is_communicated",
        "state",
        "is_busy",
        "last_error",
        "last_exception",
        "last_function",
        "last_control_action",
        "last_control_value",
        "last_control_result",
        "device_status",
        "indoor_fan_status",
        "outdoor_fan_status",
        "compressor_status",
        "heater_status",
        "return_air_temp",
        "external_temp",
        "condenser_temp",
        "evaporator_temp",
        "indoor_fan_rpm",
        "outdoor_fan_rpm",
        "dc_voltage",
        "dc_current",
        "cooling_capacity_w",
        "alarms",
        "protocol_version",
        "software_version",
        "hardware_version",
        "cool_start_temp",
        "cool_diff",
        "heat_start_temp",
        "heat_diff",
        "dehumid_setpoint",
        "run_mode",
        "monitor_humidity",
        "tx_count",
        "rx_count",
        "crc_error_count",
        "timeout_count",
    ]
    ac = dict(zip(keys, values))
    ac["is_communicated"] = bool(ac["is_communicated"])
    ac["is_busy"] = bool(ac["is_busy"])
    ac["raw"] = data[: AC_STATUS_STRUCT.size].hex()
    return ac


def power_error_name(code: Any) -> str:
    try:
        return POWER_ERROR_NAMES.get(int(code), f"unknown_{code}")
    except (TypeError, ValueError):
        return "unknown"


def aircraft_result_name(code: Any) -> str:
    try:
        return AIRCRAFT_RESULT_NAMES.get(int(code), f"unknown_{code}")
    except (TypeError, ValueError):
        return "unknown"


def env_error_name(code: Any) -> str:
    try:
        return ENV_RESULT_NAMES.get(int(code), f"unknown_{code}")
    except (TypeError, ValueError):
        return "unknown"


def led_error_name(code: Any) -> str:
    try:
        return LED_RESULT_NAMES.get(int(code), f"unknown_{code}")
    except (TypeError, ValueError):
        return "unknown"


def ac_error_name(code: Any) -> str:
    try:
        return AC_RESULT_NAMES.get(int(code), f"unknown_{code}")
    except (TypeError, ValueError):
        return "unknown"


def ac_action_id(action: Any) -> Optional[int]:
    if isinstance(action, int):
        return action if action in AC_CONTROL_NAMES and action != 0 else None
    text = str(action).lower().replace("-", "_")
    return AC_CONTROL_ACTIONS.get(text)


def command_label(cmd_set: int, cmd_id: int) -> str:
    set_name = CMD_SET_NAMES.get(cmd_set, f"set_{cmd_set}")
    cmd_name = CMD_NAMES.get((cmd_set, cmd_id), f"id_{cmd_id}")
    return f"{set_name}.{cmd_name}(set={cmd_set},id={cmd_id})"


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def hex_preview(data: bytes, max_len: int = 80) -> str:
    text = data.hex()
    if len(text) <= max_len * 2:
        return text
    return text[: max_len * 2] + "..."


def target_name(target: int) -> str:
    return MOTOR_TARGET_NAMES.get(target, f"target_{target}")


def decode_tx_payload(cmd_set: int, cmd_id: int, payload: bytes) -> str:
    raw = f"raw={hex_preview(payload)}" if payload else "raw=<empty>"

    try:
        if cmd_set == CMD_SET_INTERCEPTOR and cmd_id == CMD_ID_INTERCEPTOR_MOTOR_ENABLE and len(payload) >= 2:
            target, enabled = struct.unpack_from("<BB", payload, 0)
            return f"target={target_name(target)}({target}) enabled={bool_text(bool(enabled))} {raw}"

        if cmd_set == CMD_SET_MOTOR and cmd_id == CMD_ID_MOTOR_CALIB and len(payload) >= 2:
            target, is_start = struct.unpack_from("<BB", payload, 0)
            return f"target={target_name(target)}({target}) action={'start' if is_start else 'stop'} {raw}"

        if cmd_set == CMD_SET_INTERCEPTOR and cmd_id == CMD_ID_INTERCEPTOR_MOTOR_TRAPEZOID and len(payload) >= 9:
            target, position, speed, accel = struct.unpack_from("<BiHH", payload, 0)
            return (
                f"target={target_name(target)}({target}) "
                f"position={position}/0.1deg({position / 10.0:.1f}deg) "
                f"speed={speed}/0.1rpm({speed / 10.0:.1f}rpm) "
                f"accel={accel}rpm/s {raw}"
            )

        if cmd_set == CMD_SET_INTERCEPTOR and cmd_id == CMD_ID_INTERCEPTOR_POWER_SET_PARAM and len(payload) >= 4:
            voltage, current = struct.unpack_from("<HH", payload, 0)
            return (
                f"voltage={voltage}/0.01V({voltage / 100.0:.2f}V) "
                f"current={current}/0.01A({current / 100.0:.2f}A) {raw}"
            )

        if cmd_set == CMD_SET_INTERCEPTOR and cmd_id == CMD_ID_INTERCEPTOR_POWER_OUTPUT and len(payload) >= 1:
            return f"enabled={bool_text(bool(payload[0]))} {raw}"

        if cmd_set == CMD_SET_INTERCEPTOR and cmd_id == CMD_ID_INTERCEPTOR_POWER_QUERY:
            query = "temperature" if payload[:1] == b"\x01" else "status"
            return f"query={query} {raw}"

        if cmd_set == CMD_SET_INTERCEPTOR and cmd_id == CMD_ID_INTERCEPTOR_LED_SET and len(payload) >= 1:
            return f"mask=0x{payload[0]:02x}({payload[0]}) {raw}"

        if cmd_set == CMD_SET_INTERCEPTOR and cmd_id == CMD_ID_INTERCEPTOR_AC_CONTROL and len(payload) >= 3:
            action, value = struct.unpack_from("<BH", payload, 0)
            return f"action={AC_CONTROL_NAMES.get(action, f'unknown_{action}')}({action}) value={value} {raw}"

        if cmd_set == CMD_SET_INTERCEPTOR and cmd_id in (
            CMD_ID_INTERCEPTOR_AIRCRAFT_TRANSFER,
            CMD_ID_INTERCEPTOR_POWER_RAW_TRANSFER,
        ) and len(payload) >= 6:
            timeout_ms, idle_ms, tx_len = struct.unpack_from("<HHH", payload, 0)
            tx = payload[6 : 6 + tx_len]
            return f"timeout={timeout_ms}ms idle={idle_ms}ms tx_len={tx_len} tx_hex={hex_preview(tx)} {raw}"

        if cmd_set == CMD_SET_INTERCEPTOR and cmd_id == CMD_ID_INTERCEPTOR_AIRCRAFT_READ and len(payload) >= 4:
            timeout_ms, max_len = struct.unpack_from("<HH", payload, 0)
            return f"timeout={timeout_ms}ms max_len={max_len} {raw}"

    except (struct.error, ValueError) as exc:
        return f"decode_error={exc} {raw}"

    if not payload:
        return "no_payload"
    return raw


def decode_rx_summary(cmd_set: int, cmd_id: int, data: bytes) -> str:
    raw = f"raw={hex_preview(data)}" if data else "raw=<empty>"
    try:
        if cmd_set == CMD_SET_COMMON and cmd_id == CMD_ID_COMMON_GET_VERSION and len(data) >= 2:
            version = struct.unpack_from("<H", data, 0)[0]
            return f"version=0x{version:04x} {raw}"

        if cmd_set == CMD_SET_INTERCEPTOR and cmd_id == CMD_ID_INTERCEPTOR_GET_STATUS:
            if len(data) >= 15:
                active = ACTION_NAMES.get(data[0], f"unknown_{data[0]}")
                axis_state = STATE_NAMES.get(data[1], f"unknown_{data[1]}")
                axis_pos = struct.unpack_from("<i", data, 4)[0]
                return f"active={active} axis_state={axis_state} position={axis_pos}/0.1deg({axis_pos / 10.0:.1f}deg) len={len(data)} {raw}"
            return f"status_len={len(data)} {raw}"

        if cmd_set == CMD_SET_INTERCEPTOR and cmd_id == CMD_ID_INTERCEPTOR_STOP_STATUS and len(data) >= 3:
            return f"hardware_stop={bool_text(bool(data[0]))} soft_stop={bool_text(bool(data[1]))} active={bool_text(bool(data[2]))} {raw}"

        if cmd_set == CMD_SET_INTERCEPTOR and cmd_id == CMD_ID_INTERCEPTOR_POWER_QUERY and len(data) >= 18:
            result = data[0]
            power = parse_power_status(data[1:])
            return (
                f"result={result}({power_error_name(result)}) "
                f"set={power.get('set_volt')}/0.01V({(power.get('set_volt') or 0) / 100.0:.2f}V) "
                f"{power.get('set_curr')}/0.01A({(power.get('set_curr') or 0) / 100.0:.2f}A) "
                f"out={power.get('output_volt')}/0.01V({(power.get('output_volt') or 0) / 100.0:.2f}V) "
                f"{power.get('output_curr')}/0.01A({(power.get('output_curr') or 0) / 100.0:.2f}A) "
                f"enabled={power.get('output_enabled')} temp={power.get('temperature')}/0.1C "
                f"last_error={power.get('last_error')}({power_error_name(power.get('last_error'))}) {raw}"
            )

        if cmd_set == CMD_SET_INTERCEPTOR and cmd_id in (
            CMD_ID_INTERCEPTOR_DOOR_OPEN,
            CMD_ID_INTERCEPTOR_DOOR_CLOSE,
            CMD_ID_INTERCEPTOR_MOTOR_TRAPEZOID,
            CMD_ID_INTERCEPTOR_MOTOR_ENABLE,
            CMD_ID_INTERCEPTOR_POWER_SET_PARAM,
            CMD_ID_INTERCEPTOR_POWER_OUTPUT,
            CMD_ID_INTERCEPTOR_LED_SET,
            CMD_ID_INTERCEPTOR_AC_CONTROL,
        ) and len(data) >= 1:
            return f"result={data[0]} {raw}"

        if cmd_set == CMD_SET_MOTOR and cmd_id == CMD_ID_MOTOR_CALIB and len(data) >= 1:
            return f"result={data[0]} {raw}"

        if data:
            return f"len={len(data)} {raw}"
        return "no_data"
    except (struct.error, ValueError) as exc:
        return f"decode_error={exc} {raw}"


def public_motor_axis(state: Any, position: Any, status: Any) -> Dict[str, Any]:
    try:
        flags = int(status)
    except (TypeError, ValueError):
        flags = 0
    return {
        "state": state,
        "position": position,
        "enabled": bool(flags & 0x02),
        "stall": bool(flags & 0x01),
        "reached": bool(flags & 0x04),
        "calibed": bool(flags & 0x08),
        "calibing": bool(flags & 0x10),
        "calib_failed": bool(flags & 0x20),
        "final_reached": bool(flags & 0x40),
    }


def public_motion(
    status: Dict[str, Any],
    can_axis: Optional[Dict[str, Any]] = None,
    target_position: Optional[int] = None,
) -> Dict[str, Any]:
    motion = status.get("motion") or {}
    axis = public_motor_axis(
        motion.get("axis_state_name"),
        motion.get("axis_pos"),
        motion.get("axis_motor_status"),
    )
    axis["target_position"] = target_position
    axis["observed_reached"] = False
    if can_axis:
        can_position = can_axis.get("position")
        axis["can_position"] = can_position
        axis["can_age_s"] = can_axis.get("age_s")
        axis["can_communicated"] = can_axis.get("communicated")
        axis["can_rx_count"] = can_axis.get("rx_count")
        axis["can_iface"] = can_axis.get("iface")
        if target_position is not None and can_position is not None:
            axis["observed_reached"] = abs(int(can_position) - int(target_position)) <= RK_OBSERVED_REACHED_TOLERANCE_0P1DEG
    return {
        "active": motion.get("active_name"),
        "axis": axis,
    }


def public_power(power: Dict[str, Any]) -> Dict[str, Any]:
    last_error = power.get("last_error")
    return {
        "set_volt": power.get("set_volt"),
        "set_curr": power.get("set_curr"),
        "output_volt": power.get("output_volt"),
        "output_curr": power.get("output_curr"),
        "temperature": power.get("temperature"),
        "alarm": power.get("alarm"),
        "output_enabled": power.get("output_enabled"),
        "is_communicated": power.get("is_communicated"),
        "last_error": last_error,
        "last_error_name": power_error_name(last_error),
    }


def public_ups(ups: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "volt": ups.get("volt"),
        "curr": ups.get("curr"),
        "temp": ups.get("temp"),
        "status": ups.get("status"),
        "output_status": ups.get("output_status"),
        "software_version": ups.get("software_version"),
        "hardware_version": ups.get("hardware_version"),
        "request_power_off": ups.get("request_power_off"),
        "is_communicated": ups.get("is_communicated"),
    }


def public_env(env: Dict[str, Any]) -> Dict[str, Any]:
    last_error = env.get("last_error")
    return {
        "temperature": env.get("temperature"),
        "humidity": env.get("humidity"),
        "raw_temperature": env.get("raw_temperature"),
        "raw_humidity": env.get("raw_humidity"),
        "address": env.get("address"),
        "is_communicated": env.get("is_communicated"),
        "last_error": last_error,
        "last_error_name": env_error_name(last_error),
        "last_hal_status": env.get("last_hal_status"),
        "sample_count": env.get("sample_count"),
    }


def public_led(led: Dict[str, Any]) -> Dict[str, Any]:
    last_error = led.get("last_error")
    try:
        mask = int(led.get("mask") or 0) & 0xFF
    except (TypeError, ValueError):
        mask = 0
    groups = {}
    for name, (red_bit, green_bit) in LED_GROUP_BITS.items():
        groups[name] = {
            "red": bool(mask & (1 << red_bit)),
            "green": bool(mask & (1 << green_bit)),
        }
    return {
        "mask": mask,
        "input": led.get("input"),
        "polarity": led.get("polarity"),
        "config": led.get("config"),
        "address": led.get("address"),
        "is_communicated": led.get("is_communicated"),
        "last_error": last_error,
        "last_error_name": led_error_name(last_error),
        "last_hal_status": led.get("last_hal_status"),
        "write_count": led.get("write_count"),
        "groups": groups,
    }


def public_switches(switches: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "top": switches.get("top"),
        "bottom": switches.get("bottom"),
        "cover_button": switches.get("cover_button"),
        "platform_switch": switches.get("top"),
        "charge_base_switch": switches.get("bottom"),
        "psw1": switches.get("psw1"),
        "psw2": switches.get("psw2"),
        "psw3": switches.get("psw3"),
        "active_mask": switches.get("active_mask"),
        "raw_level_mask": switches.get("raw_level_mask"),
        "manual_action": switches.get("manual_action"),
        "manual_action_name": switches.get("manual_action_name"),
        "active_low": switches.get("active_low"),
    }


def public_ac(ac: Dict[str, Any]) -> Dict[str, Any]:
    last_error = ac.get("last_error")
    last_control_result = ac.get("last_control_result")
    try:
        alarm_bits = int(ac.get("alarms") or 0)
    except (TypeError, ValueError):
        alarm_bits = 0
    alarm_names = [
        name
        for bit, name in enumerate(AC_ALARM_NAMES)
        if alarm_bits & (1 << bit)
    ]
    action = ac.get("last_control_action")
    try:
        action_id = int(action)
    except (TypeError, ValueError):
        action_id = 0
    device_status = ac.get("device_status")
    try:
        device_status_id = int(device_status)
    except (TypeError, ValueError):
        device_status_id = 0
    run_mode = ac.get("run_mode")
    try:
        run_mode_id = int(run_mode)
    except (TypeError, ValueError):
        run_mode_id = -1
    return {
        "address": ac.get("address"),
        "is_communicated": ac.get("is_communicated"),
        "busy": ac.get("is_busy"),
        "device_status": device_status,
        "device_status_name": AC_DEVICE_STATUS_NAMES.get(device_status_id, f"unknown_{device_status}"),
        "indoor_fan_status": ac.get("indoor_fan_status"),
        "outdoor_fan_status": ac.get("outdoor_fan_status"),
        "compressor_status": ac.get("compressor_status"),
        "heater_status": ac.get("heater_status"),
        "return_air_temp": ac.get("return_air_temp"),
        "external_temp": ac.get("external_temp"),
        "condenser_temp": ac.get("condenser_temp"),
        "evaporator_temp": ac.get("evaporator_temp"),
        "indoor_fan_rpm": ac.get("indoor_fan_rpm"),
        "outdoor_fan_rpm": ac.get("outdoor_fan_rpm"),
        "dc_voltage": ac.get("dc_voltage"),
        "dc_current": ac.get("dc_current"),
        "cooling_capacity_w": ac.get("cooling_capacity_w"),
        "alarms": alarm_bits,
        "alarm_names": alarm_names,
        "protocol_version": ac.get("protocol_version"),
        "software_version": ac.get("software_version"),
        "hardware_version": ac.get("hardware_version"),
        "cool_start_temp": ac.get("cool_start_temp"),
        "cool_diff": ac.get("cool_diff"),
        "heat_start_temp": ac.get("heat_start_temp"),
        "heat_diff": ac.get("heat_diff"),
        "dehumid_setpoint": ac.get("dehumid_setpoint"),
        "run_mode": run_mode,
        "run_mode_name": {0: "normal", 1: "silent"}.get(run_mode_id, f"unknown_{run_mode}"),
        "monitor_humidity": ac.get("monitor_humidity"),
        "last_error": last_error,
        "last_error_name": ac_error_name(last_error),
        "last_exception": ac.get("last_exception"),
        "last_function": ac.get("last_function"),
        "last_control_action": action_id,
        "last_control_action_name": AC_CONTROL_NAMES.get(action_id, f"unknown_{action}"),
        "last_control_value": ac.get("last_control_value"),
        "last_control_result": last_control_result,
        "last_control_result_name": ac_error_name(last_control_result),
        "tx_count": ac.get("tx_count"),
        "rx_count": ac.get("rx_count"),
        "crc_error_count": ac.get("crc_error_count"),
        "timeout_count": ac.get("timeout_count"),
    }


def public_ok_from_ack(ack: Dict[str, Any]) -> Dict[str, Any]:
    if not ack.get("ok"):
        return {"ok": False, "error": ack.get("error", "mcu request failed")}
    result = ack.get("result")
    if result not in (0, None):
        return {"ok": False, "result": result, "error": f"mcu result {result}"}
    return {"ok": True}


class MotorCanListener:
    def __init__(self, iface: str, logger: logging.Logger):
        self.iface = iface
        self.logger = logger
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None
        self._position_0p1deg: Optional[int] = None
        self._status_raw: Optional[int] = None
        self._homing_raw: Optional[int] = None
        self._last_seen: Optional[float] = None
        self._last_position_seen: Optional[float] = None
        self._last_status_seen: Optional[float] = None
        self._last_homing_seen: Optional[float] = None
        self._rx_count = 0
        self._position_rx_count = 0
        self._status_rx_count = 0
        self._homing_rx_count = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="motor-can-listener", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            age = None if self._last_seen is None else max(0.0, now - self._last_seen)
            position_age = None if self._last_position_seen is None else max(0.0, now - self._last_position_seen)
            status_age = None if self._last_status_seen is None else max(0.0, now - self._last_status_seen)
            homing_age = None if self._last_homing_seen is None else max(0.0, now - self._last_homing_seen)
            status_raw = self._status_raw
            homing_raw = self._homing_raw
            homing_calibing = bool(homing_raw & 0x04) if homing_raw is not None else False
            homing_failed = bool(homing_raw & 0x08) if homing_raw is not None else False
            return {
                "position": self._position_0p1deg,
                "age_s": age,
                "position_age_s": position_age,
                "status_age_s": status_age,
                "homing_age_s": homing_age,
                "homing_seen_time": self._last_homing_seen,
                "communicated": age is not None and age <= 1.0,
                "status_raw": status_raw,
                "homing_raw": homing_raw,
                "driver_enabled": bool(status_raw & 0x01) if status_raw is not None else False,
                "driver_reached": bool(status_raw & 0x02) if status_raw is not None else False,
                "driver_stall": bool(status_raw & 0x08) if status_raw is not None else False,
                "calibed": homing_raw is not None and not homing_calibing and not homing_failed,
                "calibing": homing_calibing,
                "calib_failed": homing_failed,
                "rx_count": self._rx_count,
                "position_rx_count": self._position_rx_count,
                "status_rx_count": self._status_rx_count,
                "homing_rx_count": self._homing_rx_count,
                "iface": self.iface,
            }

    def _run(self) -> None:
        if not hasattr(socket, "AF_CAN"):
            self.logger.info("SocketCAN is not available on this Python build")
            return
        try:
            sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
            sock.settimeout(0.2)
            sock.bind((self.iface,))
            self._sock = sock
            self.logger.info("listening motor CAN on %s", self.iface)
        except OSError as exc:
            self.logger.info("motor CAN listener disabled on %s: %s", self.iface, exc)
            return

        while not self._stop.is_set():
            try:
                frame = sock.recv(CAN_FRAME_STRUCT.size)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(frame) < CAN_FRAME_STRUCT.size:
                continue
            can_id, dlc, data = CAN_FRAME_STRUCT.unpack(frame)
            if (can_id & CAN_EFF_MASK) != MOTOR_CAN_ID:
                continue
            now = time.monotonic()
            if dlc >= 7 and data[0] == 0x36 and data[6] == 0x6B:
                angle = (data[2] << 24) | (data[3] << 16) | (data[4] << 8) | data[5]
                if data[1] == 0x01:
                    angle = -angle
                with self._lock:
                    self._position_0p1deg = int(angle)
                    self._last_seen = now
                    self._last_position_seen = now
                    self._rx_count += 1
                    self._position_rx_count += 1
                continue
            if dlc >= 3 and data[0] == 0x3A and data[2] == 0x6B:
                with self._lock:
                    self._status_raw = int(data[1])
                    self._last_seen = now
                    self._last_status_seen = now
                    self._rx_count += 1
                    self._status_rx_count += 1
                continue
            if dlc >= 3 and data[0] == 0x3B and data[2] == 0x6B:
                with self._lock:
                    self._homing_raw = int(data[1])
                    self._last_seen = now
                    self._last_homing_seen = now
                    self._rx_count += 1
                    self._homing_rx_count += 1


class McuClient:
    def __init__(self, port: str, baud: int, logger: logging.Logger):
        self.port = port
        self.baud = baud
        self.logger = logger
        self._ser: Optional[serial.Serial] = None
        self._seq = 1
        self._lock = threading.RLock()
        self.last_messages: List[str] = []
        self._last_motor_target: Optional[int] = None
        self._motion_cv = threading.Condition()
        self._motion_events: List[Dict[str, Any]] = []
        self._motion_event_id = 0
        self._motion_id = 0
        self._active_motion: Optional[Dict[str, Any]] = None
        self._motion_stop = threading.Event()
        self._motion_thread = threading.Thread(target=self._motion_monitor, name="motion-event-monitor", daemon=True)
        can_iface = os.environ.get("INTERCEPTOR_CAN_IFACE", "can0").strip()
        self._can_listener: Optional[MotorCanListener] = None
        if can_iface and can_iface.lower() not in {"0", "false", "off", "none", "disabled"}:
            self._can_listener = MotorCanListener(can_iface, logger)
            self._can_listener.start()
        self._motion_thread.start()

    def open(self) -> None:
        if self._ser and self._ser.is_open:
            return
        self._ser = serial.Serial(self.port, self.baud, timeout=0.08, write_timeout=1.0)
        self.logger.info("opened MCU serial %s @ %s", self.port, self.baud)

    def _drop_serial_locked(self, reason: str) -> None:
        ser = self._ser
        self._ser = None
        if ser is None:
            return
        try:
            if ser.is_open:
                ser.close()
        except Exception as exc:
            self.logger.warning("error closing MCU serial after %s: %s", reason, exc)
        self.logger.warning("dropped MCU serial after %s", reason)

    def close(self) -> None:
        with self._lock:
            if self._ser and self._ser.is_open:
                self._ser.close()
            self._ser = None
        if self._can_listener:
            self._can_listener.stop()
        self._motion_stop.set()
        with self._motion_cv:
            self._motion_cv.notify_all()

    def _public_motion(self, status: Dict[str, Any]) -> Dict[str, Any]:
        can_axis = self._can_listener.snapshot() if self._can_listener else None
        return public_motion(status, can_axis, self._last_motor_target)

    def _motion_snapshot(self) -> Dict[str, Any]:
        if self._can_listener:
            return self._can_listener.snapshot()
        return {
            "position": None,
            "age_s": None,
            "communicated": False,
            "status_raw": None,
            "homing_raw": None,
            "homing_age_s": None,
            "homing_seen_time": None,
            "driver_enabled": False,
            "driver_reached": False,
            "driver_stall": False,
            "calibed": False,
            "calibing": False,
            "calib_failed": False,
            "iface": "",
        }

    def _motion_event(
        self,
        motion: Dict[str, Any],
        event_type: str,
        reason: str,
        snapshot: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        snapshot = snapshot or self._motion_snapshot()
        now = time.monotonic()
        position = snapshot.get("position")
        target = motion.get("target_position")
        error = int(position) - int(target) if position is not None and target is not None else None
        event: Dict[str, Any] = {
            "ok": True,
            "motion_ok": event_type == "motion_reached",
            "type": event_type,
            "final": event_type in {"motion_reached", "motion_timeout", "motion_failed", "motion_canceled"},
            "motion_id": motion.get("motion_id"),
            "action": motion.get("action"),
            "monitor": motion.get("monitor", "position"),
            "reason": reason,
            "target_position": target,
            "speed": motion.get("speed"),
            "accel": motion.get("accel"),
            "position": position,
            "error": error,
            "tolerance": motion.get("tolerance", RK_OBSERVED_REACHED_TOLERANCE_0P1DEG),
            "elapsed_s": round(max(0.0, now - float(motion.get("start_time", now))), 3),
            "can_communicated": bool(snapshot.get("communicated")),
            "can_age_s": snapshot.get("age_s"),
            "can_position_age_s": snapshot.get("position_age_s"),
            "can_status_age_s": snapshot.get("status_age_s"),
            "can_status": snapshot.get("status_raw"),
            "can_homing_age_s": snapshot.get("homing_age_s"),
            "can_homing_status": snapshot.get("homing_raw"),
            "can_enabled": bool(snapshot.get("driver_enabled")),
            "can_reached": bool(snapshot.get("driver_reached")),
            "can_stall": bool(snapshot.get("driver_stall")),
            "can_iface": snapshot.get("iface"),
            "calibed": snapshot.get("calibed"),
            "calibing": snapshot.get("calibing"),
            "calib_failed": snapshot.get("calib_failed"),
            "mcu_state": snapshot.get("mcu_state"),
            "mcu_enabled": snapshot.get("mcu_enabled"),
            "mcu_stall": snapshot.get("mcu_stall"),
        }
        if extra:
            event.update(extra)
        return event

    def _append_motion_event_locked(self, event: Dict[str, Any]) -> Dict[str, Any]:
        self._motion_event_id += 1
        event["event_id"] = self._motion_event_id
        self._motion_events.append(event)
        self._motion_events = self._motion_events[-MOTION_EVENT_BACKLOG:]
        self._motion_cv.notify_all()
        return event

    def _log_motion_event(self, event: Dict[str, Any]) -> None:
        log = self.logger.info if event.get("type") == "motion_reached" else self.logger.warning
        log(
            "motion event type=%s event_id=%s motion_id=%s action=%s reason=%s elapsed=%.3fs "
            "target=%s/0.1deg position=%s/0.1deg error=%s tolerance=%s "
            "can=%s age=%s status=%s enabled=%s reached=%s stall=%s "
            "homing_status=%s homing_age=%s calibed=%s calibing=%s calib_failed=%s",
            event.get("type"),
            event.get("event_id"),
            event.get("motion_id"),
            event.get("action"),
            event.get("reason"),
            float(event.get("elapsed_s") or 0.0),
            event.get("target_position"),
            event.get("position"),
            event.get("error"),
            event.get("tolerance"),
            event.get("can_communicated"),
            event.get("can_age_s"),
            event.get("can_status"),
            event.get("can_enabled"),
            event.get("can_reached"),
            event.get("can_stall"),
            event.get("can_homing_status"),
            event.get("can_homing_age_s"),
            event.get("calibed"),
            event.get("calibing"),
            event.get("calib_failed"),
        )

    def _emit_motion_event(
        self,
        motion: Dict[str, Any],
        event_type: str,
        reason: str,
        snapshot: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._motion_cv:
            event = self._motion_event(motion, event_type, reason, snapshot, extra)
            self._append_motion_event_locked(event)
        self._log_motion_event(event)
        return event

    def _begin_motion(
        self,
        action: str,
        target_position: Optional[int],
        speed: Optional[int],
        accel: Optional[int],
        timeout: float,
        monitor: str = "position",
    ) -> int:
        with self._motion_cv:
            previous = self._active_motion
            self._motion_id += 1
            motion_id = self._motion_id
            if previous is not None:
                canceled = self._motion_event(
                    previous,
                    "motion_canceled",
                    "target_replaced",
                    extra={"new_motion_id": motion_id},
                )
                self._append_motion_event_locked(canceled)
                self._log_motion_event(canceled)

            track_timeout = max(0.1, float(timeout))
            motion = {
                "motion_id": motion_id,
                "action": action,
                "target_position": int(target_position) if target_position is not None else None,
                "speed": speed,
                "accel": accel,
                "monitor": monitor,
                "tolerance": RK_OBSERVED_REACHED_TOLERANCE_0P1DEG,
            "timeout": track_timeout,
            "start_time": time.monotonic(),
            "home_progress_seen": False,
        }
            self._active_motion = motion
            started = self._motion_event(motion, "motion_started", "accepted")
            self._append_motion_event_locked(started)
            self._motion_cv.notify_all()
        self._log_motion_event(started)
        return motion_id

    def _cancel_active_motion(self, reason: str, extra: Optional[Dict[str, Any]] = None) -> None:
        with self._motion_cv:
            motion = self._active_motion
            if motion is None:
                return
            event = self._motion_event(motion, "motion_canceled", reason, extra=extra)
            self._append_motion_event_locked(event)
            self._active_motion = None
        self._log_motion_event(event)

    def _finish_active_motion_locked(
        self,
        event_type: str,
        reason: str,
        snapshot: Dict[str, Any],
        extra: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        motion = self._active_motion
        if motion is None:
            return None
        event = self._motion_event(motion, event_type, reason, snapshot, extra)
        self._append_motion_event_locked(event)
        self._active_motion = None
        return event

    def _motion_monitor(self) -> None:
        while not self._motion_stop.wait(MOTION_MONITOR_PERIOD_S):
            event = None
            with self._motion_cv:
                active = self._active_motion
                if active is None:
                    continue
                snapshot = self._motion_snapshot()
                now = time.monotonic()
                elapsed = now - float(active.get("start_time", now))

                if active.get("monitor") == "home":
                    homing_seen = snapshot.get("homing_seen_time")
                    fresh_homing = homing_seen is not None and float(homing_seen) >= float(active.get("start_time", now))
                    if fresh_homing and bool(snapshot.get("calibing")):
                        active["home_progress_seen"] = True

                    if fresh_homing and bool(snapshot.get("calib_failed")):
                        event = self._finish_active_motion_locked("motion_failed", "homing_failed", snapshot)
                    elif fresh_homing and bool(snapshot.get("calibed")):
                        event = self._finish_active_motion_locked("motion_reached", "homing_done", snapshot)
                    elif bool(snapshot.get("driver_stall")):
                        event = self._finish_active_motion_locked("motion_failed", "driver_stall", snapshot)
                    elif elapsed >= float(active.get("timeout", MOTION_ASYNC_TIMEOUT_S)):
                        reason = "homing_timeout" if fresh_homing else "homing_status_lost"
                        event = self._finish_active_motion_locked("motion_timeout", reason, snapshot)
                else:
                    position = snapshot.get("position")
                    target = active.get("target_position")
                    tolerance = int(active.get("tolerance", RK_OBSERVED_REACHED_TOLERANCE_0P1DEG))
                    if position is not None and target is not None and abs(int(position) - int(target)) <= tolerance:
                        event = self._finish_active_motion_locked("motion_reached", "position_within_tolerance", snapshot)
                    elif bool(snapshot.get("driver_stall")):
                        event = self._finish_active_motion_locked("motion_failed", "driver_stall", snapshot)
                    elif elapsed >= float(active.get("timeout", MOTION_ASYNC_TIMEOUT_S)):
                        reason = "can_lost"
                        age = snapshot.get("age_s")
                        if age is not None and age <= MOTION_CAN_LOST_TIMEOUT_S:
                            reason = "still_moving"
                        event = self._finish_active_motion_locked("motion_timeout", reason, snapshot)

            if event is not None:
                self._log_motion_event(event)

    def wait_motion_event(self, after_event_id: int = 0, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._motion_cv:
            while True:
                for event in self._motion_events:
                    if int(event.get("event_id", 0)) > after_event_id:
                        return dict(event)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._motion_cv.wait(min(remaining, 0.5))

    def wait_motion_final(self, motion_id: int, timeout: float) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._motion_cv:
            while True:
                for event in self._motion_events:
                    if int(event.get("motion_id", 0)) == int(motion_id) and bool(event.get("final")):
                        return dict(event)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._motion_cv.wait(min(remaining, 0.5))

    def latest_motion_event_id(self) -> int:
        with self._motion_cv:
            return self._motion_event_id

    def transact(self, name: str, cmd_set: int, cmd_id: int, payload: bytes = b"", timeout: float = 6.0) -> Dict[str, Any]:
        with self._lock:
            messages: List[str] = []
            start = time.monotonic()
            try:
                self.open()
                assert self._ser is not None
                packet = build_packet(self._seq, cmd_set, cmd_id, payload)
                self._seq = (self._seq + 1) & 0xFFFF

                self._ser.reset_input_buffer()
                self.logger.info(
                    "mcu tx name=%s cmd=%s payload=%s",
                    name,
                    command_label(cmd_set, cmd_id),
                    decode_tx_payload(cmd_set, cmd_id, payload),
                )
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
                        elapsed_ms = (time.monotonic() - start) * 1000.0
                        self.logger.info(
                            "mcu rx name=%s cmd=%s elapsed=%.1fms data=%s",
                            name,
                            command_label(cmd_set, cmd_id),
                            elapsed_ms,
                            decode_rx_summary(cmd_set, cmd_id, data),
                        )
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
            except (serial.SerialException, OSError) as exc:
                elapsed_ms = (time.monotonic() - start) * 1000.0
                self._drop_serial_locked(f"{name} serial error")
                self.logger.error(
                    "mcu serial error name=%s cmd=%s elapsed=%.1fms error=%s",
                    name,
                    command_label(cmd_set, cmd_id),
                    elapsed_ms,
                    exc,
                )
                return {
                    "ok": False,
                    "name": name,
                    "cmd_set": cmd_set,
                    "cmd_id": cmd_id,
                    "error": f"serial error: {exc}",
                    "messages": messages,
                }

            elapsed_ms = (time.monotonic() - start) * 1000.0
            self.logger.warning(
                "mcu timeout name=%s cmd=%s elapsed=%.1fms payload=%s",
                name,
                command_label(cmd_set, cmd_id),
                elapsed_ms,
                decode_tx_payload(cmd_set, cmd_id, payload),
            )
            self._drop_serial_locked(f"{name} ack timeout")
            return {
                "ok": False,
                "name": name,
                "cmd_set": cmd_set,
                "cmd_id": cmd_id,
                "error": "ack timeout",
                "messages": messages,
            }

    def get_version(self) -> Dict[str, Any]:
        resp = self.transact_readonly("get_version", CMD_SET_COMMON, CMD_ID_COMMON_GET_VERSION, timeout=8.0)
        if resp["ok"] and len(resp["data"]) >= 2:
            version = struct.unpack_from("<H", resp["data"], 0)[0]
            return {"ok": True, "version": f"0x{version:04x}"}
        return {"ok": False, "error": resp.get("error", "version read failed")}

    def release_stop(self) -> Dict[str, Any]:
        resp = public_ok_from_ack(self.transact("release_stop", CMD_SET_MOTOR, CMD_ID_MOTOR_RELEASE_STOP))
        if resp.get("ok"):
            self._cancel_active_motion("release_stop")
        return resp

    def stop_motors(self) -> Dict[str, Any]:
        resp = public_ok_from_ack(self.transact("stop_motors", CMD_SET_MOTOR, CMD_ID_MOTOR_STOP))
        if resp.get("ok"):
            self._last_motor_target = None
            self._cancel_active_motion("motor_stop")
        return resp

    def get_status(self) -> Dict[str, Any]:
        resp = self.transact_readonly("get_status", CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_GET_STATUS, timeout=8.0)
        if resp["ok"]:
            status = parse_status(resp["data"])
            return {"ok": True, "motor": self._public_motion(status), "power": public_power(status.get("power") or {})}
        return {"ok": False, "error": resp.get("error", "status read failed")}

    def get_motor_status(self) -> Dict[str, Any]:
        resp = self.transact_readonly("motor_status", CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_GET_STATUS, timeout=8.0)
        if resp["ok"]:
            return {"ok": True, "motor": self._public_motion(parse_status(resp["data"]))}
        return {"ok": False, "error": resp.get("error", "motor status read failed")}

    def get_stop_status(self) -> Dict[str, Any]:
        resp = self.transact_readonly("stop_status", CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_STOP_STATUS, timeout=2.0)
        if resp["ok"]:
            stop = parse_stop_status(resp["data"])
            return {"ok": True, "hardware_stop": stop.get("hardware_stop")}
        return {"ok": False, "error": resp.get("error", "stop status read failed")}

    def get_ups_status(self) -> Dict[str, Any]:
        resp = self.transact_readonly("ups_status", CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_UPS_STATUS, timeout=2.0)
        if resp["ok"]:
            return {"ok": True, "ups": public_ups(parse_ups_status(resp["data"]))}
        return {"ok": False, "error": resp.get("error", "ups status read failed")}

    def get_env_status(self) -> Dict[str, Any]:
        resp = self.transact_readonly("env_status", CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_ENV_STATUS, timeout=2.0)
        if resp["ok"]:
            return {"ok": True, "environment": public_env(parse_env_status(resp["data"]))}
        return {"ok": False, "error": resp.get("error", "environment status read failed")}

    def get_led_status(self) -> Dict[str, Any]:
        resp = self.transact_readonly("led_status", CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_LED_STATUS, timeout=2.0)
        if resp["ok"]:
            return {"ok": True, "led": public_led(parse_led_status(resp["data"]))}
        return {"ok": False, "error": resp.get("error", "LED status read failed")}

    def get_switch_status(self) -> Dict[str, Any]:
        resp = self.transact_readonly("switch_status", CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_SWITCH_STATUS, timeout=2.0)
        if resp["ok"]:
            return {"ok": True, "switches": public_switches(parse_switch_status(resp["data"]))}
        return {"ok": False, "error": resp.get("error", "switch status read failed")}

    def get_ac_status(self) -> Dict[str, Any]:
        resp = self.transact_readonly("ac_status", CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_AC_STATUS, timeout=2.0)
        if resp["ok"]:
            return {"ok": True, "ac": public_ac(parse_ac_status(resp["data"]))}
        return {"ok": False, "error": resp.get("error", "AC status read failed")}

    def ac_control(
        self,
        action: Any,
        value: int,
        wait: bool = True,
        timeout: float = 3.0,
    ) -> Dict[str, Any]:
        action_id = ac_action_id(action)
        if action_id is None:
            return {"ok": False, "error": f"unknown AC action: {action}"}
        encoded_value = int(value) & 0xFFFF
        payload = struct.pack("<BH", action_id, encoded_value)
        ack = self.transact(
            "ac_control",
            CMD_SET_INTERCEPTOR,
            CMD_ID_INTERCEPTOR_AC_CONTROL,
            payload,
            timeout=2.0,
        )
        if not ack.get("ok"):
            return {"ok": False, "error": ack.get("error", "AC control failed")}
        data = ack.get("data") or b""
        if len(data) < 1:
            return {"ok": False, "error": "AC control response too short"}
        result = data[0]
        latest = public_ac(parse_ac_status(data[1:])) if len(data) >= 1 + AC_STATUS_STRUCT.size else None
        resp: Dict[str, Any] = {
            "ok": result == 0,
            "accepted": result == 0,
            "result": result,
            "result_name": ac_error_name(result),
            "requested": {
                "action": action_id,
                "action_name": AC_CONTROL_NAMES.get(action_id, f"unknown_{action_id}"),
                "value": int(value),
                "encoded_value": encoded_value,
            },
        }
        if latest:
            resp["ac"] = latest
        if result != 0:
            resp["error"] = resp["result_name"]
            return resp
        if not wait:
            return resp

        deadline = time.monotonic() + max(0.1, float(timeout))
        while time.monotonic() < deadline:
            time.sleep(0.15)
            status_resp = self.get_ac_status()
            if status_resp.get("ok"):
                latest = status_resp.get("ac")
                resp["ac"] = latest
                if latest and latest.get("last_control_action") == action_id:
                    control_result = latest.get("last_control_result")
                    if control_result != 1 and not latest.get("busy"):
                        resp["ok"] = control_result == 0
                        resp["result"] = control_result
                        resp["result_name"] = ac_error_name(control_result)
                        if control_result != 0:
                            resp["error"] = resp["result_name"]
                        return resp

        resp["ok"] = False
        resp["error"] = "AC control wait timeout"
        return resp

    def led_set_mask(self, mask: int) -> Dict[str, Any]:
        mask = int(mask) & 0xFF
        ack = self.transact("led_set", CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_LED_SET, bytes([mask]), timeout=2.0)
        if not ack.get("ok"):
            return {"ok": False, "error": ack.get("error", "LED set failed")}
        data = ack.get("data") or b""
        if len(data) < 11:
            return {"ok": False, "error": "LED set response too short"}
        result = data[0]
        led = public_led(parse_led_status(data[1:]))
        resp = {
            "ok": result == 0,
            "result": result,
            "result_name": led_error_name(result),
            "requested": {"mask": mask},
            "led": led,
        }
        if result != 0:
            resp["error"] = resp["result_name"]
        return resp

    def led_set_group(self, group: str, color: str) -> Dict[str, Any]:
        group = group.lower()
        color = color.lower()
        color_bits = LED_COLOR_BITS.get(color)
        if color_bits is None:
            return {"ok": False, "error": f"unknown LED color: {color}"}

        if group == "all":
            mask = 0
            for red_bit, green_bit in LED_GROUP_BITS.values():
                if color_bits & 0x01:
                    mask |= 1 << red_bit
                if color_bits & 0x02:
                    mask |= 1 << green_bit
            return self.led_set_mask(mask)

        bits = LED_GROUP_BITS.get(group)
        if bits is None:
            return {"ok": False, "error": f"unknown LED group: {group}"}

        current = self.get_led_status()
        if not current.get("ok"):
            return current
        mask = int(((current.get("led") or {}).get("mask") or 0)) & 0xFF
        red_bit, green_bit = bits
        mask &= ~((1 << red_bit) | (1 << green_bit)) & 0xFF
        if color_bits & 0x01:
            mask |= 1 << red_bit
        if color_bits & 0x02:
            mask |= 1 << green_bit
        resp = self.led_set_mask(mask)
        if resp.get("ok"):
            resp["requested"] = {"group": group, "color": color, "mask": mask}
        return resp

    def transact_readonly(
        self,
        name: str,
        cmd_set: int,
        cmd_id: int,
        payload: bytes = b"",
        timeout: float = 6.0,
        attempts: int = 2,
    ) -> Dict[str, Any]:
        last = None
        for attempt in range(1, attempts + 1):
            last = self.transact(name, cmd_set, cmd_id, payload, timeout)
            if last.get("ok"):
                return last
            self.logger.warning("%s attempt %s/%s failed: %s", name, attempt, attempts, last.get("error"))
            time.sleep(0.2)
        assert last is not None
        return last

    def interceptor_cmd(
        self,
        name: str,
        cmd_id: int,
        wait: bool = False,
        timeout: float = 20.0,
        target_position: Optional[int] = None,
    ) -> Dict[str, Any]:
        ack = self.transact(name, CMD_SET_INTERCEPTOR, cmd_id)
        result: Dict[str, Any] = public_ok_from_ack(ack)
        if not result.get("ok"):
            return result
        result["accepted"] = True
        if target_position is not None:
            self._last_motor_target = int(target_position)
            result["target_position"] = self._last_motor_target
            motion_id = self._begin_motion(name, self._last_motor_target, None, None, timeout)
            result["motion_id"] = motion_id
        if not wait:
            return result

        event = self.wait_motion_final(int(result.get("motion_id", 0)), timeout)
        result["motion_event"] = event
        if event and event.get("type") == "motion_reached":
            return result
        result["wait_error"] = (event or {}).get("reason", "motion timeout")
        result["ok"] = False
        result["error"] = result["wait_error"]
        return result

    def door_open(self, wait: bool = False, timeout: float = 20.0) -> Dict[str, Any]:
        return self._send_motor_trapezoid(
            "door_open",
            DOOR_OPEN_POSITION_0P1DEG,
            DOOR_OPEN_SPEED_0P1RPM,
            DOOR_OPEN_ACCEL_RPM_S,
            wait,
            timeout,
        )

    def door_close(self, wait: bool = False, timeout: float = 20.0) -> Dict[str, Any]:
        return self._send_motor_trapezoid(
            "door_close",
            DOOR_CLOSE_POSITION_0P1DEG,
            DOOR_CLOSE_SPEED_0P1RPM,
            DOOR_CLOSE_ACCEL_RPM_S,
            wait,
            timeout,
        )

    def _send_motor_trapezoid(
        self,
        name: str,
        position: int,
        speed: int,
        accel: int,
        wait: bool = False,
        timeout: float = 20.0,
    ) -> Dict[str, Any]:
        target_id = 0
        payload = struct.pack("<BiHH", target_id, position, speed, accel)
        ack = self.transact(name, CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_MOTOR_TRAPEZOID, payload)
        result: Dict[str, Any] = public_ok_from_ack(ack)
        if not result.get("ok"):
            return result
        result["accepted"] = True
        self._last_motor_target = int(position)
        result["target_position"] = self._last_motor_target
        motion_id = self._begin_motion(
            name,
            self._last_motor_target,
            int(speed),
            int(accel),
            timeout,
        )
        result["motion_id"] = motion_id
        if not wait:
            return result

        event = self.wait_motion_final(motion_id, timeout)
        result["motion_event"] = event
        if event and event.get("type") == "motion_reached":
            return result
        result["wait_error"] = (event or {}).get("reason", "motion timeout")
        result["ok"] = False
        result["error"] = result["wait_error"]
        return result

    def motor_trapezoid(
        self,
        target: str,
        position: int,
        speed: int,
        accel: int,
        wait: bool = False,
        timeout: float = 20.0,
    ) -> Dict[str, Any]:
        target_id = {"door": 0, "motor": 0, "motor1": 0}.get(target.lower())
        if target_id is None:
            return {"ok": False, "error": f"unknown motor target: {target}"}
        return self._send_motor_trapezoid(
            "motor_trapezoid",
            position,
            speed,
            accel,
            wait,
            timeout,
        )

    def motor_home(self, target: str, wait: bool = False, timeout: float = 60.0) -> Dict[str, Any]:
        target_id = {"door": 0, "motor": 0, "motor1": 0}.get(target.lower())
        if target_id is None:
            return {"ok": False, "error": f"unknown motor target: {target}"}
        payload = struct.pack("<BB", target_id, 1)
        ack = self.transact("motor_home", CMD_SET_MOTOR, CMD_ID_MOTOR_CALIB, payload, timeout=2.0)
        result: Dict[str, Any] = public_ok_from_ack(ack)
        if not result.get("ok"):
            return result
        result["accepted"] = True
        result["requested"] = {"target": "door", "action": "home"}
        self._last_motor_target = None
        motion_id = self._begin_motion(
            "motor_home",
            None,
            None,
            None,
            timeout,
            monitor="home",
        )
        result["motion_id"] = motion_id
        if not wait:
            return result

        event = self.wait_motion_final(motion_id, timeout)
        result["motion_event"] = event
        if event and event.get("type") == "motion_reached":
            return result
        result["wait_error"] = (event or {}).get("reason", "homing timeout")
        result["ok"] = False
        result["error"] = result["wait_error"]
        return result

    def motor_home_stop(self, target: str) -> Dict[str, Any]:
        target_id = {"door": 0, "motor": 0, "motor1": 0}.get(target.lower())
        if target_id is None:
            return {"ok": False, "error": f"unknown motor target: {target}"}
        payload = struct.pack("<BB", target_id, 0)
        ack = self.transact("motor_home_stop", CMD_SET_MOTOR, CMD_ID_MOTOR_CALIB, payload, timeout=2.0)
        resp = public_ok_from_ack(ack)
        if resp.get("ok"):
            resp["requested"] = {"target": "door", "action": "home_stop"}
            self._cancel_active_motion("homing_stop")
        return resp

    def motor_enable(self, target: str, enabled: bool) -> Dict[str, Any]:
        target_id = {"door": 0, "motor": 0, "motor1": 0}.get(target.lower())
        if target_id is None:
            return {"ok": False, "error": f"unknown motor target: {target}"}
        payload = struct.pack("<BB", target_id, 1 if enabled else 0)
        with self._lock:
            ack = self.transact(
                "motor_enable",
                CMD_SET_INTERCEPTOR,
                CMD_ID_INTERCEPTOR_MOTOR_ENABLE,
                payload,
                timeout=2.0,
            )
            resp = public_ok_from_ack(ack)
            if resp.get("ok"):
                resp["requested"] = {"target": "door", "enabled": enabled}
            return resp

    def power_query(self, temperature: bool = False) -> Dict[str, Any]:
        with self._lock:
            payload = b"\x01" if temperature else b""
            ack = self.transact(
                "power_query_temp" if temperature else "power_query_status",
                CMD_SET_INTERCEPTOR,
                CMD_ID_INTERCEPTOR_POWER_QUERY,
                payload,
                timeout=2.0,
            )
            return self._power_response(ack)

    def power_set(self, voltage: int, current: int) -> Dict[str, Any]:
        with self._lock:
            payload = struct.pack("<HH", voltage, current)
            ack = self.transact("power_set", CMD_SET_INTERCEPTOR, CMD_ID_INTERCEPTOR_POWER_SET_PARAM, payload, timeout=2.0)
            resp = self._power_control_response(ack)
            if resp.get("ok"):
                resp["requested"] = {"set_volt": voltage, "set_curr": current}
            return resp

    def power_output(self, enabled: bool) -> Dict[str, Any]:
        with self._lock:
            ack = self.transact(
                "power_output",
                CMD_SET_INTERCEPTOR,
                CMD_ID_INTERCEPTOR_POWER_OUTPUT,
                b"\x01" if enabled else b"\x00",
                timeout=2.0,
            )
            resp = self._power_control_response(ack)
            if resp.get("ok"):
                resp["requested"] = {"output_enabled": enabled}
            return resp

    def power_raw_transfer(self, tx: bytes, timeout_ms: int = 1000, idle_ms: int = 20) -> Dict[str, Any]:
        if not tx:
            return {"ok": False, "error": "power raw tx payload is empty"}
        if len(tx) > 220:
            return {"ok": False, "error": "power raw tx payload too large; max is 220 bytes"}
        timeout_ms = max(1, min(int(timeout_ms), 10000))
        idle_ms = max(1, min(int(idle_ms), 1000))
        payload = struct.pack("<HHH", timeout_ms, idle_ms, len(tx)) + tx
        ack = self.transact(
            "power_raw_transfer",
            CMD_SET_INTERCEPTOR,
            CMD_ID_INTERCEPTOR_POWER_RAW_TRANSFER,
            payload,
            timeout=(timeout_ms / 1000.0) + 1.0,
        )
        if not ack.get("ok"):
            return {"ok": False, "error": ack.get("error", "power raw transfer failed")}
        data = ack.get("data") or b""
        if ack.get("ok") and len(data) >= 3:
            result = data[0]
            rx_len = struct.unpack_from("<H", data, 1)[0]
            rx = data[3 : 3 + rx_len]
            return {
                "ok": result == 0,
                "result": result,
                "result_name": power_error_name(result),
                "rx_len": len(rx),
                "rx_hex": rx.hex(),
            }
        return {"ok": False, "error": "power raw response too short"}

    def aircraft_transfer(self, tx: bytes, timeout_ms: int = 1000, idle_ms: int = 30) -> Dict[str, Any]:
        if not tx:
            return {"ok": False, "error": "aircraft tx payload is empty"}
        if len(tx) > 220:
            return {"ok": False, "error": "aircraft tx payload too large; max is 220 bytes"}
        timeout_ms = max(1, min(int(timeout_ms), 10000))
        idle_ms = max(1, min(int(idle_ms), 1000))
        payload = struct.pack("<HHH", timeout_ms, idle_ms, len(tx)) + tx
        ack = self.transact(
            "aircraft_transfer",
            CMD_SET_INTERCEPTOR,
            CMD_ID_INTERCEPTOR_AIRCRAFT_TRANSFER,
            payload,
            timeout=(timeout_ms / 1000.0) + 1.0,
        )
        if not ack.get("ok"):
            return {"ok": False, "error": ack.get("error", "aircraft transfer failed")}
        data = ack.get("data") or b""
        if ack.get("ok") and len(data) >= 3:
            result = data[0]
            rx_len = struct.unpack_from("<H", data, 1)[0]
            rx = data[3 : 3 + rx_len]
            resp = {
                "ok": result == 0,
                "result": result,
                "result_name": aircraft_result_name(result),
                "rx_len": len(rx),
                "rx_hex": rx.hex(),
            }
            if result != 0:
                resp["error"] = resp["result_name"]
            return resp
        return {"ok": False, "error": "aircraft response too short"}

    def aircraft_read(self, timeout_ms: int = 1000, max_len: int = 220) -> Dict[str, Any]:
        timeout_ms = max(0, min(int(timeout_ms), 10000))
        max_len = max(1, min(int(max_len), 220))
        payload = struct.pack("<HH", timeout_ms, max_len)
        ack = self.transact(
            "aircraft_read",
            CMD_SET_INTERCEPTOR,
            CMD_ID_INTERCEPTOR_AIRCRAFT_READ,
            payload,
            timeout=(timeout_ms / 1000.0) + 1.0,
        )
        if not ack.get("ok"):
            return {"ok": False, "error": ack.get("error", "aircraft read failed")}
        data = ack.get("data") or b""
        if len(data) >= 7:
            result = data[0]
            rx_len, dropped, remaining = struct.unpack_from("<HHH", data, 1)
            rx = data[7 : 7 + rx_len]
            resp = {
                "ok": result == 0,
                "result": result,
                "result_name": aircraft_result_name(result),
                "rx_len": len(rx),
                "rx_hex": rx.hex(),
                "dropped": dropped,
                "remaining": remaining,
            }
            if result != 0:
                resp["error"] = resp["result_name"]
            return resp
        return {"ok": False, "error": "aircraft read response too short"}

    @staticmethod
    def _power_response(ack: Dict[str, Any]) -> Dict[str, Any]:
        if not ack.get("ok"):
            return {"ok": False, "error": ack.get("error", "power request failed")}
        data = ack.get("data") or b""
        if ack.get("ok") and len(data) >= 1:
            result = data[0]
            if result != 0:
                return {"ok": False, "result": result, "error": f"power result {result}"}
            return {"ok": True, "power": public_power(parse_power_status(data[1:]))}
        return {"ok": False, "error": "power response too short"}

    @staticmethod
    def _power_control_response(ack: Dict[str, Any]) -> Dict[str, Any]:
        if not ack.get("ok"):
            return {"ok": False, "error": ack.get("error", "power request failed")}
        data = ack.get("data") or b""
        if len(data) >= 1:
            result = data[0]
            if result != 0:
                return {"ok": False, "result": result, "error": f"power result {result}"}
            return {"ok": True}
        return {"ok": False, "error": "power response too short"}
