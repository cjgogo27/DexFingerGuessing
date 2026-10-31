# 硬件与接线

## 1. 清单

| 部件 | 规格 | 备注 |
|---|---|---|
| PaXini DexH13 | 右手，13 主动 DoF，1.3 kg | 固件 `V1.4.4_D` |
| 直流电源 | 24–48 V，≥3 A | 手册标称 3 A Max，纹波 ≤150 mV |
| USB-485 转换器 | Prolific **PL2303GR** | 内置 RS485 收发器，支持 ≥4 Mbps |
| UVC 摄像头 | 720p 即可 | 用于手势识别 |
| 线缆 | 手自带 4 芯 MODBUS 线 | 绿/白/黑 + 屏蔽 |

> **不要用 CH340**。它上限 2–3 Mbps，本机实测 3.5/4 Mbps 直接打不开端口。
> 可用：CH343 / CH9102 / FT232H / PL2303GR。

## 2. 线序

手侧的 MODBUS 线一共 4 根（3 芯 + 屏蔽），热缩管印 `MODBUS`：

| 线色 | 含义 | 接到 USB-485 |
|---|---|---|
| 绿 | RS485_A | A |
| 白 | RS485_B | B |
| 黑 | RS485_GND | GND |
| 裸铜（屏蔽网） | Shield | **悬空** |

数芯数就能分辨：**4 根 = MODBUS，5 根 = EtherCAT**（热缩管印 `ECAT`）。

## 3. 串口参数

| 项 | 值 |
|---|---|
| 波特率 | **460800** |
| 数据位 / 校验 / 停止位 | 8 / None / 1 |
| 流控 | 无 |
| 从站地址 | 右手 `0x79`(121)，左手 `0x78`(120) |

> 官方上位机出厂配置写的是 4000000，那是给固件 2.0+ 的。
> 本机固件 1.4.4 在 4 Mbps 下完全无响应，**必须 460800**。

## 4. 供电与安全

- 上电前确认手是**空载**（没有夹住东西、没有被人握住）
- **禁止带电插拔电源线**
- **不要人为扳动自锁关节**（通电、断电都不行）
- 使能后电机会保持当前位置，此时不要硬掰

## 5. 常见问题

| 现象 | 原因 |
|---|---|
| 完全无响应 | 没通电 / A、B 接反 / 波特率不是 460800 |
| 串口能开但无数据 | 波特率错，或上位机正占着串口 |
| 使能了不动 | 少了「切位置模式」或「电机使能」那两步 |
| 位置恒为 0 | 没使能 |
| 状态寄存器恒 `0xFFFF` | 该固件未实现，不是故障 |
| 电流显示 6 万多 | 按无符号读了，应为 int16 有符号 |
| 摄像头反复掉线 | USB 供电不足，换主板后置口，别和 485 共用 hub |

## 6. 本机实测环境

| | |
|---|---|
| 主机 | Ubuntu 22.04.5 / RTX 5090 / 32 核 |
| 设备节点 | `/dev/ttyUSB0` |
| 用户组 | 需要 `dialout`（`sudo usermod -aG dialout $USER`） |
| Windows | COM8，需安装 PL2303 驱动 |

## 7. 摄像头「能枚举但不出帧」

典型症状：

- `lsusb` 看得到设备，`/dev/video0` 也在
- OpenCV 读帧一直 `select() timeout`
- `dmesg` 刷 `uvcvideo ...: Failed to set UVC probe control : -71`（-71 = EPROTO）

这不是驱动没装，是 **UVC 控制传输失败**，常见于 USB 自动挂起和供电/线材问题。

### 处理顺序

**① 关闭 USB 自动挂起（最有效，建议做成永久规则）**

```bash
# 临时
sudo sh -c 'echo on > /sys/bus/usb/devices/1-8/power/control'

# 永久（按 VID 匹配，插拔后依然生效）
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="1bcf", ATTR{power/control}="on"' \
  | sudo tee /etc/udev/rules.d/99-uvc-no-autosuspend.rules
sudo udevadm control --reload-rules
```

**② 复位设备**

```bash
sudo bash scripts/recover_camera.sh
```

脚本会关自动挂起 → 把 `authorized` 置 0 再置 1 重新枚举 → 试抓 3 帧验证。

**③ 换 USB 口**

优先主板后置口，**不要和 485 转接头共用 USB hub**。
这颗 SPCA2650 在共享 hub 上反复掉线，内核日志里出现过
`Cannot enable. Maybe the USB cable is bad?`。

**④ 换线 / 换摄像头**

如果在多个口、多次复位后仍然 0 帧，基本可判定线材或摄像头本体问题。

### 实在救不回来时的替代方案

- **手机当摄像头**：装 IP Webcam 一类的 App，推 RTSP/HTTP 流，
  把 `DEXH13_CAM` 换成流地址，可完全绕开 USB。
- **掌心相机**：DexH13 自带 4K UVC 掌心相机（USB-A 输出），
  接上就是另一路视频源。

> 本项目已在 `web/app.py` 里做了兜底：摄像头打不开时服务照常启动，
> 页面显示 `NO HAND`；设备恢复后视频会自动接上，不用重启。