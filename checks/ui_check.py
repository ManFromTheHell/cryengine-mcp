#!/usr/bin/env python3
"""The project's UI (FlashUI / Scaleform) as the ENGINE reads it (ENGINE_LEARNING_PLAN J1), from the open editor, without running the game.

FlashUI (CryAction/FlashUI, `gEnv->pFlashUI`) reads every `Libs/UI/UIElements/*.xml` at start: a UIElement names a flash movie (.gfx) and declares the functions, events, variables, arrays and movie clips
the game may use. The game then refers to them BY NAME in code: `Call("SetupScreen", ...)`, `GetUIElement("MainMenu")`, `SameName(event.sName, "onButton")`. A name the XML does not declare is not an
error: the engine makes a "dynamic" definition and calls the movie by that name, and a movie without that function answers nothing. So a typo is silence.

The plugin verb `bridge.ui.dump <file> | init | <element>` writes what the running engine KEPT of each element (and, with init, asks the loaded movie `IFlashPlayer::IsAvailable(path)` for every kept
name, with a positive and a negative control). This module compares that with the XML files and with the project's C++:

  U001 an element file is not valid XML, has no UIElement / name / GFx file          U008 an event the code handles is not declared (OnUIEvent never sees it)
  U002 an element in a file is missing from the engine, or code asks for an unknown   U009 the movie query failed its own control (the answers cannot be trusted)
       element                                                                          U010 code names a Libs/UI file that does not exist
  U003 the flash file (.gfx) does not exist                                           U011 a declared array / movie clip is not in the movie when it loads (it may be made later)
  U004 what the engine kept differs from the file (an entry dropped as a duplicate,  U012 UI action files exist that the engine did not load
       a different layer, flag or parameter list)                                     U013 an attribute or value the engine does not read (silently ignored)
  U005 the code calls a function the element does not declare (a dynamic definition)
  U006 the code calls a function the loaded movie does not have; or (info) a declared function the movie lacks
  U007 a call passes a different number or kind of arguments than the declaration

    python checks/ui_check.py            the project's own UI
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
DATA = os.path.join(HERE, "engine_data")
UI_DIR = os.path.join(PROJECT, "Assets", "libs", "ui")
CODE_DIR = os.path.join(PROJECT, "Code")
ASSETS_DIR = os.path.join(PROJECT, "Assets")

sys.path.insert(0, HERE)

# What SUIElementSerializer reads (CryAction/FlashUI/FlashUIElement.cpp); anything else on those nodes is dropped without a message.
FLAG_ATTRS = {"mouseevents": "mouse_events", "keyevents": "key_events", "cursor": "hardware_cursor", "console_mouse": "console_mouse", "console_cursor": "console_cursor",
              "controller_input": "controller_input", "events_exclusive": "events_exclusive", "render_lockless": "render_lockless", "force_no_unload": "force_no_unload",
              "fixed_proj_depth": "fixed_proj_depth", "lazy_update": None, "is_hud": "is_hud", "shared_rt": "shared_rt", "show_at_loading_time": "show_at_loading_time"}
KNOWN_ATTRS = {
    "uielement": {"name"} | set(FLAG_ATTRS),
    "gfx": {"file", "layer", "alpha"},
    "align": {"mode", "halign", "valign", "scale", "max"},
    "position": {"top", "left", "width", "height"},
    "function": {"name", "funcname", "desc", "dynamic", "dynname", "dyndesc"},
    "event": {"name", "fscommand", "desc", "dynamic", "dynname", "dyndesc"},
    "param": {"name", "desc", "type"}, "in_param": {"name", "desc", "type"}, "out_param": {"name", "desc", "type"},
    "variable": {"name", "varname", "desc", "type"}, "array": {"name", "varname", "desc", "type"}, "movieclip": {"name", "instancename", "desc", "type"},
}
MODES = ("fixed", "dynamic", "fullscreen", "fixdyntexsize")
ALIGNS = ("left", "top", "mid", "right", "bottom")
PARAM_TYPES = ("bool", "int", "float", "string")
SECTIONS = {"functions": ("function", "funcname"), "events": ("event", "fscommand"), "variables": (None, "varname"), "arrays": (None, "varname"), "movieclips": (None, "instancename")}


class UiError(RuntimeError):
    pass


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def _slash(p):
    return p.replace(chr(92), "/")


def _bool(value):
    """CXmlNode::getAttr(bool&): true / false, else atoi(value) != 0."""
    v = (value or "").strip().lower()
    if v == "true":
        return True
    if v == "false":
        return False
    m = re.match(r"^[+-]?\d+", v)
    return bool(m and int(m.group()) != 0)


def _ci(node, tag):
    """The engine's findChild is case-insensitive (the sample writes <Arrays> and <MovieClips>)."""
    for child in node:
        if child.tag.lower() == tag.lower():
            return child
    return None


# ------------------------------------------------------------------ the XML files, read by the engine's rules

def _entries(node, kind, key_attr, problems, where, wanted_tag=None):
    """The entries SUIElementSerializer would keep from one section: (display, movie name, desc, type, param_types), in order, with the duplicates it drops."""
    kept, dropped = [], []
    if node is None:
        return kept, dropped
    for child in node:
        tag = child.tag.lower()
        if wanted_tag and tag != wanted_tag:
            continue
        for attr in child.attrib:
            if attr.lower() not in KNOWN_ATTRS.get(tag, set(child.attrib)):
                problems.append(_finding("warning", "U013", "%s <%s name=\"%s\">" % (where, child.tag, child.get("name", "")), "the attribute '%s' is not one the engine reads (it is ignored without a message)" % attr))
        display = child.get("name", "")
        movie = child.get(key_attr, "") or display
        if "_root." in movie:
            movie = movie.split("_root.", 1)[1]
        ptype = child.get("type", "")
        if ptype and ptype.lower() not in PARAM_TYPES:
            problems.append(_finding("info", "U013", "%s <%s name=\"%s\">" % (where, child.tag, display), "the type '%s' is not bool / int / float / string: it is read as 'any'" % ptype))
        params = []
        for sub in child:
            if sub.tag.lower() in ("param", "in_param", "out_param") and sub.get("type", "") and sub.get("type", "").lower() not in PARAM_TYPES:
                problems.append(_finding("info", "U013", "%s <%s name=\"%s\"> param %s" % (where, child.tag, display, sub.get("name", "")), "the type '%s' is not bool / int / float / string: it is read as 'any'" % sub.get("type")))
            if sub.tag.lower() in ("param", "in_param"):
                params.append((sub.get("name", ""), (sub.get("type", "") or "any").lower() if (sub.get("type", "") or "").lower() in PARAM_TYPES else "any"))
                for attr in sub.attrib:
                    if attr.lower() not in KNOWN_ATTRS["param"]:
                        problems.append(_finding("warning", "U013", "%s <%s name=\"%s\"> param %s" % (where, child.tag, display, sub.get("name", "")), "the attribute '%s' is not one the engine reads" % attr))
        dup = next((k for k in kept if k["display"].lower() == display.lower() or k["movie"].lower() == movie.lower()), None)
        entry = {"display": display, "movie": movie, "type": (ptype.lower() if ptype.lower() in PARAM_TYPES else "any"), "params": params, "dynamic": _bool(child.get("dynamic"))}
        if dup is not None:
            dropped.append((entry, dup))
        else:
            kept.append(entry)
    return kept, dropped


def read_element_file(path):
    """-> {file, error, elements: [{name, gfx, layer, flags, constraints, <section>: [entry]}], problems: [finding]}"""
    where = os.path.basename(path)
    out = {"file": where, "error": None, "elements": [], "problems": []}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        out["error"] = str(exc)
        out["problems"].append(_finding("error", "U001", where, "cannot be read as XML: %s (the engine logs 'Error in element xml file' and loads nothing from it)" % exc))
        return out
    nodes = [root] if root.tag.lower() == "uielement" else [c for c in root if c.tag.lower() == "uielement"]
    if root.tag.lower() not in ("uielements", "uielement"):
        out["problems"].append(_finding("error", "U001", where, "the root is <%s>, not <UIElements>" % root.tag))
    if not nodes:
        out["problems"].append(_finding("error", "U001", where, "the file declares no <UIElement>"))
    for node in nodes:
        problems = out["problems"]
        name = node.get("name", "")
        label = "%s <UIElement %s>" % (where, name)
        if not name:
            problems.append(_finding("error", "U001", where, "a <UIElement> has no name"))
        for attr in node.attrib:
            if attr.lower() not in KNOWN_ATTRS["uielement"]:
                problems.append(_finding("warning", "U013", label, "the attribute '%s' is not one the engine reads (a flag spelt differently is simply off)" % attr))
        flags = sorted(FLAG_ATTRS[a.lower()] for a in node.attrib if a.lower() in FLAG_ATTRS and FLAG_ATTRS[a.lower()] and _bool(node.get(a)))
        gfx = _ci(node, "gfx")
        element = {"name": name, "gfx": None, "layer": 0, "flags": flags, "align": {}, "where": label}
        if gfx is None:
            problems.append(_finding("error", "U001", label, "no <GFx file=...> child: the element has no movie"))
        else:
            for attr in gfx.attrib:
                if attr.lower() not in KNOWN_ATTRS["gfx"]:
                    problems.append(_finding("warning", "U013", label, "<GFx> attribute '%s' is not read by the engine" % attr))
            element["gfx"] = gfx.get("file", "")
            try:
                element["layer"] = int(float(gfx.get("layer", "0") or 0))
            except ValueError:
                element["layer"] = 0
                problems.append(_finding("warning", "U013", label, "<GFx layer=\"%s\"> is not a number: the layer stays 0" % gfx.get("layer")))
            cons = _ci(gfx, "constraints")
            align = _ci(cons, "align") if cons is not None else None
            if align is not None:
                element["align"] = {"mode": (align.get("mode") or "dynamic").lower(), "halign": (align.get("halign") or "mid").lower(), "valign": (align.get("valign") or "mid").lower(),
                                    "scale": _bool(align.get("scale")), "max": _bool(align.get("max"))}
                for attr in align.attrib:
                    if attr.lower() not in KNOWN_ATTRS["align"]:
                        problems.append(_finding("warning", "U013", label, "<Align> attribute '%s' is not read by the engine" % attr))
                if align.get("mode") and align.get("mode").lower() not in MODES:
                    problems.append(_finding("warning", "U013", label, "<Align mode=\"%s\"> is not one of %s: it is read as 'dynamic'" % (align.get("mode"), "/".join(MODES))))
                for which in ("halign", "valign"):
                    value = (align.get(which) or "").lower()
                    if value and value not in ALIGNS:
                        # an unknown word falls back to 'mid': for centre / center / middle that is what was meant
                        harmless = value in ("center", "centre", "middle")
                        problems.append(_finding("info" if harmless else "warning", "U013", label, "<Align %s=\"%s\"> is not one of %s: it is read as 'mid'%s" % (
                            which, align.get(which), "/".join(ALIGNS), " (the same as what was meant)" if harmless else "")))
        for section, (wanted_tag, key_attr) in SECTIONS.items():
            kept, dropped = _entries(_ci(node, section), section, key_attr, problems, label, wanted_tag)
            element[section] = kept
            for entry, dup in dropped:
                problems.append(_finding("error", "U004", label, "%s '%s' (%s) is dropped by the engine as 'not unique': it clashes with '%s' (%s) -- display names and movie names are both compared, case-insensitively" % (
                    section[:-1], entry["display"], entry["movie"], dup["display"], dup["movie"])))
        # the root clip the engine adds to every element
        element["movieclips"] = [{"display": "Root", "movie": "_root", "type": "any", "params": [], "dynamic": False}] + element["movieclips"]
        out["elements"].append(element)
    return out


def read_element_files(ui_dir=UI_DIR):
    folder = os.path.join(ui_dir, "UIElements")
    files = []
    if os.path.isdir(folder):
        for f in sorted(os.listdir(folder)):
            if f.lower().endswith(".xml"):
                files.append(read_element_file(os.path.join(folder, f)))
    return files


# ------------------------------------------------------------------ the C++ side

def strip_comments(text):
    """Remove // and /* */ comments but keep string and character literals."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == chr(92) else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            out.append(" ")
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def split_args(text, start):
    """text[start] is the first character after '(' -> ([argument text], index after ')')."""
    args, depth, i, n, cur = [], 0, start, len(text), start
    while i < n:
        c = text[i]
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == chr(92) else 1
            i = j + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                tail = text[cur:i].strip()
                if tail or args:
                    args.append(tail)
                return args, i + 1
            depth -= 1
        elif c == "," and depth == 0:
            args.append(text[cur:i].strip())
            cur = i + 1
        i += 1
    return args, n


KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "else", "sizeof"}
FUNC_DEF = re.compile(r"(?:^|\n)[ \t]*[\w:<>\*&,\s]*?[\w\*&>]\s+(?:\w+::)*(\w+)\s*\(([^()]*)\)\s*(?:const\s*)?(?:override\s*)?\n?\s*\{")


def _kind_of(ctype):
    t = ctype.replace("const", " ").replace("&", " ").strip()
    if "char" in t and "*" in t:
        return "string"
    low = t.lower()
    if "string" in low:
        return "string"
    if low.startswith("bool"):
        return "bool"
    if re.match(r"^(u?int\d*(_t)?|short|long|size_t|unsigned)\b", low):
        return "int"
    if low.startswith(("float", "double")):
        return "float"
    return None


def _signatures(text):
    """[(position of '{', {parameter name: kind})] for each function definition found."""
    sigs = []
    for m in FUNC_DEF.finditer(text):
        if m.group(1) in KEYWORDS:
            continue
        params = {}
        for part in split_args(m.group(2) + ")", 0)[0]:
            pm = re.match(r"^(.*?)(\w+)\s*(?:=.*)?$", part.strip(), re.S)
            if pm:
                kind = _kind_of(pm.group(1))
                if kind:
                    params[pm.group(2)] = kind
        sigs.append((m.end(), params))
    return sigs


def _arg_kind(expr, params):
    e = expr.strip()
    if re.match(r'^L?"', e):
        return "string"
    if e in ("true", "false"):
        return "bool"
    if re.match(r"^-?\d+$", e):
        return "int"
    if re.match(r"^-?(\d+\.\d*|\.\d+)f?$", e) or re.match(r"^-?\d+f$", e):
        return "float"
    if e.endswith(".c_str()") or e.endswith("->c_str()"):
        return "string"
    if re.match(r"^\w+$", e):
        return params.get(e)
    return None


def parse_code(code_dir=CODE_DIR):
    """-> {calls: [{name, argc, kinds, file, line}], event_names: [{field, name, file, line}], elements: [{name, file, line}], paths: [{path, file, line}], files}"""
    res = {"calls": [], "call_functions": [], "event_names": [], "elements": [], "paths": [], "files": 0}
    for root, _dirs, files in os.walk(code_dir):
        for f in sorted(files):
            if not f.lower().endswith((".cpp", ".h", ".inl")):
                continue
            path = os.path.join(root, f)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    raw = fh.read()
            except OSError:
                continue
            text = strip_comments(raw)
            res["files"] += 1
            rel = os.path.relpath(path, PROJECT).replace(os.sep, "/")
            sigs = _signatures(text)

            def line_of(pos):
                return text.count("\n", 0, pos) + 1

            def params_at(pos):
                best = {}
                for end, params in sigs:
                    if end <= pos:
                        best = params
                    else:
                        break
                return best

            for m in re.finditer(r'(?<![\w.>])Call\s*\(\s*"([^"]+)"', text):
                args, _end = split_args(text, m.start() + text[m.start():].index("(") + 1)
                params = params_at(m.start())
                res["calls"].append({"name": m.group(1), "argc": len(args) - 1, "kinds": [_arg_kind(a, params) for a in args[1:]], "file": rel, "line": line_of(m.start())})
            for m in re.finditer(r'CallFunction\s*\(\s*"([^"]+)"', text):
                res["call_functions"].append({"name": m.group(1), "file": rel, "line": line_of(m.start())})
            for m in re.finditer(r'SameName\s*\(\s*event\s*\.\s*(sName|sDisplayName)\s*,\s*"([^"]+)"', text):
                res["event_names"].append({"field": m.group(1), "name": m.group(2), "file": rel, "line": line_of(m.start())})
            for m in re.finditer(r'GetUIElement\s*\(\s*"([^"]+)"', text):
                res["elements"].append({"name": m.group(1), "file": rel, "line": line_of(m.start())})
            for m in re.finditer(r'"(libs/ui/[^"]+\.(?:dds|gfx|xml|png|tga|jpg|swf|ttf))"', text, re.I):
                res["paths"].append({"path": m.group(1), "file": rel, "line": line_of(m.start())})
    return res


# ------------------------------------------------------------------ the engine's side

def engine_dump(init=True, timeout=60):
    """Run bridge.ui.dump in the open editor -> the parsed file, or None when there is no editor."""
    import bridge_py
    os.makedirs(DATA, exist_ok=True)
    out = os.path.join(DATA, "ui_dump.json")
    if os.path.exists(out):
        os.remove(out)
    cmd = "bridge.ui.dump %s%s" % (_slash(out), " | init" if init else "")
    r = bridge_py.send(cmd, timeout)
    if not r.get("ok") or not os.path.isfile(out):
        raise UiError("bridge.ui.dump failed: %s" % json.dumps(r)[:300])
    with open(out, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ the checks

def _gfx_exists(ui_dir, name):
    """The engine looks for gfx_uiaction_folder ('Libs/UI/') + file, then the file as written."""
    for candidate in (os.path.join(ui_dir, name.replace("/", os.sep)), os.path.join(os.path.dirname(ui_dir), name.replace("/", os.sep))):
        if os.path.isfile(candidate):
            return True
    return False


def _asset_exists(assets_dir, rel):
    parts = rel.replace(chr(92), "/").split("/")
    cur = assets_dir
    for p in parts:
        if not os.path.isdir(cur):
            return False
        hit = next((e for e in os.listdir(cur) if e.lower() == p.lower()), None)
        if hit is None:
            return False
        cur = os.path.join(cur, hit)
    return os.path.exists(cur)


def compare_engine(element, eng):
    """One file element against the engine's record of it -> [finding]."""
    out, where = [], element["where"]
    if eng.get("file") != element["gfx"]:
        out.append(_finding("error", "U004", where, "the engine kept the movie file '%s', the file says '%s'" % (eng.get("file"), element["gfx"])))
    if eng.get("layer") != element["layer"]:
        out.append(_finding("error", "U004", where, "the engine kept layer %s, the file says %s" % (eng.get("layer"), element["layer"])))
    if sorted(eng.get("flags", [])) != element["flags"]:
        out.append(_finding("error", "U004", where, "the engine kept the flags %s, the file asks for %s" % (sorted(eng.get("flags", [])), element["flags"])))
    if element["align"]:
        c, a = eng.get("constraints", {}), element["align"]
        mode = a["mode"] if a["mode"] in MODES else "dynamic"
        halign = {"left": "lower", "top": "lower", "mid": "mid", "right": "upper", "bottom": "upper"}.get(a["halign"], "mid")
        valign = {"left": "lower", "top": "lower", "mid": "mid", "right": "upper", "bottom": "upper"}.get(a["valign"], "mid")
        want = {"type": mode, "halign": halign, "valign": valign, "scale": a["scale"], "max": a["max"]}
        got = {k: c.get(k) for k in want}
        if want != got:
            out.append(_finding("error", "U004", where, "the engine kept the constraints %s, the file says %s" % (got, want)))
    for section, label in (("functions", "function"), ("events", "event"), ("arrays", "array"), ("movieclips", "movie clip"), ("variables", "variable")):
        eng_rows = eng.get(section, [])
        if section == "events":
            eng_key = {(r["name"], r["fscommand"]): r for r in eng_rows}
        else:
            eng_key = {(r["name"], r["movie_name"]): r for r in eng_rows}
        file_key = {(e["display"], e["movie"]): e for e in element[section]}
        for k in sorted(set(file_key) - set(eng_key)):
            out.append(_finding("error", "U004", where, "the file declares %s '%s' (%s) and the engine did not keep it" % (label, k[0], k[1])))
        for k in sorted(set(eng_key) - set(file_key)):
            out.append(_finding("warning", "U004", where, "the engine holds %s '%s' (%s) that the file does not declare (made at run time?)" % (label, k[0], k[1])))
        if section in ("functions", "events"):
            for k in sorted(set(file_key) & set(eng_key)):
                fp, ep = file_key[k]["params"], eng_key[k].get("params", [])
                if len(fp) != len(ep):
                    out.append(_finding("error", "U004", where, "%s '%s': the engine kept %d parameters, the file lists %d" % (label, k[0], len(ep), len(fp))))
    return out


def check(ui_dir=UI_DIR, code_dir=CODE_DIR, engine=None, with_engine=True, assets_dir=ASSETS_DIR, init_movie=True):
    """-> {ok, errors, warnings, findings, elements: [...], files, code: {...}, engine: bool}

    `engine` is a bridge.ui.dump result (a dict); with None and with_engine the open editor is asked (init_movie: also load the movie and ask it)."""
    findings = []
    files = read_element_files(ui_dir)
    for f in files:
        findings.extend(f["problems"])
    elements = {e["name"].lower(): e for f in files for e in f["elements"] if e["name"]}
    code = parse_code(code_dir) if os.path.isdir(code_dir) else {"calls": [], "call_functions": [], "event_names": [], "elements": [], "paths": [], "files": 0}
    eng_elements = None
    if engine is None and with_engine:
        try:
            engine = engine_dump(init_movie)
        except Exception as exc:                                  # noqa: BLE001 - no editor: the file and code checks still run
            findings.append(_finding("info", "U002", "engine", "the open editor was not asked (%s): only the files and the code were compared" % str(exc)[:120]))
            engine = None
    if engine is not None:
        if not engine.get("available"):
            findings.append(_finding("error", "U002", "engine", "the engine has no FlashUI (gEnv->pFlashUI is empty): no screen can be shown"))
        else:
            eng_elements = {e["name"].lower(): e for e in engine.get("elements", [])}

    # --- each file element against the engine
    for key, el in elements.items():
        if el["gfx"] is not None and not _gfx_exists(ui_dir, el["gfx"]):
            findings.append(_finding("error", "U003", el["where"], "the movie '%s' is in neither Libs/UI/ nor the path as written: the element has no movie" % el["gfx"], "add the .gfx to Assets/libs/ui"))
        if eng_elements is not None:
            eng = eng_elements.get(key)
            if eng is None:
                findings.append(_finding("error", "U002", el["where"], "the engine did not keep this element (look for 'Error in element xml file' in the log)"))
                continue
            findings.extend(compare_engine(el, eng))
            if eng.get("movie_loaded"):
                mv = eng.get("movie", {})
                if mv.get("control_root") is not True or mv.get("control_absent") is not False:
                    findings.append(_finding("error", "U009", el["where"], "the movie query failed its controls (_root present = %s, a made-up name present = %s): the answers about this movie are not trusted" % (mv.get("control_root"), mv.get("control_absent"))))
            for extra in sorted(set(eng_elements) - set(elements)):
                pass
    if eng_elements is not None:
        for key in sorted(set(eng_elements) - set(elements)):
            findings.append(_finding("info", "U002", eng_elements[key]["name"], "the engine holds an element that none of the project's files declares (an engine or plug-in file)"))

    # --- the code against the declarations
    declared = None
    used_elements = sorted({e["name"] for e in code["elements"]})
    for e in code["elements"]:
        if e["name"].lower() not in elements and (eng_elements is None or e["name"].lower() not in eng_elements):
            findings.append(_finding("error", "U002", "%s:%d" % (e["file"], e["line"]), "the code asks for the UI element '%s' and no UIElements file declares it" % e["name"], "declare it in Assets/libs/ui/UIElements"))
    if len(used_elements) == 1 and used_elements[0].lower() in elements:
        declared = elements[used_elements[0].lower()]
        eng = (eng_elements or {}).get(used_elements[0].lower())
        movie_has = {}
        if eng and eng.get("movie_loaded"):
            for r in eng.get("functions", []):
                if "in_movie" in r:
                    movie_has[r["name"].lower()] = r["in_movie"]
        fn = {f["display"].lower(): f for f in declared["functions"]}
        by_movie = {f["movie"].lower(): f for f in declared["functions"]}
        called = set()
        for c in code["calls"] + [dict(x, argc=None, kinds=[]) for x in code["call_functions"]]:
            where = "%s:%d" % (c["file"], c["line"])
            decl = fn.get(c["name"].lower())
            if decl is None:
                alias = by_movie.get(c["name"].lower())
                findings.append(_finding("warning", "U005", where, "the code calls '%s' and %s declares no function of that name%s: the engine makes a dynamic definition and calls the movie's '%s'%s" % (
                    c["name"], declared["name"], " (the movie name of '%s' is '%s'; the XML name is '%s')" % (alias["display"], alias["movie"], alias["display"]) if alias else "", c["name"],
                    "" if not movie_has else (", which the loaded movie has" if movie_has.get(c["name"].lower()) else ", which the loaded movie does not have"))))
                continue
            called.add(decl["display"].lower())
            if movie_has and movie_has.get(decl["display"].lower()) is False:
                findings.append(_finding("error", "U006", where, "the code calls '%s' (the movie's '_root.%s') and the loaded movie has no such function: the call does nothing" % (decl["display"], decl["movie"])))
            if c["argc"] is not None and not decl["dynamic"] and c["argc"] != len(decl["params"]):
                findings.append(_finding("warning", "U007", where, "'%s' is called with %d argument(s) and the declaration lists %d (%s)" % (decl["display"], c["argc"], len(decl["params"]), ", ".join(p[0] for p in decl["params"]))))
            elif c["argc"] is not None:
                for i, (kind, param) in enumerate(zip(c["kinds"], decl["params"])):
                    want = param[1]
                    if kind and want != "any" and kind != want and not ({kind, want} <= {"int", "float"}):
                        findings.append(_finding("warning", "U007", where, "'%s' argument %d (%s) is a %s in the code and %s in the declaration" % (decl["display"], i + 1, param[0], kind, want)))
        if movie_has:
            for name, present in sorted(movie_has.items()):
                if present is False and name not in called:
                    f = fn.get(name)
                    findings.append(_finding("info", "U006", declared["where"], "function '%s' is declared and the loaded movie has no '_root.%s' (the code does not call it)" % (f["display"] if f else name, f["movie"] if f else name)))
            for section, label in (("arrays", "array"), ("movieclips", "movie clip")):
                for r in (eng or {}).get(section, []):
                    if r.get("in_movie") is False:
                        findings.append(_finding("info", "U011", declared["where"], "%s '%s' (%s) is not in the movie when it loads (it may be created by the movie's script or by a call)" % (label, r["name"], r["movie_name"])))
        # events
        ev_by_fs = {e["movie"].lower(): e for e in declared["events"]}
        ev_by_name = {e["display"].lower(): e for e in declared["events"]}
        for e in code["event_names"]:
            table = ev_by_fs if e["field"] == "sName" else ev_by_name
            if e["name"].lower() not in table:
                findings.append(_finding("error", "U008", "%s:%d" % (e["file"], e["line"]), "the code handles the event %s '%s' and %s declares no such event: OnUIEvent is never called for it (OnUIEventEx gets it undeclared)" % (
                    "fscommand" if e["field"] == "sName" else "name", e["name"], declared["name"])))
    elif used_elements and len(used_elements) > 1:
        findings.append(_finding("info", "U002", "code", "the code uses %d elements (%s): calls and events are not matched to a declaration (this check matches calls and events only when the code uses exactly one UI element)" % (len(used_elements), ", ".join(used_elements))))

    # --- files the code names
    seen = set()
    for p in code["paths"]:
        key = p["path"].lower()
        if key in seen:
            continue
        seen.add(key)
        if not _asset_exists(assets_dir, p["path"]):
            findings.append(_finding("error", "U010", "%s:%d" % (p["file"], p["line"]), "the code names '%s' and no such file is under Assets" % p["path"]))

    # --- UI actions
    action_dir = os.path.join(ui_dir, "UIActions")
    action_files = sorted(f for f in os.listdir(action_dir) if f.lower().endswith((".xml", ".lua"))) if os.path.isdir(action_dir) else []
    if engine is not None and engine.get("available"):
        names = {a["name"].lower() for a in engine.get("actions", [])}
        for f in action_files:
            if os.path.splitext(f)[0].lower() not in names:
                findings.append(_finding("warning", "U012", f, "a UI action file the engine did not load (an invalid graph is discarded; look in the log)"))

    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    summary = [{"file": f["file"], "elements": [{"name": e["name"], "movie": e["gfx"], "layer": e["layer"], "functions": len(e["functions"]), "events": len(e["events"]), "arrays": len(e["arrays"]), "movieclips": len(e["movieclips"]) - 1} for e in f["elements"]],
               "readable": f["error"] is None} for f in files]
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "findings": findings, "files": summary, "engine": engine is not None,
            "movies_loaded": sum(1 for e in (engine or {}).get("elements", []) if e.get("movie_loaded")),
            "code": {"files_scanned": code["files"], "calls": len(code["calls"]) + len(code["call_functions"]), "events_handled": len(code["event_names"]), "elements_used": used_elements, "paths": len(seen)},
            "actions": len((engine or {}).get("actions", []))}


def main():
    r = check()
    print("%s: %d error(s), %d warning(s); engine %s; %d movie(s) loaded; code: %s" % ("OK" if r["ok"] else "PROBLEMS", r["errors"], r["warnings"], "asked" if r["engine"] else "not asked", r["movies_loaded"], r["code"]))
    for f in r["files"]:
        print("  ", f)
    for f in r["findings"]:
        print("  %-7s %s  %s\n          %s" % (f["severity"], f["code"], f["where"], f["message"][:300]))


if __name__ == "__main__":
    main()
