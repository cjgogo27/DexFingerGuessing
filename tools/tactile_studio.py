#!/usr/bin/env python3
"""触觉图像工具：把 DexH13 的 1140 个测点渲染成「触觉图像」。

这正是 TacCompress 那篇论文的输入形式：把 1140 个测点当成一条 1140 宽的图，
随时间叠成 1140 × T 的二维图像。

依赖官方 SDK（pxdex），因为它才提供全量阵列；Modbus 寄存器只有 11 个合力。

用法：
    python3 tools/tactile_studio.py            # 打开 http://<IP>:8666
    python3 tools/tactile_studio.py --record   # 同时把数据存到 records/tactile_*.npz
"""
from __future__ import annotations

import argparse
import os
import threading
import time

import numpy as np

try:
    from flask import Flask, Response, jsonify
except ImportError:
    Flask = None

from pxdex.dh13 import DexH13Control

N_POINTS = 1140
AXIS = ("x", "y", "z")

app = Flask(__name__) if Flask else None

STATE = {
    "mag": np.zeros(N_POINTS, dtype=np.float32),   # 每个测点的力大小
    "xyz": np.zeros((N_POINTS, 3), dtype=np.float32),
    "frame": None,
    "t": 0.0,
    "hz": 0.0,
    "frames": 0,
    "recording": False,
    "recorded": [],
}
LOCK = threading.Lock()
HAND = None


def normalize(mag: np.ndarray) -> np.ndarray:
    """把力大小映射到 0~255，用固定量程避免颜色随场景跳变。"""
    v = np.clip(mag / 300.0, 0.0, 1.0)
    return (v * 255).astype(np.uint8)


def render(mag: np.ndarray, h: int = 160) -> bytes:
    """1140 宽的条形图，放大成 h 像素高。"""
    import cv2
    strip = normalize(mag).reshape(1, N_POINTS)
    img = cv2.resize(strip, (N_POINTS, h), interpolation=cv2.INTER_NEAREST)
    color = cv2.applyColorMap(img, cv2.COLORMAP_INFERNO)
    cv2.putText(color, "300+", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.putText(color, "0", (6, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    ok, jpg = cv2.imencode(".jpg", color, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    return jpg.tobytes() if ok else b""


def poll():
    global HAND
    prev = time.time()
    while True:
        t0 = time.time()
        try:
            _, pts, _ = HAND.getAllTactileData()
        except Exception:
            time.sleep(0.05)
            continue
        xyz = np.array([[p.x, p.y, p.z] for p in pts], dtype=np.float32)
        if xyz.shape[0] != N_POINTS:
            time.sleep(0.05)
            continue
        mag = np.linalg.norm(xyz, axis=1)
        dt = max(t0 - prev, 1e-4)
        prev = t0
        with LOCK:
            STATE["mag"], STATE["xyz"] = mag, xyz
            STATE["frame"] = render(mag)
            STATE["t"] = t0
            STATE["frames"] += 1
            STATE["hz"] = 0.9 * STATE["hz"] + 0.1 / dt
            if STATE["recording"]:
                STATE["recorded"].append(xyz.copy())


if app:
    @app.route("/")
    def index():
        return Response(
            "<meta charset=utf-8><style>body{background:#0b0d12;color:#e6e8ee;"
            "font-family:sans-serif;text-align:center}h2{margin:18px}</style>"
            "<h2>DexH13 触觉图像 · 1140 测点</h2>"
            "<img src='/stream' style='width:96%;max-width:1400px;image-rendering:pixelated'>"
            "<p id=s></p><script>setInterval(async()=>{const d=await (await fetch('/api/status')).json();"
            "document.getElementById('s').textContent=`${d.hz.toFixed(1)} Hz · 最大力 ${d.max.toFixed(0)}"
            " · 非零点 ${d.nz} · ${d.recording?'录制中 '+d.frames_rec+' 帧':''}`},500)</script>",
            mimetype="text/html; charset=utf-8")

    @app.route("/stream")
    def stream():
        def gen():
            last = -1
            while True:
                with LOCK:
                    jpg = STATE["frame"]
                    n = STATE["frames"]
                if jpg and n != last:
                    last = n
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " +
                           str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                else:
                    time.sleep(0.005)
        r = Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")
        r.headers["Cache-Control"] = "no-store"
        return r

    @app.route("/api/status")
    def api_status():
        with LOCK:
            return jsonify({
                "hz": round(STATE["hz"], 1),
                "max": float(STATE["mag"].max()),
                "nz": int((STATE["mag"] > 0.5).sum()),
                "recording": STATE["recording"],
                "frames_rec": len(STATE["recorded"]),
                "frames": STATE["frames"],
            })

    @app.route("/api/record/start", methods=["POST"])
    def rec_start():
        with LOCK:
            STATE["recorded"] = []
            STATE["recording"] = True
        return jsonify({"ok": True})

    @app.route("/api/record/stop", methods=["POST"])
    def rec_stop():
        with LOCK:
            STATE["recording"] = False
            data = np.stack(STATE["recorded"]) if STATE["recorded"] else np.zeros((0, N_POINTS, 3))
        os.makedirs("records", exist_ok=True)
        path = "records/tactile_%s.npz" % time.strftime("%Y%m%d-%H%M%S")
        np.savez_compressed(path, tactile=data)
        return jsonify({"ok": True, "path": path, "shape": list(data.shape)})


def main():
    global HAND
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8666)
    ap.add_argument("--cert")
    ap.add_argument("--key")
    args = ap.parse_args()

    HAND = DexH13Control()
    t = HAND.activeHandy(os.environ.get("DEXH13_PORT", "/dev/ttyUSB0"), "none")
    print("灵巧手:", HALF(t), "| 固件:", HAND.getFirmwareVersion())
    threading.Thread(target=poll, daemon=True).start()
    time.sleep(1)

    if args.cert and args.key:
        app.run(host="0.0.0.0", port=args.port, ssl_context=(args.cert, args.key), threaded=True)
    else:
        app.run(host="0.0.0.0", port=args.port, threaded=True)


def HALF(t):
    return "右手" if t == 2 else ("左手" if t == 1 else str(t))


if __name__ == "__main__":
    main()