#!/usr/bin/env python3
"""Self-test of pak_textures.py on a throwaway folder (nothing of the project is touched): files are added to a zip with the engine's 7za, stored with paths relative to the assets folder,
the existing entries survive, a file that is not there is refused, and an empty list changes nothing.

    test_pak_textures.py        # exit 1 on any failure; editor-free
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys
import tempfile
import zipfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import pak_textures as P  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("  %s %-120s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def main():
    tmp = tempfile.mkdtemp(prefix="zz_pak_textures_")
    try:
        assets = os.path.join(tmp, "Assets")
        os.makedirs(os.path.join(assets, "objects", "props", "x", "textures"))
        files = {"objects/props/x/textures/a_diff.dds": b"A" * 100, "objects/props/x/textures/a_diff.dds.1": b"B" * 50, "objects/props/x/textures/a_diff.dds.1a": b"C" * 20}
        for rel, data in files.items():
            open(os.path.join(assets, *rel.split("/")), "wb").write(data)
        with zipfile.ZipFile(os.path.join(assets, P.PAK), "w") as z:           # the pak already holds something
            z.writestr("textures/old.dds", b"OLD")
        if not os.path.isfile(P.SEVENZA):
            check("the engine's 7za.exe is installed", False, P.SEVENZA)
        else:
            r = P.add(list(files), assets_dir=assets, work_dir=tmp)
            names = zipfile.ZipFile(os.path.join(assets, P.PAK)).namelist()
            norm = sorted(n.replace("\\", "/") for n in names)
            check("the three files (a split set: .dds, .dds.1, .dds.1a) are added with paths relative to the assets folder", all(k in norm for k in files), norm)
            check("the pak's earlier entry survives", "textures/old.dds" in norm, norm)
            check("the answer counts what was added and the pak grew", r["added"] == 3 and r["bytes_after"] > r["bytes_before"], r)
            z = zipfile.ZipFile(os.path.join(assets, P.PAK))
            check("the content is stored intact (stored, not altered)", z.read(next(n for n in z.namelist() if n.endswith(".dds.1"))) == b"B" * 50, "")
            r0 = P.add([], assets_dir=assets, work_dir=tmp)
            check("an empty list changes nothing", r0["added"] == 0 and r0["bytes_after"] == r0["bytes_before"], r0)
            try:
                P.add(["objects/props/x/textures/not_there.dds"], assets_dir=assets, work_dir=tmp)
                refused = False
            except RuntimeError:
                refused = True
            check("a file that is not under the assets folder is refused (nothing is guessed)", refused, "")
            check("no list file is left behind", not os.path.exists(os.path.join(tmp, "_pak_textures_list.txt")), "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
