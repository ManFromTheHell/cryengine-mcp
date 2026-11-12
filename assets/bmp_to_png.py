#!/usr/bin/env python3
"""Convert a 24-bit BMP to PNG with the standard library only (no PIL on this machine).

    bmp_to_png.py <in.bmp> [out.png]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import struct
import sys
import zlib


def convert(src, dst):
    d = open(src, "rb").read()
    off = struct.unpack_from("<I", d, 10)[0]
    w, h = struct.unpack_from("<ii", d, 18)
    bpp = struct.unpack_from("<H", d, 28)[0]
    step = bpp // 8
    row_bytes = (w * step + 3) & ~3
    raw = bytearray()
    for y in range(abs(h)):
        src_y = (abs(h) - 1 - y) if h > 0 else y
        row = d[off + src_y * row_bytes: off + src_y * row_bytes + w * step]
        raw.append(0)
        for x in range(w):
            b, g, r = row[x * step], row[x * step + 1], row[x * step + 2]
            raw += bytes((r, g, b))

    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, abs(h), 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    open(dst, "wb").write(png)


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else sys.argv[1].rsplit(".", 1)[0] + ".png")
