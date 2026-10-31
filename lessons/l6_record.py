"""第 6 课：采一段数据存 CSV。

这就是做实验的起点：时间戳 + 13 个位置 + 13 个电流 + 13 个温度。
后面接相机的话，把帧号一并写进去，就能对齐多模态数据了。
"""
import sys, pathlib, os, time, csv, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dexh13 import DexH13, default_port
from dexh13.gestures import JOINT_NAMES

PORT = os.environ.get("DEXH13_PORT") or default_port()
DURATION = float(os.environ.get("DEXH13_DURATION", "10"))
HZ = float(os.environ.get("DEXH13_HZ", "20"))

out_dir = pathlib.Path(__file__).resolve().parents[1] / "data"
out_dir.mkdir(exist_ok=True)
path = out_dir / ("record_%s.csv" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))

header = ["t"] + ["pos_%s" % n for n in JOINT_NAMES] \
                + ["cur_%s" % n for n in JOINT_NAMES] \
                + ["temp_%s" % n for n in JOINT_NAMES]

with DexH13(port=PORT) as h:
    h.enable()
    t0 = time.time()
    n = 0
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        while time.time() - t0 < DURATION:
            tick = time.time()
            row = [round(tick - t0, 4)]
            row += h.positions() + h.currents() + h.temperatures()
            w.writerow(row)
            n += 1
            time.sleep(max(0.0, 1.0 / HZ - (time.time() - tick)))
    print("采了 %d 帧 / %.1fs，实际约 %.1f Hz" % (n, DURATION, n / DURATION))
    print("文件：", path)