#!/usr/bin/env python3
"""interceptorctl 数字菜单客户端。

本脚本只负责收集用户输入并调用现有 cli.py --json；CLI 返回内容会原样输出。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence, Union


Option = tuple[str, str]


def find_cli_py() -> Path:
    """查找与本脚本一起使用的 interceptorctl cli.py。"""
    script_dir = Path(__file__).resolve().parent
    candidates = (
        script_dir / "cli.py",
        script_dir / "interceptorctl-rk" / "cli.py",
        Path.cwd() / "cli.py",
        Path.cwd() / "interceptorctl-rk" / "cli.py",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "未找到 interceptorctl 的 cli.py；请将本脚本放在 interceptorctl 目录或其上级目录。"
    )


def choose(
    title: str,
    options: Sequence[Option],
    *,
    allow_back: bool = True,
    back_label: str = "返回",
) -> str:
    """显示数字菜单并返回选项值。"""
    while True:
        print(f"\n=== {title} ===")
        for index, (_, label) in enumerate(options, start=1):
            print(f"{index}. {label}")
        if allow_back:
            print(f"0. {back_label}")

        raw = input("请选择数字: ").strip()
        if allow_back and raw == "0":
            return ""
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(options):
                return options[index - 1][0]
        print("输入无效，请输入菜单中的数字。")


def prompt_text(label: str, *, default: Optional[str] = None, allow_empty: bool = False) -> str:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        if allow_empty:
            return ""
        print("输入不能为空。")


def prompt_number(
    label: str,
    converter: Callable[[str], Union[int, float]],
    *,
    default: Optional[Union[int, float]] = None,
    minimum: Optional[Union[int, float]] = None,
    maximum: Optional[Union[int, float]] = None,
) -> Union[int, float]:
    while True:
        raw = input(f"{label}" + (f" [{default}]" if default is not None else "") + ": ").strip()
        if not raw and default is not None:
            value = default
        else:
            try:
                value = converter(raw)
            except ValueError:
                print("数值格式错误，请重新输入。")
                continue
        if minimum is not None and value < minimum:
            print(f"数值不能小于 {minimum}。")
            continue
        if maximum is not None and value > maximum:
            print(f"数值不能大于 {maximum}。")
            continue
        return value


def prompt_yes_no(label: str, *, default: bool = False) -> bool:
    options: Sequence[Option] = (("yes", "是"), ("no", "否"))
    default_number = "1" if default else "2"
    while True:
        print(f"\n{label}")
        print("1. 是")
        print("2. 否")
        raw = input(f"请选择数字 [{default_number}]: ").strip() or default_number
        if raw in {"1", "2"}:
            return options[int(raw) - 1][0] == "yes"
        print("输入无效，请输入 1 或 2。")


class MenuApp:
    def __init__(self, cli_py: Path, socket_path: str) -> None:
        self.cli_py = cli_py
        self.socket_path = socket_path

    def run_command(self, args: Sequence[str]) -> None:
        command = [
            sys.executable,
            str(self.cli_py),
            "--json",
            "--socket",
            self.socket_path,
            *args,
        ]
        print("\n返回 JSON:")
        try:
            result = subprocess.run(command, check=False)
        except OSError as exc:
            print(f"无法启动 interceptorctl CLI: {exc}", file=sys.stderr)
            return
        if result.returncode != 0:
            print(f"interceptorctl 执行失败，退出码: {result.returncode}", file=sys.stderr)
        input("\n按回车键继续...")

    def system_menu(self) -> None:
        actions: Sequence[Option] = (
            ("version", "读取 STM32 固件版本"),
            ("status", "读取整机状态"),
            ("estop", "读取急停状态"),
            ("stop", "设置软件电机急停"),
            ("release-stop", "解除软件急停并复位电机状态机"),
        )
        while action := choose("系统与急停", actions):
            self.run_command([action])

    def door_menu(self) -> None:
        actions: Sequence[Option] = (("open", "开门"), ("close", "关门"))
        while action := choose("舱门控制", actions):
            args = ["door", action]
            if prompt_yes_no("是否等待运动完成？"):
                timeout = prompt_number("等待超时（秒）", float, default=20.0, minimum=0.1)
                args.extend(("--wait", "--timeout", str(timeout)))
            self.run_command(args)

    def motor_menu(self) -> None:
        actions: Sequence[Option] = (
            ("status", "读取电机状态"),
            ("enable", "使能电机"),
            ("disable", "禁用电机"),
            ("home", "回零/校准"),
            ("home-stop", "停止回零/校准"),
            ("trap", "绝对位置梯形运动"),
        )
        while action := choose("低层电机调试", actions):
            if action == "status":
                self.run_command(["motor", "status"])
                continue

            target = choose(
                "选择电机名称（当前固件中均指向联动单轴）",
                (("door", "door"), ("motor", "motor"), ("motor1", "motor1")),
            )
            if not target:
                continue
            args = ["motor", target, action]
            if action == "home":
                if prompt_yes_no("是否等待回零完成？"):
                    timeout = prompt_number("等待超时（秒）", float, default=60.0, minimum=0.1)
                    args.extend(("--wait", "--timeout", str(timeout)))
            elif action == "trap":
                position = prompt_number("绝对目标位置（0.1 度）", int)
                speed = prompt_number("最大速度（0.1 RPM）", int, minimum=0)
                accel = prompt_number("加减速度（RPM/s）", int, minimum=0)
                args.extend(("--pos", str(position), "--speed", str(speed), "--accel", str(accel)))
                if prompt_yes_no("是否等待运动完成？"):
                    timeout = prompt_number("等待超时（秒）", float, default=20.0, minimum=0.1)
                    args.extend(("--wait", "--timeout", str(timeout)))
            self.run_command(args)

    def power_menu(self) -> None:
        actions: Sequence[Option] = (
            ("status", "读取电源状态"),
            ("fault", "读取详细故障诊断"),
            ("temp", "读取电源温度（status 别名）"),
            ("set", "设置输出电压和电流"),
            ("on", "开启电源输出"),
            ("off", "关闭电源输出"),
            ("raw", "发送原始 Modbus 字节"),
        )
        while action := choose("GPpower3000 电源", actions):
            args = ["power", action]
            if action == "set":
                voltage = prompt_number("电压（V）", float, minimum=0)
                current = prompt_number("电流（A）", float, minimum=0)
                args.extend((str(voltage), str(current)))
            elif action == "raw":
                hex_payload = prompt_text("十六进制字节（需自行包含 CRC）")
                timeout_ms = prompt_number("响应超时（ms）", int, default=1000, minimum=0)
                idle_ms = prompt_number("空闲结束时间（ms）", int, default=20, minimum=0)
                args.extend(("--hex", hex_payload, "--timeout-ms", str(timeout_ms), "--idle-ms", str(idle_ms)))
            self.run_command(args)

    def led_menu(self) -> None:
        actions: Sequence[Option] = (
            ("status", "读取 LED 状态"),
            ("group", "按灯组设置颜色"),
            ("mask", "写入原始 8 位掩码"),
        )
        while action := choose("LED 扩展器", actions):
            if action == "status":
                self.run_command(["led", "status"])
            elif action == "mask":
                mask = prompt_text("掩码（0..255 或 0x00..0xFF）")
                self.run_command(["led", "mask", mask])
            else:
                group = choose(
                    "选择灯组",
                    (("jc", "JC"), ("cd", "CD"), ("wz", "WZ"), ("dp", "DP"), ("all", "全部")),
                )
                if not group:
                    continue
                color = choose(
                    "选择颜色",
                    (("off", "关闭"), ("red", "红"), ("green", "绿"), ("both", "红绿同时"), ("yellow", "黄（等同红绿同时）")),
                )
                if color:
                    self.run_command(["led", group, color])

    def ac_menu(self) -> None:
        actions: Sequence[Option] = (
            ("status", "读取空调状态"),
            ("settings", "读取空调设置"),
            ("power", "远程开关机"),
            ("cool", "强制制冷开关"),
            ("heat", "强制制热开关"),
            ("mode", "设置运行模式"),
            ("cool-temp", "设置制冷启动温度（20.0..50.0 C）"),
            ("cool-diff", "设置制冷回差（1.0..10.0 C）"),
            ("heat-temp", "设置制热启动温度（-40.0..25.0 C）"),
            ("heat-diff", "设置制热回差（5.0..15.0 C）"),
            ("dehumid", "设置除湿目标（10..90%）"),
            ("humidity", "下发监控湿度（0..100%）"),
        )
        ranges = {
            "cool-temp": (float, 20.0, 50.0),
            "cool-diff": (float, 1.0, 10.0),
            "heat-temp": (float, -40.0, 25.0),
            "heat-diff": (float, 5.0, 15.0),
            "dehumid": (int, 10, 90),
            "humidity": (int, 0, 100),
        }
        while action := choose("HCNC4A 空调", actions):
            args = ["ac", action]
            if action in {"power", "cool", "heat"}:
                state = choose("选择状态", (("on", "开启"), ("off", "关闭")))
                if not state:
                    continue
                args.append(state)
            elif action == "mode":
                mode = choose("选择模式", (("normal", "普通"), ("silent", "静音")))
                if not mode:
                    continue
                args.append(mode)
            elif action in ranges:
                converter, minimum, maximum = ranges[action]
                value = prompt_number("设置值", converter, minimum=minimum, maximum=maximum)
                args.append(str(value))

            if action not in {"status", "settings"}:
                if prompt_yes_no("是否只等待 MCU 接收、不等待空调回复？"):
                    args.append("--no-wait")
                else:
                    timeout = prompt_number("等待超时（秒）", float, default=3.0, minimum=0.1)
                    args.extend(("--timeout", str(timeout)))
            self.run_command(args)

    def aircraft_menu(self) -> None:
        actions: Sequence[Option] = (
            ("read", "被动读取 UART4 485 数据"),
            ("xfer-text", "发送 UTF-8 文本并接收"),
            ("xfer-hex", "发送十六进制字节并接收"),
        )
        while action := choose("航空器 UART4 RS485", actions):
            if action == "read":
                timeout_ms = prompt_number("等待数据超时（ms）", int, default=1000, minimum=0)
                max_len = prompt_number("最大读取长度", int, default=220, minimum=1)
                self.run_command(["aircraft", "read", "--timeout-ms", str(timeout_ms), "--max-len", str(max_len)])
                continue

            args = ["aircraft", "xfer"]
            if action == "xfer-text":
                args.extend(("--text", prompt_text("发送文本", allow_empty=True)))
                if prompt_yes_no("末尾追加 CR（0x0D）？"):
                    args.append("--append-cr")
                if prompt_yes_no("末尾追加 LF（0x0A）？"):
                    args.append("--append-lf")
            else:
                args.extend(("--hex", prompt_text("十六进制字节")))
            timeout_ms = prompt_number("响应总超时（ms）", int, default=1000, minimum=0)
            idle_ms = prompt_number("响应空闲结束时间（ms）", int, default=30, minimum=0)
            args.extend(("--timeout-ms", str(timeout_ms), "--idle-ms", str(idle_ms)))
            self.run_command(args)

    def main_loop(self) -> None:
        menus: dict[str, Callable[[], None]] = {
            "system": self.system_menu,
            "door": self.door_menu,
            "motor": self.motor_menu,
            "power": self.power_menu,
            "led": self.led_menu,
            "ac": self.ac_menu,
            "aircraft": self.aircraft_menu,
        }
        options: Sequence[Option] = (
            ("system", "系统、整机状态与急停"),
            ("door", "舱门控制"),
            ("motor", "低层电机调试"),
            ("power", "GPpower3000 电源"),
            ("ups", "读取 UPS 状态"),
            ("env", "读取环境温湿度"),
            ("led", "LED 扩展器"),
            ("switch", "读取开关量状态"),
            ("ac", "HCNC4A 空调"),
            ("aircraft", "航空器 UART4 RS485"),
        )
        print("interceptorctl 菜单客户端")
        print(f"CLI: {self.cli_py}")
        print(f"Socket: {self.socket_path}")
        while True:
            action = choose("主菜单", options, back_label="退出")
            if not action:
                print("已退出。")
                return
            if action in menus:
                menus[action]()
            else:
                self.run_command([action, "status"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="interceptorctl 数字菜单客户端")
    parser.add_argument(
        "--socket",
        default="/tmp/interceptorctl.sock",
        help="daemon Unix socket 路径（默认: /tmp/interceptorctl.sock）",
    )
    parser.add_argument("--cli", type=Path, help="cli.py 路径；默认自动查找")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        cli_py = args.cli.resolve() if args.cli else find_cli_py()
        if not cli_py.is_file():
            raise FileNotFoundError(f"cli.py 不存在: {cli_py}")
        MenuApp(cli_py, args.socket).main_loop()
    except (EOFError, KeyboardInterrupt):
        print("\n已退出。")
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
