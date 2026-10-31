"""Modbus RTU 的 CRC16 校验（多项式 0xA001，初值 0xFFFF）。

上位机 pxdex-h13 用的就是这套：低字节在前，跟标准 Modbus 一致。
"""


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def append_crc(frame: bytes) -> bytes:
    """在帧尾追加 CRC（低字节在前）。"""
    c = crc16(frame)
    return frame + bytes((c & 0xFF, c >> 8))


def check_crc(frame: bytes) -> bool:
    """校验一整帧（含末尾 2 字节 CRC）。"""
    if len(frame) < 4:
        return False
    return crc16(frame[:-2]) == (frame[-2] | (frame[-1] << 8))