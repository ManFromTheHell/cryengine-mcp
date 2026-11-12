#!/usr/bin/env python3
"""Read and set the properties of ANY editor object, through the engine's own serializer (ENGINE_LEARNING_PLAN A9 / F1).

The Sandbox Python API has no property setter for a River's width, a Road's tile length, a Rope's mass or a Decal's sort priority. The editor itself saves every object to XML through
CBaseObject::Serialize into a CObjectArchive (that is what a layer file holds) and loads it back the same way. The bridge plugin exposes exactly that:

    bridge.object.xml <name>                         the object as the editor would save it
    bridge.object.set <name> <base64 of lines>       `Key=value` sets an attribute of the Object node, `Child/Key=value` one of a child node, `@Tag=<xml>` replaces a child
                                                     node: the object is saved to XML, edited, and loaded back into the SAME object inside one undo step

This module is the Python side: parse the XML, build the lines, and compute a spline's points (the editor's own Bezier smoothing, ported from
EditorQt/Objects/SplineObject.cpp BezierAnglesCorrection, because CSplineObject is not exported to plugins).

A graceful close of the editor SAVES the level: never leave an experiment's objects in it (`scratch()` deletes them in a finally block).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import base64
import contextlib
import json
import math
import os
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import bridge_py  # noqa: E402


class ObjectPropsError(RuntimeError):
    pass


def _call(cmd, timeout=60):
    reply = bridge_py.send(cmd, timeout)
    if not reply.get("ok"):
        raise ObjectPropsError(reply.get("error", "the bridge refused %r" % cmd))
    try:
        return json.loads(reply.get("output") or "{}")
    except ValueError:
        return {"raw": reply.get("output")}


def get_xml(name):
    """The object's XML element (attributes = its properties; children = Points, Rope, Properties ...)."""
    out = _call("bridge.object.xml " + name)
    return ET.fromstring(out["xml"])


def properties(name):
    """{attribute: value} of the Object node (type-specific properties plus Pos, Rotate, Scale, Layer ...)."""
    return dict(get_xml(name).attrib)


def set_properties(name, props=None, children=None):
    """Set attributes (`props`: {key or 'Child/key': value}) and replace child nodes (`children`: {tag: '<Tag .../>' xml text, one line}).
    Returns the bridge's answer plus 'before'/'after' dicts of the attributes that changed, so a silent no-op is visible."""
    before = properties(name)
    lines = []
    for k, v in (props or {}).items():
        if "=" in k or "\n" in str(v):
            raise ObjectPropsError("a key cannot contain '=' and a value cannot contain a newline: %r" % k)
        lines.append("%s=%s" % (k, v))
    for tag, xml in (children or {}).items():
        if "\n" in xml:
            raise ObjectPropsError("child XML must be one line")
        lines.append("@%s=%s" % (tag, xml))
    if not lines:
        raise ObjectPropsError("nothing to set")
    out = _call("bridge.object.set %s %s" % (name, base64.b64encode("\n".join(lines).encode("utf-8")).decode("ascii")))
    after = dict(ET.fromstring(out["xml"]).attrib)
    changed = {k: {"before": before.get(k), "after": after.get(k)} for k in (props or {}) if "/" not in k and str(before.get(k)) != str(after.get(k))}
    unchanged = [k for k in (props or {}) if "/" not in k and str(after.get(k)) != str(props[k])]
    out["changed_values"] = changed
    out["not_applied"] = unchanged              # asked for, but the object does not hold that value afterwards (unknown key, clamped, or refused)
    return out


# ---- splines (Road, River, SplineDistributor ...) ---------------------------------------------------------------------------------------------

def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _add(a, b):
    return [a[i] + b[i] for i in range(3)]


def _mul(a, k):
    return [a[i] * k for i in range(3)]


def _len(a):
    return math.sqrt(sum(c * c for c in a))


def _correct(pts, i):
    """CSplineObject::BezierAnglesCorrection(i) on the list `pts` as it is now (its last index is len-1)."""
    last = len(pts) - 1
    if i < 0 or i > last:
        return
    p2 = pts[i]["pos"]
    if i == 0:
        pts[i]["back"] = list(p2)
        if last == 1:
            pts[i]["forw"] = _add(p2, _mul(_sub(pts[1]["pos"], p2), 1 / 3.0))
        elif last > 0:
            p3, pb3 = pts[1]["pos"], pts[1]["back"]
            len_osn, len_b = _len(_sub(pb3, p2)), _len(_sub(p3, p2))
            if len_osn > 0 and len_b > 0:
                pts[i]["forw"] = _add(p2, _mul(_sub(pb3, p2), 1.0 / (len_osn / len_b * 3)))
    if i == last:
        pts[i]["forw"] = list(p2)
        if i > 0:
            p1, pf1 = pts[i - 1]["pos"], pts[i - 1]["forw"]
            len_osn, len_f = _len(_sub(pf1, p2)), _len(_sub(p1, p2))
            pts[i]["back"] = _add(p2, _mul(_sub(pf1, p2), 1.0 / (len_osn / len_f * 3))) if len_osn > 1e-6 and len_f > 1e-6 else list(p2)
    if 1 <= i <= last - 1:
        p1, p3 = pts[i - 1]["pos"], pts[i + 1]["pos"]
        len_osn, len_b, len_f = _len(_sub(p3, p1)), _len(_sub(p1, p2)), _len(_sub(p3, p2))
        if len_osn > 0:
            pts[i]["back"] = _add(p2, _mul(_sub(p1, p3), len_b / len_osn / 3))
            pts[i]["forw"] = _add(p2, _mul(_sub(p3, p1), len_f / len_osn / 3))


def bezier_points(local_points):
    """[{pos, back, forw}] for LOCAL points, as the editor leaves them when the points are placed one after another (InsertPoint then BezierCorrection(index):
    indices index-1, index, index+1, index-2, index+2)."""
    pts = []
    for k, p in enumerate(local_points):
        pts.append({"pos": [float(c) for c in p], "back": [float(c) for c in p], "forw": [float(c) for c in p]})
        for j in (k - 1, k, k + 1, k - 2, k + 2):
            _correct(pts, j)
    return pts


def spline_xml(origin, world_points, widths=None, angles=None):
    """The <Points> node for an object at `origin`: world points are stored LOCAL to it (point - origin), with the editor's smoothing tangents."""
    local = [_sub(p, origin) for p in world_points]
    pts = bezier_points(local)
    f = lambda v: ",".join(("%.6g" % c) for c in v)
    out = ["<Points>"]
    for i, p in enumerate(pts):
        w = widths[i] if widths and widths[i] is not None else 0
        out.append('<Point Pos="%s" Back="%s" Forw="%s" Angle="%g" Width="%g" IsDefaultWidth="%d"/>' % (f(p["pos"]), f(p["back"]), f(p["forw"]), (angles[i] if angles else 0), w, 0 if (widths and widths[i] is not None) else 1))
    out.append("</Points>")
    return "".join(out)


def set_spline(name, world_points, widths=None, angles=None):
    """Replace a Road/River's points. World coordinates; the object's own position is kept as the origin (the first point is moved to it if it is not there)."""
    if len(world_points) < 2:
        raise ObjectPropsError("a spline needs at least 2 points")
    origin = [float(c) for c in properties(name)["Pos"].split(",")]
    return set_properties(name, children={"Points": spline_xml(origin, world_points, widths, angles)})


SHAPE_TYPES = {"WaterVolume", "Shape", "AIPath", "AIShape", "AIHorizontalOcclusionPlane", "NavigationArea", "VisArea", "Portal", "OccluderArea"}


def is_shape(name):
    """True for the area/shape objects (a closed or open polygon: <Points><Point Pos/>, no Bezier handles), False for the splines (Road, River ...)."""
    return properties(name).get("Type") in SHAPE_TYPES


def set_shape(name, world_points):
    """Replace a shape object's points (WaterVolume, Shape, VisArea, Portal, OccluderArea, AIShape, NavigationArea ...) from WORLD positions. The editor stores them LOCAL to the object and then
    RE-CENTRES the object (CShapeObject::CalcBBox moves its position to the minimum corner of the points and shifts the points to match): measured, the world points stay where they were
    asked, the object's Pos and the local points change. -> {"pos": the new position, "world_points": what the object now holds, in world coordinates}"""
    if len(world_points) < 3:
        raise ObjectPropsError("a shape needs at least 3 points")
    origin = [float(c) for c in properties(name)["Pos"].split(",")]
    f = lambda v: ",".join(("%.6g" % c) for c in v)
    xml = "<Points>" + "".join('<Point Pos="%s"/>' % f(_sub(p, origin)) for p in world_points) + "</Points>"
    set_properties(name, children={"Points": xml})
    x = get_xml(name)
    pos = [float(c) for c in x.attrib["Pos"].split(",")]
    held = [_add(pos, [float(c) for c in pt.attrib["Pos"].split(",")]) for pt in x.find("Points").findall("Point")]
    return {"pos": pos, "world_points": held}


def point_count(name):
    pts = get_xml(name).find("Points")
    return 0 if pts is None else len(pts.findall("Point"))


# ---- experiments that cannot leak into the level --------------------------------------------------------------------------------------------

def object_count():
    return int(bridge_py.run_source("print(general.get_objects_count())")["log"].strip().splitlines()[-1])


def names_like(prefix):
    out = _call("bridge.object.list " + prefix)
    return [o["name"] for o in (out if isinstance(out, list) else out.get("objects", [])) if o["name"].startswith(prefix)]


def delete(names):
    for n in names:
        bridge_py.run_source("object.delete(%r)" % n)


@contextlib.contextmanager
def scratch(prefix):
    """Everything an experiment creates must have a name starting with `prefix`; whatever the experiment does (or fails to), those objects are deleted afterwards and the object count must be
    what it was. A graceful editor close saves the level, so an object left behind would be saved."""
    before_n = object_count()
    stale = names_like(prefix)
    if stale:
        raise ObjectPropsError("objects with the prefix %r already exist: %s" % (prefix, stale[:5]))
    try:
        yield
    finally:
        left = names_like(prefix)
        delete(left)
        after_n = object_count()
        if after_n != before_n or names_like(prefix):
            raise ObjectPropsError("the experiment left the level with %d objects instead of %d (%s): delete them by hand" % (after_n, before_n, names_like(prefix)[:5]))


# ---- what the MCP tools call --------------------------------------------------------------------------------------------------------------------

import re  # noqa: E402

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(/[A-Za-z_][A-Za-z0-9_]*)?$")
PROTECTED = {"Type", "Id", "LayerGUID", "Layer"}            # changing these would detach the object from what the editor knows about it


def object_types():
    """The editor's own list of object types [{category, type, fileSpec}] (bridge.object.types)."""
    out = _call("bridge.object.types")
    return out if isinstance(out, list) else out.get("types", [])


def exists(name):
    out = _call("bridge.object.list " + name)
    rows = out if isinstance(out, list) else out.get("objects", [])
    return any(o.get("name") == name for o in rows)


def create(type_name, name, position, geometry="", undoable=False):
    """Create an editor object of an engine type at a world position (the level is NOT saved). Refuses an unknown type, a bad or taken name.
    undoable: record an editor undo step ("Claude: create <Type> (<name>)", so the operator's Ctrl+Z removes it): for objects the operator requested; the scratch objects of tests leave it off."""
    types = {t["type"].lower(): t["type"] for t in object_types()}
    if type_name.lower() not in types:
        raise ObjectPropsError("unknown object type %r: the editor has %s" % (type_name, ", ".join(sorted(types.values()))))
    if not NAME.match(name or ""):
        raise ObjectPropsError("name %r must start with a letter and use letters, digits and _ (64 at most)" % name)
    if len(position) != 3 or not all(isinstance(c, (int, float)) and abs(c) < 4200 for c in position):
        raise ObjectPropsError("position must be three numbers (metres, inside +-4200)")
    if exists(name):
        raise ObjectPropsError("an object named %r already exists" % name)
    cmd = "bridge.object.create %s %.4f %.4f %.4f %s" % (types[type_name.lower()], position[0], position[1], position[2], name) + ((" " + geometry) if geometry else "")
    if undoable:
        _call("bridge.undo.mode on")
    try:
        out = _call(cmd)
    finally:
        if undoable:
            _call("bridge.undo.mode off")
    out["properties"] = properties(name)
    out["undoable"] = bool(undoable)
    area = types[type_name.lower()]
    if area in ("VisArea", "Portal", "OccluderArea"):
        import visarea_check                          # the engine classifies an area by a word in its name (visarea_check.py): a name without it is a silent no-op
        kind, ename = visarea_check.classify(name, area)
        if kind != visarea_check.EXPECTED_KIND[area]:
            out["warnings"] = ["the 3D engine will %s: it classifies an area by a word in its lower-cased name (portal, then visarea, then occlarea) and the name it is given, %r, makes it %s. "
                               "Rename the object (a VisArea's name must contain 'visarea' and not 'portal'); an area also needs 4 or more points (spline_points) to be registered at all."
                               % ("keep this VisArea in no list (it never exists)" if kind == "none" else "treat this %s as a %s" % (area, kind), ename, "nothing" if kind == "none" else "a " + kind)]
    return out


def checked_set(name, props=None, children=None):
    """set_properties with the keys validated (and the ones that would detach the object refused)."""
    for k in (props or {}):
        if not KEY.match(k):
            raise ObjectPropsError("property key %r: letters, digits, _ and one '/' for a child node" % k)
        if k in PROTECTED:
            raise ObjectPropsError("%s cannot be changed (it ties the object to the level)" % k)
    for tag in (children or {}):
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tag):
            raise ObjectPropsError("child tag %r is not a name" % tag)
    return set_properties(name, props, children)
