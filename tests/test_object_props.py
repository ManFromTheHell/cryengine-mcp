#!/usr/bin/env python3
"""Self-test of object_props.py: the spline maths offline, then (with the editor open and the level loaded) the real round trip through the engine's serializer. The live part creates
scratch objects named zz_t_*, always deletes them and asserts the object count is what it was. It never saves the level and never runs the game.

    test_object_props.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import object_props as O

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-100s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:50]))


def near(a, b, eps=1e-6):
    return all(abs(x - y) < eps for x, y in zip(a, b))


def main():
    # ---- the editor's Bezier smoothing, ported (CSplineObject::BezierAnglesCorrection): invariants it must keep ----------------------------------------
    pts = O.bezier_points([(0, 0, 0), (10, 0, 0), (20, 0, 0), (30, 0, 0)])
    check("a straight, evenly spaced line keeps every tangent ON the line, one third of a segment away (10/3)",
          all(p["pos"][1] == p["back"][1] == p["forw"][1] == 0 for p in pts) and near(pts[1]["back"], (10 - 10 / 3.0, 0, 0)) and near(pts[1]["forw"], (10 + 10 / 3.0, 0, 0)), [round(c, 2) for c in pts[1]["forw"]])
    check("the first point has no incoming tangent (back == pos) and the last none outgoing (forw == pos)", near(pts[0]["back"], pts[0]["pos"]) and near(pts[-1]["forw"], pts[-1]["pos"]), "")
    zig = O.bezier_points([(0, 0, 0), (10, 5, 0), (20, 0, 0), (30, 5, 0)])
    check("a bend: the tangents at an inner point are parallel to the chord of its neighbours (smooth, no kink)",
          abs((zig[1]["forw"][1] - zig[1]["pos"][1]) * 20 - (zig[2]["pos"][1] - zig[0]["pos"][1]) * (zig[1]["forw"][0] - zig[1]["pos"][0])) < 1e-6, "")
    two = O.bezier_points([(0, 0, 0), (9, 0, 0)])
    check("two points: the outgoing tangent of the first is a third of the way (the editor's maxindex == 1 case)", near(two[0]["forw"], (3, 0, 0)), two[0]["forw"])
    xml = O.spline_xml((100, 200, 30), [(100, 200, 30), (110, 205, 30)], widths=[None, 8])
    check("spline_xml stores points LOCAL to the object and marks an explicit width", 'Pos="0,0,0"' in xml and 'Pos="10,5,0"' in xml and 'IsDefaultWidth="0"' in xml and 'Width="8"' in xml, xml[:70])
    try:
        O.set_properties("x", {"a=b": "1"})
        bad = False
    except O.ObjectPropsError:
        bad = True
    check("a key with '=' or a multi-line value is refused before anything is sent", bad, "")

    # ---- live: the engine's own serializer ----------------------------------------------------------------------------------------------------------
    try:
        n0 = O.object_count()
    except Exception as exc:                                    # noqa: BLE001
        print("  --   the editor is not open with a level (%s): the live checks are skipped" % type(exc).__name__)
        n0 = None
    if n0 is not None:
        import bridge_py
        with O.scratch("zz_t_"):
            r = bridge_py.send("bridge.object.create Road 400 250 40 zz_t_road")
            check("a Road is created through the bridge", r.get("ok") is True, r.get("error"))
            before = O.properties("zz_t_road")
            check("the object's properties come from the engine's serializer (a Road has Width 4, StepSize 4, TileLength 4 by default)", (before["Width"], before["StepSize"], before["TileLength"]) == ("4", "4", "4"), before.get("Width"))
            out = O.set_properties("zz_t_road", {"Width": "7.5", "SortPriority": "2"})
            check("set_properties changes them and the engine reports them back (Width 4 -> 7.5)", O.properties("zz_t_road")["Width"] == "7.5" and out["changed_values"]["Width"] == {"before": "4", "after": "7.5"} and not out["not_applied"], out["changed_values"])
            out = O.set_properties("zz_t_road", {"NoSuchProperty": "1"})
            check("control: a key the object does not have is reported as not applied (the setter does not pretend)", out["not_applied"] == ["NoSuchProperty"] and "NoSuchProperty" not in O.properties("zz_t_road"), out["not_applied"])
            O.set_spline("zz_t_road", [(400, 250, 40), (415, 258, 40), (430, 250, 40)])
            check("a spline's points are set (3) and read back", O.point_count("zz_t_road") == 3, O.point_count("zz_t_road"))
            pts_back = [p.attrib for p in O.get_xml("zz_t_road").find("Points")]
            check("the points come back local to the object, tangents as computed (middle point at 15,8,0)", pts_back[1]["Pos"] == "15,8,0" and pts_back[0]["Pos"] == "0,0,0", pts_back[1]["Pos"])
            bridge_py.send("bridge.object.create WaterVolume 450 250 40 zz_t_water")
            ask = [[430, 230, 40], [470, 230, 40], [470, 270, 41], [430, 270, 41]]
            held = O.set_shape("zz_t_water", ask)
            check("a shape's (WaterVolume) points are set from WORLD positions: the editor re-centres the object on the minimum corner, and the world points stay as asked",
                  O.is_shape("zz_t_water") and not O.is_shape("zz_t_road") and held["pos"] == [430.0, 230.0, 40.0] and held["world_points"] == [[float(c) for c in p] for p in ask], held["pos"])
            try:
                O.set_shape("zz_t_water", ask[:2])
                few = False
            except O.ObjectPropsError:
                few = True
            check("a shape with fewer than 3 points is refused", few, "")
            r = bridge_py.send("bridge.object.create Rope 410 250 40 zz_t_rope")
            out = O.set_properties("zz_t_rope", {"Rope/mass": "3", "Rope/radius": "0.05"})
            rope = O.get_xml("zz_t_rope").find("Rope")
            check("a child node's attributes can be set (Rope/mass, Rope/radius)", float(rope.attrib["mass"]) == 3.0 and abs(float(rope.attrib["radius"]) - 0.05) < 1e-6, (rope.attrib.get("mass"), rope.attrib.get("radius")))
        check("scratch(): every zz_t_ object is gone and the level has the object count it had (%d)" % n0, O.object_count() == n0 and not O.names_like("zz_t_"), O.object_count())
        try:
            with O.scratch("zz_t_"):
                bridge_py.send("bridge.object.create Road 400 250 40 zz_t_boom")
                raise RuntimeError("the experiment fails")
        except RuntimeError:
            pass
        check("control: even when the experiment RAISES, scratch() deletes what it made", O.object_count() == n0 and not O.names_like("zz_t_"), O.object_count())

    bad = results.count(False)
    print("object props test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
