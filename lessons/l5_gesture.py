"""第 5 课：跑一组预设手势。

手势就是 13 个目标位置的一组常量，存在 dexh13/gestures.py 里。
想加自己的手势，往那个字典里加一行就行。
"""
import sys, pathlib, os, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dexh13 import DexH13, gestures, default_port

PORT = os.environ.get("DEXH13_PORT") or default_port()
SEQ = ["零位", "拳头", "全", "ok", "零位"]

print("可选手势：", "、".join(gestures.PRESETS))

with DexH13(port=PORT) as h:
    h.enable()
    for name in SEQ:
        print("-> %s" % name)
        h.play(name, duration=1.2)
        time.sleep(0.6)
    print("结束位置：", h.positions())