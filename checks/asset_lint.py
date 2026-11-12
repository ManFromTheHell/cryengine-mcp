#!/usr/bin/env python3
"""A linter for a CRYENGINE project's loose assets: the mistakes that cost this project days, found in seconds, WITHOUT the editor.

    asset_lint.py [scope ...] [--json] [--max N]

Scopes (default: all): meshes materials textures audio level xml shaders animations licences.
Every check is one that went wrong here at least once; each finding says what, where, and what to do.

  L001 error    a .cgf names a material file that does not exist (the engine falls back to its flat default: nine houses rendered grey for a day)
  L002 error    the .cgf's sub-material names do not match the .mtl's SubMaterials (index by index: a .cgf stores the INDEX)
  L003 error    a .mtl names a texture that is not found (loose, in the project's paks, in the engine's or the GameSDK's)
  L004 error    an XML asset does not parse (a `--` inside a comment: RC silently skips it and the packager ships a STALE copy)
  L005 error    an Illum material enables parallax / offset bump mapping without a Heightmap slot (or the reverse: a Heightmap that nothing reads); judged from
                StringGenMask, which is all the engine reads for Illum (the hex GenMask is only a fallback for a material that has no StringGenMask)
  L012 info     a StringGenMask names a flag the shader does not declare (Shaders/<name>.ext): the engine silently ignores it (one finding per shader and flag)
  L006 error    a texture's compiled format is wrong for its name (`_displ` must be BC4: RC auto-selects BC3 for any TIFF with alpha unless the
                preset is inside the file; `_ddna` must be BC5), or its size is not a power of two
  L007 error    a .wav / .mp3 / .flac under audio/ (Crytek's packager has no such extension in its copy list: it never ships)
  L008 error    an audio library (ACE) event names a sample that is not found
  L010 error    the level's layers name a mesh / material that is not found
  L013 info     meshes of 1,000+ triangles that have no LOD at all (one finding for all of them)
  L015 error    a Mannequin database (.adb) names an animation no clip / blend space / combination of the project has (the fragment plays nothing)
  L016 info     an .adb fragment the FragDef file does not declare (code cannot request it)
  L017 warning  a material's SurfaceType is not defined in Libs/MaterialEffects/SurfaceTypes.xml (the engine falls back to mat_default; one finding per surface type)
  L014 error    a layer places a mesh whose path contains '_lod': the engine refuses to load it (it draws the default sphere); only LOD parts loaded by their base mesh may have it
  L018 error    (scope: licences) an asset's path, or a text asset, names a source whose licence forbids reuse: Neon Noir (its EULA forbids copying any element and derived works)
  L011 warning  the shader cache is OLDER than a material or texture: regenerate it (shader_cache_refresh.py) and run the shader tour
                (a missing permutation does not stop the game; it shows up only as cache misses in the tour)

Names are resolved against: the project's Assets folder, the engine's Engine folder, the GameSDK sample project, and the index of every .pak among
them (central directories only; cached in .cache/pak_index.json, rebuilt when a pak changes).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
ENGINE = os.environ.get("CRYENGINE_ENGINE_DIR") or os.environ.get("CRYENGINE_ENGINE") or (os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1"))
GAMESDK = os.environ.get("CRYENGINE_GAMESDK_DIR", os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Assets\crytek\cryengine-gamesdk-sample-project\5.7.1\gamesdk"))
# CRYENGINE_SHADER_PAKS: the folder that holds the generated ShaderCache.pak (optional; L011 only warns when it is missing)
SHADER_PAKS = os.environ.get("CRYENGINE_SHADER_PAKS", os.path.join(HERE, "ShaderPak", "Engine"))
CACHE = os.path.join(HERE, ".cache")

POM_BIT, OBM_BIT = 0x8000000, 0x20000
XML_EXTS = (".xml", ".mtl", ".adb", ".env", ".bspace", ".cdf", ".lua_xml")
BAD_AUDIO = (".wav", ".mp3", ".flac")
SCOPES = ("meshes", "materials", "textures", "audio", "level", "xml", "shaders", "animations", "licences")
# L018: sources whose licence forbids using them for this game. (pattern, why). Neon Noir's EULA (Launcher\Assets\crytek\neon-noir\1.0.0\NeonNoir_En_EULA.txt,
# section 5: "to copy, reproduce, ... NEON NOIR, in whole or in part", "to modify NEON NOIR or create any derived work") gives no right to reuse any element.
FORBIDDEN_SOURCES = (
    (re.compile(r"neon[\s_\-]*noir", re.I), "Neon Noir's EULA forbids copying it in whole or in part and making derived works (NeonNoir_En_EULA.txt section 5)"),
)
LICENCE_TEXT_EXTS = (".xml", ".mtl", ".adb", ".env", ".cdf", ".bspace", ".lua_xml", ".lyr", ".cryasset", ".json", ".txt", ".cfg", ".lua", ".csv", ".md")
LICENCE_TEXT_LIMIT = 2 * 1024 * 1024                         # larger files are not read as text (a level's binary data, a pak)


def read_bytes(path, limit=None, tries=12, wait=0.4):
    """Read a file, retrying while another program holds it: the editor's asset scanner opens a file the moment it appears, so reading what RC or a tool just wrote could fail with
    'Permission denied' for a moment (for example on a .dds the editor has just been reopened onto). Raises the last error after `tries` attempts."""
    import time as _t
    for i in range(tries):
        try:
            with open(path, "rb") as f:
                return f.read() if limit is None else f.read(limit)
        except PermissionError:
            if i == tries - 1:
                raise
            _t.sleep(wait)


def parse_xml(path):
    """ET.parse with the same retry (the tree of a .mtl or .xml that was just written)."""
    return ET.fromstring(read_bytes(path))


def configure(assets=None, shader_paks=None):
    """Point the linter at another Assets folder / shader-cache folder (the self-test uses a fixture)."""
    global ASSETS, SHADER_PAKS, PROJECT
    if assets:
        ASSETS = assets
        PROJECT = os.path.dirname(assets)
    if shader_paks:
        SHADER_PAKS = shader_paks


def norm(p):
    return p.replace("\\", "/").lower().lstrip("/")


class Names:
    """Everything that can satisfy a game path: loose files in the three folders, and every name inside their paks."""

    def __init__(self):
        self.loose_roots = [ASSETS, os.path.join(ENGINE, "Engine"), GAMESDK]
        self.pak_names = set()
        self._index_paks()

    def _paks(self):
        found = []
        for root in self.loose_roots + [ENGINE]:
            if not os.path.isdir(root):
                continue
            for name in os.listdir(root):
                if name.lower().endswith(".pak"):
                    found.append(os.path.join(root, name))
        return sorted(set(found))

    def _index_paks(self):
        os.makedirs(CACHE, exist_ok=True)
        path = os.path.join(CACHE, "pak_index.json")
        try:
            cache = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        fresh = {}
        for pak in self._paks():
            st = os.stat(pak)
            key = "%d:%d" % (st.st_size, int(st.st_mtime))
            entry = cache.get(pak)
            if entry and entry["key"] == key:
                fresh[pak] = entry
                continue
            try:
                names = [norm(n) for n in zipfile.ZipFile(pak).namelist()]
            except Exception:
                names = []
            fresh[pak] = {"key": key, "names": names}
        if fresh != cache:
            json.dump(fresh, open(path, "w", encoding="utf-8"))
        for entry in fresh.values():
            self.pak_names.update(entry["names"])

    def ui_elements(self):
        """The lower-cased names of the UI elements the loose project defines (<UIElement name=...> in libs/ui/UIElements/*.xml)."""
        if getattr(self, "_ui_elements", None) is None:
            found = set()
            for root in self.loose_roots:
                folder = os.path.join(root, "libs", "ui", "UIElements")
                if os.path.isdir(folder):
                    for f in os.listdir(folder):
                        if f.lower().endswith(".xml"):
                            try:
                                text = open(os.path.join(folder, f), encoding="utf-8", errors="replace").read()
                            except OSError:
                                continue
                            found.update(m.lower() for m in re.findall(r'<UIElement\s+name="([^"]+)"', text))
            self._ui_elements = found
        return self._ui_elements

    def exists(self, game_path):
        """True when the game path resolves to a loose file or a pak entry. `.tif` sources resolve through the compiled `.dds`."""
        p = norm(game_path)
        if not p:
            return True
        if p.endswith(".ui") and "/" not in p:
            # a UI element drawn as a texture ("PgBoard.ui", "PgBoard@1.ui", manual 'UI Dynamic Textures'): a pseudo file whose name is a UIElement of Libs/UI/UIElements/*.xml
            return p[:-3].split("@")[0] in self.ui_elements()
        engine_prefixed = p.startswith("%engine%/")
        if engine_prefixed:
            p = p[len("%engine%/"):]
        candidates = {p}
        if p.endswith(".tif"):
            candidates.add(p[:-4] + ".dds")
        for c in list(candidates):
            for root in self.loose_roots:
                if os.path.exists(os.path.join(root, c)):
                    return True
            if c in self.pak_names:
                return True
            if engine_prefixed and (os.path.exists(os.path.join(ENGINE, "Engine", c)) or ("engine/" + c) in self.pak_names):
                return True
        return False


class Report:
    def __init__(self, max_findings=300):
        self.findings = []
        self.counts = {}
        self.max = max_findings
        self.checked = {}

    def add(self, severity, code, path, message, fix=""):
        self.counts[(severity, code)] = self.counts.get((severity, code), 0) + 1
        if len(self.findings) < self.max:
            self.findings.append({"severity": severity, "code": code, "path": path.replace(PROJECT + os.sep, "").replace("\\", "/"), "message": message, "fix": fix})

    def seen(self, what, n=1):
        self.checked[what] = self.checked.get(what, 0) + n

    def result(self):
        errors = sum(n for (sev, _), n in self.counts.items() if sev == "error")
        warnings = sum(n for (sev, _), n in self.counts.items() if sev == "warning")
        return {"ok": errors == 0, "errors": errors, "warnings": warnings, "checked": self.checked,
                "by_code": {"%s %s" % (sev, code): n for (sev, code), n in sorted(self.counts.items())},
                "shown": len(self.findings), "findings": self.findings}


def walk(root, exts=None):
    for d, _, names in os.walk(root):
        if "__pycache__" in d:
            continue
        for n in names:
            if exts is None or n.lower().endswith(exts):
                yield os.path.join(d, n)


# ---------------------------------------------------------------- meshes and materials
def cgf_material(path):
    """(material path, [sub-material names]) from a .cgf's material chunk (0x1014), or None."""
    data = read_bytes(path)
    try:
        sig, ver, count, table = struct.unpack_from("<4sIII", data, 0)
    except struct.error:
        return None
    if sig != b"CrCh" or ver != 0x746:
        return None
    name, max_mat = None, -1
    for i in range(count):
        ctype, cver, cid, size, offset = struct.unpack_from("<HHIII", data, table + i * 16)
        if ctype == 0x1014 and size >= 132 and name is None:
            n = data[offset:offset + 128].split(b"\0")[0].decode("latin1")
            name = n or None
        elif ctype == 0x1017 and size >= 16:
            # mesh subsets: 16-byte header, then 36 bytes each (first index, index count, first vertex, vertex count, MATERIAL ID, radius, centre)
            nsub = struct.unpack_from("<I", data, offset + 4)[0]
            if 16 + 36 * nsub <= size:
                for k in range(nsub):
                    max_mat = max(max_mat, struct.unpack_from("<I", data, offset + 16 + 36 * k + 16)[0])
    return (name, max_mat) if name else None


def cgf_extent(path):
    """(min, max, vertex count) over the mesh chunks (0x1000) of a compiled .cgf, in its own space (metres): what the ENGINE will load,
    as opposed to what the script that made it meant (cgf_bounds.py documents the chunk layout)."""
    data = read_bytes(path)
    sig, ver, count, table = struct.unpack_from("<4sIII", data, 0)
    lo, hi, verts = [1e30] * 3, [-1e30] * 3, 0
    for i in range(count):
        ctype, cver, cid, size, offset = struct.unpack_from("<HHIII", data, table + i * 16)
        if ctype == 0x1000:
            verts += struct.unpack_from("<i", data, offset + 8)[0]
            p = offset + 7 * 4 + 16 * 4 + 4 * 4
            a, b = struct.unpack_from("<3f", data, p), struct.unpack_from("<3f", data, p + 12)
            lo = [min(x, y) for x, y in zip(lo, a)]
            hi = [max(x, y) for x, y in zip(hi, b)]
    return (lo, hi, verts) if verts else None


def cgf_lods(path):
    """The levels of detail a compiled .cgf carries, from its node and mesh chunks: [{level, node, triangles, vertices}] in level order (0 = the base mesh, the node whose parent is -1;
    a LOD is a node named $lodN_<name> that hangs under it), or [] for a file this cannot read. Triangles = the mesh chunk's index count / 3 (Barn.cgf: 5022 / 3 = 1674 = the
    engine's own .cryasset figure)."""
    data = read_bytes(path)
    try:
        sig, ver, count, table = struct.unpack_from("<4sIII", data, 0)
    except struct.error:
        return []
    if sig != b"CrCh" or ver != 0x746:
        return []
    meshes, nodes = {}, []
    for i in range(count):
        ctype, cver, cid, size, offset = struct.unpack_from("<HHIII", data, table + i * 16)
        if ctype == 0x1000:
            meshes[cid] = {"vertices": struct.unpack_from("<i", data, offset + 8)[0], "triangles": struct.unpack_from("<i", data, offset + 12)[0] // 3}
        elif ctype == 0x100B:
            name = data[offset:offset + 64].split(b"\0")[0].decode("latin1")
            obj, parent = struct.unpack_from("<ii", data, offset + 64)
            nodes.append({"id": cid, "name": name, "object": obj, "parent": parent})
    out = []
    for n in nodes:
        m = re.match(r"^\$lod(\d+)", n["name"], re.I)
        level = int(m.group(1)) if m else (0 if n["parent"] == -1 else None)
        if level is not None and n["object"] in meshes:
            out.append(dict(meshes[n["object"]], level=level, node=n["name"], parent=n["parent"], id=n["id"]))
    return sorted(out, key=lambda r: r["level"])


def mtl_info(path):
    """(is multi, [(name, element)], root) of a .mtl; raises ET.ParseError."""
    root = parse_xml(path)
    subs = root.find("SubMaterials")
    if subs is not None:
        items = [(m.get("Name", ""), m) for m in subs.findall("Material")]
        return True, items, root
    return False, [(root.get("Name", ""), root)], root


def heavy_without_lod(paths):
    """[(triangles, path)], heaviest first, of the .cgf files of HEAVY_TRIANGLES or more that carry no LOD (no $lodN_ node and no _lod1.cgf next to them)."""
    have = {p.lower() for p in paths}
    heavy = []
    for path in paths:
        if "_lod" in os.path.basename(path).lower():
            continue
        try:
            lods = cgf_lods(path)
        except (OSError, struct.error):
            continue
        sibling = os.path.splitext(path)[0] + "_lod1.cgf"
        if lods and len(lods) == 1 and lods[0]["triangles"] >= HEAVY_TRIANGLES and sibling.lower() not in have:
            heavy.append((lods[0]["triangles"], path))
    heavy.sort(reverse=True)
    return heavy


def lint_meshes(rep, names):
    paths = list(walk(os.path.join(ASSETS, "objects"), (".cgf",)))
    for path in paths:
        lint_mesh(rep, names, path)
    heavy = heavy_without_lod(paths)
    if heavy:
        rep.add("info", "L013", heavy[0][1], "%d mesh%s of %d+ triangles ha%s no LOD (a $lodN_<name> node or a _lod1.cgf next to it), e.g. %s"
                % (len(heavy), "" if len(heavy) == 1 else "es", HEAVY_TRIANGLES, "s" if len(heavy) == 1 else "ve", ", ".join("%s %d" % (os.path.basename(p), t) for t, p in heavy[:4])),
                "the manual (Optimization Overview, LODs): always make LODs, each saving about half the triangles; build_mesh takes lods=N and a part's detail=N")


def lint_mesh(rep, names, path):
    """One .cgf: its material exists and covers every material id the mesh uses."""
    rep.seen("meshes")
    info = cgf_material(path)
    if info is None:
        return
    material, max_mat = info
    mtl_rel = norm(material) + ".mtl"
    if "/" not in mtl_rel and "\\" not in material:
        # a bare name is resolved next to the .cgf (that is how Crytek's own meshes name theirs)
        mtl_rel = norm(os.path.relpath(os.path.dirname(path), ASSETS)) + "/" + mtl_rel
    if not names.exists(mtl_rel):
        rep.add("error", "L001", path, "names material '%s', which does not exist (the engine uses its flat default)" % material,
                "rebuild the mesh with the right material_file, or create %s.mtl" % material)
        return
    mtl_path = os.path.join(ASSETS, mtl_rel)
    if not os.path.isfile(mtl_path) or max_mat < 1:
        return
    try:
        multi, items, _ = mtl_info(mtl_path)
    except ET.ParseError:
        return                                                  # L004 reports it
    # a .cgf stores the sub-material INDEX, never the name (Crytek's own meshes call theirs 'proxy_wood' where the .mtl says 'proxy'), and
    # its header's sub-material count includes unused slots (19 declared, 14 in the .mtl, 6 used): what breaks is a USED index past the
    # end of the .mtl's list
    if max_mat >= len(items):
        rep.add("error", "L002", path, "a subset uses material id %d but %s defines %d sub-material(s) (it draws with the default material)"
                % (max_mat, mtl_rel, len(items)), "add the sub-materials to the .mtl; append, never reorder: the mesh stores the index")


HEAVY_TRIANGLES = 1000                                       # L013: a mesh this heavy without any LOD is listed (the engine's own advice is to give every asset LODs)
HEIGHT_FLAGS = {"PARALLAX_OCCLUSION_MAPPING", "OFFSET_BUMP_MAPPING", "SILHOUETTE_PARALLAX_OCCLUSION_MAPPING"}


def _declared(shader):
    """(flags the shader declares, uses the common flag table) from the engine's own Shaders/<name>.ext, or None when the engine pak cannot be read (the check is then skipped)."""
    try:
        import shader_catalog
        return shader_catalog.declared(shader)
    except Exception:                                       # noqa: BLE001 - no engine install: lint without the shader table
        return None


def illum_wants_height(el):
    """Does this Illum material turn on a feature that reads a height map? -> (bool, what it was decided from).
    The engine decides from StringGenMask ALONE when the attribute exists (Cry3DEngine/MatMan.cpp:467 -> CRenderer::EF_GetShaderGlobalMaskGenFromString: Illum uses the common
    flag table, so the numeric GenMask is not read at all and a name outside the table is ignored). Only a material with no StringGenMask falls back to the legacy hex GenMask."""
    sg = el.get("StringGenMask")
    if sg is not None:
        flags = {n.upper() for n in sg.split("%") if n}
        return bool(flags & HEIGHT_FLAGS), "StringGenMask '%s'" % sg
    try:
        gen = int(el.get("GenMask") or "0", 16)
    except ValueError:
        gen = 0
    return bool(gen & (POM_BIT | OBM_BIT)), "legacy GenMask %s (no StringGenMask)" % el.get("GenMask")


_SURFACE_TYPES = {}


def surface_types(assets=None):
    """The surface types the engine knows in this project: the <SurfaceType name=...> entries of Libs/MaterialEffects/SurfaceTypes.xml (lower case). None when the file is missing (nothing can be said)."""
    assets = assets or ASSETS
    if assets not in _SURFACE_TYPES:
        path = os.path.join(assets, "libs", "MaterialEffects", "SurfaceTypes.xml")
        found = None
        if os.path.isfile(path):
            try:
                found = {(e.get("name") or "").lower() for e in ET.parse(path).getroot().iter("SurfaceType") if e.get("name")} or None
            except ET.ParseError:
                found = None
        _SURFACE_TYPES[assets] = found
    return _SURFACE_TYPES[assets]


def lint_materials(rep, names):
    ignored = {}
    surf = {}
    for path in walk(os.path.join(ASSETS, "materials"), (".mtl",)):
        lint_material(rep, names, path, ignored, surf)
    defined = surface_types()
    if defined is not None:                                  # imported meshes carry their own materials (objects/...): only their surface types are judged here, the rest of their leftovers are the sample's
        for path in walk(os.path.join(ASSETS, "objects"), (".mtl",)):
            try:
                _, items, _ = mtl_info(path)
            except ET.ParseError:
                continue
            for _, el in items:
                st = (el.get("SurfaceType") or "").strip()
                if st and st.lower() not in defined and path not in surf.get(st, []):
                    surf.setdefault(st, []).append(path)
    for st, paths in sorted(surf.items()):
        rep.add("warning", "L017", paths[0], "SurfaceType '%s' is used by %d material%s (e.g. %s) but Libs/MaterialEffects/SurfaceTypes.xml does not define it: the engine says \"undefined surface type, using mat_default\" for each "
                "(level.validate shows it), so physics and footstep/impact effects treat it as mat_default" % (st, len(paths), "" if len(paths) == 1 else "s", ", ".join(os.path.basename(p) for p in paths[:3])),
                "define it in SurfaceTypes.xml (the GameSDK sample project has a full set) or use one of: %s" % ", ".join(sorted(defined)))
    # one finding per (shader, flag), not per file: the GameSDK's materials carry the same leftovers hundreds of times
    for (shader, flag), paths in sorted(ignored.items()):
        rep.add("info", "L012", paths[0], "%s: '%%%s' is in %d material%s' StringGenMask but is not a flag of this shader, so the engine IGNORES it (e.g. %s)"
                % (shader, flag, len(paths), "" if len(paths) == 1 else "s", ", ".join(os.path.basename(p) for p in paths[:3])),
                "harmless; remove it from the material to say what it really does")


def lint_material(rep, names, path, ignored=None, surf=None):
    """One .mtl: its textures exist and its parallax / offset-bump flags agree with its Heightmap slot (judged from what the ENGINE reads: StringGenMask)."""
    rep.seen("materials")
    try:
        _, items, _ = mtl_info(path)
    except ET.ParseError:
        return                                              # L004
    for sub_name, el in items:
        textures = el.find("Textures")
        files = []
        has_height = False
        if textures is not None:
            for t in textures.findall("Texture"):
                if t.get("Map") == "Heightmap":
                    has_height = True
                if t.get("File"):
                    files.append(t.get("File"))
        for f in files:
            if not names.exists(f):
                rep.add("error", "L003", path, "%s: texture '%s' not found" % (sub_name or "(material)", f), "create it, or fix the path (.tif sources resolve through the .dds)")
        if (el.get("Shader") or "").lower() == "illum":
            wants, source = illum_wants_height(el)
            if wants and not has_height:
                rep.add("error", "L005", path, "%s: parallax/offset bump mapping is enabled (%s) but there is no Heightmap slot" % (sub_name or "(material)", source))
            elif has_height and not wants:
                rep.add("error", "L005", path, "%s: has a Heightmap but neither parallax occlusion nor offset bump mapping is enabled (%s): nothing reads it" % (sub_name or "(material)", source))
        if surf is not None:
            st = (el.get("SurfaceType") or "").strip()
            defined = surface_types()
            if st and defined is not None and st.lower() not in defined:
                surf.setdefault(st, []).append(path)
        if ignored is not None:
            sg = el.get("StringGenMask")
            table = _declared((el.get("Shader") or "").strip()) if sg else None
            if table and table[1]:                          # only a shader that uses the common flag table ignores names outside it
                for n in [x for x in sg.split("%") if x]:
                    if n.upper() not in table[0]:
                        ignored.setdefault(((el.get("Shader") or "").split(".")[0], n), []).append(path)


# ---------------------------------------------------------------- textures
DXGI = {71: "BC1", 72: "BC1 sRGB", 74: "BC2", 77: "BC3", 80: "BC4", 81: "BC4 snorm", 83: "BC5", 84: "BC5 snorm", 98: "BC7", 99: "BC7 sRGB"}


def dds_header(path):
    d = read_bytes(path, 148)
    if d[:4] != b"DDS " or len(d) < 128:
        return None
    h, w = struct.unpack_from("<II", d, 12)
    fourcc = d[84:88]
    dxgi = struct.unpack_from("<I", d, 128)[0] if fourcc == b"DX10" and len(d) >= 132 else None
    return {"w": w, "h": h, "fourcc": fourcc, "dxgi": dxgi}


def lint_textures(rep):
    for path in walk(os.path.join(ASSETS, "textures"), (".dds",)):
        lint_texture(rep, path)


def lint_texture(rep, path):
    """One .dds: a DDS file, power-of-two sides, and the format its name promises."""
    rep.seen("textures")
    low = os.path.basename(path).lower()
    info = dds_header(path)
    if info is None:
        rep.add("error", "L006", path, "is not a DDS file")
        return
    fmt = DXGI.get(info["dxgi"], info["fourcc"].decode("latin1") if info["dxgi"] is None else "dxgi %s" % info["dxgi"])
    if info["w"] > 0 and (info["w"] & (info["w"] - 1) or info["h"] & (info["h"] - 1)):
        rep.add("warning", "L006", path, "is %dx%d, not a power of two" % (info["w"], info["h"]))
    stem = low[:-4]
    if stem.endswith("_displ") and info["dxgi"] != 80:
        rep.add("error", "L006", path, "is %s; a height map must be BC4. RC picks BC3 for any TIFF with an alpha channel unless the preset is inside the TIFF "
                "(texture_lab.crytif_preset); /preset= on the command line is ignored" % fmt, "recompile with the Displacement preset inside the TIFF")
    elif stem.endswith("_ddna") and info["dxgi"] not in (83, 84):
        rep.add("warning", "L006", path, "is %s; normals with smoothness are BC5 (+ a BC4 alpha)" % fmt)


def lint_paths(meshes=(), materials=(), textures=()):
    """The same checks on named files only (the pipeline tools run this on what they just wrote). Returns the structured result."""
    rep = Report(100)
    names = Names()
    for p in meshes:
        lint_mesh(rep, names, p)
    for p in materials:
        lint_material(rep, names, p)
        try:
            parse_xml(p)
        except ET.ParseError as e:
            rep.add("error", "L004", p, "does not parse: %s" % e)
    for p in textures:
        lint_texture(rep, p)
    return rep.result()


# ---------------------------------------------------------------- audio, xml, level, shaders
def lint_audio(rep, names):
    audio = os.path.join(ASSETS, "audio")
    for path in walk(audio, BAD_AUDIO):
        rep.add("error", "L007", path, "Crytek's packager never ships %s files (its copy list has *.ogg, no *.wav)" % os.path.splitext(path)[1], "encode to .ogg (ffmpeg -c:a libvorbis)")
    for path in walk(audio, (".xml",)):
        if os.sep + "ace" + os.sep not in path.lower():
            continue
        rep.seen("audio libraries")
        try:
            root = parse_xml(path)
        except ET.ParseError:
            continue                                            # L004
        for ev in root.iter("Event"):
            n = ev.get("name")
            if n:
                rep.seen("audio events")
            if n and not (names.exists("audio/sdlmixer/assets/" + n) or names.exists("audio/" + n)):
                rep.add("error", "L008", path, "event names sample '%s', which is not found under audio/sdlmixer/assets" % n)


def lint_xml(rep):
    for path in walk(ASSETS, XML_EXTS):
        rep.seen("xml files")
        try:
            with open(path, "rb") as f:
                head = f.read(16)
            if head.startswith(b"CryXmlB"):
                continue
            ET.parse(path)
        except ET.ParseError as e:
            rep.add("error", "L004", path, "does not parse: %s (a '--' inside a comment is enough: RC skips the file and ships a stale converted copy)" % e)
        except OSError:
            continue


def lint_level(rep, names):
    pattern = re.compile(r'"([^"<>]+?\.(?:cgf|cdf|chr|mtl|cga|skin))"', re.I)
    material_attr = re.compile(r'\b(?:Material|CharacterMaterial)="([^"<>]+)"', re.I)   # object materials are written without the extension
    levels = os.path.join(ASSETS, "levels")
    for path in walk(levels, (".lyr",)):
        rep.seen("level layers")
        text = open(path, encoding="utf-8", errors="replace").read()
        missing = {}
        refs = [m.group(1) for m in pattern.finditer(text)]
        refs += [r if r.lower().endswith(".mtl") else r + ".mtl" for r in material_attr.findall(text)]
        for ref in refs:
            if "%" in ref and not ref.lower().startswith("%engine%"):
                continue
            rep.seen("level references")
            if ref.lower().endswith(".cgf") and "_lod" in ref.lower():
                rep.add("error", "L014", path, "places '%s': the engine refuses any mesh path containing '_lod' (ObjMan.cpp: \"'_lod' meshes can be loaded only internally\") and draws its default sphere" % ref,
                        "rename the folder or file so the path has no '_lod' in it")
            if not names.exists(ref):
                missing[ref] = missing.get(ref, 0) + 1
        for ref, n in sorted(missing.items())[:40]:
            rep.add("error", "L010", path, "names '%s' (%d time%s), which is not found" % (ref, n, "" if n == 1 else "s"))


def lint_shaders(rep):
    newest, newest_path = 0.0, ""
    for root in (os.path.join(ASSETS, "materials"), os.path.join(ASSETS, "textures")):
        for path in walk(root, (".mtl", ".dds")):
            m = os.path.getmtime(path)
            if m > newest:
                newest, newest_path = m, path
    cache = os.path.join(SHADER_PAKS, "ShaderCache.pak")
    rep.seen("shader cache")
    if not os.path.isfile(cache):
        rep.add("warning", "L011", cache, "no generated shader cache found (only the engine's stock one will ship)")
        return
    if os.path.getmtime(cache) < newest:
        rep.add("warning", "L011", cache, "is older than %s: materials or textures changed after the shader cache was generated. Any new permutation is missing "
                "from it, and only the shader tour shows that" % os.path.relpath(newest_path, PROJECT), "run shader_cache_refresh.py, repackage, and run the shader tour (0 misses)")


_ANIM_NAMES = {}


def animation_names(assets=None):
    """The animation names Mannequin and the character's animation list can resolve: the stems of every clip in the compiled Animations.img of animations.pak, of every .caf/.bspace/.comb in
    the pak and of every loose one under Assets/Animations (player.chrparams collects '*/*.caf' and '*/*.bspace' by file name). None when the project has no animations at all."""
    assets = assets or ASSETS
    if assets in _ANIM_NAMES:
        return _ANIM_NAMES[assets]
    names = set()
    pak = os.path.join(assets, "animations.pak")
    if os.path.isfile(pak):
        try:
            import anim_img
            import crypak
            import pakread
            for r in anim_img.headers(pakread.read(pak, "Animations/Animations.img")):
                names.add(os.path.splitext(os.path.basename(r["path"]))[0].lower())
            for e in crypak.entries(pak):
                n = e["name"].replace(chr(92), "/")
                if n.lower().endswith((".caf", ".bspace", ".comb")):
                    names.add(os.path.splitext(os.path.basename(n))[0].lower())
        except Exception:                                        # noqa: BLE001 - an unreadable pak: the check is skipped, not failed
            pass
    for root_dir, _, files in os.walk(os.path.join(assets, "Animations")):
        for f in files:
            if f.lower().endswith((".caf", ".bspace", ".comb")):
                names.add(os.path.splitext(f)[0].lower())
    _ANIM_NAMES[assets] = names or None
    return _ANIM_NAMES[assets]


def lint_animations(rep, names):
    """Mannequin databases (.adb): the animations a fragment names must exist, the fragment and tag definition files must exist, and a fragment the FragDef does not declare cannot be requested."""
    have = animation_names()
    for path in walk(os.path.join(ASSETS, "Animations"), (".adb",)):
        rep.seen("mannequin databases")
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue                                             # L004 reports it
        for attr in ("FragDef", "TagDef"):
            ref = root.get(attr)
            if ref and not names.exists(ref):
                rep.add("error", "L001", path, "%s='%s' does not exist" % (attr, ref), "fix the path (relative to Assets)")
        declared = set()
        fd = root.get("FragDef")
        if fd and names.exists(fd):
            try:
                declared = {(t.get("name") or "").lower() for t in ET.parse(os.path.join(ASSETS, norm(fd))).getroot().iter("Tag")}
            except (ET.ParseError, OSError):
                declared = set()
        missing = {}
        for frag_list in root.findall("FragmentList"):
            for frag in frag_list:
                if declared and frag.tag.lower() not in declared:
                    rep.add("info", "L016", path, "fragment '%s' is not declared in %s: code cannot request it" % (frag.tag, fd), "add it to the FragmentIds file or delete it")
                for a in frag.iter("Animation"):
                    n = a.get("name")
                    if have is not None and n and n.lower() not in have:
                        missing[n] = missing.get(n, 0) + 1
        for n, c in sorted(missing.items()):
            rep.add("error", "L015", path, "names animation '%s' (%d time%s), which no clip, blend space or combination in the project's animation set has (the fragment plays nothing: the character holds its pose)" % (n, c, "" if c == 1 else "s"),
                    "check the name against Animations.img (anim_img.py) or add the clip")


def lint_licences(rep):
    """L018: no asset's path, and no text asset, may name a source whose licence forbids reuse (Neon Noir). Names only: this does not prove an asset's
    origin (THIRD_PARTY.md does that); it stops the known-forbidden source from entering by path or by a reference in a file."""
    for path in walk(ASSETS):
        rel = os.path.relpath(path, ASSETS).replace("\\", "/")
        rep.seen("asset paths (licence)")
        for pat, why in FORBIDDEN_SOURCES:
            if pat.search(rel):
                rep.add("error", "L018", path, "the path names a forbidden source: %s" % why, "delete it; take the same thing from the GameSDK sample project (THIRD_PARTY.md) or make it")
        if not rel.lower().endswith(LICENCE_TEXT_EXTS):
            continue
        try:
            if os.path.getsize(path) > LICENCE_TEXT_LIMIT:
                continue
            text = read_bytes(path).decode("utf-8", "replace")
        except OSError:
            continue
        for pat, why in FORBIDDEN_SOURCES:
            m = pat.search(text)
            if m:
                rep.add("error", "L018", path, "the file refers to a forbidden source ('%s'): %s" % (m.group(0), why), "remove the reference and the asset it brought in")


def lint(scopes=None, max_findings=300):
    scopes = [s for s in (scopes or SCOPES) if s in SCOPES] or list(SCOPES)
    rep = Report(max_findings)
    need_names = any(s in scopes for s in ("meshes", "materials", "audio", "level", "animations"))
    names = Names() if need_names else None
    if "meshes" in scopes:
        lint_meshes(rep, names)
    if "materials" in scopes:
        lint_materials(rep, names)
    if "textures" in scopes:
        lint_textures(rep)
    if "audio" in scopes:
        lint_audio(rep, names)
    if "xml" in scopes or "materials" in scopes:
        lint_xml(rep)
    if "level" in scopes:
        lint_level(rep, names)
    if "shaders" in scopes:
        lint_shaders(rep)
    if "animations" in scopes:
        lint_animations(rep, names)
    if "licences" in scopes:
        lint_licences(rep)
    out = rep.result()
    out["scopes"] = scopes
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    max_n = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 300
    args = [a for a in args if not a.isdigit()]
    out = lint(args or None, max_n)
    if "--json" in sys.argv:
        print(json.dumps(out, indent=1))
        return 0 if out["ok"] else 1
    print("%s: %d error(s), %d warning(s); checked %s" % ("OK" if out["ok"] else "PROBLEMS", out["errors"], out["warnings"],
                                                          ", ".join("%d %s" % (n, k) for k, n in out["checked"].items())))
    for f in out["findings"]:
        print("  %-7s %s  %s\n          %s" % (f["severity"], f["code"], f["path"], f["message"]) + ("\n          fix: " + f["fix"] if f["fix"] else ""))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
