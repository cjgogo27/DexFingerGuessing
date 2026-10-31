"""串口收发层：只管一问一答，不懂业务语义。"""
from __future__ import annotations

import time

from . import protocol as P
from .crc import check_crc


class HandTimeout(Exception):
    """等不到手的回应。"""


class ModbusException(Exception):
    """手回了异常帧（功能码最高位置 1）。"""

    def __init__(self, fc: int, code: int):
        self.fc = fc
        self.code = code
        super().__init__("设备返回异常：功能码 0x%02X，异常码 %d" % (fc, code))


class Transport:
    def __init__(self, port: str, baud: int = 460800,
                 addr: int = P.SLAVE_RIGHT, timeout: float = 0.15):
        self.port = port
        self.baud = baud
        self.addr = addr
        self.timeout = timeout
        self.ser = None

    # ---------- 生命周期 ----------
    def open(self):
        import serial
        self.ser = serial.Serial(
            self.port, self.baud, bytesize=8, parity="N", stopbits=1,
            timeout=self.timeout, write_timeout=1.0,
            xonxoff=False, rtscts=False, dsrdtr=False,
        )
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        return self

    def close(self):
        if self.ser is not None and self.ser.is_open:
            self.ser.close()
        self.ser = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # ---------- 底层读写 ----------
    def _read_exact(self, n: int) -> bytes:
        buf = b""
        deadline = time.time() + self.timeout
        while len(buf) < n:
            chunk = self.ser.read(n - len(buf))
            if chunk:
                buf += chunk
            elif time.time() > deadline:
                break
        return buf

    def _transact(self, frame: bytes, kind: str) -> bytes:
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()

        hdr = self._read_exact(3)
        if len(hdr) < 3:
            raise HandTimeout("无响应（只收到 %d 字节）" % len(hdr))
        if hdr[0] != self.addr:
            raise HandTimeout("从站地址不匹配：%d != %d" % (hdr[0], self.addr))
        if hdr[1] & 0x80:
            self._read_exact(2)
            raise ModbusException(hdr[1] & 0x7F, hdr[2])

        if kind == "read":
            n = hdr[2]                      # 字节数域
            rest = self._read_exact(n + 2)  # 数据 + CRC
        else:
            rest = self._read_exact(5)      # 写回包固定 8 字节
        return hdr + rest

    # ---------- 业务原语 ----------
    def read_regs(self, reg: int, count: int, signed: bool = False):
        payload = self._transact(P.build_read(self.addr, reg, count), "read")
        if not check_crc(payload):
            raise HandTimeout("CRC 校验失败：%s" % payload.hex())
        data = payload[3:-2]
        return [int.from_bytes(data[i:i + 2], "big", signed=signed)
                for i in range(0, len(data) - 1, 2)]

    def write_regs(self, reg: int, values) -> None:
        payload = self._transact(P.build_write(self.addr, reg, values), "write")
        if not check_crc(payload):
            raise HandTimeout("CRC 校验失败：%s" % payload.hex())

    # ---------- 便捷包装 ----------
    def version(self) -> str:
        regs = self.read_regs(P.REG_VERSION, 14)
        raw = b"".join(r.to_bytes(2, "big") for r in regs)
        return raw.split(b"\x00")[0].decode("ascii", "ignore").strip()

    def ping(self) -> bool:
        try:
            return bool(self.version())
        except (HandTimeout, ModbusException, OSError):
            return False


def list_serial_ports():
    """列出本机所有串口。"""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [(p.device, p.description) for p in list_ports.comports()]


def scan(bauds=(460800, 4000000, 921600, 115200),
         addrs=(P.SLAVE_RIGHT, P.SLAVE_LEFT),
         ports=None, verbose=True):
    """扫描「端口 × 波特率 × 从站地址」，找到能报版本的组合。

    返回 [(port, baud, addr, version), ...]
    """
    found = []
    ports = ports or list_serial_ports()
    # 优先只扫 USB 转串口，避免在 Linux 上把几十个 ttyS* 全试一遍
    usb_only = [p for p in ports
                if any(k in p[0] for k in ("ttyUSB", "ttyACM", "ttyAMA", "COM", "usbserial"))]
    ports = usb_only or ports
    for port, desc in ports:
        for baud in bauds:
            for addr in addrs:
                t = Transport(port, baud, addr, timeout=0.12)
                try:
                    t.open()
                    ver = t.version()
                except Exception:
                    continue
                finally:
                    t.close()
                if ver:
                    found.append((port, baud, addr, ver))
                    if verbose:
                        print("  命中  %-14s %7d  0x%02X  %s" % (port, baud, addr, ver))
    return found

def default_port():
    """猜一个可用的串口：优先 USB 转串口。

    Windows 上返回 COMx，Linux 上返回 /dev/ttyUSBx。
    """
    ports = list_serial_ports()
    usb = [p for p in ports
           if any(k in p[0] for k in ("ttyUSB", "ttyACM", "ttyAMA", "COM", "usbserial"))]
    if usb:
        return usb[0][0]
    return ports[0][0] if ports else None