#ifndef INTERCEPTORCTL_CPP_CLIENT_DOCK_CLIENT_HPP
#define INTERCEPTORCTL_CPP_CLIENT_DOCK_CLIENT_HPP

/**
 * @file dock_client.hpp
 * @brief C++17 typed client for the RK3588 interceptorctl daemon.
 *
 * This is the main public header for customer applications. Customer code does
 * not need to build JSON strings, parse JSON replies, open /dev/mcu, or
 * understand the STM32 Package binary protocol.
 *
 * Communication path:
 *
 * @code
 * Customer C++ application
 *   -> /tmp/interceptorctl.sock
 *   -> interceptorctl daemon
 *   -> /dev/mcu
 *   -> STM32 / motors / power supply / UPS / aircraft RS485
 * @endcode
 *
 * Threading model:
 * - Normal request/reply APIs are synchronous and block until the daemon
 *   replies or the socket timeout expires.
 * - start_motion_event_thread() creates one background thread that subscribes
 *   to daemon motion events and invokes a customer callback when motion reaches
 *   the target, homing completes, times out, fails, or is canceled.
 * - UI or scheduler applications should still avoid long blocking calls on
 *   their main loop.
 *
 * Unit conventions:
 * - Motor position: 0.1 degree.
 * - Motor speed: 0.1 RPM.
 * - Motor acceleration: RPM/s.
 * - Power voltage: 0.01 V.
 * - Power current: 0.01 A.
 * - Power temperature: 0.1 C.
 * - Environment temperature: 0.01 C.
 * - Environment humidity: 0.01 %RH.
 * - Air-conditioner temperature: 0.1 C.
 * - Air-conditioner DC voltage/current: 0.1 V / 0.1 A.
 * - LED output mask: bit field, bit0=JC_R, bit1=JC_G, bit2=CD_R,
 *   bit3=CD_G, bit4=WZ_R, bit5=WZ_G, bit6=DP_R, bit7=DP_G.
 * - Switch inputs: active-low, PSW1/PD15=module_reached_switch,
 *   PSW2/PD14=aircraft_position_switch, PSW3/PD13=cover_button,
 *   PSW4/PD12=aircraft_present_switch.
 */

#include <cstdint>
#include <atomic>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace interceptorctl {

/**
 * @brief Motor target used by low-level motor APIs.
 */
enum class MotorTarget {
    /** @brief Door motor. Maps to the firmware target id 0. */
    Door,
};

/**
 * @brief LED group controlled by the TCA9554 GPIO expander.
 */
enum class LedGroup {
    /** @brief JC indicator pair: bit0 red, bit1 green. */
    Jc,

    /** @brief CD indicator pair: bit2 red, bit3 green. */
    Cd,

    /** @brief WZ indicator pair: bit4 red, bit5 green. */
    Wz,

    /** @brief DP indicator pair: bit6 red, bit7 green. */
    Dp,

    /** @brief Apply the requested color to every LED group. */
    All,
};

/**
 * @brief LED color request for one red/green indicator pair.
 */
enum class LedColor {
    /** @brief Turn both red and green channels off. */
    Off,

    /** @brief Turn on the red channel only. */
    Red,

    /** @brief Turn on the green channel only. */
    Green,

    /** @brief Turn on both red and green channels. */
    Both,
};

/**
 * @brief HCNC4A air-conditioner control action.
 */
enum class AirConditionerAction {
    /** @brief Write remote air-conditioner power register 0x0202. */
    RemotePower,

    /** @brief Write force-cooling register 0x0206. */
    ForceCool,

    /** @brief Write force-heating register 0x0204. */
    ForceHeat,

    /** @brief Write run-mode register 0x0020. */
    RunMode,

    /** @brief Write monitor humidity register 0x020B. */
    Humidity,

    /** @brief Write compressor cooling start temperature register 0x000A. */
    CoolStartTemp,

    /** @brief Write compressor cooling hysteresis register 0x000C. */
    CoolDiff,

    /** @brief Write heating start temperature register 0x001C. */
    HeatStartTemp,

    /** @brief Write heating hysteresis register 0x001E. */
    HeatDiff,

    /** @brief Write dehumidification setpoint register 0x0028. */
    DehumidSetpoint,
};

/**
 * @brief Common result field embedded in every typed return structure.
 *
 * Customer code must check result.ok before using any business data field in
 * the same return structure. If result.ok is false, the business data fields
 * should be treated as invalid.
 */
struct Result {
    /**
     * @brief Request success flag.
     *
     * true means the daemon handled the command and the business operation
     * succeeded. false means socket I/O failed, the daemon returned an error,
     * the MCU returned an error, or the business operation failed.
     */
    bool ok = false;

    /**
     * @brief Human-readable error text.
     *
     * Valid mainly when ok is false. Typical values include "timeout",
     * "connect failed", and "power result 3". Field logs should include both
     * error and raw_json when possible.
     */
    std::string error;

    /**
     * @brief Raw JSON line returned by the daemon.
     *
     * Normal business code does not need this field. It is kept for field
     * debugging and support so the full daemon reply can be inspected.
     */
    std::string raw_json;

    /**
     * @brief Convenience conversion for code such as if (result).
     */
    explicit operator bool() const { return ok; }
};

/**
 * @brief Firmware version query result.
 */
struct VersionResult {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /** @brief MCU firmware version string, for example "0x0023". */
    std::string version;
};

/**
 * @brief Hardware emergency-stop button status.
 */
struct StopStatus {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /**
     * @brief Hardware emergency-stop button state.
     *
     * true means the physical emergency-stop button is pressed. The current
     * system convention is to inhibit motor actions while emergency stop is
     * active. Customer application logic decides how other functions behave.
     */
    bool hardware_stop = false;
};

/**
 * @brief Status data for one motor axis.
 */
struct AxisData {
    /**
     * @brief Axis state string.
     *
     * Common linked-axis values: "unknown", "moving", "open", "closed",
     * and "error".
     */
    std::string state;

    /**
     * @brief Motor-side absolute position in 0.1 degree.
     *
     * Example: 2092769 means 209276.9 degrees. This is motor-side accumulated
     * angle, not door panel angle.
     */
    int position_0p1deg = 0;

    /** @brief true when RK SocketCAN has recently observed a motor position frame. */
    bool can_communicated = false;

    /**
     * @brief Latest RK-side passive CAN position in 0.1 degree.
     *
     * This field is a passive observation from the shared CAN bus. The RK code
     * never sends motor control frames on CAN.
     */
    int can_position_0p1deg = 0;

    /** @brief Age of the passive CAN observation in seconds. Negative means unavailable. */
    double can_age_s = -1.0;

    /**
     * @brief Last target position requested through door_open(), door_close(),
     * or motor_trapezoid(), in 0.1 degree. Zero may also be a valid close target.
     */
    int target_position_0p1deg = 0;

    /**
     * @brief RK-side passive reached estimate.
     *
     * true means can_position_0p1deg is within the RK tolerance of the last
     * requested target. The MCU still owns final motor disable logic.
     */
    bool observed_reached = false;

    /** @brief Motor driver enable state. true means the motor is enabled. */
    bool enabled = false;

    /** @brief Motor stall-protection state reported by the driver. */
    bool stall = false;

    /** @brief Raw driver reached flag. true means the driver reports target reached. */
    bool reached = false;

    /** @brief Calibration-ready flag kept by MCU motor runtime. */
    bool calibed = false;

    /** @brief Homing/calibration in-progress flag reported by MCU motor runtime. */
    bool calibing = false;

    /** @brief Homing/calibration failure flag reported by MCU motor runtime. */
    bool calib_failed = false;

    /** @brief MCU final reached flag after its own motion supervision. */
    bool final_reached = false;
};

/**
 * @brief Linked single-axis motor status.
 */
struct MotorData {
    /**
     * @brief Current system motion action.
     *
     * Common values: "idle", "door_open", "door_close", and "door_move".
     */
    std::string active;

    /** @brief Linked single motor axis status. */
    AxisData axis;
};

/**
 * @brief Return value for linked motor status query.
 */
struct MotorStatus {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /** @brief Parsed linked motor status. */
    MotorData motor;
};

/**
 * @brief GPpower3000 power supply status.
 */
struct PowerStatus {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /** @brief Configured output voltage in 0.01 V. Example: 2400 means 24.00 V. */
    int set_volt_0p01v = 0;

    /** @brief Configured output current in 0.01 A. Example: 1580 means 15.80 A. */
    int set_curr_0p01a = 0;

    /** @brief Measured output voltage in 0.01 V. */
    int output_volt_0p01v = 0;

    /** @brief Measured output current in 0.01 A. */
    int output_curr_0p01a = 0;

    /** @brief Power supply temperature in 0.1 C. Example: 310 means 31.0 C. */
    int temperature_0p1c = 0;

    /** @brief Raw alarm word. 0 means no alarm. */
    int alarm = 0;

    /** @brief Output switch state. true means output is enabled. */
    bool output_enabled = false;

    /** @brief MCU-to-power-supply communication state. true means recent communication succeeded. */
    bool is_communicated = false;

    /**
     * @brief Last power communication error code.
     *
     * 0 means ok. For non-zero values, inspect last_error_name.
     */
    int last_error = 0;

    /**
     * @brief Text form of the last power communication error code.
     *
     * Common values: "ok", "bad_len", "busy", "timeout", "overflow",
     * "tx_fail", "crc", "slave", "function", "exception", "echo",
     * "byte_count".
     */
    std::string last_error_name;
};

/**
 * @brief UPS status.
 */
struct UpsStatus {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /** @brief UPS voltage in 0.01 V. */
    int volt_0p01v = 0;

    /** @brief UPS current in 0.01 A. */
    int curr_0p01a = 0;

    /** @brief UPS temperature using the current MCU UPS payload unit. */
    int temp_0p01c = 0;

    /** @brief Raw UPS status code. 255 usually means not communicating or not connected. */
    int status = 0;

    /** @brief Raw UPS output status code. */
    int output_status = 0;

    /** @brief Raw UPS software version value. */
    int software_version = 0;

    /** @brief Raw UPS hardware version value. */
    int hardware_version = 0;

    /** @brief UPS power-off request counter or flag, returned as reported by MCU. */
    int request_power_off = 0;

    /** @brief MCU-to-UPS communication state. true means communication succeeded. */
    bool is_communicated = false;
};

/**
 * @brief GXHT30 environment temperature/humidity status.
 */
struct EnvironmentStatus {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /** @brief Environment temperature in 0.01 C. Example: 3185 means 31.85 C. */
    int temperature_0p01c = 0;

    /** @brief Relative humidity in 0.01 %RH. Example: 5832 means 58.32 %RH. */
    int humidity_0p01rh = 0;

    /** @brief Raw 16-bit GXHT30 temperature ADC value. */
    int raw_temperature = 0;

    /** @brief Raw 16-bit GXHT30 humidity ADC value. */
    int raw_humidity = 0;

    /** @brief Detected 7-bit I2C address, usually 0x44. 0 means no address detected. */
    int address = 0;

    /** @brief MCU-to-GXHT30 communication state. true means the latest valid read succeeded. */
    bool is_communicated = false;

    /**
     * @brief Last GXHT30 module error code.
     *
     * 0=ok, 1=no_device, 2=tx_fail, 3=rx_fail, 4=temperature_crc,
     * 5=humidity_crc, 6=not_ready.
     */
    int last_error = 0;

    /** @brief Text form of last_error, for example "ok" or "no_device". */
    std::string last_error_name;

    /** @brief Last STM32 HAL I2C status code. 0 means HAL_OK. */
    int last_hal_status = 0;

    /** @brief Number of valid GXHT30 samples accepted by the MCU since boot. */
    int sample_count = 0;
};

/**
 * @brief State of one red/green LED group.
 */
struct LedGroupState {
    /** @brief true means the red channel output bit is high. */
    bool red = false;

    /** @brief true means the green channel output bit is high. */
    bool green = false;
};

/**
 * @brief TCA9554 LED expander status and decoded group state.
 */
struct LedStatus {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /**
     * @brief Current TCA9554 Output Port register value.
     *
     * Bit mapping:
     * - bit0: JC_R
     * - bit1: JC_G
     * - bit2: CD_R
     * - bit3: CD_G
     * - bit4: WZ_R
     * - bit5: WZ_G
     * - bit6: DP_R
     * - bit7: DP_G
     *
     * The hardware uses NPN low-side LED drivers, so a high bit turns the
     * corresponding LED channel on.
     */
    int mask = 0;

    /** @brief Current TCA9554 Input Port register value read back by the MCU. */
    int input = 0;

    /** @brief Current TCA9554 Polarity Inversion register value. Expected value: 0x00. */
    int polarity = 0;

    /**
     * @brief Current TCA9554 Configuration register value.
     *
     * Expected value is 0x00 because every P0..P7 pin is used as an output.
     * In this register, bit=1 means input and bit=0 means output.
     */
    int config = 0;

    /** @brief TCA9554 detected 7-bit I2C address. Expected value: 0x20. */
    int address = 0;

    /** @brief MCU-to-TCA9554 communication state. true means the latest register read/write succeeded. */
    bool is_communicated = false;

    /**
     * @brief Last TCA9554 LED module error code.
     *
     * 0=ok, 1=no_device, 2=write_fail, 3=read_fail, 4=invalid_param,
     * 5=not_ready.
     */
    int last_error = 0;

    /** @brief Text form of last_error, for example "ok" or "read_fail". */
    std::string last_error_name;

    /** @brief Last STM32 HAL I2C status code. 0 means HAL_OK. */
    int last_hal_status = 0;

    /** @brief Number of successful LED output write requests accepted by the MCU since boot. */
    int write_count = 0;

    /** @brief JC LED group state. */
    LedGroupState jc;

    /** @brief CD LED group state. */
    LedGroupState cd;

    /** @brief WZ LED group state. */
    LedGroupState wz;

    /** @brief DP LED group state. */
    LedGroupState dp;
};

/**
 * @brief Active-low PSW1/PSW2/PSW3/PSW4 switch input status.
 *
 * Hardware mapping:
 * - PSW1 / PD15: module reached microswitch.
 * - PSW2 / PD14: aircraft position microswitch.
 * - PSW3 / PD13: cover open/close push button.
 * - PSW4 / PD12: aircraft present microswitch.
 *
 * The switches short the input to GND. Therefore the semantic boolean fields
 * are true when the corresponding input is low.
 *
 * A cover-button request to close is accepted only when PSW2 and PSW4 are
 * both active at the trigger instant. The MCU does not continuously monitor
 * them after the close motion starts. PSW1 is status-only and is not part of
 * this condition.
 */
struct SwitchStatus {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /** @brief true when the TOP microswitch input PSW1/PD15 is active low. */
    bool top = false;

    /** @brief true when the BOT microswitch input PSW2/PD14 is active low. */
    bool bottom = false;

    /** @brief true when the cover open/close push button input PSW3/PD13 is active low. */
    bool cover_button = false;

    /** @brief true when the aircraft-position input PSW2/PD14 is active low. */
    bool aircraft_position_switch = false;

    /** @brief true when the module-reached input PSW1/PD15 is active low. */
    bool module_reached_switch = false;

    /** @brief true when the aircraft-present input PSW4/PD12 is active low. */
    bool aircraft_present_switch = false;

    /** @brief Compatibility field; same as aircraft_position_switch. */
    bool platform_switch = false;

    /** @brief Compatibility field; same as module_reached_switch. */
    bool charge_base_switch = false;

    /** @brief Same as top, named by board signal. */
    bool psw1 = false;

    /** @brief Same as bottom, named by board signal. */
    bool psw2 = false;

    /** @brief Same as cover_button, named by board signal. */
    bool psw3 = false;

    /** @brief Same as aircraft_present_switch, named by board signal. */
    bool psw4 = false;

    /** @brief Active mask, bit0=PSW1, bit1=PSW2, bit2=PSW3, bit3=PSW4. */
    int active_mask = 0;

    /**
     * @brief MCU manual cover action name.
     *
     * Values:
     * - "none": no manual button-triggered cover action is active.
     * - "manual_opening": MCU is opening the cover due to the physical button
     *   or emergency-stop release.
     * - "manual_closing": MCU is closing the cover due to the physical button.
     */
    std::string manual_action = "none";

    /** @brief Raw MCU manual action code. 0=none, 1=manual_opening, 2=manual_closing. */
    int manual_action_code = 0;

    /**
     * @brief Raw GPIO level mask, bit=1 means high level before active-low conversion.
     *
     * With the expected pull-up wiring and no switch pressed, bits 0..3 should
     * normally read as 1. A closed switch pulls its bit to 0.
     */
    int raw_level_mask = 0;

    /** @brief true because this hardware uses active-low switch inputs. */
    bool active_low = true;
};

/**
 * @brief HCNC4A plaintext Modbus RTU air-conditioner status.
 *
 * The MCU polls the air-conditioner through UART5 RS485 and caches the latest
 * values. This structure is read from the daemon cache; it does not directly
 * open or configure UART5 from customer code.
 */
struct AirConditionerStatus {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /** @brief Modbus slave address used by the MCU. Current firmware default: 0x01. */
    int address = 0;

    /** @brief true means at least one recent plaintext Modbus transaction succeeded. */
    bool is_communicated = false;

    /** @brief true means the MCU AC state machine is waiting for a reply or has a queued control command. */
    bool busy = false;

    /**
     * @brief Raw device work status register 0x1000.
     *
     * Protocol values: 1=standby, 2=running without fault, 3=fault.
     */
    int device_status = 0;

    /** @brief Text form of device_status, for example "standby", "running", or "fault". */
    std::string device_status_name;

    /** @brief Raw indoor fan status register 0x1002. */
    int indoor_fan_status = 0;

    /** @brief Raw outdoor fan status register 0x1004. */
    int outdoor_fan_status = 0;

    /** @brief Raw compressor status register 0x1006. */
    int compressor_status = 0;

    /** @brief Raw electric heater status register 0x1007. */
    int heater_status = 0;

    /** @brief Cabinet return-air temperature in 0.1 C. 0x7FFF means sensor fault per protocol. */
    int return_air_temp_0p1c = 0;

    /** @brief External ambient temperature in 0.1 C. 0x7FFF means sensor fault per protocol. */
    int external_temp_0p1c = 0;

    /** @brief Condenser temperature in 0.1 C. 0x7FFF means sensor fault per protocol. */
    int condenser_temp_0p1c = 0;

    /** @brief Evaporator temperature in 0.1 C. 0x7FFF means sensor fault per protocol. */
    int evaporator_temp_0p1c = 0;

    /** @brief Indoor fan speed in RPM. */
    int indoor_fan_rpm = 0;

    /** @brief Outdoor fan speed in RPM. */
    int outdoor_fan_rpm = 0;

    /** @brief DC input voltage in 0.1 V. Example: 540 means 54.0 V. */
    int dc_voltage_0p1v = 0;

    /** @brief DC running current in 0.1 A. Example: 12 means 1.2 A. */
    int dc_current_0p1a = 0;

    /** @brief Rated cooling capacity in W. */
    int cooling_capacity_w = 0;

    /**
     * @brief Packed alarm bit field generated by the MCU.
     *
     * bit0=high_temp, bit1=indoor_fan_fault, bit2=outdoor_fan_fault,
     * bit3=compressor_fault, bit4=return_air_sensor_fault,
     * bit5=high_pressure, bit6=low_temp, bit7=dc_overvoltage,
     * bit8=dc_undervoltage, bit9=evaporator_sensor_fault,
     * bit10=condenser_sensor_fault, bit11=ambient_sensor_fault,
     * bit12=evaporator_frost, bit13=frequent_high_pressure.
     */
    int alarms = 0;

    /** @brief Active alarm names decoded by the daemon from alarms. */
    std::vector<std::string> alarm_names;

    /** @brief Protocol version register 0x0102. */
    int protocol_version = 0;

    /** @brief Software version register 0x0104. */
    int software_version = 0;

    /** @brief Hardware version register 0x0106. */
    int hardware_version = 0;

    /** @brief Compressor cooling start temperature setting in 0.1 C. Example: 300 means 30.0 C. */
    int cool_start_temp_0p1c = 0;

    /** @brief Compressor cooling hysteresis setting in 0.1 C. Example: 30 means 3.0 C. */
    int cool_diff_0p1c = 0;

    /** @brief Heating start temperature setting in 0.1 C. Example: 50 means 5.0 C. */
    int heat_start_temp_0p1c = 0;

    /** @brief Heating hysteresis setting in 0.1 C. Example: 80 means 8.0 C. */
    int heat_diff_0p1c = 0;

    /** @brief Dehumidification setpoint in percent. Valid protocol range: 10..90. */
    int dehumid_setpoint_percent = 0;

    /** @brief Run mode readback. 0=normal, 1=silent. */
    int run_mode = 0;

    /** @brief Text form of run_mode, for example "normal" or "silent". */
    std::string run_mode_name;

    /** @brief Monitor humidity downlink value in percent, register 0x020B. */
    int monitor_humidity_percent = 0;

    /** @brief Last MCU AC communication error code. 0 means ok. */
    int last_error = 0;

    /** @brief Text form of last_error, for example "ok", "timeout", or "exception". */
    std::string last_error_name;

    /** @brief Last Modbus exception code. 0 means no exception has been recorded. */
    int last_exception = 0;

    /** @brief Last Modbus function code accepted by the MCU AC parser. */
    int last_function = 0;

    /**
     * @brief Last air-conditioner control action id.
     *
     * 1=power, 2=cool, 3=heat, 4=mode, 5=monitor humidity,
     * 6=cooling start temperature, 7=cooling hysteresis,
     * 8=heating start temperature, 9=heating hysteresis,
     * 10=dehumidification setpoint.
     */
    int last_control_action = 0;

    /** @brief Text form of last_control_action. */
    std::string last_control_action_name;

    /** @brief Last encoded control value written to the Modbus register. */
    int last_control_value = 0;

    /** @brief Last control completion result. 0=ok, 1=busy, other values match last_error codes. */
    int last_control_result = 0;

    /** @brief Text form of last_control_result. */
    std::string last_control_result_name;

    /** @brief Plaintext Modbus request count sent by the MCU since boot. */
    int tx_count = 0;

    /** @brief Valid plaintext Modbus response count accepted by the MCU since boot. */
    int rx_count = 0;

    /** @brief CRC error count after plaintext Modbus CRC validation. */
    int crc_error_count = 0;

    /** @brief Air-conditioner transaction timeout count. */
    int timeout_count = 0;
};

/**
 * @brief Return value for one HCNC4A air-conditioner control command.
 */
struct AirConditionerControlResult {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /** @brief Whether the MCU accepted the control request into its AC queue. */
    bool accepted = false;

    /** @brief Requested action. */
    AirConditionerAction action = AirConditionerAction::RemotePower;

    /** @brief Requested high-level value before MCU protocol encoding. */
    int requested_value = 0;

    /** @brief MCU/daemon result code. 0 means ok. */
    int result_code = -1;

    /** @brief Text form of result_code. */
    std::string result_name;

    /** @brief Latest AC status returned by the daemon after the request. */
    AirConditionerStatus status;
};

/**
 * @brief Return value for motion commands.
 */
struct MotionActionResult {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /**
     * @brief Whether the MCU accepted this motion command.
     *
     * true means the command was accepted by the MCU. If wait is true, also
     * check result.ok and wait_error to know whether waiting for completion
     * succeeded.
     */
    bool accepted = false;

    /**
     * @brief Whether the motor field is valid.
     *
     * This is usually true when wait is true and the daemon successfully read
     * motor status after the action completed or timed out.
     */
    bool has_motor = false;

    /** @brief Motor status read after completion or timeout. Valid when has_motor is true. */
    MotorData motor;

    /** @brief Target position accepted by the daemon, in 0.1 degree. */
    int target_position_0p1deg = 0;

    /** @brief Motion sequence id used by asynchronous motion events. Zero means not assigned. */
    int motion_id = 0;

    /**
     * @brief Error text from waiting for motion completion.
     *
     * Typical value: "motion timeout". Empty string means no wait error.
     */
    std::string wait_error;
};

/**
 * @brief RK-side asynchronous motion event type.
 */
enum class MotionEventType {
    /** @brief Event type was not recognized by this library version. */
    Unknown,

    /** @brief The daemon accepted a new motion target and started tracking it. */
    Started,

    /** @brief Position target reached or homing completed successfully. */
    Reached,

    /** @brief Motion or homing tracking timed out before successful completion. */
    Timeout,

    /** @brief Motion failed, for example because the motor driver reported stall. */
    Failed,

    /** @brief Motion was canceled by stop/release-stop or replaced by a newer target. */
    Canceled,
};

/**
 * @brief One asynchronous motion event generated by the RK3588 daemon.
 *
 * Position and homing events are generated from RK-side SocketCAN monitoring.
 * The customer application does not need to poll motor_status() itself.
 */
struct MotionEvent {
    /**
     * @brief Transport parse result for this event line.
     *
     * result.ok reports whether the event line was received and parsed. It is
     * not the mechanical result. Use motion_ok and event_type for the motion
     * outcome.
     */
    Result result;

    /** @brief Parsed event type enum. */
    MotionEventType event_type = MotionEventType::Unknown;

    /** @brief Raw daemon event type string, for example "motion_reached". */
    std::string type;

    /** @brief Monotonic event sequence inside the daemon. */
    int event_id = 0;

    /** @brief Motion sequence id assigned when a motion command is accepted. */
    int motion_id = 0;

    /** @brief Action name, for example "motor_trapezoid", "door_open", or "door_close". */
    std::string action;

    /** @brief Monitor source, for example "position" or "home". */
    std::string monitor;

    /** @brief Reason string, for example "position_within_tolerance" or "can_lost". */
    std::string reason;

    /** @brief true for Reached/Timeout/Failed/Canceled, false for Started. */
    bool final = false;

    /** @brief true when this event represents a successful motion outcome. */
    bool motion_ok = false;

    /** @brief Requested motor-side target position in 0.1 degree. */
    int target_position_0p1deg = 0;

    /** @brief Latest CAN-observed motor-side position in 0.1 degree. */
    int position_0p1deg = 0;

    /** @brief position_0p1deg - target_position_0p1deg, in 0.1 degree. */
    int error_0p1deg = 0;

    /** @brief RK-side arrival tolerance in 0.1 degree. */
    int tolerance_0p1deg = 0;

    /** @brief Requested speed in 0.1 RPM when known. */
    int speed_0p1rpm = 0;

    /** @brief Requested acceleration/deceleration in RPM/s when known. */
    int accel_rpm_s = 0;

    /** @brief Elapsed time from command acceptance to this event, in seconds. */
    double elapsed_s = 0.0;

    /** @brief Whether RK has received a recent motor CAN frame. */
    bool can_communicated = false;

    /** @brief Age of the latest motor CAN frame, in seconds. Negative means unknown. */
    double can_age_s = -1.0;

    /** @brief Raw motor status byte from CAN command 0x3A. -1 means unknown. */
    int can_status = -1;

    /** @brief Raw motor homing status byte from CAN command 0x3B. -1 means unknown. */
    int can_homing_status = -1;

    /** @brief Age of the latest motor homing CAN frame, in seconds. Negative means unknown. */
    double can_homing_age_s = -1.0;

    /** @brief Motor driver enable bit decoded from the raw CAN status byte. */
    bool can_enabled = false;

    /** @brief Motor driver reached bit decoded from the raw CAN status byte. */
    bool can_reached = false;

    /** @brief Motor driver stall/protection bit decoded from the raw CAN status byte. */
    bool can_stall = false;

    /** @brief Homing/calibration completed flag reported by MCU when monitor is "home". */
    bool calibed = false;

    /** @brief Homing/calibration in-progress flag reported by MCU when monitor is "home". */
    bool calibing = false;

    /** @brief Homing/calibration failure flag reported by MCU when monitor is "home". */
    bool calib_failed = false;

    /** @brief Newer motion id when event_type is Canceled because a target was replaced. */
    int new_motion_id = 0;
};

/**
 * @brief Callback invoked from DockClient's motion event background thread.
 *
 * The callback must return quickly. If the application needs heavy work, push
 * the event into its own queue and process it from another thread.
 */
using MotionEventCallback = std::function<void(const MotionEvent&)>;

/**
 * @brief Return value for low-level motor enable/disable command.
 */
struct MotorEnableResult {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /** @brief Requested target motor. */
    MotorTarget target = MotorTarget::Door;

    /** @brief Requested enable state. true means enable, false means disable. */
    bool requested_enabled = false;
};

/**
 * @brief Return value for power target-setting command.
 */
struct PowerSetResult {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /** @brief Requested voltage target in 0.01 V. */
    int requested_set_volt_0p01v = 0;

    /** @brief Requested current target in 0.01 A. */
    int requested_set_curr_0p01a = 0;
};

/**
 * @brief Return value for power output switch command.
 */
struct PowerOutputResult {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /** @brief Requested output switch target. true means output-enable was requested. */
    bool requested_output_enabled = false;
};

/**
 * @brief Return value for aircraft UART4 RS485 passthrough.
 */
struct AircraftTransferResult {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /**
     * @brief Aircraft passthrough business result code.
     *
     * 0=ok, 1=bad_len, 2=busy, 3=timeout, 4=overflow, 5=tx_fail.
     */
    int result_code = -1;

    /** @brief Text form of result_code, for example "ok" or "timeout". */
    std::string result_name;

    /** @brief Received raw bytes. Empty when no reply is received or a timeout occurs. */
    std::vector<uint8_t> rx;

    /** @brief Received raw bytes as a lowercase hex string, useful for logs. */
    std::string rx_hex;
};

/**
 * @brief Return value for passive aircraft UART4 RS485 receive.
 *
 * This API reads raw bytes that the MCU has already received from the aircraft
 * RS485 bus. It does not send any bytes. Customer protocol code is responsible
 * for splitting rx into application frames.
 */
struct AircraftReadResult {
    /** @brief Common call result. Check result.ok first. */
    Result result;

    /**
     * @brief Aircraft receive business result code.
     *
     * 0=ok, 1=bad_len, 2=busy, 3=timeout, 4=overflow, 5=tx_fail.
     * For this passive read API, timeout means no byte arrived before timeout_ms.
     */
    int result_code = -1;

    /** @brief Text form of result_code, for example "ok" or "timeout". */
    std::string result_name;

    /**
     * @brief Raw bytes read from the MCU aircraft receive buffer.
     *
     * This is a byte stream, not protocol frames. Customer code should keep its
     * own parser state across calls when the aircraft protocol has frame
     * headers, length fields, CRC, or delimiters.
     */
    std::vector<uint8_t> rx;

    /** @brief Received raw bytes as a lowercase hex string, useful for logs. */
    std::string rx_hex;

    /** @brief Number of oldest bytes dropped inside the MCU ring buffer since the previous read. */
    int dropped = 0;

    /** @brief Bytes still buffered inside the MCU after this read completed. */
    int remaining = 0;
};

/**
 * @brief C++ client for the interceptorctl daemon.
 *
 * DockClient only talks to the RK3588 local Unix domain socket. It never opens
 * /dev/mcu directly. All methods are synchronous request/reply calls.
 */
class DockClient {
public:
    /**
     * @brief Construct a client object.
     * @param socket_path daemon Unix socket path. Default: /tmp/interceptorctl.sock.
     * @param request_timeout_ms socket send/receive timeout for one request, in ms.
     */
    explicit DockClient(std::string socket_path = "/tmp/interceptorctl.sock", int request_timeout_ms = 30000);

    /** @brief Stop the motion event thread, if one is running. */
    ~DockClient();

    DockClient(const DockClient&) = delete;
    DockClient& operator=(const DockClient&) = delete;

    /** @brief Return the configured Unix socket path. */
    const std::string& socket_path() const { return socket_path_; }

    /** @brief Return the configured request timeout in ms. */
    int request_timeout_ms() const { return request_timeout_ms_; }

    /**
     * @brief Send one raw JSON command and return the daemon raw JSON reply.
     * @param cmd Command name, for example "power_status".
     * @param args_json JSON text for the args object, for example "{\"voltage\":2400,\"current\":1580}".
     * @return One JSON reply line without the trailing newline.
     *
     * @note This is a low-level escape hatch. Customer business code should
     * prefer the typed APIs below.
     * @note args_json must be a valid JSON object text. Default: "{}".
     * @throw std::runtime_error if socket creation, connect, send, receive, or
     * empty reply handling fails.
     */
    std::string request_json(const std::string& cmd, const std::string& args_json = "{}") const;

    /**
     * @brief Start a background subscription thread for RK-side motion events.
     * @param callback Function invoked for each motion event.
     *
     * The daemon produces position and homing events from RK SocketCAN
     * monitoring. The subscription itself does not poll /dev/mcu and does not
     * block normal API calls.
     * If a previous event thread is running, it is stopped before the new one
     * starts.
     *
     * @note Callback runs on the DockClient background thread. Keep it short.
     */
    void start_motion_event_thread(MotionEventCallback callback);

    /**
     * @brief Stop the background motion event subscription thread.
     */
    void stop_motion_event_thread();

    /**
     * @brief Return true when the background motion event thread is running.
     */
    bool motion_event_thread_running() const;

    /** @brief Check whether the daemon is reachable. */
    Result ping() const;

    /** @brief Read MCU firmware version. */
    VersionResult version() const;

    /** @brief Read hardware emergency-stop button status. */
    StopStatus stop_status() const;

    /** @brief Read linked single-axis motor status. */
    MotorStatus motor_status() const;

    /** @brief Read power status, including set values, output values, temperature, and error code. */
    PowerStatus power_status() const;

    /** @brief Read UPS status. */
    UpsStatus ups_status() const;

    /** @brief Read GXHT30 environment temperature/humidity status. */
    EnvironmentStatus environment_status() const;

    /** @brief Read TCA9554 LED expander output/input/config status. */
    LedStatus led_status() const;

    /** @brief Read PSW1/PSW2/PSW3/PSW4 active-low switch input status. */
    SwitchStatus switch_status() const;

    /**
     * @brief Write the raw TCA9554 LED output mask and read back expander status.
     * @param mask 8-bit output mask. Values outside 0..255 are truncated by the daemon.
     *
     * @note A high bit turns on the corresponding LED channel.
     */
    LedStatus led_set_mask(int mask) const;

    /**
     * @brief Set one LED group to off/red/green/both and read back expander status.
     * @param group LED group: JC, CD, WZ, DP, or All.
     * @param color Requested red/green state.
     *
     * @note Setting one group preserves the other groups by reading the current
     * mask first, modifying only the selected two bits, then writing the new mask.
     */
    LedStatus led_set_group(LedGroup group, LedColor color) const;

    /** @brief Read HCNC4A air-conditioner cached status from the MCU. */
    AirConditionerStatus air_conditioner_status() const;

    /**
     * @brief Low-level HCNC4A air-conditioner control command.
     * @param action Control action register group.
     * @param value High-level requested value. For RemotePower/ForceCool/
     * ForceHeat, use 1 for on and 0 for off. For RunMode, use 0=normal and
     * 1=silent. For Humidity, use 0..100. Temperature settings use 0.1 C.
     * @param wait true waits until the MCU AC state machine receives the
     * Modbus reply or times out.
     * @param timeout_s Maximum wait time when wait is true, in seconds.
     */
    AirConditionerControlResult air_conditioner_control(
        AirConditionerAction action,
        int value,
        bool wait = true,
        double timeout_s = 3.0
    ) const;

    /** @brief Request remote air-conditioner power on/off. */
    AirConditionerControlResult air_conditioner_power(bool on, bool wait = true, double timeout_s = 3.0) const;

    /** @brief Request force-cooling on/off. */
    AirConditionerControlResult air_conditioner_force_cool(bool on, bool wait = true, double timeout_s = 3.0) const;

    /** @brief Request force-heating on/off. */
    AirConditionerControlResult air_conditioner_force_heat(bool on, bool wait = true, double timeout_s = 3.0) const;

    /** @brief Request normal or silent run mode. false=normal, true=silent. */
    AirConditionerControlResult air_conditioner_silent_mode(bool silent, bool wait = true, double timeout_s = 3.0) const;

    /** @brief Write monitor humidity value, 0..100 percent. */
    AirConditionerControlResult air_conditioner_humidity(int percent, bool wait = true, double timeout_s = 3.0) const;

    /** @brief Set compressor cooling start temperature in 0.1 C. Valid range: 200..500. */
    AirConditionerControlResult air_conditioner_cool_start_temp(int temp_0p1c, bool wait = true, double timeout_s = 3.0) const;

    /** @brief Set compressor cooling hysteresis in 0.1 C. Valid range: 10..100. */
    AirConditionerControlResult air_conditioner_cool_diff(int diff_0p1c, bool wait = true, double timeout_s = 3.0) const;

    /** @brief Set heating start temperature in 0.1 C. Valid range: -400..250. */
    AirConditionerControlResult air_conditioner_heat_start_temp(int temp_0p1c, bool wait = true, double timeout_s = 3.0) const;

    /** @brief Set heating hysteresis in 0.1 C. Valid range: 50..150. */
    AirConditionerControlResult air_conditioner_heat_diff(int diff_0p1c, bool wait = true, double timeout_s = 3.0) const;

    /** @brief Set dehumidification target in percent. Valid range: 10..90. */
    AirConditionerControlResult air_conditioner_dehumid_setpoint(int percent, bool wait = true, double timeout_s = 3.0) const;

    /**
     * @brief Execute door-open action.
     * @param wait false returns after MCU accepts the command; true waits for completion or timeout.
     * @param timeout_s Motion timeout in seconds. The daemon uses this value
     * for asynchronous motion events even when wait is false.
     *
     * @note The default is non-blocking with respect to mechanical motion. This
     * lets another customer process issue door_close() immediately to update
     * the in-progress target.
     */
    MotionActionResult door_open(bool wait = false, double timeout_s = 20.0) const;

    /**
     * @brief Execute door-close action.
     * @param wait false returns after MCU accepts the command; true waits for completion or timeout.
     * @param timeout_s Motion timeout in seconds. The daemon uses this value
     * for asynchronous motion events even when wait is false.
     *
     * @note Sending door_close() while door_open() is still moving updates the
     * MCU motor target through the trapezoid update path.
     */
    MotionActionResult door_close(bool wait = false, double timeout_s = 20.0) const;

    /**
     * @brief Stop current motor motion through the MCU software stop path.
     *
     * Use this when an operator or customer application wants to interrupt an
     * in-progress door/motor motion. The daemon also emits a motion_canceled
     * event for the active motion.
     */
    Result motor_stop() const;

    /**
     * @brief Clear MCU software stop and allow later motor commands.
     */
    Result motor_release_stop() const;

    /**
     * @brief Start low-level motor homing/calibration.
     * @param target Target motor. Use "door", "motor", or "motor1".
     * @param wait false returns after MCU accepts the command; true waits for homing completion or timeout.
     * @param timeout_s Homing timeout in seconds. The daemon uses this value
     * for asynchronous homing events even when wait is false.
     *
     * @note Completion is reported through start_motion_event_thread() as
     * action="motor_home", monitor="home", reason="homing_done" on success.
     */
    MotionActionResult motor_home(
        const std::string& target,
        bool wait = false,
        double timeout_s = 60.0
    ) const;

    /**
     * @brief Start low-level motor homing/calibration.
     * @param target Door motor.
     * @param wait false returns after MCU accepts the command; true waits for homing completion or timeout.
     * @param timeout_s Homing timeout in seconds.
     */
    MotionActionResult motor_home(
        MotorTarget target,
        bool wait = false,
        double timeout_s = 60.0
    ) const;

    /**
     * @brief Stop current low-level motor homing/calibration.
     * @param target Door motor.
     *
     * @note This sends the motor driver's homing-stop command. It is not the
     * same as motor_stop(), which enters the MCU software stop path.
     */
    Result motor_home_stop(MotorTarget target) const;

    /**
     * @brief Low-level motor enable/disable command.
     * @param target Door motor.
     * @param enable true enables the motor driver; false disables it.
     *
     * @note This API sends a direct motor-driver enable command through the MCU.
     * It is intended for commissioning/debug workflows. Application-level door
     * actions normally enable the motor internally as needed.
     */
    MotorEnableResult motor_enable(MotorTarget target, bool enable) const;

    /**
     * @brief Low-level motor enable helper.
     * @param target Door motor.
     */
    MotorEnableResult motor_enable(MotorTarget target) const;

    /**
     * @brief Low-level motor disable helper.
     * @param target Door motor.
     */
    MotorEnableResult motor_disable(MotorTarget target) const;

    /**
     * @brief Low-level trapezoid motor move command, intended for debug/calibration.
     * @param target Target motor. Use "door", "motor", or "motor1".
     * @param position_0p1deg Motor-side absolute target position in 0.1 degree.
     * @param speed_0p1rpm Maximum speed in 0.1 RPM.
     * @param accel_rpm_s Acceleration and deceleration in RPM/s.
     * @param wait false returns after MCU accepts the command; true waits for completion or timeout.
     * @param timeout_s Motion timeout in seconds. The daemon uses this value
     * for asynchronous motion events even when wait is false.
     */
    MotionActionResult motor_trapezoid(
        const std::string& target,
        int position_0p1deg,
        int speed_0p1rpm,
        int accel_rpm_s,
        bool wait = false,
        double timeout_s = 20.0
    ) const;

    /**
     * @brief Low-level trapezoid motor move command, intended for debug/calibration.
     * @param target Door motor.
     * @param position_0p1deg Motor-side absolute target position in 0.1 degree.
     * @param speed_0p1rpm Maximum speed in 0.1 RPM.
     * @param accel_rpm_s Acceleration and deceleration in RPM/s.
     * @param wait false returns after MCU accepts the command; true waits for completion or timeout.
     * @param timeout_s Motion timeout in seconds. The daemon uses this value
     * for asynchronous motion events even when wait is false.
     */
    MotionActionResult motor_trapezoid(
        MotorTarget target,
        int position_0p1deg,
        int speed_0p1rpm,
        int accel_rpm_s,
        bool wait = false,
        double timeout_s = 20.0
    ) const;

    /**
     * @brief Set power supply voltage/current targets.
     * @param voltage_0p01v Voltage target in 0.01 V. Example: 2400 means 24.00 V.
     * @param current_0p01a Current target in 0.01 A. Example: 1580 means 15.80 A.
     *
     * @note This command only confirms that the set request was submitted.
     * Call power_status() afterwards to confirm the actual power state.
     */
    PowerSetResult power_set(int voltage_0p01v, int current_0p01a) const;

    /**
     * @brief Request power output enable.
     * @note Call power_status() afterwards to confirm the actual output state.
     */
    PowerOutputResult power_on() const;

    /**
     * @brief Request power output disable.
     * @note Call power_status() afterwards to confirm the actual output state.
     */
    PowerOutputResult power_off() const;

    /**
     * @brief Aircraft UART4 RS485 passthrough.
     * @param tx Raw bytes to send to the aircraft RS485 bus.
     * @param timeout_ms Maximum total frame wait time, in ms.
     * @param idle_ms A frame is considered complete after this many idle ms
     * after at least one byte is received.
     *
     * @note This method blocks until a reply is received or timeout expires.
     * With no external reply device, result_code is usually 3(timeout).
     */
    AircraftTransferResult aircraft_transfer(
        const std::vector<uint8_t>& tx,
        int timeout_ms = 1000,
        int idle_ms = 30
    ) const;

    /**
     * @brief Passive aircraft UART4 RS485 receive.
     * @param timeout_ms Wait time for at least one byte, in ms. 0 means return immediately.
     * @param max_len Maximum bytes to return, 1..220.
     *
     * @note This method does not send any bytes to the aircraft bus.
     * @note Returned bytes are a stream. Customer code is responsible for
     * protocol framing and CRC validation.
     */
    AircraftReadResult aircraft_read(int timeout_ms = 1000, int max_len = 220) const;

private:
    /** @brief daemon Unix socket path. */
    std::string socket_path_;

    /** @brief Socket send/receive timeout for one request, in ms. */
    int request_timeout_ms_;

    /** @brief Guards background motion event thread state. */
    mutable std::mutex motion_event_mutex_;

    /** @brief Background thread used by start_motion_event_thread(). */
    std::thread motion_event_thread_;

    /** @brief Set true to request event thread shutdown. */
    std::atomic<bool> motion_event_stop_{false};

    /** @brief true while the event thread is inside its subscription loop. */
    std::atomic<bool> motion_event_running_{false};

    /** @brief Subscription socket fd, or -1 when no subscription is active. */
    int motion_event_fd_ = -1;
};

/**
 * @brief Convert bytes to a contiguous lowercase hex string.
 * @param bytes Input byte array.
 * @return Hex string, for example {0x01, 0x02, 0x0d} -> "01020d".
 */
std::string bytes_to_hex(const std::vector<uint8_t>& bytes);

/**
 * @brief Convert a hex string to bytes.
 * @param hex Hex string. Spaces, commas, colons, underscores, and hyphens are ignored.
 * @return Parsed byte array.
 * @throw std::runtime_error if the hex digit count is odd or an invalid
 * character is found.
 */
std::vector<uint8_t> hex_to_bytes(const std::string& hex);

}  // namespace interceptorctl

#endif  // INTERCEPTORCTL_CPP_CLIENT_DOCK_CLIENT_HPP
