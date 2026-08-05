# interceptorctl 二次开发接入指南

> C++ 客户接入和最新精简 JSON API 以 `GUIDANCE_CPP.md` 为准。本文保留通用接入说明。

本文给客户应用层使用。无论客户用 Python 还是 C/C++，正式接入都应该访问 `interceptorctl daemon` 的 Unix socket，不要直接打开 `/dev/mcu`。

## 1. 总体架构

```text
客户应用
  -> /tmp/interceptorctl.sock
  -> interceptorctl daemon
  -> /dev/mcu
  -> STM32 USART1 Package 协议
  -> 电机 / 电源 / 飞机 485 / 后续硬件
```

规则：

- 只有 `interceptorctl daemon` 可以打开 `/dev/mcu`。
- 客户程序、测试脚本、调度程序都通过 `/tmp/interceptorctl.sock` 发 JSON 请求。
- 每个请求是一行 UTF-8 JSON，以 `\n` 结尾；每个回复也是一行 JSON。
- socket 路径默认是 `/tmp/interceptorctl.sock`。
- 当前正式 MCU 固件版本是 `0x0039`，RK3588 `interceptorctl` 使用 `main` 正式分支。仍使用原电机驱动回零方式的设备可选择兼容固件 `0x0038`。

启动 daemon：

```bash
cd /home/orangepi/interceptorctl
sudo ./tools/install_service.sh
systemctl status interceptorctl.service
```

如果旧 `sbmcu.service` 正在运行，需要禁用，否则旧 `sbdocker` 会占用 `/dev/mcu`：

```bash
sudo systemctl disable --now sbmcu.service 2>/dev/null || true
```

## 2. JSON 协议

请求固定是一行 UTF-8 JSON，以 `\n` 结尾：

```json
{"cmd":"motor_status","args":{}}
```

回复也是一行 JSON。客户默认 API 只保留业务字段，例如：

```json
{"ok":true,"version":"0x0039"}
```

通用字段：

- `ok`：本次请求是否成功。客户程序必须先判断它。
- `error`：`ok == false` 时的失败原因。

`cmd_set`、`cmd_id`、`data_hex`、`result`、`messages`、`raw`、`*_raw` 等字段属于 MCU/调试层细节，不再作为客户默认 API 暴露。客户侧 C++ 接入和完整命令示例以 `GUIDANCE_CPP.md` 为准。

## 3. 当前命令表

当前命令表已拆成四类：

- API 读取类：`version`、`stop_status`、`motor_status`、`power_status`、`ups_status`、`env_status`、`led_status`、`switch_status`、`ac_status`。
- API 控制类：`door_open`、`door_close`、`power_set`、`power_on`、`power_off`、`led_set`、`ac_control`、`aircraft_read`、`aircraft_transfer`。
- 底层读取/调试类：`status`、`power_raw_transfer`。
- 底层控制/调试类：`motor_enable`、`motor_trapezoid`、`motor_stop`、`motor_release_stop`。

详细参数、返回示例、电机单位和电源错误码说明见 `GUIDANCE_CPP.md`。

空调 `ac_control` 目前支持远程开关、强制制冷/加热、正常/静音模式、监控湿度下发，以及制冷启动温度、制冷回差、加热启动温度、加热回差、除湿设定点写入。写入后用 `ac_status` 回读确认实际寄存器值。

`switch_status` 用于读取 PSW1/PSW2/PSW3/PSW4 active-low 输入：PSW1/PD15=`module_reached_switch`，PSW2/PD14=`aircraft_position_switch`，PSW3/PD13=`cover_button`，PSW4/PD12=`aircraft_present_switch`。按钮触发手动关盖时，MCU 只在触发瞬间比较 PSW2 与 PSW4：两者都按下或都没按下时允许关盖，只有一个按下时阻止关盖。运动开始后不再检查这两个输入。PSW1 不参与关盖条件，但关闭方向回零时作为零点触发开关。电机驱动器需预先配置正确的回零方向与运动参数，MCU 回零期间不会读取或改写这些参数。回零前 MCU 会先对 PSW1 消抖并读取电机状态；实时堵转或堵转保护会直接终止回零且不会自动解堵，PSW1 已触发时不会启动电机，只执行去使能、清零和校验。

## 4. 急停接口

客户默认只读取实体急停按钮状态：

```json
{"cmd":"stop_status","args":{}}
```

```json
{"ok":true,"hardware_stop":false}
```

`hardware_stop == true` 表示实体急停按钮按下。客户 App 层应基于这个状态设计自己的安全逻辑；当前系统约定急停按下后禁止电机动作，其它功能不必全部禁用。

## 5. Python 开发版

最小可用示例：

```python
#!/usr/bin/env python3
import json
import socket

SOCKET_PATH = "/tmp/interceptorctl.sock"


def dock_request(cmd, args=None, timeout=5.0):
    request = {"cmd": cmd, "args": args or {}}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(SOCKET_PATH)
        sock.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    if not data:
        raise RuntimeError("empty response from interceptorctl daemon")
    resp = json.loads(data.decode("utf-8"))
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error", "interceptorctl request failed"))
    return resp


def require_no_estop():
    status = dock_request("stop_status")
    if status["hardware_stop"]:
        raise RuntimeError(f"emergency stop active: {status}")


if __name__ == "__main__":
    print(dock_request("version"))
    require_no_estop()

    # 查询状态
    print(dock_request("status"))
    print(dock_request("ups_status"))
    print(dock_request("env_status"))
    print(dock_request("led_status"))
    print(dock_request("ac_status"))
    print(dock_request("led_set", {"group": "wz", "color": "red"}))

    # 设置电源为 24.00V / 1.00A，然后打开输出
    print(dock_request("power_set", {"voltage": 2400, "current": 100}))
    print(dock_request("power_on"))

    # 飞机 485 主动上报读取。返回原始字节流，客户协议层自行分帧。
    print(dock_request("aircraft_read", {
        "timeout_ms": 500,
        "max_len": 80,
    }, timeout=2.0))
```

建议封装：

- 客户 Python 项目中保留一个单例 `DockClient`，所有硬件动作都走它。
- 动作前统一调用 `stop_status`。
- 对 `aircraft_read` 返回的原始字节流按飞机协议再封装成更高层解析器。
- 对请求-响应型命令，可继续用 `aircraft_transfer`，不要让业务代码到处拼十六进制字符串。

## 6. C/C++ 开发版

最小 C++17 示例。生产项目建议使用 `nlohmann/json`、RapidJSON 或 cJSON 解析回复；下面为了展示 socket 调用，直接打印原始 JSON。

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
    char buf[1024];
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
        std::cout << dock_request(R"({"cmd":"status","args":{}})");
        std::cout << dock_request(R"({"cmd":"env_status","args":{}})");
        std::cout << dock_request(R"({"cmd":"led_status","args":{}})");
        std::cout << dock_request(R"({"cmd":"led_set","args":{"group":"wz","color":"red"}})");

        // 电源 24.00V / 1.00A
        std::cout << dock_request(R"({"cmd":"power_set","args":{"voltage":2400,"current":100}})");

        // 飞机 485 主动上报读取
        std::cout << dock_request(R"({"cmd":"aircraft_read","args":{"timeout_ms":500,"max_len":80}})");
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

C 项目可以使用同样的 Unix socket 流程，JSON 解析推荐用 cJSON。核心流程不变：

1. `socket(AF_UNIX, SOCK_STREAM, 0)`。
2. `connect("/tmp/interceptorctl.sock")`。
3. `write()` 一行 JSON。
4. `read()` 到 `\n`。
5. 解析 JSON，判断 `ok` 和 `result`。

## 7. 交付建议

- 客户应用层只依赖 `interceptorctl` 的 JSON socket 协议，不依赖 STM32 Package 二进制帧。
- 需要新增硬件能力时，优先在 daemon 里新增 `cmd`，保持客户侧 JSON 协议稳定。
- 对外发布前，把客户实际需要的字段冻结成一版接口表；调试字段可以继续留在 `raw` 或日志里。
- 高风险动作建议统一封装“动作前检查急停、动作后读取状态、失败时记录原始 JSON”的模板。
