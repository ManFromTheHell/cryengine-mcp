#!/usr/bin/env python3
"""Self-test of debug_views.py: the catalog parsed from the engine's own scripts (offline), then, with the editor open, real captures of two views against a control (the game is not run;
the level is not changed; every variable is checked back at its old value). About 40 s live.

    test_debug_views.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import debug_views as D

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-100s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:50]))


def main():
    cat = D.catalog()
    by = {v["id"]: v for v in cat}
    check("the catalog holds the engine's views (%d: render, environment, debug_views)" % len(cat), len(cat) >= 60 and {v["group"].split(".")[0] for v in cat} == {"render", "environment", "debug_views"}, len(cat))
    check("render:Normals sets r_DebugGBuffer 1 and is labelled from the variable's own help ('Normals'); Albedo is 4", by["render:Normals"]["sets"] == [("r_DebugGBuffer", 1)] and by["render:Albedo"]["sets"] == [("r_DebugGBuffer", 4)] and by["render:Albedo"]["label"] == "Albedo", by["render:Albedo"]["label"])
    check("float(512) in the script is read as the number 512 (texel density)", by["render:texelspermeterpc"]["sets"] == [("r_TexelsPerMeter", 512)], by["render:texelspermeterpc"]["sets"])
    check("a toggle remembers what to put back (wireframe: set r_wireframe 1, reset to 0)", by["render:wireframe"]["sets"] == [("r_wireframe", 1)] and by["render:wireframe"]["reset"] == [("r_wireframe", 0)], by["render:wireframe"]["reset"])
    check("an environment toggle is a 'without' view (e_Fog 1 -> 0)", by["environment:e_GlobalFog"]["sets"] == [("e_Fog", 0)] and by["environment:e_GlobalFog"]["reset"] == [("e_Fog", 1)], by["environment:e_GlobalFog"]["sets"])
    check("a cycle view carries its steps (the LOD view: e_DebugDraw 0, 3, -3)", [s for s in by["render:lods"]["cycle"]] == [("e_DebugDraw", 0), ("e_DebugDraw", 3), ("e_DebugDraw", -3)], by["render:lods"]["cycle"])
    stale = [v for v in cat if v["id"].startswith("debug_views:r_debugGBuffer") and v["notes"]]
    check("the engine's debug_views.py is shown to be STALE where it disagrees with the variable's own help (r_DebugGBuffer 5-8: %d entries)" % len(stale), len(stale) >= 4 and "stale" in stale[0]["notes"][0], stale[0]["notes"][0][:70] if stale else "")
    check("a variable the script names but this editor does not have is noted (e_GI, r_Beams)", "not a variable" in " ".join(by["environment:e_GI"]["notes"]) and "not a variable" in " ".join(by["environment:r_Beams"]["notes"]), by["environment:e_GI"]["notes"])
    check("control: the commented-out sound view does not leak into the material-fx view before it (no s_DrawSounds anywhere)", not any("s_DrawSounds" in str(v["sets"]) + str(v["reset"]) for v in cat), "")
    try:
        D.get("no_such_view")
        bad = False
    except D.DebugViewError:
        bad = True
    check("an unknown view is refused", bad, "")

    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                           # noqa: BLE001
        live = False
        print("  --   the editor is not open: the live checks are skipped")
    if live:
        import shot_compare as S
        eye, tgt = [487, 360, 42], [487, 400, 36]
        a, b = S.load(D.shoot("dvt_a", eye, tgt)), S.load(D.shoot("dvt_b", eye, tgt))
        noise = S.diff_stats(a, b)["mean_abs_diff"]
        check("control: the same view taken twice differs by only %.1f grey levels" % noise, noise < 8, noise)
        before = {n: D._cvar_get(n) for n in ("r_DebugGBuffer", "r_wireframe")}
        r = D.look("render:Normals", eye, tgt)
        diff = S.diff_stats(a, S.load(r["picture"]))["mean_abs_diff"]
        check("the Normals view is a different picture (%.1f levels against the control's %.1f) and the variable is back" % (diff, noise), diff > 5 * noise and r["restored"] and r["set"] == {"r_DebugGBuffer": "1"}, diff)
        r2 = D.look("render:wireframe", eye, tgt)
        diff2 = S.diff_stats(a, S.load(r2["picture"]))["mean_abs_diff"]
        check("the wireframe view differs too (%.1f) and its variable is back" % diff2, diff2 > 5 * noise and r2["restored"], diff2)
        after = {n: D._cvar_get(n) for n in before}
        check("every variable the views touched holds the value it had before: %s" % before, after == before, after)

    bad_n = results.count(False)
    print("debug views test: %s (%d checks)" % ("PASS" if not bad_n else "%d FAILED" % bad_n, len(results)))
    return 1 if bad_n else 0


if __name__ == "__main__":
    sys.exit(main())
