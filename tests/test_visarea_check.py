#!/usr/bin/env python3
"""Self-test of visarea_check.py: the engine's naming rule and the file checks on synthetic levels with a clean control first (offline), the engine comparison on synthetic engine records, then, with the
editor open, the real 3D engine: scratch areas and portals are created far above the map, asked of the engine (plugin verb bridge.visarea.dump), checked and deleted again.

    test_visarea_check.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import shutil
import struct
import sys
import tempfile
import zipfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import visarea_check as V

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(findings, text="", sev=None):
    return sorted({f["code"] for f in findings if (text in f["message"] or text in f["where"]) and (sev is None or f["severity"] == sev)})


def where_of(findings, code):
    return sorted(f["where"].split(" ")[0] for f in findings if f["code"] == code)


def obj_xml(name, typ, npoints):
    pts = "".join('<Point Pos="%d,0,0"/>' % i for i in range(npoints))
    return '<Object Type="%s" Name="%s" Layer="main" Pos="0,0,0"><Points>%s</Points></Object>' % (typ, name, pts)


def make_level(root, objects, counts=None):
    """a level folder with one layer file and (when counts is given) a level.pak holding terrain/indoor.dat"""
    lvl = os.path.join(root, "lv")
    os.makedirs(os.path.join(lvl, "layers"))
    with open(os.path.join(lvl, "layers", "main.lyr"), "w", encoding="utf-8") as f:
        f.write('<Layer><LayerObjects>%s</LayerObjects></Layer>' % "".join(obj_xml(*o) for o in objects))
    if counts is not None:
        with zipfile.ZipFile(os.path.join(lvl, "level.pak"), "w") as z:
            z.writestr("terrain/indoor.dat", struct.pack(V.HEADER, 6, 0, 0, 0, 20, *counts))
            z.writestr("terrain/other.dat", b"x")
    return os.path.join(root)


def area(name, portal=False, connections=(), outdoor=False):
    return {"id": 0, "name": name, "portal": portal, "connected_to_outdoor": outdoor, "affected_by_sun": False, "ignore_gi": False, "ignore_outdoor_ao": False, "flags": 16,
            "connections": list(connections), "connections_enabled": list(connections), "points": [], "aabb": [[0, 0, 0], [1, 1, 1]]}


def main():
    tmp = tempfile.mkdtemp(prefix="zz_mcp_visarea_test_")
    try:
        # ---- the naming rule, from the engine's source (CVisAreaManager::UpdateVisArea) -----------------------------------------------------------
        K = V.classify
        check("a VisArea named VisArea1 (the editor's default) is a vis area; any case", K("VisArea1", "VisArea")[0] == "visarea" and K("MY_VISAREA", "VisArea")[0] == "visarea", K("VisArea1", "VisArea"))
        check("a VisArea named Reeve_interior has no word the engine looks for: it is held in NO list", K("Reeve_interior", "VisArea")[0] == "none", K("Reeve_interior", "VisArea"))
        check("a Portal gets the editor's Portal_ prefix, so it is always a portal; an OccluderArea gets OcclArea_", K("door", "Portal") == ("portal", "portal_door") and K("x", "OccluderArea")[0] == "occluder", K("door", "Portal"))
        check("a VisArea with 'portal' in its name is a PORTAL (portal is looked for first); an OccluderArea with 'visarea' in it is a vis area", K("visarea_portal_room", "VisArea")[0] == "portal" and K("visarea_hide", "OccluderArea")[0] == "visarea", K("visarea_hide", "OccluderArea"))

        # ---- the layer files and the exported pack: a clean control, then every fault ----------------------------------------------------------------
        clean = [("Reeve_VisArea1", "VisArea", 4), ("door1", "Portal", 4), ("occ1", "OccluderArea", 4)]
        root = make_level(os.path.join(tmp, "clean"), clean, counts=(1, 1, 1))
        r = V.check("lv", levels_dir=root, with_engine=False)
        check("control: a clean level (a vis area, a portal, an occluder area; the pack says 1, 1, 1) has no finding", r["findings"] == [] and r["objects"] == 3 and r["valid_objects"] == 3, codes(r["findings"]))
        check("the exported counts are read from the header of terrain/indoor.dat", r["exported"] == {"version": 6, "vis_areas": 1, "portals": 1, "occluders": 1, "size": 20}, r["exported"])

        bad = [("Reeve_interior", "VisArea", 4), ("visarea_portal_room", "VisArea", 4), ("VisArea3", "VisArea", 3), ("wide", "Portal", 6), ("visarea_hide", "OccluderArea", 4), ("ok_VisArea", "VisArea", 4)]
        rootb = make_level(os.path.join(tmp, "bad"), bad, counts=(0, 0, 0))
        r = V.check("lv", levels_dir=(rootb), with_engine=False)
        check("V001: the VisArea without the word, the VisArea that is a portal and the OccluderArea that is a vis area are found", where_of(r["findings"], "V001") == ["Reeve_interior", "visarea_hide", "visarea_portal_room"], where_of(r["findings"], "V001"))
        check("V002: the VisArea with 3 points is found (and no other)", where_of(r["findings"], "V002") == ["VisArea3"], where_of(r["findings"], "V002"))
        check("V010: the 6-point portal is found; the good ones are not named by any finding", where_of(r["findings"], "V010") == ["wide"] and not any("ok_VisArea" in f["where"] for f in r["findings"]), codes(r["findings"]))
        check("V007: the pack says 0, 0, 0 while the layer holds a valid vis area and a valid portal: stale export", "V007" in codes(r["findings"]) and r["counts"]["error"] >= 5, codes(r["findings"]))
        check("V007 is silent when the pack matches the valid objects", "V007" not in codes(V.check("lv", levels_dir=(make_level(os.path.join(tmp, "m"), [("a_VisArea", "VisArea", 4), ("door", "Portal", 4)], counts=(1, 1, 0))), with_engine=False)["findings"]), "")
        r = V.check("lv", levels_dir=(make_level(os.path.join(tmp, "none"), [], counts=(0, 0, 0))), with_engine=False)
        check("V009: a level without any area says so (information, not an error)", codes(r["findings"]) == ["V009"] and r["counts"]["error"] == 0, codes(r["findings"]))
        check("a level folder without layers raises", raises(lambda: V.read_objects("lv", os.path.join(tmp, "nowhere"))), "")
        check("a level without a pack has no exported counts (None), a pack without indoor.dat too", V.exported_counts("lv", (make_level(os.path.join(tmp, "np"), []))) is None, "")

        # ---- the engine comparison on synthetic records ----------------------------------------------------------------------------------------------
        rootc = (make_level(os.path.join(tmp, "eng"), [("Hut_VisArea1", "VisArea", 4), ("Hut_door", "Portal", 4)], counts=(1, 1, 0)))
        good = {"available": True, "count": 2, "areas": [area("hut_visarea1", outdoor=True), area("portal_hut_door", portal=True, connections=["hut_visarea1"], outdoor=True)]}
        r = V.check("lv", levels_dir=rootc, engine=good)
        check("control: the engine holds both, the portal reaches the room: no finding", r["findings"] == [] and r["engine_areas"] == 2, codes(r["findings"]))
        floating = {"available": True, "count": 2, "areas": [area("hut_visarea1"), area("portal_hut_door", portal=True, connections=[])]}
        r = V.check("lv", levels_dir=rootc, engine=floating)
        check("V003 + V004: a portal that connects nothing and the room nobody reaches are both found", codes(r["findings"]) == ["V003", "V004"], codes(r["findings"]))
        three = {"available": True, "count": 2, "areas": [area("hut_visarea1"), area("portal_hut_door", portal=True, connections=["hut_visarea1", "a", "b"])]}
        check("V005: a portal with 3 connections is an error", "V005" in codes(V.check("lv", levels_dir=rootc, engine=three)["findings"], sev="error"), "")
        missing = {"available": True, "count": 1, "areas": [area("hut_visarea1", outdoor=True)]}
        r = V.check("lv", levels_dir=rootc, engine=missing)
        check("V006: the portal in the layer file that the engine does not hold is found", where_of(r["findings"], "V006") == ["Hut_door"], codes(r["findings"]))
        extra = {"available": True, "count": 3, "areas": good["areas"] + [area("stray_visarea")]}
        r = V.check("lv", levels_dir=rootc, engine=extra)
        check("V008 (information): an area in the engine that no layer object explains", where_of(r["findings"], "V008") == ["stray_visarea"] and r["counts"]["error"] == 0, codes(r["findings"]))
        check("an engine record that is not available adds nothing (the file checks stand)", V.check("lv", levels_dir=rootc, engine={"available": False, "count": 0, "areas": []})["findings"] == [], "")

        # ---- the project's own level, no editor needed ------------------------------------------------------------------------------------------------
        if os.path.isdir(os.path.join(__import__("project_root").ASSETS, "levels", "ashvale")):
            r = V.check("ashvale", with_engine=False)
            check("the project's own level (files only): no error", r["counts"]["error"] == 0, [f["message"][:60] for f in r["findings"] if f["severity"] == "error"])
        else:
            print("  SKIPPED: the project has no level named 'ashvale' -- the project-level check is not run")

        # ---- the real engine -----------------------------------------------------------------------------------------------------------------------------
        live = False
        try:
            base = V.engine_dump()
            live = bool(base.get("available"))
        except Exception as exc:                                              # noqa: BLE001
            print("  (the editor or the verb does not answer: %s)" % str(exc)[:80])
        if live:
            run_live(base)
        else:
            print("  (editor not answering: the live checks are skipped)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


def raises(fn):
    try:
        fn()
    except V.VisAreaError:
        return True
    return False


def run_live(base):
    import bridge_py
    import object_props as OP
    # far above the highest terrain of the level (Ashvale's is 190 m): no render node is inside these boxes. CRYENGINE_TEST_SCRATCH_Z raises it for a taller world.
    Z = float(os.environ.get("CRYENGINE_TEST_SCRATCH_Z", "300"))

    def sq(cx, cy, h):
        return [[cx - h, cy - h, Z], [cx + h, cy - h, Z], [cx + h, cy + h, Z], [cx - h, cy + h, Z]]

    def add(name, pts):
        r = bridge_py.send("bridge.shape.add_points %s - " % name + " ".join("%g %g %g" % tuple(p) for p in pts), 60)
        assert r.get("ok"), r

    def names(d):
        return {a["name"]: a for a in d["areas"]}

    n0 = base["count"]
    with OP.scratch("zz_mcp_va_"):
        OP.create("VisArea", "zz_mcp_va_visarea_room", [100, 100, Z])
        add("zz_mcp_va_visarea_room", sq(100, 100, 3))
        OP.create("VisArea", "zz_mcp_va_plain", [140, 100, Z])
        add("zz_mcp_va_plain", sq(140, 100, 3))
        OP.create("VisArea", "zz_mcp_va_visarea_tri", [180, 100, Z])
        add("zz_mcp_va_visarea_tri", [[177, 97, Z], [183, 97, Z], [180, 103, Z]])
        OP.create("VisArea", "zz_mcp_va_visarea_xml", [220, 100, Z])
        OP.set_shape("zz_mcp_va_visarea_xml", sq(220, 100, 3))
        probes = [[100, 100, Z + 1.5], [100, 100, Z + 8], [110, 100, Z + 1.5], [220, 100, Z + 1.5], [140, 100, Z + 1.5]]
        d = V.engine_dump(probes)
        have = names(d)
        check("live: the room whose name holds 'visarea' is held by the 3D engine (4 points, height 5)", "zz_mcp_va_visarea_room" in have and not have["zz_mcp_va_visarea_room"]["portal"], sorted(have))
        check("live: its box is exactly the 6 x 6 m square asked for, 5 m high (add_points no longer displaces the points of an object not made on its first point)", have["zz_mcp_va_visarea_room"]["aabb"] == [[97.0, 97.0, Z], [103.0, 103.0, Z + 5.0]], have["zz_mcp_va_visarea_room"]["aabb"])
        check("live control: the same square WITHOUT the word in the name is not held: the engine keeps it in no list", "zz_mcp_va_plain" not in have, sorted(have))
        check("live control: 3 points, right name: not held (the editor registers an area only with more than 3)", "zz_mcp_va_visarea_tri" not in have, sorted(have))
        check("live: the XML-loaded shape (object_props.set_shape) with the right name is held too: the route does not matter, the name does", "zz_mcp_va_visarea_xml" in have, sorted(have))
        check("live: the count grew by exactly the two that count", d["count"] == n0 + 2, (n0, d["count"]))
        pr = d["probes"]
        check("live: a point inside the room is in it (GetVisAreaFromPos and the direct test agree)", pr[0]["area"] == "zz_mcp_va_visarea_room" and pr[0]["inside"] == ["zz_mcp_va_visarea_room"], pr[0])
        check("live control: 8 m above the floor (the room is 5 m high), and 7 m outside the wall: in no area", pr[1]["area"] is None and pr[1]["inside"] == [] and pr[2]["area"] is None, (pr[1], pr[2]))
        check("live control: inside the plain (unregistered) square is in no area; inside the XML room is in the XML room", pr[4]["area"] is None and pr[3]["area"] == "zz_mcp_va_visarea_xml", (pr[3]["area"], pr[4]["area"]))
        check("live: a room with no portal is closed (not connected to the outdoors)", have["zz_mcp_va_visarea_room"]["connected_to_outdoor"] is False, "")

        OP.create("Portal", "zz_mcp_va_door", [103, 100, Z])
        OP.set_properties("zz_mcp_va_door", props={"Height": "3"})
        add("zz_mcp_va_door", [[102, 99, Z], [104, 99, Z], [104, 101, Z], [102, 101, Z]])
        OP.create("Portal", "zz_mcp_va_floating", [100, 100, Z])
        OP.set_properties("zz_mcp_va_floating", props={"Height": "3"})
        add("zz_mcp_va_floating", [[99, 99, Z], [101, 99, Z], [101, 101, Z], [99, 101, Z]])
        d = V.engine_dump([[103, 100, Z + 1.5], [101, 100, Z + 1.5]])
        have = names(d)
        door, room, fl = have["portal_zz_mcp_va_door"], have["zz_mcp_va_visarea_room"], have["portal_zz_mcp_va_floating"]
        check("live: the door portal (4 points, its opposite edges crossing the east wall) connects the room, and both are connected to the outdoors", door["portal"] and door["connections"] == ["zz_mcp_va_visarea_room"] and door["connected_to_outdoor"] and room["connected_to_outdoor"], (door["connections"], room["connected_to_outdoor"]))
        check("live control: the portal lying inside the room connects nothing and is not connected to the outdoors", fl["portal"] and fl["connections"] == [] and not fl["connected_to_outdoor"], fl["connections"])
        check("live: a point in the doorway is in the portal, one inside the room is in the room", d["probes"][0]["area"] == "portal_zz_mcp_va_door" and d["probes"][1]["area"] == "zz_mcp_va_visarea_room", [p["area"] for p in d["probes"]])

        live_objects = [{"name": n, "type": t, "layer": "live", "file": "", "points": 4} for n, t in (("zz_mcp_va_visarea_room", "VisArea"), ("zz_mcp_va_plain", "VisArea"), ("zz_mcp_va_door", "Portal"), ("zz_mcp_va_floating", "Portal"), ("zz_mcp_va_visarea_xml", "VisArea"))]
        r = V.check("lv", engine=d, objects=live_objects, exported=None)
        check("live: the checker, given these objects and the engine's record: V001 for the plain room, V003 for the floating portal, V004 for the XML room that no portal reaches", where_of(r["findings"], "V001") == ["zz_mcp_va_plain"] and where_of(r["findings"], "V003") == ["portal_zz_mcp_va_floating"] and where_of(r["findings"], "V004") == ["zz_mcp_va_visarea_xml"], codes(r["findings"]))
        check("live control: the room that the door reaches and the door itself get no finding", not any(f["where"].startswith(("zz_mcp_va_visarea_room", "portal_zz_mcp_va_door")) for f in r["findings"] if f["code"] in ("V003", "V004", "V005")), codes(r["findings"]))
    after = V.engine_dump()
    check("live: the scratch objects are gone and the engine holds what it held before (they are deleted with the objects)", after["count"] == n0 and not [a for a in after["areas"] if a["name"].find("zz_mcp_va_") >= 0], (n0, after["count"]))


if __name__ == "__main__":
    main()
