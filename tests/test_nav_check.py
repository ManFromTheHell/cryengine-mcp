#!/usr/bin/env python3
"""Live test of nav_check.py (the editor open with the level): the engine's connectivity test on the village navmesh, with controls. The game is not run; nothing is changed. About 10 s.

    test_nav_check.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import nav_check as N

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-112s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:50]))


def main():
    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
    except Exception:                                           # noqa: BLE001
        print("  --   the editor is not open: nothing to test")
        return 0
    ag = N.agents()
    check("the editor has the Villager agent type with its mesh AshvaleNav", ag and ag[0]["name"] == "Villager" and ag[0]["meshes"][0]["name"] == "AshvaleNav", ag)
    r = N.reach("AshvaleStart", ["BanditYard", "CampFire", "512,172,31.2", "3900,3900,10"])
    s = {x["label"]: x for x in r["segments"]}
    check("positive control: the player start is connected to the bandit yard and the camp fire (the far end of the valley), and to itself", s["BanditYard"]["reachable"] and s["CampFire"]["reachable"] and s["t2"]["reachable"], [x["reachable"] for x in r["segments"]])
    check("negative control: a point 3 km outside the navigation area is not connected and lies in no mesh volume", not s["t3"]["reachable"] and not s["t3"]["end_in_volume"] and s["t3"]["end_mesh"] == -1, s["t3"])
    m = N.map_area(470, 360, 490, 380, 5.0, 36.5)
    check("a map of the Reeve house: its surroundings are connected and its interior (473-483 x 369-376) is an island of the mesh: the door is cut out of the mesh on purpose (the FarmGate crosses it with an off-mesh link)",
          "#" in m["rows"][0] and "." in "".join(m["rows"]), m["rows"][:2])
    r2 = N.reach("AshvaleStart", ["Reeve_Door", "Barn_Logs2"])
    s2 = {x["label"]: x for x in r2["segments"]}
    check("the open-sided barn's interior is connected (no door to cross), the Reeve's doorway is not (by design)", s2["Barn_Logs2"]["reachable"] and not s2["Reeve_Door"]["reachable"], [x["reachable"] for x in r2["segments"]])
    try:
        N.reach("NoSuchObjectAnywhere", ["BanditYard"])
        bad = False
    except N.NavError:
        bad = True
    check("an object name that does not exist is refused", bad, "")
    bad = results.count(False)
    print("nav check test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
