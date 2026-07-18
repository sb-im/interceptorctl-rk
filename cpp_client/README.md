# interceptorctl C++ client

This directory provides a C++17 customer-side wrapper. Customer code calls typed
`DockClient` methods and receives parsed structures. Customer code does not
need to build JSON strings or parse JSON replies directly.

Transport path:

```text
Customer C++ application
  -> DockClient
  -> /tmp/interceptorctl.sock
  -> interceptorctl daemon
  -> /dev/mcu
```

Files:

- `dock_client.hpp`: public header with all structures, field units, and API comments.
- `dock_client.cpp`: Unix socket transport, JSON request builder, and internal lightweight JSON parser.
- `example.cpp`: detailed read-only example that reads every public status
  group and tries one passive aircraft RS485 receive. Control calls are shown
  only as commented snippets.
- `Makefile`: builds `libdock_client.a` and `dock_client_example`.

Build on RK3588:

```bash
cd /home/orangepi/interceptorctl/cpp_client
make clean
make
```

Run the example:

```bash
./dock_client_example
```

The example is safe for integration checks: it does not move motors, change the
power supply, write LEDs, write air-conditioner settings, or transmit on the
aircraft RS485 bus.

Minimal usage:

```cpp
#include "dock_client.hpp"

int main() {
    interceptorctl::DockClient dock;

    auto power = dock.power_status();
    if (!power.result.ok) {
        return 1;
    }

    int output_v_0p01v = power.output_volt_0p01v;
    (void)output_v_0p01v;

    auto environment = dock.environment_status();
    if (environment.result.ok) {
        int temp_0p01c = environment.temperature_0p01c;
        int humidity_0p01rh = environment.humidity_0p01rh;
        (void)temp_0p01c;
        (void)humidity_0p01rh;
    }

    auto led = dock.led_status();
    if (led.result.ok) {
        bool wz_red = led.wz.red;
        bool wz_green = led.wz.green;
        (void)wz_red;
        (void)wz_green;
    }

    auto switches = dock.switch_status();
    if (switches.result.ok) {
        bool top_active = switches.top;
        bool bottom_active = switches.bottom;
        bool button_active = switches.button;
        (void)top_active;
        (void)bottom_active;
        (void)button_active;
    }

    auto ac = dock.air_conditioner_status();
    if (ac.result.ok) {
        int return_air_temp_0p1c = ac.return_air_temp_0p1c;
        int dc_voltage_0p1v = ac.dc_voltage_0p1v;
        (void)return_air_temp_0p1c;
        (void)dc_voltage_0p1v;
    }
    return 0;
}
```

Important conventions:

- Every typed return structure has `result.ok`, `result.error`, and `result.raw_json`.
- Customer code must check `result.ok` before using business fields.
- `result.raw_json` is intended for field debugging. Normal business code should not parse it.
- Motor position unit is `0.1deg`.
- Motor speed unit is `0.1RPM`.
- Motor acceleration unit is `RPM/s`.
- `motor_status()` returns linked-axis `enabled`, `stall`, `reached`,
  `calibed`, `calibing`, `calib_failed`, and `final_reached` flags.
- Customer code should read `motor_status().motor.axis`.
- Application-level motor APIs are `door_open()` and `door_close()`.
- `door_open()`, `door_close()`, and `motor_trapezoid()` return after MCU ack by
  default. Pass `true` as the wait parameter only when the caller explicitly
  wants to wait for mechanical completion.
- The `timeout_s` argument is always the motion timeout used by asynchronous
  motion events. When `wait` is `true`, the same value is also the blocking wait
  timeout for the API call.
- Prefer `start_motion_event_thread()` for arrival/timeout notification.
  Position and homing events are generated from RK-side SocketCAN monitoring.
  Customer code does not need to poll `motor_status()` itself.
- Low-level motor commissioning APIs are `motor_enable()`, `motor_disable()`,
  `motor_home()`, `motor_home_stop()`, `motor_stop()`,
  `motor_release_stop()`, and `motor_trapezoid()`. Check `stop_status()` and
  `motor_status()` before using them on field hardware.
- Power voltage/current units are `0.01V` / `0.01A`.
- Power temperature unit is `0.1C`.
- Environment temperature/humidity units are `0.01C` / `0.01%RH`.
- `environment_status()` reads the GXHT30 sensor cached by the MCU. The MCU
  polls this sensor at a low rate, so it is suitable for environment display
  or logs, not hard real-time control.
- `led_status()` reads the TCA9554 LED expander on I2C1. LED mask bits are
  `bit0=JC_R`, `bit1=JC_G`, `bit2=CD_R`, `bit3=CD_G`, `bit4=WZ_R`,
  `bit5=WZ_G`, `bit6=DP_R`, and `bit7=DP_G`. A high bit turns the channel on.
- `led_set_mask()` writes the raw 8-bit LED output register.
- `led_set_group()` sets one red/green group (`Jc`, `Cd`, `Wz`, `Dp`, or
  `All`) to `Off`, `Red`, `Green`, or `Both`.
- `switch_status()` reads the active-low PSW1/PSW2/PSW3 inputs. PSW1/PD15 is
  the top microswitch, PSW2/PD14 is the bottom microswitch, and PSW3/PD13 is
  the user push button. The semantic fields are true when the input is pulled
  low by the switch.
- `air_conditioner_status()` reads HCNC4A air-conditioner status cached by the
  MCU UART5 plaintext Modbus state machine.
- Air-conditioner temperature unit is `0.1C`; DC voltage/current units are
  `0.1V` / `0.1A`.
- Air-conditioner control APIs are `air_conditioner_power()`,
  `air_conditioner_force_cool()`, `air_conditioner_force_heat()`,
  `air_conditioner_silent_mode()`, `air_conditioner_humidity()`,
  `air_conditioner_cool_start_temp()`, `air_conditioner_cool_diff()`,
  `air_conditioner_heat_start_temp()`, `air_conditioner_heat_diff()`, and
  `air_conditioner_dehumid_setpoint()`. They are blocking by default until the
  MCU state machine receives the Modbus reply or times out.
- `air_conditioner_cool_start_temp()`, `air_conditioner_cool_diff()`,
  `air_conditioner_heat_start_temp()`, and `air_conditioner_heat_diff()` use
  `0.1C` units. Example: `300` means `30.0C`, and `30` means `3.0C`.
- After changing air-conditioner parameters, call `air_conditioner_status()` to
  confirm `cool_start_temp_0p1c`, `cool_diff_0p1c`,
  `heat_start_temp_0p1c`, `heat_diff_0p1c`, `dehumid_setpoint_percent`,
  `run_mode`, and `monitor_humidity_percent`.
- `power_set()`, `power_on()`, and `power_off()` only confirm request submission. Call `power_status()` afterwards to confirm the actual state.
- `aircraft_transfer()` exposes `std::vector<uint8_t>` to customer code. The library converts it to/from JSON `tx_hex/rx_hex` internally.
- `aircraft_read()` passively reads raw bytes already received from the aircraft
  RS485 bus. It does not split protocol frames; customer code should keep its
  own parser state across calls.
- All typed methods are blocking calls. UI or main-loop applications should call them from a customer-owned worker thread.

Motor low-level example:

```cpp
interceptorctl::DockClient dock;

auto stop = dock.stop_status();
if (!stop.result.ok || stop.hardware_stop) {
    return 1;
}

auto before = dock.motor_status();
if (!before.result.ok || before.motor.active != "idle") {
    return 1;
}

auto enable = dock.motor_enable(interceptorctl::MotorTarget::Door);
if (!enable.result.ok) {
    return 1;
}

auto home = dock.motor_home(
    interceptorctl::MotorTarget::Door,
    false,  // return after MCU accepts the homing command
    60.0    // homing timeout in seconds
);
if (!home.result.ok) {
    return 1;
}

std::cout << "accepted homing motion_id=" << home.motion_id << "\n";

auto move = dock.motor_trapezoid(
    interceptorctl::MotorTarget::Door,
    181900,  // 18190.0 deg motor-side absolute position
    3000,    // 300.0 RPM maximum speed
    100,     // 100 RPM/s acceleration and deceleration
    false,   // return after MCU accepts the new target
    20.0     // timeout in seconds
);
if (!move.result.ok) {
    return 1;
}

std::cout << "accepted motion_id=" << move.motion_id << "\n";

// Interrupt an in-progress motion when required:
// auto stopped = dock.motor_stop();
// auto released = dock.motor_release_stop();
```

Asynchronous motion event example:

```cpp
interceptorctl::DockClient dock;

dock.start_motion_event_thread([](const interceptorctl::MotionEvent& event) {
    if (!event.result.ok) {
        std::cerr << "motion event subscription error: " << event.result.error << "\n";
        return;
    }

    if (event.event_type == interceptorctl::MotionEventType::Reached) {
        std::cout << "motion reached"
                  << " id=" << event.motion_id
                  << " action=" << event.action
                  << " monitor=" << event.monitor
                  << " reason=" << event.reason
                  << " position=" << event.position_0p1deg << "/0.1deg"
                  << " error=" << event.error_0p1deg << "/0.1deg"
                  << " calibed=" << event.calibed
                  << " elapsed=" << event.elapsed_s << "s\n";
    } else if (event.event_type == interceptorctl::MotionEventType::Timeout ||
               event.event_type == interceptorctl::MotionEventType::Failed ||
               event.event_type == interceptorctl::MotionEventType::Canceled) {
        std::cerr << "motion ended abnormally"
                  << " id=" << event.motion_id
                  << " type=" << event.type
                  << " reason=" << event.reason
                  << " position=" << event.position_0p1deg << "/0.1deg"
                  << " can_status=" << event.can_status
                  << "\n";
    }
});

// Send a motion command here. The command returns after MCU ack. The callback
// receives motion_started and the final reached/timeout/failed/canceled event.

dock.stop_motion_event_thread();
```

## Version Tracking

This directory is an independent Git repository for the customer-facing C++
client files only. It intentionally excludes RK daemon internals, temporary
hardware tests, logs, and build outputs.

Useful commands:

```bash
git status
git log --oneline --stat
git show --stat
git diff HEAD~1..HEAD
```
