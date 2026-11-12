#!/usr/bin/env python3
"""Read the per-clip headers the RC wrote into an Animations.img (CHUNK_GAHCAF_INFO, CryHeaders.h):
duration, distance, speed, turn speed, asset turn -- offline, read-only.

    anim_img.py <Animations.img | animations.pak> [substring ...]

Note: the ENGINE loads only the times, flags and start location from this file
(CharacterManager::LoadAnimationImageFileCAF); speed/turn here are the RC's own figures, useful as
a cross-check of what the compiled clip contains, not as what a blend space uses.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import math
import struct
import sys

import pakread

CAF_FMT = "<I256sII8f3fI7f7f3f5f"   # CHUNK_GAHCAF_INFO 0x0971


def chunks(data):
    sig, ver, n, table = struct.unpack_from("<4sIII", data, 0)
    assert sig in (b"CrCh", b"CryTek\x00\x00"[:4]), sig
    out = []
    if ver == 0x746:
        for i in range(n):
            ctype, cver, cid, size, off = struct.unpack_from("<HHIII", data, table + 16 * i)
            out.append((ctype, cver & 0x7FFF, data[off:off + size]))
    else:
        raise SystemExit("chunk file version %#x not handled" % ver)
    return out


def headers(data):
    size = struct.calcsize(CAF_FMT)
    for ctype, cver, blob in chunks(data):
        if cver != 0x0971 or len(blob) < size:
            continue
        v = struct.unpack_from(CAF_FMT, blob)
        path = v[1].split(b"\x00")[0].decode("latin-1")
        start, end, total = v[12], v[13], v[14]
        dist, speed, slope, turn_speed, asset_turn = v[-5:]
        yield {"path": path, "dur": end - start, "total": total, "dist": dist, "speed": speed,
               "slope": slope, "turn_speed": turn_speed, "asset_turn_deg": math.degrees(asset_turn)}


if __name__ == "__main__":
    src = sys.argv[1]
    if src.lower().endswith(".pak"):
        data = pakread.read(src, "Animations/Animations.img")
    else:
        data = open(src, "rb").read()
    subs = [s.lower() for s in sys.argv[2:]]
    for h in headers(data):
        if subs and not any(s in h["path"].lower() for s in subs):
            continue
        print("%-70s dur %.3f  dist %.3f  speed %.3f  turn %.3f rad/s  asset_turn %.1f deg"
              % (h["path"].rsplit("/", 1)[-1], h["dur"], h["dist"], h["speed"], h["turn_speed"], h["asset_turn_deg"]))
