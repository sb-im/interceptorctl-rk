# interceptorctl C++ 客户接入指南

本文面向客户侧 C++ 程序。客户程序只访问 RK3588 上的
`interceptorctl daemon`，不直接打开 `/dev/mcu`，不处理 STM32 Package
二进制帧。

当前版本：

- MCU 固件版本：`0x002C`
- RK3588 `interceptorctl` 版本：`20260701-1`
- Unix socket：`/tmp/interceptorctl.sock`

## 1. 通信方式

客户程序和 daemon 是两个 Linux 进程：

```text
客户 C++ 进程
  -> /tmp/interceptorctl.sock
  -> interceptorctl daemon 进程
```

`/tmp/interceptorctl.sock` 是 Unix domain socket，是 Linux 内核提供的本机
进程间通信 IPC，不走 TCP/UDP/IP，也不走网卡。

协议很简单：

```text
客户发送一行 JSON，以 \n 结尾
daemon 返回一行 JSON，以 \n 结尾
```

请求固定格式：

```json
{"cmd":"motor_status","args":{}}
```

字段含义：

- `cmd`：命令名，字符串。
- `args`：参数对象，没有参数时传 `{}`。

回复里保留一个统一字段：

- `ok`：本次请求是否成功。客户程序必须先判断它。

`ok == true` 表示本次命令被 daemon 正确处理，并且业务层结果成功。

`ok == false` 时，通常会有：

```json
{"ok":false,"error":"timeout"}
```

此时客户程序应停止继续动作，并记录整条 JSON。

## 2. C++ 最小示例

保存为 `dock_client_example.cpp`：

```cpp
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>

static const char* kSocketPath = "/tmp/interceptorctl.sock";

std::string dock_request(const std::string& json_line) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        throw std::runtime_error(std::string("socket failed: ") + std::strerror(errno));
    }

    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    std::strncpy(addr.sun_path, kSocketPath, sizeof(addr.sun_path) - 1);

    if (connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        std::string err = std::strerror(errno);
        close(fd);
        throw std::runtime_error("connect failed: " + err);
    }

    std::string request = json_line;
    if (request.empty() || request.back() != '\n') {
        request.push_back('\n');
    }

    const char* p = request.data();
    size_t left = request.size();
    while (left > 0) {
        ssize_t n = write(fd, p, left);
        if (n <= 0) {
            std::string err = std::strerror(errno);
            close(fd);
            throw std::runtime_error("write failed: " + err);
        }
        p += n;
        left -= static_cast<size_t>(n);
    }

    std::string response;
    char buf[4096];
    while (response.find('\n') == std::string::npos) {
        ssize_t n = read(fd, buf, sizeof(buf));
        if (n < 0) {
            std::string err = std::strerror(errno);
            close(fd);
            throw std::runtime_error("read failed: " + err);
        }
        if (n == 0) {
            break;
        }
        response.append(buf, static_cast<size_t>(n));
    }

    close(fd);
    return response;
}

int main() {
    try {
        std::cout << dock_request(R"({"cmd":"version","args":{}})");
        std::cout << dock_request(R"({"cmd":"stop_status","args":{}})");
        std::cout << dock_request(R"({"cmd":"motor_status","args":{}})");
        std::cout << dock_request(R"({"cmd":"power_status","args":{}})");
        std::cout << dock_request(R"({"cmd":"env_status","args":{}})");
        std::cout << dock_request(R"({"cmd":"led_status","args":{}})");
        std::cout << dock_request(R"({"cmd":"ac_status","args":{}})");
    } catch (const std::exception& e) {
        std::cerr << e.what() << std::endl;
        return 1;
    }
    return 0;
}
```

编译：

```bash
g++ -std=c++17 dock_client_example.cpp -o dock_client_example
```

运行前确认 daemon 在线：

```bash
systemctl status interceptorctl.service
ls -l /tmp/interceptorctl.sock
```

运行：

```bash
./dock_client_example
```

生产项目建议用 `nlohmann/json`、RapidJSON 或 cJSON 解析回复。不要靠字符串查找
字段。

## 3. 客户 API 指令

### 3.1 读取类 API

| cmd | args | 说明 |
| --- | --- | --- |
| `version` | `{}` | 读取 MCU 固件版本。 |
| `stop_status` | `{}` | 读取实体急停按钮状态。 |
| `motor_status` | `{}` | 读取门和平台电机状态。 |
| `power_status` | `{}` | 读取电源状态，返回值包含温度。 |
| `ups_status` | `{}` | 读取 UPS 状态。 |
| `env_status` | `{}` | 读取 GXHT30 环境温湿度状态。 |
| `led_status` | `{}` | 读取 TCA9554 LED 扩展器状态。 |
| `ac_status` | `{}` | 读取 HCNC4A 空调状态、温度、直流输入、告警和通信错误码。 |

### 3.2 控制类 API

| cmd | args | 说明 |
| --- | --- | --- |
| `door_open` | `{"wait":false,"timeout":20}` | 单电机联动门打开。默认只等 MCU ack，不等机械动作完成。 |
| `door_close` | `{"wait":false,"timeout":20}` | 单电机联动门关闭。运动中再次下发会更新梯形目标。 |
| `power_set` | `{"voltage":2400,"current":100}` | 设置电源目标值，单位 0.01V / 0.01A。 |
| `power_on` | `{}` | 打开电源输出。 |
| `power_off` | `{}` | 关闭电源输出。 |
| `led_set` | `{"group":"wz","color":"red"}` 或 `{"mask":16}` | 设置 TCA9554 LED 输出。 |
| `switch_status` | `{}` | 读取 PSW1/PSW2/PSW3/PSW4 active-low 输入，并返回 MCU 手动开/关盖状态。 |
| `ac_control` | `{"action":"remote_power","value":1,"wait":true,"timeout":3}` | HCNC4A 空调控制。`action` 支持 `remote_power`、`force_cool`、`force_heat`、`run_mode`、`humidity`、`cool_start_temp`、`cool_diff`、`heat_start_temp`、`heat_diff`、`dehumid_setpoint`。 |
| `aircraft_read` | `{"timeout_ms":500,"max_len":80}` | 被动读取飞机 UART4 485 主动上报原始字节。 |
| `aircraft_transfer` | `{"tx_hex":"0102030d","timeout_ms":1000,"idle_ms":30}` | 飞机 UART4 485 请求-响应透传。 |

## 4. 底层测试指令

底层测试指令供调试、标定、售后使用，不建议客户业务流程长期依赖。

### 4.1 底层读取/调试

| cmd | args | 说明 |
| --- | --- | --- |
| `status` | `{}` | 一次读取 motor + power 的聚合状态。客户建议改用 `motor_status` 和 `power_status`。 |
| `power_raw_transfer` | `{"tx_hex":"0103001c0001e40d","timeout_ms":1000,"idle_ms":20}` | 电源 USART3 raw 调试。 |

### 4.2 底层控制/调试

| cmd | args | 说明 |
| --- | --- | --- |
| `motor_enable` | `{"target":"door","enabled":true}` | 单电机底层使能或失能，调试和标定用。 |
| `motor_home` | `{"target":"door","wait":false,"timeout":60}` | 单电机底层回零/校准，完成结果通过 `action="motor_home"`、`monitor="home"` 的运动事件通知。 |
| `motor_home_stop` | `{"target":"door"}` | 停止当前底层回零/校准流程，不等同于 `motor_stop`。 |
| `motor_trapezoid` | `{"target":"door","position":181900,"speed":3000,"accel":100,"wait":false,"timeout":20}` | 单电机绝对位置梯形运动，标定用。运动中再次下发会更新目标。 |
| `motor_stop` | `{}` | MCU 软件电机停止，调试用。 |
| `motor_release_stop` | `{}` | 清除 MCU 软件电机停止，调试用。 |

## 5. 基础命令

### version

请求：

```json
{"cmd":"version","args":{}}
```

回复：

```json
{"ok":true,"version":"0x002C"}
```

字段说明：

- `ok`：请求成功。
- `version`：MCU 固件版本。

旧版调试字段 `cmd_set`、`cmd_id`、`data_hex`、`result` 不再暴露给客户默认 API。

### stop_status

请求：

```json
{"cmd":"stop_status","args":{}}
```

回复：

```json
{"ok":true,"hardware_stop":false}
```

字段说明：

- `hardware_stop == true`：实体急停按钮按下。
- `hardware_stop == false`：实体急停按钮未按下。

客户 App 层应根据 `hardware_stop` 设计自己的安全逻辑。当前系统约定急停按下后禁止电机动作，其它功能不必全部禁用。

## 6. 电机状态

### motor_status

请求：

```json
{"cmd":"motor_status","args":{}}
```

回复：

```json
{
  "ok": true,
  "motor": {
    "active": "idle",
    "axis": {
      "state": "unknown",
      "position": 2092769,
      "enabled": false,
      "stall": false,
      "reached": false,
      "calibed": false,
      "calibing": false,
      "calib_failed": false,
      "final_reached": false,
      "can_position": 2092769,
      "can_communicated": true,
      "target_position": 100,
      "observed_reached": false
    }
  }
}
```

字段说明：

| 字段 | 单位/类型 | 说明 |
| --- | --- | --- |
| `motor.active` | string | 当前动作。常见值：`idle`、`door_open`、`door_close`、`door_move`。 |
| `motor.axis.state` | string | 单电机联动轴状态。常见值：`unknown`、`moving`、`open`、`closed`、`error`。 |
| `motor.axis.position` | 0.1deg | 单电机当前绝对位置。`2092769` 表示 `209276.9deg` 电机侧角度。 |
| `motor.axis.enabled` | bool | 电机驱动器使能状态。`true` 表示当前驱动器处于使能。 |
| `motor.axis.stall` | bool | 电机驱动器堵转/保护标志。 |
| `motor.axis.reached` | bool | 驱动器原始到位标志。 |
| `motor.axis.calibed` | bool | MCU 侧标定完成标志。 |
| `motor.axis.calibing` | bool | MCU 侧标定进行中标志。 |
| `motor.axis.calib_failed` | bool | MCU 侧标定失败标志。 |
| `motor.axis.final_reached` | bool | MCU 运动监督后的最终到位标志。 |
| `motor.axis.can_position` | 0.1deg | RK 侧只监听 CAN 得到的电机位置。 |
| `motor.axis.observed_reached` | bool | RK 侧按目标位置 ±20 判断的到位估计，MCU 仍负责真正到位失能。 |

注意：`position` 是电机侧绝对角度，不是门板开合角度或平台直线位移。

## 7. 门和平台动作

请求示例：

```json
{"cmd":"door_open","args":{"wait":false,"timeout":20}}
```

```json
{"cmd":"door_close","args":{"wait":false,"timeout":20}}
```

参数说明：

- `wait`：是否由 daemon 等待动作结束。
- `timeout`：最大等待时间，单位秒。

`wait:false` 是默认推荐值，daemon 只等待 MCU ack；`wait:true` 时，daemon 会轮询 `motor_status`，直到 `motor.active == "idle"` 或超时。

成功回复：

```json
{
  "ok": true,
  "accepted": true,
  "target_position": 100,
  "motor": {
    "active": "idle",
    "axis": {
      "state": "open",
      "position": 2092769,
      "enabled": true,
      "stall": false,
      "reached": true,
      "calibed": false,
      "calibing": false,
      "calib_failed": false,
      "final_reached": true,
      "can_position": 2092769,
      "can_communicated": true,
      "target_position": 100,
      "observed_reached": false
    }
  }
}
```

只接受命令但不等待时：

```json
{"ok":true,"accepted":true,"target_position":100}
```

等待超时：

```json
{
  "ok": false,
  "accepted": true,
  "error": "motion timeout",
  "wait_error": "motion timeout",
  "motor": {
    "active": "door_open",
    "axis": {
      "state": "moving",
      "position": 2080000
    }
  }
}
```

客户判断建议：

1. 动作前读 `stop_status`，确认 `hardware_stop == false`。
2. 发送动作命令。
3. 判断 `ok == true`。
4. 如果显式使用 `wait:true`，确认返回的 `motor.active == "idle"`。

## 8. 低层电机控制

### motor_enable

`motor_enable` 是底层电机驱动器使能/失能接口，主要用于标定、调试、售后。
正式业务动作通常直接使用门和平台动作接口，MCU 会按动作流程控制电机。

请求：

```json
{
  "cmd": "motor_enable",
  "args": {
    "target": "door",
    "enabled": true
  }
}
```

参数说明：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `target` | string | `door`、`motor` 或 `motor1` 表示当前单电机联动轴。 |
| `enabled` | bool | `true` 使能驱动器，`false` 失能驱动器。 |

成功回复：

```json
{
  "ok": true,
  "requested": {
    "target": "door",
    "enabled": true
  }
}
```

使能命令只表示 MCU 已把目标发送给电机驱动器。需要确认实际状态时，随后读取
`motor_status` 并检查对应轴的 `enabled` 字段。

### motor_trapezoid

`motor_trapezoid` 是标定和测试接口。正式业务优先用 `door_open`、`door_close`。

请求：

```json
{
  "cmd": "motor_trapezoid",
  "args": {
    "target": "door",
    "position": 181900,
    "speed": 3000,
    "accel": 100,
    "wait": false,
    "timeout": 20
  }
}
```

参数说明：

| 参数 | 类型 | 单位 | 说明 |
| --- | --- | --- | --- |
| `target` | string | - | `door`、`motor` 或 `motor1` 表示当前单电机联动轴。 |
| `position` | int32 | 0.1deg | 电机侧绝对目标位置。`181900` 表示 `18190.0deg`。 |
| `speed` | uint16 | 0.1RPM | 最大转速。`3000` 表示 `300.0RPM`。 |
| `accel` | uint16 | RPM/s | 加减速度。`100` 表示 `100RPM/s`。同一个值用于加速和减速。 |
| `wait` | bool | - | 是否等待运动结束。默认建议为 `false`，只等待 MCU ack。 |
| `timeout` | number | 秒 | 等待运动结束的最大时间。 |

C++ 换算示例：

```cpp
int position_raw = static_cast<int>(position_deg * 10.0); // deg -> 0.1deg
int speed_raw = static_cast<int>(rpm * 10.0);             // RPM -> 0.1RPM
int accel_raw = static_cast<int>(rpm_per_s);              // RPM/s
```

成功回复格式和门/平台业务动作一致：

```json
{
  "ok": true,
  "accepted": true,
  "motor": {
    "active": "idle",
    "axis": {
      "state": "unknown",
      "position": 181900
    }
  }
}
```

## 9. 电源命令

### power_status

请求：

```json
{"cmd":"power_status","args":{}}
```

回复：

```json
{
  "ok": true,
  "power": {
    "set_volt": 2400,
    "set_curr": 100,
    "output_volt": 2,
    "output_curr": 0,
    "temperature": 306,
    "alarm": 0,
    "output_enabled": false,
    "is_communicated": true,
    "last_error": 0,
    "last_error_name": "ok"
  }
}
```

字段说明：

| 字段 | 单位/类型 | 说明 |
| --- | --- | --- |
| `set_volt` | 0.01V | 电源设定电压。`2400` 表示 `24.00V`。 |
| `set_curr` | 0.01A | 电源设定电流。`100` 表示 `1.00A`。 |
| `output_volt` | 0.01V | 电源实测输出电压。`2` 表示 `0.02V`。 |
| `output_curr` | 0.01A | 电源实测输出电流。 |
| `temperature` | 0.1C | 电源温度。`306` 表示 `30.6C`。 |
| `alarm` | raw/bit | 电源报警字。`0` 表示无报警。 |
| `output_enabled` | bool | 电源输出开关状态。 |
| `is_communicated` | bool | MCU 和电源通信是否正常。 |
| `last_error` | code | 最近一次电源错误码。 |
| `last_error_name` | string | 错误码解析。 |

电源错误码：

| code | name | 说明 |
| --- | --- | --- |
| `0` | `ok` | 正常。 |
| `1` | `bad_len` | 帧长度错误。 |
| `2` | `busy` | 电源通信忙。 |
| `3` | `timeout` | 电源未回复。 |
| `4` | `overflow` | 接收溢出。 |
| `5` | `tx_fail` | 串口发送失败。 |
| `6` | `crc` | CRC 校验错误。 |
| `7` | `slave` | 从站地址错误。 |
| `8` | `function` | 功能码错误。 |
| `9` | `exception` | Modbus 异常回复。 |
| `10` | `echo` | 写寄存器回显不匹配。 |
| `11` | `byte_count` | 返回字节数不匹配。 |

### power_set

请求：

```json
{"cmd":"power_set","args":{"voltage":2400,"current":100}}
```

参数说明：

- `voltage`：0.01V。`2400` 表示 `24.00V`。
- `current`：0.01A。`100` 表示 `1.00A`。

回复：

```json
{
  "ok": true,
  "requested": {
    "set_volt": 2400,
    "set_curr": 100
  }
}
```

说明：`requested` 表示本次提交给 MCU 电源状态机的目标值。设置后建议再读
`power_status` 确认最终状态。

### power_on / power_off

请求：

```json
{"cmd":"power_on","args":{}}
```

```json
{"cmd":"power_off","args":{}}
```

回复：

```json
{
  "ok": true,
  "requested": {
    "output_enabled": true
  }
}
```

说明：

- `power_on` 返回 `requested.output_enabled == true`。
- `power_off` 返回 `requested.output_enabled == false`。
- 判断实际输出电压和开关状态时，应随后读 `power_status`。

## 10. UPS 状态

请求：

```json
{"cmd":"ups_status","args":{}}
```

回复：

```json
{
  "ok": true,
  "ups": {
    "volt": 2400,
    "curr": 0,
    "temp": 306,
    "status": 1,
    "output_status": 1,
    "software_version": 1,
    "hardware_version": 1,
    "request_power_off": 0,
    "is_communicated": true
  }
}
```

字段说明：

- `volt`：0.01V。
- `curr`：0.01A。
- `temp`：0.01C，沿用 UPS 模块原始单位。
- `status`：UPS 原始状态字节。
- `output_status`：UPS 输出状态字节。
- `software_version` / `hardware_version`：UPS 版本。
- `request_power_off`：UPS 请求关机计数。
- `is_communicated`：UPS 通信是否正常。

## 11. 环境温湿度

### env_status

`env_status` 读取 MCU 缓存的 GXHT30 温湿度状态。MCU 使用 I2C1 低频轮询
GXHT30，当前轮询档位约为 0.5 mps，适合界面显示和日志记录，不建议作为强实时
控制条件。

请求：

```json
{"cmd":"env_status","args":{}}
```

回复：

```json
{
  "ok": true,
  "environment": {
    "temperature": 3185,
    "humidity": 5832,
    "raw_temperature": 28779,
    "raw_humidity": 38222,
    "address": 68,
    "is_communicated": true,
    "last_error": 0,
    "last_error_name": "ok",
    "last_hal_status": 0,
    "sample_count": 10
  }
}
```

字段说明：

| 字段 | 单位/类型 | 说明 |
| --- | --- | --- |
| `temperature` | 0.01C | 环境温度。`3185` 表示 `31.85C`。 |
| `humidity` | 0.01%RH | 相对湿度。`5832` 表示 `58.32%RH`。 |
| `raw_temperature` | raw | GXHT30 温度 16 bit 原始值。 |
| `raw_humidity` | raw | GXHT30 湿度 16 bit 原始值。 |
| `address` | 7-bit I2C | 传感器地址。`68` 即 `0x44`。 |
| `is_communicated` | bool | 最近一次有效采样是否成功。 |
| `last_error` | code | 最近错误码。`0` 表示正常。 |
| `last_error_name` | string | 错误码文本。 |
| `last_hal_status` | code | STM32 HAL I2C 状态码。`0` 表示 `HAL_OK`。 |
| `sample_count` | count | MCU 启动后接受的有效样本数。 |

错误码：

| last_error | last_error_name | 说明 |
| --- | --- | --- |
| `0` | `ok` | 最近一次采样成功。 |
| `1` | `no_device` | 未探测到 `0x44/0x45` 设备。 |
| `2` | `tx_fail` | I2C 写命令失败。 |
| `3` | `rx_fail` | I2C 读数据失败。 |
| `4` | `temperature_crc` | 温度 CRC 校验失败。 |
| `5` | `humidity_crc` | 湿度 CRC 校验失败。 |
| `6` | `not_ready` | 周期模式下暂时没有新数据。 |

## 12. LED 扩展器

### led_status

`led_status` 读取 I2C1 上的 TCA9554 LED 扩展器。硬件 A0/A1/A2 接地，所以
7-bit I2C 地址为 `0x20`。P0..P7 全部配置为输出。由于外部 LED 驱动使用
S8050 低边开关，输出 bit 为 `1` 表示对应 LED 通道点亮。

请求：
```json
{"cmd":"led_status","args":{}}
```

回复：
```json
{
  "ok": true,
  "led": {
    "mask": 16,
    "input": 16,
    "polarity": 0,
    "config": 0,
    "address": 32,
    "is_communicated": true,
    "last_error": 0,
    "last_error_name": "ok",
    "last_hal_status": 0,
    "write_count": 1,
    "groups": {
      "jc": {"red": false, "green": false},
      "cd": {"red": false, "green": false},
      "wz": {"red": true, "green": false},
      "dp": {"red": false, "green": false}
    }
  }
}
```

位定义：

| bit | 信号 | 说明 |
| --- | --- | --- |
| `0` | `JC_R` | JC 红色通道。 |
| `1` | `JC_G` | JC 绿色通道。 |
| `2` | `CD_R` | CD 红色通道。 |
| `3` | `CD_G` | CD 绿色通道。 |
| `4` | `WZ_R` | WZ 红色通道。 |
| `5` | `WZ_G` | WZ 绿色通道。 |
| `6` | `DP_R` | DP 红色通道。 |
| `7` | `DP_G` | DP 绿色通道。 |

字段说明：

| 字段 | 类型/单位 | 说明 |
| --- | --- | --- |
| `mask` | uint8 | TCA9554 Output Port 寄存器读回值。 |
| `input` | uint8 | TCA9554 Input Port 寄存器读回值，可用于辅助确认实际引脚电平。 |
| `polarity` | uint8 | Polarity Inversion 寄存器，当前应为 `0x00`。 |
| `config` | uint8 | Configuration 寄存器，当前应为 `0x00`，表示 P0..P7 均为输出。 |
| `address` | 7-bit I2C | `32` 即 `0x20`。 |
| `is_communicated` | bool | MCU 最近一次访问 TCA9554 是否成功。 |
| `last_error` | code | LED 模块最近错误码。 |
| `last_error_name` | string | 错误码文本。 |
| `last_hal_status` | code | STM32 HAL I2C 状态码，`0` 表示 `HAL_OK`。 |
| `write_count` | count | MCU 启动后成功写 LED 输出寄存器的次数。 |
| `groups` | object | 按 `jc/cd/wz/dp` 解码后的红绿通道布尔值。 |

错误码：

| last_error | last_error_name | 说明 |
| --- | --- | --- |
| `0` | `ok` | 最近一次访问成功。 |
| `1` | `no_device` | 未检测到 `0x20` 设备或初始化时无 ACK。 |
| `2` | `write_fail` | I2C 写寄存器失败。 |
| `3` | `read_fail` | I2C 读寄存器失败。 |
| `4` | `invalid_param` | MCU 侧参数错误，当前 JSON 层会提前限制参数。 |
| `5` | `not_ready` | 模块刚初始化，尚无有效状态。 |

### led_set

按组设置：
```json
{"cmd":"led_set","args":{"group":"wz","color":"red"}}
```

直接写 mask：
```json
{"cmd":"led_set","args":{"mask":16}}
```

参数说明：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `group` | string | `jc`、`cd`、`wz`、`dp` 或 `all`。 |
| `color` | string | `off`、`red`、`green`、`both`。`yellow` 作为 `both` 的兼容别名。 |
| `mask` | uint8 | 原始 8-bit 输出掩码。使用 `mask` 时会直接覆盖全部 LED 输出。 |

成功回复：
```json
{
  "ok": true,
  "result": 0,
  "result_name": "ok",
  "requested": {
    "group": "wz",
    "color": "red",
    "mask": 16
  },
  "led": {
    "mask": 16,
    "input": 16,
    "config": 0,
    "address": 32,
    "is_communicated": true,
    "last_error": 0,
    "last_error_name": "ok"
  }
}
```

`led_set` 会在 MCU 写 TCA9554 Output Port 后立即读回寄存器，所以返回的
`led.mask` 是本次闭环读回值。按组设置会先读取当前 mask，再只修改目标组
两位；直接 `mask` 设置会覆盖全部 8 位。

## 13. 开关输入

### switch_status

`switch_status` 读取 PSW1/PSW2/PSW3/PSW4 四路 active-low GPIO 输入，并返回 MCU 侧按钮触发的手动开/关盖状态。

硬件映射：
- `PSW1` / `PD15`：`module_reached_switch`，兼容字段为 `top`
- `PSW2` / `PD14`：`aircraft_position_switch`，兼容字段为 `bottom`
- `PSW3` / `PD13`：`cover_button`
- `PSW4` / `PD12`：`aircraft_present_switch`

按钮触发手动关盖时，MCU 只在触发瞬间检查 `aircraft_position_switch`
（PSW2）与 `aircraft_present_switch`（PSW4）是否同时为 `true`。条件满足后
运动继续执行，即使任一开关随后释放也不会因此停止。`module_reached_switch`
（PSW1）只用于状态上报，不参与该关盖条件。RK 下发的 `door_close` API 不受
此按钮触发条件限制。

请求：
```json
{"cmd":"switch_status","args":{}}
```

典型回复：
```json
{
  "ok": true,
  "switches": {
    "top": false,
    "bottom": false,
    "cover_button": false,
    "aircraft_position_switch": false,
    "module_reached_switch": false,
    "aircraft_present_switch": false,
    "platform_switch": false,
    "charge_base_switch": false,
    "psw1": false,
    "psw2": false,
    "psw3": false,
    "psw4": false,
    "active_mask": 0,
    "raw_level_mask": 7,
    "manual_action": 0,
    "manual_action_name": "none",
    "active_low": true
  }
}
```

`active_mask`：bit0=PSW1，bit1=PSW2，bit2=PSW3，bit3=PSW4，bit=1 表示 active。`raw_level_mask` 是未做 active-low 转换的原始 GPIO 电平，bit=1 表示高电平。`manual_action_name` 取值为 `none`、`manual_opening`、`manual_closing`。

## 14. 空调

### ac_status

`ac_status` 读取 MCU UART5 轮询缓存的 HCNC4A 空调数据。MCU 侧每次只执行一条
明文 Modbus RTU 事务，避免阻塞其它状态机。厂商原始 PDF 写有 ChaCha20 加密层，
但现场设备实测不启用加密，必须直接发送 `ADDR + FUNC + DATA + CRC16` 明文帧。

请求：
```json
{"cmd":"ac_status","args":{}}
```

典型回复：
```json
{
  "ok": true,
  "ac": {
    "is_communicated": true,
    "busy": false,
    "device_status": 2,
    "device_status_name": "running",
    "return_air_temp": 312,
    "external_temp": 298,
    "dc_voltage": 540,
    "dc_current": 12,
    "cool_start_temp": 300,
    "cool_diff": 30,
    "heat_start_temp": 50,
    "heat_diff": 80,
    "dehumid_setpoint": 60,
    "run_mode": 0,
    "run_mode_name": "normal",
    "monitor_humidity": 55,
    "alarms": 0,
    "alarm_names": [],
    "last_error": 0,
    "last_error_name": "ok"
  }
}
```

单位：
- `return_air_temp` / `external_temp` / `condenser_temp` / `evaporator_temp`：0.1C。
- `dc_voltage` / `dc_current`：0.1V / 0.1A。
- `indoor_fan_rpm` / `outdoor_fan_rpm`：rpm。
- `cooling_capacity_w`：W。
- `cool_start_temp` / `cool_diff` / `heat_start_temp` / `heat_diff`：0.1C，均为 MCU 从空调寄存器读回的实际配置值。
- `dehumid_setpoint`：除湿设定点，单位 %。
- `run_mode`：运行模式，`0` 正常，`1` 静音；`run_mode_name` 为文本解释。
- `monitor_humidity`：监控湿度下发寄存器读回值，单位 %，不是除湿设定点。

### ac_control

请求：
```json
{"cmd":"ac_control","args":{"action":"remote_power","value":1,"wait":true,"timeout":3}}
```

`action` 支持：
- `remote_power`：远程开关空调，`value=1` 开，`value=0` 关。
- `force_cool`：强制制冷，`value=1` 开，`value=0` 关。
- `force_heat`：强制加热，`value=1` 开，`value=0` 关。
- `run_mode`：运行模式，`value=0` 正常，`value=1` 静音。
- `humidity`：监控湿度下发，`value=0..100`。
- `cool_start_temp`：压缩机制冷启动温度，`value` 单位 0.1C，范围 `200..500`。
- `cool_diff`：压缩机制冷回差，`value` 单位 0.1C，范围 `10..100`。
- `heat_start_temp`：加热点，`value` 单位 0.1C，范围 `-400..250`。
- `heat_diff`：加热回差，`value` 单位 0.1C，范围 `50..150`。
- `dehumid_setpoint`：除湿设定点，`value=10..90`，单位 %。

默认 `wait=true` 时，daemon 会等待 MCU 空调状态机发出该控制帧并收到回复或超时。
控制后仍建议再次调用 `ac_status` 确认实际状态和参数回读。例如设置制冷启动温度 30.0C、回差 3.0C：

```json
{"cmd":"ac_control","args":{"action":"cool_start_temp","value":300,"wait":true,"timeout":3}}
{"cmd":"ac_control","args":{"action":"cool_diff","value":30,"wait":true,"timeout":3}}
{"cmd":"ac_status","args":{}}
```

## 15. 飞机 485

### aircraft_read

`aircraft_read` 用于读取飞机主动上报数据。MCU UART4 中断会持续把收到的
485 字节放进环形缓冲区；本接口只取原始字节流，不做帧头、帧尾、长度或 CRC
解析。客户应在自己的协议层维护解析状态并自行分帧。

请求：

```json
{
  "cmd": "aircraft_read",
  "args": {
    "timeout_ms": 500,
    "max_len": 80
  }
}
```

参数说明：

- `timeout_ms`：等待至少 1 个字节的时间，单位 ms。`0` 表示立即返回。
- `max_len`：本次最多返回多少字节，范围 `1..220`。

成功回复：

```json
{
  "ok": true,
  "result": 0,
  "result_name": "ok",
  "rx_len": 16,
  "rx_hex": "11223344556677881122334455667788",
  "dropped": 0,
  "remaining": 0
}
```

超时回复：

```json
{
  "ok": false,
  "result": 3,
  "result_name": "timeout",
  "error": "timeout",
  "rx_len": 0,
  "rx_hex": "",
  "dropped": 0,
  "remaining": 0
}
```

字段说明：

- `rx_hex`：收到的原始字节流。
- `dropped`：MCU 环形缓冲区因为客户读取太慢而丢掉的旧字节数量。
- `remaining`：本次读取后 MCU 内仍然缓存的字节数量。

### aircraft_transfer

请求：

```json
{
  "cmd": "aircraft_transfer",
  "args": {
    "tx_hex": "0102030d",
    "timeout_ms": 1000,
    "idle_ms": 30
  }
}
```

参数说明：

- `tx_hex`：要发到 UART4 485 的原始字节。`0102030d` 表示 `01 02 03 0D`。
- `timeout_ms`：总等待时间，单位 ms。
- `idle_ms`：收到数据后，连续空闲这么久就认为一帧结束，单位 ms。
- 单次发送和接收最大 220 字节。

成功回复：

```json
{
  "ok": true,
  "result": 0,
  "result_name": "ok",
  "rx_len": 4,
  "rx_hex": "0102030d"
}
```

超时回复：

```json
{
  "ok": false,
  "result": 3,
  "result_name": "timeout",
  "error": "timeout",
  "rx_len": 0,
  "rx_hex": ""
}
```

结果码：

| result | result_name | 说明 |
| --- | --- | --- |
| `0` | `ok` | 成功收到回复。 |
| `1` | `bad_len` | 参数长度错误。 |
| `2` | `busy` | 飞机 485 通道忙。 |
| `3` | `timeout` | 超时，没有收到回复。 |
| `4` | `overflow` | 接收溢出。 |
| `5` | `tx_fail` | 串口发送失败。 |

## 16. C++ 封装库

已提供一版无第三方依赖的 C++17 封装：

```text
interceptorctl/cpp_client/
  dock_client.hpp
  dock_client.cpp
  example.cpp
  Makefile
  README.md
```

编译：

```bash
cd /home/orangepi/interceptorctl/cpp_client
make clean
make
```

运行只读示例：

```bash
./dock_client_example
```

客户代码包含 `dock_client.hpp` 后直接调用类型化接口，不需要自己解析 JSON：

```cpp
#include "dock_client.hpp"

int main() {
    interceptorctl::DockClient dock;

    auto power = dock.power_status();
    if (!power.result.ok) {
        return 1;
    }

    int set_v = power.set_volt_0p01v;
    int temp = power.temperature_0p1c;
    (void)set_v;
    (void)temp;

    auto environment = dock.environment_status();
    if (environment.result.ok) {
        int env_temp = environment.temperature_0p01c;
        int env_humi = environment.humidity_0p01rh;
        (void)env_temp;
        (void)env_humi;
    }
    return 0;
}
```

主要返回结构：

| 方法 | 返回结构 | 说明 |
| --- | --- | --- |
| `version()` | `VersionResult` | `version` 字符串。 |
| `stop_status()` | `StopStatus` | `hardware_stop`。 |
| `motor_status()` | `MotorStatus` | `MotorData active/axis`，含 `position/enabled/stall/reached` 等字段。 |
| `door_open()` / `door_close()` | `MotionActionResult` | 应用层门打开/关闭动作，默认只等待 MCU ack。 |
| `motor_enable()` / `motor_disable()` | `MotorEnableResult` | 底层电机使能/失能，用于调试和标定。 |
| `motor_home()` / `motor_home_stop()` | `MotionActionResult` / `Result` | 底层回零/校准启动和停止，完成结果建议通过 `start_motion_event_thread()` 接收。 |
| `motor_stop()` / `motor_release_stop()` | `Result` | 停止当前电机运动、解除停止状态。 |
| `motor_trapezoid()` | `MotionActionResult` | 底层绝对位置梯形运动，用于调试和标定。 |
| `power_status()` | `PowerStatus` | 电源设定、输出、温度、错误码。 |
| `ups_status()` | `UpsStatus` | UPS 电压、电流、温度、版本和通信状态。 |
| `environment_status()` | `EnvironmentStatus` | GXHT30 环境温度、湿度、I2C 地址和错误码。 |
| `led_status()` | `LedStatus` | TCA9554 LED mask、I2C 地址、寄存器读回值和分组状态。 |
| `led_set_mask()` | `LedStatus` | 写原始 8-bit LED 输出 mask，并返回闭环读回状态。 |
| `led_set_group()` | `LedStatus` | 按 `Jc/Cd/Wz/Dp/All` 和 `Off/Red/Green/Both` 设置 LED。 |
| `switch_status()` | `SwitchStatus` | 读取 PSW1/PSW2/PSW3/PSW4 active-low 输入。 |
| `air_conditioner_status()` | `AirConditionerStatus` | HCNC4A 空调状态、温度、直流输入、配置参数回读、告警和错误码。 |
| `air_conditioner_power()` | `AirConditionerControlResult` | 远程空调开关。 |
| `air_conditioner_force_cool()` | `AirConditionerControlResult` | 强制制冷开关。 |
| `air_conditioner_force_heat()` | `AirConditionerControlResult` | 强制加热开关。 |
| `air_conditioner_silent_mode()` | `AirConditionerControlResult` | 正常/静音模式。 |
| `air_conditioner_humidity()` | `AirConditionerControlResult` | 下发监控湿度，0..100%。 |
| `air_conditioner_cool_start_temp()` | `AirConditionerControlResult` | 设置制冷启动温度，单位 0.1C。 |
| `air_conditioner_cool_diff()` | `AirConditionerControlResult` | 设置制冷回差，单位 0.1C。 |
| `air_conditioner_heat_start_temp()` | `AirConditionerControlResult` | 设置加热启动温度，单位 0.1C。 |
| `air_conditioner_heat_diff()` | `AirConditionerControlResult` | 设置加热回差，单位 0.1C。 |
| `air_conditioner_dehumid_setpoint()` | `AirConditionerControlResult` | 设置除湿目标，单位 %。 |
| `power_set()` | `PowerSetResult` | 本次请求的设定电压/电流。 |
| `power_on()` / `power_off()` | `PowerOutputResult` | 本次请求的输出开关目标。 |
| `aircraft_read()` | `AircraftReadResult` | 被动读取飞机主动上报原始字节流，含 `rx`、`rx_hex`、`dropped`、`remaining`。 |
| `aircraft_transfer()` | `AircraftTransferResult` | `std::vector<uint8_t> rx` 和 `rx_hex`。 |

每个返回结构都有 `result` 字段：

```cpp
struct Result {
    bool ok;
    std::string error;
    std::string raw_json;
};
```

`raw_json` 用于现场问题排查；正常业务代码只需要使用解析后的结构体字段。

`aircraft_read()` 不发送数据，只读取 MCU 已经缓存的飞机 485 原始字节流。
客户协议层需要自己按帧头、长度、CRC 或分隔符做分帧。

`aircraft_transfer()` 的发送参数是 `std::vector<uint8_t>`，库内部自动转换为
JSON 的 `tx_hex` 字符串；收到 `rx_hex` 后也会自动转换为 `std::vector<uint8_t>`。
这样客户业务代码不用手写 hex 字符串。

安全调用顺序建议：

1. 读 `stop_status`，确认 `hardware_stop == false`。
2. 对运动类动作，发送 `door_open/door_close`。
3. 判断返回 `ok == true`。
4. 动作后读 `motor_status`，确认 `motor.active == "idle"` 和对应状态。
5. 对电源动作，发送 `power_set/on/off` 后读 `power_status` 确认实际状态。
6. 现场问题排查时记录完整原始 JSON。
