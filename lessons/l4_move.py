"""第 4 课：让一个关节动起来。

核心命令只有一个：FC16 写 0x2050，连续写 13 个目标位置。
本课只动「拇指 1」，幅度 +200，然后回原位。
"""
import sys, pathlib, os, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dexh13 import DexH13, default_port
from dexh13.gestures import JOINT_NAMES

PORT = os.environ.get("DEXH13_PORT") or default_port()

with DexH13(port=PORT) as h:
    h.enable()
    base = h.positions()
    print("起始位置：", base)

    target = list(base)
    target[0] += 200
    print("拇指1 %d -> %d" % (base[0], target[0]))
    h.move_to(target, duration=1.0)
    time.sleep(0.3)
    print("到位位置：", h.positions())

    h.move_to(base, duration=1.0)
    time.sleep(0.3)
    print("回位位置：", h.positions())