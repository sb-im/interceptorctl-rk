#!/usr/bin/env python3
import argparse
import json
import socket
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Optional


DEFAULT_SOCKET = "/tmp/interceptorctl.sock"


def request(socket_path: str, cmd: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = json.dumps({"cmd": cmd, "args": args or {}}, ensure_ascii=False).encode("utf-8") + b"\n"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(socket_path)
        sock.sendall(payload)
        chunks = []
        while True:
            part = sock.recv(65536)
            if not part:
                break
            chunks.append(part)
    except FileNotFoundError as exc:
        raise RuntimeError(f"daemon socket not found: {socket_path}. Start it with ./run.sh") from exc
    except ConnectionRefusedError as exc:
        raise RuntimeError(f"daemon is not accepting connections on {socket_path}") from exc
    finally:
        sock.close()
    data = b"".join(chunks).decode("utf-8").strip()
    if not data:
        return {"ok": False, "error": "empty daemon response"}
    return json.loads(data)


def centi_value(value: str, unit: str) -> int:
    try:
        decimal = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"invalid {unit}: {value}") from exc
    scaled = (decimal * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if scaled < 0 or scaled > 65535:
        raise argparse.ArgumentTypeError(f"{unit} out of range: {value}")
    return int(scaled)


def tenth_value(value: str, unit: str, minimum: int, maximum: int) -> int:
    try:
        decimal = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"invalid {unit}: {value}") from exc
    scaled = (decimal * Decimal("10")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    raw = int(scaled)
    if raw < minimum or raw > maximum:
        raise argparse.ArgumentTypeError(f"{unit} out of range: {value}")
    return raw


def parse_hex_bytes(value: str) -> bytes:
    normalized = value.replace(",", " ").replace(":", " ").replace("_", " ")
    parts = normalized.split()
    if not parts:
        raise argparse.ArgumentTypeError("empty hex payload")
    out = bytearray()
    try:
        for part in parts:
            token = part[2:] if part.lower().startswith("0x") else part
            if len(token) % 2 != 0:
                raise ValueError
            out.extend(bytes.fromhex(token))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid hex payload: {value}") from exc
    return bytes(out)


def u8_value(value: str) -> int:
    try:
        out = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid 8-bit value: {value}") from exc
    if out < 0 or out > 0xFF:
        raise argparse.ArgumentTypeError(f"8-bit value out of range: {value}")
    return out


def percent_value(value: str) -> int:
    out = u8_value(value)
    if out > 100:
        raise argparse.ArgumentTypeError(f"percent value out of range: {value}")
    return out


def dehumid_value(value: str) -> int:
    out = u8_value(value)
    if out < 10 or out > 90:
        raise argparse.ArgumentTypeError(f"dehumid setpoint out of range: {value}")
    return out


def ac_cool_start_temp(value: str) -> int:
    return tenth_value(value, "cool start temperature", 200, 500)


def ac_cool_diff(value: str) -> int:
    return tenth_value(value, "cool temperature hysteresis", 10, 100)


def ac_heat_start_temp(value: str) -> int:
    return tenth_value(value, "heat start temperature", -400, 250)


def ac_heat_diff(value: str) -> int:
    return tenth_value(value, "heat temperature hysteresis", 50, 150)


def print_json(resp: Dict[str, Any]) -> None:
    print(json.dumps(resp, ensure_ascii=False, indent=2))


def ack_ok(resp: Dict[str, Any]) -> bool:
    return bool(resp.get("ok"))


def scaled(value: Any, divisor: int, digits: int, unit: str) -> str:
    if value is None:
        return f"--{unit}"
    try:
        return f"{int(value) / divisor:.{digits}f}{unit}"
    except (TypeError, ValueError):
        return f"{value}{unit}"


def format_power(power: Dict[str, Any]) -> str:
    return (
        "power: "
        f"set={scaled(power.get('set_volt'), 100, 2, 'V')} "
        f"{scaled(power.get('set_curr'), 100, 2, 'A')} "
        f"out={scaled(power.get('output_volt'), 100, 2, 'V')} "
        f"{scaled(power.get('output_curr'), 100, 2, 'A')} "
        f"temp={scaled(power.get('temperature'), 10, 1, 'C')} "
        f"enabled={power.get('output_enabled')} communicated={power.get('is_communicated')} "
        f"last_error={power.get('last_error')}({power.get('last_error_name')})"
    )


def print_power_command_target(command: str, resp: Dict[str, Any]) -> None:
    requested = resp.get("requested") or {}
    if not ack_ok(resp):
        print(f"result={resp.get('result')} error={resp.get('error')}")
        return

    if command == "power_set":
        print(
            "target: "
            f"set={scaled(requested.get('set_volt'), 100, 2, 'V')} "
            f"{scaled(requested.get('set_curr'), 100, 2, 'A')}"
        )
    elif command in {"power_on", "power_off"}:
        print(f"target: enabled={requested.get('output_enabled')}")


def print_status(resp: Dict[str, Any]) -> None:
    motor = resp.get("motor") or (resp.get("status") or {}).get("motion") or {}
    power = resp.get("power") or (resp.get("status") or {}).get("power") or {}
    if not motor and not power:
        print(f"status: failed error={resp.get('error')}")
        return
    if motor:
        print_motor(motor)
    if power:
        print(format_power(power))


def print_motor(motor: Dict[str, Any]) -> None:
    axis = motor.get("axis") or {}
    active = motor.get("active") or motor.get("active_name")
    state = axis.get("state") or motor.get("axis_state_name")
    position = axis.get("position", motor.get("axis_pos"))
    print(f"motion: active={active} axis={state}")
    print(f"position: axis={position}/0.1deg")
    if "can_position" in axis:
        print(
            "can_observe: "
            f"position={axis.get('can_position')}/0.1deg "
            f"communicated={axis.get('can_communicated')} "
            f"target={axis.get('target_position')} "
            f"observed_reached={axis.get('observed_reached')}"
        )
    if "enabled" in axis:
        print(
            "motor_flags: "
            f"enabled={axis.get('enabled')} stall={axis.get('stall')} reached={axis.get('reached')} "
            f"final_reached={axis.get('final_reached')}"
        )


def print_ups(ups: Dict[str, Any]) -> None:
    print(
        "ups: "
        f"volt={ups.get('volt')}/100V "
        f"curr={ups.get('curr')}/100A "
        f"temp={ups.get('temp')}/100C "
        f"status={ups.get('status')} "
        f"output={ups.get('output_status')} "
        f"communicated={ups.get('is_communicated')} "
        f"hw={ups.get('hardware_version')} "
        f"sw={ups.get('software_version')} "
        f"request_power_off={ups.get('request_power_off')}"
    )


def print_environment(env: Dict[str, Any]) -> None:
    address = env.get("address")
    if isinstance(address, int) and address > 0:
        address_text = f"0x{address:02x}"
    else:
        address_text = "--"
    print(
        "environment: "
        f"temp={scaled(env.get('temperature'), 100, 2, 'C')} "
        f"humi={scaled(env.get('humidity'), 100, 2, '%RH')} "
        f"communicated={env.get('is_communicated')} "
        f"addr={address_text} "
        f"last_error={env.get('last_error')}({env.get('last_error_name')}) "
        f"hal={env.get('last_hal_status')} "
        f"samples={env.get('sample_count')}"
    )


def led_group_text(group: Dict[str, Any]) -> str:
    red = bool(group.get("red"))
    green = bool(group.get("green"))
    if red and green:
        return "both"
    if red:
        return "red"
    if green:
        return "green"
    return "off"


def print_led(led: Dict[str, Any]) -> None:
    address = led.get("address")
    address_text = f"0x{address:02x}" if isinstance(address, int) and address > 0 else "--"
    mask = led.get("mask")
    input_value = led.get("input")
    config = led.get("config")
    groups = led.get("groups") or {}
    print(
        "led: "
        f"mask=0x{int(mask or 0):02x} "
        f"input=0x{int(input_value or 0):02x} "
        f"config=0x{int(config or 0):02x} "
        f"communicated={led.get('is_communicated')} "
        f"addr={address_text} "
        f"last_error={led.get('last_error')}({led.get('last_error_name')}) "
        f"hal={led.get('last_hal_status')} "
        f"writes={led.get('write_count')}"
    )
    print(
        "led_groups: "
        f"jc={led_group_text(groups.get('jc') or {})} "
        f"cd={led_group_text(groups.get('cd') or {})} "
        f"wz={led_group_text(groups.get('wz') or {})} "
        f"dp={led_group_text(groups.get('dp') or {})}"
    )


def print_switches(switches: Dict[str, Any]) -> None:
    print(
        "switches: "
        f"top={switches.get('top')} "
        f"bottom={switches.get('bottom')} "
        f"cover_button={switches.get('cover_button')} "
        f"aircraft_position_switch={switches.get('aircraft_position_switch')} "
        f"module_reached_switch={switches.get('module_reached_switch')} "
        f"aircraft_present_switch={switches.get('aircraft_present_switch')} "
        f"platform_switch={switches.get('platform_switch')} "
        f"charge_base_switch={switches.get('charge_base_switch')} "
        f"manual_action={switches.get('manual_action_name')} "
        f"psw1={switches.get('psw1')} "
        f"psw2={switches.get('psw2')} "
        f"psw3={switches.get('psw3')} "
        f"psw4={switches.get('psw4')} "
        f"active_mask=0x{int(switches.get('active_mask') or 0):02x} "
        f"raw_level_mask=0x{int(switches.get('raw_level_mask') or 0):02x} "
        f"active_low={switches.get('active_low')}"
    )


def print_ac(ac: Dict[str, Any]) -> None:
    address = ac.get("address")
    address_text = f"0x{address:02x}" if isinstance(address, int) and address > 0 else "--"
    alarms = ac.get("alarm_names") or []
    alarm_text = ",".join(alarms) if alarms else "none"
    print(
        "ac: "
        f"communicated={ac.get('is_communicated')} "
        f"busy={ac.get('busy')} "
        f"addr={address_text} "
        f"device={ac.get('device_status')}({ac.get('device_status_name')}) "
        f"indoor_fan={ac.get('indoor_fan_status')} "
        f"outdoor_fan={ac.get('outdoor_fan_status')} "
        f"compressor={ac.get('compressor_status')} "
        f"heater={ac.get('heater_status')}"
    )
    print(
        "ac_temp: "
        f"return={scaled(ac.get('return_air_temp'), 10, 1, 'C')} "
        f"external={scaled(ac.get('external_temp'), 10, 1, 'C')} "
        f"condenser={scaled(ac.get('condenser_temp'), 10, 1, 'C')} "
        f"evaporator={scaled(ac.get('evaporator_temp'), 10, 1, 'C')}"
    )
    print(
        "ac_electrical: "
        f"dc={scaled(ac.get('dc_voltage'), 10, 1, 'V')} "
        f"current={scaled(ac.get('dc_current'), 10, 1, 'A')} "
        f"indoor_rpm={ac.get('indoor_fan_rpm')} "
        f"outdoor_rpm={ac.get('outdoor_fan_rpm')} "
        f"capacity={ac.get('cooling_capacity_w')}W"
    )
    print(
        "ac_settings: "
        f"cool_start={scaled(ac.get('cool_start_temp'), 10, 1, 'C')} "
        f"cool_diff={scaled(ac.get('cool_diff'), 10, 1, 'C')} "
        f"heat_start={scaled(ac.get('heat_start_temp'), 10, 1, 'C')} "
        f"heat_diff={scaled(ac.get('heat_diff'), 10, 1, 'C')} "
        f"dehumid={ac.get('dehumid_setpoint')}% "
        f"mode={ac.get('run_mode')}({ac.get('run_mode_name')}) "
        f"monitor_humidity={ac.get('monitor_humidity')}%"
    )
    print(
        "ac_error: "
        f"last_error={ac.get('last_error')}({ac.get('last_error_name')}) "
        f"exception={ac.get('last_exception')} "
        f"function=0x{int(ac.get('last_function') or 0):02x} "
        f"last_control={ac.get('last_control_action_name')} "
        f"value={ac.get('last_control_value')} "
        f"result={ac.get('last_control_result')}({ac.get('last_control_result_name')}) "
        f"alarms=0x{int(ac.get('alarms') or 0):04x}({alarm_text}) "
        f"tx={ac.get('tx_count')} rx={ac.get('rx_count')} "
        f"crc={ac.get('crc_error_count')} timeout={ac.get('timeout_count')}"
    )
    print(
        "ac_version: "
        f"protocol={ac.get('protocol_version')} "
        f"software={ac.get('software_version')} "
        f"hardware={ac.get('hardware_version')}"
    )


def print_human(command: str, resp: Dict[str, Any]) -> None:
    if command == "version":
        print(f"mcu version: {resp.get('version')}")
        return
    if command == "status":
        print_status(resp)
        return
    if command == "motor_status":
        motor = resp.get("motor") or {}
        if motor:
            print_motor(motor)
        else:
            print(f"motor_status: failed error={resp.get('error')}")
        return
    if command == "stop_status":
        if resp.get("ok"):
            print(f"estop: hardware={resp.get('hardware_stop')}")
        else:
            print(f"estop: failed error={resp.get('error')}")
        return
    if command.startswith("power_"):
        print(f"{command}: {'ok' if ack_ok(resp) else 'failed'}")
        if command == "power_raw_transfer":
            print(f"result={resp.get('result')} name={resp.get('result_name')} error={resp.get('error')}")
            if resp.get("rx_hex") is not None:
                print(f"rx: len={resp.get('rx_len')} hex={resp.get('rx_hex')}")
        elif command in {"power_set", "power_on", "power_off"}:
            print_power_command_target(command, resp)
        elif command == "power_status" and resp.get("power"):
            print_power(resp["power"])
        elif resp.get("power"):
            print_power(resp["power"])
        elif resp.get("status"):
            print_status(resp)
        elif resp.get("error"):
            print(f"error: {resp['error']}")
        return
    if command == "ups_status":
        ups = resp.get("ups") or {}
        if ups:
            print_ups(ups)
        else:
            print(f"ups_status: failed error={resp.get('error')}")
        return
    if command == "env_status":
        env = resp.get("environment") or {}
        if env:
            print_environment(env)
        else:
            print(f"env_status: failed error={resp.get('error')}")
        return
    if command in {"led_status", "led_set"}:
        print(f"{command}: {'ok' if ack_ok(resp) else 'failed'}")
        led = resp.get("led") or {}
        if led:
            print_led(led)
        if resp.get("error"):
            print(f"error: {resp['error']}")
        return
    if command == "switch_status":
        print(f"{command}: {'ok' if ack_ok(resp) else 'failed'}")
        switches = resp.get("switches") or {}
        if switches:
            print_switches(switches)
        if resp.get("error"):
            print(f"error: {resp['error']}")
        return
    if command in {"ac_status", "ac_control"}:
        print(f"{command}: {'ok' if ack_ok(resp) else 'failed'}")
        if command == "ac_control":
            requested = resp.get("requested") or {}
            print(
                "target: "
                f"action={requested.get('action_name')} "
                f"value={requested.get('value')} "
                f"encoded={requested.get('encoded_value')} "
                f"result={resp.get('result')}({resp.get('result_name')})"
            )
        ac = resp.get("ac") or {}
        if ac:
            print_ac(ac)
        if resp.get("error"):
            print(f"error: {resp['error']}")
        return
    if command in {"door_open", "door_close"}:
        print(f"{command}: {'accepted' if ack_ok(resp) else 'failed'} error={resp.get('error')}")
        if resp.get("motor"):
            print_motor(resp["motor"])
        if resp.get("wait_error"):
            print(f"wait_error: {resp['wait_error']}")
        return
    if command in {"motor_trapezoid", "motor_home"}:
        print(f"{command}: {'accepted' if ack_ok(resp) else 'failed'} error={resp.get('error')}")
        if resp.get("motion_id") is not None:
            print(f"motion_id={resp.get('motion_id')}")
        if resp.get("motor"):
            print_motor(resp["motor"])
        if resp.get("motion_event"):
            event = resp["motion_event"]
            print(
                "motion_event: "
                f"type={event.get('type')} reason={event.get('reason')} "
                f"elapsed={event.get('elapsed_s')}s"
            )
        if resp.get("wait_error"):
            print(f"wait_error: {resp['wait_error']}")
        return
    if command == "motor_home_stop":
        requested = resp.get("requested") or {}
        print(f"{command}: {'ok' if ack_ok(resp) else 'failed'} target={requested.get('target')} error={resp.get('error')}")
        return
    if command == "motor_enable":
        requested = resp.get("requested") or {}
        print(f"{command}: {'ok' if ack_ok(resp) else 'failed'} target={requested.get('target')} enabled={requested.get('enabled')} error={resp.get('error')}")
        return
    if command == "aircraft_transfer":
        print(f"{command}: {'ok' if ack_ok(resp) else 'failed'} result={resp.get('result')} name={resp.get('result_name')} error={resp.get('error')}")
        if resp.get("rx_hex") is not None:
            print(f"rx: len={resp.get('rx_len')} hex={resp.get('rx_hex')}")
        return
    if command == "aircraft_read":
        print(
            f"{command}: {'ok' if ack_ok(resp) else 'failed'} "
            f"result={resp.get('result')} name={resp.get('result_name')} error={resp.get('error')} "
            f"dropped={resp.get('dropped')} remaining={resp.get('remaining')}"
        )
        if resp.get("rx_hex") is not None:
            print(f"rx: len={resp.get('rx_len')} hex={resp.get('rx_hex')}")
        return
    print(f"{command}: {'ok' if ack_ok(resp) else 'failed'}")
    if resp.get("error"):
        print(f"error: {resp['error']}")


def print_power(power: Dict[str, Any]) -> None:
    print(format_power(power))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Interceptor dock control CLI. All commands talk to the local daemon; this CLI never opens /dev/mcu directly.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  ./interceptorctl version
  ./interceptorctl status
  ./interceptorctl estop
  ./interceptorctl stop
  ./interceptorctl release-stop

  ./interceptorctl motor status
  ./interceptorctl motor door enable
  ./interceptorctl motor door disable
  ./interceptorctl motor door home --timeout 60
  ./interceptorctl motor door home-stop
  ./interceptorctl motor door trap --pos 181900 --speed 3000 --accel 100
  ./interceptorctl motor door trap --pos 17970 --speed 3000 --accel 100 --wait --timeout 20

  ./interceptorctl power status
  ./interceptorctl power set 24.00 1.00
  ./interceptorctl power off
  ./interceptorctl power raw --hex "01 03 00 1c 00 01"
  ./interceptorctl ups status
  ./interceptorctl env status
  ./interceptorctl led status
  ./interceptorctl led wz red
  ./interceptorctl led all off
  ./interceptorctl led mask 0x10
  ./interceptorctl switch status
  ./interceptorctl ac status
  ./interceptorctl ac settings
  ./interceptorctl ac power on
  ./interceptorctl ac cool off
  ./interceptorctl ac mode normal
  ./interceptorctl ac cool-temp 30.0
  ./interceptorctl ac cool-diff 3.0
  ./interceptorctl ac heat-temp 5.0
  ./interceptorctl ac heat-diff 8.0
  ./interceptorctl ac dehumid 60
  ./interceptorctl ac humidity 55

  ./interceptorctl aircraft xfer --text ping
  ./interceptorctl aircraft read --timeout-ms 500 --max-len 80
  ./interceptorctl aircraft xfer --hex "01 03 00 00" --timeout-ms 1000 --idle-ms 30

units:
  status motor position: 0.1 degree
  motor trap --pos: absolute target position in 0.1 degree
    example: 17970 means 1797.0 degrees
  motor trap --speed: max speed in 0.1 RPM
    example: 3000 means 300.0 RPM; 300 means 30.0 RPM
  motor trap --accel: acceleration and deceleration in RPM/s
  power set voltage/current: V and A
    example: 24.00 1.00 means 24.00 V and 1.00 A
  ac temperature, temperature hysteresis, DC voltage, and DC current: raw value / 10
  aircraft xfer --timeout-ms: total wait time for UART4 485 response, in ms
  aircraft xfer --idle-ms: response is complete after this many idle ms
""",
    )
    parser.add_argument("--socket", default=DEFAULT_SOCKET, help=f"daemon Unix socket path, default: {DEFAULT_SOCKET}")
    parser.add_argument("--json", action="store_true", help="print raw JSON response")
    sub = parser.add_subparsers(dest="area", required=True)

    sub.add_parser("version", help="read STM32 firmware version")
    sub.add_parser("status", help="read dock, motor, and power status")
    sub.add_parser("estop", help="read hardware and software emergency-stop status")
    sub.add_parser("stop", help="set MCU software motor stop")
    sub.add_parser("release-stop", help="clear MCU software motor stop and reset motor state machines")

    door = sub.add_parser("door", help="door business actions using configured firmware positions")
    door_sub = door.add_subparsers(dest="action", required=True)
    for action in ("open", "close"):
        p = door_sub.add_parser(action, help=f"run door {action} action")
        p.add_argument("--wait", action="store_true", help="wait for motion completion")
        p.add_argument("--no-wait", action="store_false", dest="wait", help=argparse.SUPPRESS)
        p.set_defaults(wait=False)
        p.add_argument("--timeout", type=float, default=20.0, help="seconds to wait for motion completion, default: 20")

    motor = sub.add_parser("motor", help="low-level motor debug commands")
    motor_target = motor.add_subparsers(dest="target", required=True)
    motor_target.add_parser("status", help="read linked single-axis motor status")
    for target in ("door", "motor", "motor1"):
        target_parser = motor_target.add_parser(target, help=f"select {target} motor")
        target_sub = target_parser.add_subparsers(dest="action", required=True)
        target_sub.add_parser("enable", help="low-level: enable the selected motor")
        target_sub.add_parser("disable", help="low-level: disable the selected motor")
        home = target_sub.add_parser("home", help="low-level: start motor homing/calibration")
        home.add_argument("--wait", action="store_true", help="wait for homing completion")
        home.add_argument("--no-wait", action="store_false", dest="wait", help=argparse.SUPPRESS)
        home.set_defaults(wait=False)
        home.add_argument("--timeout", type=float, default=60.0, help="seconds to wait/track homing, default: 60")
        target_sub.add_parser("home-stop", help="low-level: stop current homing/calibration")
        trap = target_sub.add_parser(
            "trap",
            help="absolute trapezoid move",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""units:
  --pos    absolute target position in 0.1 degree
           example: 17970 means 1797.0 degrees
  --speed  max speed in 0.1 RPM
           example: 3000 means 300.0 RPM; 300 means 30.0 RPM
  --accel  acceleration and deceleration in RPM/s

notes:
  --pos is absolute, not relative. If current position is 179401 and target is
  17970, the motor will move about 161431 units = 16143.1 degrees = 44.8 turns.
""",
        )
        trap.add_argument("--pos", type=int, required=True, help="absolute target position in 0.1 degree")
        trap.add_argument("--speed", type=int, required=True, help="max speed in 0.1 RPM")
        trap.add_argument("--accel", type=int, required=True, help="accel/decel in RPM/s; same value is used for both")
        trap.add_argument("--wait", action="store_true", help="wait for motion completion")
        trap.add_argument("--no-wait", action="store_false", dest="wait", help=argparse.SUPPRESS)
        trap.set_defaults(wait=False)
        trap.add_argument("--timeout", type=float, default=20.0, help="seconds to wait for motion completion, default: 20")

    power = sub.add_parser("power", help="power supply commands")
    power_sub = power.add_subparsers(dest="action", required=True)
    power_sub.add_parser("status", help="query output voltage/current/alarm")
    power_sub.add_parser("temp", help="compatibility alias for status")
    set_cmd = power_sub.add_parser("set", help="set output voltage/current")
    set_cmd.add_argument("voltage", help="voltage in V, for example 24.00")
    set_cmd.add_argument("current", help="current in A, for example 1.00")
    power_sub.add_parser("on", help="enable power output")
    power_sub.add_parser("off", help="disable power output")
    raw = power_sub.add_parser("raw", help="debug: send raw bytes to MCU USART3 power RS485")
    raw.add_argument("--hex", dest="hex_payload", required=True, help='raw bytes without CRC auto-fill, for example "01 03 00 1c 00 01 e4 0d"')
    raw.add_argument("--timeout-ms", type=int, default=1000, help="total response timeout in ms, default: 1000")
    raw.add_argument("--idle-ms", type=int, default=20, help="end response after idle ms, default: 20")

    ups = sub.add_parser("ups", help="UPS commands")
    ups_sub = ups.add_subparsers(dest="action", required=True)
    ups_sub.add_parser("status", help="read UPS voltage/current/temp/status/version")

    env = sub.add_parser("env", help="GXHT30 environment sensor commands")
    env_sub = env.add_subparsers(dest="action", required=True)
    env_sub.add_parser("status", help="read GXHT30 temperature/humidity status")

    led = sub.add_parser("led", help="TCA9554 LED expander commands")
    led_sub = led.add_subparsers(dest="target", required=True)
    led_sub.add_parser("status", help="read LED expander registers and decoded groups")
    mask_cmd = led_sub.add_parser("mask", help="write raw 8-bit LED output mask")
    mask_cmd.add_argument("mask", type=u8_value, help="0..255, decimal or 0x-prefixed hex")
    all_cmd = led_sub.add_parser("all", help="set all LED groups")
    all_sub = all_cmd.add_subparsers(dest="color", required=True)
    for color in ("off", "red", "green", "both", "yellow"):
        all_sub.add_parser(color, help=f"set all groups to {color}")
    for group in ("jc", "cd", "wz", "dp"):
        group_parser = led_sub.add_parser(group, help=f"set {group} LED group")
        group_sub = group_parser.add_subparsers(dest="color", required=True)
        for color in ("off", "red", "green", "both", "yellow"):
            group_sub.add_parser(color, help=f"set {group} to {color}")

    for area, help_text in (
        ("switch", "read PSW1/PSW2/PSW3/PSW4 switch inputs"),
    ):
        switch = sub.add_parser(area, help=help_text)
        switch_sub = switch.add_subparsers(dest="action", required=True)
        switch_sub.add_parser("status", help="read active-low switch inputs")

    ac = sub.add_parser("ac", help="HCNC4A air-conditioner UART5 RS485 commands")
    ac_sub = ac.add_subparsers(dest="action", required=True)
    ac_sub.add_parser("status", help="read air-conditioner status, temperatures, DC input, and alarms")
    ac_sub.add_parser("settings", help="read air-conditioner cached settings and status")
    for action, help_text in (
        ("power", "remote air-conditioner on/off"),
        ("cool", "force cooling on/off"),
        ("heat", "force heating on/off"),
    ):
        p = ac_sub.add_parser(action, help=help_text)
        p.add_argument("state", choices=("on", "off"), help="target state")
        p.add_argument("--no-wait", action="store_true", help="return after MCU accepts the command")
        p.add_argument("--timeout", type=float, default=3.0, help="seconds to wait for AC reply, default: 3")
    mode = ac_sub.add_parser("mode", help="set air-conditioner run mode")
    mode.add_argument("mode", choices=("normal", "silent"), help="target mode")
    mode.add_argument("--no-wait", action="store_true", help="return after MCU accepts the command")
    mode.add_argument("--timeout", type=float, default=3.0, help="seconds to wait for AC reply, default: 3")
    humidity = ac_sub.add_parser("humidity", help="write monitor humidity, 0..100 percent")
    humidity.add_argument("value", type=percent_value, help="humidity percent, 0..100")
    humidity.add_argument("--no-wait", action="store_true", help="return after MCU accepts the command")
    humidity.add_argument("--timeout", type=float, default=3.0, help="seconds to wait for AC reply, default: 3")
    cool_temp = ac_sub.add_parser("cool-temp", help="set compressor cooling start temperature, 20.0..50.0 C")
    cool_temp.add_argument("value", type=ac_cool_start_temp, help="temperature in C, for example 30.0")
    cool_temp.add_argument("--no-wait", action="store_true", help="return after MCU accepts the command")
    cool_temp.add_argument("--timeout", type=float, default=3.0, help="seconds to wait for AC reply, default: 3")
    cool_diff = ac_sub.add_parser("cool-diff", help="set compressor cooling hysteresis, 1.0..10.0 C")
    cool_diff.add_argument("value", type=ac_cool_diff, help="hysteresis in C, for example 3.0")
    cool_diff.add_argument("--no-wait", action="store_true", help="return after MCU accepts the command")
    cool_diff.add_argument("--timeout", type=float, default=3.0, help="seconds to wait for AC reply, default: 3")
    heat_temp = ac_sub.add_parser("heat-temp", help="set heating start temperature, -40.0..25.0 C")
    heat_temp.add_argument("value", type=ac_heat_start_temp, help="temperature in C, for example 5.0")
    heat_temp.add_argument("--no-wait", action="store_true", help="return after MCU accepts the command")
    heat_temp.add_argument("--timeout", type=float, default=3.0, help="seconds to wait for AC reply, default: 3")
    heat_diff = ac_sub.add_parser("heat-diff", help="set heating hysteresis, 5.0..15.0 C")
    heat_diff.add_argument("value", type=ac_heat_diff, help="hysteresis in C, for example 8.0")
    heat_diff.add_argument("--no-wait", action="store_true", help="return after MCU accepts the command")
    heat_diff.add_argument("--timeout", type=float, default=3.0, help="seconds to wait for AC reply, default: 3")
    dehumid = ac_sub.add_parser("dehumid", help="set dehumidification setpoint, 10..90 percent")
    dehumid.add_argument("value", type=dehumid_value, help="humidity percent, 10..90")
    dehumid.add_argument("--no-wait", action="store_true", help="return after MCU accepts the command")
    dehumid.add_argument("--timeout", type=float, default=3.0, help="seconds to wait for AC reply, default: 3")

    aircraft = sub.add_parser("aircraft", help="aircraft UART4 RS485 passthrough")
    aircraft_sub = aircraft.add_subparsers(dest="action", required=True)
    read = aircraft_sub.add_parser(
        "read",
        help="read raw bytes passively received from aircraft 485",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""notes:
  This command does not send bytes to the aircraft bus.
  It returns raw buffered bytes; customer code is responsible for protocol framing.
""",
    )
    read.add_argument("--timeout-ms", type=int, default=1000, help="wait for at least one byte, default: 1000")
    read.add_argument("--max-len", type=int, default=220, help="maximum bytes to return, default: 220")
    xfer = aircraft_sub.add_parser(
        "xfer",
        help="send one request to aircraft 485 and wait for one response",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""payload:
  --text sends UTF-8 text.
  --hex sends raw bytes. Separators may be spaces, commas, or colons.

receive:
  --timeout-ms is the maximum total wait time.
  --idle-ms says the response is complete after that many idle milliseconds.
""",
    )
    payload_group = xfer.add_mutually_exclusive_group(required=True)
    payload_group.add_argument("--hex", dest="hex_payload", help='raw bytes, for example "01 03 00 00"')
    payload_group.add_argument("--text", help="UTF-8 text payload")
    xfer.add_argument("--append-cr", action="store_true", help="append 0x0d to payload")
    xfer.add_argument("--append-lf", action="store_true", help="append 0x0a to payload")
    xfer.add_argument("--timeout-ms", type=int, default=1000, help="total response timeout in ms, default: 1000")
    xfer.add_argument("--idle-ms", type=int, default=30, help="end response after idle ms, default: 30")

    return parser


def command_from_args(args: argparse.Namespace) -> tuple[str, Dict[str, Any]]:
    if args.area == "version":
        return "version", {}
    if args.area == "status":
        return "status", {}
    if args.area == "estop":
        return "stop_status", {}
    if args.area == "stop":
        return "motor_stop", {}
    if args.area == "release-stop":
        return "motor_release_stop", {}
    if args.area == "door":
        return f"door_{args.action}", {"wait": args.wait, "timeout": args.timeout}
    if args.area == "motor":
        if args.target == "status":
            return "motor_status", {}
        if args.action in {"enable", "disable"}:
            return "motor_enable", {
                "target": args.target,
                "enabled": args.action == "enable",
            }
        if args.action == "trap":
            return "motor_trapezoid", {
                "target": args.target,
                "position": args.pos,
                "speed": args.speed,
                "accel": args.accel,
                "wait": args.wait,
                "timeout": args.timeout,
            }
        if args.action == "home":
            return "motor_home", {
                "target": args.target,
                "wait": args.wait,
                "timeout": args.timeout,
            }
        if args.action == "home-stop":
            return "motor_home_stop", {
                "target": args.target,
            }
    if args.area == "power":
        if args.action == "status":
            return "power_status", {}
        if args.action == "temp":
            return "power_status", {}
        if args.action == "set":
            return "power_set", {
                "voltage": centi_value(args.voltage, "voltage"),
                "current": centi_value(args.current, "current"),
            }
        if args.action == "on":
            return "power_on", {}
        if args.action == "off":
            return "power_off", {}
        if args.action == "raw":
            tx = parse_hex_bytes(args.hex_payload)
            return "power_raw_transfer", {
                "tx_hex": tx.hex(),
                "timeout_ms": args.timeout_ms,
                "idle_ms": args.idle_ms,
            }
    if args.area == "ups":
        if args.action == "status":
            return "ups_status", {}
    if args.area == "env":
        if args.action == "status":
            return "env_status", {}
    if args.area == "led":
        if args.target == "status":
            return "led_status", {}
        if args.target == "mask":
            return "led_set", {"mask": args.mask}
        if args.target == "all":
            return "led_set", {"group": "all", "color": args.color}
        return "led_set", {"group": args.target, "color": args.color}
    if args.area == "switch":
        if args.action == "status":
            return "switch_status", {}
    if args.area == "ac":
        if args.action in {"status", "settings"}:
            return "ac_status", {}
        if args.action == "power":
            return "ac_control", {
                "action": "remote_power",
                "value": 1 if args.state == "on" else 0,
                "wait": not args.no_wait,
                "timeout": args.timeout,
            }
        if args.action == "cool":
            return "ac_control", {
                "action": "force_cool",
                "value": 1 if args.state == "on" else 0,
                "wait": not args.no_wait,
                "timeout": args.timeout,
            }
        if args.action == "heat":
            return "ac_control", {
                "action": "force_heat",
                "value": 1 if args.state == "on" else 0,
                "wait": not args.no_wait,
                "timeout": args.timeout,
            }
        if args.action == "mode":
            return "ac_control", {
                "action": "run_mode",
                "value": 1 if args.mode == "silent" else 0,
                "wait": not args.no_wait,
                "timeout": args.timeout,
            }
        if args.action == "humidity":
            return "ac_control", {
                "action": "humidity",
                "value": args.value,
                "wait": not args.no_wait,
                "timeout": args.timeout,
            }
        if args.action == "cool-temp":
            return "ac_control", {
                "action": "cool_start_temp",
                "value": args.value,
                "wait": not args.no_wait,
                "timeout": args.timeout,
            }
        if args.action == "cool-diff":
            return "ac_control", {
                "action": "cool_diff",
                "value": args.value,
                "wait": not args.no_wait,
                "timeout": args.timeout,
            }
        if args.action == "heat-temp":
            return "ac_control", {
                "action": "heat_start_temp",
                "value": args.value,
                "wait": not args.no_wait,
                "timeout": args.timeout,
            }
        if args.action == "heat-diff":
            return "ac_control", {
                "action": "heat_diff",
                "value": args.value,
                "wait": not args.no_wait,
                "timeout": args.timeout,
            }
        if args.action == "dehumid":
            return "ac_control", {
                "action": "dehumid_setpoint",
                "value": args.value,
                "wait": not args.no_wait,
                "timeout": args.timeout,
            }
    if args.area == "aircraft":
        if args.action == "read":
            return "aircraft_read", {
                "timeout_ms": args.timeout_ms,
                "max_len": args.max_len,
            }
        if args.action == "xfer":
            tx = parse_hex_bytes(args.hex_payload) if args.hex_payload is not None else args.text.encode("utf-8")
            if args.append_cr:
                tx += b"\r"
            if args.append_lf:
                tx += b"\n"
            return "aircraft_transfer", {
                "tx_hex": tx.hex(),
                "timeout_ms": args.timeout_ms,
                "idle_ms": args.idle_ms,
            }
    raise RuntimeError("unreachable command")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        command, payload = command_from_args(args)
        resp = request(args.socket, command, payload)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print_json(resp)
    else:
        print_human(command, resp)
    return 0 if ack_ok(resp) else 1


if __name__ == "__main__":
    sys.exit(main())
