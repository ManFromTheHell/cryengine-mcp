#!/usr/bin/env python3
"""Environment (time-of-day) presets as the ENGINE reads them (ENGINE_LEARNING_PLAN G2), without the game.

The engine's own table of time-of-day variables is in the source (Cry3DEngine/EnvironmentPreset.cpp: CTimeOfDayVariables::Reset, one AddVar line per variable with group, display name, name,
enum id, type, default and the range) and the enum in CryCommon/Cry3DEngine/ITimeOfDay.h. How a preset file is read (EnvironmentPreset.cpp CEnvironmentPreset::Serialize, line 627):

  * a file of the CURRENT version (4) is read POSITIONALLY: for i in 0..PARAM_TOTAL the i-th <var> element is read into variable i, and its id, type, minValue, maxValue and the three splines
    replace the engine's. So the <var> elements must be exactly the enum's, in the enum's order: a missing, extra or reordered one shifts every later variable (no error is logged).
  * an OLDER file is converted: each <var> is matched by id (an unknown id is dropped, a missing variable keeps its default), and its times are rescaled when it is version 0.
  * the file's minValue / maxValue are what the editor uses afterwards, not the table's; every evaluated spline value is CLAMPED to them (GetInterpolatedAt), so a key outside the range is
    authored but never reached.
  * a key time is in 1/6000 hour units: a day is 0..144000 (6:00 is 36000).

    E001 the file is not an EnvironmentPreset XML                       E005 a spline key time is outside 0..144000 or the keys are not in time order
    E002 older file version (converted by id on load)                   E006 a colour variable has keys in spline0 and none in spline1 / spline2
    E003 the <var> sequence differs from the engine's (version 4: misload)  E007 the file's minValue/maxValue differ from the engine's table
    E004 a key value is outside the variable's range                    E008 the Constants block is missing (a version-4 file reads it)

    python checks/env_check.py check [libs/environmentpresets/x.env ...]         default: every .env under Assets/libs/environmentpresets
    python checks/env_check.py vars [text]                                        the engine's variables (group, name, type, default, range)
    python checks/env_check.py diff a.env b.env [hour ...]                        the variables that differ, sampled at the given hours
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
# CRYENGINE_SOURCE: root of a CRYENGINE source checkout (the variable table is parsed from its ITimeOfDay.h and EnvironmentPreset.cpp; a Launcher install does not ship them).
SOURCE = _paths.ENGINE_SOURCE
TOD_H = os.path.join(SOURCE, "Code", "CryEngine", "CryCommon", "Cry3DEngine", "ITimeOfDay.h")
PRESET_CPP = os.path.join(SOURCE, "Code", "CryEngine", "Cry3DEngine", "EnvironmentPreset.cpp")
DAY = 144000.0

_CACHE = {}


class EnvError(RuntimeError):
    pass


def _slash(p):
    return p.replace(chr(92), "/")


def table():
    """The engine's variable table, in enum order: [{id, index, group, display, name, type, default: [..], min, max}] and the current preset version."""
    if "table" in _CACHE:
        return _CACHE["table"]
    try:
        h = open(TOD_H, encoding="utf-8", errors="replace").read()
        c = open(PRESET_CPP, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        raise EnvError("the engine source is needed (read-only reference): set the environment variable CRYENGINE_SOURCE to a CRYENGINE source checkout (%s)" % exc)
    enum = re.search(r"enum ETimeOfDayParamID\s*\{(.*?)\n\t\};", h, re.S)
    ids = [m.group(1) for m in re.finditer(r"^\s*(PARAM_[A-Z0-9_]+)\s*(?:,|$)", enum.group(1), re.M)] if enum else []
    ids = [i for i in ids if i != "PARAM_TOTAL"]
    rows = {}
    for m in re.finditer(r'AddVar\(\s*"([^"]*)"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*,\s*ITimeOfDay::(PARAM_[A-Z0-9_]+)\s*,\s*ITimeOfDay::TYPE_(FLOAT|COLOR)\s*,\s*([^,]+),\s*([^,]+),\s*([^)]+)\)', c):
        group, display, name, pid, typ, a, b, d = m.groups()

        def num(s):
            s = s.strip().rstrip("f").replace("*", " * ")
            try:
                parts = [p for p in s.replace("fRecip255", "(1/255)").split("*")]
                v = 1.0
                for p in parts:
                    p = p.strip().rstrip("f")
                    v *= eval(p, {"__builtins__": {}}, {}) if p.startswith("(") else float(p)  # noqa: S307 - only the literal "(1/255)" reaches eval
                return v
            except ValueError:
                return 0.0
        a, b, d = num(a), num(b), num(d)
        rows[pid] = {"id": pid, "group": group, "display": display or name, "name": name, "type": "TYPE_" + typ,
                     "default": [a, b, d] if typ == "COLOR" else [a], "min": 0.0 if typ == "COLOR" else b, "max": 1.0 if typ == "COLOR" else d}
    out = []
    for i, pid in enumerate(ids):
        r = dict(rows.get(pid) or {"id": pid, "group": "", "display": pid, "name": pid, "type": "TYPE_FLOAT", "default": [0.0], "min": 0.0, "max": 1.0})
        r["index"] = i
        out.append(r)
    version = re.search(r"sCurrentPresetVersion\s*=\s*(\d+)", c)
    _CACHE["table"] = (out, int(version.group(1)) if version else 4)
    return _CACHE["table"]


def variables(text=""):
    words = [w for w in re.split(r"\s+", text.lower().strip()) if w]
    rows, _ = table()
    return [r for r in rows if all(w in (r["id"] + " " + r["name"] + " " + r["group"] + " " + r["display"]).lower() for w in words)]


def project_presets():
    base = os.path.join(ASSETS, "libs", "environmentpresets")
    return sorted(_slash(os.path.relpath(os.path.join(base, f), ASSETS)) for f in os.listdir(base) if f.lower().endswith(".env")) if os.path.isdir(base) else []


def read_preset(path):
    """-> {version, vars: [{id, type, min, max, keys: [[(time, value, ...), ...] x3]}], constants: bool}. Raises ET.ParseError / EnvError."""
    full = path if os.path.isabs(path) else os.path.join(ASSETS, path.replace("/", os.sep))
    root = ET.parse(full).getroot()
    if root.tag != "EnvironmentPreset":
        raise EnvError("root element is <%s>, not <EnvironmentPreset>" % root.tag)
    out = {"version": int(root.get("version") or 0), "vars": [], "constants": root.find("Constants") is not None}
    for v in root.findall("var"):
        keys = []
        for n in ("spline0", "spline1", "spline2"):
            s = v.find(n)
            ks = []
            if s is not None and (s.get("keys") or "").strip():
                for k in (s.get("keys") or "").split(","):
                    if k.strip():
                        f = k.split(":")
                        ks.append((float(f[0]), float(f[1])))
            keys.append(ks)
        out["vars"].append({"id": v.get("id") or "", "type": v.get("type") or "", "min": float(v.get("minValue") or 0), "max": float(v.get("maxValue") or 0), "keys": keys})
    return out


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def check_one(path, preset, rows=None, current=None):
    rows, current = (rows, current) if rows else table()
    findings = []
    ids = [v["id"] for v in preset["vars"]]
    want = [r["id"] for r in rows]
    if preset["version"] < current:
        findings.append(_finding("info", "E002", path, "file version %d, the engine's is %d: it is converted by variable id on load (an unknown id is dropped, a missing one keeps its default)" % (preset["version"], current)))
    if preset["version"] == current:
        if ids != want:
            at = next((i for i, (a, b) in enumerate(zip(ids, want)) if a != b), min(len(ids), len(want)))
            findings.append(_finding("error", "E003", path, "a version-%d file is read POSITIONALLY and its <var> sequence differs from the engine's at position %d (file: %s, engine: %s; %d vars in the file, %d in the engine): every later variable is misloaded"
                                     % (current, at, ids[at] if at < len(ids) else "(end)", want[at] if at < len(want) else "(end)", len(ids), len(want)),
                                     "write the variables in the engine's order, one per id"))
        if not preset["constants"]:
            findings.append(_finding("warning", "E008", path, "no <Constants> block (sun position, moon, wind ... a version-%d file reads it)" % current))
    else:
        missing = [i for i in want if i not in ids]
        extra = [i for i in ids if i not in want]
        if extra:
            findings.append(_finding("warning", "E003", path, "ids the engine does not have are dropped on load: %s" % ", ".join(extra[:6])))
        if missing:
            findings.append(_finding("info", "E003", path, "%d variable(s) are not in the file and keep their defaults (first: %s)" % (len(missing), ", ".join(missing[:4]))))
    by_id = {r["id"]: r for r in rows}
    for v in preset["vars"]:
        r = by_id.get(v["id"])
        where = "%s %s" % (path, v["id"])
        if r and (abs(v["min"] - r["min"]) > 1e-6 or abs(v["max"] - r["max"]) > 1e-6):
            findings.append(_finding("info", "E007", where, "file range %g..%g, the engine's table says %g..%g (the file's is what the editor then uses)" % (v["min"], v["max"], r["min"], r["max"])))
        for n, ks in enumerate(v["keys"]):
            times = [t for t, _ in ks]
            if times != sorted(times) or any(t < 0 or t > DAY for t in times):
                findings.append(_finding("error", "E005", where, "spline%d key times %s are not in order inside 0..%d" % (n, [int(t) for t in times][:6], DAY)))
            if any(val < v["min"] - 1e-6 or val > v["max"] + 1e-6 for _, val in ks):
                bad = next(val for _, val in ks if val < v["min"] - 1e-6 or val > v["max"] + 1e-6)
                findings.append(_finding("warning", "E004", where, "spline%d has a key value %g outside the variable's range %g..%g: the engine CLAMPS every evaluated value to that range (CTimeOfDayVariable::GetInterpolatedAt), so the value never takes effect"
                                         % (n, bad, v["min"], v["max"]), "lower the value to the range, or widen minValue/maxValue in the file"))
        if v["type"] == "TYPE_COLOR" and v["keys"][0] and (not v["keys"][1] or not v["keys"][2]):
            findings.append(_finding("warning", "E006", where, "colour variable with keys in spline0 (red) and none in spline%s" % ("1 (green)" if not v["keys"][1] else "2 (blue)")))
    return findings


def check(paths=None):
    rows, current = table()
    paths = [p.replace(chr(92), "/") for p in (paths or project_presets())]
    findings, presets = [], []
    for p in paths:
        try:
            pr = read_preset(p)
        except (ET.ParseError, EnvError, OSError, ValueError) as exc:
            findings.append(_finding("error", "E001", p, "cannot be read as a preset: %s" % exc))
            presets.append({"file": p, "readable": False})
            continue
        findings.extend(check_one(p, pr, rows, current))
        presets.append({"file": p, "readable": True, "version": pr["version"], "variables": len(pr["vars"]), "constants": pr["constants"]})
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "findings": findings, "presets": presets, "engine_variables": len(rows), "engine_version": current}


def value_at(keys, t):
    """The value of one spline at time t (units of 1/6000 hour), by linear interpolation between keys (the engine uses Bezier with the stored tangents: this is an approximation). None when there are no keys."""
    if not keys:
        return None
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return keys[-1][1]


def sample(preset, var_id, hour):
    v = next((x for x in preset["vars"] if x["id"] == var_id), None)
    if v is None:
        return None
    vals = [value_at(k, hour * 6000.0) for k in v["keys"]]
    return [x for x in vals if x is not None] if v["type"] == "TYPE_COLOR" else vals[:1]


def diff(a, b, hours=(0, 6, 9, 12, 15, 18, 21), eps=1e-4):
    """The variables whose value differs between two presets at any of the hours -> [{id, group, name, samples: {hour: (a, b)}}]."""
    rows, _ = table()
    pa, pb = read_preset(a), read_preset(b)
    out = []
    for r in rows:
        diffs = {}
        for h in hours:
            va, vb = sample(pa, r["id"], h), sample(pb, r["id"], h)
            if va is None or vb is None:
                continue
            if len(va) != len(vb) or any(abs(x - y) > eps * max(1.0, abs(x)) for x, y in zip(va, vb)):
                diffs[h] = (va, vb)
        if diffs:
            out.append({"id": r["id"], "group": r["group"], "name": r["name"], "hours_differing": len(diffs), "samples": {h: (["%.4g" % x for x in va], ["%.4g" % x for x in vb]) for h, (va, vb) in sorted(diffs.items())}})
    return out


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return
    if argv[0] == "vars":
        rows = variables(" ".join(argv[1:]))
        print("%d variable(s)" % len(rows))
        for r in rows:
            print("  %-3d %-14s %-46s %-10s default %-28s range %g..%g" % (r["index"], r["group"], r["name"][:46], r["type"][5:], r["default"], r["min"], r["max"]))
    elif argv[0] == "diff":
        d = diff(argv[1], argv[2], tuple(float(x) for x in argv[3:]) or (0, 6, 9, 12, 15, 18, 21))
        print("%d variable(s) differ" % len(d))
        for x in d:
            print("  %-48s %-12s differs at %d hour(s)" % (x["name"][:48], x["group"], x["hours_differing"]))
    else:
        r = check(argv[1:] if argv[0] == "check" else argv)
        print("%s: %d error(s), %d warning(s); presets %s" % ("OK" if r["ok"] else "PROBLEMS", r["errors"], r["warnings"], r["presets"]))
        for f in r["findings"]:
            print("  %-7s %s  %s\n          %s" % (f["severity"], f["code"], f["where"], f["message"][:260]))


if __name__ == "__main__":
    try:
        main()
    except EnvError as exc:
        print("env_check: %s" % exc)
        sys.exit(2)
