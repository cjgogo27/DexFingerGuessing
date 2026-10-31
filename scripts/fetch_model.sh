#!/usr/bin/env bash
# 下载 MediaPipe 手部关键点模型
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)/models"
URL="https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
mkdir -p "$DIR"
if [ -f "$DIR/hand_landmarker.task" ]; then
  echo "已存在：$DIR/hand_landmarker.task"
  exit 0
fi
echo "下载中…"
curl -L --max-time 300 -o "$DIR/hand_landmarker.task" "$URL"
echo "完成：$(du -h "$DIR/hand_landmarker.task" | cut -f1)"