#!/usr/bin/env python3
"""Materials as the ENGINE resolves them (ENGINE_LEARNING_PLAN B3/B4), from the open editor, without running the game.

The plugin verb `bridge.material.check` loads each .mtl through the engine's own material manager (I3DEngine::GetMaterialManager()->LoadMaterial) and writes what the engine ended up with:
for the material, or for every sub-material, the surface type it resolved (an undefined name becomes mat_default), the shader it loaded and that shader's generation mask, every texture slot with
the file asked for and the texture the engine bound, and the log lines the load wrote. This module compares that with the .mtl file:

  M001 the engine cannot load the material (or returned its default material)       M005 the material has a different number of sub-materials in the engine
  M002 the SurfaceType in the file is not the one the engine uses (undefined name)   M006 a texture in the file is not bound, or the engine bound another one
  M003 the shader the engine loaded is not the one the file names                    M007 the load wrote an error or a warning
  M004 a flag named in StringGenMask is valid and the engine's final generation mask does not carry it (dropped)
  M009 a UI element drawn as a texture (Name.ui) is not defined in libs/ui/UIElements (the renderer asks the UI database; the engine binds no file at load time, so M006/M008 skip these)
  M008 a texture the file names does not exist in the project (the engine keeps a stand-in texture object of the asked-for name for a streamed texture, so the file is what shows it)

Measured with copies of one material in the editor: the engine ORs bits in that no StringGenMask asks for. For Illum NORMAL_MAP (0x20) follows from a normal-map texture
(Illum.ext: DependencySet = $TEX_Normals) and SUBSURFACE_SCATTERING is on regardless, so the string's own %NORMAL_MAP / %SUBSURFACE_SCATTERING / %BUMP_MAP change nothing;
%PARALLAX_OCCLUSION_MAPPING does change the mask; a numeric GenMask counts only when StringGenMask is ABSENT (an empty one wins), and then its bits are OR-ed in as they are
(minus those the shader drops: 80000081 gave only 0x80000000). The engine keeps the NAME of a shader it could not load; only its log line ('Failed to load shader') says so, and a
log line exists only for the FIRST load of a material in a session (the editor must be restarted for a second look; material_load_logs.json remembers what earlier runs saw).

    python checks/material_check.py [materials/ashvale/cloth.mtl ...]     default: every .mtl under Assets
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
DATA = os.path.join(HERE, "engine_data")
SCRATCH = os.path.join(DATA, "material_scratch")

sys.path.insert(0, HERE)


class MaterialError(RuntimeError):
    pass


def _slash(p):
    return p.replace(chr(92), "/")


def project_materials():
    """Every .mtl under Assets as a path relative to Assets (forward slashes)."""
    out = []
    for root, _, files in os.walk(ASSETS):
        for f in files:
            if f.lower().endswith(".mtl"):
                out.append(_slash(os.path.relpath(os.path.join(root, f), ASSETS)))
    return sorted(out)


def file_subs(path):
    """What the .mtl FILE says, per slot (a single material is slot -1): [{slot, name, shader, surface, string_gen, gen, textures: [file]}]."""
    root = ET.parse(os.path.join(ASSETS, path.replace("/", os.sep))).getroot()
    subs = root.find("SubMaterials")
    els = [(i, m) for i, m in enumerate(subs.findall("Material"))] if subs is not None else [(-1, root)]
    out = []
    for slot, el in els:
        tex = [t.get("File") for t in (el.find("Textures").findall("Texture") if el.find("Textures") is not None else []) if t.get("File")]
        out.append({"slot": slot, "name": el.get("Name") or "", "shader": (el.get("Shader") or "").strip(), "surface": (el.get("SurfaceType") or "").strip(),
                    "string_gen": el.get("StringGenMask"), "gen": el.get("GenMask"), "textures": tex})
    return out


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


NOISE = re.compile(r"error|fail|not found|cannot|can't|missing|warning|invalid|unknown|undefined", re.I)


def _tex_key(p):
    """A texture path reduced to what two spellings of the same file share: lower case, forward slashes, no extension, no leading folder alias."""
    p = _slash((p or "").strip().lower())
    p = re.sub(r"^(\./|/)", "", p)
    return os.path.splitext(p)[0]


def compare(row, fsubs, resolve=None, exists=None):
    """One engine row against the file's description. resolve(shader) -> ({FLAG: bit}, uses the common table) for M004; exists(path) -> bool for M008. -> [finding]"""
    where = row["file"]
    findings = []
    if not row.get("found"):
        return [_finding("error", "M001", where, "the engine cannot load this material (not found or not a material file)", "check the path and that the file is valid XML")]
    if row.get("is_default") and fsubs:
        return [_finding("error", "M001", where, "the engine returned its DEFAULT material for this file: the file did not load as a material", "check the file in the Material Editor")]
    eng = {s["slot"]: s for s in row.get("subs", [])}
    if len(eng) != len(fsubs):
        findings.append(_finding("error", "M005", where, "the file has %d (sub-)material%s and the engine has %d" % (len(fsubs), "" if len(fsubs) == 1 else "s", len(eng))))
    explained = set()
    for fs in fsubs:
        label = "%s%s" % (where, "" if fs["slot"] < 0 else " [%d %s]" % (fs["slot"], fs["name"]))
        es = eng.get(fs["slot"])
        if es is None or es.get("missing"):
            findings.append(_finding("error", "M005", label, "this sub-material is not in the engine's material"))
            continue
        if fs["surface"] and fs["surface"].lower() != (es.get("surface") or "").lower():
            explained.add(fs["surface"].lower())
            findings.append(_finding("warning", "M002", label, "SurfaceType '%s' in the file, the engine uses '%s' (an undefined name becomes mat_default)" % (fs["surface"], es.get("surface")),
                                     "define it in Libs/MaterialEffects/SurfaceTypes.xml or use a defined one"))
        if fs["shader"] and fs["shader"].split(".")[0].lower() != (es.get("shader") or "").split(".")[0].lower():
            findings.append(_finding("error", "M003", label, "the file names shader '%s' and the engine loaded '%s'" % (fs["shader"], es.get("shader")), "check the shader name (shader_search)"))
        bound = {_tex_key(t["file"]): t for t in es.get("textures", [])}
        for f in fs["textures"]:
            if f.lower().endswith(".ui") and "/" not in f.replace("\\", "/"):
                # a UI element drawn as a texture ('PgBoard.ui', TexType Dynamic 2D-Map): not a file, the renderer asks the UI database; the engine binds nothing at load time.
                # Only the element's existence is checked (exists() knows .ui names), the engine's own warning "UI-Element identifier ... can't be found" says the same at level validate.
                if exists is not None and not exists(f):
                    findings.append(_finding("warning", "M009", label, "UI element '%s' is not defined in libs/ui/UIElements/*.xml (the material would draw nothing)" % f[:-3]))
                continue
            t = bound.get(_tex_key(f))
            substituted = t is not None and t["bound"] and t["bound_file"] and _tex_key(t["bound_file"]) != _tex_key(f) and _tex_key(os.path.basename(t["bound_file"])) != _tex_key(os.path.basename(f))
            if exists is not None and not substituted and not exists(f):
                # a streamed texture (the diffuse map, for one) that is missing is NOT swapped for a placeholder by name: the engine keeps a texture object of the asked-for name with a 128x128 stand-in
                findings.append(_finding("warning", "M008", label, "texture '%s' does not exist in the project (the engine keeps a stand-in of the asked-for name for a streamed texture, so only the file shows it)" % f,
                                         "import the texture, or point the material at one that exists (lint L003 checks only Assets/materials)"))
            if t is None:
                findings.append(_finding("warning", "M006", label, "texture '%s' is in the file and the engine has no texture slot for it (the shader does not offer that slot, or the Map name is unknown)" % f))
            elif not t["bound"]:
                findings.append(_finding("error", "M006", label, "texture '%s' (slot %s) is not bound by the engine" % (f, t["slot"])))
            elif t["bound_file"] and _tex_key(t["bound_file"]) != _tex_key(f) and _tex_key(os.path.basename(t["bound_file"])) != _tex_key(os.path.basename(f)):
                findings.append(_finding("error", "M006", label, "texture '%s' (slot %s): the engine bound '%s' instead (a placeholder when the file is missing)" % (f, t["slot"], t["bound_file"])))
        if resolve and fs["shader"] and fs["string_gen"]:
            try:
                table, common = resolve(fs["shader"])
            except Exception:                                     # noqa: BLE001 - the shader catalog is optional
                table, common = {}, False
            try:
                mask = int(es.get("gen_mask") or "0", 16)
            except ValueError:
                mask = 0
            dropped = [n for n in sorted({x.strip().upper() for x in fs["string_gen"].split("%") if x.strip()}) if common and table.get(n) and not mask & table[n]]
            if dropped:
                findings.append(_finding("warning", "M004", label, "StringGenMask names %s, valid flags of the common table, and the engine's final generation mask %X does not carry them: the shader dropped them "
                                         "(a flag whose texture or dependency is missing, for example a height map for parallax)" % (", ".join("%" + n for n in dropped), mask),
                                         "check the textures the flag needs (shader_info), or remove the flag"))
    seen = set()
    for l in row.get("log", []):
        text = re.sub(r"\$\d", "", l).strip()
        if not text or text in seen or not NOISE.search(text):
            continue
        seen.add(text)
        m = re.search(r'Failed to load shader "([^"]+)"', text)
        if m:                                           # the engine keeps the NAME of a shader it could not load, so the comparison above cannot see it: its own log line is the evidence
            findings.append(_finding("error", "M003", where, "the engine could not load shader '%s' (%s)" % (m.group(1), text), "check the shader name (shader_search); a material with a missing shader draws with the default one"))
            continue
        findings.append(_finding("warning", "M007", where, text + "   (the log is process-wide: a line can belong to something else that loaded at the same moment)"))
    return findings


def flag_names(mask, table):
    """The names of the common flags whose bit is set in a generation mask."""
    return sorted(n for n, b in table.items() if b and mask & b)


def _catalog_resolver():
    """resolve(shader) -> ({FLAG: global bit}, the shader uses the common flag table), from the engine's own globals.txt and Shaders/<name>.ext; None when the catalog cannot be read."""
    try:
        import shader_catalog
        gp = shader_catalog.find_globals()
        table = shader_catalog.parse_globals(open(gp, encoding="utf-8").read())[0] if gp else {}
    except Exception:                                             # noqa: BLE001
        return None

    def resolve(shader):
        sh = shader_catalog.find(shader)
        return table, bool(sh and sh["uses_common_flags"])
    return resolve


def check(paths=None):
    """paths: .mtl paths relative to Assets (default: all). -> {ok, errors, warnings, findings, materials: [{file, found, subs, surfaces, shaders}]}."""
    import bridge_py
    paths = [p.replace(chr(92), "/") for p in (paths or project_materials())]
    os.makedirs(SCRATCH, exist_ok=True)
    names_file, out = os.path.join(SCRATCH, "names.txt"), os.path.join(SCRATCH, "mtl_%d.json" % os.getpid())
    with open(names_file, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(paths) + "\n")
    if os.path.exists(out):
        os.remove(out)
    r = bridge_py.send("bridge.material.check %s | %s" % (_slash(out), _slash(names_file)), 240)
    if not r.get("ok"):
        raise MaterialError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % r.get("error"))
    with open(out, encoding="utf-8") as f:
        rows = json.load(f)
    os.remove(out)
    resolve = _catalog_resolver()
    try:
        from asset_lint import Names
        names = Names()
        exists = lambda f: names.exists(f) or names.exists(os.path.splitext(f)[0] + ".dds")           # noqa: E731
    except Exception:                                             # noqa: BLE001 - lint is optional here
        exists = None
    findings, mats = [], []
    keep =os.path.join(DATA, "material_load_logs.json")           # the engine's log lines exist only for a material's FIRST load in a session: remember them across runs
    try:
        with open(keep, encoding="utf-8") as f:
            logs = json.load(f)
    except (OSError, ValueError):
        logs = {}
    logs = {k: v for k, v in logs.items() if "zz_mcp_" not in k}
    for row in rows:
        if row.get("log") and "zz_mcp_" not in row["file"]:                  # the tests' scratch copies are not worth remembering
            logs[row["file"]] = row["log"]
    with open(keep, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=1)
    for row in rows:
        path = row["file"]
        try:
            fsubs = file_subs(path if path.lower().endswith(".mtl") else path + ".mtl") if os.path.isfile(os.path.join(ASSETS, (path if path.lower().endswith(".mtl") else path + ".mtl").replace("/", os.sep))) else []
        except (ET.ParseError, OSError):
            fsubs = []
        findings.extend(compare(row, fsubs, resolve, exists))
        mats.append({"file": path, "found": row.get("found", False), "already_loaded": row.get("already_loaded", False),
                     "subs": len(row.get("subs", [])), "surfaces": sorted({s.get("surface", "") for s in row.get("subs", []) if s.get("surface")}),
                     "shaders": sorted({s.get("shader", "") for s in row.get("subs", []) if s.get("shader")})})
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "findings": findings, "materials": mats}


def main():
    r = check(sys.argv[1:] or None)
    print("%s: %d error(s), %d warning(s); %d materials" % ("OK" if r["ok"] else "PROBLEMS", r["errors"], r["warnings"], len(r["materials"])))
    shaders, surfaces = {}, {}
    for m in r["materials"]:
        for s in m["shaders"]:
            shaders[s] = shaders.get(s, 0) + 1
        for s in m["surfaces"]:
            surfaces[s] = surfaces.get(s, 0) + 1
    print("  shaders :", ", ".join("%s %d" % kv for kv in sorted(shaders.items())))
    print("  surfaces:", ", ".join("%s %d" % kv for kv in sorted(surfaces.items())))
    for f in r["findings"]:
        print("  %-7s %s  %s\n          %s" % (f["severity"], f["code"], f["where"], f["message"][:240]))


if __name__ == "__main__":
    main()
