#!/usr/bin/env python3
"""Read CryPak .pak archives without Sandbox.

A .pak is a ZIP, but CryPak writes backslashes into entry names and Python's
zipfile rejects some of them (BadZipFile on engine.pak), so this parses the
central directory by hand. Standard library only.

    crypak.py list    <pak> [--depth N]          folder summary (or every file with --depth 0)
    crypak.py grep    <pak|dir> <regex>          matching entry names, across every pak under a dir
    crypak.py extract <pak|dir> <regex> <outdir> write matching entries to disk
    crypak.py cat     <pak|dir> <entry>          print one entry (first match, case-insensitive)

A directory argument searches every *.pak below it, so a project's asset root
(e.g. "<project>/gamesdk") finds Libs/ and Scripts/ wherever they were packed.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import collections
import os
import re
import struct
import sys
import zlib

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def entries(path):
    """Return [{name, method, csize, usize, off}] from the pak's central directory."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 65536))
        tail = f.read()
        i = tail.rfind(b"PK\x05\x06")
        if i < 0:
            return []
        n, cdsize, cdoff = struct.unpack("<HII", tail[i + 10:i + 20])
        f.seek(cdoff)
        cd = f.read(cdsize)
    out, p = [], 0
    for _ in range(n):
        if cd[p:p + 4] != b"PK\x01\x02":
            break
        method = struct.unpack("<H", cd[p + 10:p + 12])[0]
        csize, usize = struct.unpack("<II", cd[p + 20:p + 28])
        nl, el, cl = struct.unpack("<HHH", cd[p + 28:p + 34])
        off = struct.unpack("<I", cd[p + 42:p + 46])[0]
        name = cd[p + 46:p + 46 + nl].decode("latin1").replace("\\", "/")
        out.append(dict(name=name, method=method, csize=csize, usize=usize, off=off, pak=path))
        p += 46 + nl + el + cl
    return out


def read(entry):
    """Bytes of one entry. Method 0 is stored, 8 is raw deflate."""
    with open(entry["pak"], "rb") as f:
        f.seek(entry["off"])
        h = f.read(30)
        nl, el = struct.unpack("<HH", h[26:30])
        f.seek(entry["off"] + 30 + nl + el)
        raw = f.read(entry["csize"])
    return raw if entry["method"] == 0 else zlib.decompress(raw, -15)


def paks_under(target):
    if os.path.isfile(target):
        return [target]
    found = []
    for root, _dirs, files in os.walk(target):
        found += [os.path.join(root, f) for f in files if f.lower().endswith(".pak")]
    return sorted(found)


def find(target, regex):
    rx = re.compile(regex, re.I)
    for pak in paks_under(target):
        for e in entries(pak):
            if rx.search(e["name"]):
                yield e


def read_asset(asset_root, rel_path):
    """Read an asset by game path, preferring a loose file over a packed one.

    Mirrors CryPak's default lookup order closely enough for inspection work.
    Returns bytes or None.
    """
    parts = [x for x in rel_path.replace("\\", "/").split("/") if x not in ("", ".")]
    if not any(x == ".." or ":" in x for x in parts):     # never read outside asset_root
        loose = os.path.join(asset_root, *parts)
        try:
            root_real = os.path.realpath(asset_root)
            real = os.path.realpath(loose)
            inside = os.path.commonpath([root_real, real]) == root_real
        except ValueError:                                # different drive
            inside = False
        if inside and os.path.isfile(real):
            with open(real, "rb") as f:
                return f.read()
    want = rel_path.replace("\\", "/").lower()
    for pak in paks_under(asset_root):
        for e in entries(pak):
            if e["name"].lower() == want:
                return read(e)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("pak"); p.add_argument("--depth", type=int, default=2)
    p = sub.add_parser("grep"); p.add_argument("target"); p.add_argument("regex")
    p = sub.add_parser("extract"); p.add_argument("target"); p.add_argument("regex"); p.add_argument("outdir")
    p = sub.add_parser("cat"); p.add_argument("target"); p.add_argument("entry")
    a = ap.parse_args()

    if a.cmd == "list":
        es = entries(a.pak)
        print("== %s  %d files" % (a.pak, len(es)))
        if a.depth <= 0:
            for e in es:
                print("%10d  %s" % (e["usize"], e["name"]))
        else:
            c = collections.Counter("/".join(e["name"].split("/")[:a.depth]) for e in es)
            for d, k in sorted(c.items()):
                print("%6d  %s" % (k, d))
    elif a.cmd == "grep":
        for e in find(a.target, a.regex):
            print("%10d  %s  [%s]" % (e["usize"], e["name"], os.path.basename(e["pak"])))
    elif a.cmd == "extract":
        n = 0
        for e in find(a.target, a.regex):
            parts = [x for x in e["name"].split("/") if x not in ("", ".")]
            out_abs = os.path.abspath(a.outdir)
            dst = os.path.join(out_abs, *parts)
            try:
                safe = not any(x == ".." or ":" in x for x in parts) and os.path.commonpath([out_abs, os.path.abspath(dst)]) == out_abs
            except ValueError:
                safe = False
            if not safe:
                print("skip unsafe entry name:", e["name"])
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(read(e))
            n += 1
        print("extracted %d files to %s" % (n, a.outdir))
    elif a.cmd == "cat":
        want = a.entry.replace("\\", "/").lower()
        for e in find(a.target, re.escape(want) + "$"):
            sys.stdout.write(read(e).decode("utf-8", "replace"))
            return
        sys.exit("not found: " + a.entry)


if __name__ == "__main__":
    main()
