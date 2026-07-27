#include "dock_client.hpp"

/**
 * @file dock_client.cpp
 * @brief Linux Unix socket transport and JSON parsing implementation for DockClient.
 *
 * Design goals:
 * 1. Customer projects only need a C++17 compiler. No third-party JSON library
 *    is required.
 * 2. The public API exposes typed structures, while the internal transport still
 *    uses the one-line JSON protocol required by the interceptorctl daemon.
 * 3. Every API call opens one Unix domain socket connection and closes it after
 *    the request finishes.
 *
 * Notes:
 * - This file uses Linux/POSIX socket APIs and targets the RK3588 Linux runtime.
 * - The internal JSON parser only covers the JSON shapes returned by the daemon.
 *   It is not intended to be a general-purpose JSON library.
 */

#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <cctype>
#include <cstring>
#include <iomanip>
#include <map>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace interceptorctl {
namespace {

/**
 * @brief Convert one hexadecimal character to a 0..15 nibble.
 * @param ch Input character.
 * @retval 0..15 Conversion succeeded.
 * @retval -1 Invalid hexadecimal character.
 */
int hex_nibble(char ch) {
    if (ch >= '0' && ch <= '9') {
        return ch - '0';
    }
    if (ch >= 'a' && ch <= 'f') {
        return 10 + ch - 'a';
    }
    if (ch >= 'A' && ch <= 'F') {
        return 10 + ch - 'A';
    }
    return -1;
}

/**
 * @brief Internal lightweight JSON value object.
 *
 * This type is only used inside dock_client.cpp to parse daemon replies. The
 * public customer API does not expose Json, so customer code does not depend on
 * daemon JSON internals.
 */
class Json {
public:
    enum class Type { Null, Bool, Number, String, Object, Array };

    Type type = Type::Null;
    bool bool_value = false;
    double number_value = 0.0;
    std::string string_value;
    std::map<std::string, Json> object_value;
    std::vector<Json> array_value;

    static Json make_bool(bool value) {
        Json out;
        out.type = Type::Bool;
        out.bool_value = value;
        return out;
    }

    static Json make_number(double value) {
        Json out;
        out.type = Type::Number;
        out.number_value = value;
        return out;
    }

    static Json make_string(std::string value) {
        Json out;
        out.type = Type::String;
        out.string_value = std::move(value);
        return out;
    }

    static Json make_object(std::map<std::string, Json> value) {
        Json out;
        out.type = Type::Object;
        out.object_value = std::move(value);
        return out;
    }

    static Json make_array(std::vector<Json> value) {
        Json out;
        out.type = Type::Array;
        out.array_value = std::move(value);
        return out;
    }
};

/**
 * @brief Internal recursive-descent JSON parser.
 *
 * Supports object, array, string, number, bool, and null. The daemon replies are
 * intentionally simple, so this avoids a third-party dependency for customer
 * deployments.
 */
class JsonParser {
public:
    explicit JsonParser(const std::string& text) : text_(text) {}

    Json parse() {
        skip_ws();
        Json value = parse_value();
        skip_ws();
        if (pos_ != text_.size()) {
            throw std::runtime_error("unexpected trailing JSON data");
        }
        return value;
    }

private:
    const std::string& text_;
    size_t pos_ = 0;

    void skip_ws() {
        while (pos_ < text_.size() && std::isspace(static_cast<unsigned char>(text_[pos_]))) {
            ++pos_;
        }
    }

    char peek() const {
        if (pos_ >= text_.size()) {
            throw std::runtime_error("unexpected end of JSON");
        }
        return text_[pos_];
    }

    char take() {
        char ch = peek();
        ++pos_;
        return ch;
    }

    void expect(char ch) {
        if (take() != ch) {
            throw std::runtime_error("unexpected JSON character");
        }
    }

    bool consume_literal(const char* literal) {
        size_t n = std::strlen(literal);
        if (text_.compare(pos_, n, literal) != 0) {
            return false;
        }
        pos_ += n;
        return true;
    }

    Json parse_value() {
        skip_ws();
        char ch = peek();
        if (ch == '{') {
            return parse_object();
        }
        if (ch == '[') {
            return parse_array();
        }
        if (ch == '"') {
            return Json::make_string(parse_string());
        }
        if (ch == '-' || std::isdigit(static_cast<unsigned char>(ch))) {
            return parse_number();
        }
        if (consume_literal("true")) {
            return Json::make_bool(true);
        }
        if (consume_literal("false")) {
            return Json::make_bool(false);
        }
        if (consume_literal("null")) {
            return Json{};
        }
        throw std::runtime_error("invalid JSON value");
    }

    Json parse_object() {
        expect('{');
        skip_ws();
        std::map<std::string, Json> object;
        if (peek() == '}') {
            ++pos_;
            return Json::make_object(std::move(object));
        }
        while (true) {
            skip_ws();
            if (peek() != '"') {
                throw std::runtime_error("expected JSON object key");
            }
            std::string key = parse_string();
            skip_ws();
            expect(':');
            Json value = parse_value();
            object.emplace(std::move(key), std::move(value));
            skip_ws();
            char ch = take();
            if (ch == '}') {
                break;
            }
            if (ch != ',') {
                throw std::runtime_error("expected JSON object comma");
            }
        }
        return Json::make_object(std::move(object));
    }

    Json parse_array() {
        expect('[');
        skip_ws();
        std::vector<Json> array;
        if (peek() == ']') {
            ++pos_;
            return Json::make_array(std::move(array));
        }
        while (true) {
            array.push_back(parse_value());
            skip_ws();
            char ch = take();
            if (ch == ']') {
                break;
            }
            if (ch != ',') {
                throw std::runtime_error("expected JSON array comma");
            }
        }
        return Json::make_array(std::move(array));
    }

    static void append_utf8(std::string& out, unsigned codepoint) {
        if (codepoint <= 0x7F) {
            out.push_back(static_cast<char>(codepoint));
        } else if (codepoint <= 0x7FF) {
            out.push_back(static_cast<char>(0xC0 | (codepoint >> 6)));
            out.push_back(static_cast<char>(0x80 | (codepoint & 0x3F)));
        } else {
            out.push_back(static_cast<char>(0xE0 | (codepoint >> 12)));
            out.push_back(static_cast<char>(0x80 | ((codepoint >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (codepoint & 0x3F)));
        }
    }

    std::string parse_string() {
        expect('"');
        std::string out;
        while (true) {
            char ch = take();
            if (ch == '"') {
                break;
            }
            if (ch != '\\') {
                out.push_back(ch);
                continue;
            }

            char esc = take();
            switch (esc) {
            case '"':
            case '\\':
            case '/':
                out.push_back(esc);
                break;
            case 'b':
                out.push_back('\b');
                break;
            case 'f':
                out.push_back('\f');
                break;
            case 'n':
                out.push_back('\n');
                break;
            case 'r':
                out.push_back('\r');
                break;
            case 't':
                out.push_back('\t');
                break;
            case 'u': {
                unsigned codepoint = 0;
                for (int i = 0; i < 4; ++i) {
                    int digit = hex_nibble(take());
                    if (digit < 0) {
                        throw std::runtime_error("invalid JSON unicode escape");
                    }
                    codepoint = (codepoint << 4) | static_cast<unsigned>(digit);
                }
                append_utf8(out, codepoint);
                break;
            }
            default:
                throw std::runtime_error("invalid JSON string escape");
            }
        }
        return out;
    }

    Json parse_number() {
        size_t start = pos_;
        if (peek() == '-') {
            ++pos_;
        }
        while (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_]))) {
            ++pos_;
        }
        if (pos_ < text_.size() && text_[pos_] == '.') {
            ++pos_;
            while (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_]))) {
                ++pos_;
            }
        }
        if (pos_ < text_.size() && (text_[pos_] == 'e' || text_[pos_] == 'E')) {
            ++pos_;
            if (pos_ < text_.size() && (text_[pos_] == '+' || text_[pos_] == '-')) {
                ++pos_;
            }
            while (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_]))) {
                ++pos_;
            }
        }
        return Json::make_number(std::stod(text_.substr(start, pos_ - start)));
    }
};

const Json* member(const Json& object, const std::string& key) {
    if (object.type != Json::Type::Object) {
        return nullptr;
    }
    auto it = object.object_value.find(key);
    if (it == object.object_value.end()) {
        return nullptr;
    }
    return &it->second;
}

/**
 * @brief Read a string from a JSON field.
 * @param value JSON field pointer. nullptr is allowed.
 * @param fallback Default value when the field is missing or has the wrong type.
 */
std::string get_string(const Json* value, const std::string& fallback = "") {
    if (!value || value->type != Json::Type::String) {
        return fallback;
    }
    return value->string_value;
}

/**
 * @brief Read a bool from a JSON field.
 * @param value JSON field pointer. nullptr is allowed.
 * @param fallback Default value when the field is missing or has the wrong type.
 */
bool get_bool(const Json* value, bool fallback = false) {
    if (!value || value->type != Json::Type::Bool) {
        return fallback;
    }
    return value->bool_value;
}

/**
 * @brief Read an int from a JSON field.
 * @param value JSON field pointer. nullptr is allowed.
 * @param fallback Default value when the field is missing or has the wrong type.
 */
int get_int(const Json* value, int fallback = 0) {
    if (!value || value->type != Json::Type::Number) {
        return fallback;
    }
    return static_cast<int>(value->number_value);
}

double get_double(const Json* value, double fallback = 0.0) {
    if (!value || value->type != Json::Type::Number) {
        return fallback;
    }
    return value->number_value;
}

Json parse_json(const std::string& text) {
    return JsonParser(text).parse();
}

Result make_result(const Json& root, const std::string& raw_json) {
    Result result;
    result.raw_json = raw_json;
    result.ok = get_bool(member(root, "ok"), false);
    result.error = get_string(member(root, "error"));
    if (!result.ok && result.error.empty()) {
        result.error = get_string(member(root, "result_name"));
    }
    return result;
}

/**
 * @brief Escape a C++ string as a JSON string value.
 *
 * request_json() accepts cmd and args_json separately. This helper safely places
 * cmd into the generated JSON request line.
 */
std::string escape_json_string(const std::string& text) {
    std::ostringstream out;
    out << '"';
    for (unsigned char ch : text) {
        switch (ch) {
        case '"':
            out << "\\\"";
            break;
        case '\\':
            out << "\\\\";
            break;
        case '\b':
            out << "\\b";
            break;
        case '\f':
            out << "\\f";
            break;
        case '\n':
            out << "\\n";
            break;
        case '\r':
            out << "\\r";
            break;
        case '\t':
            out << "\\t";
            break;
        default:
            if (ch < 0x20) {
                out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(ch);
            } else {
                out << static_cast<char>(ch);
            }
            break;
        }
    }
    out << '"';
    return out.str();
}

/**
 * @brief Build one daemon request line.
 * @param cmd Command name.
 * @param args_json JSON object text for the args field.
 * @return Complete JSON line with a trailing '\n'.
 */
std::string build_request_line(const std::string& cmd, const std::string& args_json) {
    return std::string("{\"cmd\":") + escape_json_string(cmd) + ",\"args\":" + args_json + "}\n";
}

std::string bool_json(bool value) {
    return value ? "true" : "false";
}

std::string motor_target_name(MotorTarget target) {
    (void)target;
    return "door";
}

std::string led_group_name(LedGroup group) {
    switch (group) {
    case LedGroup::Jc:
        return "jc";
    case LedGroup::Cd:
        return "cd";
    case LedGroup::Wz:
        return "wz";
    case LedGroup::Dp:
        return "dp";
    case LedGroup::All:
        return "all";
    }
    return "all";
}

std::string led_color_name(LedColor color) {
    switch (color) {
    case LedColor::Off:
        return "off";
    case LedColor::Red:
        return "red";
    case LedColor::Green:
        return "green";
    case LedColor::Both:
        return "both";
    }
    return "off";
}

std::string ac_action_name(AirConditionerAction action) {
    switch (action) {
    case AirConditionerAction::RemotePower:
        return "remote_power";
    case AirConditionerAction::ForceCool:
        return "force_cool";
    case AirConditionerAction::ForceHeat:
        return "force_heat";
    case AirConditionerAction::RunMode:
        return "run_mode";
    case AirConditionerAction::Humidity:
        return "humidity";
    case AirConditionerAction::CoolStartTemp:
        return "cool_start_temp";
    case AirConditionerAction::CoolDiff:
        return "cool_diff";
    case AirConditionerAction::HeatStartTemp:
        return "heat_start_temp";
    case AirConditionerAction::HeatDiff:
        return "heat_diff";
    case AirConditionerAction::DehumidSetpoint:
        return "dehumid_setpoint";
    }
    return "remote_power";
}

void parse_axis_data(const Json& axis_json, AxisData& axis) {
    axis.state = get_string(member(axis_json, "state"));
    axis.position_0p1deg = get_int(member(axis_json, "position"));
    axis.communicated = get_bool(member(axis_json, "communicated"));
    axis.can_communicated = get_bool(member(axis_json, "can_communicated"));
    axis.can_position_0p1deg = get_int(member(axis_json, "can_position"));
    axis.can_age_s = get_double(member(axis_json, "can_age_s"), -1.0);
    axis.target_position_0p1deg = get_int(member(axis_json, "target_position"));
    axis.observed_reached = get_bool(member(axis_json, "observed_reached"));
    axis.enabled = get_bool(member(axis_json, "enabled"));
    axis.stall = get_bool(member(axis_json, "stall"));
    axis.reached = get_bool(member(axis_json, "reached"));
    axis.calibed = get_bool(member(axis_json, "calibed"));
    axis.calibing = get_bool(member(axis_json, "calibing"));
    axis.calib_failed = get_bool(member(axis_json, "calib_failed"));
    axis.final_reached = get_bool(member(axis_json, "final_reached"));
}

MotorData parse_motor_data(const Json& root) {
    MotorData data;
    const Json* motor = member(root, "motor");
    if (!motor) {
        return data;
    }
    data.active = get_string(member(*motor, "active"));

    const Json* axis = member(*motor, "axis");
    if (axis) {
        parse_axis_data(*axis, data.axis);
    }
    return data;
}

LedGroupState parse_led_group(const Json* group) {
    LedGroupState out;
    if (!group) {
        return out;
    }
    out.red = get_bool(member(*group, "red"));
    out.green = get_bool(member(*group, "green"));
    return out;
}

void parse_led_data(const Json& root, LedStatus& out) {
    const Json* led = member(root, "led");
    if (!led) {
        return;
    }
    out.mask = get_int(member(*led, "mask"));
    out.input = get_int(member(*led, "input"));
    out.polarity = get_int(member(*led, "polarity"));
    out.config = get_int(member(*led, "config"));
    out.address = get_int(member(*led, "address"));
    out.is_communicated = get_bool(member(*led, "is_communicated"));
    out.last_error = get_int(member(*led, "last_error"));
    out.last_error_name = get_string(member(*led, "last_error_name"));
    out.last_hal_status = get_int(member(*led, "last_hal_status"));
    out.write_count = get_int(member(*led, "write_count"));

    const Json* groups = member(*led, "groups");
    if (groups) {
        out.jc = parse_led_group(member(*groups, "jc"));
        out.cd = parse_led_group(member(*groups, "cd"));
        out.wz = parse_led_group(member(*groups, "wz"));
        out.dp = parse_led_group(member(*groups, "dp"));
    }
}

void parse_switch_data(const Json& root, SwitchStatus& out) {
    const Json* switches = member(root, "switches");
    if (!switches) {
        return;
    }
    out.top = get_bool(member(*switches, "top"));
    out.bottom = get_bool(member(*switches, "bottom"));
    out.cover_button = get_bool(member(*switches, "cover_button"));
    out.aircraft_position_switch = get_bool(member(*switches, "aircraft_position_switch"));
    out.module_reached_switch = get_bool(member(*switches, "module_reached_switch"));
    out.aircraft_present_switch = get_bool(member(*switches, "aircraft_present_switch"));
    out.platform_switch = get_bool(member(*switches, "platform_switch"));
    out.charge_base_switch = get_bool(member(*switches, "charge_base_switch"));
    out.psw1 = get_bool(member(*switches, "psw1"));
    out.psw2 = get_bool(member(*switches, "psw2"));
    out.psw3 = get_bool(member(*switches, "psw3"));
    out.psw4 = get_bool(member(*switches, "psw4"));
    out.active_mask = get_int(member(*switches, "active_mask"));
    out.manual_action = get_string(member(*switches, "manual_action_name"), "none");
    out.manual_action_code = get_int(member(*switches, "manual_action"));
    out.raw_level_mask = get_int(member(*switches, "raw_level_mask"));
    out.active_low = get_bool(member(*switches, "active_low"));
}

std::vector<std::string> parse_string_array(const Json* array) {
    std::vector<std::string> out;
    if (!array || array->type != Json::Type::Array) {
        return out;
    }
    for (const Json& item : array->array_value) {
        if (item.type == Json::Type::String) {
            out.push_back(item.string_value);
        }
    }
    return out;
}

void parse_ac_data(const Json& root, AirConditionerStatus& out) {
    const Json* ac = member(root, "ac");
    if (!ac) {
        return;
    }
    out.address = get_int(member(*ac, "address"));
    out.is_communicated = get_bool(member(*ac, "is_communicated"));
    out.busy = get_bool(member(*ac, "busy"));
    out.device_status = get_int(member(*ac, "device_status"));
    out.device_status_name = get_string(member(*ac, "device_status_name"));
    out.indoor_fan_status = get_int(member(*ac, "indoor_fan_status"));
    out.outdoor_fan_status = get_int(member(*ac, "outdoor_fan_status"));
    out.compressor_status = get_int(member(*ac, "compressor_status"));
    out.heater_status = get_int(member(*ac, "heater_status"));
    out.return_air_temp_0p1c = get_int(member(*ac, "return_air_temp"));
    out.external_temp_0p1c = get_int(member(*ac, "external_temp"));
    out.condenser_temp_0p1c = get_int(member(*ac, "condenser_temp"));
    out.evaporator_temp_0p1c = get_int(member(*ac, "evaporator_temp"));
    out.indoor_fan_rpm = get_int(member(*ac, "indoor_fan_rpm"));
    out.outdoor_fan_rpm = get_int(member(*ac, "outdoor_fan_rpm"));
    out.dc_voltage_0p1v = get_int(member(*ac, "dc_voltage"));
    out.dc_current_0p1a = get_int(member(*ac, "dc_current"));
    out.cooling_capacity_w = get_int(member(*ac, "cooling_capacity_w"));
    out.alarms = get_int(member(*ac, "alarms"));
    out.alarm_names = parse_string_array(member(*ac, "alarm_names"));
    out.protocol_version = get_int(member(*ac, "protocol_version"));
    out.software_version = get_int(member(*ac, "software_version"));
    out.hardware_version = get_int(member(*ac, "hardware_version"));
    out.cool_start_temp_0p1c = get_int(member(*ac, "cool_start_temp"));
    out.cool_diff_0p1c = get_int(member(*ac, "cool_diff"));
    out.heat_start_temp_0p1c = get_int(member(*ac, "heat_start_temp"));
    out.heat_diff_0p1c = get_int(member(*ac, "heat_diff"));
    out.dehumid_setpoint_percent = get_int(member(*ac, "dehumid_setpoint"));
    out.run_mode = get_int(member(*ac, "run_mode"));
    out.run_mode_name = get_string(member(*ac, "run_mode_name"));
    out.monitor_humidity_percent = get_int(member(*ac, "monitor_humidity"));
    out.last_error = get_int(member(*ac, "last_error"));
    out.last_error_name = get_string(member(*ac, "last_error_name"));
    out.last_exception = get_int(member(*ac, "last_exception"));
    out.last_function = get_int(member(*ac, "last_function"));
    out.last_control_action = get_int(member(*ac, "last_control_action"));
    out.last_control_action_name = get_string(member(*ac, "last_control_action_name"));
    out.last_control_value = get_int(member(*ac, "last_control_value"));
    out.last_control_result = get_int(member(*ac, "last_control_result"));
    out.last_control_result_name = get_string(member(*ac, "last_control_result_name"));
    out.tx_count = get_int(member(*ac, "tx_count"));
    out.rx_count = get_int(member(*ac, "rx_count"));
    out.crc_error_count = get_int(member(*ac, "crc_error_count"));
    out.timeout_count = get_int(member(*ac, "timeout_count"));
}

MotionEventType parse_motion_event_type(const std::string& type) {
    if (type == "motion_started") {
        return MotionEventType::Started;
    }
    if (type == "motion_reached") {
        return MotionEventType::Reached;
    }
    if (type == "motion_timeout") {
        return MotionEventType::Timeout;
    }
    if (type == "motion_failed") {
        return MotionEventType::Failed;
    }
    if (type == "motion_canceled") {
        return MotionEventType::Canceled;
    }
    return MotionEventType::Unknown;
}

MotionEvent parse_motion_event_line(const std::string& raw) {
    MotionEvent event;
    Json root = parse_json(raw);
    event.result = make_result(root, raw);
    event.type = get_string(member(root, "type"));
    event.event_type = parse_motion_event_type(event.type);
    event.event_id = get_int(member(root, "event_id"));
    event.motion_id = get_int(member(root, "motion_id"));
    event.action = get_string(member(root, "action"));
    event.monitor = get_string(member(root, "monitor"));
    event.reason = get_string(member(root, "reason"));
    event.final = get_bool(member(root, "final"));
    event.motion_ok = get_bool(member(root, "motion_ok"), get_bool(member(root, "ok")));
    event.target_position_0p1deg = get_int(member(root, "target_position"));
    event.position_0p1deg = get_int(member(root, "position"));
    event.error_0p1deg = get_int(member(root, "error"));
    event.tolerance_0p1deg = get_int(member(root, "tolerance"));
    event.speed_0p1rpm = get_int(member(root, "speed"));
    event.accel_rpm_s = get_int(member(root, "accel"));
    event.elapsed_s = get_double(member(root, "elapsed_s"));
    event.can_communicated = get_bool(member(root, "can_communicated"));
    event.can_age_s = get_double(member(root, "can_age_s"), -1.0);
    event.can_status = get_int(member(root, "can_status"), -1);
    event.can_homing_status = get_int(member(root, "can_homing_status"), -1);
    event.can_homing_age_s = get_double(member(root, "can_homing_age_s"), -1.0);
    event.can_enabled = get_bool(member(root, "can_enabled"));
    event.can_reached = get_bool(member(root, "can_reached"));
    event.can_stall = get_bool(member(root, "can_stall"));
    event.calibed = get_bool(member(root, "calibed"));
    event.calibing = get_bool(member(root, "calibing"));
    event.calib_failed = get_bool(member(root, "calib_failed"));
    event.new_motion_id = get_int(member(root, "new_motion_id"));
    return event;
}

/**
 * @brief Build the args JSON object for motion business actions.
 * @param wait Whether to wait for action completion.
 * @param timeout_s Maximum wait time in seconds.
 */
std::string action_args(bool wait, double timeout_s) {
    std::ostringstream out;
    out << "{\"wait\":" << bool_json(wait) << ",\"timeout\":" << timeout_s << "}";
    return out.str();
}

Result exception_result(const std::string& error) {
    Result result;
    result.ok = false;
    result.error = error;
    return result;
}

/**
 * @brief Execute one motion command and parse the common motion reply.
 *
 * door_open, door_close, and motor_trapezoid share the same reply structure,
 * so parsing is centralized here.
 */
MotionActionResult run_motion_command(const DockClient& dock, const std::string& cmd, const std::string& args) {
    MotionActionResult out;
    try {
        std::string raw = dock.request_json(cmd, args);
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        out.accepted = get_bool(member(root, "accepted"));
        out.target_position_0p1deg = get_int(member(root, "target_position"));
        out.motion_id = get_int(member(root, "motion_id"));
        out.wait_error = get_string(member(root, "wait_error"));
        if (member(root, "motor")) {
            out.has_motor = true;
            out.motor = parse_motor_data(root);
        }
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

}  // namespace

DockClient::DockClient(std::string socket_path, int request_timeout_ms)
    : socket_path_(std::move(socket_path)), request_timeout_ms_(request_timeout_ms) {}

DockClient::~DockClient() {
    stop_motion_event_thread();
}

std::string DockClient::request_json(const std::string& cmd, const std::string& args_json) const {
    /*
     * A new socket is created for each request because:
     * 1. The daemon protocol is one connection, one request, one reply.
     * 2. Customer code does not need persistent connection recovery logic.
     * 3. Command frequency is low enough that connection overhead is acceptable.
     */
    int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        throw std::runtime_error(std::string("socket failed: ") + std::strerror(errno));
    }

    // Set send/receive timeouts so customer threads cannot block forever.
    timeval tv{};
    tv.tv_sec = request_timeout_ms_ / 1000;
    tv.tv_usec = (request_timeout_ms_ % 1000) * 1000;
    ::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    ::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (socket_path_.size() >= sizeof(addr.sun_path)) {
        ::close(fd);
        throw std::runtime_error("Unix socket path too long");
    }
    std::strncpy(addr.sun_path, socket_path_.c_str(), sizeof(addr.sun_path) - 1);

    // Connect to interceptorctl daemon. This is local IPC, not TCP/IP.
    if (::connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        std::string err = std::strerror(errno);
        ::close(fd);
        throw std::runtime_error("connect failed: " + err);
    }

    std::string request = build_request_line(cmd, args_json);
    const char* p = request.data();
    size_t left = request.size();
    // send may write partial data, so loop until the full JSON line is sent.
    while (left > 0) {
        ssize_t n = ::send(fd, p, left, 0);
        if (n <= 0) {
            std::string err = std::strerror(errno);
            ::close(fd);
            throw std::runtime_error("send failed: " + err);
        }
        p += n;
        left -= static_cast<size_t>(n);
    }

    std::string response;
    char buf[4096];
    // Daemon replies end with '\n'. Stop after newline or peer close.
    while (response.find('\n') == std::string::npos) {
        ssize_t n = ::recv(fd, buf, sizeof(buf), 0);
        if (n < 0) {
            std::string err = std::strerror(errno);
            ::close(fd);
            throw std::runtime_error("recv failed: " + err);
        }
        if (n == 0) {
            break;
        }
        response.append(buf, static_cast<size_t>(n));
    }

    ::close(fd);
    if (response.empty()) {
        throw std::runtime_error("empty daemon response");
    }
    if (!response.empty() && response.back() == '\n') {
        response.pop_back();
    }
    return response;
}

void DockClient::start_motion_event_thread(MotionEventCallback callback) {
    stop_motion_event_thread();
    {
        std::lock_guard<std::mutex> lock(motion_event_mutex_);
        motion_event_stop_.store(false);
        motion_event_running_.store(true);
        motion_event_thread_ = std::thread([this, callback]() {
            auto emit = [&callback](const MotionEvent& event) {
                try {
                    callback(event);
                } catch (...) {
                    // Customer callbacks must not terminate the subscription thread.
                }
            };

            int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
            if (fd < 0) {
                MotionEvent event;
                event.result = exception_result(std::string("motion event socket failed: ") + std::strerror(errno));
                emit(event);
                motion_event_running_.store(false);
                return;
            }

            {
                std::lock_guard<std::mutex> lock(motion_event_mutex_);
                motion_event_fd_ = fd;
            }

            sockaddr_un addr{};
            addr.sun_family = AF_UNIX;
            if (socket_path_.size() >= sizeof(addr.sun_path)) {
                MotionEvent event;
                event.result = exception_result("Unix socket path too long");
                emit(event);
                ::close(fd);
                std::lock_guard<std::mutex> lock(motion_event_mutex_);
                motion_event_fd_ = -1;
                motion_event_running_.store(false);
                return;
            }
            std::strncpy(addr.sun_path, socket_path_.c_str(), sizeof(addr.sun_path) - 1);

            if (::connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
                MotionEvent event;
                event.result = exception_result(std::string("motion event connect failed: ") + std::strerror(errno));
                emit(event);
                ::close(fd);
                std::lock_guard<std::mutex> lock(motion_event_mutex_);
                motion_event_fd_ = -1;
                motion_event_running_.store(false);
                return;
            }

            std::string request = build_request_line("motion_events_subscribe", "{\"heartbeat_s\":1.0}");
            const char* p = request.data();
            size_t left = request.size();
            while (left > 0 && !motion_event_stop_.load()) {
                ssize_t n = ::send(fd, p, left, 0);
                if (n <= 0) {
                    MotionEvent event;
                    event.result = exception_result(std::string("motion event send failed: ") + std::strerror(errno));
                    emit(event);
                    ::close(fd);
                    std::lock_guard<std::mutex> lock(motion_event_mutex_);
                    motion_event_fd_ = -1;
                    motion_event_running_.store(false);
                    return;
                }
                p += n;
                left -= static_cast<size_t>(n);
            }

            std::string line;
            char ch = 0;
            while (!motion_event_stop_.load()) {
                ssize_t n = ::recv(fd, &ch, 1, 0);
                if (n < 0) {
                    if (motion_event_stop_.load()) {
                        break;
                    }
                    MotionEvent event;
                    event.result = exception_result(std::string("motion event recv failed: ") + std::strerror(errno));
                    emit(event);
                    break;
                }
                if (n == 0) {
                    if (!motion_event_stop_.load()) {
                        MotionEvent event;
                        event.result = exception_result("motion event connection closed");
                        emit(event);
                    }
                    break;
                }
                if (ch != '\n') {
                    line.push_back(ch);
                    continue;
                }
                if (line.empty()) {
                    continue;
                }

                try {
                    Json root = parse_json(line);
                    std::string type = get_string(member(root, "type"));
                    if (type == "heartbeat" || type == "subscribed") {
                        line.clear();
                        continue;
                    }
                    MotionEvent event = parse_motion_event_line(line);
                    emit(event);
                } catch (const std::exception& e) {
                    MotionEvent event;
                    event.result = exception_result(e.what());
                    emit(event);
                }
                line.clear();
            }

            ::close(fd);
            std::lock_guard<std::mutex> lock(motion_event_mutex_);
            if (motion_event_fd_ == fd) {
                motion_event_fd_ = -1;
            }
            motion_event_running_.store(false);
        });
    }
}

void DockClient::stop_motion_event_thread() {
    std::thread thread_to_join;
    int fd = -1;
    {
        std::lock_guard<std::mutex> lock(motion_event_mutex_);
        motion_event_stop_.store(true);
        fd = motion_event_fd_;
        motion_event_fd_ = -1;
        if (motion_event_thread_.joinable()) {
            thread_to_join = std::move(motion_event_thread_);
        }
    }
    if (fd >= 0) {
        ::shutdown(fd, SHUT_RDWR);
    }
    if (thread_to_join.joinable()) {
        thread_to_join.join();
    }
}

bool DockClient::motion_event_thread_running() const {
    return motion_event_running_.load() && !motion_event_stop_.load();
}

Result DockClient::ping() const {
    try {
        std::string raw = request_json("ping");
        Json root = parse_json(raw);
        return make_result(root, raw);
    } catch (const std::exception& e) {
        return exception_result(e.what());
    }
}

VersionResult DockClient::version() const {
    VersionResult out;
    try {
        std::string raw = request_json("version");
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        out.version = get_string(member(root, "version"));
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

StopStatus DockClient::stop_status() const {
    StopStatus out;
    try {
        std::string raw = request_json("stop_status");
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        out.hardware_stop = get_bool(member(root, "hardware_stop"));
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

MotorStatus DockClient::motor_status() const {
    MotorStatus out;
    try {
        std::string raw = request_json("motor_status");
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        if (out.result.ok && member(root, "motor")) {
            out.motor = parse_motor_data(root);
        }
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

PowerStatus DockClient::power_status() const {
    PowerStatus out;
    try {
        std::string raw = request_json("power_status");
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        const Json* power = member(root, "power");
        if (out.result.ok && power) {
            out.set_volt_0p01v = get_int(member(*power, "set_volt"));
            out.set_curr_0p01a = get_int(member(*power, "set_curr"));
            out.output_volt_0p01v = get_int(member(*power, "output_volt"));
            out.output_curr_0p01a = get_int(member(*power, "output_curr"));
            out.temperature_0p1c = get_int(member(*power, "temperature"));
            out.alarm = get_int(member(*power, "alarm"));
            out.output_enabled = get_bool(member(*power, "output_enabled"));
            out.is_communicated = get_bool(member(*power, "is_communicated"));
            out.last_error = get_int(member(*power, "last_error"));
            out.last_error_name = get_string(member(*power, "last_error_name"));
        }
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

UpsStatus DockClient::ups_status() const {
    UpsStatus out;
    try {
        std::string raw = request_json("ups_status");
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        const Json* ups = member(root, "ups");
        if (out.result.ok && ups) {
            out.volt_0p01v = get_int(member(*ups, "volt"));
            out.curr_0p01a = get_int(member(*ups, "curr"));
            out.temp_0p01c = get_int(member(*ups, "temp"));
            out.status = get_int(member(*ups, "status"));
            out.output_status = get_int(member(*ups, "output_status"));
            out.software_version = get_int(member(*ups, "software_version"));
            out.hardware_version = get_int(member(*ups, "hardware_version"));
            out.request_power_off = get_int(member(*ups, "request_power_off"));
            out.is_communicated = get_bool(member(*ups, "is_communicated"));
        }
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

EnvironmentStatus DockClient::environment_status() const {
    EnvironmentStatus out;
    try {
        std::string raw = request_json("env_status");
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        const Json* env = member(root, "environment");
        if (out.result.ok && env) {
            out.temperature_0p01c = get_int(member(*env, "temperature"));
            out.humidity_0p01rh = get_int(member(*env, "humidity"));
            out.raw_temperature = get_int(member(*env, "raw_temperature"));
            out.raw_humidity = get_int(member(*env, "raw_humidity"));
            out.address = get_int(member(*env, "address"));
            out.is_communicated = get_bool(member(*env, "is_communicated"));
            out.last_error = get_int(member(*env, "last_error"));
            out.last_error_name = get_string(member(*env, "last_error_name"));
            out.last_hal_status = get_int(member(*env, "last_hal_status"));
            out.sample_count = get_int(member(*env, "sample_count"));
        }
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

LedStatus DockClient::led_status() const {
    LedStatus out;
    try {
        std::string raw = request_json("led_status");
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        if (out.result.ok) {
            parse_led_data(root, out);
        }
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

LedStatus DockClient::led_set_mask(int mask) const {
    LedStatus out;
    try {
        std::ostringstream args;
        args << "{\"mask\":" << (mask & 0xFF) << "}";
        std::string raw = request_json("led_set", args.str());
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        if (member(root, "led")) {
            parse_led_data(root, out);
        }
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

LedStatus DockClient::led_set_group(LedGroup group, LedColor color) const {
    LedStatus out;
    try {
        std::ostringstream args;
        args << "{\"group\":" << escape_json_string(led_group_name(group))
             << ",\"color\":" << escape_json_string(led_color_name(color))
             << "}";
        std::string raw = request_json("led_set", args.str());
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        if (member(root, "led")) {
            parse_led_data(root, out);
        }
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

SwitchStatus DockClient::switch_status() const {
    SwitchStatus out;
    try {
        std::string raw = request_json("switch_status");
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        if (out.result.ok) {
            parse_switch_data(root, out);
        }
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

AirConditionerStatus DockClient::air_conditioner_status() const {
    AirConditionerStatus out;
    try {
        std::string raw = request_json("ac_status");
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        if (out.result.ok) {
            parse_ac_data(root, out);
        }
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

AirConditionerControlResult DockClient::air_conditioner_control(
    AirConditionerAction action,
    int value,
    bool wait,
    double timeout_s
) const {
    AirConditionerControlResult out;
    out.action = action;
    out.requested_value = value;
    try {
        std::ostringstream args;
        args << "{\"action\":" << escape_json_string(ac_action_name(action))
             << ",\"value\":" << value
             << ",\"wait\":" << bool_json(wait)
             << ",\"timeout\":" << timeout_s
             << "}";
        std::string raw = request_json("ac_control", args.str());
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        out.accepted = get_bool(member(root, "accepted"));
        out.result_code = get_int(member(root, "result"), -1);
        out.result_name = get_string(member(root, "result_name"));
        out.status.result = out.result;
        parse_ac_data(root, out.status);
        const Json* requested = member(root, "requested");
        if (requested) {
            out.requested_value = get_int(member(*requested, "value"), value);
        }
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
        out.status.result = out.result;
    }
    return out;
}

AirConditionerControlResult DockClient::air_conditioner_power(bool on, bool wait, double timeout_s) const {
    return air_conditioner_control(AirConditionerAction::RemotePower, on ? 1 : 0, wait, timeout_s);
}

AirConditionerControlResult DockClient::air_conditioner_force_cool(bool on, bool wait, double timeout_s) const {
    return air_conditioner_control(AirConditionerAction::ForceCool, on ? 1 : 0, wait, timeout_s);
}

AirConditionerControlResult DockClient::air_conditioner_force_heat(bool on, bool wait, double timeout_s) const {
    return air_conditioner_control(AirConditionerAction::ForceHeat, on ? 1 : 0, wait, timeout_s);
}

AirConditionerControlResult DockClient::air_conditioner_silent_mode(bool silent, bool wait, double timeout_s) const {
    return air_conditioner_control(AirConditionerAction::RunMode, silent ? 1 : 0, wait, timeout_s);
}

AirConditionerControlResult DockClient::air_conditioner_humidity(int percent, bool wait, double timeout_s) const {
    return air_conditioner_control(AirConditionerAction::Humidity, percent, wait, timeout_s);
}

AirConditionerControlResult DockClient::air_conditioner_cool_start_temp(int temp_0p1c, bool wait, double timeout_s) const {
    return air_conditioner_control(AirConditionerAction::CoolStartTemp, temp_0p1c, wait, timeout_s);
}

AirConditionerControlResult DockClient::air_conditioner_cool_diff(int diff_0p1c, bool wait, double timeout_s) const {
    return air_conditioner_control(AirConditionerAction::CoolDiff, diff_0p1c, wait, timeout_s);
}

AirConditionerControlResult DockClient::air_conditioner_heat_start_temp(int temp_0p1c, bool wait, double timeout_s) const {
    return air_conditioner_control(AirConditionerAction::HeatStartTemp, temp_0p1c, wait, timeout_s);
}

AirConditionerControlResult DockClient::air_conditioner_heat_diff(int diff_0p1c, bool wait, double timeout_s) const {
    return air_conditioner_control(AirConditionerAction::HeatDiff, diff_0p1c, wait, timeout_s);
}

AirConditionerControlResult DockClient::air_conditioner_dehumid_setpoint(int percent, bool wait, double timeout_s) const {
    return air_conditioner_control(AirConditionerAction::DehumidSetpoint, percent, wait, timeout_s);
}

MotionActionResult DockClient::door_open(bool wait, double timeout_s) const {
    return run_motion_command(*this, "door_open", action_args(wait, timeout_s));
}

MotionActionResult DockClient::door_close(bool wait, double timeout_s) const {
    return run_motion_command(*this, "door_close", action_args(wait, timeout_s));
}

Result DockClient::motor_stop() const {
    try {
        std::string raw = request_json("motor_stop");
        Json root = parse_json(raw);
        return make_result(root, raw);
    } catch (const std::exception& e) {
        return exception_result(e.what());
    }
}

Result DockClient::motor_release_stop() const {
    try {
        std::string raw = request_json("motor_release_stop");
        Json root = parse_json(raw);
        return make_result(root, raw);
    } catch (const std::exception& e) {
        return exception_result(e.what());
    }
}

MotionActionResult DockClient::motor_home(const std::string& target, bool wait, double timeout_s) const {
    std::ostringstream args;
    args << "{\"target\":" << escape_json_string(target)
         << ",\"wait\":" << bool_json(wait)
         << ",\"timeout\":" << timeout_s
         << "}";
    return run_motion_command(*this, "motor_home", args.str());
}

MotionActionResult DockClient::motor_home(MotorTarget target, bool wait, double timeout_s) const {
    return motor_home(motor_target_name(target), wait, timeout_s);
}

Result DockClient::motor_home_stop(MotorTarget target) const {
    try {
        std::ostringstream args;
        args << "{\"target\":" << escape_json_string(motor_target_name(target)) << "}";
        std::string raw = request_json("motor_home_stop", args.str());
        Json root = parse_json(raw);
        return make_result(root, raw);
    } catch (const std::exception& e) {
        return exception_result(e.what());
    }
}

MotorEnableResult DockClient::motor_enable(MotorTarget target, bool enable) const {
    MotorEnableResult out;
    out.target = target;
    out.requested_enabled = enable;
    try {
        std::ostringstream args;
        args << "{\"target\":" << escape_json_string(motor_target_name(target))
             << ",\"enabled\":" << bool_json(enable)
             << "}";
        std::string raw = request_json("motor_enable", args.str());
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        const Json* requested = member(root, "requested");
        if (out.result.ok && requested) {
            out.requested_enabled = get_bool(member(*requested, "enabled"), enable);
        }
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

MotorEnableResult DockClient::motor_enable(MotorTarget target) const {
    return motor_enable(target, true);
}

MotorEnableResult DockClient::motor_disable(MotorTarget target) const {
    return motor_enable(target, false);
}

MotionActionResult DockClient::motor_trapezoid(
    const std::string& target,
    int position_0p1deg,
    int speed_0p1rpm,
    int accel_rpm_s,
    bool wait,
    double timeout_s
) const {
    std::ostringstream args;
    args << "{\"target\":" << escape_json_string(target)
         << ",\"position\":" << position_0p1deg
         << ",\"speed\":" << speed_0p1rpm
         << ",\"accel\":" << accel_rpm_s
         << ",\"wait\":" << bool_json(wait)
         << ",\"timeout\":" << timeout_s
         << "}";
    return run_motion_command(*this, "motor_trapezoid", args.str());
}

MotionActionResult DockClient::motor_trapezoid(
    MotorTarget target,
    int position_0p1deg,
    int speed_0p1rpm,
    int accel_rpm_s,
    bool wait,
    double timeout_s
) const {
    return motor_trapezoid(motor_target_name(target), position_0p1deg, speed_0p1rpm, accel_rpm_s, wait, timeout_s);
}

PowerSetResult DockClient::power_set(int voltage_0p01v, int current_0p01a) const {
    PowerSetResult out;
    try {
        std::ostringstream args;
        args << "{\"voltage\":" << voltage_0p01v << ",\"current\":" << current_0p01a << "}";
        std::string raw = request_json("power_set", args.str());
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        const Json* requested = member(root, "requested");
        if (out.result.ok && requested) {
            out.requested_set_volt_0p01v = get_int(member(*requested, "set_volt"));
            out.requested_set_curr_0p01a = get_int(member(*requested, "set_curr"));
        }
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

PowerOutputResult DockClient::power_on() const {
    PowerOutputResult out;
    try {
        std::string raw = request_json("power_on");
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        const Json* requested = member(root, "requested");
        if (out.result.ok && requested) {
            out.requested_output_enabled = get_bool(member(*requested, "output_enabled"));
        }
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

PowerOutputResult DockClient::power_off() const {
    PowerOutputResult out;
    try {
        std::string raw = request_json("power_off");
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        const Json* requested = member(root, "requested");
        if (out.result.ok && requested) {
            out.requested_output_enabled = get_bool(member(*requested, "output_enabled"));
        }
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

AircraftTransferResult DockClient::aircraft_transfer(
    const std::vector<uint8_t>& tx,
    int timeout_ms,
    int idle_ms
) const {
    AircraftTransferResult out;
    try {
        std::ostringstream args;
        args << "{\"tx_hex\":" << escape_json_string(bytes_to_hex(tx))
             << ",\"timeout_ms\":" << timeout_ms
             << ",\"idle_ms\":" << idle_ms
             << "}";
        std::string raw = request_json("aircraft_transfer", args.str());
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        out.result_code = get_int(member(root, "result"), -1);
        out.result_name = get_string(member(root, "result_name"));
        out.rx_hex = get_string(member(root, "rx_hex"));
        out.rx = hex_to_bytes(out.rx_hex);
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

AircraftReadResult DockClient::aircraft_read(int timeout_ms, int max_len) const {
    AircraftReadResult out;
    try {
        std::ostringstream args;
        args << "{\"timeout_ms\":" << timeout_ms
             << ",\"max_len\":" << max_len
             << "}";
        std::string raw = request_json("aircraft_read", args.str());
        Json root = parse_json(raw);
        out.result = make_result(root, raw);
        out.result_code = get_int(member(root, "result"), -1);
        out.result_name = get_string(member(root, "result_name"));
        out.rx_hex = get_string(member(root, "rx_hex"));
        out.rx = hex_to_bytes(out.rx_hex);
        out.dropped = get_int(member(root, "dropped"));
        out.remaining = get_int(member(root, "remaining"));
    } catch (const std::exception& e) {
        out.result = exception_result(e.what());
    }
    return out;
}

std::string bytes_to_hex(const std::vector<uint8_t>& bytes) {
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (uint8_t byte : bytes) {
        out << std::setw(2) << static_cast<int>(byte);
    }
    return out.str();
}

std::vector<uint8_t> hex_to_bytes(const std::string& hex) {
    std::string normalized;
    for (unsigned char ch : hex) {
        if (std::isspace(ch) || ch == ',' || ch == ':' || ch == '_' || ch == '-') {
            continue;
        }
        normalized.push_back(static_cast<char>(ch));
    }
    if (normalized.empty()) {
        return {};
    }
    if (normalized.size() % 2 != 0) {
        throw std::runtime_error("hex string length must be even");
    }

    std::vector<uint8_t> out;
    out.reserve(normalized.size() / 2);
    for (size_t i = 0; i < normalized.size(); i += 2) {
        int hi = hex_nibble(normalized[i]);
        int lo = hex_nibble(normalized[i + 1]);
        if (hi < 0 || lo < 0) {
            throw std::runtime_error("invalid hex string");
        }
        out.push_back(static_cast<uint8_t>((hi << 4) | lo));
    }
    return out;
}

}  // namespace interceptorctl
