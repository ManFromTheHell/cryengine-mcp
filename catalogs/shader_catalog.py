#!/usr/bin/env python3
"""The engine's shaders as a catalog: generation flags, public parameters and texture slots (ENGINE_LEARNING_PLAN B3).

Everything here is read from the engine's own files, none from memory:

  Shaders/<Name>.ext              each shader's generation FLAGS (Name, Mask, label, description, dependencies), in engine/shaders.pak
  Shaders/HWScripts/CryFX/*.cfx   each shader's PUBLIC PARAMETERS (type, UI name, range, default, register) and TEXTURE SLOTS (register tN, the TM_ it takes), with the
                                  `#if %FLAG` each is declared under, and the .cfi files it includes
  <package>/user/shaders/cache/globals.txt   the table the ENGINE wrote: the common global flag bits (bit n = the n-th name in sorted order, with legacy fixes)
  Code/CryEngine/Cry3DEngine/MaterialHelpers.cpp + RenderDll/Common/Textures/TextureHelpers.cpp   the texture-slot tables (Map name <-> _suffix <-> TM_ name)

What the material loader does with a flag (Cry3DEngine/MatMan.cpp:467, Renderer.cpp:1316), which is the rule the catalog checks against: for the eight shaders that share
the common flag table (Eye, Glass, Hair, HumanSkin, Illum, MultiLayeredMaterials, Terrain, Vegetation) the final mask is computed ONLY from the material's `StringGenMask`;
a name that is not in the table is silently ignored, and the numeric `GenMask` attribute is not used at all. For every other shader `GenMask` is used.

    python catalogs/shader_catalog.py                 list the shaders
    python catalogs/shader_catalog.py Illum           flags, parameters, textures of one shader
    python catalogs/shader_catalog.py Illum %PARALLAX_OCCLUSION_MAPPING %NORMAL_MAP     resolve a flag set
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import re
import sys

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ROOT = os.path.dirname(PROJECT)
sys.path.insert(0, HERE)
import cpp_conditions as cpp  # noqa: E402
import crypak  # noqa: E402

ENGINE = os.environ.get("CRYENGINE_ENGINE", os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1"))
SOURCE = _paths.ENGINE_SOURCE   # env CRYENGINE_SOURCE; "" when unset (the source-reading steps then skip)
CFX_DIR = "Shaders/HWScripts/CryFX/"

_CACHE = {}
# the TM_ names a material's texture slots bind to (RenderDll/Common/Textures/TextureHelpers.cpp, the 14 rows): a texture declared with one of these is a slot the Material Editor offers
MATERIAL_TM = {"TM_Diffuse", "TM_Normals", "TM_Specular", "TM_Env", "TM_Detail", "TM_Smoothness", "TM_Height", "TM_DecalOverlay", "TM_SubSurface", "TM_Custom",
               "TM_CustomSecondary", "TM_Opacity", "TM_Translucency", "TM_Emittance"}


def _pak_files(engine=ENGINE):
    """{entry name with /: text} for the shader scripts in engine/shaders.pak (.ext, .cfx, .cfi). Cached."""
    key = ("pak", engine)
    if key not in _CACHE:
        pak = os.path.join(engine, "engine", "shaders.pak")
        out = {}
        for e in crypak.entries(pak):
            name = e["name"].replace(chr(92), "/")
            if name.lower().endswith((".ext", ".cfx", ".cfi")):
                out[name] = crypak.read(dict(e, pak=pak)).decode("utf-8", "replace")
        _CACHE[key] = out
    return _CACHE[key]


# --------------------------------------------------------------------------- generation flags (.ext)

def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


_PROP = re.compile(r"\bProperty\s*\{(.*?)\n\s*\}", re.S)


def parse_ext(text):
    """One .ext -> {version, uses_common, properties: [{name, mask, label, description, hidden, depends_set, depends_reset, condition}]}.
    `condition` is the #ifdef/#if the property sits under (e.g. FEATURE_MESH_TESSELLATION) or ''."""
    clean = _strip_comments(text)
    uses_common = "UsesCommonGlobalFlags" in clean
    lines = clean.splitlines()
    terms = cpp.condition_terms(lines)
    # offsets of each line start, to map a match back to its line
    starts, pos = [], 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    flat = "\n".join(lines)
    props = []
    for m in _PROP.finditer(flat):
        body = m.group(1)
        line_idx = max(0, flat.count("\n", 0, m.start()))
        name = re.search(r"\bName\s*=\s*(%?\w+)", body)
        mask = re.search(r"\bMask\s*=\s*(0x[0-9A-Fa-f]+|\d+)", body)
        if not name or not mask:
            continue
        label = re.search(r"^\s*Property\s*\(([^)]*)\)", body, re.M)
        desc = re.search(r"^\s*Description\s*\(([^)]*)\)", body, re.M)
        dset = re.findall(r"\bDependencySet\s*=\s*(\S+)", body)
        dreset = re.findall(r"\bDependencyReset\s*=\s*(\S+)", body)
        cond = " && ".join(("" if want else "!") + expr for expr, want in (terms[line_idx] if line_idx < len(terms) else []))
        props.append({"name": name.group(1).lstrip("%"), "mask": int(mask.group(1), 16 if mask.group(1).lower().startswith("0x") else 10),
                      "label": (label.group(1).strip() if label else ""), "description": (desc.group(1).strip() if desc else ""),
                      "hidden": bool(re.search(r"^\s*Hidden\s*$", body, re.M)), "depends_set": dset, "depends_reset": dreset, "condition": cond})
    return {"uses_common": uses_common, "properties": props}


def parse_globals(text):
    """The engine's own globals.txt -> ({FLAG: mask}, [shaders that use the common table], version)."""
    lines = text.splitlines()
    version = lines[0].split()[1] if lines and lines[0].startswith("FX_CACHE_VER") else ""
    shaders = re.findall(r"%([A-Z0-9_]+)", lines[1]) if len(lines) > 1 else []
    flags = {}
    for ln in lines[2:]:
        m = re.match(r"^%(\w+)\s+([0-9a-fA-F]+)\s*$", ln.strip())
        if m:
            flags[m.group(1)] = int(m.group(2), 16)
    return flags, shaders, version


def find_globals():
    """A globals.txt the engine wrote: one of this project's packages (their shader caches), newest first. -> path or None."""
    best = None
    for name in os.listdir(ROOT):
        p = os.path.join(ROOT, name, "user", "shaders", "cache", "globals.txt")
        if os.path.isfile(p) and (best is None or os.path.getmtime(p) > os.path.getmtime(best)):
            best = p
    return best


# --------------------------------------------------------------------------- parameters and texture slots (.cfx / .cfi)

_DECL = re.compile(r"(?P<type>\b[A-Za-z_][\w]*(?:<[^>;]*>)?)\s+(?P<name>[A-Za-z_]\w*)\s*(?::\s*(?P<sem>[^<=;{}]+?))?\s*<(?P<ann>[^<>]*)>\s*(?:=\s*(?P<default>[^;]+))?;", re.S)
_ANN = re.compile(r"(?:(?P<atype>string|float|int|bool|float2|float3|float4)\s+)?(?P<key>\w+)\s*=\s*(?P<val>\"[^\"]*\"|[^;]+);")
_TEXDECL = re.compile(r"\b(?P<type>Texture2D|TextureCube|Texture3D|Texture2DArray)\s+(?P<name>\w+)\s*:\s*register\(t(?P<slot>\d+)\)\s*(?:<(?P<ann>[^<>]*)>)?\s*(?:=\s*(?P<tm>\w+))?\s*;", re.S)
_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)


def _ann_dict(ann):
    out = {}
    for m in _ANN.finditer(ann or ""):
        v = m.group("val").strip()
        out[m.group("key")] = v[1:-1] if v.startswith('"') else v
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_cfx(text, files=None, seen=None, origin=""):
    """Public parameters and texture slots of one shader script, with the `#if` terms each is declared under, following its #include "x.cfi" lines.
    A parameter is 'public' when it carries a UIName annotation (that is what the Material Editor lists) or a register; a texture is any `Texture2D n : register(tN)`."""
    files = files if files is not None else {}
    seen = seen if seen is not None else set()
    params, textures = [], []
    clean = _strip_comments(text)
    lines = clean.splitlines()
    terms = cpp.condition_terms(lines)
    starts, pos = [], 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    flat = "\n".join(lines)

    def cond_at(offset):
        idx = flat.count("\n", 0, offset)
        return [("" if want else "!") + expr for expr, want in (terms[idx] if idx < len(terms) else [])]

    for m in _TEXDECL.finditer(flat):
        ann = _ann_dict(m.group("ann"))
        textures.append({"name": m.group("name"), "type": m.group("type"), "register": "t" + m.group("slot"), "tm": m.group("tm") or "", "ui_name": ann.get("UIName", ""),
                         "material_slot": (m.group("tm") or "") in MATERIAL_TM,
                         "description": ann.get("UIDescription", ""), "srgb": ann.get("sRGBLookup", "") == "true", "conditions": cond_at(m.start()), "file": origin})
    for m in _DECL.finditer(flat):
        if m.group("type") in ("Texture2D", "TextureCube", "Texture3D", "Texture2DArray", "sampler", "SamplerState"):
            continue
        ann = _ann_dict(m.group("ann"))
        if "UIName" not in ann and "register" not in ann and "UIWidget" not in ann:
            continue
        default = (m.group("default") or "").strip()
        params.append({"name": m.group("name"), "type": m.group("type"), "public": bool(ann.get("UIName") or ann.get("UIWidget")), "ui_name": ann.get("UIName", ""), "description": ann.get("UIDescription", ""), "widget": ann.get("UIWidget", ""),
                       "min": _num(ann.get("UIMin")), "max": _num(ann.get("UIMax")), "step": _num(ann.get("UIStep")), "default": default,
                       "register": ann.get("register", ""), "conditions": cond_at(m.start()), "file": origin})
    lower = _CACHE.setdefault(("lower", id(files)), {k.lower(): k for k in files})      # the engine looks files up case-insensitively (VertexLib.cfi is vertexLib.cfi on disk)
    for inc in _INCLUDE.findall(clean):
        key = lower.get((CFX_DIR + inc).lower())
        if key and key not in seen:
            seen.add(key)
            p, t = parse_cfx(files[key], files, seen, inc)
            params += p
            textures += t
    return params, textures


# --------------------------------------------------------------------------- texture slot tables from the source

def texture_slots(source=SOURCE):
    """The 14 texture slots: [{map (the .mtl Map= name), suffix, tm (the shader's TM_ name), slot, adjustable}] read from the two source tables."""
    if not source:   # CRYENGINE_SOURCE unset: the tables cannot be read
        return []
    try:
        a = open(os.path.join(source, "Code", "CryEngine", "Cry3DEngine", "MaterialHelpers.cpp"), encoding="utf-8", errors="replace").read()
        b = open(os.path.join(source, "Code", "CryEngine", "RenderDll", "Common", "Textures", "TextureHelpers.cpp"), encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    rows = re.findall(r'\{\s*(EFTT_\w+),\s*"(EFTT_\w+)",\s*(true|false),\s*"([^"]+)",\s*"([^"]*)"\s*\}', a)
    tms = dict(re.findall(r'\{\s*(EFTT_\w+),\s*\d+,\s*[^,]+,\s*[^,]+,\s*"[^"]*",\s*"(TM_\w+)"', b))
    out, seen = [], set()
    for enum, _e2, adj, name, suffix in rows:
        if enum in seen or enum == "EFTT_UNKNOWN":
            continue
        seen.add(enum)
        out.append({"enum": enum, "map": name, "suffix": suffix, "tm": tms.get(enum, ""), "adjustable": adj == "true", "slot": len(out)})
    return out


# --------------------------------------------------------------------------- the catalog

def load(engine=ENGINE):
    """{shader name: {file, uses_common_flags, flags, params, textures}} for every shader with an .ext or a .cfx. Cached."""
    key = ("catalog", engine)
    if key in _CACHE:
        return _CACHE[key]
    files = _pak_files(engine)
    names = sorted({os.path.splitext(os.path.basename(n))[0] for n in files if n.endswith(".ext") and "/" not in n[len("Shaders/"):]}
                   | {os.path.splitext(os.path.basename(n))[0] for n in files if n.startswith(CFX_DIR) and n.endswith(".cfx")}, key=str.lower)
    out = {}
    for n in names:
        ext = files.get("Shaders/%s.ext" % n)
        cfx = files.get("%s%s.cfx" % (CFX_DIR, n))
        parsed = parse_ext(ext) if ext else {"uses_common": False, "properties": []}
        params, textures = parse_cfx(cfx, files, set(), n + ".cfx") if cfx else ([], [])
        out[n] = {"name": n, "has_ext": ext is not None, "has_cfx": cfx is not None, "uses_common_flags": parsed["uses_common"], "flags": parsed["properties"], "params": params, "textures": textures}
    _CACHE[key] = out
    return out


def find(name, engine=ENGINE):
    """Shader by name, case-insensitive, `Terrain.Layer` -> Terrain (the shaders use concatenated names)."""
    base = name.split(".")[0].lower()
    for k, v in load(engine).items():
        if k.lower() == base:
            return v
    return None


def declared(shader, engine=ENGINE):
    """Fast path for the linter: ({FLAG: mask}, uses_common_flag_table) read from Shaders/<shader>.ext alone (no scripts parsed), or None when the engine has no such file."""
    base = shader.split(".")[0].lower()
    key = ("declared", engine, base)
    if key not in _CACHE:
        text = next((t for n, t in _pak_files(engine).items() if n.lower() == "shaders/%s.ext" % base), None)
        if text is None:
            _CACHE[key] = None
        else:
            parsed = parse_ext(text)
            _CACHE[key] = ({f["name"].upper(): f["mask"] for f in parsed["properties"]}, parsed["uses_common"])
    return _CACHE[key]


def resolve_flags(shader, names, globals_path=None, engine=ENGINE):
    """What a StringGenMask means for a shader: which names the engine will honour, which it silently ignores, and the masks.
    -> {string_gen_mask, honoured, ignored, wrong_shader, local_mask_hex, global_mask_hex, rule}"""
    sh = find(shader, engine)
    if sh is None:
        raise KeyError("no shader %r" % shader)
    wanted = [n.strip().lstrip("%").upper() for n in names if n and n.strip()]
    declared = {f["name"].upper(): f for f in sh["flags"]}
    gp = globals_path or find_globals()
    gflags, gshaders, _v = parse_globals(open(gp, encoding="utf-8").read()) if gp else ({}, [], "")
    honoured, ignored, wrong = [], [], []
    for n in sorted(set(wanted)):
        if n in declared:
            honoured.append(n)
        elif n in gflags:
            wrong.append(n)                      # exists in the common table (another shader declares it) but not for this one
        else:
            ignored.append(n)
    common = sh["uses_common_flags"]
    local = sum(declared[n]["mask"] for n in honoured)
    glob = sum(gflags.get(n, 0) for n in honoured) if common else None
    return {"shader": sh["name"], "string_gen_mask": "".join("%" + n for n in sorted(set(wanted))), "honoured": honoured, "ignored": ignored, "wrong_shader": wrong,
            "local_mask_hex": "%X" % local, "global_mask_hex": ("%X" % glob) if glob is not None else None, "uses_common_flags": common,
            "rule": ("the engine computes this shader's mask ONLY from StringGenMask; a name outside the table is ignored; GenMask is not used (MatMan.cpp:467, Renderer.cpp:1316)"
                     if common else "this shader does not use the common flag table: the numeric GenMask is what counts")}


def decode_gen_mask(shader, mask, engine=ENGINE):
    """What a numeric GenMask (the hex in a .mtl) switches on for a shader: the flag names whose local mask bits are set, and the bits the shader does not declare.
    A shader that uses the common flag table (Illum and seven others) does not read GenMask at all: the answer says so. -> {shader, mask_hex, flags, unknown_bits_hex, used}"""
    sh = find(shader, engine)
    if sh is None:
        raise KeyError("no shader %r" % shader)
    value = mask if isinstance(mask, int) else int(str(mask).strip() or "0", 16)          # int(s, 16) takes an optional 0x prefix
    flags, covered = [], 0
    for f in sh["flags"]:
        if f["mask"] and value & f["mask"] == f["mask"]:
            flags.append(f["name"])
            covered |= f["mask"]
    return {"shader": sh["name"], "mask_hex": "%X" % value, "flags": flags, "unknown_bits_hex": "%X" % (value & ~covered), "used": not sh["uses_common_flags"],
            "rule": ("the engine reads this shader's numeric GenMask: these flags are on" if not sh["uses_common_flags"]
                     else "this shader does NOT read GenMask (it computes its mask from StringGenMask): the number below is ignored")}


def search(text="", limit=25, engine=ENGINE):
    """Flags, parameters and texture slots whose name, label or description contain every word. -> [{shader, kind, name, ...}]"""
    words = [w for w in re.split(r"\s+", text.lower().strip()) if w]
    out = []
    for sh in load(engine).values():
        for kind, items, label in (("flag", sh["flags"], "label"), ("param", sh["params"], "ui_name"), ("texture", sh["textures"], "ui_name")):
            for it in items:
                hay = (it["name"] + " " + it.get(label, "") + " " + it.get("description", "")).lower()
                if all(w in hay for w in words):
                    out.append({"shader": sh["name"], "kind": kind, "name": it["name"], "label": it.get(label, ""), "description": it.get("description", "")})
    return out[:limit], len(out)


def main():
    args = sys.argv[1:]
    if not args:
        for n, s in load().items():
            print("%-26s flags %-3d params %-3d textures %-3d %s" % (n, len(s["flags"]), len(s["params"]), len(s["textures"]), "[common flag table]" if s["uses_common_flags"] else ""))
        return
    sh = find(args[0])
    if sh is None:
        raise SystemExit("no shader %r" % args[0])
    if len(args) > 1:
        print(resolve_flags(args[0], args[1:]))
        return
    print("== %s" % sh["name"])
    for f in sh["flags"]:
        print("  flag  %%%-40s 0x%-10X %s%s" % (f["name"], f["mask"], f["label"], "  [hidden]" if f["hidden"] else ""))
    for p in sh["params"]:
        print("  param %-28s %-8s default %-8s [%s..%s] %s %s" % (p["name"], p["type"], p["default"], p["min"], p["max"], p["ui_name"], ("if " + " ".join(p["conditions"])) if p["conditions"] else ""))
    for t in sh["textures"]:
        print("  tex   %-20s %-5s %-16s %s" % (t["name"], t["register"], t["tm"], t["ui_name"]))


if __name__ == "__main__":
    main()
