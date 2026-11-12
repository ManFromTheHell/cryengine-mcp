#!/usr/bin/env python3
"""List a PE file's imports (per DLL, demangled-ish) and exports -- standard library only, so it
works where dumpbin is not on PATH.

    pe_imports.py <file.dll|exe> [--exports] [--filter substring]

Used to answer "does ClaudeBridgePlugin need the locally rebuilt Sandbox.exe?":
an imported symbol that only a local build exports is a hard dependency.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import struct
import sys


def rva_to_off(sections, rva):
    for va, vsize, raw, rsize in sections:
        if va <= rva < va + max(vsize, rsize):
            return rva - va + raw
    raise ValueError("rva 0x%x not in any section" % rva)


def cstr(d, off):
    end = d.index(b"\0", off)
    return d[off:end].decode("latin-1")


def load(path):
    d = open(path, "rb").read()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    optsz = struct.unpack_from("<H", d, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", d, opt)[0]
    ddir = opt + (112 if magic == 0x20B else 96)
    exp_rva, _ = struct.unpack_from("<II", d, ddir)
    imp_rva, _ = struct.unpack_from("<II", d, ddir + 8)
    secs = []
    so = opt + optsz
    for i in range(nsec):
        vsize, va, rsize, raw = struct.unpack_from("<IIII", d, so + i * 40 + 8)
        secs.append((va, vsize, raw, rsize))
    return d, secs, exp_rva, imp_rva, magic == 0x20B


def imports(path):
    d, secs, _, imp_rva, pe64 = load(path)
    out = {}
    if not imp_rva:
        return out
    off = rva_to_off(secs, imp_rva)
    while True:
        oft, _, _, name_rva, ft = struct.unpack_from("<IIIII", d, off)
        if name_rva == 0:
            break
        dll = cstr(d, rva_to_off(secs, name_rva))
        thunk = rva_to_off(secs, oft or ft)
        names = []
        step, flag = (8, 1 << 63) if pe64 else (4, 1 << 31)
        while True:
            v = struct.unpack_from("<Q" if pe64 else "<I", d, thunk)[0]
            if v == 0:
                break
            if v & flag:
                names.append("#%d" % (v & 0xFFFF))
            else:
                names.append(cstr(d, rva_to_off(secs, v & 0x7FFFFFFF) + 2))
            thunk += step
        out[dll] = names
        off += 20
    return out


def exports(path):
    d, secs, exp_rva, _, _ = load(path)
    if not exp_rva:
        return []
    off = rva_to_off(secs, exp_rva)
    nnames, _, names_rva = struct.unpack_from("<III", d, off + 24)[0], None, struct.unpack_from("<I", d, off + 32)[0]
    nnames = struct.unpack_from("<I", d, off + 24)[0]
    base = rva_to_off(secs, names_rva)
    return [cstr(d, rva_to_off(secs, struct.unpack_from("<I", d, base + 4 * i)[0])) for i in range(nnames)]


if __name__ == "__main__":
    path = sys.argv[1]
    flt = sys.argv[sys.argv.index("--filter") + 1].lower() if "--filter" in sys.argv else None
    if "--exports" in sys.argv:
        ex = exports(path)
        print("%d exports" % len(ex))
        for e in ex:
            if not flt or flt in e.lower():
                print("  " + e)
    else:
        for dll, names in imports(path).items():
            shown = [n for n in names if not flt or flt in n.lower()]
            print("%-28s %4d imports" % (dll, len(names)))
            if flt or dll.lower() in ("sandbox.exe", "editorcommon.dll", "cryqt.dll", "mfctoolsplugin.dll"):
                for n in shown[:400]:
                    print("      " + n)
