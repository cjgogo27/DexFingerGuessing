"""第 1 课：把「手在哪」找出来。

要点：一只手的身份由三件事决定 —— 串口号、波特率、从站地址。
三者任一不对，都会表现为「完全没有回应」。
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dexh13 import list_serial_ports, scan

USB_KEYS = ("ttyUSB", "ttyACM", "ttyAMA", "COM", "usbserial", "cu.usb")

print("=" * 62)
ports = list_serial_ports()
usb = [p for p in ports if any(k in p[0] for k in USB_KEYS)]
print("本机串口 %d 个，其中 USB 转串口 %d 个：" % (len(ports), len(usb)))
for dev, desc in usb:
    print("  %-16s %s" % (dev, desc or "-"))
if not usb:
    print("  没发现 USB 转串口设备 —— 检查 USB 线、驱动（Windows 需 CH343/PL2303 驱动）")

print("=" * 62)
print("开始扫描 端口 x 波特率 x 地址 …")
found = scan()

print("=" * 62)
if not found:
    print("没扫到。依次检查：")
    print("  1) 手通电了吗（24-48V）")
    print("  2) RS485 的 A/B 有没有接反（绿=A，白=B，黑=GND）")
    print("  3) 上位机是不是正占着这个串口（先关掉）")
    print("  4) 串口权限（Linux：sudo usermod -aG dialout $USER）")
else:
    for port, baud, addr, ver in found:
        print("可用的手：%s @ %d  地址 0x%02X（%s）  版本 %s"
              % (port, baud, addr, "右手" if addr == 0x79 else "左手", ver))