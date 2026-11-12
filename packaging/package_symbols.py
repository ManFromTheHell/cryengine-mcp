#!/usr/bin/env python3
"""Symbols for a package: which PDB belongs to which binary, an archive of them beside each package, and the lookup symbolize.py uses.

A PE file names its PDB in the debug directory (CodeView 'RSDS' record: GUID, age, path). A PDB carries the same GUID and age in its info stream (stream 1 of the MSF container). A PDB
is THE PDB of a binary only when both match; a PDB that merely has the same name (the usual mistake: yesterday's Game.pdb beside today's Game.dll) resolves to the wrong function.

    package_symbols.py describe <file.dll|exe|pdb>     GUID, age, PDB path
    package_symbols.py archive <package-dir> [--config release] [--out <dir>]   copy Game.dll + the matching Game.pdb and write modules.json
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import shutil
import struct
import sys
import time

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ENGINE = os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1")
ENGINE_SOURCE = _paths.ENGINE_SOURCE            # "" when CRYENGINE_SOURCE is unset: that tree is then not searched
MSF_MAGIC = b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\x00\x00\x00"


def _guid(raw):
    d1, d2, d3 = struct.unpack_from("<IHH", raw, 0)
    return "%08X-%04X-%04X-%s-%s" % (d1, d2, d3, raw[8:10].hex().upper(), raw[10:16].hex().upper())


def rsds(path):
    """-> {"guid", "age", "pdb"} from a PE file's CodeView debug record, or None (no debug directory / not a PE / no RSDS record)"""
    try:
        with open(path, "rb") as f:
            d = f.read(4 << 20) if os.path.getsize(path) > (64 << 20) else f.read()
            if d[:2] != b"MZ":
                return None
            pe = struct.unpack_from("<I", d, 0x3C)[0]
            if d[pe:pe + 4] != b"PE\0\0":
                return None
            nsec = struct.unpack_from("<H", d, pe + 6)[0]
            optsz = struct.unpack_from("<H", d, pe + 20)[0]
            opt = pe + 24
            magic = struct.unpack_from("<H", d, opt)[0]
            ddir = opt + (112 if magic == 0x20B else 96)
            dbg_rva, dbg_size = struct.unpack_from("<II", d, ddir + 6 * 8)
            if not dbg_rva:
                return None
            secs = []
            so = opt + optsz
            for i in range(nsec):
                vsize, va, rsize, raw = struct.unpack_from("<IIII", d, so + i * 40 + 8)
                secs.append((va, vsize, raw, rsize))
            off = next((dbg_rva - va + raw for va, vsize, raw, rsize in secs if va <= dbg_rva < va + max(vsize, rsize)), None)
            if off is None:
                return None
            for i in range(dbg_size // 28):
                typ, size, _addr, ptr = struct.unpack_from("<IIII", d, off + i * 28 + 12)
                if typ == 2:
                    f.seek(ptr)
                    rec = f.read(min(size, 1024))
                    if rec[:4] == b"RSDS":
                        return {"guid": _guid(rec[4:20]), "age": struct.unpack_from("<I", rec, 20)[0], "pdb": rec[24:].split(b"\0", 1)[0].decode("utf-8", "replace")}
    except (OSError, struct.error):
        return None
    return None


def pdb_signature(path):
    """-> {"guid", "age"} read from a PDB's info stream (MSF 7 container), or None"""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
            if head[:32] != MSF_MAGIC:
                return None
            block, _free, _nblocks, dir_bytes, _unk, map_addr = struct.unpack_from("<6I", head, 32)

            def blocks(indices):
                out = b""
                for i in indices:
                    f.seek(i * block)
                    out += f.read(block)
                return out
            ndir = (dir_bytes + block - 1) // block
            f.seek(map_addr * block)
            dir_blocks = struct.unpack("<%dI" % ndir, f.read(4 * ndir))
            directory = blocks(dir_blocks)[:dir_bytes]
            nstreams = struct.unpack_from("<I", directory, 0)[0]
            sizes = struct.unpack_from("<%dI" % nstreams, directory, 4)
            pos = 4 + 4 * nstreams
            if nstreams < 2:
                return None
            # stream 0's block list comes first, then stream 1's
            n0 = 0 if sizes[0] in (0xFFFFFFFF, 0) else (sizes[0] + block - 1) // block
            pos += 4 * n0
            n1 = (sizes[1] + block - 1) // block
            s1_blocks = struct.unpack_from("<%dI" % n1, directory, pos)
            info = blocks(s1_blocks[:1])
            _ver, _sig, age = struct.unpack_from("<III", info, 0)
            return {"guid": _guid(info[12:28]), "age": age}
    except (OSError, struct.error):
        return None


def matches(binary, pdb):
    """True when `pdb` is the PDB of `binary` (GUID and age agree; a PDB's age may be higher than the image's only for incremental links, which Crytek's builds do not use)"""
    a, b = rsds(binary), pdb_signature(pdb)
    return bool(a and b and a["guid"] == b["guid"] and a["age"] == b["age"])


def find_pdb(binary, extra_dirs=()):
    """Search for the PDB that matches `binary`: beside it, the path it names, then `extra_dirs` (a symbols folder, the engine, the engine source tree). -> (path, "matched") | (path, "name-only")
    | (None, "none")"""
    info = rsds(binary)
    base = os.path.splitext(os.path.basename(binary))[0] + ".pdb"
    cands = [os.path.join(os.path.dirname(binary), base)]
    if info and info["pdb"]:
        cands.append(info["pdb"])
    for d in extra_dirs:
        if not d:
            continue
        cands += [os.path.join(d, base)]
        for sub in ("bin/win_x64_release", "bin/win_x64", "win_x64_release", "win_x64"):
            cands.append(os.path.join(d, *sub.split("/"), base))
    seen, name_only = set(), None
    for c in cands:
        c = os.path.normcase(os.path.abspath(c))
        if c in seen or not os.path.isfile(c):
            continue
        seen.add(c)
        if matches(binary, c):
            return c, "matched"
        name_only = name_only or c
    return (name_only, "name-only") if name_only else (None, "none")


def archive(dest, config="release", out_dir=None, project=PROJECT, search=tuple(d for d in (ENGINE, ENGINE_SOURCE) if d), copy_pdbs=True):
    """Write <out_dir> (default <package>_symbols beside the package): the Game.dll of the package, the Game.pdb that MATCHES it (never a same-named other one), the matching PDB of every engine
    module that has one, and modules.json (every PE of the package's bin folders: GUID, age, PDB named, PDB archived or why not) -> the report dict"""
    dest = os.path.abspath(dest)
    out_dir = out_dir or dest.rstrip("\\/") + "_symbols"
    os.makedirs(out_dir, exist_ok=True)
    mods, archived, missing = [], [], []
    written = set()
    project_bin = os.path.join(project, "bin", "win_x64_release" if config == "release" else "win_x64")
    for sub in ("bin", "bin/win_x64", "bin/win_x64_release"):
        folder = os.path.join(dest, *sub.split("/"))
        if not os.path.isdir(folder):
            continue
        for n in sorted(os.listdir(folder)):
            p = os.path.join(folder, n)
            if not (os.path.isfile(p) and n.lower().endswith((".dll", ".exe"))):
                continue
            info = rsds(p)
            rec = {"module": sub + "/" + n, "size": os.path.getsize(p), "guid": info and info["guid"], "age": info and info["age"], "pdb_named": info and info["pdb"], "pdb_archived": None, "pdb_status": "no debug record"}
            if info:
                dirs = [project_bin] + list(search) if n.lower() == "game.dll" else list(search)
                pdb, how = find_pdb(p, dirs)
                rec["pdb_status"] = how if how != "name-only" else "name-only (GUID/age differ: NOT archived)"
                if pdb and how == "matched" and copy_pdbs:
                    target = os.path.join(out_dir, os.path.basename(pdb))
                    if target not in written:            # overwrite a stale same-named PDB from an earlier run, but keep the first match of THIS run
                        shutil.copy2(pdb, target)
                        written.add(target)
                    rec["pdb_archived"] = os.path.basename(pdb)
                    archived.append(os.path.basename(pdb))
                elif n.lower() == "game.dll":
                    missing.append("Game.pdb matching the packaged Game.dll (%s age %s): %s" % (info["guid"], info["age"], how))
            if n.lower() == "game.dll":
                shutil.copy2(p, os.path.join(out_dir, "Game.dll"))
            mods.append(rec)
    rep = {"package": os.path.basename(dest), "config": config, "when": time.strftime("%Y-%m-%d %H:%M:%S"), "pdbs_archived": sorted(set(archived)), "problems": missing, "modules": mods}
    with open(os.path.join(out_dir, "modules.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    rep["out_dir"] = out_dir
    return rep


def main():
    a = sys.argv[1:]
    if len(a) >= 2 and a[0] == "describe":
        p = a[1]
        print(json.dumps(pdb_signature(p) if p.lower().endswith(".pdb") else rsds(p), indent=1))
        return 0
    if len(a) >= 2 and a[0] == "archive":
        cfg = a[a.index("--config") + 1] if "--config" in a else "release"
        out = a[a.index("--out") + 1] if "--out" in a else None
        r = archive(a[1], cfg, out)
        print(json.dumps({k: v for k, v in r.items() if k != "modules"}, indent=1))
        return 1 if r["problems"] else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
