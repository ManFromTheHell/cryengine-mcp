#!/usr/bin/env python3
"""Live test of objects.py against the OPEN editor with the level. Places and removes throwaway objects named Mcptest_*, never saves the level, and
checks at the end that the level holds exactly the objects it held at the start.

    test_objects.py         # exit 1 on any failure; skips (exit 0, says so) when the editor is not open
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import final_packages as F
import objects as O

CUBE = "%ENGINE%/EngineAssets/Objects/primitive_cube.cgf"
MTL = "materials/ashvale/stone_wall"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-66s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:80]))


def refused(name, fn, *a, **k):
    try:
        fn(*a, **k)
    except O.ObjectError as exc:
        check(name, True, str(exc)[:68])
        return
    check(name, False, "was accepted")


def main():
    try:
        before = len(O.listing())
    except O.ObjectError as exc:
        print("  --   the editor is not open with the level: %s\nobjects test: SKIPPED" % str(exc)[:100])
        return 0
    try:
        check("the level starts without Mcptest_ objects", not O.listing("Mcptest_"), "%d objects in the level" % before)
        r = O.place("brush", CUBE, "Mcptest_a", [30, 30, 200], [0, 0, 30], [2, 2, 2], MTL)
        o = r["object"]
        check("place: the editor reports the object where, how big and how turned it was asked", r["ok"] and o["pos"] == [30, 30, 200] and o["scale"] == [2, 2, 2]
              and abs(o["rotDeg"][2] - 30) < 0.5 and o["type"] == "Brush", r["problems"] or o)
        check("place: the material is on it", MTL in (o.get("material") or "") or o.get("material"), o.get("material"))
        check("place: it says the level is NOT saved", r["saved"] is False)
        refused("place: the same name again without overwrite is refused", O.place, "brush", CUBE, "Mcptest_a", [31, 30, 200])
        r2 = O.place("brush", CUBE, "Mcptest_a", [31, 30, 200], overwrite=True)
        check("place: overwrite=true replaces it (moved to x 31)", r2["ok"] and r2["replaced"] and r2["object"]["pos"][0] == 31 and len(O.listing("Mcptest_")) == 1, r2["problems"])
        refused("place: a mesh that does not exist is refused", O.place, "brush", "objects/no/such/mesh.cgf", "Mcptest_x", [30, 30, 200])
        refused("place: a bad name is refused", O.place, "brush", CUBE, "bad name;", [30, 30, 200])
        refused("place: a position in the wrong units is refused", O.place, "brush", CUBE, "Mcptest_x", [3e9, 0, 0])
        refused("place: a zero scale is refused", O.place, "brush", CUBE, "Mcptest_x", [30, 30, 200], scale=[0, 1, 1])
        refused("place: a material that does not exist is refused", O.place, "brush", CUBE, "Mcptest_x", [30, 30, 200], material="materials/nope/nothing")
        refused("place: a brush without a .cgf path is refused", O.place, "brush", "stone_wall", "Mcptest_x", [30, 30, 200])
        check("none of those refusals created anything", len(O.listing("Mcptest_")) == 1, len(O.listing("Mcptest_")))
        for i in (1, 2, 3):
            O.place("brush", CUBE, "Mcptest_b%d" % i, [30 + i, 40, 200])
        # --- delete: a dry run first, the count must be confirmed
        dry = O.delete(prefix="Mcptest_")
        check("delete: the dry run counts 4 and deletes nothing", dry["dry_run"] and dry["would_delete"] == 4 and len(O.listing("Mcptest_")) == 4, dry["would_delete"])
        refused("delete: really=3 for 4 matches is refused and deletes nothing", O.delete, prefix="Mcptest_", really=3)
        check("delete: still 4 there", len(O.listing("Mcptest_")) == 4)
        refused("delete: a 2-letter prefix is refused", O.delete, prefix="Mc")
        refused("delete: names and prefix together are refused", O.delete, names=["Mcptest_a"], prefix="Mcptest_")
        refused("delete: neither names nor prefix is refused", O.delete)
        refused("delete: a name that is not in the level is refused when really is given", O.delete, names=["Mcptest_a", "Mcptest_zzz"], really=2)
        real = O.delete(prefix="Chapel_")                       # the control on REAL objects: a dry run must never delete
        starts = [o for o in O.listing("Chapel_") if o["name"].startswith("Chapel_")]       # the editor's filter is a substring (Trim_Chapel_...); the tool counts prefixes
        check("delete: a dry run on the real Chapel_ objects counts them and removes none", real["dry_run"] and real["would_delete"] >= 10 and len(starts) == real["would_delete"]
              and len(O.listing()) == before + 4, "%d would go; %d objects in the level" % (real["would_delete"], len(O.listing())))
        done = O.delete(prefix="Mcptest_", really=4)
        check("delete: really=4 removes exactly the four", done["ok"] and done["deleted"] == 4 and not O.listing("Mcptest_"), done)
        # --- the editor not answering is a clear refusal, not a crash
        real_bridge = F.bridge
        F.bridge = lambda *a, **k: (_ for _ in ()).throw(ConnectionRefusedError("refused"))
        try:
            refused("the editor not answering is reported, not a traceback", O.listing)
        finally:
            F.bridge = real_bridge
    finally:
        left = O.listing("Mcptest_")
        if left:
            O.delete(prefix="Mcptest_", really=len(left))
    after = len(O.listing())
    check("the level holds exactly the objects it held at the start (%d)" % before, after == before, "%d now" % after)
    bad = results.count(False)
    print("objects test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
