#!/usr/bin/env python3
"""猜拳：摄像头认人手 → DexH13 出手 → 判胜负

无桌面环境（headless），不弹窗：
  * 控制台实时打印每一轮
  * 全程录像存 mp4，跑完拉回本地看

用法：
  python3 games/rps.py --rounds 5
  python3 games/rps.py --rounds 5 --no-hand     # 只识别、不动手，调参用
"""
import argparse, collections, os, random, sys, time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dexh13 import DexH13

ZH = {"rock": "石头", "paper": "布", "scissors": "剪刀"}
ROBOT_MOVE = {"rock": "拳头", "paper": "零位", "scissors": "耶"}
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}  # key 赢 value

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("DEXH13_HAND_MODEL") or os.path.join(
    os.path.dirname(HERE), "models", "hand_landmarker.task")

# 21 个关键点的骨架连线
CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
               (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
               (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]


def dist(a, b):
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def classify(lm):
    """只用 食指/中指/无名/小指 判断，绕开左右手与拇指朝向的问题。

    原理：手指伸直时，指尖到手腕的距离明显大于指节(PIP)到手腕的距离。
    """
    wrist = lm[0]
    ext = [dist(lm[tip], wrist) > dist(lm[pip], wrist) * 1.10
           for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18))]
    i, m, r, p = ext
    if i and m and not r and not p:
        return "scissors"
    if i and m and r and p:
        return "paper"
    if not i and not m and not r and not p:
        return "rock"
    return None


def draw(frame, lm, h, w, color=(0, 255, 0)):
    pts = [(int(p.x * w), int(p.y * h)) for p in lm]
    for a, b in CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], color, 2)
    for x, y in pts:
        cv2.circle(frame, (x, y), 4, (0, 0, 255), -1)


class Referee:
    def __init__(self):
        self.me = self.bot = self.draw = 0

    def judge(self, mine, robot):
        if mine == robot:
            self.draw += 1
            return "draw"
        if BEATS[mine] == robot:
            self.me += 1
            return "me"
        self.bot += 1
        return "bot"

    def line(self):
        return "你 %d : %d 电脑（平 %d）" % (self.me, self.bot, self.draw)


def open_cam(idx, width, height):
    """打开摄像头。分辨率压到 640x480 是为了降低 USB 带宽——
    这颗 SPCA2650 挂在共享 hub 上，720p 时容易触发 USB reset。"""
    cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
    if not cap.isOpened():
        return cap
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--seconds", type=float, default=180)
    ap.add_argument("--settle", type=int, default=8, help="连续多少帧稳定才判一轮")
    ap.add_argument("--cooldown", type=float, default=2.5)
    ap.add_argument("--no-hand", action="store_true")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if not os.path.exists(MODEL):
        print("缺少模型文件：", MODEL)
        return 2


    cap = open_cam(args.camera, args.width, args.height)
    if not cap.isOpened():
        print("打不开摄像头 /dev/video%d —— 先确认 usb 摄像头插好（ls /dev/video*）" % args.camera)
        return 2

    ok, frame = cap.read()
    if not ok:
        print("摄像头能开但读不到画面")
        return 2
    h, w = frame.shape[:2]
    out_path = args.out or os.path.join(
        os.path.dirname(HERE), "data", "rps_%s.mp4" % time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    vw = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), 20, (w, h))
    print("画面 %dx%d，录像 -> %s" % (w, h, out_path))

    import mediapipe as mp
    from mediapipe.tasks import python as mpp
    from mediapipe.tasks.python import vision

    landmarker = vision.HandLandmarker.create_from_options(
        vision.HandLandmarkerOptions(
            base_options=mpp.BaseOptions(model_asset_path=MODEL),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.6,
            min_tracking_confidence=0.5,
        ))
    print("模型：", os.path.basename(MODEL))

    hand = None
    if not args.no_hand:
        hand = DexH13(port=os.environ.get("DEXH13_PORT") or None)
        try:
            hand.connect()
            hand.enable()
        except Exception as e:
            print("灵巧手没接上（%s），退回只看不动" % e)
            hand = None

    ref = Referee()
    recent = collections.deque(maxlen=args.settle)
    last_round = 0.0
    t0 = time.time()
    rounds = 0
    fps = 0.0
    timeline = []

    try:
        while rounds < args.rounds and time.time() - t0 < args.seconds:
            ok, frame = cap.read()
            if not ok:
                print("画面中断，尝试重连摄像头…")
                cap.release()
                for _ in range(6):
                    time.sleep(0.5)
                    cap = open_cam(args.camera, args.width, args.height)
                    if cap.isOpened():
                        ok, frame = cap.read()
                        if ok:
                            print("重连成功")
                            break
                if not ok:
                    print("摄像头一直没恢复，退出")
                    break
            tick = time.time()
            now_ms = int((tick - t0) * 1000)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            res = landmarker.detect_for_video(img, now_ms)

            mine = None
            if res.hand_landmarks:
                lm = res.hand_landmarks[0]
                mine = classify(lm)
                draw(frame, lm, h, w)

            recent.append(mine)
            stable = (len(recent) == args.settle and recent[0] is not None
                      and all(g == recent[0] for g in recent))

            if stable and time.time() - last_round > args.cooldown:
                rounds += 1
                robot = random.choice(["rock", "paper", "scissors"])
                winner = ref.judge(mine, robot)
                last_round = time.time()
                recent.clear()
                tag = {"me": "你赢", "bot": "电脑赢", "draw": "平"}[winner]
                print("[第 %d 轮] 你出 %s，手出 %s → %s    %s"
                      % (rounds, ZH[mine], ROBOT_MOVE[robot], tag, ref.line()))
                timeline.append((rounds, mine, robot, winner))
                if hand:
                    hand.play(ROBOT_MOVE[robot], duration=0.9)
                cv2.putText(frame, "ROUND %d  %s vs %s -> %s"
                            % (rounds, mine.upper(), robot.upper(), tag),
                            (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)

            cv2.putText(frame, "you: %s" % (mine or "?"), (20, h - 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
            cv2.putText(frame, "score %d : %d  draw %d" % (ref.me, ref.bot, ref.draw),
                        (20, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            vw.write(frame)

            dt = time.time() - tick
            fps = 0.9 * fps + 0.1 * (1.0 / dt if dt > 0 else 0)
    finally:
        cap.release()
        vw.release()
        landmarker.close()
        if hand:
            hand.disable()
            hand.close()

    print("-" * 52)
    print("结束：%s" % ref.line())
    print("平均 %.1f FPS，录像 %s" % (fps, out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())