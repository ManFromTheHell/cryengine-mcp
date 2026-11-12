"""Game token libraries of a level, checked by the loader's own rules (CryFlowGraph/GameTokens/GameTokenSystem.cpp `_InternalLoadLibrary`).

    python checks/gametoken_check.py [level]        # default: ashvale

The engine loads `<level>/GameTokens/*.xml` at level load (CryAction/LevelSystem.cpp `LoadLibs`). The loader is silent about most mistakes (a GameWarning in the log, the token is
simply not there), so a flow graph or a DRS response that names it reads nothing and nothing says why. Rules, all read from the loader:
  - the root tag is exactly `GameTokensLibrary` and has a `Name` (else the whole file is refused);
  - every child has `Name`, `Type` (exactly Int, Float, EntityId, Vec3, String or Bool: case-sensitive; Void, Any or anything else is skipped), `Value` (converted to the type), `LocalOnly`;
  - the token is `<LibName>.<Name>`; one that exists already is NOT overwritten (a duplicate inside a library or across libraries keeps the FIRST).
References are collected from the level's layers (flow graph node inputs `gametoken_Token`, `Token`) and from the DRS files (`"Token": "Lib.Name"` of a GameToken condition / SetGameToken action) and
reported when they name a token no library of the level defines.

Findings: {"level", "libraries": {lib: [tokens]}, "findings": [{"id", "level": error|warn, "where", "message"}]}. Offline: no editor needed.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
TYPES = ("Int", "Float", "EntityId", "Vec3", "String", "Bool")


def _value_problem(kind, value):
    if kind in ("Int", "EntityId"):
        return None if re.fullmatch(r"\s*[-+]?\d+\s*", value or "") else "'%s' is not an integer (atoi would read it as 0 or a prefix)" % value
    if kind == "Float":
        return None if re.fullmatch(r"\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\s*", value or "") else "'%s' is not a number" % value
    if kind == "Vec3":
        return None if re.fullmatch(r"\s*[-+0-9.eE]+\s*,\s*[-+0-9.eE]+\s*,\s*[-+0-9.eE]+\s*", value or "") else "'%s' is not 'x,y,z'" % value
    if kind == "Bool":
        return None if (value or "").strip().lower() in ("0", "1", "true", "false") else "'%s' is not 0/1/true/false" % value
    return None


def check_library_dir(folder, rel_root=""):
    """-> (libraries {libname: [token names]}, findings) for every *.xml in one GameTokens folder"""
    libs, findings, seen = {}, [], {}
    for path in sorted(glob.glob(os.path.join(folder, "*.xml"))):
        where = os.path.relpath(path, rel_root or folder).replace("\\", "/")
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            findings.append({"id": "G001", "level": "error", "where": where, "message": "not well-formed XML: %s (the loader: 'Unable to load game token database')" % exc})
            continue
        if root.tag != "GameTokensLibrary":
            findings.append({"id": "G002", "level": "error", "where": where, "message": "root tag is '%s', the loader needs exactly 'GameTokensLibrary' ('Not a game tokens library')" % root.tag})
            continue
        lib = root.get("Name")
        if not lib:
            findings.append({"id": "G003", "level": "error", "where": where, "message": "the library has no Name attribute: the whole file is refused"})
            continue
        if " " in lib:
            findings.append({"id": "G004", "level": "warn", "where": where, "message": "library name '%s' contains a space (flow graph token fields and DRS conditions split on it)" % lib})
        names = libs.setdefault(lib, [])
        for node in root:
            name, kind, value = node.get("Name"), node.get("Type"), node.get("Value")
            here = "%s: %s" % (where, name or "<no name>")
            if not name:
                findings.append({"id": "G005", "level": "error", "where": where, "message": "a token without a Name attribute (<%s>)" % node.tag})
                continue
            if " " in name:
                findings.append({"id": "G004", "level": "warn", "where": here, "message": "token name contains a space"})
            if kind not in TYPES:
                findings.append({"id": "G006", "level": "error", "where": here, "message": "Type '%s' is not one of %s (case-sensitive): the loader skips this token with 'Unsupported type'" % (kind, ", ".join(TYPES))})
                continue
            if value is None:
                findings.append({"id": "G007", "level": "warn", "where": here, "message": "no Value attribute: the token starts at its default"})
            else:
                bad = _value_problem(kind, value)
                if bad:
                    findings.append({"id": "G008", "level": "warn", "where": here, "message": "Value " + bad})
            full = "%s.%s" % (lib, name)
            if full in seen:
                findings.append({"id": "G009", "level": "warn", "where": here, "message": "'%s' is already defined in %s: the loader keeps the first and ignores this one" % (full, seen[full])})
                continue
            seen[full] = where
            names.append(name)
    return libs, findings


REF_PATTERNS = (
    re.compile(r'gametoken_Token="([^"]*)"'),
    re.compile(r'<GameTokenSet[^>]*\bToken="([^"]*)"'),
    re.compile(r'"Token"\s*:\s*"([^"]*)"'),
)


def references(level_dir, drs_dir=None):
    """-> {full token name: [places]} named by the level's layers and by the DRS files"""
    refs = {}
    files = glob.glob(os.path.join(level_dir, "layers", "*.lyr")) + glob.glob(os.path.join(level_dir, "*.lyr"))
    if drs_dir and os.path.isdir(drs_dir):
        files += glob.glob(os.path.join(drs_dir, "**", "*.response"), recursive=True)
    for path in files:
        try:
            text = open(path, encoding="utf8", errors="replace").read()
        except OSError:
            continue
        for pat in REF_PATTERNS:
            for m in pat.finditer(text):
                token = m.group(1).strip()
                if token:
                    refs.setdefault(token, []).append(os.path.basename(path))
    return refs


def check(level="ashvale", project=PROJECT):
    level_dir = os.path.join(project, "Assets", "levels", level)
    if not os.path.isdir(level_dir):
        return {"level": level, "error": "no level folder %s" % level_dir}
    folder = os.path.join(level_dir, "GameTokens")
    libs, findings = ({}, [])
    if os.path.isdir(folder) and glob.glob(os.path.join(folder, "*.xml")):
        libs, findings = check_library_dir(folder, level_dir)
        findings.append({"id": "G011", "level": "warn", "where": "Assets/levels/%s/GameTokens" % level,
                         "message": "a loose GameTokens folder is packaged into GameData.pak, which a RELEASE game does not read while a level loads (the level loader finds no tokens there, a later manual LoadLibs finds them; the Profile build loads them): "
                                    "make the libraries in the editor's database (bridge.db.add_library / import_library gametoken, save_library) so the export writes them into the level's own level.pak"})
    # what the exporter wrote into level.pak: the editor's gametoken database, as <level>/GameTokens/<library>.xml (lower-case in the pack)
    packed = {}
    pak = os.path.join(level_dir, "level.pak")
    if os.path.isfile(pak):
        import tempfile
        import zipfile
        try:
            with zipfile.ZipFile(pak) as z:
                with tempfile.TemporaryDirectory() as tmp:
                    for n in z.namelist():
                        if n.lower().startswith("gametokens/") and n.lower().endswith(".xml"):
                            z.extract(n, tmp)
                    sub = os.path.join(tmp, "gametokens")
                    if os.path.isdir(sub):
                        plibs, pfind = check_library_dir(sub, tmp)
                        packed = plibs
                        for f in pfind:
                            f["where"] = "level.pak " + f["where"]
                        findings.extend(pfind)
        except zipfile.BadZipFile:
            findings.append({"id": "G001", "level": "error", "where": "level.pak", "message": "level.pak is not a readable zip"})
    for lib, names in packed.items():
        libs.setdefault(lib, [])
        libs[lib] = sorted(set(libs[lib]) | set(names))
    defined = {"%s.%s" % (lib, n) for lib, names in libs.items() for n in names}
    refs = references(level_dir, os.path.join(project, "Assets", "libs", "DynamicResponseSystem"))
    for token, places in sorted(refs.items()):
        if token not in defined:
            findings.append({"id": "G010", "level": "warn", "where": ", ".join(sorted(set(places))[:3]),
                             "message": "names token '%s' that no library of the level defines: it reads nothing (the loader does not say so)" % token})
    return {"level": level, "folder": os.path.relpath(folder, project).replace("\\", "/"), "folder_exists": os.path.isdir(folder),
            "libraries": libs, "in_level_pak": packed, "references": {t: len(p) for t, p in refs.items()}, "findings": findings,
            "errors": sum(1 for f in findings if f["level"] == "error"), "warnings": sum(1 for f in findings if f["level"] == "warn")}


if __name__ == "__main__":
    out = check(sys.argv[1] if len(sys.argv) > 1 else "ashvale")
    print(json.dumps(out, indent=1))
    sys.exit(1 if out.get("error") or out.get("errors") else 0)
