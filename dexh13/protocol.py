"""DexH13 寄存器地图与帧构造。

来源：对 pxdex-h13 v1.0.28（Electron，Vue 打包产物）的逆向记录，
并用真机逐条验证过。帧格式为标准 Modbus RTU（FC03 / FC16）。
"""
from .crc import append_crc

# ---- 从站地址（由上位机的 handType 决定）----
SLAVE_RIGHT = 0x79   # 121，右手
SLAVE_LEFT = 0x78    # 120，左手

FC_READ = 0x03
FC_WRITE = 0x10

N_MOTORS = 13        # 拇指 4 + 食指 3 + 中指 3 + 无名指 3

# ---- 寄存器 ----
REG_VERSION = 0x1000      # 读 14 个寄存器 → ASCII 版本串
REG_ENABLE = 0x1021       # 写 1/0 → 手总使能 / 失能
REG_MOTOR_MODE = 0x2010   # 写 13 个：3 = 位置模式
REG_MOTOR_CMD = 0x2020    # 写 13 个：6=使能 7=失能 8=置零点 0=清命令
REG_MOTOR_TARGET = 0x2050 # 写 13 个：目标位置
REG_DEV_CMD = 0x2000      # 单寄存器：0x40=重启 0x80=清电机错误
REG_SENSOR_DATA = 0x4000  # 读 17 个：触觉数据（分页）

# 电机信息块：每个都是连读 13 个寄存器，一个电机一个值
REG_INFO = {
    "position": 0x1050,
    "current": 0x1060,      # 上位机里叫 electricity，单位 mA
    "error": 0x1070,
    "error2": 0x1080,
    "status": 0x1090,
    "temperature": 0x10A0,  # ℃
    "voltage": 0x10B0,
}

# 位置量纲：上位机滑块直接用整数，手势库里的取值在 -600 ~ 5000 之间
POS_MIN = -1000
POS_MAX = 5000


def build_read(addr: int, reg: int, count: int) -> bytes:
    """FC03：读保持寄存器。"""
    body = bytes((addr, FC_READ, reg >> 8 & 0xFF, reg & 0xFF,
                  count >> 8 & 0xFF, count & 0xFF))
    return append_crc(body)


def build_write(addr: int, reg: int, values) -> bytes:
    """FC16：连续写多个寄存器，每个值 2 字节大端。"""
    values = list(values)
    n = len(values)
    body = bytes((addr, FC_WRITE, reg >> 8 & 0xFF, reg & 0xFF,
                  n >> 8 & 0xFF, n & 0xFF, n * 2 & 0xFF))
    body += b"".join(int(v).to_bytes(2, "big", signed=True) for v in values)
    return append_crc(body)