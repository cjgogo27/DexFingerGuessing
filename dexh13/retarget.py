"""把 VR 头显的手部关节（WebXR XRHand，25 点）重定向到 DexH13 的 13 个关节。

XRHand 关节顺序（固定 25 个）：
  0  wrist
  1  thumb-metacarpal        2  thumb-phalanx-proximal   3  thumb-phalanx-distal   4  thumb-tip
  5  index-metacarpal        6  index-proximal           7  index-intermediate      8  index-distal   9  index-tip
 10  middle-metacarpal      11  middle-proximal         12  middle-intermediate    13  middle-distal 14  middle-tip
 15  ring-metacarpal        16  ring-proximal           17  ring-intermediate      18  ring-distal   19  ring-tip
 20  pinky-metacarpal       21  pinky-proximal          22  pinky-intermediate     23  pinky-distal  24  pinky-tip

思路和摄像头版一致：算指节夹角 → 屈曲度 → 按标定区间归一化 → 乘「拳头」手势的电机值。
区别是输入是 3D 坐标，不受透视影响，比 2D 关键点准。
"""
from __future__ import annotations

import math

from . import gestures

# 每根手指参与计算的 (前, 中, 后) 三元组，索引是 XRHand 的关节序号
FINGER_TRIPLETS = {
    "thumb":  [(1, 2, 3), (2, 3, 4)],
    "index":  [(5, 6, 7), (6, 7, 8), (7, 8, 9)],
    "middle": [(10, 11, 12), (11, 12, 13), (12, 13, 14)],
    "ring":   [(15, 16, 17), (16, 17, 18), (17, 18, 19)],
    "pinky":  [(20, 21, 22), (21, 22, 23), (22, 23, 24)],
}
FINGER_ORDER = ["thumb", "index", "middle", "ring", "pinky"]
MOTOR_FINGER = (["thumb"] * 4) + (["index"] * 3) + (["middle"] * 3) + (["ring"] * 3)
FIST = gestures.PRESETS["拳头"]
MIN_SPAN = 60.0     # 标定幅度太小就用默认映射


def _angle(a, b, c) -> float:
    """三点夹角（度），b 是顶点。输入是 3D 坐标。"""
    v1 = (a[0] - b[0], a[1] - b[1], a[2] - b[2])
    v2 = (c[0] - b[0], c[1] - b[1], c[2] - b[2])
    n1 = math.sqrt(sum(x * x for x in v1))
    n2 = math.sqrt(sum(x * x for x in v2))
    if n1 < 1e-9 or n2 < 1e-9:
        return 180.0
    cos = sum(x * y for x, y in zip(v1, v2)) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def flexion(joints) -> dict:
    """25 个 3D 点 → 每根手指的屈曲量（0 = 伸直，越大越弯）。"""
    out = {}
    for name, triplets in FINGER_TRIPLETS.items():
        s = 0.0
        for a, b, c in triplets:
            s += 180.0 - _angle(joints[a], joints[b], joints[c])
        out[name] = s
    return out


class XRRetargeter:
    """带自动标定的重定向器：越用越准。"""

    def __init__(self, smooth=0.35):
        self.lo = {n: None for n in FINGER_ORDER}
        self.hi = {n: None for n in FINGER_ORDER}
        self.smooth = {n: 0.0 for n in FINGER_ORDER}
        self.alpha = smooth
        self.last = [0] * 13
        self.frames = 0

    def _calibrate(self, f):
        for n in FINGER_ORDER:
            self.lo[n] = f[n] if self.lo[n] is None else min(self.lo[n], f[n])
            self.hi[n] = f[n] if self.hi[n] is None else max(self.hi[n], f[n])

    def reset(self):
        self.lo = {n: None for n in FINGER_ORDER}
        self.hi = {n: None for n in FINGER_ORDER}
        self.smooth = {n: 0.0 for n in FINGER_ORDER}
        self.frames = 0

    def update(self, joints, max_step=400):
        """输入 25 个 3D 点，返回 13 个电机目标值。"""
        self.frames += 1
        f = flexion(joints)
        self._calibrate(f)
        ratios = {}
        for n in FINGER_ORDER:
            span = self.hi[n] - self.lo[n]
            r = (f[n] - 10.0) / 250.0 if span < MIN_SPAN else (f[n] - self.lo[n]) / span
            r = max(0.0, min(1.0, r))
            self.smooth[n] = (1 - self.alpha) * self.smooth[n] + self.alpha * r
            ratios[n] = self.smooth[n]
        target = [int(round(self.smooth[fg] * FIST[i])) for i, fg in enumerate(MOTOR_FINGER)]
        # 限速：单帧最多变化 max_step，防止抖动甩到底
        target = [max(self.last[i] - max_step, min(self.last[i] + max_step, t))
                  for i, t in enumerate(target)]
        target = [max(-1000, min(5000, t)) for t in target]
        self.last = target
        return target, ratios

    def spans(self):
        return {n: (self.hi[n] - self.lo[n]) if self.lo[n] is not None else 0
                for n in FINGER_ORDER}