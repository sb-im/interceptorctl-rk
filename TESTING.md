# 截击机控制程序测试说明

当前 RK3588 地址：`192.168.99.21`

服务目录：`/home/orangepi/interceptorctl`

日志：`/home/orangepi/interceptorctl/interceptorctl.log`

daemon socket：`/tmp/interceptorctl.sock`

当前固件版本：`0x001F`

## 0. 固件烧录方法和开机自启动（jjj 已验证）

固件统一放在：

```text
/home/orangepi/dockctl3/firmware/
```

当前 0x001F 急停版本：

```text
/home/orangepi/dockctl3/firmware/sbdock_0x001F_estop.bin
```

烧录命令：

```bash
sudo /usr/bin/python3 /home/orangepi/interceptorctl/tools/flash_mcu.py /home/orangepi/dockctl3/firmware/sbdock_0x001F_estop.bin
```

只预演、不烧录：

```bash
sudo /usr/bin/python3 /home/orangepi/interceptorctl/tools/flash_mcu.py --dry-run /home/orangepi/dockctl3/firmware/sbdock_0x001F_estop.bin
```

Windows PowerShell 远程执行烧录示例：

```powershell
ssh jjj 'sudo /usr/bin/python3 /home/orangepi/interceptorctl/tools/flash_mcu.py /home/orangepi/dockctl3/firmware/sbdock_0x001F_estop.bin'
```

注意：

- 20260622 起当前正式烧录工具是 `/home/orangepi/interceptorctl/tools/flash_mcu.py`。
- `flash_mcu.py` 会停止 `interceptorctl.service`，烧录后再启动 `interceptorctl.service`。
- 旧 `sbmcu.service` / `sbdocker` 不再作为截击机正式链路使用，也不会在烧录后被拉起。
- 烧录成功输出包含 `==> Done.`。
- 旧 `/home/orangepi/dockctl3/tools/flash.py` 只作为历史参考，后续不要再依赖它。

开机自启动安装：

```bash
cd /home/orangepi/interceptorctl
sudo ./tools/install_service.sh
```

检查：

```bash
systemctl is-enabled interceptorctl.service
systemctl is-active interceptorctl.service
systemctl status interceptorctl.service --no-pager
```

20260622 已在 `jjj` 部署验证：`interceptorctl.service` 为 `enabled/active`，`./interceptorctl version` 返回 `0x001f (31)`。

## 1. 控制链路

```text
./interceptorctl
  -> /tmp/interceptorctl.sock
  -> interceptorctl daemon
  -> /dev/mcu
  -> STM32 USART1 Package 协议
  -> STM32 控制 CAN 电机 / USART3 485 电源
```

原则：

- 只有 `interceptorctl daemon` 打开 `/dev/mcu`。
- 用户、调试脚本、后续客户程序都不直接打开 `/dev/mcu`。
- RK3588 的 `can0` 可以被动抓包或临时两两测试，但正式电机运动和状态刷新由 MCU 统一发电机 CAN。
- 不能让 RK3588 正式直接向电机发 `0x36/0x3A` 查询帧，因为 MCU 也会收到这些 CAN 帧，可能污染 MCU 电机状态机。

## 2. 启动服务

```bash
ssh orangepi@192.168.99.21
cd /home/orangepi/interceptorctl
sudo ./tools/install_service.sh
```

检查：

```bash
systemctl status interceptorctl.service --no-pager
ls -l /tmp/interceptorctl.sock
tail -f /home/orangepi/interceptorctl/interceptorctl.log
```

## 3. 基础命令

```bash
./interceptorctl version
```

预期：

```text
mcu version: 0x001f (31)
```

```bash
./interceptorctl status
```

预期：

```text
motion: active=idle axis=unknown
position: axis=.../0.1deg
power: ... communicated=True last_error=0
```

说明：

- `position` 单位是 `0.1°`，与电机协议一致。
- `status` 在所有电机空闲时会触发 MCU 同步读取电机位置和状态，所以不是旧缓存。
- 任一电机运动中都不会插入额外 CAN 查询，只返回 MCU 当前缓存，避免打断运动流程。

原始 JSON：

```bash
./interceptorctl --json status
```

读取急停状态：

```bash
./interceptorctl estop
./interceptorctl --json estop
```

daemon JSON 接口：

```json
{"cmd":"stop_status","args":{}}
```

未按下实体急停、未执行软件 stop 时预期：

```text
estop: active=False hardware=False software=False
```

说明：
- `hardware=True` 表示实体急停按钮触发，当前与老 sbdocker 逻辑一致，为 `BT_STOP` 高电平有效。
- `software=True` 表示执行过 `./interceptorctl stop`，需要 `./interceptorctl release-stop` 清除。
- 任一项为 True 时，电机运动会进入急停流程。
- 如果 `hardware=True`，`release-stop` 不能清除硬件急停，必须先机械复位实体急停按钮。

20260622 实物急停测试记录：

- 只读测试：18:17:00 开始轮询 `./interceptorctl estop`，18:17:05.145 按下急停后读到 `active=True hardware=True software=False`。
- 释放测试：18:17:39.625 读到 `active=False hardware=False software=False`，说明机械复位后输入能恢复。
- 运动中急停测试：门电机从约 `18682/0.1deg` 向 `19682/0.1deg` 低速运动，按下急停后停在约 `18884/0.1deg`，未到目标位；急停状态保持 `hardware=True software=False`。
- 后续两轮释放检查持续读到 `hardware=True`，经现场确认是测试人员尚未松开急停，不是输入异常。
- 18:23:27 再次确认机械复位后 JSON 返回 `data_hex=000000`，`hardware_stop=false`、`soft_stop=false`、`active=false`。

## 4. 电机梯形运动调试

门电机：

```bash
./interceptorctl motor door trap --pos 181900 --speed 300 --accel 100 --timeout 8
```

参数单位：

- `--pos`：绝对终点位置，单位 `0.1°`。
- `--speed`：最大速度，单位 `0.1RPM`。例：`300` 表示 `30.0RPM`。
- `--accel`：加速度，单位 `RPM/s`；MCU 下发时加速和减速都用这个值。
- `--timeout`：CLI 等动作结束的最长时间。
- `--no-wait`：只确认 MCU 接收命令，不等待动作完成。

0x001C 当前实测：

- 门：`--pos 181900 --speed 300 --accel 100`，最终约 `181907/0.1deg`，耗时约 `0.68s`。
- 平台：`--pos 179700 --speed 300 --accel 100`，最终约 `179684/0.1deg`，耗时约 `0.68s`。
- 平台回退：`--pos 179400 --speed 300 --accel 100`，最终约 `179401/0.1deg`，耗时约 `0.69s`。

现象：

- 电机会按梯形曲线运行到指定绝对角度附近。
- 动作结束后 MCU 会自动去使能，空载时位置读数可能轻微漂移。

## 5. 停止和释放

```bash
./interceptorctl stop
./interceptorctl release-stop
```

预期：

```text
motor_stop: ok
motor_release_stop: ok
```

说明：

- `stop` 设置 MCU 软件急停。
- `release-stop` 清除软件急停并把电机状态机复位到空闲。
- 0x001F 起，`Hardware_Interceptor` 恢复读取已接线的 `BT_STOP` 实体急停输入，并提供 `./interceptorctl estop` 读取接口。

## 6. 电源测试

电源地址：`0x01`

电源链路：STM32 USART3 485，9600 8N1。

```bash
./interceptorctl power status
./interceptorctl power temp
./interceptorctl power set 24.00 1.00
./interceptorctl power off
```

预期：

- `power_status: ok`
- `power_temp: ok`
- `power_set: ok`
- `power_off: ok`
- `communicated=True`
- `last_error=0`

当前实测成功路径约 `0.13s` 到 `0.15s`。

打开输出：

```bash
./interceptorctl power on
```

建议只在确认电压电流设置正确后执行，测试结束保持 `power off`。

## 7. 业务动作

```bash
./interceptorctl door open
./interceptorctl door close
```

这些命令已经接到 MCU 业务动作。当前机械结构为单电机联动，只保留开门和关门。机械结构标定前，优先用第 4 节的 `motor ... trap` 调试真实电机参数。

## 8. 飞机 UART4 485 透传

链路：

```text
./interceptorctl aircraft xfer
  -> interceptorctl daemon
  -> /dev/mcu
  -> STM32 USART1 Package cmd_set=12 cmd_id=10
  -> STM32 UART4 485
  -> 飞机 485
```

MCU 端当前 485 配置：

- 物理串口：STM32 `UART4`。
- 波特率：`115200`。
- 数据格式：`8N1`，8 数据位、无校验、1 停止位。
- 硬件流控：无。
- RS485 方向：发送前使能脚置发送态，`TC` 发送完成中断里切回接收态。
- 接收方式：`UART4 RXNE` 中断逐字节缓存。
- 收包边界：协议未知，当前使用 `timeout_ms + idle_ms`。收到最后一个字节后空闲 `idle_ms` 即认为一帧结束；如果总等待超过 `timeout_ms` 且没有形成成功接收，则返回超时。
- 最大透传发送长度：`220` 字节。
- 最大透传返回长度：`220` 字节，超过后返回 `result=4`，数据会截断。

推荐测试方法是使用 PC 串口助手：

1. PC 连接 USB 转 485。
2. USB 转 485 的 A/B 接到 MCU 飞机 485 的 A/B；必要时共地。
3. PC 串口助手选择实际出现的串口号，配置 `115200 8N1`、无流控。
4. RK3588 上执行 `./interceptorctl aircraft xfer ...`。
5. PC 串口助手应先收到 RK 侧下发的数据；然后在 `timeout_ms` 结束前手动或自动回发响应数据。

不要再把测试设备写死成某个固定设备名。USB 串口插拔后设备名可能变化；如果在 RK3588 本机挂 USB 转 485 做临时回环，测试命令必须显式传串口参数，例如 `/dev/ttyACM1`：

```bash
python3 ../local_maintenance/tools/aircraft_485_responder.py --port /dev/ttyACM1 --baud 115200 --idle-ms 30
```

上面 responder 只用于临时测试，收到一帧后默认回复 `ACK:` + 原始数据。正式联调用 PC 串口助手或真实飞机设备即可。

文本测试：

```bash
./interceptorctl aircraft xfer --text ping --timeout-ms 500 --idle-ms 30
```

PC 串口助手应收到：

```text
ping
```

如果 PC 在 500ms 内回发 `ACK:ping`，预期：

```text
aircraft_transfer: ok result=0 error=None
rx: len=8 hex=41434b3a70696e67
text: ACK:ping
```

如果 PC 不回发，预期是超时：

```text
aircraft_transfer: failed result=3 error=None
```

这里 `result=3` 表示没有收到返回，不代表发送一定失败。确认发送是否到达，以 PC 串口助手接收窗口为准。

二进制测试：

```bash
./interceptorctl aircraft xfer --hex "01 02 03 0d" --timeout-ms 500 --idle-ms 30
```

PC 串口助手使用 HEX 显示时应收到：

```text
01 02 03 0D
```

如果 PC 回发 HEX：

```text
41 43 4B 3A 01 02 03 0D
```

预期：

```text
aircraft_transfer: ok result=0 error=None
rx: len=8 hex=41434b3a0102030d
```

带 CR 行尾的文本协议测试：

```bash
./interceptorctl aircraft xfer --text "AT+PING" --append-cr --timeout-ms 1000 --idle-ms 30
```

PC 串口助手应收到 `AT+PING` 后跟 `0D`。如果真实飞机协议要求 `CRLF`，使用：

```bash
./interceptorctl aircraft xfer --text "AT+PING" --append-cr --append-lf --timeout-ms 1000 --idle-ms 30
```

较慢响应设备测试：

```bash
./interceptorctl aircraft xfer --hex "01 03 00 00 00 02 c4 0b" --timeout-ms 1500 --idle-ms 50
```

说明：

- `timeout-ms=1500` 给设备最多 1.5s 的响应时间。
- `idle-ms=50` 表示收到最后一个字节后 50ms 没有新字节就结束本次接收。
- 如果响应一帧中间会停顿，优先增大 `idle-ms`；如果首字节来得慢，优先增大 `timeout-ms`。

最大长度边界测试：

```bash
./interceptorctl aircraft xfer --hex "$(python3 - <<'PY'
print(' '.join(['55'] * 220))
PY
)" --timeout-ms 500 --idle-ms 30
```

超过 220 字节时，RK 侧会直接拒绝：

```text
aircraft tx payload too large; max is 220 bytes
```

参数说明：

- `--hex`：发送原始字节，适合后续飞机真实协议。
- `--text`：发送 UTF-8 文本，适合联调看现象。
- `--append-cr` / `--append-lf`：需要行尾时追加 `0x0d` / `0x0a`。
- `--timeout-ms`：本次透传等待返回的总超时，单位 ms。默认 `1000`，RK 侧限制 `1` 到 `10000`。
- `--idle-ms`：收到返回后，连续空闲这么多 ms 就认为一帧结束。默认 `30`，RK 侧限制 `1` 到 `1000`。

推荐默认值：

- 普通调试：`--timeout-ms 500 --idle-ms 30`。
- 响应较慢：`--timeout-ms 1500 --idle-ms 50`。
- 只确认发送是否到 PC 串口助手：`--timeout-ms 200 --idle-ms 30`，PC 不回发时会得到 `result=3`。

MCU 返回码：

- `0`：成功。
- `1`：payload 长度错误。
- `2`：UART4 透传忙。
- `3`：超时没有收到返回。
- `4`：返回太长被截断。
- `5`：UART4 发送失败。

## 9. 被动 CAN 抓包

RK can0 设置：

```bash
sudo ip link set can0 down 2>/dev/null || true
sudo ip link set can0 type can bitrate 500000 restart-ms 100
sudo ip link set can0 up
```

本机没有依赖 `candump/cansend`。需要抓包时可临时用 Python SocketCAN 被动监听。

门电机运动时 0x001B/0x001C 已抓到的关键帧：

```text
0x0100 f3 ab 01 00 6b        使能
0x0100 fd 00 00 64 00 64 01 2c
0x0101 fd 00 02 c7 b8 01 00 6b
0x0100 fd 02 6b              运动命令接收确认
0x0100 36 ...                位置查询/回包
0x0100 3a ...                状态查询/回包
0x0100 f3 ab 00 00 6b        去使能
```

## 10. 常见问题

`result=2`：

- 表示 MCU 判断电机业务忙。
- 0x001B 已修复“刚 status 后立刻 motion 被 CAN 冷却窗口误判 busy”的问题。
- 如果仍出现，先执行 `./interceptorctl status` 看 `active` 是否不是 `idle`。

电机 accepted 但不动：

- 0x001B 前的根因是旧 `BT_STOP` 输入误触发急停，抓包只会看到 `FE 98` 和去使能，没有 `FD` 运动帧。
- 0x001B 曾在 `Hardware_Interceptor` 下屏蔽旧 `BT_STOP` 输入。
- 0x001F 起实体急停已接线，恢复 `BT_STOP` 输入读取；如运动中按下急停，预期再次看到 `FE 98` 急停和后续去使能帧。

位置慢慢变：

- 当前到位后会自动去使能，空载电机可能漂移。
- 后续如果机构需要保持位置，应改成到位后保持使能或加入保持策略。

daemon socket not found：

```bash
sudo systemctl restart interceptorctl.service
```

串口被旧服务占用：

```bash
sudo systemctl disable --now sbmcu.service 2>/dev/null || true
sudo systemctl restart interceptorctl.service
```
