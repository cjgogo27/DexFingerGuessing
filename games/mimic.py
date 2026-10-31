#!/usr/bin/env python3
"""跟随模仿：人手的连续动作 → DexH13 实时复现

不需要配合倒计时：手一进画面就开始跟随。
标定是「边跟边学」——程序持续记录每根手指屈曲的极值，
你做得越充分，映射越准；做得少也会用一个默认区间兜底。

映射：屈曲量 [开, 闭] → 0~1 → 乘以「拳头」手势里对应的电机值。
所以全开 = 全 0，握拳 = 拳头手势，中间线性过渡。

注：DexH13 是 4 指（拇指 + 食/中/无名），没有小指。
"""
import argparse, math, os, sys, time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dexh13 import DexH13, gestures

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("DEXH13_HAND_MODEL") or os.path.join(
    os.path.dirname(HERE), "models", "hand_landmarker.task")

FINGER_JOINTS = {
    "thumb":  [(1, 2, 3), (2, 3, 4)],
    "index":  [(0, 5, 6), (5, 6, 7), (6, 7, 8)],
    "middle": [(0, 9, 10), (9, 10, 11), (10, 11, 12)],
    "ring":   [(0, 13, 14), (13, 14, 15), (14, 15, 16)],
    "pinky":  [(0, 17, 18), (17, 18, 19), (18, 19, 20)],
}
FINGER_ORDER = ["thumb", "index", "middle", "ring", "pinky"]
MOTOR_FINGER = (["thumb"] * 4) + (["index"] * 3) + (["middle"] * 3) + (["ring"] * 3)
FIST = gestures.PRESETS["拳头"]
CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
               (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
               (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]

MIN_SPAN = 60.0     # 低于这个幅度认为标定不充分，走默认映射


def to_px(lm, w, h):
    return [(p.x * w, p.y * h) for p in lm]


def angle(a, b, c):
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return 180.0
    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def flexion(lm, w, h):
    pts = to_px(lm, w, h)
    return {n: sum(180.0 - angle(pts[a], pts[b], pts[c]) for a, b, c in t)
            for n, t in FINGER_JOINTS.items()}


def draw(frame, lm, w, h, ratios, spans):
    pts = [(int(x), int(y)) for x, y in to_px(lm, w, h)]
    for a, b in CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (0, 255, 0), 2)
    for p in pts:
        cv2.circle(frame, p, 3, (0, 0, 255), -1)
    y = 22
    for n in FINGER_ORDER:
        cv2.putText(frame, "%-7s %.2f  [%.0f]" % (n, ratios.get(n, 0.0), spans.get(n, 0.0)),
                    (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        y += 19


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--hz", type=float, default=25.0)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--no-hand", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(MODEL):
        print("缺少模型：", MODEL)
        return 2

    import mediapipe as mp
    from mediapipe.tasks import python as mpp
    from mediapipe.tasks.python import vision

    cap = cv2.VideoCapture(args.camera, cv2.CAP_V4L2)
    if not cap.isOpened():
        print("打不开摄像头")
        return 2
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    ok, frame = cap.read()
    if not ok:
        print("摄像头读不到画面")
        return 2
    h, w = frame.shape[:2]

    out_path = os.path.join(os.path.dirname(HERE), "data",
                            "mimic_%s.mp4" % time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    vw = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), 20, (w, h))

    landmarker = vision.HandLandmarker.create_from_options(
        vision.HandLandmarkerOptions(
            base_options=mpp.BaseOptions(model_asset_path=MODEL),
            running_mode=vision.RunningMode.VIDEO, num_hands=1,
            min_hand_detection_confidence=0.5, min_tracking_confidence=0.5))

    hand = None
    if not args.no_hand:
        hand = DexH13(port=os.environ.get("DEXH13_PORT") or None)
        try:
            hand.connect()
            hand.enable()
            hand.move_to([0] * 13, duration=0.8)
        except Exception as e:
            print("灵巧手没接上（%s），只录像不动手" % e)
            hand = None

    lo = {n: None for n in FINGER_ORDER}
    hi = {n: None for n in FINGER_ORDER}
    smooth = {n: 0.0 for n in FINGER_ORDER}
    last_target = [0] * 13
    t0 = time.time()
    last_send = 0.0
    last_report = 0.0
    n_frames = n_sent = n_hand = 0

    # 先等手出现再开始计时，避免远程操作时白跑一整轮
    print("等待手进入画面（最多 60 秒）…")
    while time.time() - t0 < 60:
        ok, frame = cap.read()
        if not ok:
            break
        _res = landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB,
                     data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)),
            int((time.time() - t0) * 1000))
        if _res.hand_landmarks:
            print("检测到手了，开始跟随 %.0f 秒（动得越多映射越准）" % args.seconds)
            break
        time.sleep(0.05)
    t_run = time.time()

    try:
        while time.time() - t_run < args.seconds:
            ok, frame = cap.read()
            if not ok:
                print("画面中断，退出")
                break
            now = time.time()
            n_frames += 1
            img = mp.Image(image_format=mp.ImageFormat.SRGB,
                           data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            res = landmarker.detect_for_video(img, int((now - t0) * 1000))

            ratios = {}
            if res.hand_landmarks:
                n_hand += 1
                f = flexion(res.hand_landmarks[0], w, h)
                for n in FINGER_ORDER:
                    lo[n] = f[n] if lo[n] is None else min(lo[n], f[n])
                    hi[n] = f[n] if hi[n] is None else max(hi[n], f[n])
                    span = hi[n] - lo[n]
                    if span < MIN_SPAN:
                        r = (f[n] - 10.0) / 250.0        # 默认映射
                    else:
                        r = (f[n] - lo[n]) / span
                    r = max(0.0, min(1.0, r))
                    smooth[n] = 0.7 * smooth[n] + 0.3 * r
                    ratios[n] = smooth[n]
                draw(frame, res.hand_landmarks[0], w, h, ratios,
                     {n: (hi[n] - lo[n]) if lo[n] is not None else 0 for n in FINGER_ORDER})
                cv2.putText(frame, "MIMIC", (10, h - 18),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

                if now - last_send >= 1.0 / args.hz:
                    last_send = now
                    target = [int(round(smooth[fing] * FIST[i]))
                              for i, fing in enumerate(MOTOR_FINGER)]
                    target = [max(-1000, min(5000, t)) for t in target]
                    target = [max(last_target[i] - 350, min(last_target[i] + 350, t))
                              for i, t in enumerate(target)]
                    last_target = target
                    if hand:
                        hand.tp.write_regs(0x2050, target)
                        n_sent += 1
            else:
                cv2.putText(frame, "NO HAND", (10, h - 18),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

            if now - last_report > 2.0:
                last_report = now
                sp = {n: (hi[n] - lo[n]) if lo[n] is not None else 0 for n in FINGER_ORDER}
                print("  t=%4.1fs  拇指%.2f 食指%.2f 中指%.2f 无名%.2f   幅度 拇指%.0f 食指%.0f 中指%.0f 无名%.0f"
                      % (now - t_run, smooth["thumb"], smooth["index"], smooth["middle"],
                         smooth["ring"], sp["thumb"], sp["index"], sp["middle"], sp["ring"]))
            vw.write(frame)
    finally:
        cap.release()
        vw.release()
        landmarker.close()
        if hand:
            try:
                hand.move_to([0] * 13, duration=0.8)
                hand.disable()
            finally:
                hand.close()

    print("帧 %d（含手 %d），下发 %d 次，录像 %s" % (n_frames, n_hand, n_sent, out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())