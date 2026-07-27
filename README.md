# interceptorctl

RK3588-side control program for the interceptor dock.

The primary path is now:

```text
CLI / future virtual device
  -> interceptorctl daemon over /tmp/interceptorctl.sock
  -> single owned /dev/mcu
  -> STM32 USART1 Package protocol
```

Only the daemon opens `/dev/mcu`. Do not let other programs open `/dev/mcu`
directly, otherwise ACK packets, status pushes, OTA traffic, and logs can be
consumed by the wrong process.

The current STM32 interceptor firmware runs USART1 in silent request-response
mode: debug, error, status, and motor-position push packets are suppressed.
Only command ACK/data responses are expected during normal operation.

Current STM32 firmware version: `0x0033`.
Current RK3588 `interceptorctl` version: `20260701-1`.

## Files

- `mcu.py`: STM32 Package protocol and high-level MCU commands.
- `daemon.py`: single-owner daemon for `/dev/mcu`; exposes a local Unix socket.
- `cli.py`: simple command-line client.
- `cpp_client/`: C++17 typed client library and closed-loop example.
- `GUIDANCE.md`: customer integration guide for Python and C/C++ applications.
- `GUIDANCE_CPP.md`: detailed C++ customer integration guide with JSON examples.
- `interceptorctl`: shell wrapper for `cli.py`.
- `run.sh`: starts `daemon.py`.
- `systemd/interceptorctl.service`: boot-time systemd service.
- `tools/install_service.sh`: installs and enables `interceptorctl.service`.
- `tools/flash_mcu.py`: independent STM32 firmware flashing tool.
- `main.py`: legacy HTTP debug service kept for reference.

## Start

```bash
cd /home/orangepi/interceptorctl
sudo ./tools/install_service.sh
systemctl status interceptorctl.service
```

The daemon creates:

```text
/tmp/interceptorctl.sock
```

## Logs

The systemd service writes daemon logs to:

```text
/home/orangepi/interceptorctl/logs/interceptorctl.log
```

The log file is rotated by the daemon:

- Max size per file: `10 MB`.
- Max retained files: `50`.
- Rotated files use Python logging names such as `interceptorctl.log.1`,
  `interceptorctl.log.2`, and so on.

Useful commands:

```bash
tail -f /home/orangepi/interceptorctl/logs/interceptorctl.log
ls -lh /home/orangepi/interceptorctl/logs/
```

MCU traffic logs are decoded for humans. Example:

```text
mcu tx name=motor_enable cmd=interceptor.motor_enable(set=12,id=14) payload=target=door(0) enabled=true raw=0001
mcu tx name=motor_trapezoid cmd=interceptor.motor_trapezoid(set=12,id=9) payload=target=door(0) position=181900/0.1deg(18190.0deg) speed=10000/0.1rpm(1000.0rpm) accel=1000rpm/s raw=008cc602001027e803
```

`set=12` is the interceptor command set. `id=14` is `motor_enable`,
`id=9` is `motor_trapezoid`, and `id=12` is `ups_status`. The raw hex is kept
at the end so low-level packet problems can still be checked.

Power polling is handled inside the STM32 firmware by `MOD_Power485_Exec()`.
The MCU keeps a power status cache and advances one USART3 Modbus transaction
at a time without blocking the main loop while waiting for the power supply
reply.

Power control commands are accepted by the MCU and applied by the power polling
state machine. `power status` returns the latest MCU cache including
temperature; `power set/on/off` show the requested target rather than cached
measurements.

## Firmware Flash

On the RK3588 board, MCU firmware is stored under:

```text
/home/orangepi/interceptorctl/tools/
```

The verified flash flow on `jjj` is:

```bash
sudo /usr/bin/python3 /home/orangepi/interceptorctl/tools/flash_mcu.py \
  /home/orangepi/interceptorctl/tools/sbdock_0x0033_mcu_motor_communication.bin
```

Preview without flashing:

```bash
sudo /usr/bin/python3 /home/orangepi/interceptorctl/tools/flash_mcu.py --dry-run \
  /home/orangepi/interceptorctl/tools/sbdock_0x0033_mcu_motor_communication.bin
```

`flash_mcu.py` stops `interceptorctl.service`, drives BOOT0/RESET GPIO, runs
`stm32loader`, then starts `interceptorctl.service` again. It does not start the
old `sbmcu.service`.

## CLI

Global options:

```bash
./interceptorctl --json <command>
./interceptorctl --socket /tmp/interceptorctl.sock <command>
```

### System And Stop

```bash
./interceptorctl version                 # read STM32 firmware version
./interceptorctl status                  # read combined motor and power status
./interceptorctl estop                   # read hardware/software emergency-stop status
./interceptorctl stop                    # set MCU software motor stop
./interceptorctl release-stop            # clear software stop and reset motor state machines
```

### Door Business Actions

These commands are RK-side wrappers around the low-level trapezoid command.
The new linked mechanics use one motor only. The RK daemon fills the configured
open/close target position plus independent max speed and acceleration, then sends
`motor_trapezoid` to the MCU. By default the CLI returns after the MCU accepts
the target. Add `--wait` to wait for motion completion, or `--timeout <seconds>`
to change the wait timeout.

```bash
./interceptorctl door open
./interceptorctl door close
./interceptorctl door open --wait
./interceptorctl door close --wait --timeout 20
```

### Low-Level Motor Debug

```bash
./interceptorctl motor status

./interceptorctl motor door enable
./interceptorctl motor door disable
./interceptorctl motor door home
./interceptorctl motor door home --wait --timeout 60
./interceptorctl motor door home-stop
./interceptorctl motor door trap --pos 181900 --speed 3000 --accel 100
./interceptorctl motor door trap --pos 181900 --speed 3000 --accel 100 --wait
./interceptorctl motor door trap --pos 181900 --speed 3000 --accel 100 --wait --timeout 20
```

`home` starts low-level motor homing/calibration. `home-stop` sends the motor
driver homing-stop command. `trap` means absolute trapezoid motion in raw motor
protocol units. `door`, `motor`, and `motor1` select the linked motor.

### GPpower3000 Power Supply

```bash
./interceptorctl power status
./interceptorctl power temp              # alias of power status
./interceptorctl power set 24.00 1.00    # voltage/current in V/A
./interceptorctl power on
./interceptorctl power off
./interceptorctl power raw --hex "01 03 00 1c 00 01 e4 0d"
./interceptorctl power raw --hex "01 03 00 1c 00 01 e4 0d" --timeout-ms 1000 --idle-ms 20
```

`power raw` is a debug command that sends exact raw bytes to the MCU USART3
power RS485 path. It does not auto-fill CRC. This example reads a GPpower3000
power supply at Modbus address `0x01`.

### UPS

```bash
./interceptorctl ups status
```

### Environment Sensor

```bash
./interceptorctl env status
```

### LED Expander

```bash
./interceptorctl led status

./interceptorctl led jc off
./interceptorctl led jc red
./interceptorctl led jc green
./interceptorctl led jc both
./interceptorctl led jc yellow

./interceptorctl led cd off
./interceptorctl led cd red
./interceptorctl led cd green
./interceptorctl led cd both
./interceptorctl led cd yellow

./interceptorctl led wz off
./interceptorctl led wz red
./interceptorctl led wz green
./interceptorctl led wz both
./interceptorctl led wz yellow

./interceptorctl led dp off
./interceptorctl led dp red
./interceptorctl led dp green
./interceptorctl led dp both
./interceptorctl led dp yellow

./interceptorctl led all off
./interceptorctl led all red
./interceptorctl led all green
./interceptorctl led all both
./interceptorctl led all yellow

./interceptorctl led mask 0x10
./interceptorctl led mask 255
```

`yellow` turns on red and green together, same as `both`.

### Switch Inputs

```bash
./interceptorctl switch status
```

These commands read the active-low PSW1/PSW2/PSW3/PSW4 GPIO inputs:

- `PSW1` / `PD15`: `module_reached_switch`
- `PSW2` / `PD14`: `aircraft_position_switch`
- `PSW3` / `PD13`: `cover_button`
- `PSW4` / `PD12`: `aircraft_present_switch`

The semantic fields are `true` when the switch pulls the input to GND.
`raw_level_mask` keeps the raw GPIO level before active-low conversion:
bit0=PSW1, bit1=PSW2, bit2=PSW3, bit3=PSW4, and bit=1 means high level.
`manual_action_name` is `none`, `manual_opening`, or `manual_closing` and
reports MCU-side cover-button handling.

When the cover button requests a manual close, the MCU accepts the request
only when `aircraft_position_switch` (PSW2) and
`aircraft_present_switch` (PSW4) have the same state: both active or both
inactive. A request is blocked when exactly one input is active. The inputs
are sampled only when the button triggers the close; changing them after
motion starts does not stop the motion. `module_reached_switch` (PSW1) is
status-only and is not part of this close interlock.

### Air Conditioner

```bash
./interceptorctl ac status
./interceptorctl ac settings

./interceptorctl ac power on
./interceptorctl ac power off
./interceptorctl ac power on --no-wait
./interceptorctl ac power off --timeout 3

./interceptorctl ac cool on
./interceptorctl ac cool off

./interceptorctl ac heat on
./interceptorctl ac heat off

./interceptorctl ac mode normal
./interceptorctl ac mode silent

./interceptorctl ac cool-temp 30.0
./interceptorctl ac cool-diff 3.0
./interceptorctl ac heat-temp 5.0
./interceptorctl ac heat-diff 8.0
./interceptorctl ac dehumid 60
./interceptorctl ac humidity 55
```

The AC CLI waits for the MCU UART5 Modbus reply by default. Add `--no-wait` to
return after queue acceptance, or `--timeout <seconds>` to change the wait time.
Use `ac settings` after changing parameters to confirm the values read back by
the MCU from the air-conditioner registers.

### Aircraft UART4 RS485 Passthrough

```bash
./interceptorctl aircraft read
./interceptorctl aircraft read --timeout-ms 500
./interceptorctl aircraft read --timeout-ms 500 --max-len 80

./interceptorctl aircraft xfer --text ping
./interceptorctl aircraft xfer --text ping --append-cr
./interceptorctl aircraft xfer --text ping --append-lf
./interceptorctl aircraft xfer --text ping --append-cr --append-lf --timeout-ms 1500 --idle-ms 30

./interceptorctl aircraft xfer --hex "01 02 03 0d" --timeout-ms 1500 --idle-ms 30
./interceptorctl aircraft xfer --hex "01,02,03,0d"
./interceptorctl aircraft xfer --hex "01:02:03:0d"
```

Use `--json` to print the raw daemon response:

```bash
./interceptorctl --json status
```

The `motor ... trap` interface uses raw motor protocol units:

- `--pos`: absolute target position in `0.1 degree`.
- `--speed`: max speed in `0.1 RPM`.
- `--accel`: accel and decel in `RPM/s`.

`status` and `motor status` return motor positions in `0.1 degree` plus driver
flags such as `enabled`, `stall`, and `reached`. The `communicated` field is
maintained by the MCU from valid motor replies. The separate RK SocketCAN
observation is diagnostic data used to assist asynchronous motion monitoring.

## Environment Sensor

`env status` reads the GXHT30 temperature/humidity status cached by the MCU.
The STM32 polls the I2C1 sensor at a low rate, using the GXHT30 0.5 mps
periodic mode, so this data is intended for display and logs rather than hard
real-time control.

- Temperature unit: `0.01C`.
- Humidity unit: `0.01%RH`.
- `last_error=0(ok)` means the latest accepted sample is valid.
- Typical address is `0x44`.

## LED Expander

`led status` reads the TCA9554 LED expander on I2C1. The device address is
`0x20` because A0/A1/A2 are tied to GND. All P0..P7 pins are configured as
outputs; output bit `1` turns the corresponding LED channel on through the
external S8050 low-side driver.

Bit mapping:

- bit0: `JC_R`, bit1: `JC_G`
- bit2: `CD_R`, bit3: `CD_G`
- bit4: `WZ_R`, bit5: `WZ_G`
- bit6: `DP_R`, bit7: `DP_G`

Examples:

```bash
./interceptorctl led status
./interceptorctl led wz red
./interceptorctl led wz green
./interceptorctl led wz both
./interceptorctl led all off
./interceptorctl led mask 0x10
```

`both` turns on the red and green channels of the selected group. `mask`
writes the raw 8-bit output register and then reads the TCA9554 registers back
for closed-loop verification.

## Air Conditioner

`ac status` reads the HCNC4A air-conditioner status cached by the MCU UART5
RS485 state machine. The field AC protocol is plaintext Modbus RTU: the MCU
builds a normal Modbus frame, appends CRC16, sends that frame over UART5, and
validates the plaintext Modbus response CRC.

Important units:

- Temperature registers: `0.1C`.
- DC input voltage/current: `0.1V` / `0.1A`.
- Fan speed: `rpm`.
- Cooling capacity: `W`.
- Cooling/heating parameter commands take human-readable Celsius values in the
  CLI, then send protocol values in `0.1C`.

Supported control commands:

```bash
./interceptorctl ac status
./interceptorctl ac settings
./interceptorctl ac power on
./interceptorctl ac power off
./interceptorctl ac cool on
./interceptorctl ac cool off
./interceptorctl ac heat on
./interceptorctl ac heat off
./interceptorctl ac mode normal
./interceptorctl ac mode silent
./interceptorctl ac cool-temp 30.0
./interceptorctl ac cool-diff 3.0
./interceptorctl ac heat-temp 5.0
./interceptorctl ac heat-diff 8.0
./interceptorctl ac dehumid 60
./interceptorctl ac humidity 55
```

Parameter meanings:

- `cool-temp`: compressor cooling start temperature, protocol register
  `0x000A`, valid range `20.0..50.0C`.
- `cool-diff`: compressor cooling hysteresis, protocol register `0x000C`,
  valid range `1.0..10.0C`.
- `heat-temp`: heating start temperature, protocol register `0x001C`, valid
  range `-40.0..25.0C`.
- `heat-diff`: heating hysteresis, protocol register `0x001E`, valid range
  `5.0..15.0C`.
- `dehumid`: dehumidification setpoint, protocol register `0x0028`, valid
  range `10..90%`.
- `humidity`: monitor humidity downlink, protocol register `0x020B`, valid
  range `0..100%`; this is not the dehumidification setpoint.

For example, `cool-temp 30.0` plus `cool-diff 3.0` means the controller is
expected to start cooling around `30.0C` return-air temperature and stop around
`27.0C`, subject to the air-conditioner's internal protection logic and field
test results. After any write, run `./interceptorctl ac settings` to confirm
the actual register readback.

The MCU accepts one AC control request into its lightweight queue, sends it from
the UART5 state machine, then records `last_control_result`. The CLI waits for
that completion by default; use `--no-wait` to return immediately after queue
acceptance.

## Aircraft Passthrough

`aircraft read` passively reads raw bytes already received from STM32 UART4
RS485. It does not split protocol frames; customer code should parse the
aircraft protocol.

`aircraft xfer` sends one payload through STM32 UART4 RS485 and waits for one
response. The STM32 uses idle-time framing because the aircraft protocol is not
known yet.

Current MCU-side UART4 RS485 settings:

- `115200 8N1`, no parity, no hardware flow control.
- Max TX payload: `220` bytes.
- Max RX payload: `220` bytes.
- Passive RX ring buffer: `512` bytes. If the customer application reads too
  slowly, oldest bytes are dropped and reported by `dropped`.
- RS485 direction is controlled by STM32 GPIO: transmit before sending, back to
  receive after UART4 transmission-complete interrupt.

- `aircraft read --timeout-ms`: waits for at least one buffered byte.
- `aircraft read --max-len`: maximum bytes returned in one call.
- `--hex`: raw bytes, for binary protocols.
- `--text`: UTF-8 text, useful for simple loopback tests.
- `--append-cr` / `--append-lf`: append line endings when needed.
- `--timeout-ms`: total UART4 response wait time.
- `--idle-ms`: response is complete after this many idle milliseconds.

The current implementation exposes both passive raw-byte receive and
request-response transfer. A future virtual serial device can be built on top
of the same daemon and MCU commands without letting customer programs open
`/dev/mcu` directly.

For detailed PC serial-assistant and optional USB-RS485 responder tests, see
`TESTING.md`.
