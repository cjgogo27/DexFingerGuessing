#!/usr/bin/env bash
# 摄像头「能枚举但不出帧」时的恢复脚本（Linux）。
#
# 症状：lsusb 能看到设备、/dev/video0 存在，OpenCV 却 select() timeout，
#       dmesg 里刷 "Failed to set UVC probe control : -71"。
# 原因多半是 USB 自动挂起 + UVC 控制传输失败。
#
# 用法： sudo bash scripts/recover_camera.sh
set -e

VID="${UVC_VID:-1bcf}"          # 摄像头 USB Vendor ID，用 lsusb 查
DEV=""
for d in /sys/bus/usb/devices/*/; do
  if [ -f "${d}idVendor" ] && [ "$(cat "${d}idVendor")" = "$VID" ]; then DEV="$d"; fi
done

if [ -z "$DEV" ]; then
  echo "没找到 VID=$VID 的 USB 设备，先检查线是否插好"; exit 1
fi
echo "device: $DEV"

echo "[1/3] 关闭 USB 自动挂起"
echo on > "${DEV}power/control" 2>/dev/null || true

echo "[2/3] 重新枚举设备"
echo 0 > "${DEV}authorized" 2>/dev/null || true
sleep 2
echo 1 > "${DEV}authorized" 2>/dev/null || true
sleep 4

echo "[3/3] 试抓 3 帧"
rm -f /tmp/uvc_probe.raw
timeout 15 v4l2-ctl -d /dev/video0 --stream-mmap --stream-count=3 \
        --stream-to=/tmp/uvc_probe.raw 2>&1 | tail -2 || true
if [ -s /tmp/uvc_probe.raw ]; then
  echo "OK：拿到 $(stat -c%s /tmp/uvc_probe.raw) 字节"
else
  echo "仍然无帧 —— 换 USB 口或换线"
fi