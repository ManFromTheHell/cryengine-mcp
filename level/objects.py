#!/usr/bin/env python3
"""Place and delete level objects with checks before and a read-back after. Needs the OPEN editor with the level.

    objects.py place brush <mesh.cgf> <name> x y z [--material <mtl>] [--scale s] [--yaw deg]
    objects.py delete --names a,b | --prefix P [--really <count>]

What already existed and is reused: `general.new_object` / `object.set_scale` / `object.set_rotation` / `object.set_custom_material` / `object.delete` (the same editor calls
build_ashvale.Scene and house_kit.place make), `bridge.object.list` (the read-back; capped near 500 objects), asset_lint.Names (does the mesh / material exist). What was
missing: the checks (the mesh and material exist, the name is free, the numbers are sane), the read-back (what the editor now says the object is), and a delete that cannot
take a hundred objects by accident. Neither saves the level: the caller decides (`general.save_level()`); a job that closes the editor saves first.

Delete is a dry run unless `really` equals the number of objects that match, so a prefix that matches more than expected is seen before anything goes. More than ~490
matches (the list is capped) is refused: narrow the prefix, or use build_ashvale.py `clear <prefix>` which repeats until none remain.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import base64
import json
import math
import os
import re
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import asset_lint as L          # noqa: E402
import bridge_py as B           # noqa: E402  (the shared editor bridge: host/port from the environment, optional editor auto-recovery)

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
CLASS = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
LIST_CAP = 490                  # bridge.object.list stops near 500
LEVEL = 4200.0                  # metres either way: far outside any level here, but catches a pasted wrong unit
BATCH = 80


class ObjectError(ValueError):
    pass


def bridge(cmd, timeout=60):
    try:
        return B.send(cmd, timeout)
    except OSError as exc:
        raise ObjectError("the editor's bridge does not answer (%s): this needs Sandbox open with the level (health_check says what is wrong)" % exc)


def run_python(source):
    r = bridge("bridge.execute_python.b64 " + base64.b64encode(source.encode()).decode(), 120)
    try:
        log = json.loads(r.get("output", "{}")).get("log", "")
    except ValueError:
        log = r.get("output", "")
    if "Traceback" in log:
        raise ObjectError("the editor raised: " + log[-400:])
    return log


def listing(name_filter=""):
    r = bridge("bridge.object.list" + (" " + name_filter if name_filter else ""), 60)
    if not r.get("ok"):
        raise ObjectError("bridge.object.list failed: %s" % str(r)[:200])
    return json.loads(r.get("output") or "[]")


def find(name):
    for o in listing(name):
        if o["name"].lower() == name.lower():
            return o
    return None


def _vec(v, n, what, lo=-LEVEL, hi=LEVEL):
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise ObjectError("%s must be %d numbers" % (what, n))
    out = []
    for c in v:
        try:
            f = float(c)
        except (TypeError, ValueError):
            raise ObjectError("%s: %r is not a number" % (what, c))
        if math.isnan(f) or not lo <= f <= hi:
            raise ObjectError("%s: %g is outside %g..%g" % (what, f, lo, hi))
        out.append(f)
    return out


def place(kind, asset, name, position, rotation_deg=(0, 0, 0), scale=(1, 1, 1), material="", overwrite=False):
    """kind 'brush': asset is a mesh path (objects/... .cgf or %ENGINE%/...); kind 'entity': asset is an entity class name. Returns the object as the editor reports it."""
    if kind not in ("brush", "entity"):
        raise ObjectError("kind is 'brush' (a mesh) or 'entity' (a class)")
    if not NAME.match(name or ""):
        raise ObjectError("name %r must start with a letter and use letters, digits and _ (64 at most)" % name)
    pos = _vec(position, 3, "position")
    rot = _vec(rotation_deg, 3, "rotation_deg", -360, 360)
    scl = _vec(scale, 3, "scale", 0.01, 500)
    names = L.Names()
    if kind == "brush":
        if not (asset or "").lower().endswith((".cgf", ".cdf")):
            raise ObjectError("a brush needs a mesh path ending .cgf (e.g. 'objects/props/misc/stacked_logs/stacked_logs_full_2m_a.cgf')")
        if not names.exists(asset):
            raise ObjectError("the mesh %r does not exist (not loose under Assets/ or Engine/, and in no pak): check the path with read_asset_file or lint_assets" % asset)
    elif not CLASS.match(asset or ""):
        raise ObjectError("entity class %r must be a plain class name (entity_class_properties lists them)" % asset)
    if material:
        mtl = material if material.lower().endswith(".mtl") else material + ".mtl"
        if not names.exists(mtl):
            raise ObjectError("the material %r does not exist" % material)
    existing = find(name)
    if existing and not overwrite:
        raise ObjectError("an object named %r already exists (%s at %s): pass overwrite=true to replace it" % (name, existing["type"], existing["pos"]))
    if existing:
        run_python("import sandbox.object as object\nobject.delete(%r)\nprint('deleted')" % name)
    if kind == "brush":
        src = ("import sandbox.general as general\nimport sandbox.object as object\n"
               "n = general.new_object('Brush', %r, %r, %f, %f, %f)\n"
               "if not n:\n    print('FAILED')\nelse:\n"
               "    object.set_scale(n, %f, %f, %f)\n"
               "%s%s    print('made', n)\n") % (asset, name, pos[0], pos[1], pos[2], scl[0], scl[1], scl[2],
                                                 ("    object.set_rotation(n, %f, %f, %f)\n" % tuple(rot)) if any(rot) else "",
                                                 ("    object.set_custom_material(n, %r)\n" % material) if material else "")
    else:
        src = ("import sandbox.general as general\nimport sandbox.object as object\n"
               "n = general.new_object('Entity', %r, %r, %f, %f, %f)\n"
               "if not n:\n    print('FAILED')\nelse:\n"
               "    object.set_scale(n, %f, %f, %f)\n"
               "%s    print('made', n)\n") % (asset, name, pos[0], pos[1], pos[2], scl[0], scl[1], scl[2],
                                              ("    object.set_rotation(n, %f, %f, %f)\n" % tuple(rot)) if any(rot) else "")
    log = run_python(src)
    if "FAILED" in log or "made" not in log:
        raise ObjectError("the editor did not create the object: %s" % log.strip()[-200:])
    # the read-back: what the editor now says is there, against what was asked for
    got = find(name)
    if not got:
        raise ObjectError("the editor said it made %r but it is not in the object list" % name)
    problems = []
    if max(abs(a - b) for a, b in zip(got["pos"], pos)) > 0.05:
        problems.append("position is %s, asked for %s" % (got["pos"], [round(c, 3) for c in pos]))
    if max(abs(a - b) for a, b in zip(got["scale"], scl)) > 0.01:
        problems.append("scale is %s, asked for %s" % (got["scale"], scl))
    if any(rot) and max(abs(((a - b + 180) % 360) - 180) for a, b in zip(got["rotDeg"], rot)) > 0.5:
        problems.append("rotation is %s, asked for %s" % (got["rotDeg"], rot))
    if material and not got.get("material"):
        problems.append("the editor reports no material (asked for %r)" % material)
    return {"ok": not problems, "object": got, "problems": problems, "replaced": bool(existing),
            "saved": False, "note": "the level is NOT saved: save it (execute_editor_command general.save_level) when the phase is done; it is lost if the editor closes unsaved "
                                    "(the jobs that close the editor save first)"}


def delete(names=None, prefix=None, really=None):
    """Dry run unless `really` equals the number of matching objects."""
    if bool(names) == bool(prefix):
        raise ObjectError("give exactly one of names (a list) or prefix")
    if prefix is not None:
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]{2,63}$", prefix):
            raise ObjectError("prefix %r must be at least 3 characters: letters, digits and _" % prefix)
        matches = listing(prefix)                                # the editor filters on a substring, case-insensitively; the list stops near 500
        if len(matches) >= LIST_CAP:
            raise ObjectError("that prefix matches %d or more objects and the editor's list stops near 500: narrow it, or use build_ashvale.py clear %s" % (LIST_CAP, prefix))
        found = [o for o in matches if o["name"].lower().startswith(prefix.lower())]
    else:
        if not isinstance(names, (list, tuple)) or not 1 <= len(names) <= 200 or not all(NAME.match(str(n)) for n in names):
            raise ObjectError("names: 1 to 200 object names (letters, digits, _)")
        found = []
        missing = []
        for n in names:
            o = find(n)
            (found if o else missing).append(o or n)
        if missing and really is not None:
            raise ObjectError("not in the level: %s (nothing deleted)" % ", ".join(missing[:10]))
    count = len(found)
    sample = [o["name"] for o in found[:30]]
    if really is None:
        return {"ok": True, "dry_run": True, "would_delete": count, "names": sample, "more": max(0, count - 30),
                "next": "call again with really=%d to delete exactly these" % count if count else "nothing matches"}
    if really != count:
        raise ObjectError("really=%s but %d objects match: nothing deleted. Look at the list (dry run) and pass the number you mean" % (really, count))
    gone = []
    for i in range(0, count, BATCH):
        chunk = [o["name"] for o in found[i:i + BATCH]]
        run_python("import sandbox.object as object\nfor n in %r:\n    object.delete(n)\nprint('batch', len(%r))\n" % (chunk, chunk))
        gone += chunk
    left = [n for n in gone if find(n)]
    return {"ok": not left, "deleted": len(gone) - len(left), "still_there": left[:20],
            "saved": False, "note": "the level is NOT saved; the list is capped near 500, so more may remain if the prefix matched exactly that many"}


def main():
    a = sys.argv[1:]
    try:
        if a[:2] == ["place", "brush"]:
            opts = dict(zip([x for x in a if x.startswith("--")], [a[a.index(x) + 1] for x in a if x.startswith("--")]))
            pos = [float(v) for v in a[5:8]]
            print(json.dumps(place("brush", a[2], a[3], pos, (0, 0, float(opts.get("--yaw", 0))), [float(opts.get("--scale", 1))] * 3, opts.get("--material", "")), indent=1))
        elif a and a[0] == "delete":
            names = a[a.index("--names") + 1].split(",") if "--names" in a else None
            prefix = a[a.index("--prefix") + 1] if "--prefix" in a else None
            really = int(a[a.index("--really") + 1]) if "--really" in a else None
            print(json.dumps(delete(names, prefix, really), indent=1))
        else:
            print(__doc__)
    except ObjectError as exc:
        print("refused: %s" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
