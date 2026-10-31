"""第 2 课：只读，不动手。

先学会安全地观察：版本、位置、电流、温度、电压。
这一步不会让任何关节动起来。
"""
import sys, pathlib, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dexh13 import DexH13, default_port
from dexh13.gestures import JOINT_NAMES

PORT = os.environ.get("DEXH13_PORT", "/dev/ttyUSB0")

with DexH13(port=PORT) as h:
    print("端口 %s  波特率 %d  版本 %s" % (h.port, h.baud, h.version()))
    pos = h.positions()
    cur = h.currents()
    tmp = h.temperatures()
    vol = h.voltages()
    print("-" * 52)
    print("%-9s %8s %8s %8s %7s" % ("关节", "位置", "电流mA", "温度℃", "电压"))
    for i, name in enumerate(JOINT_NAMES):
        print("%-9s %8d %8d %8d %7d" % (name, pos[i], cur[i], tmp[i], vol[i]))
    print("-" * 52)
    print("状态寄存器：", h.status())
    if all(v == 0 for v in pos):
        print("提示：位置全 0 通常表示「还没使能」，属于正常现象。")