"""第 3 课：使能与失能。

使能 = 电机通电并在当前位置「抓住」。真机的标准时序是：
  总使能关闭 -> 总使能打开 -> 13 个电机切位置模式 -> 13 个电机使能 -> 清命令
缺任何一步，滑块发下去都不会动。
"""
import sys, pathlib, os, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dexh13 import DexH13, default_port

PORT = os.environ.get("DEXH13_PORT") or default_port()

with DexH13(port=PORT) as h:
    print("使能前位置：", h.positions())
    h.enable()
    print("使能后位置：", h.positions())
    print("温度：", h.temperatures())
    print()
    if sys.stdin.isatty():
        input("现在电机带电。确认手是空载后按回车，执行失能 …")
    else:
        print("（非交互环境，2 秒后自动失能）")
        time.sleep(2)
    h.disable(verbose=True)
    print("失能后位置：", h.positions())