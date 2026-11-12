#!/usr/bin/env python3
"""The Dynamic Response System as the ENGINE holds it (ENGINE_LEARNING_PLAN E5), from the open editor, without running the game.

The plugin verb `bridge.drs.dump` asks the running DRS what it has: the action and condition TYPES its class factories know (what a .response file may use), every dialog line set and variation in its
line database, and, for the signals asked for, the response tree AS THE ENGINE SERIALISES IT (IResponseManager::SerializeResponse into a JSON archive): what it KEPT from the file. With `reload` it first
re-initialises the system (IDynamicResponseSystem::ReInit, the call the game's Dialogue.cpp makes), which reloads the files from disk and logs what it dislikes. This module compares that with the files:

  D001 a .response file whose signal the engine holds no response for (it was not loaded)       D005 a dialog line's audio trigger is not in the project's audio libraries (silence)
  D002 an action or condition class in a .response file that the engine's factories do not know   D006 the engine's response differs from the file (a value dropped or changed)
  D003 a SpeakLine action names a line the engine's line database does not hold                 D007 the engine's dialog line differs from the file, or the line is not loaded
  D004 the game's code sends a signal no .response file answers (or the reverse)                 D008 the engine logged a problem while re-initialising

How the files are read (CryDynamicResponseSystem/ResponseManager.cpp:95): every file in Libs/DynamicResponseSystem/Responses is loaded as ONE response whose signal name is the FILE'S BASE NAME
(sg_villager_sees_player.response answers the signal sg_villager_sees_player); dialog lines come from DialogLines/*.dialog.

    python checks/drs_check.py [reload]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import sys

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
CODE = os.path.join(PROJECT, "Code")
DATA = os.path.join(HERE, "engine_data")
SCRATCH = os.path.join(DATA, "drs_scratch")
ROOT = os.path.join(ASSETS, "libs", "DynamicResponseSystem")

sys.path.insert(0, HERE)


class DrsError(RuntimeError):
    pass


def _slash(p):
    return p.replace(chr(92), "/")


def dump(signals, reload=False):
    """-> the verb's JSON as a dict, with each response's JSON text parsed (None when the engine holds none)."""
    import bridge_py
    os.makedirs(SCRATCH, exist_ok=True)
    out = os.path.join(SCRATCH, "drs_%d.json" % os.getpid())
    if os.path.exists(out):
        os.remove(out)
    cmd = "bridge.drs.dump %s" % _slash(out)
    if reload:
        cmd += " | reload"
    for s in signals:
        cmd += " | %s" % s
    r = bridge_py.send(cmd, 120)
    if not r.get("ok"):
        raise DrsError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % r.get("error"))
    with open(out, encoding="utf-8") as f:
        d = json.load(f)
    os.remove(out)
    resp = {}
    for k, v in (d.get("responses") or {}).items():
        try:
            resp[k] = json.loads(v) if v else None
        except ValueError:
            resp[k] = None
    d["responses"] = resp
    return d


def response_files(root=ROOT):
    base = os.path.join(root, "Responses")
    out = []
    for r, _, files in os.walk(base):
        for f in files:
            if f.lower().endswith(".response"):
                out.append(os.path.join(r, f))
    return sorted(out)


def dialog_files(root=ROOT):
    base = os.path.join(root, "DialogLines")
    out = []
    for r, _, files in os.walk(base):
        for f in files:
            if f.lower().endswith(".dialog"):
                out.append(os.path.join(r, f))
    return sorted(out)


def signal_of(path):
    return os.path.splitext(os.path.basename(path))[0]


def code_signals(code_dir=None):
    """{signal: file:line} for the string literals starting sg_ in the game's C++ (the signals it sends)."""
    found = {}
    for r, _, files in os.walk(code_dir or CODE):
        for fn in files:
            if not fn.endswith((".cpp", ".h")):
                continue
            path = os.path.join(r, fn)
            try:
                lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            for n, line in enumerate(lines, 1):
                for m in re.finditer(r'"(sg_[A-Za-z0-9_]+)"', line):
                    found.setdefault(m.group(1), "%s:%d" % (_slash(os.path.relpath(path, PROJECT)), n))
    return found


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def leaves(obj, path=""):
    """{path: scalar} of a JSON value; lists are indexed."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(leaves(v, path + "/" + str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(leaves(v, path + "[%d]" % i))
    else:
        out[path] = obj
    return out


def _same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 1e-5
    return a == b


def used_classes(obj, key):
    """The class names used under the given key ('Action' / 'Condition') anywhere in a response tree: {name: count}."""
    found = {}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == key and isinstance(v, dict):
                    for cls in v:
                        found[cls] = found.get(cls, 0) + 1
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return found


def speak_lines(obj):
    ids = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "SpeakLine" and isinstance(v, dict) and v.get("lineToSpeak"):
                    ids.append(v["lineToSpeak"])
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return ids


def compare(dumped, responses, dialogs, code, triggers=None):
    """dumped: the verb's result; responses: {signal: file json}; dialogs: {file: json}; code: {signal: where}; triggers: set of ACE trigger names or None -> [finding]"""
    findings = []
    if not dumped.get("available"):
        return [_finding("error", "D001", "DRS", "the engine has no Dynamic Response System in this editor session (gEnv->pDynamicResponseSystem is null)")]
    actions = {a["name"] for a in dumped.get("actions", [])}
    conditions = {c["name"] for c in dumped.get("conditions", [])}
    lines = {l["id"]: l for l in dumped.get("lines", [])}
    for l in dumped.get("log", []):
        text = re.sub(r"\$\d", "", l).strip()
        if text and re.search(r"error|fail|warning|not found|unknown|invalid|could not|cannot", text, re.I):
            findings.append(_finding("warning", "D008", "ReInit", text))
    for sig, tree in responses.items():
        where = sig + ".response"
        held = dumped.get("responses", {}).get(sig)
        for cls in used_classes(tree, "Action"):
            if cls not in actions:
                findings.append(_finding("error", "D002", where, "action class '%s' is not one the engine knows (%d: %s ...): the engine drops it" % (cls, len(actions), ", ".join(sorted(actions))[:90]), "use a registered class name"))
        for cls in used_classes(tree, "Condition"):
            if cls not in conditions:
                findings.append(_finding("error", "D002", where, "condition class '%s' is not one the engine knows (%d: %s ...): the engine drops it" % (cls, len(conditions), ", ".join(sorted(conditions))[:90]), "use a registered class name (note a trailing space in some names)"))
        for lid in speak_lines(tree):
            if lid not in lines:
                findings.append(_finding("error", "D003", where, "SpeakLine names line '%s', which the engine's line database does not hold (%d lines)" % (lid, len(lines)), "add it to a .dialog file or fix the id"))
        if held is None:
            findings.append(_finding("error", "D001", where, "the engine holds no response for signal '%s': the file was not loaded (reload the DRS, or the file does not parse)" % sig))
            continue
        want, have = leaves(tree), leaves(held)
        missing = [(p, v) for p, v in want.items() if p not in have or not _same(have[p], v)]
        if missing:
            p, v = missing[0]
            findings.append(_finding("warning", "D006", where, "%d value(s) in the file are not in the engine's copy; first: %s = %r (engine: %r)" % (len(missing), p, v, have.get(p, "(absent)"))))
    for sig, where in sorted(code.items()):
        if sig not in responses:
            findings.append(_finding("warning", "D004", where, "the code sends signal '%s' and no .response file answers it (the signal does nothing)" % sig))
    for sig in sorted(responses):
        if sig not in code:
            findings.append(_finding("info", "D004", sig + ".response", "no string literal '%s' in the game's code: the response is never triggered by it (a flow graph or data may send it)" % sig))
    for fn, doc in dialogs.items():
        for ls in doc.get("lineSets", []):
            lid = ls.get("lineID")
            held = lines.get(lid)
            if held is None:
                findings.append(_finding("error", "D007", os.path.basename(fn), "line '%s' is in the file and not in the engine's line database" % lid))
                continue
            vs, hv = ls.get("lineVariations", []), held.get("variations", [])
            if len(vs) != len(hv) or ls.get("priority") != held.get("priority") or ls.get("flags") != held.get("flags"):
                findings.append(_finding("warning", "D007", os.path.basename(fn), "line '%s': the file has %d variation(s), priority %s, flags %s; the engine holds %d, priority %s, flags %s" % (lid, len(vs), ls.get("priority"), ls.get("flags"), len(hv), held.get("priority"), held.get("flags"))))
            else:
                for i, (a, b) in enumerate(zip(vs, hv)):
                    if a.get("text") != b.get("text") or a.get("audioStartTrigger") != b.get("start") or a.get("audioStopTrigger") != b.get("stop") or not _same(a.get("pauseLength", 0), b.get("pause", 0)):
                        findings.append(_finding("warning", "D007", os.path.basename(fn), "line '%s' variation %d differs from the engine's copy (text or triggers or pause)" % (lid, i)))
            if triggers is not None:
                for v in vs:
                    for key in ("audioStartTrigger", "audioStopTrigger"):
                        t = v.get(key)
                        if t and t not in triggers:
                            findings.append(_finding("warning", "D005", os.path.basename(fn), "line '%s' names audio trigger '%s', which no audio library of the project defines (a trigger name is a hash: a typo is silence)" % (lid, t)))
    return findings


def check(reload=False, root=ROOT):
    """-> {ok, errors, warnings, findings, signals, engine: {actions, conditions, lines}}"""
    responses, bad_responses, dialogs, bad_dialogs = {}, {}, {}, {}
    for p in response_files(root):
        with open(p, encoding="utf-8") as f:
            try:
                responses[signal_of(p)] = json.load(f)
            except ValueError as exc:
                bad_responses[signal_of(p)] = str(exc)
    for p in dialog_files(root):
        with open(p, encoding="utf-8") as f:
            try:
                dialogs[p] = json.load(f)
            except ValueError as exc:
                bad_dialogs[os.path.basename(p)] = str(exc)
    code = code_signals()
    d = dump(sorted(set(responses) | set(bad_responses) | set(code)), reload)
    try:
        import ace_catalog
        triggers = set(ace_catalog.load()["triggers"])
    except Exception:                                             # noqa: BLE001 - the audio catalog is optional
        triggers = None
    findings = compare(d, responses, dialogs, code, triggers)
    for name, exc in bad_responses.items():
        held = d.get("responses", {}).get(name)
        base = (held or {}).get("BaseSegment", {})
        empty = bool(held) and not base.get("Actions") and not base.get("ChildSegments")
        findings.append(_finding("error", "D001", name + ".response", "is not valid JSON: %s%s" % (exc, ". The engine still creates a response for the signal and it is EMPTY (no actions, no log line): the file's content is lost silently" if empty else "")))
    for name, exc in bad_dialogs.items():
        findings.append(_finding("error", "D001", name, "is not valid JSON: %s" % exc))
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "findings": findings, "signals": sorted(responses), "code_signals": code,
            "engine": {"actions": [a["name"] for a in d.get("actions", [])], "conditions": [c["name"] for c in d.get("conditions", [])], "lines": [l["id"] for l in d.get("lines", [])], "reloaded": d.get("reloaded")}}


def main():
    r = check(len(sys.argv) > 1 and sys.argv[1] == "reload")
    print("%s: %d error(s), %d warning(s); signals %s; engine knows %d actions, %d conditions, %d lines" % ("OK" if r["ok"] else "PROBLEMS", r["errors"], r["warnings"], r["signals"], len(r["engine"]["actions"]), len(r["engine"]["conditions"]), len(r["engine"]["lines"])))
    for f in r["findings"]:
        print("  %-7s %s  %s\n          %s" % (f["severity"], f["code"], f["where"], f["message"][:260]))


if __name__ == "__main__":
    main()
