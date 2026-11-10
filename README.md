# DexFingerGuessing

**Rock-paper-scissors and imitation for a dexterous hand.** Throw a gesture, let it catch you.

<p align="center">
  <a href="http://10.16.10.28:8443">
    <img alt="Open the web console" src="https://img.shields.io/badge/%E2%96%B6%20OPEN%20CONSOLE-10.16.10.28%3A8443-1f6feb?style=for-the-badge">
  </a>
</p>

> The console link above points at the lab LAN (`https://10.16.10.28:8443`) and is not reachable
> from the internet. When deploying your own copy, replace it with `https://<your-ip>:8443`.

A Python driver layer for the PaXini **DexH13** dexterous hand, plus two interactive games,
a browser console, VR teleoperation, a teleop data recorder and a force-controlled grasp.

Works with two backends:

| Firmware | Backend | Notes |
|---|---|---|
| **1.4.x** | our own Modbus RTU driver | reverse engineered, no vendor library needed |
| **2.0.2+** | official `pxdex` SDK | required for the full tactile array |

<p align="center"><img src="docs/img/hand-tracking.jpg" width="620" alt="Live hand landmarks"></p>

## Hardware

| Part | Spec |
|---|---|
| Hand | PaXini DexH13, right hand, slave `0x79` (left `0x78`) |
| Power | 24–48 V DC, 3 A max |
| Link | RS485 / Modbus RTU, 8N1 |
| Baud | **460800** on firmware 1.x, **4 000 000** on firmware 2.x |
| USB-RS485 | Prolific **PL2303GR** — CH340 tops out at 3 Mbaud and will not work |
| Camera | any UVC camera, 720p is enough |

Wiring: `green -> A`, `white -> B`, `black -> GND`, shield floating.
Diagrams, power safety and troubleshooting live in [docs/HARDWARE.md](docs/HARDWARE.md).

## Quick start

```bash
pip install -r requirements.txt
bash scripts/fetch_model.sh      # downloads the 7.8 MB hand landmark model
python3 scripts/check_env.py     # checks deps / model / serial / camera
python3 web/app.py               # then open https://<host>:8000
```

`check_env.py` ticks every item off one by one, so a missing piece is obvious.
The camera-only parts work with no hardware attached.

## What you can do

**Web console** — live MJPEG feed with the 21-point hand skeleton, plus controls.

| Mode | Behaviour |
|---|---|
| Preview | look, do not move |
| Mimic | your fingers bend, the hand bends |
| RPS | recognises rock / paper / scissors and plays against you |
| **VR** | PICO / Quest hand tracking drives the hand (see below) |

The console also has a **gesture challenge** minigame (pure front end, no hand needed)
and a **return to zero** button that stops the current mode and homes all 13 joints.

**Command line**:

```bash
python3 games/rps.py --rounds 5        # rock-paper-scissors
python3 games/mimic.py --seconds 30    # imitation
python3 lessons/l1_probe.py            # start here: identify the hand
```

Six lessons under `lessons/l1..l6` walk from scanning and reading registers to enabling the
motors and recording data.

## VR teleoperation

The headset opens `/xr` in its browser and streams WebXR hand joints over a WebSocket;
the server retargets 25 joints down to the hand's 13 degrees of freedom.

```bash
# WebXR needs a secure context, so serve over TLS
export DEXH13_SSL_CERT=certs/cert.pem DEXH13_SSL_KEY=certs/key.pem
export DEXH13_WEB_PORT=8443
python3 web/app.py
```

On PICO the interaction mode must be **"controllers and hands switch automatically"**;
"controllers only" hides the hand input source entirely. When no hand data arrives the page
falls back to the controller trigger as a grip value, so the link can be tested before the
hand tracking is sorted out.

* (EN) `xr.html` requests an `XRWebGLLayer` and clears it every frame. Without that the
  compositor never receives a frame and the headset shows its loading spinner forever.

## Recording and force grasp

Two extra buttons in the console:

- **Start recording** writes `records/<timestamp>/trace.csv` at 30 Hz with the commanded
  targets, the measured joint positions, the motor currents and the retargeting ratios.
- **Force grasp** closes the fingers step by step and freezes a finger on the first contact
  signal — a tactile spike, a position stall, or a current rise. Three layers, in that order,
  so the hand stops as soon as it touches something instead of crushing it.

See [docs/DATA.md](docs/DATA.md) for the CSV layout and the tuning knobs.

## Tactile

Firmware 1.x exposes only a handful of tactile channels over Modbus (about 7 usable values,
3.9 Hz), which is why the full array used to be out of reach.

After upgrading the mainboard to **2.0.2** the official SDK returns the whole thing:

```
getAllTactileData()  ->  11 sensors, 1140 points (3 axes each), 11 temperatures
                         ~15 ms per call,  ~65 Hz
```

`tools/tactile_studio.py` renders those 1140 points as a strip image and serves it over MJPEG —
that is exactly the `1140 x T` representation used by tactile-compression work.

```bash
python3 tools/tactile_studio.py          # http://<host>:8666
curl -X POST http://<host>:8666/api/record/start
curl -X POST http://<host>:8666/api/record/stop   # -> records/tactile_*.npz (T,1140,3)
```

> Only one process can own the serial port, so the console and the tactile studio cannot
> run at the same time. Pick one.

## Firmware notes

Upgrading the mainboard changes the wire protocol underneath you:

- the hand leaves 460800 and settles at **4 000 000** baud
- the reverse-engineered Modbus registers stop answering
- the SDK becomes the only way to read the full tactile array

The driver probes several baud rates and picks the first one that returns a version string, and
the engine tries the SDK first and falls back to Modbus, so both firmware generations work
without editing code. Force the backend with `DEXH13_BACKEND=sdk|modbus`.

## Layout

```
dexh13/     driver (crc / protocol / transport / hand / gestures / sdk_hand / grasp)
games/      rps.py · mimic.py
web/        app.py + templates/{index,xr}.html  (Flask + MJPEG + WebSocket)
tools/      tactile_studio.py (full 1140-point tactile image)
lessons/    l1_probe -> l6_record
docs/       PROTOCOL.md · HARDWARE.md · DATA.md
scripts/    fetch_model.sh · check_env.py · recover_camera.sh
```

## Protocol notes

Standard Modbus RTU (FC03 / FC16, CRC low byte first). The register map and captured frames are
in [docs/PROTOCOL.md](docs/PROTOCOL.md). Three things that bite:

1. positions and currents are **signed int16** — reading them unsigned turns `-17` into `65519`
2. `0x2010` / `0x2020` are write-only and always read back as `0xFFFF`
3. enabling needs a five-step sequence; skip one and the joints simply do not move

On the SDK side: `joint1` (abduction) rejects non-zero values on this hand, so it is held at 0
and the motors map onto `joint2..4`; `getFingerTactile()` returns all zeros on 2.0.2 and the
full array has to come from `getAllTactileData()`.

## Known limitations

- **Four fingers** — thumb plus index/middle/ring, the pinky does not exist
- **No force control on firmware 1.x** — it lives in the 2.0 SDK
- **Monocular camera** — no depth, no calibration; moving the camera means recalibrating
- **One serial owner** — console, tactile studio and CLI scripts take turns

## Disclaimer

Unofficial project. The 1.x protocol was obtained by reverse engineering the `pxdex-h13` host
application, for interoperability and research only. Trademarks and copyrights belong to
PaXini.

## License

MIT for the code in this repository.
Dependencies: MediaPipe / OpenCV (Apache-2.0), pyserial (BSD), Flask (BSD).