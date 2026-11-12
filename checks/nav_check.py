#!/usr/bin/env python3
"""The navigation mesh asked with the engine's own questions, from the editor, without running the game (ENGINE_LEARNING_PLAN D1).

Bridge verb `bridge.nav.query` calls INavigationSystem::IsPointReachableFromPosition (the engine's cheap connectivity test: "are these two points connected on the NavMesh, without a full path"),
FindEnclosingMeshID / IsLocationInMeshVolume (which mesh volume a point lies in) and NavMeshRayCast. What it does NOT know: dynamic obstacles and OFF-MESH links. This project cuts every house door
out of the mesh on purpose (a `<House>_DoorCut` exclusion area) and crosses it with the FarmGate's off-mesh link, so a house INTERIOR is an island of the mesh by design: the check answers for
the outdoors, the doorsteps and the open-sided buildings.

    python checks/nav_check.py agents
    python checks/nav_check.py reach <from object or x,y,z> <to object or x,y,z> [...]
    python checks/nav_check.py map <x0> <y0> <x1> <y1> [step] [z]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import base64
import json
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)


class NavError(RuntimeError):
    pass


def _send(command, timeout=120):
    import bridge_py
    r = bridge_py.send(command, timeout)
    if not r.get("ok"):
        raise NavError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % r.get("error"))
    return json.loads(r["output"])


def agents():
    """[{id, name, meshes: [{id, name}]}]: the navigation agent types the editor has and the meshes built for each."""
    return _send("bridge.nav.query")["agent_types"]


def _is_coords(p):
    return isinstance(p, str) and "," in p and all(part.replace(".", "", 1).replace("-", "", 1).isdigit() for part in p.split(","))


def objects(wanted=None):
    """{name: pos} of the level's objects. With `wanted` (object names), each is looked up by its own filtered call (bridge.object.list stops near 500 objects, so the
    full list can miss a name in a big level); without, the whole (capped) list."""
    import bridge_py
    if not wanted:
        return {o["name"]: o["pos"] for o in json.loads(bridge_py.send("bridge.object.list", 30)["output"])}
    out = {}
    for w in wanted:
        for o in json.loads(bridge_py.send("bridge.object.list " + w, 30)["output"]):
            if o["name"].lower() == w.lower():
                out[w] = o["pos"]
                break
    return out


def _point(p, names):
    if isinstance(p, str):
        if _is_coords(p):
            return [float(c) for c in p.split(",")]
        if p not in names:
            raise NavError("no object named %r in the level" % p)
        return [float(c) for c in names[p]]
    if len(p) != 3:
        raise NavError("a point is x, y, z (metres) or the name of an object")
    return [float(c) for c in p]


def reach(origin, targets, agent="-"):
    """Is each target connected to `origin` on the navmesh? origin / targets: [x, y, z] or an object's name. -> {agent, segments: [{label, reachable, start_mesh, end_mesh, start_in_volume,
    end_in_volume, ray}]} (ray: the NavMesh ray cast result between them as the engine's enum: 0 none/clear, 1 = hit a boundary...)."""
    wanted = [p for p in [origin] + list(targets) if isinstance(p, str) and not _is_coords(p)]
    names = objects(wanted) if wanted else {}
    a = _point(origin, names)
    lines = []
    for i, t in enumerate(targets):
        b = _point(t, names)
        label = t if isinstance(t, str) and "," not in t else "t%d" % i
        lines.append("%s %g %g %g %g %g %g" % (label.replace(" ", "_"), a[0], a[1], a[2], b[0], b[1], b[2]))
    out = _send("bridge.nav.query %s %s" % (agent, base64.b64encode("\n".join(lines).encode()).decode()))
    return {"agent": out.get("agent"), "from": a, "segments": out.get("segments", [])}


def map_area(x0, y0, x1, y1, step=5.0, z=None, origin=None, agent="-"):
    """An ASCII map of the rectangle seen from `origin` (an object name or x,y,z; default: the object named by the environment variable NAV_ORIGIN_OBJECT, else 'AshvaleStart') at height `z` (default: the origin's own z): '#' connected to the origin, '.' inside a mesh volume but NOT connected, ' ' outside every volume.
    Rows run from the top (high y) down; one character per `step` metres."""
    origin = origin if origin else os.environ.get("NAV_ORIGIN_OBJECT", "AshvaleStart")
    names = objects([origin]) if isinstance(origin, str) and not _is_coords(origin) else {}
    o = _point(origin, names)
    if z is None:
        z = o[2]
    xs = [x0 + step * i for i in range(int((x1 - x0) / step) + 1)]
    ys = [y0 + step * i for i in range(int((y1 - y0) / step) + 1)]
    if len(xs) * len(ys) > 4000:
        raise NavError("at most 4000 cells per map (%d asked): widen the step" % (len(xs) * len(ys)))
    lines = ["c_%d_%d %g %g %g %g %g %g" % (i, j, o[0], o[1], o[2], x, y, z) for j, y in enumerate(ys) for i, x in enumerate(xs)]
    segs = {s["label"]: s for s in _send("bridge.nav.query %s %s" % (agent, base64.b64encode("\n".join(lines).encode()).decode()), 300)["segments"]}
    rows = []
    for j in range(len(ys) - 1, -1, -1):
        rows.append("%6.0f %s" % (ys[j], "".join("#" if segs["c_%d_%d" % (i, j)]["reachable"] else ("." if segs["c_%d_%d" % (i, j)]["end_in_volume"] else " ") for i in range(len(xs)))))
    return {"origin": o, "z": z, "step": step, "x_from": xs[0], "x_to": xs[-1], "rows": rows, "legend": "# connected to the origin, . inside a mesh volume but not connected, blank outside every volume"}


def main():
    a = sys.argv[1:]
    if not a or a[0] == "agents":
        print(json.dumps(agents(), indent=1))
    elif a[0] == "reach":
        r = reach(a[1], a[2:])
        for s in r["segments"]:
            print("%-16s reachable=%s meshes %s -> %s" % (s["label"], s["reachable"], s["start_mesh"], s["end_mesh"]))
    elif a[0] == "map":
        m = map_area(float(a[1]), float(a[2]), float(a[3]), float(a[4]), float(a[5]) if len(a) > 5 else 5.0, float(a[6]) if len(a) > 6 else None)
        print("\n".join(m["rows"]))
        print(m["legend"])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
