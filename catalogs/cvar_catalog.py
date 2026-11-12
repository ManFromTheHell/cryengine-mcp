#!/usr/bin/env python3
"""The engine's console variables and commands as a catalog (ENGINE_LEARNING_PLAN L3).

Three sources, each used for what it knows best:

  1. the RUNNING editor, through the engine's own tools: `DumpCommandsVars` (every variable and command:
     name, type, current value, help) and `audit_cvars cheat const readonly dev dedi` (the flags the first
     one leaves out: its GetFlagsString prints only READONLY/DEPRECATED/DUMPTODISK/RELOAD/RESTART, so a
     cheat variable such as e_DefaultMaterial looks like any other). Only this knows what is REALLY
     registered, including the variables the game and the editor add.
  2. the engine SOURCE (read-only reference): where each variable is registered (file:line), its DEFAULT
     value (the dump holds the current one), which macro registered it (the *_DEV_ONLY macros register
     nothing in a Release build) and the preprocessor conditions around it.
  3. the rules in CrySystem/XConsole.cpp that decide whether a change is accepted (see settable()).

    python catalogs/cvar_catalog.py --capture     # needs the editor: runs the two engine commands, saves the raw dumps
    python catalogs/cvar_catalog.py --build       # raw dumps + source scan -> engine_data/cvars.json
    python catalogs/cvar_catalog.py <text>        # search

Nothing here changes the engine. --capture runs two diagnostic console commands in the editor and moves
the file the first one writes (consolecommandsandvars.txt, ConsoleHTMLHelp/) out of the project root.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import shutil
import sys

import cpp_conditions as cpp

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
DATA = os.path.join(HERE, "engine_data")
CATALOG = os.path.join(DATA, "cvars.json")
SOURCE = _paths.ENGINE_SOURCE   # env CRYENGINE_SOURCE; "" when unset (the source-reading steps then skip)
# where registrations live: the engine, its extensions/plugins, the sample game, the editor, the launcher, and this project's own code
SOURCE_DIRS = ["Code/CryEngine", "Code/CryExtensions", "Code/CryPlugins", "Code/GameSDK", "Code/Sandbox", "Code/Launcher"]
PROJECT_DIRS = [os.path.join(PROJECT, "Code")]          # this game's own variables (ash_*, claude_*)

NL = chr(10)

FLAG_NAMES = {  # IConsole.h, the VF_ values the audit and the dump can show us
    "CHEAT": 0x2, "DEV_ONLY": 0x4, "DEDI_ONLY": 0x8, "READONLY": 0x800, "CONST_CVAR": 0x800000,
}


# --------------------------------------------------------------------------- the editor's own dumps

def parse_dump(text):
    """consolecommandsandvars.txt -> ({name: {type, current, help, flags}}, {name: {help, flags}})."""
    variables, commands = {}, {}
    cur = None
    for line in text.splitlines():
        if line.startswith("variable: "):
            parts = line[len("variable: "):].split(" ", 1)
            cur = {"name": parts[0], "flags": [f.strip() for f in (parts[1] if len(parts) > 1 else "").split(",") if f.strip()],
                   "type": "", "current": "", "help": ""}
            variables[cur["name"]] = cur
            field = None
        elif line.startswith("Command: "):
            parts = line[len("Command: "):].split(" ", 1)
            cur = {"name": parts[0], "flags": [f.strip() for f in (parts[1] if len(parts) > 1 else "").split(",") if f.strip()], "help": ""}
            commands[cur["name"]] = cur
            field = None
        elif cur is not None:
            if line.startswith("type: ") and "type" in cur and field is None:
                cur["type"] = line[6:].strip()
            elif line.startswith("current: ") and "current" in cur and field is None:
                cur["current"] = line[9:]
            elif line.startswith("script: ") and field is None:
                pass
            elif line.startswith("help: ") and field is None:
                cur["help"] = line[6:]
                field = "help"
            elif field == "help":
                cur["help"] += "\n" + line
    for rec in list(variables.values()) + list(commands.values()):
        rec["help"] = rec["help"].strip()
    return variables, commands


def parse_audit(text):
    """The [CVARS]: [VARIABLE] lines of `audit_cvars` -> {name: set of flag names}. Lines arrive twice (console + log mirror): a set absorbs that."""
    out = {}
    for m in re.finditer(r"\[CVARS\]: \[VARIABLE\] (\S+)((?: \[VF_\w+\])*)", text):
        out.setdefault(m.group(1), set()).update(re.findall(r"\[VF_(\w+)\]", m.group(2)))
    total = re.findall(r"\[END AUDIT\] \(commands (\d+)/(\d+); variables (\d+)/(\d+)\)", text)
    return out, (tuple(map(int, total[0])) if total else None)


def capture(out_dir=DATA):
    """Run the engine's two diagnostic commands in the open editor and keep their output. Needs the bridge (the editor open)."""
    import bridge_py
    os.makedirs(out_dir, exist_ok=True)
    before = {f: os.path.getmtime(os.path.join(PROJECT, f)) for f in ("consolecommandsandvars.txt",) if os.path.exists(os.path.join(PROJECT, f))}
    bridge_py.send("DumpCommandsVars", timeout=60)
    dump = os.path.join(PROJECT, "consolecommandsandvars.txt")
    if not os.path.exists(dump) or (dump and before.get("consolecommandsandvars.txt") == os.path.getmtime(dump)):
        raise RuntimeError("DumpCommandsVars did not write %s: is the editor the one for this project (its working folder is the project root)?" % dump)
    shutil.move(dump, os.path.join(out_dir, "consolecommandsandvars.txt"))
    html = os.path.join(PROJECT, "ConsoleHTMLHelp")
    if os.path.isdir(html):           # the same dump as HTML pages per prefix: not needed, it is only the same data
        shutil.rmtree(html, ignore_errors=True)
    resp = bridge_py.send("audit_cvars cheat const readonly dev dedi", timeout=120)
    audit = resp.get("output", "")
    flags, total = parse_audit(audit)
    if not total or total[2] != total[3] or total[2] != len(flags):
        raise RuntimeError("the audit did not list every variable: %r vs %d parsed" % (total, len(flags)))
    with open(os.path.join(out_dir, "cvar_flags.txt"), "w", encoding="utf-8", newline="\n") as f:
        for name in sorted(flags):
            f.write("%s %s\n" % (name, " ".join(sorted(flags[name]))))
    return {"variables": total[3], "dump": os.path.join(out_dir, "consolecommandsandvars.txt")}


# --------------------------------------------------------------------------- the source scan

# macro -> (kind, roles by argument position). Roles: name (a string), ident (the variable's own name: the macro stringifies it),
# var, default, flags, help, func, cb. Learnt from CryCommon/CrySystem/ConsoleRegistration.h (read it: the argument names say it).
def _sig(kind, *roles):
    return kind, roles


MACROS = {}
for suffix, extra in (("", ()), ("_CB", ("cb",))):
    MACROS["REGISTER_CVAR" + suffix] = _sig("cvar", "ident", "default", "flags", "help", *extra)
    MACROS["REGISTER_CVAR2" + suffix] = _sig("cvar", "name", "var", "default", "flags", "help", *extra)
    MACROS["REGISTER_CVAR3" + suffix] = _sig("cvar", "name", "var", "default", "flags", "help", *extra)
    for t in ("INT", "INT64", "FLOAT", "STRING"):
        MACROS["REGISTER_%s%s" % (t, suffix)] = _sig("cvar", "name", "default", "flags", "help", *extra)
MACROS["REGISTER_CVAR_AUTO"] = _sig("cvar", "type", "ident", "default", "flags", "help")       # Cry3DEngine/SVO/SceneTreeCVars.inl
MACROS["REGISTER_CVAR_AUTO_STRING"] = _sig("cvar", "ident", "default", "flags", "help")
MACROS["attachVariable"] = _sig("cvar", "name", "var", "help", "flags")                          # CrySystem/SystemInit.cpp: the sys_Pak* variables
MACROS["REGISTER_COMMAND"] = _sig("command", "name", "func", "flags", "help")
MACROS["REGISTER_MANAGED_COMMAND"] = _sig("command", "name", "func", "flags", "help")
MACROS["DefineConstIntCVar"] = _sig("cvar", "ident", "default", "flags", "help")
MACROS["DefineConstIntCVar3"] = _sig("cvar", "name", "var", "default", "flags", "help")
MACROS["DefineConstIntCVarName"] = _sig("cvar", "name", "ident", "default", "flags", "help")
MACROS["DefineConstFloatCVar"] = _sig("cvar", "ident", "flags", "help")
for base in [k for k in list(MACROS) if k.startswith("REGISTER_")]:
    for tag in ("DEV_ONLY", "DEDI_ONLY"):
        # REGISTER_CVAR_CB_DEV_ONLY: the tag goes last, after any _CB
        MACROS[base + "_" + tag] = MACROS[base]

# console method calls: pConsole->RegisterInt("name", default, flags, help), ->Register("name", &var, default, flags, help), ->AddCommand(...)
METHODS = {
    "RegisterInt": _sig("cvar", "name", "default", "flags", "help"), "RegisterInt64": _sig("cvar", "name", "default", "flags", "help"),
    "RegisterFloat": _sig("cvar", "name", "default", "flags", "help"), "RegisterString": _sig("cvar", "name", "default", "flags", "help"),
    "Register": _sig("cvar", "name", "var", "default", "flags", "help"), "AddCommand": _sig("command", "name", "func", "flags", "help"),
}

_MACRO_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, MACROS), key=len, reverse=True)) + r")\s*\(")
_METHOD_RE = re.compile(r"\b\w*[Cc]onsole\w*\s*(?:->|\.)\s*(" + "|".join(METHODS) + r")\s*\(")
_STR = re.compile(r'"((?:[^"\\]|\\.)*)"')


def split_args(text, start):
    """Arguments of the call whose '(' is at text[start-1]: split at depth-0 commas, understanding strings, chars and comments.
    Returns (args, index after the ')'), or (None, start) when the call does not close."""
    depth, i, n, args, cur = 1, start, len(text), [], []
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            cur.append(text[i:j + 1]); i = j + 1; continue
        if c == "'":
            j = i + 1
            while j < n and text[j] != "'":
                j += 2 if text[j] == "\\" else 1
            cur.append(text[i:j + 1]); i = j + 1; continue
        if c == "/" and text[i:i + 2] == "//":
            j = text.find("\n", i)
            i = n if j < 0 else j; continue
        if c == "/" and text[i:i + 2] == "/*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2; continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                args.append("".join(cur).strip())
                return args, i + 1
        elif c == "," and depth == 1:
            args.append("".join(cur).strip()); cur = []; i += 1; continue
        cur.append(c); i += 1
    return None, start


def literal(arg):
    """The text of a C string literal, or of several side by side ("a" "b"); None if the argument is not a literal."""
    if not arg or not _STR.search(arg):
        return None
    rest = _STR.sub("", arg)
    if re.sub(r"\s|CVARHELP|\(|\)|", "", rest):      # something other than strings (and the CVARHELP wrapper) in it
        return None
    text = "".join(m.group(1) for m in _STR.finditer(arg))
    return text.replace("\\n", "\n").replace('\\"', '"').replace("\\t", "\t").replace("\\\\", "\\")


_DEFS = {}


def build_defs(source=SOURCE):
    """(release defs, profile defs): the macros ProjectDefines.h leaves defined for each build (cpp_conditions runs the header). Cached."""
    if source not in _DEFS:
        # source == "" (CRYENGINE_SOURCE unset): no header, no macros
        path = os.path.join(source, "Code", "CryEngine", "CryCommon", "CryCore", "Project", "ProjectDefines.h") if source else ""
        text = open(path, encoding="utf-8", errors="replace").read() if path and os.path.exists(path) else ""
        _DEFS[source] = (cpp.defines_from(text, True), cpp.defines_from(text, False))
    return _DEFS[source]


def release_state(terms, defs):
    """Where the conditions put the line: 'present' (compiled into both, or unconditional), 'absent' (Profile only: not in a Release build), 'release_only',
    'neither', or None when either build cannot be decided. For a variable the editor HAS, 'release_only'/'neither' cannot be true (the editor is a Profile
    build): build() turns them into 'contradiction', which is the control on this evaluator."""
    if not terms:
        return "present"                          # no condition at all: compiled into every build
    rel, prof = cpp.build_state(terms, defs[0]), cpp.build_state(terms, defs[1])
    if prof is False:
        return "neither" if rel is False else ("release_only" if rel is True else None)
    if rel is False:
        return "absent" if prof is True else None
    if rel is True and prof is True:
        return "present"
    return None


def scan_text(text, path="", defs=None):
    """Every registration in one file's text -> [{name, kind, macro, default, flags_text, vf, help, file, line, release_cond, dev_only}]."""
    lines = text.splitlines()
    defs = defs or build_defs()
    conds = cpp.condition_terms(lines)
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln) + 1)
    found = []
    for rx, table in ((_MACRO_RE, MACROS), (_METHOD_RE, METHODS)):
        for m in rx.finditer(text):
            macro = m.group(1)
            line_no = text.count("\n", 0, m.start())          # 0-based
            code_prefix = lines[line_no][:m.start() - starts[line_no]] if line_no < len(lines) else ""
            if "//" in code_prefix or re.match(r"^\s*#\s*define\b", lines[line_no] if line_no < len(lines) else ""):
                continue                                       # commented out, or the macro's own definition
            args, _end = split_args(text, m.end())
            if args is None:
                continue
            kind, roles = table[macro]
            rec = dict(zip(roles, args))
            if "name" in rec:
                name = literal(rec["name"])
            elif "ident" in rec:
                name = literal(rec["ident"]) or rec["ident"].strip("() &")
            else:
                name = None
            if not name or not re.match(r"^[A-Za-z_][\w.]*$", name):
                continue
            flags_text = rec.get("flags", "")
            found.append({
                "name": name, "kind": kind, "macro": macro,
                "default": rec.get("default"), "flags_text": flags_text, "vf": sorted(set(re.findall(r"VF_(\w+)", flags_text))),
                "help": literal(rec.get("help", "")) if "help" in rec else None,
                "file": path, "line": line_no + 1,
                "release_cond": release_state(conds[line_no], defs) if line_no < len(conds) else None,
                "cond_text": " && ".join(("" if want else "!") + "(" + expr + ")" for expr, want in (conds[line_no] if line_no < len(conds) else []))[:200],
                "dev_only": macro.endswith("_DEV_ONLY"), "dedi_only": macro.endswith("_DEDI_ONLY"),
            })
    return found


def _walk_dirs(roots, source, extra):
    for rel in roots or SOURCE_DIRS:
        yield source, os.path.join(source, rel.replace("/", os.sep))
    for d in extra:
        yield PROJECT, d


def scan_source(roots=None, source=SOURCE, extra=None):
    """Walk the source directories (read only) and collect every registration. Returns (records, files_scanned)."""
    recs, nfiles = [], 0
    if not source:   # CRYENGINE_SOURCE unset: nothing to scan
        return recs, nfiles
    for anchor, base in _walk_dirs(roots, source, PROJECT_DIRS if extra is None else extra):
        for root, _dirs, files in os.walk(base):
            for f in files:
                if not f.endswith((".cpp", ".h", ".inl", ".hpp")):
                    continue
                full = os.path.join(root, f)
                try:
                    with open(full, encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except OSError:
                    continue
                if "REGISTER_" not in text and "DefineConst" not in text and "Register" not in text and "AddCommand" not in text:
                    continue
                nfiles += 1
                recs.extend(scan_text(text, os.path.relpath(full, anchor).replace("\\", "/")))
    return recs, nfiles


def find_mentions(names, roots=None, source=SOURCE, extra=None):
    """For names with no registration the scan understood (a name held in a string constant, or registered by a form
    not in the table): the first place its quoted name appears. -> {lowercase name: 'file:line'}. Weaker than a registration: marked 'mention'."""
    if not names or not source:   # no names, or CRYENGINE_SOURCE unset
        return {}
    rx = re.compile(r'"(' + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r')"', re.I)
    out = {}
    for anchor, base in _walk_dirs(roots, source, PROJECT_DIRS if extra is None else extra):
        for root, _dirs, files in os.walk(base):
            for f in files:
                if not f.endswith((".cpp", ".h", ".inl", ".hpp")):
                    continue
                full = os.path.join(root, f)
                try:
                    with open(full, encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except OSError:
                    continue
                for m in rx.finditer(text):
                    key = m.group(1).lower()
                    if key not in out:
                        out[key] = "%s:%d" % (os.path.relpath(full, anchor).replace("\\", "/"), text.count(NL, 0, m.start()) + 1)
    return out


# --------------------------------------------------------------------------- what may be changed where

BLOCKING = ("CHEAT", "CONST_CVAR", "READONLY", "DEPRECATED")


def settable(flags, devonly=False, dedionly=False):
    """Can the value be changed from the console, a .cfg or game code? Follows CXConsole::OnBeforeVarChange / LoadConfigVar:
    a variable that is cheat, const, read-only or deprecated is refused unless (not deprecated) and (the system is in DEV MODE or
    this is the editor); CVar groups (sys_spec) are privileged. Dev mode is on in non-Release builds outside the editor unless
    -nodevmode is given, and off in a _RELEASE build (SystemInit.cpp). *_DEV_ONLY registrations do not exist in a Release build.
    Returns {editor, profile, release}: 'yes' | 'no' | 'absent', and `why`."""
    blocked = [f for f in BLOCKING if f in flags]
    why = []
    if blocked:
        why.append("flagged " + "+".join(b.lower().replace("_cvar", "") for b in blocked))
    out = {}
    out["editor"] = "no" if "DEPRECATED" in flags else "yes"
    out["profile"] = "no" if "DEPRECATED" in flags else "yes"
    if devonly or "DEV_ONLY" in flags:
        out["release"] = "absent"
        why.append("registered only in non-Release builds (_DEV_ONLY)")
    elif dedionly or "DEDI_ONLY" in flags:
        out["release"] = "absent"
        why.append("registered only in non-Release or dedicated-server builds")
    else:
        out["release"] = "no" if blocked else "yes"
    out["why"] = "; ".join(why)
    # HOW a value can reach a Release game (as measured on a packaged build with cvar_probe): code that calls ICVar::Set and %USER%/game.cfg are gated only by the flags;
    # the command line (+var value) and user.cfg go through the compile-time whitelist (CVARS_WHITELIST), which is empty unless the build adds entries, so they are ignored.
    out["release_ways"] = ["game code (ICVar::Set)", "user/game.cfg"] if out["release"] == "yes" else []
    return out


# --------------------------------------------------------------------------- spec groups (Config/CVarGroups in engine.pak)

ENGINE = os.environ.get("CRYENGINE_ENGINE", os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1"))


def parse_group(text):
    """One CVarGroups .cfg -> {default_level, sections: {'default'|'1'|'2'...: {cvar: value}}}.
    Format (XConsoleVariable.cpp): [default] holds every key with its default and a first line '= N' (the group's own default level);
    [1], [2] ... hold the overrides for that spec level. Effective values = [default] overlaid by [level]."""
    sections, cur, default_level = {}, None, None
    for raw in text.splitlines():
        line = raw.split(";")[0].strip()
        if not line:
            continue
        m = re.match(r"^\[(\w+)\]$", line)
        if m:
            cur = sections.setdefault(m.group(1).lower(), {})
        elif cur is not None and line.startswith("="):
            default_level = int(re.match(r"=\s*(\d+)", line).group(1))
        elif cur is not None and "=" in line:
            key, _eq, value = line.partition("=")
            cur[key.strip()] = re.split(r"\s+--", value)[0].strip()      # a trailing '-- comment' is not part of the value
    return {"default_level": default_level, "sections": sections}


def spec_groups(engine=ENGINE):
    """Every sys_spec_* group of the installed engine: {group name: parse_group(...)}. Read straight from engine.pak."""
    import crypak
    pak = os.path.join(engine, "engine", "engine.pak")
    out = {}
    for ent in crypak.entries(pak):
        name = ent["name"].replace("\\", "/")
        m = re.match(r"^Config/CVarGroups/(\w+)\.cfg$", name, re.I)
        if m:
            ent = dict(ent, pak=pak)
            out[m.group(1)] = parse_group(crypak.read(ent).decode("utf-8", "replace"))
    return out


def group_values(groups, group, level):
    """The values a spec group applies at `level` (1 low, 2 medium, 3 high, 4 very high): [default] overlaid by [level]."""
    g = groups.get(group)
    if not g:
        raise KeyError("no group %r: %s" % (group, ", ".join(sorted(groups))))
    values = dict(g["sections"].get("default", {}))
    values.update(g["sections"].get(str(level).lower(), {}))
    return values


# --------------------------------------------------------------------------- build and query

def pick_registration(srcs, kind):
    """A name can be registered in several places (an `#if _RELEASE ... #else ...` pair, one per platform). -> (the registration the Profile editor compiled, the
    state of the name in a Release build): 'present' if ANY registration is compiled into Release, 'absent' if every one is Profile-only, None if undecided,
    'contradiction' if none would be compiled into the Profile editor that has it (the evaluator is wrong about something)."""
    mine = [s for s in srcs if s["kind"] == kind]
    if not mine:
        return None, None
    in_profile = [s for s in mine if s["release_cond"] not in ("release_only", "neither")]
    main = in_profile[0] if in_profile else mine[0]
    states = [s["release_cond"] for s in mine]
    if not in_profile:
        return main, "contradiction"
    if any(st in ("present", "release_only") for st in states):
        return main, "present"
    if all(st == "absent" for st in states):
        return main, "absent"
    return main, None


def build(data_dir=DATA, source=SOURCE, roots=None, engine=ENGINE):
    """raw dumps + source scan -> engine_data/cvars.json. Returns the numbers that say how good the join is."""
    dump_path = os.path.join(data_dir, "consolecommandsandvars.txt")
    flag_path = os.path.join(data_dir, "cvar_flags.txt")
    if not (os.path.exists(dump_path) and os.path.exists(flag_path)):
        raise RuntimeError("no raw dumps in %s: run  python catalogs/cvar_catalog.py --capture  with the editor open" % data_dir)
    with open(dump_path, encoding="utf-8", errors="replace") as f:
        variables, commands = parse_dump(f.read())
    audit = {}
    with open(flag_path, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if parts:
                audit[parts[0]] = set(parts[1:])
    recs, nfiles = scan_source(roots, source)
    by_name = {}
    for r in recs:                                 # a name can be registered in several places (per platform): keep all, first is the main one
        by_name.setdefault(r["name"].lower(), []).append(r)

    entries = []
    for name, v in variables.items():
        flags = set(v["flags"]) | audit.get(name, set())
        srcs = by_name.get(name.lower(), [])
        src, rstate = pick_registration(srcs, "cvar")
        entry = {"name": name, "kind": "cvar", "type": v["type"], "current": v["current"], "help": v["help"], "flags": sorted(flags),
                 "default": None, "source": None, "also_in": [], "release_condition": None, "condition_text": ""}
        if src:
            entry["default"] = src["default"]
            entry["source"] = "%s:%d" % (src["file"], src["line"])
            entry["also_in"] = ["%s:%d" % (s["file"], s["line"]) for s in srcs if s is not src][:4]
            entry["release_condition"] = rstate
            entry["condition_text"] = src["cond_text"]
            if not v["help"] and src["help"]:
                entry["help"] = src["help"]
        entry["settable"] = settable(flags, devonly=bool(src and src["dev_only"]), dedionly=bool(src and src["dedi_only"]))
        if src and rstate == "absent" and entry["settable"]["release"] != "absent":
            entry["settable"]["release"] = "absent"
            entry["settable"]["release_ways"] = []
            entry["settable"]["why"] = (entry["settable"]["why"] + "; " if entry["settable"]["why"] else "") + "registered under #if %s: not compiled into Release" % src["cond_text"]
        entries.append(entry)
    for name, c in commands.items():
        src, rstate = pick_registration(by_name.get(name.lower(), []), "command")
        vf = set(src["vf"]) if src else set()
        entry = {"name": name, "kind": "command", "type": "", "current": "", "help": c["help"], "flags": sorted(vf), "default": None,
                 "source": "%s:%d" % (src["file"], src["line"]) if src else None, "also_in": [], "release_condition": rstate}
        cheat = bool(vf & {"CHEAT", "CHEAT_NOCHECK", "CHEAT_ALWAYS_CHECK"})
        entry["settable"] = {"editor": "yes", "profile": "yes",
                             "release": "absent" if (src and (src["dev_only"] or rstate == "absent")) else ("no" if cheat else "yes"),
                             "why": "cheat command: refused outside the editor unless dev mode" if cheat else ""}
        entries.append(entry)

    groups = spec_groups(engine) if os.path.isdir(engine) else {}
    set_by = {}
    for gname, g in groups.items():
        for sect, kv in g["sections"].items():
            for key, value in kv.items():
                set_by.setdefault(key.lower(), {}).setdefault(gname, {})[sect] = value
    for e in entries:
        e["spec_groups"] = set_by.get(e["name"].lower(), {})
        e["is_group"] = e["name"] in groups or e["name"].lower() in {g.lower() for g in groups}
        e["source_kind"] = "registration" if e["source"] else None
    mentions = find_mentions({e["name"] for e in entries if not e["source"]}, roots, source)
    for e in entries:
        if not e["source"] and e["name"].lower() in mentions:
            e["source"], e["source_kind"] = mentions[e["name"].lower()], "mention"
    cvar_srcs = {r["name"].lower() for r in recs if r["kind"] == "cvar"}
    runtime_cvars = {e["name"].lower() for e in entries if e["kind"] == "cvar"}
    stats = {"variables": len(variables), "commands": len(commands), "registrations_in_source": len(recs), "files_scanned": nfiles,
             "source_scan_skipped": not os.path.isdir(source), "engine_specs_skipped": not os.path.isdir(engine),   # set CRYENGINE_SOURCE / CRYENGINE_ENGINE when True
             "variables_registration_found": sum(1 for e in entries if e["kind"] == "cvar" and e["source_kind"] == "registration"),
             "variables_mention_only": sum(1 for e in entries if e["kind"] == "cvar" and e["source_kind"] == "mention"),
             "variables_nothing_found": sum(1 for e in entries if e["kind"] == "cvar" and not e["source"]),
             "commands_registration_found": sum(1 for e in entries if e["kind"] == "command" and e["source_kind"] == "registration"),
             "commands_mention_only": sum(1 for e in entries if e["kind"] == "command" and e["source_kind"] == "mention"),
             "commands_nothing_found": sum(1 for e in entries if e["kind"] == "command" and not e["source"]),
             "source_names_not_in_the_editor": len(cvar_srcs - runtime_cvars),
             "release_state_absent": sum(1 for e in entries if e["release_condition"] == "absent"),
             "release_state_present": sum(1 for e in entries if e["release_condition"] == "present"),
             "release_state_undecided": sum(1 for e in entries if e["kind"] == "cvar" and e["source_kind"] == "registration" and e["release_condition"] is None),
             "evaluator_contradictions": [e["name"] for e in entries if e["release_condition"] == "contradiction"],
             "spec_groups": len(groups), "variables_set_by_a_spec_group": sum(1 for e in entries if e["spec_groups"])}
    os.makedirs(data_dir, exist_ok=True)
    out = {"generated_from": "editor dump + audit + source scan", "stats": stats, "entries": sorted(entries, key=lambda e: e["name"].lower())}
    tmp = os.path.join(data_dir, "cvars.json.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    os.replace(tmp, os.path.join(data_dir, "cvars.json"))
    return stats


_CACHE = {}


def load(path=CATALOG):
    mt = os.path.getmtime(path) if os.path.exists(path) else 0
    if _CACHE.get("path") != path or _CACHE.get("mtime") != mt:
        if not mt:
            raise RuntimeError("no catalog at %s: run  python catalogs/cvar_catalog.py --capture --build  with the editor open" % path)
        with open(path, encoding="utf-8") as f:
            _CACHE.update(path=path, mtime=mt, data=json.load(f))
    return _CACHE["data"]


def get(name, path=CATALOG):
    low = name.lower()
    for e in load(path)["entries"]:
        if e["name"].lower() == low:
            return e
    return None


def search(text, kind=None, settable_in=None, flag=None, limit=25, path=CATALOG):
    """Entries whose name or help contains every word of `text` (name matches first). settable_in: editor|profile|release -> only those
    that can be changed there; flag: a flag name such as CHEAT or DUMPTODISK."""
    words = [w for w in re.split(r"\s+", text.lower().strip()) if w]
    rows = []
    for e in load(path)["entries"]:
        if kind and e["kind"] != kind:
            continue
        if flag and flag.upper() not in e["flags"]:
            continue
        if settable_in and e["settable"].get(settable_in) != "yes":
            continue
        name, hay = e["name"].lower(), (e["name"] + " " + e["help"]).lower()
        if all(w in hay for w in words):
            rows.append((0 if all(w in name for w in words) else 1, name, e))
    rows.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in rows[:limit]], len(rows)


SAFE_PREFIXES = ("e_", "ai_", "ca_", "p_", "fg_")          # the groups where setting a value mid-game is harmless: no renderer, audio, network or save variables
UNSAFE_WORDS = re.compile(r"save|load|dump|export|pak|stream|quit|shut|reset|exec|level|debug|log|record|capture|profile|video", re.I)


def pick_probes(per_class=8, path=CATALOG):
    """A deterministic, evenly spread sample of simple variables for the `cvar_probe` job: `per_class` that the catalog predicts
    settable in Release ('yes'), `per_class` it predicts refused ('no': cheat/const/readonly) and `per_class` it predicts absent (_DEV_ONLY).
    Each comes with a value different from its current one (toggle 0/1, else +1). -> [(name, value, predicted)]"""
    classes = {"yes": [], "no": [], "absent": [], "undecided": []}
    for e in load(path)["entries"]:
        if e["kind"] != "cvar" or e["type"] not in ("int", "float") or not e["name"].startswith(SAFE_PREFIXES) or UNSAFE_WORDS.search(e["name"]):
            continue
        if "DEPRECATED" in e["flags"] or e["is_group"] or e["current"] == "" or len(e["name"]) > 60:
            continue
        try:
            cur = float(e["current"])
        except ValueError:
            continue
        pred = e["settable"]["release"]
        if e["release_condition"] is None and e["source_kind"] == "registration" and pred != "absent":
            pred = "undecided"                    # whether it exists in Release depends on a macro the evaluator cannot see: measured, not predicted
        if pred in classes:
            value = (1 if cur == 0 else 0) if cur in (0.0, 1.0) else cur + 1
            classes[pred].append((e["name"], ("%g" % value), pred))
    out = []
    for pred, items in classes.items():
        step = max(1, len(items) // max(1, per_class))
        out.extend(items[::step][:per_class])
    return out


def compare_probe(rows, path=CATALOG):
    """Measured (from the game) vs predicted (the catalog). rows: [{name, state: accepted|refused|absent}] ->
    {'agree': n, 'total': n, 'by_class': {...}, 'disagreements': [...]}"""
    want = {"yes": "accepted", "no": "refused", "absent": "absent"}
    stats = {"agree": 0, "total": 0, "by_class": {}, "disagreements": [], "undecided": {"present": [], "absent": []}}
    for r in rows:
        e = get(r["name"], path)
        if not e:
            continue
        if r.get("build", "release") == "release" and e["release_condition"] is None and e["source_kind"] == "registration" and e["settable"]["release"] != "absent":
            stats["undecided"]["absent" if r["state"] == "absent" else "present"].append(r["name"])      # not compared: the catalog does not claim anything here
            continue
        pred = e["settable"]["release"] if r.get("build", "release") == "release" else ("accepted" if e["settable"]["profile"] == "yes" else "refused")
        expected = want.get(pred, pred)
        cls = stats["by_class"].setdefault(expected, [0, 0])
        cls[1] += 1
        stats["total"] += 1
        if r["state"] == expected:
            stats["agree"] += 1
            cls[0] += 1
        else:
            stats["disagreements"].append({"name": r["name"], "flags": e["flags"], "predicted": expected, "measured": r["state"], "before": r.get("before"), "after": r.get("after")})
    return stats


def main():
    args = sys.argv[1:]
    if "--capture" in args:
        print(capture())
    if "--build" in args:
        print(json.dumps(build(), indent=1))
    rest = [a for a in args if not a.startswith("--")]
    if rest:
        rows, total = search(" ".join(rest), limit=15)
        for e in rows:
            print("%-34s %-8s default=%-10s now=%-8s %s" % (e["name"], e["type"] or e["kind"], str(e["default"])[:10], e["current"][:8], ",".join(e["flags"])))
            print("      release: %-6s  %s" % (e["settable"]["release"], e["settable"]["why"]))
        print("%d of %d shown" % (len(rows), total))


if __name__ == "__main__":
    main()
