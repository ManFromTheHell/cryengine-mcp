#!/usr/bin/env python3
"""Vis areas, portals and occluder areas as the 3D ENGINE holds them (ENGINE_LEARNING_PLAN F4), checked against the level's layer files and the exported level pack, without running the game.

The manual (Vis Area, Portal, Occlusion pages): objects inside a Vis Area are not rendered from outside and vice versa, a Portal cuts an entrance into it and has to protrude on both sides of the wall, a closed
area is invisible from outside. The ENGINE adds rules the manual does not state, read from its source (Cry3DEngine/VisAreaMan.cpp UpdateVisArea, VisAreas.cpp) and MEASURED in the open editor (the plugin verb
`bridge.visarea.dump` asks I3DEngine::GetIVisAreaManager for every area it holds):

  * it classifies an area by a SUBSTRING OF ITS NAME, lower case, in this order: "portal" -> portal, "visarea" -> vis area, "occlarea" -> occluder, anything else -> held in NO list (silently not an area).
    The editor passes a VisArea object's own name, a Portal's "Portal_<name>", an OccluderArea's "OcclArea_<name>": so a VisArea called "Reeve_interior" does nothing, and one called "visarea_portal_room" is a portal.
  * the editor registers an area only with MORE THAN 3 points (VisAreaShapeObject.cpp UpdateGameArea);
  * a portal connects two areas only when its first 4 points form a quad whose two opposite edges cross a wall of the area (VisAreas.cpp IsPortalIntersectAreaInValidWay): one that lies inside a room, or does
    not reach through the wall, connects nothing; a valid portal has 1 connection (to the outdoors) or 2 (room to room), never more.

  V001 a VisArea / Portal / OccluderArea object whose name the engine will not classify as that kind (file)          V006 a layer object the 3D engine does not hold (live editor)
  V002 an area object with fewer than 4 points: the editor never registers it (file)                                  V007 the exported level pack's terrain/indoor.dat counts differ from the layer (stale export)
  V003 a portal that connects no vis area (live)                                                                       V008 the engine holds an area that no layer object explains (unsaved, or made at run time)
  V004 a vis area no portal reaches: closed, invisible from outside (live)                                             V009 the level has no vis areas at all (information)
  V005 a portal with more than 2 connections (invalid for the engine) (live)                                           V010 a portal with more than 4 points: only the first 4 are tested (file)

    python checks/visarea_check.py [level folder name]        default: ashvale
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
LEVELS = os.path.join(PROJECT, "Assets", "levels")

sys.path.insert(0, HERE)

AREA_TYPES = ("VisArea", "Portal", "OccluderArea")
MIN_POINTS = 4                                              # CVisAreaShapeObject::UpdateGameArea: GetPointCount() > 3
ENGINE_PREFIX = {"VisArea": "", "Portal": "Portal_", "OccluderArea": "OcclArea_"}
EXPECTED_KIND = {"VisArea": "visarea", "Portal": "portal", "OccluderArea": "occluder"}
HEADER = "<bbbbiiii"                                         # SVisAreaManChunkHeader: nVersion, nDummy, nFlags, nFlags2, nChunkSize, nVisAreasNum, nPortalsNum, nOcclAreasNum


class VisAreaError(RuntimeError):
    pass


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def classify(name, obj_type):
    """What the engine makes of an area object: (kind, the name it is given). CVisAreaManager::UpdateVisArea looks for "portal", then "visarea", then "occlarea" in the LOWER-CASED name the editor passes;
    a name with none of them gives kind "none" (the area is created and kept in no list)."""
    engine_name = (ENGINE_PREFIX.get(obj_type, "") + name).lower()
    for word, kind in (("portal", "portal"), ("visarea", "visarea"), ("occlarea", "occluder")):
        if word in engine_name:
            return kind, engine_name
    return "none", engine_name


def read_objects(level="ashvale", levels_dir=LEVELS):
    """-> [{name, type, layer, file, points}] for every VisArea / Portal / OccluderArea object in the level's layer files"""
    folder = os.path.join(levels_dir, level, "layers")
    if not os.path.isdir(folder):
        raise VisAreaError("no layers folder at %s" % folder)
    out = []
    for f in sorted(os.listdir(folder)):
        if not f.lower().endswith(".lyr"):
            continue
        try:
            root = ET.parse(os.path.join(folder, f)).getroot()
        except ET.ParseError as exc:
            raise VisAreaError("%s is not valid XML: %s" % (f, exc)) from exc
        for o in root.iter("Object"):
            if o.get("Type") not in AREA_TYPES:
                continue
            pts = o.find("Points")
            out.append({"name": o.get("Name", ""), "type": o.get("Type"), "layer": o.get("Layer", os.path.splitext(f)[0]), "file": f,
                        "points": 0 if pts is None else len(pts.findall("Point"))})
    return out


def exported_counts(level="ashvale", levels_dir=LEVELS):
    """The exported level pack's compiled area data (terrain/indoor.dat, SVisAreaManChunkHeader) -> {version, vis_areas, portals, occluders, size} | None when there is no pack or no such file"""
    pak = os.path.join(levels_dir, level, "level.pak")
    if not os.path.isfile(pak):
        return None
    with zipfile.ZipFile(pak) as z:
        for zi in z.infolist():
            if zi.filename.replace("\\", "/").lower().endswith("terrain/indoor.dat"):
                data = z.read(zi)                                        # through the entry itself: a pack may store backslash names
                if len(data) < struct.calcsize(HEADER):
                    return {"version": None, "vis_areas": None, "portals": None, "occluders": None, "size": len(data)}
                version, _d, _f, _f2, _size, vis, portals, occl = struct.unpack(HEADER, data[:struct.calcsize(HEADER)])
                return {"version": version, "vis_areas": vis, "portals": portals, "occluders": occl, "size": len(data)}
    return None


def engine_dump(probes=None, timeout=60):
    """Ask the open editor's 3D engine for its vis areas / portals (plugin verb bridge.visarea.dump) -> the verb's JSON; probes [(x, y, z)] are looked up too (area that contains each)"""
    import bridge_py
    fd, path = tempfile.mkstemp(prefix="zz_mcp_visarea_", suffix=".json")
    os.close(fd)
    try:
        cmd = "bridge.visarea.dump " + path.replace(os.sep, "/")
        if probes:
            cmd += " | " + " ".join("%.4f %.4f %.4f" % tuple(float(c) for c in p) for p in probes)
        reply = bridge_py.send(cmd, timeout)
        if not reply.get("ok"):
            raise VisAreaError("bridge.visarea.dump failed: %s" % str(reply)[:200])
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def check(level="ashvale", levels_dir=LEVELS, engine=None, with_engine=True, objects=None, exported="read"):
    """-> {"level", "objects", "engine_areas", "exported", "findings", "counts"}; engine: a bridge.visarea.dump result (read from the open editor when None and with_engine)"""
    objs = read_objects(level, levels_dir) if objects is None else objects
    exp = exported_counts(level, levels_dir) if exported == "read" else exported
    findings = []
    expected = {}                                         # engine name -> object, for the objects the editor will register
    valid_by_type = {t: 0 for t in AREA_TYPES}
    for o in objs:
        where = "%s (%s, layer %s)" % (o["name"], o["type"], o["layer"])
        kind, ename = classify(o["name"], o["type"])
        want = EXPECTED_KIND[o["type"]]
        if kind != want:
            word = {"portal": "portal", "visarea": "visarea", "occluder": "occlarea"}
            if kind == "none":
                msg = "the engine keeps this %s in NO list: it classifies an area by a word in its lower-cased name (portal, visarea, occlarea) and %r has none, so it never exists as an area" % (o["type"], ename)
                fix = "rename the object so that its name contains %r (the editor's default name for a new VisArea, VisArea1, does)" % word[want]
            else:
                msg = "the engine makes this %s a %s: the name it is given, %r, contains %r (the engine looks for portal, then visarea, then occlarea)" % (o["type"], kind, ename, word[kind])
                fix = "take %r out of the object's name" % word[kind]
            findings.append(_finding("error", "V001", where, msg, fix))
        if o["points"] < MIN_POINTS:
            findings.append(_finding("error", "V002", where, "%d point(s): the editor registers an area with the 3D engine only with more than 3 (VisAreaShapeObject.cpp UpdateGameArea), so this one never exists" % o["points"],
                                     "give it at least 4 points"))
        if kind == want and o["points"] >= MIN_POINTS:
            expected[ename] = o
            valid_by_type[o["type"]] += 1
        if o["type"] == "Portal" and o["points"] > 4:
            findings.append(_finding("warning", "V010", where, "%d points: the engine tests a portal's connection with its first 4 points only (VisAreas.cpp IsPortalIntersectAreaInValidWay)" % o["points"],
                                     "make the portal a quad (4 points) across the wall"))
    if exp is not None and exp.get("vis_areas") is not None:
        got = (exp["vis_areas"], exp["portals"], exp["occluders"])
        want_counts = (valid_by_type["VisArea"], valid_by_type["Portal"], valid_by_type["OccluderArea"])
        # only a lower bound: a portal/area that the engine cannot place may still be exported; the counts of a clean level are equal
        if got != want_counts:
            findings.append(_finding("error", "V007", "level.pak terrain/indoor.dat",
                                     "the exported pack holds %d vis areas, %d portals, %d occluder areas; the layer files hold %d, %d, %d valid ones: the export is out of date" % (got + want_counts),
                                     "export the level again (File > Export to Engine) before packaging"))
    if not objs:
        findings.append(_finding("info", "V009", level, "the level has no vis areas, portals or occluder areas: every house is open to the outdoors (no interior culling, no indoor lighting rules)"))

    eng = engine
    if eng is None and with_engine:
        try:
            eng = engine_dump()
        except Exception:                                  # noqa: BLE001  (no editor, or an older plugin without the verb)
            eng = None
    held = None
    if eng and eng.get("available"):
        areas = eng.get("areas", [])
        held = len(areas)
        names = {a["name"].lower(): a for a in areas}
        for ename, o in expected.items():
            if ename not in names:
                findings.append(_finding("warning", "V006", "%s (%s)" % (o["name"], o["type"]), "the layer file holds it but the 3D engine does not (looked for %r among %d areas): not loaded into the editor, or changed there and not saved" % (ename, held)))
        for n, a in names.items():
            if n not in expected:
                findings.append(_finding("info", "V008", n, "the 3D engine holds %s %r that no layer object explains (made in the editor and not saved, or by game code)" % ("a portal" if a["portal"] else "an area", n)))
        reached = set()
        for a in areas:
            if a["portal"]:
                conns = [c.lower() for c in a.get("connections", [])]
                reached.update(conns)
                if len(conns) == 0:
                    findings.append(_finding("warning", "V003", a["name"], "this portal connects no vis area: it must be a 4-point quad whose opposite edges cross a wall of the area (half inside, half outside); one inside a room or short of the wall does nothing",
                                             "move it so that it protrudes through the wall on both sides"))
                elif len(conns) > 2:
                    findings.append(_finding("error", "V005", a["name"], "this portal connects %d areas; the engine allows 1 (to the outdoors) or 2 (room to room): %s" % (len(conns), ", ".join(conns)),
                                             "split it into one portal per doorway"))
        for a in areas:
            if not a["portal"] and a["name"].lower() not in reached:
                findings.append(_finding("warning", "V004", a["name"], "no portal reaches this vis area: it is closed, so what is inside is not drawn from outside (and the outdoors is not drawn from inside)",
                                         "add a Portal through the wall at the door or window"))
    counts = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        counts[f["severity"]] += 1
    return {"level": level, "objects": len(objs), "valid_objects": sum(valid_by_type.values()), "engine_areas": held, "exported": exp, "findings": findings, "counts": counts}


def main():
    level = sys.argv[1] if len(sys.argv) > 1 else "ashvale"
    rep = check(level)
    print("level %s: %d area object(s) in the layers (%d valid), engine holds %s, exported pack: %s" % (level, rep["objects"], rep["valid_objects"],
                                                                                                     "?" if rep["engine_areas"] is None else rep["engine_areas"], rep["exported"]))
    for f in rep["findings"]:
        print("  %-7s %s %s: %s" % (f["severity"], f["code"], f["where"], f["message"]))
    print("%(error)d error(s), %(warning)d warning(s), %(info)d information" % rep["counts"])
    return 1 if rep["counts"]["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
