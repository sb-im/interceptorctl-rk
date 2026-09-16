# interceptorctl 图形化菜单

把板卡现有的 `interceptorctl_menu.py` 完整搬到浏览器中，后端仍调用同一份
`/home/orangepi/interceptorctl/cli.py --json`，所以命令行为与数字菜单保持一致。

页面包含：

- 11 个组件状态卡片，支持单独刷新、全部刷新和可选的定时刷新。
- “系统与急停”页直接显示固件、整机、急停、UPS、温湿度、按键和按钮开盖角度状态。
- “舱门控制”页可选择并持久化实体按钮的 90°/120° 开盖档位，并可点击“从 MCU 回读当前角度”通过独立命令 ID 23 验证实际生效值。
- “低层电机”页可通过 RK `can0` 依次向 ID 1～32 定向发送只读 `1F 6B` 来扫描唯一电机，不使用广播指令；自动配置会将非 1 的电机 ID 持久化改为 1 并以同样方式复扫确认，再写入生产回零参数并严格回读验证；该流程不使能、不回零、不运动。
- 系统/急停、舱门、低层电机、电源、LED、空调、航空器 UART4 RS485 的全部菜单功能。
- 始终可见的命令日志；原始 stdout JSON、stderr、返回码和耗时都会直接显示。
- 不包含登录、权限或操作确认逻辑，适合当前生产调试网络直接使用。

## 本次交付状态

代码随 RK 仓库放在 `/home/orangepi/interceptorctl/factory_web`，默认不会自动启动，也不会配置开机启动。只上传和做
Python/JavaScript 静态校验，不会主动读取或控制硬件。

## 手动运行

```bash
cd /home/orangepi/interceptorctl/factory_web
python3 -m uvicorn app:app --host 0.0.0.0 --port 8080 --no-proxy-headers
```

然后从同一局域网访问 `http://itc-005.local:8080`。板卡走网口、手机走路由器 Wi-Fi 即可。

自动配置期间不要按实体开盖按钮。每次扫描都会逐个探测 ID 1～32，不会发送广播指令。页面会显示初始/最终电机 ID、是否完成改址复扫、当前/目标参数、ACK 和回读验证结果；完整 JSON 仍保留在右侧命令日志中。改址前必须明确读到电机未使能，改址或复扫失败时不会继续写回零参数。

`systemd/factory-web.service` 是后续需要开机启动时使用的模板，本次不会安装。

## 校验

```bash
cd /home/orangepi/interceptorctl/factory_web
python3 -m unittest discover -s tests -v
python3 -m py_compile *.py tests/*.py
node --check static/app.js
```
