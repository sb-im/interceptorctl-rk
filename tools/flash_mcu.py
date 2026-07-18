#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


INTERCEPTOR_SERVICE = "interceptorctl.service"
OLD_SERVICES = ("sbmcu.service", "sbdockctl3.service", "sbdockctl300.service")


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def sudo_prefix() -> list[str]:
    return [] if is_root() else ["sudo"]


def run_cmd(cmd: list[str], *, check: bool = True, dry_run: bool = False) -> int:
    print("[CMD]", " ".join(cmd))
    if dry_run:
        return 0
    result = subprocess.run(cmd)
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)} (rc={result.returncode})")
    return result.returncode


def command_ok(cmd: list[str]) -> bool:
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def systemctl(args: list[str], *, check: bool = False, dry_run: bool = False) -> int:
    return run_cmd(sudo_prefix() + ["systemctl"] + args, check=check, dry_run=dry_run)


def service_exists(service: str) -> bool:
    return command_ok(["systemctl", "list-unit-files", service])


def service_active(service: str) -> bool:
    return command_ok(["systemctl", "is-active", "--quiet", service])


def service_enabled(service: str) -> bool:
    return command_ok(["systemctl", "is-enabled", "--quiet", service])


def stop_services(dry_run: bool) -> tuple[bool, bool]:
    interceptor_was_active = service_active(INTERCEPTOR_SERVICE)
    interceptor_is_enabled = service_enabled(INTERCEPTOR_SERVICE)

    if service_exists(INTERCEPTOR_SERVICE):
        systemctl(["stop", INTERCEPTOR_SERVICE], dry_run=dry_run)

    for service in OLD_SERVICES:
        if service_exists(service):
            systemctl(["stop", service], dry_run=dry_run)

    if command_ok(["bash", "-lc", "command -v tmux"]):
        run_cmd(["tmux", "kill-session", "-t", "interceptorctl"], check=False, dry_run=dry_run)

    run_cmd(sudo_prefix() + ["rm", "-f", "/tmp/interceptorctl.sock"], check=False, dry_run=dry_run)
    return interceptor_was_active, interceptor_is_enabled


def restore_service(was_active: bool, is_enabled: bool, dry_run: bool) -> None:
    if service_exists(INTERCEPTOR_SERVICE) and (was_active or is_enabled):
        systemctl(["start", INTERCEPTOR_SERVICE], check=True, dry_run=dry_run)


def read_dt_model(path: str = "/proc/device-tree/model") -> str | None:
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    text = raw.decode(errors="ignore").replace("\x00", "").strip()
    return text or None


def detect_board_type() -> str | None:
    model = read_dt_model()
    if not model:
        return None
    print(f"==> /proc/device-tree/model: {model}")
    name = model.lower()
    if "orange pi 5 pro" in name:
        return "opi5pro"
    if "orange pi cm5" in name:
        return "cm5"
    if "orange pi 5" in name:
        return "opi5"
    return None


def set_cm5_gpio(chip: str, line: int, high: bool, dry_run: bool) -> None:
    run_cmd(sudo_prefix() + ["gpioset", chip, f"{line}={1 if high else 0}"], dry_run=dry_run)


def enter_bootloader_cm5(chip: str, boot_line: int, reset_line: int, dry_run: bool) -> None:
    print(">> Enter STM32 bootloader: BOOT0=1, RESET pulse")
    set_cm5_gpio(chip, boot_line, True, dry_run)
    set_cm5_gpio(chip, reset_line, True, dry_run)
    if not dry_run:
        time.sleep(0.2)
    set_cm5_gpio(chip, reset_line, False, dry_run)
    if not dry_run:
        time.sleep(0.5)


def exit_bootloader_cm5(chip: str, boot_line: int, reset_line: int, dry_run: bool) -> None:
    print(">> Exit STM32 bootloader: BOOT0=0, RESET pulse")
    set_cm5_gpio(chip, boot_line, False, dry_run)
    set_cm5_gpio(chip, reset_line, True, dry_run)
    if not dry_run:
        time.sleep(0.2)
    set_cm5_gpio(chip, reset_line, False, dry_run)


def enter_bootloader_wiringpi(board_type: str, boot_pin: int, reset_pin: int, dry_run: bool):
    if dry_run:
        print(f">> Enter STM32 bootloader with wiringpi {board_type}: BOOT0 pin {boot_pin}, RESET pin {reset_pin}")
        return None

    import wiringpi
    from wiringpi import GPIO

    wiringpi.wiringPiSetup()
    wiringpi.pinMode(boot_pin, GPIO.OUTPUT)
    wiringpi.pinMode(reset_pin, GPIO.OUTPUT)
    wiringpi.digitalWrite(boot_pin, GPIO.HIGH)
    wiringpi.digitalWrite(reset_pin, GPIO.HIGH)
    time.sleep(0.2)
    wiringpi.digitalWrite(reset_pin, GPIO.LOW)
    time.sleep(0.5)
    return wiringpi, GPIO


def exit_bootloader_wiringpi(ctx, boot_pin: int, reset_pin: int, dry_run: bool) -> None:
    if dry_run:
        print(f">> Exit STM32 bootloader with wiringpi: BOOT0 pin {boot_pin}, RESET pin {reset_pin}")
        return
    wiringpi, GPIO = ctx
    wiringpi.digitalWrite(boot_pin, GPIO.LOW)
    wiringpi.digitalWrite(reset_pin, GPIO.HIGH)
    time.sleep(0.2)
    wiringpi.digitalWrite(reset_pin, GPIO.LOW)


def flash_firmware(port: str, firmware: str, dry_run: bool) -> None:
    print(">> Flashing with stm32loader")
    run_cmd(["stm32loader", "-p", port, "-e", "-w", firmware], dry_run=dry_run)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Independent STM32 MCU flasher for interceptorctl. It does not depend on dockctl3/sbdocker."
    )
    parser.add_argument("firmware", help="STM32 .bin firmware path")
    parser.add_argument("-p", "--port", default="/dev/mcu", help="MCU serial device, default: /dev/mcu")
    parser.add_argument(
        "-t",
        "--type",
        dest="board_type",
        choices=["auto", "opi5", "opi5pro", "cm5"],
        default="auto",
        help="board type, default: auto",
    )
    parser.add_argument("--chip", default="gpiochip4", help="CM5 gpiochip, default: gpiochip4")
    parser.add_argument("--boot-line", type=int, default=3, help="CM5 BOOT0 line, default: 3")
    parser.add_argument("--reset-line", type=int, default=4, help="CM5 RESET line, default: 4")
    parser.add_argument("--no-service-control", action="store_true", help="do not stop/start systemd services")
    parser.add_argument("--dry-run", action="store_true", help="print actions without changing GPIO, services, or flash")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    firmware = str(Path(args.firmware).expanduser())
    if not args.dry_run and not Path(firmware).is_file():
        raise RuntimeError(f"firmware not found: {firmware}")

    board_type = args.board_type
    if board_type == "auto":
        detected = detect_board_type()
        if not detected:
            raise RuntimeError("cannot auto-detect board type; use -t opi5, opi5pro, or cm5")
        board_type = detected

    board_config = {
        "opi5": {"method": "wiringpi", "boot_pin": 3, "reset_pin": 4},
        "opi5pro": {"method": "wiringpi", "boot_pin": 24, "reset_pin": 26},
        "cm5": {"method": "libgpiod"},
    }
    cfg = board_config[board_type]

    print(f"==> Board: {board_type}")
    print(f"==> Firmware: {firmware}")
    print(f"==> Port: {args.port}")

    interceptor_was_active = False
    interceptor_is_enabled = False
    if not args.no_service_control:
        interceptor_was_active, interceptor_is_enabled = stop_services(args.dry_run)

    boot_ctx = None
    try:
        if cfg["method"] == "libgpiod":
            enter_bootloader_cm5(args.chip, args.boot_line, args.reset_line, args.dry_run)
            flash_firmware(args.port, firmware, args.dry_run)
        else:
            boot_ctx = enter_bootloader_wiringpi(
                board_type,
                int(cfg["boot_pin"]),
                int(cfg["reset_pin"]),
                args.dry_run,
            )
            flash_firmware(args.port, firmware, args.dry_run)
    finally:
        if cfg["method"] == "libgpiod":
            exit_bootloader_cm5(args.chip, args.boot_line, args.reset_line, args.dry_run)
        else:
            exit_bootloader_wiringpi(boot_ctx, int(cfg["boot_pin"]), int(cfg["reset_pin"]), args.dry_run)
        if not args.no_service_control:
            restore_service(interceptor_was_active, interceptor_is_enabled, args.dry_run)

    print("==> Done.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nAborted by user.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
