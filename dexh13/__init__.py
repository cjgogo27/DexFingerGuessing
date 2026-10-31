"""dexh13 —— 帕西尼 DexH13 灵巧手的独立 Python 控制库。

不依赖官方 SDK（官方只有 Linux 版），直接走 RS485/Modbus RTU。
"""
from .hand import DexH13
from . import gestures
from .transport import scan, list_serial_ports, default_port

__version__ = "0.1.0"
__all__ = ["DexH13", "gestures", "scan", "list_serial_ports", "default_port"]