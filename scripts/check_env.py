#!/usr/bin/env python3
"""DexFingerGuessing 环境自检：一条命令告诉你缺什么。

    python3 scripts/check_env.py

退出码 0 = 硬件和依赖都能用；1 = 有阻断项。
"""
import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []
warns = []


def ok(name, msg):
    print("  [ OK ] %-14s %s" % (name, msg))


def bad(name, msg):
    fails.append(name)
    print("  [FAIL] %-14s %s" % (name, msg))


def warn(name, msg):
    warns.append(name)
    print("  [WARN] %-14s %s" % (name, msg))


print("== Python ==")
if sys.version_info < (3, 8):
    bad("python", "%s 太旧，需要 >=3.8" % sys.version.split()[0])
else:
    ok("python", sys.version.split()[0])

print("== 依赖 ==")
for mod in ("serial", "cv2", "mediapipe", "flask"):
    try:
        m = importlib.import_module(mod)
        ok(mod, getattr(m, "__version__", "?"))
    except Exception as e:
        bad(mod, str(e))

print("== 模型 ==")
model = os.path.join(ROOT, "models", "hand_landmarker.task")
if not os.path.exists(model):
    bad("model", "缺少 %s，先跑 bash scripts/fetch_model.sh" % model)
elif os.path.getsize(model) < 1_000_000:
    bad("model", "文件太小，可能没下完")
else:
    ok("model", "%.1f MB" % (os.path.getsize(model) / 1048576))

print("== 串口 ==")
try:
    from serial.tools import list_ports
    ports = [(p.device, p.description) for p in list_ports.comports()]
    usb = [p for p in ports if any(k in p[0] for k in ("ttyUSB", "ttyACM", "COM"))]
    if usb:
        for d, desc in usb:
            ok("serial", "%s  %s" % (d, desc or ""))
    else:
        warn("serial", "没看到 USB 串口（共 %d 个）" % len(ports))
except Exception as e:
    bad("serial", str(e))

print("== 灵巧手 ==")
try:
    from dexh13 import DexH13
    h = DexH13()
    h.connect()
    ok("hand", "%s @ %d  %s" % (h.port, h.baud, h.version()))
    h.close()
except Exception as e:
    warn("hand", "连不上（只玩摄像头玩法可以忽略）：%s" % e)

print("== 摄像头 ==")
try:
    import cv2
    cap = cv2.VideoCapture(int(os.environ.get("DEXH13_CAM", "0")), cv2.CAP_V4L2)
    okc = cap.isOpened()
    frame = cap.read()[1] if okc else None
    cap.release()
    if frame is None:
        bad("camera", "打不开 /dev/video0")
    else:
        ok("camera", "%dx%d" % (frame.shape[1], frame.shape[0]))
except Exception as e:
    bad("camera", str(e))

print()
if fails:
    print("有 %d 项阻断：%s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("全部通过" + ("（%d 项提醒）" % len(warns) if warns else ""))