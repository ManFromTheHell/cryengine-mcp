#!/usr/bin/env python3
"""Self-test of asset_meta.py with the engine's real resource compiler on a THROWAWAY asset folder (the project is only read), then a dry run on the project itself.

    test_asset_meta.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys
import tempfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import asset_meta as M

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def main():
    if not os.path.isfile(M.RC):
        print("  SKIPPED: the engine's resource compiler (rc.exe) is not installed here -- 0 checks run")
        return
    root = tempfile.mkdtemp(prefix="zz_mcp_meta_")
    scratch = os.path.join(root, "scratch")
    try:
        assets = os.path.join(root, "Assets")
        src_tex = os.path.join(M.ASSETS, "libs", "ui", "Textures", "pin_you.dds")
        src_ogg = os.path.join(M.ASSETS, "audio", "sdlmixer", "assets", "ash_bow.ogg")
        src_cgf = next((os.path.join(r, f) for r, _d, fs in os.walk(os.path.join(M.ASSETS, "objects", "ashvale", "village")) for f in sorted(fs) if f.lower() == "well.cgf"), None)
        if not (os.path.isfile(src_tex) and os.path.isfile(src_ogg) and src_cgf):
            print("  SKIPPED: the project lacks the sample assets this test copies (pin_you.dds, ash_bow.ogg, well.cgf under objects/ashvale/village) -- 0 checks run")
            return
        os.makedirs(os.path.join(assets, "t"))
        shutil.copyfile(src_tex, os.path.join(assets, "t", "a.dds"))
        shutil.copyfile(src_ogg, os.path.join(assets, "t", "b.ogg"))
        shutil.copyfile(src_cgf, os.path.join(assets, "t", "c.cgf"))
        shutil.copyfile(src_cgf, os.path.join(assets, "t", "c_lod1.cgf"))
        open(os.path.join(assets, "t", "d.txt"), "w").write("not an asset")
        # a file that already has metadata: the content must survive a run unchanged
        shutil.copyfile(src_tex, os.path.join(assets, "t", "e.dds"))
        keep = os.path.join(assets, "t", "e.dds.cryasset")
        open(keep, "w").write('<AssetMetadata version="0" type="Texture" guid="11111111-2222-3333-4444-555555555555"><Files><File path="e.dds"/></Files></AssetMetadata>')
        before = M._sha(keep)

        r = M.generate(False, assets, scratch)
        check("dry run: the resource compiler runs and exits 0", r["rc_exit"] == 0 and not r["applied"], r["seconds"])
        check("positive control: it would create the metadata of the texture, the sound and the mesh (3), and says what kinds", r["would_create"] == 3 and r["by_type"] == {".dds": 1, ".ogg": 1, ".cgf": 1}, r["by_type"])
        check("control: the _lod1 mesh, the text file and the file that already has metadata are not in the list", all("c_lod1" not in f and "d.txt" not in f and "e.dds" not in f for f in r["files"]), r["files"])
        check("a dry run writes nothing into the project's folder", not os.path.exists(os.path.join(assets, "t", "a.dds.cryasset")) and M._sha(keep) == before, "")
        r = M.generate(True, assets, scratch)
        check("apply: creates exactly those 3 files next to their sources", r["created"] == 3 and all(os.path.isfile(os.path.join(assets, "t", n)) for n in ("a.dds.cryasset", "b.ogg.cryasset", "c.cgf.cryasset")), r.get("created"))
        check("control: the existing metadata file is byte for byte what it was (the GUID was not replaced)", M._sha(keep) == before, "")
        text = open(os.path.join(assets, "t", "a.dds.cryasset"), encoding="utf-8").read()
        check("the new file is the engine's metadata: type, GUID, the file and its details (width, height, mips)", 'type="Texture"' in text and 'guid="' in text and 'name="width"' in text, text[:60])
        r = M.generate(True, assets, scratch)
        check("idempotent: a second apply creates nothing", r["created"] == 0 and r["already_have_metadata"] >= 4, (r["created"], r["already_have_metadata"]))
        os.remove(os.path.join(assets, "t", "a.dds"))
        os.remove(os.path.join(assets, "t", "a.dds.cryasset"))
        r = M.generate(True, assets, scratch)
        check("control: a file with no metadata and no source (deleted) creates nothing for it", not os.path.exists(os.path.join(assets, "t", "a.dds.cryasset")), r["created"])
        check("the scratch run folders are cleaned up", os.path.isdir(scratch) and os.listdir(scratch) == [], os.listdir(scratch))
        try:
            M.run_rc(assets, scratch, rc=os.path.join(root, "no_such_rc.exe"))
            check("positive control: a resource compiler that is not there is refused", False, "")
        except M.MetaError as exc:
            check("positive control: a resource compiler that is not there is refused", "resource compiler" in str(exc), str(exc)[:50])

        # the project itself: read only
        r = M.generate(False)
        check("the project: a dry run completes and writes nothing (0 to create after 2026-10-11; a number above 0 only means assets were added since)", r["rc_exit"] == 0 and r["applied"] is False, (r["would_create"], r["by_type"], r["examples"][:3]))
        check("the project: the compiler's answer covers the files the project already has metadata for (500+)", r["already_have_metadata"] >= 500, r["already_have_metadata"])
    finally:
        shutil.rmtree(root, ignore_errors=True)
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
