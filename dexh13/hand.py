"""DexH13 高层封装：把寄存器细节包成「会用」的 API。"""
from __future__ import annotations

import time

from . import gestures as G
from . import protocol as P
from .transport import Transport, scan, list_serial_ports


class DexH13:
    """一只帕西尼 DexH13。

    典型用法：
        with DexH13(port="COM8") as h:
            h.enable()
            h.move_to([0, 0, 0, 0, 0, 0, 0, 0, 0, 300, 0, 0, 0])
            print(h.positions())
    """

    def __init__(self, port=None, baud=460800, addr=P.SLAVE_RIGHT,
                 timeout=0.15, limits=(P.POS_MIN, P.POS_MAX)):
        self.port = port
        self.baud = baud
        self.addr = addr
        self.timeout = timeout
        self.limits = limits
        self.tp = None

    # ---------- 连接 ----------
    def connect(self):
        if self.port is None:
            found = scan(verbose=False)
            if not found:
                raise RuntimeError(
                    "没扫到灵巧手。检查：电源是否上电、RS485 的 A/B 是否接反、"
                    "串口是否被上位机占用。")
            self.port, self.baud, self.addr, _ = found[0]
        # 固件 1.x 走 460800，2.x 走 4000000。两个都试，谁先回话用谁。
        candidates = [self.baud, 4000000, 460800, 1000000]
        seen = set()
        last_err = None
        for baud in candidates:
            if baud in seen:
                continue
            seen.add(baud)
            try:
                tp = Transport(self.port, baud, self.addr, self.timeout).open()
            except Exception as e:
                last_err = e
                continue
            self.tp, self.baud = tp, baud
            try:
                ver = self.version()
            except Exception as e:
                last_err, ver = e, None
            if ver:
                return self
            tp.close()
        raise RuntimeError("%s 无有效版本号（试过 %s）：%s"
                           % (self.port, sorted(seen), last_err))

    def close(self):
        if self.tp:
            self.tp.close()
            self.tp = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        try:
            self.disable()
        finally:
            self.close()

    @property
    def connected(self):
        return self.tp is not None and self.tp.ser is not None

    # ---------- 基础信息 ----------
    def version(self) -> str:
        return self.tp.version()

    # 位置 / 电流 / 错误码是 int16 有符号，温度 / 电压是正数
    SIGNED = {"position", "current", "error", "error2"}

    def info(self, key: str):
        """读某类电机信息块，返回 13 个数。"""
        return self.tp.read_regs(P.REG_INFO[key], P.N_MOTORS,
                                 signed=key in self.SIGNED)

    def positions(self):
        return self.info("position")

    def currents(self):
        return self.info("current")

    def temperatures(self):
        return self.info("temperature")

    def voltages(self):
        return self.info("voltage")

    def status(self):
        return self.info("status")

    def snapshot(self):
        return {
            "version": self.version(),
            "position": self.positions(),
            "current": self.currents(),
            "temperature": self.temperatures(),
            "voltage": self.voltages(),
        }

    # ---------- 使能 ----------
    def enable(self, settle=2.0, verbose=True):
        """复刻上位机【使能】的完整时序。

        失能 → 总使能 → 13 电机切位置模式 → 13 电机使能 → 清命令
        """
        t = self.tp
        t.write_regs(P.REG_ENABLE, [0])
        time.sleep(0.2)
        t.write_regs(P.REG_ENABLE, [1])
        time.sleep(0.2)
        t.write_regs(P.REG_MOTOR_MODE, [3] * P.N_MOTORS)
        time.sleep(0.2)
        t.write_regs(P.REG_MOTOR_CMD, [6] * P.N_MOTORS)
        time.sleep(settle)
        t.write_regs(P.REG_MOTOR_CMD, [0] * P.N_MOTORS)
        if verbose:
            print("已使能，等待 %0.1fs 稳定" % settle)

    def disable(self, verbose=False):
        """复刻上位机【关闭使能】：13 电机失能。"""
        if not self.connected:
            return
        self.tp.write_regs(P.REG_MOTOR_CMD, [7] * P.N_MOTORS)
        time.sleep(1.0)
        if verbose:
            print("已失能（电机不再出力，可用手轻拨）")

    # ---------- 运动 ----------
    def _clamp(self, values):
        lo, hi = self.limits
        return [max(lo, min(hi, int(v))) for v in values]

    @staticmethod
    def _check_len(values):
        if len(values) != P.N_MOTORS:
            raise ValueError("要 %d 个值，收到 %d 个" % (P.N_MOTORS, len(values)))

    def move_to(self, target, duration=1.0, hz=30.0):
        """从当前位姿线性插值到目标位姿。

        分步下发而不是一步跳过去，避免关节打到底、也保护减速箱。
        """
        target = self._clamp(target)
        self._check_len(target)
        start = self.positions()
        steps = max(1, int(duration * hz))
        for i in range(1, steps + 1):
            k = i / steps
            cur = [int(round(s + (t - s) * k)) for s, t in zip(start, target)]
            self.tp.write_regs(P.REG_MOTOR_TARGET, cur)
            time.sleep(1.0 / hz)
        return self.positions()

    def move_joint(self, index, value, duration=1.0):
        target = self.positions()
        target[index] = value
        return self.move_to(target, duration=duration)

    def home(self, duration=1.5):
        """回零位（全 0）。注意：这是「命令回到零位」，不是「把当前位置设为零点」。"""
        return self.move_to([0] * P.N_MOTORS, duration=duration)

    # ---------- 手势 ----------
    def play(self, name, duration=1.2, hold=0.0):
        if name not in G.PRESETS:
            raise KeyError("没有手势 %r，可选：%s" % (name, "、".join(G.PRESETS)))
        self.move_to(G.PRESETS[name], duration=duration)
        if hold:
            time.sleep(hold)

    def gesture_names(self):
        return list(G.PRESETS.keys())