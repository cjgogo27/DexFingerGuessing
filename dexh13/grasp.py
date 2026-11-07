"""电流闭环抓握：逐级收紧，等到电流上升就停，用来"捏住但不捏碎"。

思路：
  1. 空载先采一段电流当基线（每根手指一个）
  2. 每帧把目标值加一点点，手指慢慢合拢
  3. 某根手指的电流偏离基线超过阈值 → 认为"碰到东西了" → 冻结那根手指
  4. 全部冻结或超时 → 保持 hold 秒
"""
from __future__ import annotations

import statistics
import time

REG_POS = 0x1050
REG_CUR = 0x1060


def _signed(vals):
    return [v - 65536 if v > 32767 else v for v in vals]


class ForceGrasp:
    def __init__(self, hand, delta=150, step=80, hz=25.0,
                 hold=1.2, max_time=8.0, limit=5000, settle=0.4,
                 stall_n=3, stall_eps=3):
        self.hand = hand
        self.delta = delta          # 电流超过基线多少判定为"接触"
        self.step = step            # 每帧增加的电机单位
        self.dt = 1.0 / hz
        self.hold = hold
        self.max_time = max_time
        self.limit = limit
        self.settle = settle
        self.stall_n = stall_n        # 连续多少帧位置不动就判定"顶住了"
        self.stall_eps = stall_eps    # 位置变化小于多少算不动
        self.trace = []
        self.stopped_at = []

    def _read_cur(self):
        return _signed(self.hand.tp.read_regs(REG_CUR, 13))

    def _read_pos(self):
        return _signed(self.hand.tp.read_regs(REG_POS, 13))

    def run(self):
        t0 = time.time()
        # 1) 空载基线
        samples = []
        for _ in range(6):
            try:
                samples.append(self._read_cur())
            except Exception:
                pass
            time.sleep(0.03)
        base = ([statistics.median(s[i] for s in samples) for i in range(13)]
                if samples else [0] * 13)

        # 2) 从张开开始
        self.hand.move_to([0] * 13, duration=max(self.settle, 0.4))
        time.sleep(self.settle)

        targets = [0] * 13
        frozen = [False] * 13
        stall = [0] * 13
        last_pos = None
        last = time.time()

        while time.time() - t0 < self.max_time:
            now = time.time()
            if now - last < self.dt:
                time.sleep(0.002)
                continue
            last = now
            try:
                cur = self._read_cur()
                pos = self._read_pos()
            except Exception:
                time.sleep(0.05)
                continue

            for i in range(13):
                if frozen[i]:
                    continue
                # 判据一：电流明显偏离基线（顶到硬物 / 到头）
                if abs(cur[i] - base[i]) > self.delta:
                    frozen[i] = True
                    self.stopped_at.append(i)
                    continue
                # 判据二：目标在涨，但位置连续不动（顶到软物）
                if (last_pos is not None and targets[i] > 200
                        and abs(pos[i] - last_pos[i]) < self.stall_eps):
                    stall[i] += 1
                    if stall[i] >= self.stall_n:
                        frozen[i] = True
                        self.stopped_at.append(i)
                        continue
                else:
                    stall[i] = 0
                if targets[i] < self.limit:
                    targets[i] = min(self.limit, targets[i] + self.step)

            try:
                self.hand.tp.write_regs(0x2050, targets)
            except Exception:
                pass
            last_pos = pos
            self.trace.append({
                "t": round(now - t0, 3),
                "tgt": list(targets),
                "cur": list(cur),
                "n_frozen": sum(frozen),
            })

            if all(frozen):
                break

        # 3) 保持
        time.sleep(self.hold)
        return {
            "elapsed": round(time.time() - t0, 2),
            "frozen": [i for i, f in enumerate(frozen) if f],
            "targets": targets,
            "baseline": base,
            "samples": len(self.trace),
        }