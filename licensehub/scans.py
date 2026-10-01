"""扫描件模块：本地保存 + 纯标准库 PNG 生成/读取（无第三方依赖）。

样例扫描件由内置 5x7 点阵字库合成，包含竖版（营业执照式）与
横版（许可证式）两种比例；模糊样例用降采样再放大模拟。
"""
from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path

# 测试/演示快速模式：样例件按 1/4 尺寸渲染（业务逻辑与 PNG 结构完全相同）
_FAST = os.environ.get("LICENHUB_FAST_SCAN") == "1"

# ---------- 5x7 点阵字库（# = 落笔） ----------
_FONT = {
    " ": ["00000"] * 7,
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11100", "10010", "10001", "10001", "10001", "10010", "11100"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "J": ["00111", "00010", "00010", "00010", "00010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "10001", "11001", "10101", "10011", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "-": ["00000", "00000", "00000", "01110", "00000", "00000", "00000"],
    ":": ["00000", "00100", "00100", "00000", "00100", "00100", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "00110", "00110"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "(": ["00010", "00100", "01000", "01000", "01000", "00100", "00010"],
    ")": ["01000", "00100", "00010", "00010", "00010", "00100", "01000"],
    ",": ["00000", "00000", "00000", "00000", "00110", "00110", "00100"],
    "'": ["00100", "00100", "01000", "00000", "00000", "00000", "00000"],
}

DARK = (43, 47, 51)        # 深灰正文
VERMILION = (198, 49, 38)  # 朱红印章
GRAY = (150, 154, 158)


def _text_width(text: str, scale: int) -> int:
    return len(text) * 6 * scale


def _put_text(buf, w: int, text: str, x: int, y: int, scale: int, color):
    c = bytes(color)
    block = c * (scale * 3)  # 一个缩放像素（横向）
    for ci, ch in enumerate(text.upper()):
        glyph = _FONT.get(ch, _FONT[" "])
        for row, bits in enumerate(glyph):
            for col, bit in enumerate(bits):
                if bit != "1":
                    continue
                for dy in range(scale):
                    base = ((y + row * scale + dy) * w + x + (ci * 6 + col) * scale) * 3
                    buf[base:base + scale * 3] = block


def _put_rect(buf, w, h, x0, y0, x1, y1, color, fill=False):
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w - 1, x1), min(h - 1, y1)
    if x0 > x1 or y0 > y1:
        return
    row = bytes(color) * (x1 - x0 + 1)
    span = len(row)
    if fill:
        ys = range(y0, y1 + 1)
    else:
        ys = (y0, y1)
    for y in ys:
        i = (y * w + x0) * 3
        buf[i:i + span] = row
    if not fill:
        c = bytes(color)
        for y in range(y0 + 1, y1):
            base = y * w * 3
            buf[base + x0 * 3:base + x0 * 3 + 3] = c
            buf[base + x1 * 3:base + x1 * 3 + 3] = c


def _put_circle(buf, w, h, cx, cy, r, color):
    for y in range(max(0, cy - r), min(h, cy + r + 1)):
        for x in range(max(0, cx - r), min(w, cx + r + 1)):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            if r - 1.2 <= d <= r:
                i = (y * w + x) * 3
                buf[i:i + 3] = bytes(color)


def _render(width: int, height: int, lines: list[tuple[str, int]], stamp: str) -> bytes:
    """白底证件底图，返回 RGB 字节。"""
    buf = bytearray(b"\xff" * (width * height * 3))
    m = max(24, width // 22)
    _put_rect(buf, width, height, m // 2, m // 2, width - m // 2 - 1, height - m // 2 - 1, GRAY)
    _put_rect(buf, width, height, m // 2 + 4, m // 2 + 4, width - m // 2 - 5, height - m // 2 - 5, DARK)

    y = m
    for text, scale in lines:
        if text == "---":
            _put_rect(buf, width, height, m, y + scale * 2, width - m, y + scale * 2, GRAY)
            y += scale * 6
            continue
        _put_text(buf, width, text, m, y, scale, DARK)
        y += scale * 10
    # 表单横线
    while y < height - m * 3:
        _put_rect(buf, width, height, m, y, width - m, y, GRAY)
        y += max(28, height // 22)
    # 朱红圆形印章
    r = max(40, width // 12)
    cx, cy = width - m - r, height - m - r
    _put_circle(buf, width, height, cx, cy, r, VERMILION)
    _put_circle(buf, width, height, cx, cy, r - 8, VERMILION)
    sw = max(2, r // 22)
    _put_text(buf, width, stamp, cx - _text_width(stamp, sw) // 2, cy - sw * 3, sw, VERMILION)
    return bytes(buf)


def _downscale_up(src: bytes, w: int, h: int, factor: int) -> bytes:
    """降采样后最近邻放大：模拟低清模糊扫描（整行批量复制）。"""
    sw, sh = w // factor, h // factor
    out = bytearray()
    for y in range(sh):
        row = bytearray()
        sy = y * factor
        for x in range(sw):
            i = (sy * w + x * factor) * 3
            row += src[i:i + 3] * factor
        # factor 不整除时用最后一个像素补齐右边
        row += src[(sy * w + (sw - 1) * factor) * 3:
                   (sy * w + (sw - 1) * factor) * 3 + 3] * (w - sw * factor)
        out += bytes(row) * factor
    # 底部补齐
    if sh * factor < h and sh:
        out += bytes(out[-w * 3:]) * (h - sh * factor)
    return bytes(out)


def generate_sample(path: str | Path, title: str, fields: list[str],
                    landscape: bool = False, clear: bool = True, stamp: str = "SEAL"):
    """生成一张合成扫描件。fields 为若干行大写英文/数字（点阵字库仅支持 ASCII）。

    相同参数的样例件复用已渲染字节（多用例/重启播种时大幅省时）。
    """
    if landscape:
        width, height, head_scale, scale = (300, 195, 1, 1) if _FAST else (1200, 780, 4, 2)
    else:
        width, height, head_scale, scale = (215, 295, 1, 1) if _FAST else (860, 1180, 3, 2)
    key = (title, tuple(fields), width, height, head_scale, scale, clear, stamp[:14])
    cached = _SAMPLE_CACHE.get(key)
    if cached is None:
        lines: list[tuple[str, int]] = [(title, head_scale), ("---", head_scale)]
        lines += [(f, scale) for f in fields]
        rgb = _render(width, height, lines, stamp[:14])
        if not clear:
            rgb = _downscale_up(rgb, width, height, 4)
        cached = _png_bytes(rgb, width, height)
        if len(_SAMPLE_CACHE) < 128:
            _SAMPLE_CACHE[key] = cached
    Path(path).write_bytes(cached)
    return width, height


_SAMPLE_CACHE: dict = {}


def _png_bytes(rgb: bytes, width: int, height: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)  # filter: none
        raw += rgb[y * stride:(y + 1) * stride]
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def write_png(path: str | Path, rgb: bytes, width: int, height: int):
    Path(path).write_bytes(_png_bytes(rgb, width, height))


def read_png_size(path: str | Path) -> tuple[int, int]:
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是有效的 PNG 文件")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def save_upload(base_dir: str | Path, data: bytes, name: str) -> tuple[str, int, int]:
    """校验并保存上传的 PNG，返回 (相对路径, 宽, 高)。name 仅用于扩展名。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("仅支持 PNG 扫描件")
    width, height = read_png_bytes(data)
    if width < 200 or height < 200:
        raise ValueError("扫描件尺寸过小（低于 200x200），请重新上传清晰原件")
    import datetime
    base = Path(base_dir)
    sub = base / datetime.datetime.now().strftime("%Y%m")
    sub.mkdir(parents=True, exist_ok=True)
    fname = f"upload_{datetime.datetime.now():%Y%m%d%H%M%S%f}.png"
    rel = f"{sub.name}/{fname}"
    (sub / fname).write_bytes(data)
    return rel, width, height


def read_png_bytes(data: bytes) -> tuple[int, int]:
    if len(data) < 24:
        raise ValueError("PNG 数据不完整")
    return struct.unpack(">II", data[16:24])
