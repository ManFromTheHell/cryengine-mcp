#!/usr/bin/env python3
"""Convert the 24/32-bit BMPs that bridge.viewport.screenshot writes into PNGs
(standard library only), optionally downscaled, so they can be viewed by tools
that do not read BMP. Also prints mean luminance and saturation.

    bmp2png.py in.bmp [out.png] [--scale N]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import struct
import sys
import zlib


def read_bmp(path):
    data = open(path, "rb").read()
    if data[:2] != b"BM":
        raise ValueError("not a BMP: " + path)
    off = struct.unpack_from("<I", data, 10)[0]
    w, h = struct.unpack_from("<ii", data, 18)
    bpp = struct.unpack_from("<H", data, 28)[0]
    if bpp not in (24, 32):
        raise ValueError("only 24/32-bit BMP supported, got %d" % bpp)
    step = bpp // 8
    row = (w * step + 3) & ~3
    top_down = h < 0
    h = abs(h)
    rows = []
    for y in range(h):
        src = y if top_down else h - 1 - y
        start = off + src * row
        line = data[start:start + w * step]
        rgb = bytearray(w * 3)
        rgb[0::3] = line[2::step]
        rgb[1::3] = line[1::step]
        rgb[2::3] = line[0::step]
        rows.append(bytes(rgb))
    return w, h, rows


def write_png(path, w, h, rows):
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(chunk(b"IEND", b""))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    scale = 1
    if "--scale" in sys.argv:
        scale = max(1, int(sys.argv[sys.argv.index("--scale") + 1]))
        args = [a for a in args if a != str(scale)]
    src = args[0]
    dst = args[1] if len(args) > 1 else src.rsplit(".", 1)[0] + ".png"
    w, h, rows = read_bmp(src)
    if scale > 1:
        # nearest-neighbour: keep every Nth pixel of every Nth row
        rows = [b"".join(r[x * 3:x * 3 + 3] for x in range(0, w, scale)) for r in rows[::scale]]
        w, h = (w + scale - 1) // scale, len(rows)
    lum = sat = n = 0
    for r in rows[::4]:
        for x in range(0, len(r), 12):
            cr, cg, cb = r[x], r[x + 1], r[x + 2]
            lum += 0.2126 * cr + 0.7152 * cg + 0.0722 * cb
            sat += max(cr, cg, cb) - min(cr, cg, cb)
            n += 1
    write_png(dst, w, h, rows)
    print("%s %dx%d mean_luma=%.1f mean_saturation=%.1f" % (dst, w, h, lum / max(n, 1), sat / max(n, 1)))


if __name__ == "__main__":
    main()
