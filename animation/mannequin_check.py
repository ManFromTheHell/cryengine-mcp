#!/usr/bin/env python3
"""Mannequin files as the ENGINE reads them (ENGINE_LEARNING_PLAN C1), without running the game.

The editor loads an animation database (.adb), a controller definition and a character through the engine's own loaders (IAnimationDatabaseManager::Load / LoadControllerDef, a character
instance for its animation set), writes back what it HOLDS with the editor manager's own Save* calls (into memory, nothing is written to the project), and runs the engine's own
IAnimationDatabase::Validate against the character's animation set (bridge verb `bridge.mannequin.check`). This module diffs file against what the engine kept and reports:

  * what the loader DROPPED: a fragment whose FragmentID or tags it does not know, a blend between unknown fragments, a scope or tag it cannot resolve (the loader logs a line at most);
  * the engine's own validation: "Animation not found" for every clip the character cannot play (the engine's Validate RETURN VALUE is unreliable: it keeps only the last tag set's
    answer, so the callback reports are counted instead);
  * the manual's rules the engine does not enforce: scopes of one context must not share animation layers, a controller definition names every FragmentID once, a database and its
    controller definition use the same tag and fragment files;
  * the game's C++ against the database: every fragment name the code asks for must exist (a typo is silence, not an error).

    python animation/mannequin_check.py check [adb] [controller] [cdf]       needs the editor
    python animation/mannequin_check.py clips [text]                          the 27 procedural clip types and their parameters (engine registry)

Defaults: the villager's files (Animations/Mannequin/ADB/Villager.adb, VillagerControllerDefinition.xml, sdk_player.cdf).
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
SCRATCH = os.path.join(DATA, "mannequin_scratch")
CLIPS_FILE = os.path.join(DATA, "mannequin_clips.json")

# The defaults are the example project's files (Assets-relative); override them with the environment variables MANNEQUIN_ADB, MANNEQUIN_CONTROLLER, MANNEQUIN_CHARACTER.
# When the default database is absent, check() uses the only/first *.adb under Assets/Animations/Mannequin/ADB.
DEFAULT_ADB = os.environ.get("MANNEQUIN_ADB", "Animations/Mannequin/ADB/Villager.adb")
DEFAULT_CONTROLLER = os.environ.get("MANNEQUIN_CONTROLLER", "Animations/Mannequin/ADB/VillagerControllerDefinition.xml")
DEFAULT_CHARACTER = os.environ.get("MANNEQUIN_CHARACTER", "Objects/Characters/Human/sdk_player/sdk_player.cdf")

sys.path.insert(0, HERE)


class MannequinError(RuntimeError):
    pass


def _slash(p):
    return p.replace(chr(92), "/")


# ---------------------------------------------------------------- the procedural clip registry


def clips(refresh=False):
    """[{name, params: {name: default} or None, note}]: every procedural clip type the engine's IProceduralClipFactory registers, with the parameters its IProceduralParams serializes
    (the engine's own JSON writer). Cached in engine_data/mannequin_clips.json; refresh=True asks the editor again."""
    if not refresh and os.path.isfile(CLIPS_FILE):
        with open(CLIPS_FILE, encoding="utf-8") as f:
            return json.load(f)
    import bridge_py
    os.makedirs(SCRATCH, exist_ok=True)
    raw = os.path.join(SCRATCH, "clips_raw.json")
    r = bridge_py.send("bridge.mannequin.clips " + _slash(raw), 60)
    if not r.get("ok"):
        raise MannequinError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % r.get("error"))
    with open(raw, encoding="utf-8") as f:
        items = json.load(f)
    out = []
    for it in items:
        params, note = None, ""
        if it["has_params"]:
            try:
                params = json.loads(it["json"])
            except ValueError:
                note = "the engine's JSON writer produced an unreadable stub (%r): the clip has no serialized parameters" % it["json"][:20]
        else:
            note = "no parameter object"
        out.append({"name": it["name"], "params": params, "note": note})
    out.sort(key=lambda c: c["name"].lower())
    os.makedirs(DATA, exist_ok=True)
    with open(CLIPS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    return out


def clip_search(text="", refresh=False):
    """The clips whose name or a parameter name contains `text` (case-insensitive); all of them for an empty text."""
    t = (text or "").lower()
    out = []
    for c in clips(refresh):
        hay = c["name"].lower() + " " + " ".join((c["params"] or {}).keys()).lower()
        if t in hay:
            out.append(c)
    return out


# ---------------------------------------------------------------- comparing a file with what the engine kept

TAGSET_ATTRS = {"tags", "fragtags", "from", "to", "fromfrag", "tofrag", "scopes", "flags", "sharedtags"}


def _tagset(v):
    return ",".join(sorted(x for x in re.split(r"[+,\s]+", (v or "").lower()) if x))


def _numbers(v):
    try:
        return [float(x) for x in v.split(",")]
    except ValueError:
        return None


def same_value(name, a, b):
    """Tag lists compare as sets (the engine sorts them), numbers within 1e-3 (it writes 0.30000001 where the file says 0.3), names without case."""
    if name.lower() in TAGSET_ATTRS:
        return _tagset(a) == _tagset(b)
    if a == b or a.lower() == b.lower():
        return True
    na, nb = _numbers(a), _numbers(b)
    if na is None or nb is None or len(na) != len(nb):
        return False
    return all(abs(x - y) <= 1e-3 * max(1.0, abs(x), abs(y)) for x, y in zip(na, nb))


def _key(el, parent_tag):
    """What identifies an element among its siblings: a fragment option by its tag sets (the engine re-sorts options), a transition by from/to, everything else by tag and name."""
    t = el.tag
    if t == "Fragment":
        return "Fragment[%s|%s]" % (_tagset(el.get("Tags")), _tagset(el.get("FragTags")))
    if t == "Variant":
        return "Variant[%s|%s|%s|%s]" % (_tagset(el.get("from")), _tagset(el.get("to")), _tagset(el.get("fromFrag")), _tagset(el.get("toFrag")))
    if t == "Blend" and parent_tag == "FragmentBlendList":
        return "Blend[%s>%s]" % ((el.get("from") or "").lower(), (el.get("to") or "").lower())
    if t == "Tag" and el.get("name"):
        return "Tag[%s]" % el.get("name").lower()
    if parent_tag in ("FragmentList", "FragmentDefs", "ScopeDefs", "ScopeContextDefs", "SubContexts"):
        return t.lower()
    return t


def _walk(el, here):
    """{path: attributes}; a path is the chain of keys with an occurrence number (options with identical tags are matched in file order)."""
    out = {here: dict(el.attrib)}
    out[here]["#children"] = str(len(el))
    seen = {}
    for c in el:
        k = _key(c, el.tag)
        seen[k] = seen.get(k, 0) + 1
        out.update(_walk(c, "%s/%s#%d" % (here, k, seen[k])))
    return out


def _top_most(paths, walked=None):
    """Only the paths whose parent is not itself in the list: one dropped fragment is one finding, not one per layer and clip. An element with no attributes and no children says nothing
    (the engine writes `<TagDefinition version="2"/>` for a file that had an empty <Tags/>), so it is not a loss."""
    s = set(paths)
    out = [p for p in paths if p.rsplit("/", 1)[0] not in s]
    if walked is not None:
        out = [p for p in out if len(walked[p]) > 1 or walked[p].get("#children") != "0"]
    return out


def merge_fragment_elements(root):
    """The loader reads every element of the FragmentList by name and adds its options to that fragment, so two <Walk> elements are one fragment (and the engine saves them as one): merge
    same-name siblings in the file's tree before comparing. Returns the same root."""
    fl = root.find("FragmentList")
    if fl is None:
        return root
    first = {}
    for el in list(fl):
        k = el.tag.lower()
        if k in first:
            first[k].extend(list(el))
            fl.remove(el)
        else:
            first[k] = el
    return root


def diff_documents(src, eng):
    """src, eng: ElementTree roots (the file, what the engine saved). -> {dropped: [path], added: [path], dropped_attrs: [(path, name, value)], changed: [(path, name, file, engine)]}.
    Attributes only the engine writes (its defaults) are not differences."""
    a, b = _walk(src, "/" + src.tag), _walk(eng, "/" + eng.tag)
    rep = {"dropped": _top_most([k for k in a if k not in b], a), "added": _top_most([k for k in b if k not in a], b), "dropped_attrs": [], "changed": []}
    for k, attrs in a.items():
        if k not in b:
            continue
        for name, v in attrs.items():
            if name.startswith("#"):
                continue
            if name not in b[k]:
                if v not in ("", "0", "false"):
                    rep["dropped_attrs"].append((k, name, v))
            elif not same_value(name, v, b[k][name]):
                rep["changed"].append((k, name, v, b[k][name]))
    return rep


# ---------------------------------------------------------------- the engine's log lines

LOG_RULES = [
    (re.compile(r"Warning missing fragmentID (\S+)"), "warning", "M002", "the database has fragment '%s', which the FragmentID file does not define: the loader skipped every option of it"),
    (re.compile(r"Unknown tags for fragmentID (\S+) tag (\S*)"), "warning", "M002", "an option of fragment '%s' names a tag the tag file does not define (tags '%s'): the loader skipped that option"),
    (re.compile(r"Broken fragment entry for fragmentID (\S+)"), "error", "M002", "an option of fragment '%s' is broken (the engine could not read its layers): skipped"),
    (re.compile(r"Unknown tags for blend (.*)"), "warning", "M002", "a transition names tags the tag file does not define (%s): skipped"),
    (re.compile(r"Broken fragment entry for blend"), "error", "M002", "a transition's fragment is broken: skipped"),
    (re.compile(r"Skipping unknown fragment '([^']+)'"), "warning", "M003", "the controller definition has an entry for fragment '%s', which the FragmentID file does not define: skipped"),
    (re.compile(r"Skipping duplicate fragment '([^']+)'"), "warning", "M003", "the controller definition names fragment '%s' twice: the second entry is ignored"),
    (re.compile(r"Skipping unknown scope '([^']+)'"), "warning", "M003", "ScopeDefs names scope '%s' twice or unknown: skipped"),
    (re.compile(r"Skipping duplicate scope '([^']+)'"), "warning", "M003", "ScopeDefs defines scope '%s' twice: the second is ignored"),
    (re.compile(r"Duplicate scope context '([^']+)'"), "warning", "M003", "ScopeContextDefs defines context '%s' twice: the second is ignored"),
    (re.compile(r"Invalid tag '([^']*)'"), "info", "M004", "the engine does not know tag '%s'"),
    (re.compile(r"Missing (def|fragment definition|tag definition) file"), "error", "M001", "the database names a definition file the engine could not load: the whole database is skipped"),
    (re.compile(r"Could not load animation database"), "error", "M001", "a sub-database could not be loaded"),
    (re.compile(r"Tags, Fragments or Scopes missing"), "error", "M001", "the controller definition lacks its Tags / Fragments file: the engine refuses it"),
]


def log_findings(lines, ignore=()):
    out = []
    for raw in lines:
        text = re.sub(r"\$\d", "", raw).strip()
        if not text or any(i in text for i in ignore):
            continue
        for rx, sev, code, msg in LOG_RULES:
            m = rx.search(text)
            if m:
                out.append({"severity": sev, "code": code, "message": msg % m.groups() if "%s" in msg else msg, "engine_log": text})
                break
    return out


# ---------------------------------------------------------------- the code's fragment names

FRAG_CALLS = [re.compile(r'GetFragmentId\(\s*"([^"]+)"'), re.compile(r'SetDefaultFragmentName\(\s*"([^"]+)"'), re.compile(r'GetFragID\(\s*"([^"]+)"'),
              re.compile(r'QueueFragment(?:WithName)?\(\s*"([^"]+)"')]


# the generated table of the villagers' gesture fragments (villager_anim.py): { "Fragment", "clip" } rows and the look-at fragment's name; the code asks for these through the table
# rows: { "Fragment", "clip" }, { "Fragment", "clip", 0.5f }, { "Fragment", "clip", 97.5f, profile, 21 } and the start / stop pairs { "StartX", "clip", "StopX", "clip", 0.5f, 0.3f } (the stop fragment is the third string)
GENERATED_TABLE = [re.compile(r'\{\s*"([A-Za-z0-9_]+)"\s*,\s*"[^"]+"\s*[,}]'), re.compile(r'\{\s*"[A-Za-z0-9_]+"\s*,\s*"[^"]+"\s*,\s*"([A-Za-z0-9_]+)"\s*,\s*"[^"]+"\s*,'),
                   re.compile(r'constexpr const char\*\s*\w+\s*=\s*"([A-Za-z0-9_]+)"')]


def code_fragment_names(code_dir=None):
    """{name: [file:line]}: the fragment names the project's C++ asks Mannequin for: literals in GetFragmentId / SetDefaultFragmentName / GetFragID / QueueFragment calls, and the literals a
    function whose name ends in Fragment returns (the villager maps its actions to fragments in ActionFragment())."""
    code_dir = code_dir or CODE
    found = {}
    for root, _, files in os.walk(code_dir):
        for fn in files:
            if not fn.endswith((".cpp", ".h")):
                continue
            path = os.path.join(root, fn)
            try:
                lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            in_fragment_fn = False
            for n, line in enumerate(lines, 1):
                if re.match(r"^\S.*\b\w*Fragment\s*\([^;]*$", line) and "{" not in line.split("(")[0]:
                    in_fragment_fn = True
                elif in_fragment_fn and line.startswith("}"):
                    in_fragment_fn = False
                for rx in FRAG_CALLS + (GENERATED_TABLE if fn.endswith("AnimTable.h") else []):
                    for m in rx.finditer(line):
                        found.setdefault(m.group(1), []).append("%s:%d" % (os.path.relpath(path, PROJECT).replace(chr(92), "/"), n))
                if in_fragment_fn:
                    m = re.search(r'return\s+"([A-Za-z0-9_]+)"\s*;', line)
                    if m:
                        found.setdefault(m.group(1), []).append("%s:%d" % (os.path.relpath(path, PROJECT).replace(chr(92), "/"), n))
    return found


# ---------------------------------------------------------------- the check


def _read_xml(rel):
    full = os.path.join(ASSETS, rel.replace("/", os.sep))
    if not os.path.isfile(full):
        return None
    try:
        return ET.parse(full).getroot()
    except ET.ParseError:
        return None


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def _layer_overlaps(scope_defs):
    out = []
    for i, a in enumerate(scope_defs):
        for b in scope_defs[i + 1:]:
            if a["context"] == b["context"] and a["num_layers"] and b["num_layers"] and a["layer"] < b["layer"] + b["num_layers"] and b["layer"] < a["layer"] + a["num_layers"]:
                out.append((a, b))
    return out


def check(adb=DEFAULT_ADB, controller=DEFAULT_CONTROLLER, character=DEFAULT_CHARACTER, reload=True, code_dir=None):
    """The files' paths are relative to Assets/ (as the engine wants them); pass None for a file you do not want checked. -> {ok, errors, warnings, findings: [{severity, code, where, message,
    fix}], fragments: [{name, options, animations: [name]}], animations: {used, missing}, engine: {...}, saved: {file: path of the engine's version}}. reload=True makes the engine re-read the files
    (the manager caches a loaded database by name, and the editor has the villager's loaded since the level opened)."""
    import bridge_py
    os.makedirs(SCRATCH, exist_ok=True)
    if adb == DEFAULT_ADB and not os.path.isfile(os.path.join(ASSETS, adb.replace("/", os.sep))):
        adb_dir = os.path.join(ASSETS, "Animations", "Mannequin", "ADB")
        found = sorted(f for f in (os.listdir(adb_dir) if os.path.isdir(adb_dir) else []) if f.lower().endswith(".adb"))
        if found:
            adb = "Animations/Mannequin/ADB/" + found[0]
    for label, rel in (("database", adb), ("controller definition", controller), ("character", character)):
        if rel and not os.path.isfile(os.path.join(ASSETS, rel.replace("/", os.sep))):
            raise MannequinError("no %s at Assets/%s" % (label, rel))
    out_json = os.path.join(SCRATCH, "check_%d.json" % os.getpid())
    if os.path.exists(out_json):
        os.remove(out_json)
    cmd = "bridge.mannequin.check %s | %s | %s | %s | %s" % (_slash(out_json), adb or "-", controller or "-", character or "-", "reload" if reload else "-")
    r = bridge_py.send(cmd, 90)
    if not r.get("ok"):
        raise MannequinError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % r.get("error"))
    with open(out_json, encoding="utf-8") as f:
        eng = json.load(f)
    os.remove(out_json)

    findings = []
    where_adb = adb or ""
    if adb and not eng["loaded"]:
        findings.append(_finding("error", "M001", where_adb, "the engine did not load this database (it is not well-formed, or the FragmentID / tag file it names cannot be loaded)",
                                 "read the engine log lines below; fix the file names in the AnimDB element"))
    if controller and not eng["controller_loaded"]:
        findings.append(_finding("error", "M001", controller, "the engine did not load this controller definition (not a ControllerDef, or its Tags / Fragments file is missing)"))
    if character and not eng["character_loaded"]:
        findings.append(_finding("warning", "M006", character, "the engine could not create this character: the clips could not be checked against its animation set"))
    ignore = ("zz_mcp_",) if not any("zz_mcp_" in (x or "") for x in (adb, controller)) else ()
    findings.extend(dict(f, where=where_adb or controller or "") for f in log_findings(eng.get("log", []), ignore))

    # what the engine kept, against the file
    saved_paths = {}
    for s in eng.get("saved", []):
        name = s["file"]
        p = os.path.join(SCRATCH, re.sub(r"[^A-Za-z0-9_.-]", "_", name))
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(s["xml"])
        saved_paths[name] = p
        src = _read_xml(name)
        if src is None:
            continue
        try:
            kept = ET.fromstring(s["xml"])
        except ET.ParseError:
            continue
        d = diff_documents(merge_fragment_elements(src), kept)
        for path in d["dropped"]:
            findings.append(_finding("warning", "M010", name, "in the file, not in what the engine kept: %s" % _pretty(path), "the loader skipped it (see the log lines); fix the name or tag it uses"))
        for path, attr, v in d["dropped_attrs"]:
            findings.append(_finding("info", "M011", name, "%s: attribute %s=%r is not in what the engine kept" % (_pretty(path), attr, v)))
        for path, attr, was, now in d["changed"]:
            findings.append(_finding("warning", "M012", name, "%s: %s is %r in the file and %r in the engine" % (_pretty(path), attr, was, now), "the engine normalised or rejected the value"))

    # the engine's own validation, per fragment
    frag_names = {f["id"]: f["name"] for f in eng.get("fragments", [])}
    v = eng.get("validate") or {}
    for e in v.get("errors", []):
        m = re.match(r"fragment (\d+) option (\d+): (.*)", e)
        if m:
            findings.append(_finding("error", "M020", "%s fragment %s option %s" % (where_adb, frag_names.get(int(m.group(1)), m.group(1)), m.group(2)), "the engine says: " + m.group(3),
                                     "name an animation the character has (anim_img.py lists them) or add the clip"))
        else:
            findings.append(_finding("error", "M020", where_adb, e))
    for w in v.get("warnings", []):
        m = re.match(r"fragment (\d+) option (\d+): (.*)", w)
        findings.append(_finding("warning", "M021", "%s fragment %s" % (where_adb, frag_names.get(int(m.group(1)), m.group(1)) if m else "?"), "the engine says: " + (m.group(3) if m else w)))
    if adb and eng["loaded"] and not v.get("ran_with_animation_set"):
        findings.append(_finding("warning", "M006", where_adb, "no character: Validate ran without an animation set, so a missing animation is not reported"))

    # procedural clips the factory does not know
    for f in eng.get("fragments", []):
        for ts in f["tag_sets"]:
            for opt in ts["options"]:
                for layer in opt["proc_layers"]:
                    for pc in layer:
                        if not pc["known"]:
                            findings.append(_finding("error", "M022", "%s fragment %s" % (where_adb, f["name"]), "procedural clip type '%s' is not registered with the engine: it does nothing" % pc["type"]))

    # the manual's rules about the controller definition
    sc = eng.get("scopes")
    if sc:
        for a, b in _layer_overlaps(sc["scope_defs"]):
            findings.append(_finding("error", "M030", controller, "scopes '%s' (layers %d-%d) and '%s' (layers %d-%d) share context '%s' and overlap: the engine does not check; two fragments would fight for the layer" %
                                     (a["name"], a["layer"], a["layer"] + a["num_layers"] - 1, b["name"], b["layer"], b["layer"] + b["num_layers"] - 1, a["context"]), "give each scope of a context its own layers"))
        root = _read_xml(controller) if controller else None
        if root is not None:
            frag_file = (root.find("Fragments").get("filename") if root.find("Fragments") is not None else "") or ""
            ids = _read_xml(frag_file)
            defined = [t.get("name") for t in ids.iter("Tag")] if ids is not None else []
            have = [c.tag for c in (root.find("FragmentDefs") if root.find("FragmentDefs") is not None else [])]
            for name in defined:
                if name.lower() not in [h.lower() for h in have]:
                    findings.append(_finding("warning", "M031", controller, "FragmentDefs has no entry for fragment '%s' (the manual: exactly one per FragmentID): it can use every scope" % name, "add <%s scopes=\"...\"/>" % name))
            declared = {c.tag.lower() for c in (root.find("ScopeContextDefs") if root.find("ScopeContextDefs") is not None else [])}
            for sd in sc["scope_defs"]:
                if sd["context"].lower() not in declared:
                    findings.append(_finding("info", "M032", controller, "scope '%s' uses context '%s', which ScopeContextDefs does not declare (the engine adds it with default tags)" % (sd["name"], sd["context"])))
    if adb and controller:
        a, c = _read_xml(adb), _read_xml(controller)
        if a is not None and c is not None:
            same = {"FragDef": c.find("Fragments").get("filename") if c.find("Fragments") is not None else None, "TagDef": c.find("Tags").get("filename") if c.find("Tags") is not None else None}
            for attr, cv in same.items():
                av = a.get(attr)
                if av and cv and av.lower() != cv.lower():
                    findings.append(_finding("error", "M033", adb, "%s='%s' differs from the controller definition's '%s': the manual requires every database of a controller definition to use the same tag and fragment files" % (attr, av, cv)))

    # the game's code against the database
    code_names = code_fragment_names(code_dir)
    if eng.get("fragments"):
        have = {f["name"].lower(): f for f in eng["fragments"]}
        for name, where in sorted(code_names.items()):
            if name.lower() not in have:
                findings.append(_finding("error", "M040", where[0], "the code asks Mannequin for fragment '%s', which %s does not define: the call returns an invalid id and nothing plays" % (name, adb), "fix the name or add the fragment"))
        asked = {n.lower() for n in code_names}
        for f in eng["fragments"]:
            if f["name"].lower() not in asked and code_names:
                findings.append(_finding("info", "M041", where_adb, "fragment '%s' is defined and no C++ literal asks for it" % f["name"]))
            if not any(ts["options"] for ts in f["tag_sets"]):
                findings.append(_finding("warning", "M042", where_adb, "fragment '%s' has no option in the database (every one was dropped, or none was written): requesting it plays nothing" % f["name"]))

    anims = eng.get("anims", [])
    missing = sorted({a["name"] for a in anims if a["anim_id"] < 0})
    frags = []
    for f in eng.get("fragments", []):
        names = []
        for ts in f["tag_sets"]:
            for opt in ts["options"]:
                names.extend(c["name"] for layer in opt["anim_layers"] for c in layer)
        frags.append({"name": f["name"], "tag_sets": len(f["tag_sets"]), "options": sum(len(ts["options"]) for ts in f["tag_sets"]), "animations": names})
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "findings": findings, "fragments": frags,
            "animations": {"used": len({a["name"] for a in anims}), "missing": missing, "animation_set_size": eng.get("animation_count", -1)},
            "controller": sc, "engine": {"already_loaded": eng.get("already_loaded"), "log": [re.sub(r"\$\d", "", l).strip() for l in eng.get("log", []) if l.strip()],
                                         "validate_ok_flag": v.get("ok"), "note": "the engine's Validate return value keeps only the LAST tag set's answer; findings count the callbacks"},
            "saved": saved_paths}


def _pretty(path):
    parts = []
    for p in path.strip("/").split("/"):
        p = re.sub(r"#\d+$", "", p)
        parts.append(p)
    return "/".join(parts)


def main():
    a = sys.argv[1:]
    if not a or a[0] == "check":
        args = a[1:]
        r = check(*(args[:3] if args else ()))
        print("%s: %d error(s), %d warning(s); %d fragments, %d animations used, %d missing" % ("OK" if r["ok"] else "PROBLEMS", r["errors"], r["warnings"], len(r["fragments"]), r["animations"]["used"], len(r["animations"]["missing"])))
        for f in r["findings"]:
            print("  %-7s %s  %s\n          %s" % (f["severity"], f["code"], f["where"], f["message"]))
    elif a[0] == "clips":
        for c in clip_search(a[1] if len(a) > 1 else ""):
            print("%-34s %s" % (c["name"], ", ".join("%s=%s" % (k, v) for k, v in (c["params"] or {}).items())[:120] or c["note"]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
