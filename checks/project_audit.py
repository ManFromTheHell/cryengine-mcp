#!/usr/bin/env python3
"""One call that runs every ENGINE-OWNED validator of this server over the project and says what is wrong, what is new since the last accepted baseline, and which validators could not run.

It runs (each in turn, each timed, a failure of one never stops the others): the asset linter, the engine's own loaders for materials, characters, Mannequin databases, behaviour trees, UQS queries, flow graphs,
the Dynamic Response System, time-of-day presets, particle effects and the Track View sequence, the character animation data, the .cryproject file, the audio libraries against the code, the editor's
'Check Level for Errors', the PBS material rules and Crytek's own unit tests. Everything it needs from the editor is read from the open editor; with the editor closed those validators are reported as
`not run` and the editor-free ones still run. Nothing is changed, nothing is run in the game.

    python checks/project_audit.py [--accept]        run all; --accept saves the result as the baseline (engine_data/audit_baseline.json)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import glob
import json
import os
import sys
import time

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
DATA = os.path.join(HERE, "engine_data")
BASELINE = os.path.join(DATA, "audit_baseline.json")

sys.path.insert(0, HERE)

KNOWN_FALSE_POSITIVE = "Geometry File in Slot"          # level.validate says it for ANY Mesh-component entity (a scratch entity gets it too; the loaves render)


def _norm(findings, where_key="where"):
    out = []
    for f in findings or []:
        out.append({"severity": f.get("severity", "info"), "code": f.get("code", ""), "where": f.get(where_key) or f.get("where") or f.get("path") or "", "message": (f.get("message") or "")[:200]})
    return out


ADVISORY = ("PBS material rules",)              # guidelines from the manual, not correctness: their errors are reported as warnings


def _result(name, findings, editor, extra=None, seconds=0.0):
    if name in ADVISORY:
        findings = [dict(f, severity="warning") if f["severity"] == "error" else f for f in findings]
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    r = {"name": name, "editor": editor, "ran": True, "errors": errors, "warnings": warnings, "info": len(findings) - errors - warnings, "findings": findings, "seconds": round(seconds, 1)}
    if extra:
        r["detail"] = extra
    return r


# --------------------------------------------------------------------------- the adapters: one per validator -> a normalised result

def a_lint():
    import asset_lint
    r = asset_lint.lint()
    return _norm(r["findings"], "path"), {"checked": r.get("checked")}


def a_materials():
    import material_check
    r = material_check.check()
    return _norm(r["findings"]), {"materials": len(r["materials"])}


def a_characters():
    import character_check
    r = character_check.check()
    return _norm(r["findings"]), {"characters": len(r["characters"])}


def a_mannequin():
    import mannequin_check
    r = mannequin_check.check()
    return _norm(r["findings"]), {"fragments": r.get("fragments") if not isinstance(r.get("fragments"), (list, dict)) else len(r["fragments"])}


def a_bt():
    import bt_check
    r = bt_check.check()
    return _norm(r["findings"]), {"strict": r.get("strict"), "lenient": r.get("lenient")}


def a_uqs():
    import uqs_check
    r = uqs_check.check()
    return _norm(r["findings"]), {"loaded": r.get("loaded"), "valid": r.get("valid")}


def a_flow():
    import flow_check
    r = flow_check.check()
    return _norm(r["findings"]), {"graphs": r["graphs"]}


def a_drs():
    import drs_check
    r = drs_check.check()
    return _norm(r["findings"]), {"signals": r["signals"], "lines": len(r["engine"]["lines"])}


def a_env():
    import env_check
    r = env_check.check()
    return _norm(r["findings"]), {"presets": [p["file"] for p in r["presets"]]}


def a_anim():
    import anim_check
    r = anim_check.check()
    return _norm(r["findings"]), {"checked": r["checked"]}


def a_ui():
    import ui_check
    r = ui_check.check()
    return _norm(r["findings"]), {"elements": sum(len(f["elements"]) for f in r["files"]), "movies_loaded": r["movies_loaded"], "calls": r["code"]["calls"]}


def a_level_settings():
    import level_check
    r = level_check.check()
    return _norm(r["findings"]), {"saved_presets": len(r["summary"].get("saved_presets", [])), "exported": r["summary"].get("exported")}


def a_visareas():
    import visarea_check
    r = visarea_check.check()
    return _norm(r["findings"]), {"area_objects": r["objects"], "engine_areas": r["engine_areas"], "exported": r["exported"]}


def a_objects():
    import object_check
    r = object_check.check()
    return _norm(r["findings"]), {"objects": r["objects"], "compared": r["compared"], "types": r["types"]}


def a_entities():
    import entity_check
    r = entity_check.check()
    return _norm(r["findings"]), {"entities": r["entities"], "compared": r["compared"]}


def a_cryproject():
    import cryproject_check
    r = cryproject_check.check()
    return _norm(r["findings"]), {"projects": [p["file"] for p in r["projects"]]}


def a_pbs():
    import pbs_check
    r = pbs_check.check()
    return _norm(r["findings"]), {"sub_materials": r["checked"]}


def a_audio():
    import ace_catalog
    p = ace_catalog.problems()
    out = []
    for n, w in (p.get("undefined_in_code") or {}).items():
        out.append({"severity": "error", "code": "H001", "where": ",".join(w)[:100], "message": "the code uses audio control '%s' that no library defines" % n})
    for t, s in p.get("missing_samples") or []:
        out.append({"severity": "error", "code": "H002", "where": t, "message": "trigger '%s' names sample '%s', which is not under audio/" % (t, s)})
    for d in p.get("duplicates") or []:
        out.append({"severity": "warning", "code": "H003", "where": str(d[0]), "message": "trigger %s is defined in two libraries: %s" % (d[0], d[1:])})
    for lib in p.get("broken_libraries") or []:
        out.append({"severity": "error", "code": "H004", "where": lib.get("library", ""), "message": "library does not parse: %s" % lib.get("error")})
    for n in p.get("unreferenced_triggers") or []:
        out.append({"severity": "info", "code": "H005", "where": n, "message": "trigger '%s' is referenced by nothing in the code" % n})
    return out, {"triggers": p["triggers"], "code_names": p["code_names"]}


def a_level():
    import level_stats
    v = level_stats.validate()
    out = []
    for kind, msgs in v["kinds"].items():
        # the editor remembers every material it loaded in this session (the tests' scratch copies, a Decal's missing default material) and level.validate says it again: session residue, gone after a restart
        residue = all("zz_" in m.lower() or "zzno" in m.lower() or "Materials/Decals/Default" in m for m in msgs)       # the tests' scratch names: zz_mcp_ files, 'mat_zz_undefined', ZzNoSuchShader
        sev = "info" if (KNOWN_FALSE_POSITIVE in kind or residue) else "warning"
        out.append({"severity": sev, "code": "V001", "where": kind[:80], "message": "%d line(s)%s: %s" % (len(msgs), " (session residue of a test)" if residue else "", msgs[0][:120])})
    return out, {"warnings_total": v["total"]}


def a_sequence():
    import sequence_check
    out, n = [], 0
    files = sorted(glob.glob(os.path.join(ASSETS, "**", "sequences", "*.xml"), recursive=True))
    if not files:
        out.append({"severity": "info", "code": "Q000", "where": "sequences", "message": "no Track View sequence files found under Assets/**/sequences"})
    for p in files:
        n += 1
        r = sequence_check.check(os.path.relpath(p, PROJECT))
        rel = os.path.basename(p)
        if not r.get("loaded"):
            out.append({"severity": "error", "code": "Q001", "where": rel, "message": "the engine did not load the sequence"})
        for k in ("dropped_elements", "dropped_attributes", "changed"):
            for item in r.get(k) or []:
                out.append({"severity": "error", "code": "Q002", "where": rel, "message": "%s: %s" % (k, str(item)[:150])})
        for w in r.get("warnings") or []:
            out.append({"severity": "warning", "code": "Q003", "where": rel, "message": str(w)[:150]})
    return out, {"sequences": n}


def a_particles():
    import particle_catalog
    out, n = [], 0
    for p in sorted(glob.glob(os.path.join(ASSETS, "Particles", "**", "*.pfx"), recursive=True)):
        n += 1
        r = particle_catalog.check(os.path.relpath(p, PROJECT))
        rel = os.path.relpath(p, ASSETS).replace(chr(92), "/")
        d = r.get("dropped") or []
        if d:
            out.append({"severity": "warning", "code": "G001", "where": rel, "message": "the engine drops %d key(s) it does not know (e.g. %s)" % (len(d), str(d[0])[:100])})
        for x in r.get("disabled_by_engine") or []:
            out.append({"severity": "warning", "code": "G002", "where": rel, "message": "the engine switched a feature off: %s" % str(x)[:120]})
        if not r.get("loaded", True):
            out.append({"severity": "error", "code": "G003", "where": rel, "message": "the engine could not load the effect"})
    return out, {"effects": n}


def a_engine_tests():
    import engine_tests
    r = engine_tests.run()
    out = []
    for exe, v in r["exes"].items():
        for t, d in v["tests"].items():
            if d["status"] != "ok":
                out.append({"severity": "error", "code": "T001", "where": "%s %s" % (exe, t), "message": "%s: %s" % (t, d["status"])})
        if v["returncode"] != 0 and not v["failed"]:
            out.append({"severity": "error", "code": "T002", "where": exe, "message": "exit code %s" % v["returncode"]})
    return out, {"passed": r["passed"], "failed": r["failed"]}


# name, adapter, needs the editor
VALIDATORS = [
    ("asset lint", a_lint, False), ("engine unit tests", a_engine_tests, False), ("level settings and export", a_level_settings, False), ("vis areas and portals", a_visareas, False), ("PBS material rules", a_pbs, False), ("audio libraries vs code", a_audio, False), ("time-of-day presets", a_env, False),
    ("materials (engine load)", a_materials, True), ("characters (engine load)", a_characters, True), ("Mannequin", a_mannequin, True), ("behaviour tree", a_bt, True), ("UQS query", a_uqs, True),
    ("flow graphs", a_flow, True), ("dynamic response system", a_drs, True), ("character animation data", a_anim, True), (".cryproject", a_cryproject, True), ("UI (FlashUI)", a_ui, True), ("level entities", a_entities, True), ("level objects (brushes, areas, probes)", a_objects, True), ("Track View sequence", a_sequence, True),
    ("particle effects", a_particles, True), ("editor level validate", a_level, True),
]


def editor_open():
    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        return True
    except Exception:                                           # noqa: BLE001
        return False


def key(f):
    if f["code"] == "L011":      # 'the shader cache is older than <the newest changed file>': the same finding whichever file is newest (each new texture used to make it look NEW again)
        return "%s|%s" % (f["code"], f["where"])
    return "%s|%s|%s" % (f["code"], f["where"], f["message"][:80])


def run(only=None, skip_editor=False):
    live = (not skip_editor) and editor_open()
    results = []
    for name, fn, needs_editor in VALIDATORS:
        if only and name not in only:
            continue
        if needs_editor and not live:
            results.append({"name": name, "editor": True, "ran": False, "reason": "the editor is not open" if not skip_editor else "skipped (editor-free run)", "errors": 0, "warnings": 0, "info": 0, "findings": []})
            continue
        t0 = time.time()
        try:
            findings, extra = fn()
            results.append(_result(name, findings, needs_editor, extra, time.time() - t0))
        except Exception as exc:                                  # noqa: BLE001 - a validator that cannot run is reported, the rest still run
            results.append({"name": name, "editor": needs_editor, "ran": False, "reason": "%s: %s" % (type(exc).__name__, str(exc)[:200]), "errors": 0, "warnings": 0, "info": 0, "findings": [], "seconds": round(time.time() - t0, 1)})
    ran = [r for r in results if r["ran"]]
    return {"ok": all(r["errors"] == 0 for r in ran) and bool(ran), "errors": sum(r["errors"] for r in results), "warnings": sum(r["warnings"] for r in results),
            "ran": len(ran), "not_run": [{"name": r["name"], "reason": r["reason"]} for r in results if not r["ran"]], "validators": results, "editor_open": live}


def _norm_key(k):
    """A baseline key as key() would write it now (older baselines kept the newest file's name in the L011 key)."""
    parts = k.split("|", 2)
    return "%s|%s" % (parts[0], parts[1]) if parts[0] == "L011" and len(parts) > 1 else k


def baseline():
    if os.path.isfile(BASELINE):
        with open(BASELINE, encoding="utf-8") as f:
            return json.load(f)
    return None


def accept(result):
    slim = {"saved": time.strftime("%Y-%m-%d %H:%M:%S"), "validators": {r["name"]: {"ran": r["ran"], "errors": r["errors"], "warnings": r["warnings"], "info": r["info"],
                                                                                    "keys": sorted(key(f) for f in r["findings"] if f["severity"] in ("error", "warning"))} for r in result["validators"]}}
    os.makedirs(DATA, exist_ok=True)
    with open(BASELINE, "w", encoding="utf-8") as f:
        json.dump(slim, f, indent=1)
    return slim


def compare(result, base):
    """New errors and warnings since the baseline, and ones that went away -> {new: [...], gone: [...]}; a validator that ran before and did not now (or the reverse) is noted"""
    new, gone, notes = [], [], []
    for r in result["validators"]:
        b = (base.get("validators") or {}).get(r["name"])
        if b is None:
            notes.append("%s: not in the baseline" % r["name"])
            continue
        if b["ran"] and not r["ran"]:
            notes.append("%s: ran in the baseline, did not run now (%s)" % (r["name"], r.get("reason")))
            continue
        if not b["ran"] and r["ran"]:
            notes.append("%s: did not run in the baseline" % r["name"])
            continue
        now = {key(f): f for f in r["findings"] if f["severity"] in ("error", "warning")}
        before = {_norm_key(k) for k in b["keys"]}               # a baseline saved before L011 lost its file name still matches
        for k in sorted(set(now) - before):
            new.append({"validator": r["name"], "severity": now[k]["severity"], "code": now[k]["code"], "where": now[k]["where"], "message": now[k]["message"]})
        for k in sorted(before - set(now)):
            gone.append({"validator": r["name"], "finding": k})
    return {"new": new, "gone": gone, "notes": notes}


def main():
    r = run()
    print("%s: %d error(s), %d warning(s) in %d validators that ran; editor %s" % ("OK" if r["ok"] else "PROBLEMS", r["errors"], r["warnings"], r["ran"], "open" if r["editor_open"] else "NOT open"))
    for v in r["validators"]:
        if v["ran"]:
            print("  %-28s errors %-3d warnings %-3d info %-3d %5.1fs  %s" % (v["name"], v["errors"], v["warnings"], v["info"], v.get("seconds", 0), json.dumps(v.get("detail"))[:90] if v.get("detail") else ""))
        else:
            print("  %-28s NOT RUN: %s" % (v["name"], v["reason"]))
    b = baseline()
    if b:
        d = compare(r, b)
        print("since the baseline (%s): %d new, %d gone" % (b["saved"], len(d["new"]), len(d["gone"])))
        for n in d["new"]:
            print("  NEW  ", n)
        for n in d["notes"]:
            print("  note ", n)
    if "--accept" in sys.argv:
        accept(r)
        print("baseline saved")


if __name__ == "__main__":
    main()
