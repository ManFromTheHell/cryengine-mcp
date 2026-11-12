#!/usr/bin/env python3
"""The asset pipeline as functions the MCP server wraps: textures, materials, meshes, a voice preview. Each one writes into the
project's Assets folder through Crytek's own tools (rc.exe) and then lints what it wrote with asset_lint, so a tool call returns "what exists now
and whether the engine will accept it", not "ok".

    make_texture(surface, name, size, folder)         procedural diffuse + normal (+ height map) -> .dds through rc.exe
    write_material(path, submaterials)                an Illum multi-material .mtl, with the offset-bump / parallax flags when a height map is named
    build_mesh(spec)                                  boxes, bars, quads and cylinders in metres -> FBX -> rc.exe -> .cgf, and the material name read back
    preview_voice(text, voice)                        one line through Piper into a scratch WAV (nothing in Assets)

Nothing here downloads anything. Names are validated (letters, digits, underscore, dash, slash; no '..'); a file that exists is never replaced
unless overwrite=true. The Ashvale houses (house_kit.py), the weapons (weapon_kit.py) and the texture set (texture_lab.py) are the worked examples
of what these functions generalise.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import copy
import json
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
sys.path.insert(0, HERE)
import asset_lint as L          # noqa: E402
import jobs as J                # noqa: E402

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
FOLDER = re.compile(r"^[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+){0,6}$")
MAX_PARTS = 3000
MAX_COORD = 500.0


class PipelineError(ValueError):
    pass


def _safe_folder(folder, top):
    folder = (folder or "").strip().strip("/").replace("\\", "/")
    if not FOLDER.match(folder) or ".." in folder or not (folder == top or folder.startswith(top + "/")):
        raise PipelineError("folder %r must be a plain path under %s/ (letters, digits, _ and -; no '..')" % (folder, top))
    return folder


def _safe_name(name, what="name"):
    if not NAME.match(name or ""):
        raise PipelineError("%s %r must start with a letter and use only letters, digits and _ (40 characters at most)" % (what, name))
    return name


def _editor_note(overwrote):
    return ("Sandbox.exe is running and a file was REPLACED: CryPak caches loose assets, so the editor keeps showing the old one until it restarts "
            "(write a new file name instead, or restart the editor)" if overwrote and J.sandbox_running() else "")


# ---------------------------------------------------------------- textures
def texture_recipes():
    """{surface: {tile_metres, height_map}} read from texture_lab.py's source (so the server needs no numpy)."""
    text = open(_paths.script("texture_lab.py"), encoding="utf-8").read()
    tiles = dict((k, float(v)) for k, v in re.findall(r'"(\w+)": ([\d.]+)', re.search(r"^TILE_METRES = \{(.*?)\}", text, re.S | re.M).group(1)))
    displ = set(re.findall(r'"(\w+)"', re.search(r"^DISPL_SURFACES = \((.*?)\)", text, re.S | re.M).group(1)))
    return {s: {"tile_metres": tiles.get(s, 1.0), "height_map": s in displ} for s in J._source_keys("texture_lab.py", "SURFACES")}


def make_texture(surface, name, size=512, folder="textures/generated", overwrite=False):
    recipes = texture_recipes()
    if surface not in recipes:
        raise PipelineError("unknown surface %r; recipes: %s" % (surface, ", ".join(sorted(recipes))))
    _safe_name(name, "name")
    folder = _safe_folder(folder, "textures")
    if int(size) not in (128, 256, 512, 1024, 2048):
        raise PipelineError("size must be 128, 256, 512, 1024 or 2048")
    out = os.path.join(ASSETS, *folder.split("/"))
    existing = [f for f in (os.listdir(out) if os.path.isdir(out) else []) if f.lower().startswith(name.lower() + "_") and f.lower().endswith(".dds")]
    if existing and not overwrite:
        raise PipelineError("%s already holds %s: pick another name or pass overwrite=true" % (folder, existing))
    t0 = time.time()
    proc = J.run([sys.executable, _paths.script("texture_lab.py"), surface, "--as", name, "--size", str(int(size)), "--out", out],
                          capture_output=True, text=True, cwd=HERE, timeout=900)
    files = []
    for suffix in ("_diff", "_ddna", "_displ"):
        path = os.path.join(out, name + suffix + ".dds")
        if os.path.isfile(path):
            h = L.dds_header(path) or {}
            files.append({"path": "Assets/%s/%s%s.dds" % (folder, name, suffix), "bytes": os.path.getsize(path), "size": "%sx%s" % (h.get("w"), h.get("h")),
                          "format": L.DXGI.get(h.get("dxgi"), (h.get("fourcc") or b"").decode("latin1"))})
    if proc.returncode != 0 or len(files) < 2:
        raise PipelineError("texture_lab/rc.exe failed (exit %d): %s" % (proc.returncode, (proc.stdout + proc.stderr)[-900:]))
    lint = L.lint_paths(textures=[os.path.join(out, f["path"].rsplit("/", 1)[1]) for f in files])
    has_height = recipes[surface]["height_map"]
    return {"ok": lint["ok"], "seconds": round(time.time() - t0, 1), "files": files, "lint": lint["findings"],
            "tile_metres": recipes[surface]["tile_metres"],
            "material_texture": "%s/%s" % (folder, name),
            "height_map": has_height,
            "note": _editor_note(bool(existing)),
            "next": "write_material(...) with texture='%s/%s'%s; then build_mesh with tiles={'<material>': %g} so a texture tile covers %g m"
                    % (folder, name, ", height_depth=0.02" if has_height else "", recipes[surface]["tile_metres"], recipes[surface]["tile_metres"])}


# ---------------------------------------------------------------- materials
def _project_surface_types():
    """The surface types the project defines (Assets/libs/MaterialEffects/SurfaceTypes.xml): a material naming any other falls back to mat_default with a load warning
    (level_validate / lint L017). Read from the file so the list cannot drift from it; the fallback is the project's default set."""
    try:
        with open(os.path.join(ASSETS, "libs", "MaterialEffects", "SurfaceTypes.xml"), encoding="utf-8", errors="replace") as f:
            found = re.findall(r'<SurfaceType\s+name="(mat_[A-Za-z0-9_]+)"', f.read())
        if found:
            return tuple(found)
    except OSError:
        pass
    return ("mat_default", "mat_concrete", "mat_water", "mat_metal", "mat_vegetation", "mat_canopy", "mat_grass", "mat_wood", "mat_soil", "mat_flesh", "mat_rock",
            "mat_fabric", "mat_plastic", "mat_snow", "mat_glass", "mat_glass_unbreakable", "mat_wood_breakable")


MTL_SURFACES = _project_surface_types()


def _num(v, lo, hi, what):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise PipelineError("%s must be a number" % what)
    if not (lo <= f <= hi) or math.isnan(f):
        raise PipelineError("%s must be between %g and %g" % (what, lo, hi))
    return f


def _color(v, what):
    parts = [p for p in str(v).replace(" ", "").split(",") if p != ""]
    if len(parts) != 3:
        raise PipelineError("%s must be 'r,g,b' (0 to 4 each)" % what)
    return ",".join("%g" % _num(p, 0, 4, what) for p in parts)


def _clone_node(sm, name):
    """The ElementTree node of a sub-material that is a COPY of an existing material of the project (`from_material`: a path under Assets, e.g. one imported from the GameSDK sample with
    import_gamesdk_asset.py; 'path#SubName' picks one sub-material of a multi-material file). Its shader, flags, specular, texture slots and public parameters are kept exactly: those
    materials were tuned by their artists, and the gloss of a GameSDK texture lives in the alpha of its _ddna map (the engine multiplies it by Shininess/255), which a copy preserves.
    Optional overrides: tiles (TileU = TileV on every texture slot), tint ('r,g,b', multiplies Diffuse), shininess_scale (multiplies Shininess: below 1 = duller), surface_type."""
    src = str(sm["from_material"]).strip().replace("\\", "/")
    src, _, wanted = src.partition("#")                      # 'path#SubName': one sub-material of a multi-material file
    if src.lower().endswith(".mtl"):
        src = src[:-4]
    if not FOLDER.match(src) or ".." in src:
        raise PipelineError("from_material %r must be a path under Assets without a suffix" % src)
    path = os.path.join(ASSETS, *src.split("/")) + ".mtl"
    if not os.path.isfile(path):
        # a material of the GameSDK sample that has not been copied into the project (or was pruned after a preview): copy it, with its textures, out of the sample's own paks on this
        # machine (import_gamesdk_asset.py: a local copy, nothing is downloaded), then go on
        try:
            import import_gamesdk_asset
            import_gamesdk_asset.main([src + ".mtl"])
        except Exception as exc:                                  # noqa: BLE001
            raise PipelineError("from_material %r: %s.mtl is not under Assets and the GameSDK paks could not supply it (%s)" % (src, src, str(exc)[:80]))
        if not os.path.isfile(path):
            raise PipelineError("from_material %r: %s.mtl is neither under Assets nor in the GameSDK sample's paks" % (src, src))
    root = ET.parse(path).getroot()
    if wanted:
        node = next((m for m in root.iter("Material") if m.get("Shader") and m.get("Name") == wanted), None)
    else:
        node = root if root.get("Shader") else next((m for m in root.iter("Material") if m.get("Shader")), None)
    if node is None:
        raise PipelineError("from_material %r holds no material%s with a Shader" % (src, " named %r" % wanted if wanted else ""))
    node = copy.deepcopy(node)
    node.set("Name", name)
    for child in list(node):
        if child.tag == "SubMaterials":
            node.remove(child)
    if sm.get("tint"):
        t = [float(v) for v in _color(sm["tint"], "tint").split(",")]
        d = [float(v) for v in (node.get("Diffuse") or "1,1,1").split(",")]
        node.set("Diffuse", ",".join("%g" % (a * b) for a, b in zip(d, t)))
    if sm.get("shininess_scale") is not None:
        node.set("Shininess", "%g" % min(255.0, float(node.get("Shininess", 255)) * _num(sm["shininess_scale"], 0.0, 2.0, "shininess_scale")))
    if sm.get("surface_type"):
        if sm["surface_type"] not in MTL_SURFACES:
            raise PipelineError("surface_type %r: choose from %s" % (sm["surface_type"], ", ".join(MTL_SURFACES)))
        node.set("SurfaceType", sm["surface_type"])
    if sm.get("tiles") is not None:
        tile = _num(sm["tiles"], 0.02, 64, "tiles")
        for tex in node.iter("Texture"):
            if not tex.get("File"):
                continue
            mod = tex.find("TexMod")
            if mod is None:
                mod = ET.SubElement(tex, "TexMod", {"TexMod_RotateType": "0", "TexMod_TexGenType": "0", "TexMod_bTexGenProjected": "0"})
            mod.set("TileU", "%g" % tile)
            mod.set("TileV", "%g" % tile)
    return node, ("PARALLAX" in (node.get("StringGenMask") or ""))


def _clone_material_lines(sm, name):
    node, parallax = _clone_node(sm, name)
    ET.indent(node, space=" ", level=1)
    return ["  " + l if i else " " + l for i, l in enumerate(ET.tostring(node, encoding="unicode").splitlines())], parallax


def write_single_material(path, sm, overwrite=False):
    """A material FILE (not a multi-material) that is a copy of an existing one, with the overrides of `from_material` (see _clone_node): what a brush's custom material wants (a brush
    asks a multi-material file for the sub-material its mesh uses; one material file is unambiguous). path: 'materials/<folder>/<name>'."""
    path = path.strip().strip("/").replace("\\", "/")
    if path.lower().endswith(".mtl"):
        path = path[:-4]
    folder, _, name = path.rpartition("/")
    folder = _safe_folder(folder, "materials")
    _safe_name(name, "material name")
    target = os.path.join(ASSETS, *folder.split("/"), name + ".mtl")
    if os.path.exists(target) and not overwrite:
        raise PipelineError("%s exists: pass overwrite=true to replace it" % path)
    node, _ = _clone_node(sm, name)
    if "Name" in node.attrib:
        del node.attrib["Name"]
    ET.indent(node, space=" ", level=0)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    open(target, "w", newline="\n").write(ET.tostring(node, encoding="unicode") + "\n")
    lint = L.lint_paths(materials=[target])
    return {"ok": lint["ok"], "path": "Assets/%s/%s.mtl" % (folder, name), "lint": lint["findings"]}


def write_material(path, submaterials, overwrite=False):
    """path: 'materials/<folder>/<name>'. submaterials: [{name, texture?, tint?, shininess?, surface_type?, height_depth?, glass?, metal?, tiles?, from_material?}]; the order IS the
    sub-material index the mesh stores, so append, never reorder. metal=true: the tint becomes the SPECULAR colour and Diffuse goes near-black (see pbs_check).
    from_material='materials/generic/brick/brick_old' (or 'objects/.../loghouse#roof_tiles': one sub-material of a multi-material file): the sub-material is a COPY of that project material (shader, flags, specular, textures, public parameters kept; overrides: tiles, tint, shininess_scale, surface_type): use it to
    reuse the GameSDK sample's photographic materials (import_gamesdk_asset.py copies one with its textures) instead of inventing a look."""
    path = path.strip().strip("/").replace("\\", "/")
    if path.lower().endswith(".mtl"):
        path = path[:-4]
    folder, _, name = path.rpartition("/")
    folder = _safe_folder(folder, "materials")
    _safe_name(name, "material name")
    if not submaterials or len(submaterials) > 32:
        raise PipelineError("submaterials: 1 to 32 entries")
    target = os.path.join(ASSETS, *folder.split("/"), name + ".mtl")
    if os.path.exists(target) and not overwrite:
        raise PipelineError("%s exists: pass overwrite=true to replace it (a mesh stores sub-material INDICES: keep the order)" % path)
    names = L.Names()
    lines = ['<Material MtlFlags="524544">', ' <SubMaterials>']
    seen, uses_height = set(), False
    for i, sm in enumerate(submaterials):
        sub = _safe_name(sm.get("name"), "submaterial %d name" % i)
        if sub in seen:
            raise PipelineError("submaterial name %r twice" % sub)
        seen.add(sub)
        if sm.get("from_material"):
            cloned, parallax = _clone_material_lines(sm, sub)
            lines += cloned
            uses_height = uses_height or parallax
            continue
        surface = sm.get("surface_type", "mat_default")
        if surface not in MTL_SURFACES:
            raise PipelineError("surface_type %r: choose from %s" % (surface, ", ".join(MTL_SURFACES)))
        shin = _num(sm.get("shininess", 80), 0, 255, "shininess")
        if sm.get("glass"):
            # nearly clear and barely reflective (a gloss-255 version mirrored the sky: a pale panel nobody could see through)
            lines += ['  <Material Name="%s" MtlFlags="524416" Shader="Illum" GenMask="0" StringGenMask="" SurfaceType="mat_glass" MatTemplate="" '
                      'Diffuse="%s" Specular="0.04,0.04,0.04" Emissive="0,0,0" Shininess="40" Opacity="%g" LayerAct="1">'
                      % (sub, _color(sm.get("tint", "0.55,0.62,0.66"), "tint"), _num(sm.get("opacity", 0.08), 0.02, 0.9, "opacity")),
                      '   <Textures><Texture Map="Diffuse" File="%engine%/EngineAssets/TextureMsg/color_white.dds"/></Textures>',
                      '   <PublicParams SSSIndex="0"/>', '  </Material>']
            continue
        tex = sm.get("texture")
        depth = sm.get("height_depth")
        tiles = _num(sm.get("tiles", 1.0), 0.05, 64, "tiles")
        tint = _color(sm.get("tint", "1,1,1"), "tint")
        slots = []
        if tex:
            tex = tex.strip().replace("\\", "/")
            if not FOLDER.match(tex) or ".." in tex or not tex.startswith("textures/"):
                raise PipelineError("texture %r must be 'textures/<folder>/<name>' without a suffix (make_texture's material_texture)" % tex)
            for slot, suffix in (("Diffuse", "diff"), ("Bumpmap", "ddna")):
                if not names.exists("%s_%s.tif" % (tex, suffix)):
                    raise PipelineError("%s_%s is not compiled yet (no .dds found): run make_texture first" % (tex, suffix))
                slots.append((slot, "%s_%s.tif" % (tex, suffix)))
            if depth is not None:
                if not names.exists("%s_displ.tif" % tex):
                    raise PipelineError("height_depth was given but %s_displ has no compiled .dds (only some surfaces have height maps; see texture_recipes)" % tex)
                slots.append(("Heightmap", "%s_displ.tif" % tex))
        elif depth is not None:
            raise PipelineError("height_depth needs a texture")
        pom = depth is not None
        uses_height = uses_height or pom
        # Illum.ext: OFFSET_BUMP_MAPPING 0x20000 (Low/Medium spec), PARALLAX_OCCLUSION_MAPPING 0x8000000 (High); both on, as the manual says. 80000081 is the
        # GameSDK materials' own mask (bump + subsurface), so the shader cache already holds its permutations.
        gen = ("88020081" if pom else "80000081") if tex else "80000081"
        sgm = ("%ALLOW_SILHOUETTE_POM%BUMP_MAP%OFFSET_BUMP_MAPPING%PARALLAX_OCCLUSION_MAPPING%SUBSURFACE_SCATTERING" if pom
               else "%ALLOW_SILHOUETTE_POM%BUMP_MAP%SUBSURFACE_SCATTERING")
        if not tex:
            gen, sgm = "0", ""
        # a metal (the manual's PBS rules, pbs_check S005): a near-black Diffuse and the colour in the SPECULAR (linear; iron 0.55, gold 1.0,0.71,0.28: sRGB 196 / 255,219,145);
        # every other material keeps the dielectric Specular 0.0212 (sRGB 40) and its colour in Diffuse
        diffuse, specular = (("0.02,0.02,0.02", tint) if sm.get("metal") else (tint, "0.021219011,0.021219011,0.021219011"))
        lines.append('  <Material Name="%s" MtlFlags="524416" Shader="Illum" GenMask="%s" StringGenMask="%s" SurfaceType="%s" MatTemplate="" Diffuse="%s" '
                     'Specular="%s" Emissive="0,0,0" Shininess="%g" Opacity="1" LayerAct="1">' % (sub, gen, sgm, surface, diffuse, specular, shin))
        lines.append('   <Textures>')
        if not slots:
            lines.append('    <Texture Map="Diffuse" File="%engine%/EngineAssets/TextureMsg/color_white.dds"/>')
        for slot, f in slots:
            if tiles != 1.0:
                lines.append('    <Texture Map="%s" File="%s"><TexMod TexMod_RotateType="0" TexMod_TexGenType="0" TexMod_bTexGenProjected="0" TileU="%g" TileV="%g"/></Texture>'
                             % (slot, f, tiles, tiles))
            else:
                lines.append('    <Texture Map="%s" File="%s"/>' % (slot, f))
        lines.append('   </Textures>')
        lines.append('   <PublicParams SSSIndex="0" IndirectColor="0.25,0.25,0.25"%s/>' % (
            ' PomDisplacement="%g" HeightBias="0.5" SelfShadowStrength="1"' % _num(depth, 0.001, 0.2, "height_depth") if pom else ""))
        lines.append('  </Material>')
    lines += [' </SubMaterials>', '</Material>', '']
    os.makedirs(os.path.dirname(target), exist_ok=True)
    existed = os.path.exists(target)
    open(target, "w", newline="\n").write("\n".join(lines))
    lint = L.lint_paths(materials=[target])
    return {"ok": lint["ok"], "path": "Assets/%s/%s.mtl" % (folder, name), "game_path": "%s/%s" % (folder, name), "submaterials": [s["name"] for s in submaterials],
            "lint": lint["findings"], "note": _editor_note(existed),
            "shader_cache": ("these materials enable parallax/offset bump mapping: if the permutation is new to the game, the shipped shader cache is stale. "
                             "asset_lint warns (L011) and the shader tour is the gate: run the shader_cache job after the asset work, then the shaders test group"
                             if uses_height else "the flag set is one the shipped cache already holds (GenMask 80000081); the shader tour is still the gate"),
            "next": "build_mesh(... material_file='%s/%s', materials=%s)" % (folder, name, [s["name"] for s in submaterials])}


# ---------------------------------------------------------------- meshes
def _vec(v, what):
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise PipelineError("%s must be [x, y, z] in metres" % what)
    return tuple(_num(c, -MAX_COORD, MAX_COORD, what) for c in v)


def _cylinder(mesh, c, radius, height, sides, material, tile_metres):
    n = max(3, min(32, int(sides)))
    pts = [(c[0] + radius * math.cos(2 * math.pi * i / n), c[1] + radius * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
    circ = 2 * math.pi * radius
    for i in range(n):
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        u0, u1 = circ * i / n, circ * (i + 1) / n
        mesh.quad((x0, y0, c[2]), (x1, y1, c[2]), (x1, y1, c[2] + height), (x0, y0, c[2] + height), material, [(u0, c[2]), (u1, c[2]), (u1, c[2] + height), (u0, c[2] + height)])
    top = [(x, y, c[2] + height) for x, y in pts[:n]]
    mesh.poly(top, material, [(x, y) for x, y, _ in top])
    bottom = [(x, y, c[2]) for x, y in pts[:n]]
    mesh.poly(list(reversed(bottom)), material, [(x, y) for x, y, _ in reversed(bottom)])


def _ear_clip(pts):
    """Triangulate a simple polygon given as [(a, b)] in a plane (either winding): -> [(i, j, k)] index triples, each counter-clockwise in (a, b)."""
    n = len(pts)
    if n < 3:
        return []
    area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n)) / 2.0
    order = list(range(n)) if area > 0 else list(range(n - 1, -1, -1))

    def cross2(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, c):
        return cross2(a, b, p) >= -1e-12 and cross2(b, c, p) >= -1e-12 and cross2(c, a, p) >= -1e-12

    tris = []
    guard = 0
    while len(order) > 3 and guard < 10000:
        guard += 1
        clipped = False
        for k in range(len(order)):
            i0, i1, i2 = order[k - 1], order[k], order[(k + 1) % len(order)]
            a, b, c = pts[i0], pts[i1], pts[i2]
            if cross2(a, b, c) <= 1e-12:
                continue
            if any(inside(pts[j], a, b, c) for j in order if j not in (i0, i1, i2)):
                continue
            tris.append((i0, i1, i2))
            order.pop(k)
            clipped = True
            break
        if not clipped:
            # a degenerate sliver (collinear points): drop the flattest vertex and go on
            k = min(range(len(order)), key=lambda q: abs(cross2(pts[order[q - 1]], pts[order[q]], pts[order[(q + 1) % len(order)]])))
            order.pop(k)
    if len(order) == 3:
        tris.append(tuple(order))
    return tris


def _poly(mesh, pts, normal, material):
    """A flat (or gently warped) polygon with the given facing: ear-clipped in the plane perpendicular to `normal`'s dominant axis, UVs = positions in metres on that plane (so
    neighbouring floors and ceilings continue one pattern)."""
    nx, ny, nz = (abs(normal[0]), abs(normal[1]), abs(normal[2]))
    axes = (1, 2) if nx >= ny and nx >= nz else ((0, 2) if ny >= nz else (0, 1))
    flat = [(p[axes[0]], p[axes[1]]) for p in pts]
    for i, j, k in _ear_clip(flat):
        tri = [pts[i], pts[j], pts[k]]
        g = ((tri[1][1] - tri[0][1]) * (tri[2][2] - tri[0][2]) - (tri[1][2] - tri[0][2]) * (tri[2][1] - tri[0][1]),
             (tri[1][2] - tri[0][2]) * (tri[2][0] - tri[0][0]) - (tri[1][0] - tri[0][0]) * (tri[2][2] - tri[0][2]),
             (tri[1][0] - tri[0][0]) * (tri[2][1] - tri[0][1]) - (tri[1][1] - tri[0][1]) * (tri[2][0] - tri[0][0]))
        if g[0] * normal[0] + g[1] * normal[1] + g[2] * normal[2] < 0:
            tri = [tri[0], tri[2], tri[1]]
        mesh.poly(tri, material, [(q[axes[0]], q[axes[1]]) for q in tri])


def _frustum(mesh, c, r0, r1, height, sides, material, tile_metres):
    """A tapered cylinder: radius r0 at the bottom (c is the centre of the bottom), r1 at the top; a cap on each end that has a radius."""
    n = max(3, min(32, int(sides)))
    z0, z1 = c[2], c[2] + height
    ring0 = [(c[0] + r0 * math.cos(2 * math.pi * i / n), c[1] + r0 * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
    ring1 = [(c[0] + r1 * math.cos(2 * math.pi * i / n), c[1] + r1 * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
    circ = 2 * math.pi * max(r0, r1, 0.01)
    for i in range(n):
        u0, u1 = circ * i / n, circ * (i + 1) / n
        a, b = ring0[i], ring0[i + 1]
        d, e = ring1[i], ring1[i + 1]
        if r1 < 1e-6:
            mesh.poly([(a[0], a[1], z0), (b[0], b[1], z0), (c[0], c[1], z1)], material, [(u0, z0), (u1, z0), ((u0 + u1) / 2.0, z1)])
        elif r0 < 1e-6:
            mesh.poly([(c[0], c[1], z0), (e[0], e[1], z1), (d[0], d[1], z1)], material, [((u0 + u1) / 2.0, z0), (u1, z1), (u0, z1)])
        else:
            mesh.quad((a[0], a[1], z0), (b[0], b[1], z0), (e[0], e[1], z1), (d[0], d[1], z1), material, [(u0, z0), (u1, z0), (u1, z1), (u0, z1)])
    if r1 > 1e-6:
        top = [(x, y, z1) for x, y in ring1[:n]]
        mesh.poly(top, material, [(x, y) for x, y, _ in top])
    if r0 > 1e-6:
        bottom = [(x, y, z0) for x, y in ring0[:n]]
        mesh.poly(list(reversed(bottom)), material, [(x, y) for x, y, _ in reversed(bottom)])


def build_mesh(spec, overwrite=False):
    """spec = {name, folder?, material_file, materials: [names in index order], tiles?: {material: metres per texture tile}, parts: [...]}
    part = one of {box: [x0,y0,z0,x1,y1,z1], faces?}, {bar: {a, b, half_width, half_thickness, plane_normal?}}, {quad: [p0,p1,p2,p3], flip?, uv?},
    {cylinder: {center: [x,y,z_bottom], radius, height, sides?}}, each with a "material". Metres, x right, y forward, z up, faces counter-clockwise from outside."""
    import mesh_fbx as M
    name = _safe_name(spec.get("name"), "name")
    folder = _safe_folder(spec.get("folder", "objects/generated"), "objects")
    if "_lod" in (folder + "/" + name).lower():
        raise PipelineError("%s/%s: the engine refuses to load any mesh whose path contains '_lod' (ObjMan.cpp: 'lod meshes can be loaded only internally') and draws its default sphere; rename it" % (folder, name))
    material_file = (spec.get("material_file") or "").strip().strip("/").replace("\\", "/")
    if material_file.lower().endswith(".mtl"):
        material_file = material_file[:-4]
    mf_folder, _, mf_name = material_file.rpartition("/")
    _safe_folder(mf_folder, "materials")
    _safe_name(mf_name, "material_file name")
    materials = list(spec.get("materials") or [])
    if not materials or len(materials) > 32 or len(set(materials)) != len(materials):
        raise PipelineError("materials: 1 to 32 distinct names, in the order of the .mtl's sub-materials (the .cgf stores the index)")
    for m in materials:
        _safe_name(m, "material")
    parts = list(spec.get("parts") or [])
    if not parts or len(parts) > MAX_PARTS:
        raise PipelineError("parts: 1 to %d" % MAX_PARTS)
    tiles = {k: _num(v, 0.05, 64, "tile metres") for k, v in (spec.get("tiles") or {}).items()}
    out_dir = os.path.join(ASSETS, *folder.split("/"))
    target = os.path.join(out_dir, name + ".cgf")
    if os.path.exists(target) and not overwrite:
        raise PipelineError("%s/%s.cgf exists: pass overwrite=true to replace it" % (folder, name))
    kinds = ("box", "bar", "quad", "cylinder", "poly", "frustum")
    n_lods = spec.get("lods", 0)
    if not isinstance(n_lods, int) or isinstance(n_lods, bool) or not 0 <= n_lods <= 3:
        raise PipelineError("lods: 0 to 3 extra levels of detail (the engine supports 6 in all)")
    for i, part in enumerate(parts):
        d = part.get("detail")
        if d is not None and (not isinstance(d, int) or isinstance(d, bool) or not 1 <= d <= 3):
            raise PipelineError("part %d: detail is 1 to 3 (the part is left out of that level of detail and every coarser one)" % i)

    def fill(mesh, level):
        """The parts of LOD `level` (0 = the full mesh): a part with detail=N is left out of LOD N and coarser; a cylinder loses half its sides per level (not below 6)."""
        for i, part in enumerate(parts):
            if level and part.get("detail") is not None and part["detail"] <= level:
                continue
            mat = part.get("material")
            if mat not in materials:
                raise PipelineError("part %d: material %r is not in materials %s" % (i, mat, materials))
            which = [k for k in kinds if k in part]
            if len(which) != 1:
                raise PipelineError("part %d: exactly one of %s" % (i, ", ".join(kinds)))
            k = which[0]
            if k == "box":
                b = part["box"]
                if not isinstance(b, (list, tuple)) or len(b) != 6:
                    raise PipelineError("part %d: box is [x0,y0,z0,x1,y1,z1]" % i)
                v = [_num(c, -MAX_COORD, MAX_COORD, "box") for c in b]
                if not (v[0] < v[3] and v[1] < v[4] and v[2] < v[5]):
                    raise PipelineError("part %d: box needs x0<x1, y0<y1, z0<z1" % i)
                mesh.box(*v, material=mat, faces=part.get("faces", "all"))
            elif k == "bar":
                b = part["bar"]
                import weapon_kit as W        # only the "bar" part needs weapon_kit (prism); the other parts build without it
                W.prism(mesh, _vec(b["a"], "bar a"), _vec(b["b"], "bar b"), _num(b["half_width"], 0.001, 50, "half_width"), _num(b["half_thickness"], 0.001, 50, "half_thickness"),
                        mat, _vec(b.get("plane_normal", [0, 1, 0]), "plane_normal"))
            elif k == "quad":
                q = [_vec(p, "quad point") for p in part["quad"]] if len(part["quad"]) == 4 else None
                if q is None:
                    raise PipelineError("part %d: quad is four [x,y,z] points, counter-clockwise from outside" % i)
                w = math.dist(q[0], q[1])
                h = math.dist(q[0], q[3])
                uv = [tuple(p) for p in part["uv"]] if part.get("uv") else M.world_uv(w, h)
                mesh.quad(q[0], q[1], q[2], q[3], mat, uv, flip=bool(part.get("flip")))
            elif k == "poly":
                pl = part["poly"]
                pts = [_vec(q, "poly point") for q in pl["points"]]
                if len(pts) < 3:
                    raise PipelineError("part %d: a poly needs 3 or more points" % i)
                _poly(mesh, pts, _vec(pl.get("normal", [0, 0, 1]), "poly normal"), mat)
            elif k == "frustum":
                f = part["frustum"]
                sides = int(f.get("sides", 12))
                for _ in range(level):
                    sides = max(6, sides // 2)
                _frustum(mesh, _vec(f["center"], "frustum center"), _num(f["r0"], 0.0, 100, "r0"), _num(f["r1"], 0.0, 100, "r1"), _num(f["height"], 0.001, 200, "height"), sides, mat, tiles.get(mat, 1.0))
            else:
                c = part["cylinder"]
                sides = int(c.get("sides", 12))
                for _ in range(level):
                    sides = max(6, sides // 2)
                _cylinder(mesh, _vec(c["center"], "cylinder center"), _num(c["radius"], 0.001, 100, "radius"), _num(c["height"], 0.001, 200, "height"), sides, mat, tiles.get(mat, 1.0))

    mesh = M.Mesh(name, tiles)
    fill(mesh, 0)
    lod_meshes = []
    for level in range(1, n_lods + 1):
        lm = M.Mesh("$lod%d_%s" % (level, name), tiles)
        lm.parent = name                                   # the engine's FBX importer takes a child named $lodN_<name> as that LOD of the mesh it hangs under (manual: LODs, Blender LoDs)
        fill(lm, level)
        if not lm.polys:
            raise PipelineError("LOD %d would be empty: every part has detail <= %d" % (level, level))
        lod_meshes.append(lm)
    t0 = time.time()
    try:
        path = M.build_cgf(name, [mesh] + lod_meshes, materials, out_dir, material_file=material_file)
    except RuntimeError as exc:
        raise PipelineError(str(exc))
    stored = L.cgf_material(path)
    lo, hi = mesh.bounds()
    names = L.Names()
    mtl_exists = names.exists(material_file + ".mtl")
    lint = L.lint_paths(meshes=[path])
    problems = []
    if not stored or stored[0].replace("\\", "/").lower() != material_file.lower():
        problems.append("the .cgf names material %r, expected %r" % (stored[0] if stored else None, material_file))
    # the size of the COMPILED mesh (what the engine loads) against what the spec described: a unit or axis slip in the import shows here
    ext = L.cgf_extent(path)
    want = sorted(round(h - l, 3) for l, h in zip(lo, hi))
    if not ext:
        problems.append("the compiled .cgf has no mesh chunk")
        got = want
    else:
        got = sorted(round(h - l, 3) for l, h in zip(ext[0], ext[1]))
        if any(abs(a - b) > max(0.01, 0.01 * b) for a, b in zip(got, want)):
            problems.append("the compiled mesh measures %s m but the spec describes %s m (sorted sides)" % (got, want))
    # the levels of detail the COMPILED file carries (nodes $lodN_<name> under the base node): each wanted one must be there, hang under the base mesh and cut the triangles
    lods_info = L.cgf_lods(path)
    warnings = []
    have = {r["level"]: r for r in lods_info}
    for level in range(0, n_lods + 1):
        if level not in have:
            problems.append("the compiled .cgf has no LOD %d (RC dropped the $lod%d_%s node)" % (level, level, name))
    for level in range(1, n_lods + 1):
        if level in have and 0 in have:
            if have[level]["parent"] != have[0]["id"]:
                problems.append("LOD %d does not hang under the base mesh" % level)
            prev = have.get(level - 1)
            if prev and have[level]["triangles"] >= prev["triangles"]:
                problems.append("LOD %d has %d triangles, not fewer than LOD %d (%d)" % (level, have[level]["triangles"], level - 1, prev["triangles"]))
            elif prev and have[level]["triangles"] > 0.67 * prev["triangles"]:
                warnings.append("LOD %d keeps %d%% of LOD %d's triangles: the manual wants each step to save about 50%% (a warning below 33%% saved)" % (level, round(100.0 * have[level]["triangles"] / prev["triangles"]), level - 1))
    shown = (ext[0], ext[1]) if ext else (lo, hi)
    return {"ok": lint["ok"] and not problems and mtl_exists, "path": "Assets/%s/%s.cgf" % (folder, name), "game_path": "%s/%s.cgf" % (folder, name),
            "seconds": round(time.time() - t0, 1), "polygons": len(mesh.polys), "vertices_in_cgf": ext[2] if ext else 0,
            "lods": [{"level": r["level"], "triangles": r["triangles"], "vertices": r["vertices"], "node": r["node"]} for r in lods_info], "lod_warnings": warnings,
            "bounds_m": {"min": [round(c, 3) for c in shown[0]], "max": [round(c, 3) for c in shown[1]], "size": [round(h - l, 3) for l, h in zip(*shown)], "from": "the compiled .cgf"},
            "material_named_in_cgf": stored[0] if stored else None, "highest_material_id_used": stored[1] if stored else None,
            "material_file_exists": mtl_exists, "problems": problems + ([] if mtl_exists else ["%s.mtl does not exist yet: write_material it (the mesh draws flat grey until then)" % material_file]),
            "lint": lint["findings"], "note": _editor_note(os.path.exists(target) and overwrite),
            "next": "place it in the level (the editor's object tools, or Prefab=%r in a layer), then shoot_game to look at it" % ("%s/%s.cgf" % (folder, name))}


# ---------------------------------------------------------------- voices
def preview_voice(text, voice="north", length_scale=1.0):
    """One line through Piper into job_runs/previews/<n>.wav (not into Assets): to hear a voice or a wording before rendering the cast."""
    models = dict(re.findall(r'"(\w+)": "([^"]+\.onnx)"', re.search(r"^PIPER_MODELS = \{(.*?)\}", open(_paths.script("make_voices.py"), encoding="utf-8").read(), re.S | re.M).group(1)))
    if voice not in models:
        raise PipelineError("voice %r: choose from %s" % (voice, ", ".join(sorted(models))))
    text = (text or "").strip()
    if not 2 <= len(text) <= 600:
        raise PipelineError("text: 2 to 600 characters")
    piper_dir = os.path.join(HERE, "tts_piper")
    exe = os.path.join(piper_dir, "piper", "piper.exe")
    if not os.path.isfile(exe):
        raise PipelineError("Piper is not installed (tts_piper). Installing it is a download (piper_windows_amd64.zip, 22,477,236 bytes, from "
                            "github.com/rhasspy/piper release 2023.11.14-2; a voice .onnx of 63,201,294 bytes from huggingface.co/rhasspy/piper-voices): ask the user "
                            "first, naming those files and sizes.")
    scale = _num(length_scale, 0.5, 2.0, "length_scale")
    out_dir = os.path.join(J.JOBS, "previews")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "%s_%s.wav" % (time.strftime("%H%M%S"), voice))
    t0 = time.time()
    proc = J.run([exe, "--model", os.path.join(piper_dir, models[voice]), "--output_file", out, "--length_scale", "%.3f" % scale, "--sentence_silence", "0.15"],
                          input=text.encode("utf-8"), capture_output=True, timeout=120)
    if proc.returncode != 0 or not os.path.isfile(out):
        raise PipelineError("piper failed: %s" % proc.stderr.decode("utf-8", "replace")[-400:])
    import wave
    w = wave.open(out)
    seconds = w.getnframes() / float(w.getframerate())
    w.close()
    return {"ok": True, "path": out, "speech_seconds": round(seconds, 2), "render_seconds": round(time.time() - t0, 2),
            "note": "Automated measurement only; judge the sound by listening. To put lines into the game, run the `voices` job (it renders every line of libs/ashvale/dialogue.xml)."}


if __name__ == "__main__":
    print(__doc__)
    print(json.dumps(texture_recipes(), indent=1))
