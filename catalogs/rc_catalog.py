#!/usr/bin/env python3
"""The Resource Compiler as the engine documents it (ENGINE_LEARNING_PLAN B1): its texture PRESETS (rc.ini), its command-line OPTIONS (rc.exe /help, 800 lines) and the file types it converts.

Nothing here is guessed or copied by hand: the presets are read from Tools/rc/rc.ini (the file RC itself loads), the options and converters from RC's own `/help` output (run from Python: Git Bash rewrites
`/help` into a Windows path). The manual (System Utilities: Resource Compiler, Presets) says a preset is chosen by the file name mask: `filemasks=*_diff*` ...; a preset can also be named in the TIFF
(CryTIF stores `/preset=<name>`) or on the command line (`/preset=`).

    python catalogs/rc_catalog.py presets [text]
    python catalogs/rc_catalog.py options [text]
    python catalogs/rc_catalog.py for <file name>        which presets RC may pick for it
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import fnmatch
import json
import os
import re
import subprocess
import sys

HERE = _paths.ROOT
DATA = os.path.join(HERE, "engine_data")
# CRYENGINE_ENGINE_DIR (the installed engine folder) overrides the Launcher's default 5.7.1 location.
RC_DIR = os.path.join(os.environ.get("CRYENGINE_ENGINE_DIR") or os.path.expandvars(
    r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1"), "Tools", "rc")
RC_EXE = os.path.join(RC_DIR, "rc.exe")
RC_INI = os.path.join(RC_DIR, "rc.ini")
OPTIONS_FILE = os.path.join(DATA, "rc_options.json")

NOT_PRESETS = {"_platform", "_presetaliases"}


class RcError(RuntimeError):
    pass


def parse_ini(path=None):
    """-> {"globals": {key: value}, "platforms": [{...}], "aliases": {old: new}, "presets": {name: {"options": {key: value}, "description": text, "line": n}}}. The description is the comment
    lines directly above the section."""
    path = path or RC_INI
    if not os.path.isfile(path):
        raise RcError("no rc.ini at %s" % path)
    out = {"globals": {}, "platforms": [], "aliases": {}, "presets": {}}
    section, comment, cur = None, [], None
    for n, raw in enumerate(open(path, encoding="utf-8", errors="replace").read().splitlines(), 1):
        line = raw.strip()
        if not line:
            comment = []
            continue
        if line.startswith(";"):
            text = line.lstrip(";").strip()
            if text and not set(text) <= set(";"):
                comment.append(text)
            continue
        m = re.match(r"^\[([^\]]+)\]$", line)
        if m:
            section = m.group(1)
            if section.lower() == "_platform":
                cur = {}
                out["platforms"].append(cur)
            elif section.lower() == "_presetaliases":
                cur = None
            else:
                cur = {"options": {}, "description": " ".join(comment), "line": n}
                out["presets"][section] = cur
            comment = []
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if section is None:
                out["globals"][k] = v
            elif section.lower() == "_platform":
                cur[k] = v
            elif section.lower() == "_presetaliases":
                out["aliases"][k] = v
            else:
                cur["options"][k] = v
    return out


def presets(text="", ini=None):
    """[{name, description, pixelformat, filemasks, options}] whose name, description or an option contains `text`."""
    ini = ini or parse_ini()
    t = (text or "").lower()
    out = []
    for name, p in ini["presets"].items():
        o = p["options"]
        hay = (name + " " + p["description"] + " " + " ".join("%s=%s" % kv for kv in o.items())).lower()
        if t in hay:
            out.append({"name": name, "description": p["description"], "pixelformat": o.get("pixelformat", ""), "pixelformatalpha": o.get("pixelformatalpha", ""), "filemasks": [m for m in o.get("filemasks", "").replace(",", ";").split(";") if m.strip()],
                        "mipmaps": o.get("mipmaps", ""), "colorspace": o.get("colorspace", ""), "options": o})
    return out


def for_file(file_name, ini=None):
    """The presets whose filemasks match the file name (case-insensitive), in rc.ini order: RC picks among these by what the image holds (an alpha channel makes Albedo into AlbedoWithOpacity ...: the manual,
    'Automatic Adjustments'). Also the aliases: an old preset name saved in a TIFF is converted to its current name."""
    ini = ini or parse_ini()
    base = os.path.basename(file_name).lower()
    hits = []
    for name, p in ini["presets"].items():
        masks = [m.strip().lower() for m in p["options"].get("filemasks", "").replace(",", ";").split(";") if m.strip()]
        if any(fnmatch.fnmatch(base, m) for m in masks):
            hits.append({"preset": name, "pixelformat": p["options"].get("pixelformat", ""), "description": p["description"]})
    return hits


def resolve_alias(name, ini=None):
    ini = ini or parse_ini()
    return ini["aliases"].get(name, name)


# ---------------------------------------------------------------- rc.exe /help


def run_help():
    """The text rc.exe /help prints (about 800 lines)."""
    if not os.path.isfile(RC_EXE):
        raise RcError("no rc.exe at %s" % RC_EXE)
    try:
        r = subprocess.run([RC_EXE, "/help"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=90, cwd=RC_DIR)
    except subprocess.SubprocessError as exc:
        raise RcError("rc.exe /help failed: %s" % exc)
    return r.stdout


def parse_help(text):
    """-> {"version": str, "converters": [{name, extensions}], "options": [{key, applies_to: [tags], description}]}. An option is a line with five spaces then /key; its description is the indented lines after it;
    a leading [TIF] / [CGF] ... tag says which compiler reads it."""
    version = re.search(r"Version ([0-9.]+)", text)
    conv = [{"name": m.group(1), "extensions": re.findall(r'"([^"]+)"', m.group(2))} for m in re.finditer(r"Registered (\w+) \(((?:\"[^\"]+\"(?:, )?)+)\)", text)]
    options = []
    cur = None
    for line in text.splitlines():
        m = re.match(r"^\s{5}(/[A-Za-z_][A-Za-z0-9_]*)\s*$", line)
        if m:
            cur = {"key": m.group(1), "lines": []}
            options.append(cur)
            continue
        if cur is not None:
            if line.strip() and re.match(r"^\s{10}", line):
                cur["lines"].append(line.strip())
            elif line.strip() and not re.match(r"^\s{5}/", line):
                cur = None
    out = []
    for o in options:
        desc = " ".join(o["lines"])
        tags = re.findall(r"\[([A-Za-z0-9 ,]+)\]", desc)
        out.append({"key": o["key"], "applies_to": sorted({x.strip() for t in tags for x in t.split(",")}), "description": desc})
    return {"version": version.group(1) if version else "", "converters": conv, "options": out}


def options(text="", refresh=False):
    """{version, converters, options: [{key, applies_to, description}]}: filtered by `text` (key or description). Cached in engine_data/rc_options.json."""
    if not refresh and os.path.isfile(OPTIONS_FILE):
        data = json.load(open(OPTIONS_FILE, encoding="utf-8"))
    else:
        data = parse_help(run_help())
        os.makedirs(DATA, exist_ok=True)
        json.dump(data, open(OPTIONS_FILE, "w", encoding="utf-8"), indent=1)
    t = (text or "").lower()
    if t:
        data = dict(data, options=[o for o in data["options"] if t in o["key"].lower() or t in o["description"].lower()])
    return data


def main():
    a = sys.argv[1:]
    if not a or a[0] == "presets":
        for p in presets(a[1] if len(a) > 1 else ""):
            print("%-44s %-8s %-18s %s" % (p["name"], p["pixelformat"], ";".join(p["filemasks"]), p["description"][:60]))
    elif a[0] == "options":
        d = options(a[1] if len(a) > 1 else "")
        print("RC %s: %d converters, %d options" % (d["version"], len(d["converters"]), len(d["options"])))
        for o in d["options"][:60]:
            print("%-26s %-10s %s" % (o["key"], ",".join(o["applies_to"]), o["description"][:90]))
    elif a[0] == "for" and len(a) > 1:
        for h in for_file(a[1]):
            print("%-40s %-8s %s" % (h["preset"], h["pixelformat"], h["description"][:70]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
