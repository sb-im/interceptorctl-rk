# RK3588 控制程序与 MCU 固件更新说明

本文用于在 RK3588 上更新 `interceptorctl daemon`、CLI、C++ SDK 和 STM32 MCU 固件。

正式程序目录：

```text
/home/orangepi/interceptorctl
```

GitHub 仓库：

```text
https://github.com/sb-im/interceptorctl-rk.git
```

正式客户版本使用 `main` 分支。测试分支中的程序和固件未经确认前不要用于现场设备。

## 1. 更新前检查

登录 RK3588：

```bash
ssh orangepi@<RK3588_IP>
cd /home/orangepi/interceptorctl
```

检查当前服务、代码版本和 MCU 固件版本：

```bash
systemctl is-active interceptorctl.service
git branch --show-current
git log -1 --oneline
git status --short
./interceptorctl version
```

正常情况下：

- `interceptorctl.service` 应显示 `active`。
- Git 分支应为 `main`。
- `git status --short` 不应显示对正式代码文件的修改。
- `./interceptorctl version` 应能读取 MCU 固件版本。

如果 `git status --short` 显示 `mcu.py`、`daemon.py`、`cli.py`、`cpp_client/` 等正式文件被修改，先停止更新并联系维护人员。不要直接执行 `git reset --hard`，避免丢失现场文件。

## 2. 更新 RK3588 daemon 和 SDK

### 2.1 停止服务

```bash
cd /home/orangepi/interceptorctl
sudo systemctl stop interceptorctl.service
systemctl is-active interceptorctl.service
```

最后一条命令显示 `inactive` 属于正常现象。

### 2.2 拉取正式版本

```bash
git fetch origin
git switch main
git pull --ff-only origin main
```

不要使用 `sudo git pull`，否则仓库文件可能变成 `root` 所有，影响后续更新。

如果仓库为公开仓库，但 HTTPS 拉取仍要求输入 GitHub 用户名，检查远程地址：

```bash
git remote -v
git remote set-url origin https://github.com/sb-im/interceptorctl-rk.git
git pull --ff-only origin main
```

### 2.3 检查更新后的程序

```bash
python3 -m py_compile mcu.py daemon.py cli.py main.py
make -C cpp_client clean all
```

两个命令都成功后再启动服务。Python 检查失败或 C++ SDK 编译失败时，不要继续烧录 MCU 固件。

### 2.4 安装并启动最新服务

```bash
sudo ./tools/install_service.sh
systemctl is-enabled interceptorctl.service
systemctl is-active interceptorctl.service
```

`install_service.sh` 会更新 systemd 服务文件、停用冲突的旧服务，并重新启动 `interceptorctl.service`。

### 2.5 只读验证

```bash
git log -1 --oneline
ls -l /tmp/interceptorctl.sock
./interceptorctl version
./interceptorctl motor status
./interceptorctl power status
```

以上命令只读取状态，不会控制电机或打开电源输出。未连接对应外设时，状态中的 `communicated=False` 属于正常现象。

如果更新过程在启动服务前失败，应先恢复服务：

```bash
sudo systemctl start interceptorctl.service
```

## 3. 更新 STM32 MCU 固件

MCU 固件位于：

```text
/home/orangepi/interceptorctl/tools/
```

### 3.1 查看仓库中的固件

```bash
cd /home/orangepi/interceptorctl
find tools -maxdepth 1 -type f -name 'sbdock_0x*.bin' -printf '%f\n' | sort -V
```

固件文件名中的 `0xXXXX` 是 MCU 固件版本。应按设备的回零方式选择正式固件，不要使用来源不明或测试分支中的 `.bin` 文件。

当前仓库中的主要自动恢复固件为：

```text
tools/sbdock_0x0038_legacy_home_auto_recovery.bin
tools/sbdock_0x0039_close_switch_home_auto_recovery.bin
tools/sbdock_0x003B_button_90deg_open.bin
```

- `0x003B`：默认正式版本，使用 PSW1 关门方向回零；实体按钮开盖到电机侧 `-34500°`，API 完整开盖目标不变。
- `0x003A`：因 `0.1°` 单位换算错误已作废，最新仓库不再提供该固件，不得烧录。
- `0x0039`：上一正式版本，使用 PSW1 关门方向回零。
- `0x0038`：兼容版本，保留 `0x0033` 的原电机驱动回零方式。
- 上述版本都包含电机错误自动恢复；瞬时 CAN/ACK 故障不再让 MCU 永久停留在错误状态。
- 自动恢复不会继续执行故障前的运动目标，也不会自动清除电机堵转保护。

发布文件校验值：

```text
0x0038  71764 bytes  SHA256 58ee5e79ed49a03e70fef37bcf7cc4a3265260c0d3b9285648a5ba74f90e2c2b
0x0039  73960 bytes  SHA256 cb83f6bfeb021e6d8d5f45c57ae62948b22f67828ef876cc774e26f3e5e173d6
0x003B  73984 bytes  SHA256 62f552cec1bd3118ba61fdf6fba7a7476d1b4853eeec95894416ef8e2fbeaafa
```

### 3.2 烧录前预演

预演只显示将要执行的操作，不切换 GPIO，也不烧录 MCU：

```bash
sudo /usr/bin/python3 tools/flash_mcu.py --dry-run \
  tools/sbdock_0x003B_button_90deg_open.bin
```

确认板型、固件路径和 `/dev/mcu` 均正确后再执行正式烧录。

### 3.3 正式烧录

```bash
sudo /usr/bin/python3 tools/flash_mcu.py \
  tools/sbdock_0x003B_button_90deg_open.bin
```

烧录工具会自动完成以下操作：

1. 停止 `interceptorctl.service`，释放 `/dev/mcu`。
2. 控制 BOOT0 和 RESET，使 STM32 进入串口 Bootloader。
3. 使用 `stm32loader` 擦除并写入固件。
4. 退出 Bootloader 并复位 MCU。
5. 重新启动 `interceptorctl.service`。

终端出现 `==> Done.` 表示烧录流程执行完成。烧录过程中不要关闭 RK3588 电源、拔掉主板连接或中断终端。

### 3.4 烧录后验证

```bash
sleep 2
systemctl is-active interceptorctl.service
./interceptorctl version
./interceptorctl motor status
./interceptorctl power status
```

使用默认 `0x003B` 固件时，版本回读应为：

```text
0x003B
```

版本正确且服务为 `active` 后，固件更新才算完成。

## 4. daemon 和固件一起更新

推荐顺序：

1. 停止 `interceptorctl.service`。
2. 从 `origin/main` 拉取最新 RK 程序。
3. 执行 Python 检查并重新编译 C++ SDK。
4. 运行 `install_service.sh`，确认 daemon 可以启动。
5. 使用新仓库中发布的指定 `.bin` 文件烧录 MCU。
6. 回读 Git 提交、MCU 版本和设备状态。

不要先烧录新 MCU 固件再运行明显较旧的 RK daemon。两端协议同时有变化时，旧 daemon 可能无法正确解析新固件返回的数据。

## 5. 故障排查

查看服务状态：

```bash
systemctl status interceptorctl.service --no-pager --full
```

查看 systemd 日志：

```bash
journalctl -u interceptorctl.service -n 100 --no-pager
```

查看 daemon 日志：

```bash
tail -n 100 /home/orangepi/interceptorctl/logs/interceptorctl.log
```

检查 MCU 设备和本地 Socket：

```bash
ls -l /dev/mcu
ls -l /tmp/interceptorctl.sock
```

检查是否有其他进程占用 `/dev/mcu`：

```bash
sudo lsof /dev/mcu
```

正常情况下只有 `interceptorctl daemon` 持有 `/dev/mcu`。客户程序和调试程序都不应直接打开该设备。

## 6. 更新完成记录

更新后建议保存以下信息：

```bash
date
git branch --show-current
git log -1 --oneline
./interceptorctl version
systemctl is-active interceptorctl.service
```

出现问题时，将以上输出以及 `logs/interceptorctl.log` 中对应时间段的日志提供给维护人员。
