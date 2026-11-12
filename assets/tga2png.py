#!/usr/bin/env python3
"""Convert an uncompressed 24/32-bit TGA (the engine's lossless screenshot format) to PNG, standard
library only, optionally downscaled.   tga2png.py in.tga [out.png] [--scale N]"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import struct
import sys
import zlib


def read_tga(path):
    data = open(path, "rb").read()
    idlen, cmtype, imtype = data[0], data[1], data[2]
    w, h, bpp, desc = struct.unpack_from("<HHBB", data, 12)
    if imtype != 2 or cmtype != 0 or bpp not in (24, 32):
        raise ValueError("unsupported TGA (type %d, %d bpp)" % (imtype, bpp))
    step = bpp // 8
    px = data[18 + idlen:18 + idlen + w * h * step]
    rows = [[(px[(y * w + x) * step + 2], px[(y * w + x) * step + 1], px[(y * w + x) * step]) for x in range(w)]
            for y in range(h)]
    if not desc & 0x20:          # bottom-up unless the origin bit says top-left
        rows.reverse()
    return w, h, rows


def write_png(path, rows):
    h, w = len(rows), len(rows[0])
    raw = b"".join(b"\x00" + bytes(c for p in row for c in p) for row in rows)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    scale = int(sys.argv[sys.argv.index("--scale") + 1]) if "--scale" in sys.argv else 1
    w, h, rows = read_tga(args[0])
    rows = [row[::scale] for row in rows[::scale]]
    out = args[1] if len(args) > 1 else args[0].rsplit(".", 1)[0] + ".png"
    write_png(out, rows)
    print(out, "%dx%d" % (len(rows[0]), len(rows)))
