"""SDK 后端：用官方 pxdex 驱动灵巧手，接口和自研 Modbus 驱动保持一致。

为什么需要它：
  * 固件升到 2.0.2 后，我们逆向的 Modbus 寄存器不再响应
  * 全量触觉（1140 点）只有 SDK 能拿到

用法：
    from dexh13.sdk_hand import SDKHand
    h = SDKHand().connect()
    h.enable()
    h.play("拳头", duration=1.0)

注意：SDK 的关节单位是「度」，本模块对外仍用项目里的 0~5000 电机刻度，
      换算比例是 TARGET_MAX -> DEG_MAX。
"""
from __future__ import annotations

import math
import time

try:
    from pxdex.dh13 import DexH13Control, ControlMode
except ImportError:                      # 没装 SDK 的环境仍可 import 本模块
    DexH13Control = None
    ControlMode = None

from . import gestures

TARGET_MAX = 5000.0      # 项目内部的电机刻度上限
DEG_MAX = 120.0          # 对应 SDK 的角度上限（度）
MAX_RAD_STEP = math.radians(1.0)   # 每步最大角度变化，用于平滑插值

# SDK 的 16 个值 = 4 根手指 × 4 个关节 [外展, MCP, PIP, DIP]
FINGER_ORDER = ["thumb", "index", "middle", "ring"]
MOTOR_FINGER = (["thumb"] * 4) + (["index"] * 3) + (["middle"] * 3) + (["ring"] * 3)


def _to_target(deg: float) -> float:
    return deg / DEG_MAX * TARGET_MAX


def _to_deg(target: float) -> float:
    return max(0.0, min(DEG_MAX, target / TARGET_MAX * DEG_MAX))


class _TPShim:
    """让上层还能用 read_regs(0x4000/0x1060/0x1050) 这套接口。"""

    def __init__(self, hand: "SDKHand"):
        self.h = hand

    def read_regs(self, reg: int, count: int):
        h = self.h
        if reg == 0x4000:
            vals = h.tactile_resultant()
            return (vals + [0] * count)[:count]
        if reg == 0x1060:
            vals = h.currents()
            return (vals + [0] * count)[:count]
        if reg == 0x1050:
            vals = [int(_to_target(t)) for t in h.angles_deg()]
            return (vals + [0] * count)[:count]
        return [0] * count

    def write_regs(self, reg: int, values):
        if reg == 0x2050:
            self.h.move_to(list(values), duration=0.3)


class SDKHand:
    def __init__(self, port: str = "/dev/ttyUSB0", camera: str = "none"):
        if DexH13Control is None:
            raise RuntimeError("没装官方 SDK：pip install pxdex")
        self.port = port
        self.camera = camera
        self.c = DexH13Control()
        self.enabled = False
        self.tp = _TPShim(self)
        self._last_deg = [0.0] * 16

    # ---------- 生命周期 ----------
    def connect(self):
        t = self.c.activeHandy(self.port, self.camera)
        if t not in (1, 2):
            raise RuntimeError("激活灵巧手失败，返回 %r" % (t,))
        self.side = "左手" if t == 1 else "右手"
        try:
            self.c.initMotorPosition()
        except Exception:
            pass
        try:
            self.c.setMotorControlMode(ControlMode.POSITION_CONTROL_MODE)
        except Exception:
            pass
        return self

    def enable(self, settle: float = 2.0, verbose: bool = True):
        if self.enabled:
            return
        try:
            self.c.initMotorPosition()
        except Exception:
            pass
        try:
            self.c.setMotorControlMode(ControlMode.POSITION_CONTROL_MODE)
        except Exception:
            pass
        self.c.enableMotor()
        self.enabled = True
        time.sleep(settle)

    def disable(self):
        try:
            self.c.disableMotor()
        except Exception:
            pass
        self.enabled = False

    def close(self):
        try:
            self.c.disconnectHandy()
        except Exception:
            pass

    def version(self) -> str:
        try:
            return self.c.getFirmwareVersion().strip()
        except Exception:
            return ""

    # ---------- 读数 ----------
    def currents(self):
        try:
            _, d = self.c.getMotorActualCurrent()
            return [getattr(d, "motor%d_current" % i) for i in range(1, 14)]
        except Exception:
            return [0] * 13

    def angles_deg(self):
        try:
            out = []
            for f in self.c.getJointPositionsAngle():
                out += [f.joint1, f.joint2, f.joint3, f.joint4]
            return out
        except Exception:
            return [0.0] * 16

    def tactile_resultant(self, bins: int = 17):
        """给网页的粗粒度触觉：把 1140 点按顺序分箱取峰值。

        注意：SDK 的 getFingerTactile()（合力）在 2.0.2 上返回全零，
        只有 getAllTactileData() 是有效的，所以这里从全量阵列降维。
        """
        pts = self.tactile_full()
        if not pts:
            return [0] * bins
        vals = [abs(x) + abs(y) + abs(z) for (x, y, z) in pts]
        n = len(vals)
        out = []
        for b in range(bins):
            lo, hi = b * n // bins, (b + 1) * n // bins
            out.append(max(vals[lo:hi]) if hi > lo else 0)
        return out

    def tactile_full(self):
        """完整 1140 点，返回 [(x,y,z), ...]；失败返回空列表。"""
        try:
            _, pts, _ = self.c.getAllTactileData()
            return [(p.x, p.y, p.z) for p in pts]
        except Exception:
            return []

    # ---------- 动作 ----------
    def _apply(self, deg16, duration: float):
        """平滑下发 16 个角度（度）。"""
        start = list(self._last_deg)
        steps = max(1, int(duration * 30))
        for s in range(1, steps + 1):
            k = s / steps
            cur = [start[i] + (deg16[i] - start[i]) * k for i in range(16)]
            try:
                self.c.setJointPositionsRadian([math.radians(v) for v in cur])
            except Exception:
                pass
            time.sleep(duration / steps)
        # 末位再补两次，防止最后一帧被 SDK 丢掉（实测会有 invalid range 丢弃）
        for _ in range(2):
            try:
                self.c.setJointPositionsRadian([math.radians(v) for v in deg16])
            except Exception:
                pass
            time.sleep(0.15)
        self._last_deg = list(deg16)

    def move_to(self, targets, duration: float = 1.0, hz: float = 30.0):
        """项目刻度 13 个目标 -> SDK 的 16 个角度。

        joint1 是外展（abduction），实测给 10° 会被判非法，所以固定为 0；
        每根手指的电机依次映射到 joint2/3/4。
        """
        deg16 = [0.0] * 16
        for i, t in enumerate(targets[:13]):
            fg = MOTOR_FINGER[i]
            slot = FINGER_ORDER.index(fg) * 4
            idxs = [j for j in range(13) if MOTOR_FINGER[j] == fg]
            dim = idxs.index(i)
            joint = min(1 + dim, 3)          # joint2..4，跳过外展；多的那个夹到 joint4
            deg16[slot + joint] = _to_deg(t)
        if self.enabled:
            self._apply(deg16, duration)

    def play(self, name: str, duration: float = 1.0):
        preset = gestures.PRESETS.get(name)
        if preset is None:
            return
        self.move_to(preset, duration=duration)

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()