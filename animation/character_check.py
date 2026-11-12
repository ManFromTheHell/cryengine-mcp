#!/usr/bin/env python3
"""Characters as the ENGINE resolves them (ENGINE_LEARNING_PLAN B6), from the open editor, without running the game.

The plugin verb `bridge.character.check` creates each character the way the game and the Character Tool do (ICharacterManager::CreateInstance on a .cdf) and writes what the engine ended up with: the
skeleton and its joint count, every attachment (type, joint, whether an object is bound and which file), the size of the animation set and the log lines the load produced. This module compares that
with the .cdf file and the game's C++:

  C001 the engine cannot create the character                      C004 an attachment's bone is not in the skeleton
  C002 an attachment in the file is not in the engine's list         C005 the load logged an error or warning (a missing material, skin ...)
  C003 an attachment names a file (Binding) and nothing is bound     C006 the code names a character that does not exist or does not load

    python animation/character_check.py [cdf ...]        default: every .cdf under Assets and the one the C++ names
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
CODE = os.path.join(PROJECT, "Code")
DATA = os.path.join(HERE, "engine_data")
SCRATCH = os.path.join(DATA, "character_scratch")

sys.path.insert(0, HERE)


class CharacterError(RuntimeError):
    pass


def _slash(p):
    return p.replace(chr(92), "/")


def project_characters():
    """Every .cdf under Assets as a path relative to Assets (forward slashes)."""
    out = []
    for root, _, files in os.walk(ASSETS):
        for f in files:
            if f.lower().endswith(".cdf"):
                out.append(_slash(os.path.relpath(os.path.join(root, f), ASSETS)))
    return sorted(out)


def code_characters(code_dir=None):
    """{path: [file:line]}: string literals in the C++ that end in .cdf / .chr / .skin."""
    found = {}
    for root, _, files in os.walk(code_dir or CODE):
        for fn in files:
            if not fn.endswith((".cpp", ".h")):
                continue
            path = os.path.join(root, fn)
            rel = _slash(os.path.relpath(path, PROJECT))
            try:
                lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            for n, line in enumerate(lines, 1):
                for m in re.finditer(r'"([^"]+\.(?:cdf|chr|skin))"', line, re.I):
                    found.setdefault(m.group(1), []).append("%s:%d" % (rel, n))
    return found


_TEXT_CACHE = {}


def referenced_by_game(path):
    """Is the character's file name mentioned in the game's C++, in a level layer, a script or an XML library? (a plain text search for the base name; a character nothing mentions is inert)"""
    base = os.path.basename(path).lower()
    if not _TEXT_CACHE:
        for root_dir in (CODE, os.path.join(ASSETS, "levels"), os.path.join(ASSETS, "scripts"), os.path.join(ASSETS, "libs")):
            for r, _, files in os.walk(root_dir):
                for f in files:
                    if f.lower().endswith((".cpp", ".h", ".lyr", ".lua", ".xml", ".json", ".level", ".cdf")):
                        try:
                            _TEXT_CACHE[os.path.join(r, f)] = open(os.path.join(r, f), encoding="utf-8", errors="replace").read().lower()
                        except OSError:
                            pass
    own = os.path.normcase(os.path.join(ASSETS, path.replace("/", os.sep)))
    return any(base in t for fp, t in _TEXT_CACHE.items() if os.path.normcase(fp) != own)


def file_attachments(path):
    """{name: {type, binding, bone, material}} read from the .cdf XML, and the model: -> (model attrs, attachments)."""
    root = ET.parse(os.path.join(ASSETS, path.replace("/", os.sep))).getroot()
    model = root.find("Model")
    atts = {}
    for a in root.iter("Attachment"):
        atts[a.get("AName") or ""] = {"type": a.get("Type") or "", "binding": a.get("Binding") or "", "bone": a.get("BoneName") or "", "material": a.get("Material") or ""}
    return (dict(model.attrib) if model is not None else {}), atts


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


NOISE = re.compile(r"error|fail|not found|cannot|can't|missing|warning|invalid|unknown", re.I)


def check(paths=None, code_dir=None):
    """paths: .cdf paths relative to Assets (default: all of them). -> {ok, errors, warnings, findings, characters: [{file, created, skeleton, joints, attachments, bound, animations}]}."""
    import bridge_py
    from asset_lint import Names
    _TEXT_CACHE.clear()  # the server process is long-lived: re-read the game's files on every run so edits are seen
    names = Names()
    paths = [p.replace(chr(92), "/") for p in (paths or project_characters())]
    code = code_characters(code_dir)
    for c in code:
        if c.lower().endswith(".cdf") and c not in paths:
            paths.append(c)
    os.makedirs(SCRATCH, exist_ok=True)
    names_file, out = os.path.join(SCRATCH, "names.txt"), os.path.join(SCRATCH, "chars_%d.json" % os.getpid())
    with open(names_file, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(paths) + "\n")
    if os.path.exists(out):
        os.remove(out)
    r = bridge_py.send("bridge.character.check %s | %s" % (_slash(out), _slash(names_file)), 180)
    if not r.get("ok"):
        raise CharacterError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % r.get("error"))
    with open(out, encoding="utf-8") as f:
        rows = json.load(f)
    os.remove(out)
    findings, chars = [], []
    logged = {}                                                      # engine log line -> characters it appeared for (the same line repeats for every variant)
    for row in rows:
        where = row["file"]
        if not row["created"]:
            findings.append(_finding("error", "C001", where, "the engine cannot create this character (the file is missing, not a character definition, or its skeleton does not load)", "read the log lines; check Model File and the skeleton"))
            for l in row.get("log", []):
                text = re.sub(r"\$\d", "", l).strip()
                if text and NOISE.search(text):
                    findings.append(_finding("info", "C005", where, text))
            chars.append({"file": where, "created": False})
            continue
        eng = {a["name"]: a for a in row["attachments"]}
        model, fatts = ({}, {})
        if os.path.isfile(os.path.join(ASSETS, where.replace("/", os.sep))) and where.lower().endswith(".cdf"):
            try:
                model, fatts = file_attachments(where)
            except (ET.ParseError, OSError):
                pass
        for name, fa in fatts.items():
            if name not in eng:
                findings.append(_finding("warning", "C002", where, "attachment '%s' (%s) is in the file and not in what the engine built" % (name, fa["type"]), "the engine skipped it: check its Type and Binding/BoneName"))
                continue
            ea = eng[name]
            failed = [l for l in row.get("log", []) if fa["binding"] and fa["binding"].lower() in l.lower() and re.search(r"fail", l, re.I)]
            if failed:
                findings.append(_finding("error" if referenced_by_game(where) else "warning", "C003", where, ("" if referenced_by_game(where) else "[nothing in the game refers to this character: inert] ") + "attachment '%s' binds '%s' and the engine could not load it (%s). The engine binds its placeholder object instead, so 'bound' alone does not show it; the file is %s the project's own files" %
                                         (name, fa["binding"], re.sub(r"\$\d", "", failed[0]).strip()[:90], "in" if os.path.isfile(os.path.join(ASSETS, fa["binding"].replace("/", os.sep))) else "NOT in"),
                                         "import the file (the GameSDK sample has it) or delete the attachment / the unused character variant"))
            elif fa["binding"] and not ea["bound"]:
                findings.append(_finding("error", "C003", where, "attachment '%s' binds '%s' and the engine bound nothing" % (name, fa["binding"]), "check the file loads (Character Tool)"))
            if fa["type"] == "CA_BONE" and fa["bone"] and not ea["joint"]:
                findings.append(_finding("error", "C004", where, "attachment '%s' sits on bone '%s', which the skeleton does not have" % (name, fa["bone"])))
            if fa["material"] and not names.exists(fa["material"] + ".mtl") and not names.exists(fa["material"]):
                findings.append(_finding("warning", "C005", where, "attachment '%s' names material '%s', which is not in the project" % (name, fa["material"])))
        if model.get("Material") and not names.exists(model["Material"] + ".mtl") and not names.exists(model["Material"]):
            findings.append(_finding("warning", "C005", where, "the model's material '%s' is not in the project" % model["Material"]))
        seen = set()
        for l in row.get("log", []):
            text = re.sub(r"\$\d", "", l).strip()
            explained = any(fa["binding"] and fa["binding"].lower() in text.lower() for fa in fatts.values())          # already reported as C003 with the file name
            if text and NOISE.search(text) and text not in seen and not explained:
                seen.add(text)
                logged.setdefault(text, []).append(where)
        bound = sum(1 for a in row["attachments"] if a["bound"])
        if row["animations"] == 0:
            findings.append(_finding("info", "C006", where, "the animation set is empty (no .chrparams animation list for this skeleton)"))
        chars.append({"file": where, "created": True, "skeleton": row["skeleton"], "joints": row["joints"], "attachments": len(row["attachments"]), "bound": bound, "animations": row["animations"]})
    for text, files in logged.items():
        # a failed cgf is reported once as C003 already (from the file); the engine's own lines stay as information, one per distinct line
        findings.append(_finding("info" if "Failed to" in text or "CGF Loading Failed" in text else "warning", "C005", files[0], text + ("  (also in %d other character%s)" % (len(files) - 1, "" if len(files) == 2 else "s") if len(files) > 1 else "")))
    got = {c["file"].lower() for c in chars if c["created"]}
    for c, where in sorted(code.items()):
        if not names.exists(c):
            findings.append(_finding("error", "C007", where[0], "the code names character '%s', which is not in the project" % c))
        elif c.lower().endswith(".cdf") and c.lower() not in got:
            findings.append(_finding("error", "C007", where[0], "the code names character '%s' and the engine cannot create it" % c))
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "findings": findings, "characters": chars, "code_references": code}


def main():
    r = check(sys.argv[1:] or None)
    print("%s: %d error(s), %d warning(s); %d characters" % ("OK" if r["ok"] else "PROBLEMS", r["errors"], r["warnings"], len(r["characters"])))
    for c in r["characters"][:20]:
        print("  %-62s joints %-4s attachments %s/%s animations %s" % (c["file"], c.get("joints", ""), c.get("bound", ""), c.get("attachments", ""), c.get("animations", "")))
    for f in r["findings"]:
        print("  %-7s %s  %s\n          %s" % (f["severity"], f["code"], f["where"], f["message"][:240]))


if __name__ == "__main__":
    main()
