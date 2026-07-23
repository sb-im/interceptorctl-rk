#include "dock_client.hpp"

#include <atomic>
#include <chrono>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

using interceptorctl::DockClient;

namespace {

/*
 * This example is intentionally read-only.
 *
 * It is safe to run during customer integration because it does not execute
 * power output commands, motor commands, LED writes, air-conditioner writes, or
 * aircraft RS485 transmit commands. It only reads cached status from the MCU
 * through the interceptorctl daemon.
 *
 * Control APIs are shown in comment blocks near the related status section.
 * Keep those calls commented until the application has its own safety checks,
 * operator confirmation, and hardware test procedure.
 */

void print_section(const char* title) {
    std::cout << "\n== " << title << " ==\n";
}

bool print_result(const char* name, const interceptorctl::Result& result) {
    std::cout << name << ": ok=" << std::boolalpha << result.ok;
    if (!result.ok) {
        std::cout << " error=" << result.error;
        if (!result.raw_json.empty()) {
            std::cout << " raw_json=" << result.raw_json;
        }
    }
    std::cout << "\n";
    return result.ok;
}

double scaled(int value, double divisor) {
    return static_cast<double>(value) / divisor;
}

[[maybe_unused]] std::string fixed1(int value, double divisor) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(1) << scaled(value, divisor);
    return out.str();
}

[[maybe_unused]] std::string fixed2(int value, double divisor) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(2) << scaled(value, divisor);
    return out.str();
}

[[maybe_unused]] std::string hex8(int value) {
    std::ostringstream out;
    out << "0x" << std::hex << std::setw(2) << std::setfill('0') << (value & 0xFF);
    return out.str();
}

[[maybe_unused]] std::string hex16(int value) {
    std::ostringstream out;
    out << "0x" << std::hex << std::setw(4) << std::setfill('0') << (value & 0xFFFF);
    return out.str();
}

[[maybe_unused]] std::string join_strings(const std::vector<std::string>& values) {
    if (values.empty()) {
        return "none";
    }
    std::ostringstream out;
    for (size_t i = 0; i < values.size(); ++i) {
        if (i != 0) {
            out << ",";
        }
        out << values[i];
    }
    return out.str();
}

void print_axis(const char* name, const interceptorctl::AxisData& axis) {
    std::cout << "  " << name
              << ": state=" << axis.state
              << " position=" << axis.position_0p1deg << "/0.1deg"
              << " enabled=" << std::boolalpha << axis.enabled
              << " stall=" << axis.stall
              << " reached=" << axis.reached
              << " final_reached=" << axis.final_reached
              << " calibed=" << axis.calibed
              << " calibing=" << axis.calibing
              << " calib_failed=" << axis.calib_failed
              << "\n";
}

int fail_if_not_ok(const char* name, const interceptorctl::Result& result) {
    if (result.ok) {
        return 0;
    }
    std::cerr << "Stop example: " << name << " failed. Check daemon, /dev/mcu, and field wiring.\n";
    return 1;
}

}  // namespace
#if 0
int main() {
    /*
     * DockClient talks to the local interceptorctl daemon through a Unix domain
     * socket. The daemon is the only process that owns /dev/mcu.
     *
     * Default socket path:
     *   /tmp/interceptorctl.sock
     *
     * If the field deployment changes the socket path, pass it explicitly:
     *   DockClient dock("/path/to/interceptorctl.sock");
     *
     * All typed API calls below are synchronous. A UI application should call
     * them from its own worker thread if blocking the UI thread is not allowed.
     */
    DockClient dock;

    print_section("Version");
    auto version = dock.version();
    if (!print_result("version", version.result)) {
        return fail_if_not_ok("version", version.result);
    }
    std::cout << "  mcu_firmware=" << version.version << "\n";

    print_section("Emergency Stop");
    auto stop = dock.stop_status();
    if (!print_result("stop_status", stop.result)) {
        return fail_if_not_ok("stop_status", stop.result);
    }
    std::cout << "  hardware_stop=" << std::boolalpha << stop.hardware_stop << "\n";
    std::cout << "  note: application code should refuse motor motion while hardware_stop is true.\n";

    print_section("Motor Status");
    auto motor = dock.motor_status();
    if (!print_result("motor_status", motor.result)) {
        return fail_if_not_ok("motor_status", motor.result);
    }
    std::cout << "  active_action=" << motor.motor.active << "\n";
    print_axis("linked_axis", motor.motor.axis);
    if (motor.motor.axis.can_communicated) {
        std::cout << "  can_observe: position=" << motor.motor.axis.can_position_0p1deg << "/0.1deg"
                  << " age=" << motor.motor.axis.can_age_s << "s"
                  << " target=" << motor.motor.axis.target_position_0p1deg << "/0.1deg"
                  << " observed_reached=" << std::boolalpha << motor.motor.axis.observed_reached
                  << "\n";
    }
    std::cout << "  units: motor position is motor-side absolute position in 0.1 degree.\n";

    /*
     * Motor control examples for commissioning only.
     *
     * These calls are deliberately commented out because they can energize and
     * move the linked single-axis motor. Production code should first check:
     *   1. stop_status().hardware_stop == false
     *   2. motor_status().motor.active == "idle"
     *   3. local application interlocks and operator intent
     *
     * Low-level APIs:
     *   auto enable = dock.motor_enable(interceptorctl::MotorTarget::Door);
     *   auto disable = dock.motor_disable(interceptorctl::MotorTarget::Door);
     *   auto home = dock.motor_home(
     *       interceptorctl::MotorTarget::Door,
     *       false,  // return after MCU accepts the homing command
     *       60.0    // homing timeout, seconds
     *   );
     *   auto home_stop = dock.motor_home_stop(interceptorctl::MotorTarget::Door);
     *   auto move = dock.motor_trapezoid(
     *       interceptorctl::MotorTarget::Door,
     *       181900,  // target position, 18190.0 deg motor-side
     *       3000,    // max speed, 300.0 RPM
     *       100,     // acceleration and deceleration, 100 RPM/s
     *       false,   // return after MCU accepts the new target
     *       20.0     // timeout, seconds
     *   );
     *
     * Application-level APIs:
     *   auto open = dock.door_open();       // non-blocking after MCU ack
     *   auto close = dock.door_close();     // updates target even while opening
     *   auto open_wait = dock.door_open(true, 20.0);  // optional completion wait
     *
     * Asynchronous motion event callback:
     *
     *   dock.start_motion_event_thread([](const interceptorctl::MotionEvent& event) {
     *       if (!event.result.ok && event.event_type == interceptorctl::MotionEventType::Unknown) {
     *           std::cerr << "motion event subscription error: " << event.result.error << "\n";
     *           return;
     *       }
     *
     *       std::cout << "motion_event"
     *                 << " type=" << event.type
     *                 << " motion_id=" << event.motion_id
     *                 << " action=" << event.action
     *                 << " monitor=" << event.monitor
     *                 << " reason=" << event.reason
     *                 << " target=" << event.target_position_0p1deg << "/0.1deg"
     *                 << " position=" << event.position_0p1deg << "/0.1deg"
     *                 << " error=" << event.error_0p1deg << "/0.1deg"
     *                 << " elapsed=" << event.elapsed_s << "s"
     *                 << " can_age=" << event.can_age_s << "s"
     *                 << " can_status=0x" << std::hex << event.can_status << std::dec
     *                 << " can_homing_status=0x" << std::hex << event.can_homing_status << std::dec
     *                 << " can_homing_age=" << event.can_homing_age_s << "s"
     *                 << " can_enabled=" << std::boolalpha << event.can_enabled
     *                 << " can_reached=" << event.can_reached
     *                 << " can_stall=" << event.can_stall
     *                 << " calibed=" << event.calibed
     *                 << " calibing=" << event.calibing
     *                 << " calib_failed=" << event.calib_failed
     *                 << "\n";
     *
     *       if (event.event_type == interceptorctl::MotionEventType::Reached) {
     *           // position monitor: target tolerance reached.
     *           // home monitor: homing completed, reason == "homing_done".
     *       } else if (event.event_type == interceptorctl::MotionEventType::Timeout ||
     *                  event.event_type == interceptorctl::MotionEventType::Failed ||
     *                  event.event_type == interceptorctl::MotionEventType::Canceled) {
     *           // Handle abnormal or replaced motion result here.
     *       }
     *   });
     *
     *   auto move = dock.motor_trapezoid(
     *       interceptorctl::MotorTarget::Door,
     *       181900,  // target position, 18190.0 deg motor-side
     *       10000,   // max speed, 1000.0 RPM
     *       1000,    // acceleration and deceleration, 1000 RPM/s
     *       false,   // return after MCU accepts the new target
     *       20.0
     *   );
     *
     *   if (move.result.ok) {
     *       std::cout << "accepted motion_id=" << move.motion_id << "\n";
     *   }
     *
     *   // Homing uses the same asynchronous event subscription:
     *   auto home = dock.motor_home(interceptorctl::MotorTarget::Door, false, 60.0);
     *   if (home.result.ok) {
     *       std::cout << "accepted homing motion_id=" << home.motion_id << "\n";
     *   }
     *
     *   // Keep the application alive while waiting for asynchronous events.
     *   // Stop the event thread during clean shutdown.
     *   dock.stop_motion_event_thread();
     */

    print_section("Power Supply");
    auto power = dock.power_status();
    if (!print_result("power_status", power.result)) {
        return fail_if_not_ok("power_status", power.result);
    }
    std::cout << "  set_voltage=" << fixed2(power.set_volt_0p01v, 100.0) << "V"
              << " set_current=" << fixed2(power.set_curr_0p01a, 100.0) << "A\n";
    std::cout << "  output_voltage=" << fixed2(power.output_volt_0p01v, 100.0) << "V"
              << " output_current=" << fixed2(power.output_curr_0p01a, 100.0) << "A"
              << " temperature=" << fixed1(power.temperature_0p1c, 10.0) << "C\n";
    std::cout << "  output_enabled=" << std::boolalpha << power.output_enabled
              << " communicated=" << power.is_communicated
              << " alarm=" << power.alarm
              << " last_error=" << power.last_error << "(" << power.last_error_name << ")\n";

    /*
     * Power control examples. These calls change field state and are not run by
     * this example.
     *
     *   auto set = dock.power_set(2400, 1580);  // 24.00 V, 15.80 A
     *   auto on = dock.power_on();
     *   auto off = dock.power_off();
     *
     * power_set(), power_on(), and power_off() confirm command submission.
     * Call power_status() afterwards to confirm measured output state.
     */

    print_section("UPS");
    auto ups = dock.ups_status();
    if (!print_result("ups_status", ups.result)) {
        return fail_if_not_ok("ups_status", ups.result);
    }
    std::cout << "  communicated=" << std::boolalpha << ups.is_communicated
              << " status=" << ups.status
              << " output_status=" << ups.output_status
              << " voltage=" << fixed2(ups.volt_0p01v, 100.0) << "V"
              << " current=" << fixed2(ups.curr_0p01a, 100.0) << "A"
              << " temperature_raw=" << ups.temp_0p01c
              << " request_power_off=" << ups.request_power_off
              << "\n";

    print_section("Environment Sensor");
    auto environment = dock.environment_status();
    if (!print_result("environment_status", environment.result)) {
        return fail_if_not_ok("environment_status", environment.result);
    }
    std::cout << "  communicated=" << std::boolalpha << environment.is_communicated
              << " temperature=" << fixed2(environment.temperature_0p01c, 100.0) << "C"
              << " humidity=" << fixed2(environment.humidity_0p01rh, 100.0) << "%RH"
              << " address=" << hex8(environment.address)
              << " last_error=" << environment.last_error << "(" << environment.last_error_name << ")"
              << " hal_status=" << environment.last_hal_status
              << " samples=" << environment.sample_count
              << "\n";
    std::cout << "  raw_temperature=" << environment.raw_temperature
              << " raw_humidity=" << environment.raw_humidity << "\n";

    print_section("LED Expander");
    auto led = dock.led_status();
    if (!print_result("led_status", led.result)) {
        return fail_if_not_ok("led_status", led.result);
    }
    std::cout << "  communicated=" << std::boolalpha << led.is_communicated
              << " mask=" << hex8(led.mask)
              << " input=" << hex8(led.input)
              << " polarity=" << hex8(led.polarity)
              << " config=" << hex8(led.config)
              << " address=" << hex8(led.address)
              << " last_error=" << led.last_error << "(" << led.last_error_name << ")"
              << " writes=" << led.write_count
              << "\n";
    std::cout << "  groups:"
              << " jc(R=" << led.jc.red << ",G=" << led.jc.green << ")"
              << " cd(R=" << led.cd.red << ",G=" << led.cd.green << ")"
              << " wz(R=" << led.wz.red << ",G=" << led.wz.green << ")"
              << " dp(R=" << led.dp.red << ",G=" << led.dp.green << ")"
              << "\n";

    /*
     * LED write examples. These calls change visible LED outputs and are not
     * run by this example.
     *
     *   auto wz_red = dock.led_set_group(interceptorctl::LedGroup::Wz,
     *                                    interceptorctl::LedColor::Red);
     *   auto all_off = dock.led_set_group(interceptorctl::LedGroup::All,
     *                                     interceptorctl::LedColor::Off);
     *   auto raw_mask = dock.led_set_mask(0x10);
     */

    print_section("Switch Inputs");
    auto switches = dock.switch_status();
    if (!print_result("switch_status", switches.result)) {
        return fail_if_not_ok("switch_status", switches.result);
    }
    std::cout << "  top=" << std::boolalpha << switches.top
              << " bottom=" << switches.bottom
              << " button=" << switches.button
              << " cover_button=" << switches.cover_button
              << " platform_switch=" << switches.platform_switch
              << " charge_base_switch=" << switches.charge_base_switch
              << " manual_action=" << switches.manual_action
              << " psw1=" << switches.psw1
              << " psw2=" << switches.psw2
              << " psw3=" << switches.psw3
              << " active_mask=" << hex8(switches.active_mask)
              << " raw_level_mask=" << hex8(switches.raw_level_mask)
              << " active_low=" << switches.active_low
              << "\n";
    std::cout << "  mapping: PSW1/PD15=platform_switch, PSW2/PD14=charge_base_switch, PSW3/PD13=cover_button.\n";
    std::cout << "  active-low rule: semantic fields are true when the input is pulled to GND.\n";
    std::cout << "  manual_action values: none, manual_opening, manual_closing.\n";

    print_section("Air Conditioner");
    auto ac = dock.air_conditioner_status();
    if (!print_result("air_conditioner_status", ac.result)) {
        return fail_if_not_ok("air_conditioner_status", ac.result);
    }
    std::cout << "  communicated=" << std::boolalpha << ac.is_communicated
              << " busy=" << ac.busy
              << " device=" << ac.device_status << "(" << ac.device_status_name << ")"
              << " indoor_fan=" << ac.indoor_fan_status
              << " outdoor_fan=" << ac.outdoor_fan_status
              << " compressor=" << ac.compressor_status
              << " heater=" << ac.heater_status
              << "\n";
    std::cout << "  temperatures:"
              << " return=" << fixed1(ac.return_air_temp_0p1c, 10.0) << "C"
              << " external=" << fixed1(ac.external_temp_0p1c, 10.0) << "C"
              << " condenser=" << fixed1(ac.condenser_temp_0p1c, 10.0) << "C"
              << " evaporator=" << fixed1(ac.evaporator_temp_0p1c, 10.0) << "C"
              << "\n";
    std::cout << "  electrical:"
              << " dc_voltage=" << fixed1(ac.dc_voltage_0p1v, 10.0) << "V"
              << " dc_current=" << fixed1(ac.dc_current_0p1a, 10.0) << "A"
              << " indoor_rpm=" << ac.indoor_fan_rpm
              << " outdoor_rpm=" << ac.outdoor_fan_rpm
              << " capacity=" << ac.cooling_capacity_w << "W"
              << "\n";
    std::cout << "  settings:"
              << " cool_start=" << fixed1(ac.cool_start_temp_0p1c, 10.0) << "C"
              << " cool_diff=" << fixed1(ac.cool_diff_0p1c, 10.0) << "C"
              << " heat_start=" << fixed1(ac.heat_start_temp_0p1c, 10.0) << "C"
              << " heat_diff=" << fixed1(ac.heat_diff_0p1c, 10.0) << "C"
              << " dehumid=" << ac.dehumid_setpoint_percent << "%"
              << " run_mode=" << ac.run_mode << "(" << ac.run_mode_name << ")"
              << " monitor_humidity=" << ac.monitor_humidity_percent << "%"
              << "\n";
    std::cout << "  errors:"
              << " alarms=" << hex16(ac.alarms) << "(" << join_strings(ac.alarm_names) << ")"
              << " last_error=" << ac.last_error << "(" << ac.last_error_name << ")"
              << " last_exception=" << ac.last_exception
              << " last_function=" << hex8(ac.last_function)
              << " last_control=" << ac.last_control_action << "(" << ac.last_control_action_name << ")"
              << " value=" << ac.last_control_value
              << " result=" << ac.last_control_result << "(" << ac.last_control_result_name << ")"
              << "\n";
    std::cout << "  counters:"
              << " tx=" << ac.tx_count
              << " rx=" << ac.rx_count
              << " crc=" << ac.crc_error_count
              << " timeout=" << ac.timeout_count
              << " protocol=" << ac.protocol_version
              << " software=" << ac.software_version
              << " hardware=" << ac.hardware_version
              << "\n";

    /*
     * Air-conditioner control examples. These calls write Modbus registers and
     * are not run by this example.
     *
     *   auto power_on = dock.air_conditioner_power(true);
     *   auto power_off = dock.air_conditioner_power(false);
     *   auto cool_off = dock.air_conditioner_force_cool(false);
     *   auto heat_off = dock.air_conditioner_force_heat(false);
     *   auto normal_mode = dock.air_conditioner_silent_mode(false);
     *   auto humidity = dock.air_conditioner_humidity(55);
     *
     * Temperature setting units are 0.1 C:
     *   auto cool_start = dock.air_conditioner_cool_start_temp(300);  // 30.0 C
     *   auto cool_diff = dock.air_conditioner_cool_diff(30);          // 3.0 C
     *   auto heat_start = dock.air_conditioner_heat_start_temp(50);   // 5.0 C
     *   auto heat_diff = dock.air_conditioner_heat_diff(80);          // 8.0 C
     *   auto dehumid = dock.air_conditioner_dehumid_setpoint(60);     // 60 %
     *
     * After any write, call air_conditioner_status() again and check the
     * settings fields to confirm actual register readback.
     */

    print_section("Aircraft RS485 Passive Read");
    /*
     * Passive read does not transmit bytes. It returns raw bytes already
     * buffered by the MCU UART4 interrupt path.
     *
     * The returned rx vector is a byte stream, not application frames. Customer
     * aircraft protocol code should keep its own parser state across calls and
     * split frames using its own header, length, delimiter, and CRC rules.
     *
     * If no aircraft-side sender is active, result.ok=false with result_code=3
     * and result_name="timeout" is expected and is not a daemon or MCU failure.
     */
    auto aircraft = dock.aircraft_read(300, 80);
    print_result("aircraft_read", aircraft.result);
    std::cout << "  result=" << aircraft.result_code << "(" << aircraft.result_name << ")"
              << " rx_len=" << aircraft.rx.size()
              << " rx_hex=" << aircraft.rx_hex
              << " dropped=" << aircraft.dropped
              << " remaining=" << aircraft.remaining
              << "\n";

    /*
     * Aircraft transmit example. This is commented because it writes to the
     * aircraft RS485 bus.
     *
     *   std::vector<uint8_t> request = {0x01, 0x02, 0x03, 0x0d};
     *   auto reply = dock.aircraft_transfer(request, 1000, 30);
     */

    return 0;
}
#else
int main() {
    DockClient dock;

    auto enable = dock.motor_enable(interceptorctl::MotorTarget::Door);
    if (!enable.result.ok) {
        std::cerr << "motor_enable failed: " << enable.result.error << "\n";
        //return 1;
    }

    std::atomic<int> target_motion_id{0};
    std::atomic<bool> motion_finished{false};
    std::atomic<int> exit_code{0};

    dock.start_motion_event_thread([&](const interceptorctl::MotionEvent& event) {
        if (!event.result.ok) {
            std::cerr << "motion event subscription error: " << event.result.error << "\n";
            exit_code.store(1);
            motion_finished.store(true);
            return;
        }

        int target = target_motion_id.load();
        if (target == 0 || event.motion_id != target) {
            return;
        }

        std::cout << "motion_event"
                  << " type=" << event.type
                  << " motion_id=" << event.motion_id
                  << " reason=" << event.reason
                  << " target=" << event.target_position_0p1deg << "/0.1deg"
                  << " position=" << event.position_0p1deg << "/0.1deg"
                  << " error=" << event.error_0p1deg << "/0.1deg"
                  << " elapsed=" << event.elapsed_s << "s"
                  << " can_status=0x" << std::hex << event.can_status << std::dec
                  << " can_stall=" << std::boolalpha << event.can_stall
                  << "\n";

        if (event.event_type == interceptorctl::MotionEventType::Reached) {
            motion_finished.store(true);
        } else if (event.event_type == interceptorctl::MotionEventType::Timeout ||
                   event.event_type == interceptorctl::MotionEventType::Failed ||
                   event.event_type == interceptorctl::MotionEventType::Canceled) {
            exit_code.store(2);
            motion_finished.store(true);
        }
    });

    auto move = dock.motor_trapezoid(
        interceptorctl::MotorTarget::Door,
        181900,  // target position, 18190.0 deg motor-side
        3000,   // max speed, 1000.0 RPM
        1000,    // acceleration and deceleration, 1000 RPM/s
        false,   // return after MCU accepts the new target
        30.0
    );

    if (!move.result.ok) {
        std::cerr << "motor_trapezoid failed: " << move.result.error << "\n";
        dock.stop_motion_event_thread();
        return 1;
    }

    target_motion_id.store(move.motion_id);
    std::cout << "accepted motion_id=" << move.motion_id << "\n";


    while (!motion_finished.load()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
            print_section("Motor Status");
        auto motor = dock.motor_status();
        if (!print_result("motor_status", motor.result)) {
            return fail_if_not_ok("motor_status", motor.result);
        }
        std::cout << "  active_action=" << motor.motor.active << "\n";
        print_axis("linked_axis", motor.motor.axis);
        if (motor.motor.axis.can_communicated) {
            std::cout << "  can_observe: position=" << motor.motor.axis.can_position_0p1deg << "/0.1deg"
                    << " age=" << motor.motor.axis.can_age_s << "s"
                    << " target=" << motor.motor.axis.target_position_0p1deg << "/0.1deg"
                    << " observed_reached=" << std::boolalpha << motor.motor.axis.observed_reached
                    << "\n";
        }
        std::cout << "  units: motor position is motor-side absolute position in 0.1 degree.\n";

    }

    dock.stop_motion_event_thread();
    dock.motor_stop();
    return exit_code.load();
}

#endif
