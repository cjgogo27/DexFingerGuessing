#!/usr/bin/env python3
"""DexH13 网页控制台

浏览器里实时看摄像头画面 → 手摆好了点「开始」→ 选玩法 → 灵巧手开始跟/比。

  /            控制台页面
  /stream      MJPEG 实时流
  /api/status  状态 JSON
  /api/start   开始（POST {"mode": "mimic"|"rps"|"preview"}）
  /api/stop    停止

启动： python3 web/app.py  →  http://<服务器IP>:8000
"""
import csv
import datetime
import json
import math
import os
import random
import sys
import threading
import time
from collections import deque

import cv2

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from dexh13 import DexH13, gestures          # noqa: E402

from flask import Flask, Response, jsonify, make_response, render_template, request   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("DEXH13_HAND_MODEL") or os.path.join(
    os.path.dirname(HERE), "models", "hand_landmarker.task")
PORT = int(os.environ.get("DEXH13_WEB_PORT", "8000"))

FINGER_JOINTS = {
    "thumb":  [(1, 2, 3), (2, 3, 4)],
    "index":  [(0, 5, 6), (5, 6, 7), (6, 7, 8)],
    "middle": [(0, 9, 10), (9, 10, 11), (10, 11, 12)],
    "ring":   [(0, 13, 14), (13, 14, 15), (14, 15, 16)],
    "pinky":  [(0, 17, 18), (17, 18, 19), (18, 19, 20)],
}
FINGER_ORDER = ["thumb", "index", "middle", "ring", "pinky"]
FINGER_CN = {"thumb": "拇指", "index": "食指", "middle": "中指", "ring": "无名", "pinky": "小指"}
MOTOR_FINGER = (["thumb"] * 4) + (["index"] * 3) + (["middle"] * 3) + (["ring"] * 3)
FIST = gestures.PRESETS["拳头"]
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
ROBOT_MOVE = {"rock": "拳头", "paper": "零位", "scissors": "耶"}
CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
               (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
               (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]
MIN_SPAN = 60.0

LOCK = threading.Lock()
STATE = {
    "hand": False, "gesture": None, "ratios": {}, "spans": {},
    "fps": 0.0, "active": False, "mode": "preview",
    "score": {"me": 0, "bot": 0, "draw": 0},
    "last": "", "jpeg": None, "jpeg_id": 0, "lag_ms": 0, "proc_ms": 0, "cap_fps": 0.0, "hand_ok": False, "message": "",
    "tactile": [], "current": [], "pending": None,
}


# ---------------- VR（WebXR）----------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC = {"on": False, "dir": None, "n": 0, "f": None, "w": None, "t0": 0.0}

XR_LOCK = threading.Lock()
XR_STATE = {"t": 0.0, "targets": None, "ratios": {}, "hb_t": 0.0, "frames": 0,
            "ev": "", "err": "", "ev_t": 0.0, "build": ""}
try:
    from dexh13.retarget import XRRetargeter
    RETARGET = XRRetargeter()
except Exception:                      # 没有 VR 模块也不影响原有功能
    RETARGET = None


# ---------------- 几何 ----------------
def to_px(lm, w, h):
    return [(p.x * w, p.y * h) for p in lm]


def angle(a, b, c):
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return 180.0
    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def flexion(lm, w, h):
    pts = to_px(lm, w, h)
    return {n: sum(180.0 - angle(pts[a], pts[b], pts[c]) for a, b, c in t)
            for n, t in FINGER_JOINTS.items()}


def classify(lm, w, h):
    pts = to_px(lm, w, h)
    wrist = pts[0]
    d = lambda p, q: math.hypot(p[0] - q[0], p[1] - q[1])
    ext = [d(pts[tip], wrist) > d(pts[pip], wrist) * 1.10
           for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18))]
    i, m, r, p = ext
    if i and m and not r and not p:
        return "scissors"
    if i and m and r and p:
        return "paper"
    if not i and not m and not r and not p:
        return "rock"
    return None


def draw(frame, lm, w, h, ratios, spans):
    pts = [(int(x), int(y)) for x, y in to_px(lm, w, h)]
    for a, b in CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (0, 255, 0), 2)
    for p in pts:
        cv2.circle(frame, p, 3, (0, 0, 255), -1)
    y = 24
    for n in FINGER_ORDER:
        cv2.putText(frame, "%s %.2f" % (n[:3], ratios.get(n, 0.0)), (10, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        y += 18


# ---------------- 摄像头 ----------------
def pick_camera():
    """挑一个能真正出画面的摄像头。

    USB 重新枚举后 /dev/video0 常常变成 1、2，写死编号就会黑屏。
    优先用 DEXH13_CAM 指定的路径，否则从 /dev/video0..7 里实测。
    """
    pref = os.environ.get("DEXH13_CAM")
    if pref and os.path.exists(pref):
        return pref
    for i in range(8):
        path = "/dev/video%d" % i
        if not os.path.exists(path):
            continue
        cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
        if cap.isOpened():
            ok, _ = cap.read()
            cap.release()
            if ok:
                return path
        else:
            cap.release()
    return "/dev/video0"


class Camera:
    def __init__(self, idx=None, w=640, h=480):
        self.idx = idx if idx is not None else pick_camera()
        self.w, self.h = w, h
        self.fmt = "MJPG"
        self.cap = self._open(self.fmt)
        self.frame = None
        self.ts = 0.0
        self.last_cap = 0.0
        self.cap_fps = 0.0
        self.lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def _open(self, fourcc="MJPG"):
        """打开摄像头（不阻塞：不在构造时探测首帧）。

        便宜的 UVC 摄像头在反复开关后经常只剩默认格式能用，
        所以格式在重连时轮流尝试：MJPG <-> 驱动默认。
        """
        c = cv2.VideoCapture(self.idx, cv2.CAP_V4L2)
        if fourcc:
            c.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        c.set(cv2.CAP_PROP_FRAME_WIDTH, self.w)
        c.set(cv2.CAP_PROP_FRAME_HEIGHT, self.h)
        return c

    def _loop(self):
        fails = 0
        while True:
            ok, f = self.cap.read()
            if not ok:
                # 摄像头被拔出/复位时自动重开；失败次数越多等得越久，
                # 避免高频重试把 UVC 打成 EPROTO
                fails += 1
                time.sleep(min(0.3 * (2 ** min(fails, 4)), 5.0))
                # 连续失败：设备可能重新枚举换了编号，重新挑一次
                if (not self.cap.isOpened()) or fails >= 3:
                    self.cap.release()
                    try:
                        new_dev = pick_camera()
                        if new_dev:
                            self.idx = new_dev
                    except Exception:
                        pass
                    self.fmt = None if self.fmt == "MJPG" else "MJPG"
                    self.cap = self._open(self.fmt)
                    with LOCK:
                        STATE["message"] = "摄像头重连：%s" % self.idx
                continue
            fails = 0
            with self.lock:
                self.frame = f
                self.ts = time.time()
                if self.last_cap > 0:
                    d = self.ts - self.last_cap
                    if d > 0:
                        self.cap_fps = 0.9 * self.cap_fps + 0.1 / d 
                self.last_cap = self.ts

    def get(self):
        with self.lock:
            return (None if self.frame is None else self.frame.copy()), self.ts


# ---------------- 引擎 ----------------
class Engine(threading.Thread):
    def __init__(self, cam, landmarker):
        super().__init__(daemon=True)
        self.cam = cam
        self.lm = landmarker
        self.hand = None
        self.busy = False
        self.enabled = False
        self.lo = {n: None for n in FINGER_ORDER}
        self.hi = {n: None for n in FINGER_ORDER}
        self.smooth = {n: 0.0 for n in FINGER_ORDER}
        self.settle = deque(maxlen=8)
        self.last_round = 0.0
        self.last_send = 0.0
        self.t0 = time.time()
        self.fps = 0.0
        self.sent = 0
        self.last_ts = 0.0
        self.last_sense = 0.0
        self.last_rec = 0.0
        self.grasp = None          # 力控抓握任务（后台线程用）
        self.grasp_trace = []

    # --- 灵巧手 ---
    def connect_hand(self):
        try:
            h = DexH13(port=os.environ.get("DEXH13_PORT") or None)
            h.connect()
            self.hand = h
            with LOCK:
                STATE["hand_ok"] = True
                STATE["message"] = "灵巧手已连接 " + h.version()
        except Exception as e:
            self.hand = None
            with LOCK:
                STATE["hand_ok"] = False
                STATE["message"] = "灵巧手未连接：%s" % e

    def _do(self, fn):
        def run():
            self.busy = True
            try:
                fn()
            except Exception as e:
                with LOCK:
                    STATE["message"] = "执行出错：%s" % e
            finally:
                self.busy = False
        threading.Thread(target=run, daemon=True).start()

    def start_mode(self, mode):
        def go():
            if self.hand and not STATE["hand_ok"]:
                pass
            if self.hand:
                self.hand.enable()
                self.hand.move_to([0] * 13, duration=0.6)
                self.enabled = True
            with LOCK:
                STATE["active"] = True
                STATE["mode"] = mode
                STATE["score"] = {"me": 0, "bot": 0, "draw": 0}
                STATE["last"] = ""
            self.settle.clear()
        self._do(go)

    def stop_mode(self):
        def go():
            if self.hand:
                try:
                    self.hand.move_to([0] * 13, duration=0.6)
                    self.hand.disable()
                    self.enabled = False
                except Exception:
                    pass
            with LOCK:
                STATE["active"] = False
                STATE["mode"] = "preview"
                STATE["last"] = "已停止"
        self._do(go)

    def reset_pose(self):
        """复位：停掉当前玩法，13 个关节回零位，并保持使能。"""
        def go():
            with LOCK:
                STATE["active"] = False
                STATE["mode"] = "preview"
                STATE["last"] = ""
                STATE["score"] = {"me": 0, "bot": 0, "draw": 0}
            if self.hand:
                if not self.enabled:
                    self.hand.enable()
                    self.enabled = True
                self.hand.move_to([0] * 13, duration=1.5)
            with LOCK:
                STATE["message"] = "已复位到零位"
        self._do(go)

    def send(self, target):
        if not self.hand:
            return
        try:
            self.hand.tp.write_regs(0x2050, target)
            self.sent += 1
        except Exception:
            pass

    # --- 主循环 ---
    def run(self):
        prev = time.time()
        while True:
            # 待执行的抓握动作（与采集同线程，避免串口竞争）
            with LOCK:
                _act = STATE.get("pending")
                STATE["pending"] = None
            if _act and self.hand:
                if not self.enabled:
                    self.hand.enable()
                    self.enabled = True
                self.hand.move_to(gestures.PRESETS["拳头"] if _act == "close" else [0] * 13, duration=1.2)
                with LOCK:
                    STATE["message"] = "已闭合" if _act == "close" else "已张开"

            # ---- VR 模式：不依赖摄像头，直接下发头显给的目标 ----
            with LOCK:
                _mode = STATE["mode"]
                _active = STATE["active"]
            _now2 = time.time()
            if _mode == "vr" and _active and self.hand:
                if not self.enabled:
                    self.hand.enable()
                    self.enabled = True
                if _now2 - self.last_send >= 1.0 / 25:
                    self.last_send = _now2
                    with XR_LOCK:
                        _fresh = (_now2 - XR_STATE["t"] < 0.6) and XR_STATE["targets"]
                        _tgt = list(XR_STATE["targets"]) if _fresh else None
                    if _tgt:
                        self.send(_tgt)

            # ---- 数据录制（30 Hz）：目标 / 实际位置 / 电流 / VR 输入 ----
            if REC["on"] and self.hand and _now2 - self.last_rec >= 1.0 / 30:
                self.last_rec = _now2
                try:
                    pos = _to_signed(self.hand.tp.read_regs(0x1050, 13))
                    cur = _to_signed(self.hand.tp.read_regs(0x1060, 13))
                    with XR_LOCK:
                        tgt = list(XR_STATE["targets"] or [0] * 13)
                        rat = dict(XR_STATE["ratios"])
                    with LOCK:
                        _m = STATE["mode"]
                    REC["w"].writerow(
                        [round(_now2 - REC["t0"], 4), _m] + tgt + pos + cur +
                        [round(rat.get(k, 0.0), 4) for k in ("thumb", "index", "middle", "ring")])
                    REC["n"] += 1
                except Exception:
                    pass

            # 触觉 / 电流轮询：与摄像头无关，没画面也照采
            _now = time.time()
            if self.hand and _now - self.last_sense > 0.1:
                self.last_sense = _now
                try:
                    tac = [v - 65536 if v > 32767 else v
                           for v in self.hand.tp.read_regs(0x4000, 17)]
                    cur = [v - 65536 if v > 32767 else v
                           for v in self.hand.tp.read_regs(0x1060, 13)]
                    with LOCK:
                        STATE["tactile"] = tac
                        STATE["current"] = cur
                except Exception:
                    pass

            frame, ts = self.cam.get()
            if frame is None:
                time.sleep(0.01)
                continue
            # 同一帧不要重复推理，否则 CPU 空转、帧率虚高
            if ts == self.last_ts:
                time.sleep(0.004)
                continue
            self.last_ts = ts
            now = time.time()
            dt = now - prev
            prev = now
            _lag = (now - ts) * 1000.0          # 采集到现在多久（排队/驱动缓冲）
            _t_proc = time.time()
            self.fps = 0.9 * self.fps + 0.1 * (1.0 / dt if dt > 0 else 0)

            h, w = frame.shape[:2]
            import mediapipe as mp
            res = self.lm.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)),
                int((now - self.t0) * 1000))

            with LOCK:
                mode = STATE["mode"]
                active = STATE["active"]

            gesture = None
            ratios = {}
            if res.hand_landmarks:
                lm = res.hand_landmarks[0]
                f = flexion(lm, w, h)
                for n in FINGER_ORDER:
                    self.lo[n] = f[n] if self.lo[n] is None else min(self.lo[n], f[n])
                    self.hi[n] = f[n] if self.hi[n] is None else max(self.hi[n], f[n])
                    span = self.hi[n] - self.lo[n]
                    r = ((f[n] - 10.0) / 250.0) if span < MIN_SPAN else ((f[n] - self.lo[n]) / span)
                    r = max(0.0, min(1.0, r))
                    self.smooth[n] = 0.7 * self.smooth[n] + 0.3 * r
                    ratios[n] = self.smooth[n]
                gesture = classify(lm, w, h)
                draw(frame, lm, w, h, ratios,
                     {n: (self.hi[n] - self.lo[n]) for n in FINGER_ORDER})

            with LOCK:
                STATE["hand"] = bool(res.hand_landmarks)
                STATE["gesture"] = gesture
                STATE["ratios"] = ratios
                STATE["spans"] = {n: (self.hi[n] - self.lo[n]) if self.lo[n] is not None else 0
                                  for n in FINGER_ORDER}
                STATE["fps"] = round(self.fps, 1)

            # ---- 控制 ----
            if active and res.hand_landmarks:
                if mode == "mimic":
                    if now - self.last_send >= 1.0 / 25:
                        self.last_send = now
                        tgt = [int(round(self.smooth[fg] * FIST[i]))
                               for i, fg in enumerate(MOTOR_FINGER)]
                        self.send([max(-1000, min(5000, t)) for t in tgt])
                elif mode == "rps":
                    self.settle.append(gesture)
                    stable = (len(self.settle) == 8 and self.settle[0] is not None
                              and all(g == self.settle[0] for g in self.settle))
                    if stable and now - self.last_round > 2.5 and not self.busy:
                        self.last_round = now
                        mine = self.settle[0]
                        self.settle.clear()
                        robot = random.choice(["rock", "paper", "scissors"])
                        if mine == robot:
                            res_txt, key = "平", "draw"
                        elif BEATS[mine] == robot:
                            res_txt, key = "你赢", "me"
                        else:
                            res_txt, key = "电脑赢", "bot"
                        with LOCK:
                            STATE["score"][key] += 1
                            STATE["last"] = "%s vs %s → %s" % (mine, robot, res_txt)
                        if self.hand:
                            self._do(lambda: self.hand.play(ROBOT_MOVE[robot], duration=0.9))

            # ---- 叠字 + 编码 ----
            cv2.putText(frame, datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
                        (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            color = (0, 255, 0) if active else (160, 160, 160)
            cv2.putText(frame, "MODE: %s" % mode, (10, h - 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            if not res.hand_landmarks:
                cv2.putText(frame, "NO HAND", (10, h - 14),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            elif gesture:
                cv2.putText(frame, gesture.upper(), (w - 200, h - 14),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
            ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if ok:
                with LOCK:
                    STATE["jpeg"] = jpg.tobytes()
                STATE["jpeg_id"] += 1
                STATE["lag_ms"] = round(_lag)
                STATE["proc_ms"] = round((time.time() - _t_proc) * 1000)


# ---------------- Flask ----------------
app = Flask(__name__)

try:
    from flask_sock import Sock
    sock = Sock(app)
except Exception:                      # 没装 flask-sock 时退化为无 VR 模式
    sock = None

@app.route("/")
def index():
    resp = make_response(render_template("index.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.route("/stream")
def stream():
    """MJPEG 流：只在有新帧时推送，避免重复发同一张图把带宽吃满。"""
    def gen():
        last_id = -1
        while True:
            with LOCK:
                jpg = STATE["jpeg"]
                jid = STATE["jpeg_id"]
            if jpg and jid != last_id:
                last_id = jid
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " +
                       str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
            else:
                time.sleep(0.005)
    resp = Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Connection"] = "close"
    return resp


@app.route("/xr")
def xr_page():
    """头显页面。禁止缓存——否则头显浏览器会一直跑旧 JS。"""
    resp = make_response(render_template("xr.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def _to_signed(vals):
    return [v - 65536 if v > 32767 else v for v in vals]


def handle_xr(msg):
    """收到头显数据：重定向并写入共享状态。

    没有 hand 字段也接受——那是心跳，用来判断"链路通不通"。
    """
    d = json.loads(msg)
    now = time.time()
    with XR_LOCK:
        XR_STATE["hb_t"] = now
        XR_STATE["frames"] += 1
        if d.get("v"):
            XR_STATE["build"] = str(d["v"])[:20]
        if d.get("ev"):
            XR_STATE["ev"] = str(d["ev"])[:40]
            XR_STATE["ev_t"] = now
        if d.get("err"):
            XR_STATE["err"] = str(d["err"])[:200]
    grip = d.get("grip")
    if grip is not None:
        # 手柄扳机：0=张开，1=握紧，所有手指同步
        g = max(0.0, min(1.0, float(grip)))
        tgt = [int(round(g * FIST[i])) for i in range(13)]
        with XR_LOCK:
            XR_STATE["t"] = now
            XR_STATE["targets"] = tgt
            XR_STATE["ratios"] = {f: round(g, 3) for f in ("thumb", "index", "middle", "ring")}
        with LOCK:
            if STATE["mode"] != "vr":
                STATE["mode"] = "vr"
        return
    joints = d.get("hand")
    if not joints:
        return
    if RETARGET is None or len(joints) != 25:
        return
    tgt, ratios = RETARGET.update(joints)
    with XR_LOCK:
        XR_STATE["t"] = time.time()
        XR_STATE["targets"] = tgt
        XR_STATE["ratios"] = ratios
    # 收到 VR 数据就自动把模式切到 vr，省得用户漏选
    with LOCK:
        if STATE["mode"] != "vr":
            STATE["mode"] = "vr"


@app.route("/api/xr_frame", methods=["POST"])
def api_xr_frame():
    """HTTP 后备通道：wss 被证书挡住时用这个。"""
    try:
        handle_xr(request.get_data(as_text=True))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


if sock:
    @sock.route("/ws/xr")
    def ws_xr(ws):
        while True:
            msg = ws.receive()
            if msg is None:
                break
            try:
                handle_xr(msg)
            except Exception:
                continue


@app.route("/api/record/start", methods=["POST"])
def api_rec_start():
    if REC["on"]:
        return jsonify({"ok": False, "error": "已经在录制"})
    d = request.get_json(silent=True) or {}
    name = d.get("name") or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = os.path.join(BASE_DIR, "records", name)
    os.makedirs(outdir, exist_ok=True)
    f = open(os.path.join(outdir, "trace.csv"), "w", newline="", encoding="utf-8")
    w = csv.writer(f)
    w.writerow(["t", "mode"] + ["tgt%d" % i for i in range(13)] +
               ["pos%d" % i for i in range(13)] + ["cur%d" % i for i in range(13)] +
               ["r_thumb", "r_index", "r_middle", "r_ring"])
    REC.update({"on": True, "dir": outdir, "n": 0, "f": f, "w": w, "t0": time.time()})
    return jsonify({"ok": True, "dir": outdir})


@app.route("/api/record/stop", methods=["POST"])
def api_rec_stop():
    if REC["on"]:
        try:
            REC["f"].close()
        except Exception:
            pass
        REC.update({"on": False, "f": None, "w": None})
    return jsonify({"ok": True, "n": REC["n"], "dir": REC["dir"]})


@app.route("/api/grasp", methods=["POST"])
def api_grasp():
    if not ENG or not ENG.hand:
        return jsonify({"ok": False, "error": "灵巧手未连接"}), 503
    if ENG.grasp:
        return jsonify({"ok": False, "error": "抓握进行中"})
    d = request.get_json(silent=True) or {}
    try:
        from dexh13.grasp import ForceGrasp
    except Exception as e:
        return jsonify({"ok": False, "error": "缺少 grasp 模块: %s" % e}), 500

    def job():
        g = ForceGrasp(ENG.hand,
                       delta=int(d.get("delta", 150)),
                       step=int(d.get("step", 80)),
                       hold=float(d.get("hold", 1.2)),
                       use_tactile=bool(d.get("tactile", True)),
                       tac_delta=int(d.get("tac_delta", 800)))
        ENG.grasp = g
        try:
            if not ENG.enabled:
                ENG.hand.enable()
                ENG.enabled = True
            res = g.run()
            ENG.grasp_trace = g.trace[-200:]
            with LOCK:
                STATE["message"] = "抓握完成：%d 指触发，%.1fs" % (len(res["frozen"]), res["elapsed"])
            print("grasp:", res)
        except Exception as e:
            with LOCK:
                STATE["message"] = "抓握出错：%s" % e
        finally:
            ENG.grasp = None

    threading.Thread(target=job, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    with LOCK:
        s = {k: v for k, v in STATE.items() if k != "jpeg"}
    s["ratios"] = {FINGER_CN.get(k, k): round(v, 2) for k, v in s["ratios"].items()}
    now = time.time()
    s["rec"] = {"on": REC["on"], "n": REC["n"], "dir": REC["dir"]}
    s["grasp"] = {"on": bool(ENG and ENG.grasp), "trace": (ENG.grasp_trace[-60:] if ENG else [])}
    s["vr"] = {"age": round(now - XR_STATE["t"], 2) if XR_STATE["t"] else None,
               "hb_age": round(now - XR_STATE["hb_t"], 2) if XR_STATE["hb_t"] else None,
               "frames": XR_STATE["frames"],
               "ev": XR_STATE["ev"],
               "build": XR_STATE["build"],
               "ev_age": round(now - XR_STATE["ev_t"], 1) if XR_STATE["ev_t"] else None,
               "err": XR_STATE["err"],
               "ratios": {k: round(v, 2) for k, v in XR_STATE["ratios"].items()}}
    return jsonify(s)


@app.route("/api/quick")
def api_quick():
    """给手势小游戏用的轻量接口（高频轮询，只回必要字段）。"""
    with LOCK:
        return jsonify({"hand": STATE["hand"], "gesture": STATE["gesture"],
                        "active": STATE["active"], "mode": STATE["mode"],
                        "tactile": STATE["tactile"], "current": STATE["current"]})


@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "mimic")
    if mode not in ("mimic", "rps", "preview", "vr"):
        return jsonify({"ok": False, "error": "mode?"}), 400
    if not ENG:
        return jsonify({"ok": False, "error": "engine not running"}), 503
    ENG.start_mode(mode)
    return jsonify({"ok": True, "mode": mode})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    if ENG:
        ENG.stop_mode()
    return jsonify({"ok": True})


@app.route("/api/gripper", methods=["POST"])
def api_gripper():
    """手动闭合 / 张开，用于观察触觉与电流。"""
    data = request.get_json(silent=True) or {}
    with LOCK:
        STATE["pending"] = "close" if data.get("close") else "open"
    return jsonify({"ok": True})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """复位：回到零位。"""
    if not ENG:
        return jsonify({"ok": False, "error": "engine not running"}), 503
    ENG.reset_pose()
    return jsonify({"ok": True})


def main():
    global ENG
    if not os.path.exists(MODEL):
        print("缺少模型：", MODEL)
        return 2
    import mediapipe as mp
    from mediapipe.tasks import python as mpp
    from mediapipe.tasks.python import vision

    landmarker = vision.HandLandmarker.create_from_options(
        vision.HandLandmarkerOptions(
            base_options=mpp.BaseOptions(model_asset_path=MODEL),
            running_mode=vision.RunningMode.VIDEO, num_hands=1,
            min_hand_detection_confidence=0.5, min_tracking_confidence=0.5))

    cam = Camera(int(os.environ.get("DEXH13_CAM", "0")))
    for _ in range(6):                        # 最多等 3 秒，没画面也照常起服务
        if cam.get()[0] is not None:
            break
        time.sleep(0.5)
    if cam.get()[0] is None:
        print("警告：摄像头暂无画面，服务照常启动（页面会显示 NO HAND）")

    ENG = Engine(cam, landmarker)
    ENG.connect_hand()
    ENG.start()
    cert = os.environ.get("DEXH13_SSL_CERT")
    key = os.environ.get("DEXH13_SSL_KEY")
    if cert and key and os.path.exists(cert) and os.path.exists(key):
        print("控制台已启动： https://0.0.0.0:%d  (VR 页面 /xr)" % PORT)
        app.run(host="0.0.0.0", port=PORT, threaded=True, debug=False,
                use_reloader=False, ssl_context=(cert, key))
    else:
        print("控制台已启动： http://0.0.0.0:%d  (VR 需要 HTTPS，见 README)" % PORT)
        app.run(host="0.0.0.0", port=PORT, threaded=True, debug=False, use_reloader=False)
    return 0


ENG = None
if __name__ == "__main__":
    sys.exit(main())