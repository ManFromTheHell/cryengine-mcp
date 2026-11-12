#!/usr/bin/env python3
"""World building as first-class tools: a new level, the terrain's shape, ground height at a point, terrain layers and their baked texture, vegetation, saving (ENGINE_LEARNING_PLAN F5 / F6; the goal:
an AI can build a game without any issues with tools and knowledge).

Everything here already existed as bridge verbs and Ashvale-specific scripts; this module makes them callable and SAFE for a level that is not Ashvale. Facts it rests on, each measured in the open editor:

  * `general.create_level('levels/<name>', resolution, unitSize, True)` is silent and instant (0.6 s), switches the editor to the new level (0 objects, flat at 32 m, water 16, maxHeight setting 1024) and writes
    Assets/levels/<name>/<name>.level plus Assets/levels/<name>.level.cryasset. The name WITHOUT the 'levels/' prefix puts the level directly under Assets/.
  * Leaving a level that has unsaved changes opens a MODAL 'Save Level?' prompt that blocks the bridge: these functions save the current level BEFORE they switch away, and `open_level` is explicit about it.
  * The raw heightmap is indexed [x, y] (terrain_gen.py); `set_heights_raw` also sets the terrain's max height setting to the ceiling it is given and reports how many values it clamped.
  * `bridge.vegetation.add` answers ok:true for a mesh that FAILED to load (the brush is created with the load error in its log, and no verb removes a brush): the mesh path is checked first.
  * A level made by create_level draws BLACK terrain until the terrain colour texture is compiled: `bake` runs generate_surface_texture and then export_texture.

Nothing here saves a level except save_level() and new_level()'s save of the level it leaves; every other function says so.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import math
import os
import re
import sys
import tempfile
import time

import numpy as np

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
LEVELS = os.path.join(ASSETS, "levels")
BACKUPS = os.path.join(HERE, "engine_data", "terrain_backups")
sys.path.insert(0, HERE)

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
RESOLUTIONS = (128, 256, 512, 1024, 2048, 4096)
KEEP_BACKUPS = 12


class WorldBuildError(RuntimeError):
    pass


def _send(cmd, timeout=120):
    import bridge_py
    try:
        r = bridge_py.send(cmd, timeout)
    except OSError as exc:
        raise WorldBuildError("the editor's bridge does not answer (%s): this needs Sandbox open (health_check says what is wrong)" % exc) from exc
    if not r.get("ok"):
        raise WorldBuildError("%s: %s" % (cmd.split()[0], str(r.get("error") or r)[:300]))
    out = r.get("output")
    if isinstance(out, str):
        try:
            return json.loads(out)
        except ValueError:
            return {"raw": out}
    return out


def _py(src, timeout=120):
    import bridge_py
    r = bridge_py.run_source(src, timeout)
    return (r.get("log") or "").strip()


# ---------------------------------------------------------------- levels
def current_level():
    out = _py("print(general.get_current_level_name())\nprint(general.get_objects_count())").splitlines()
    return {"name": out[0] if out else "", "objects": int(out[1]) if len(out) > 1 and out[1].isdigit() else None}


def save_level():
    """Save the level the editor has open (the editor's own general.save_level) -> {saved, level, objects}"""
    ok = _py("print(general.save_level())").splitlines()[-1:] == ["True"]
    c = current_level()
    return {"saved": ok, "level": c["name"], "objects": c["objects"]}


def new_level(name, resolution=1024, unit_size=1.0, terrain=True):
    """Create a new level and open it. The level that is open is SAVED first (leaving a modified level opens a blocking prompt). -> a report with the new terrain's numbers"""
    if not NAME.match(name or ""):
        raise WorldBuildError("level name %r: start with a letter, then letters, digits and _ (40 at most)" % name)
    if int(resolution) not in RESOLUTIONS:
        raise WorldBuildError("resolution is one of %s samples per side" % ", ".join(map(str, RESOLUTIONS)))
    if not 0.25 <= float(unit_size) <= 16:
        raise WorldBuildError("unit_size is 0.25 to 16 metres per sample")
    if os.path.exists(os.path.join(LEVELS, name)) or os.path.exists(os.path.join(LEVELS, name + ".level.cryasset")):
        raise WorldBuildError("a level named %r already exists under Assets/levels: choose another name (nothing is overwritten)" % name)
    before = current_level()
    saved = save_level() if before["name"] else {"saved": None}
    rc = _py("print(general.create_level('levels/%s', %d, %r, %s))" % (name, int(resolution), float(unit_size), "True" if terrain else "False"), 180).splitlines()[-1:]
    time.sleep(2.0)
    now = current_level()
    if now["name"] != name:
        raise WorldBuildError("create_level answered %s but the editor has %r open (was %r)" % (rc, now["name"], before["name"]))
    rep = {"level": name, "path": "Assets/levels/%s" % name, "left": before["name"], "left_saved": saved.get("saved"), "objects": now["objects"], "create_level_returned": rc[0] if rc else None}
    if terrain:
        rep["terrain"] = terrain_info()
        rep["notes"] = ["the terrain is flat (32 m) and BLACK until the colour texture is compiled: terrain_shape, then terrain_layers(action='bake')",
                        "not exported: export_level when the level is ready; save_level to keep editor changes"]
    return rep


def open_level(name, save_current=True, discard_current=False):
    """Open Assets/levels/<name>. save_current (default) saves the open level first; discard_current=True throws its unsaved changes away (for scratch levels) by answering the prompt No."""
    if not NAME.match(name or ""):
        raise WorldBuildError("level name %r is not a plain name" % name)
    if not os.path.isfile(os.path.join(LEVELS, name, name + ".level")):
        raise WorldBuildError("no level file Assets/levels/%s/%s.level" % (name, name))
    if save_current and not discard_current:
        save_level()
    import bridge_py
    cur = current_level()["name"]
    r = bridge_py.send("bridge.execute_command asset.edit levels/%s/%s.level" % (name, name), 240)
    if discard_current:
        for _ in range(6):                                       # a modified level asks 'Save Level?': No
            time.sleep(2.0)
            d = json.loads(bridge_py.send("bridge.dialog.list", 20).get("output", "{}"))
            if d.get("count") and "Save Level" in (d.get("activeModal") or ""):
                bridge_py.send("bridge.dialog.click no Save Level", 20)
                break
    for _ in range(40):
        time.sleep(1.0)
        if current_level()["name"] == name:
            break
    now = current_level()
    if now["name"] != name:
        raise WorldBuildError("the editor has %r open after asking for %r (was %r; the command answered %s)" % (now["name"], name, cur, str(r)[:80]))
    return {"level": name, "objects": now["objects"], "was": cur}


def delete_scratch_level(name):
    """Delete Assets/levels/<name> and its .cryasset: ONLY for names that start with zz_mcp_ (a test's throwaway level), and never while the editor has it open."""
    import shutil
    if not (name or "").startswith("zz_mcp_") or not NAME.match(name):
        raise WorldBuildError("only scratch levels named zz_mcp_* can be deleted here")
    if current_level()["name"] == name:
        raise WorldBuildError("the editor has %r open: open another level first" % name)
    gone = []
    for p in (os.path.join(LEVELS, name), os.path.join(LEVELS, name + ".level.cryasset")):
        if os.path.isdir(p):
            shutil.rmtree(p)
            gone.append(p)
        elif os.path.isfile(p):
            os.remove(p)
            gone.append(p)
    if os.path.isdir(BACKUPS):                                     # the heightmap backups terrain_shape made for this scratch level
        for f in sorted(os.listdir(BACKUPS)):
            if f.startswith(name + "_") and f.endswith(".r32"):
                os.remove(os.path.join(BACKUPS, f))
                gone.append(os.path.join(BACKUPS, f))
    return {"deleted": [os.path.relpath(g, PROJECT) for g in gone]}


# ---------------------------------------------------------------- terrain
def terrain_info():
    """-> {width, height, unit, max_height_setting, water_level, min, max, avg, size_meters} of the open level's terrain"""
    d = _send("bridge.terrain.info", 30)
    hm = d.get("heightmap") or {}
    if not hm.get("width"):
        raise WorldBuildError("the open level has no terrain (is a level loaded?)")
    return {"width": hm["width"], "height": hm["height"], "unit": hm["unitSize"], "max_height_setting": hm["maxHeightSetting"], "water_level": hm["waterLevel"],
            "min": hm["elevationMin"], "max": hm["elevationMax"], "avg": hm["elevationAvg"], "size_meters": hm["width"] * hm["unitSize"]}


def read_heights():
    """the open level's heightmap as float32 [x, y] (metres), and its terrain info"""
    info = terrain_info()
    fd, path = tempfile.mkstemp(prefix="heights_", suffix=".r32")
    os.close(fd)
    try:
        _send("bridge.terrain.get_heights_raw %s" % path.replace(os.sep, "/"), 60)
        a = np.fromfile(path, dtype="<f4")
    finally:
        os.remove(path)
    if a.size != info["width"] * info["height"]:
        raise WorldBuildError("the heightmap file holds %d values, the terrain %d x %d" % (a.size, info["width"], info["height"]))
    return a.reshape(info["width"], info["height"]), info


def terrain_hole(x, y, width, height, make=True):
    """Make (make=True) or fill a rectangular terrain hole, world metres (x, y = the corner with the smaller coordinates): the plugin verb bridge.terrain.hole
    (CHeightmap::MakeHole). The answer is read back: holes_in_rect must equal cells_in_rect for a made hole and be 0 for a filled one; is_hole confirms the centre. Not saved."""
    if width <= 0 or height <= 0:
        raise WorldBuildError("width and height are metres > 0")
    r = _send("bridge.terrain.hole %g %g %g %g %d" % (x, y, width, height, 1 if make else 0))
    c = _send("bridge.terrain.is_hole %g %g" % (x + width / 2.0, y + height / 2.0))
    r["centre_is_hole"] = bool(c.get("hole"))
    r["as_asked"] = (r.get("holes_in_rect") == r.get("cells_in_rect")) if make else (r.get("holes_in_rect") == 0)
    return r


def height_at(points):
    """Ground height in metres at world (x, y) points [[x, y], ...], bilinear on the heightmap (x and y in metres; the terrain starts at 0, 0) -> [{x, y, z, inside}]"""
    h, info = read_heights()
    u = float(info["unit"])
    out = []
    for p in points:
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError) as exc:
            raise WorldBuildError("a point is [x, y] in metres: %r" % (p,)) from exc
        fx, fy = x / u, y / u
        inside = 0 <= fx <= info["width"] - 1 and 0 <= fy <= info["height"] - 1
        cx, cy = min(max(fx, 0.0), info["width"] - 1.001), min(max(fy, 0.0), info["height"] - 1.001)
        x0, y0 = int(cx), int(cy)
        tx, ty = cx - x0, cy - y0
        z = (h[x0, y0] * (1 - tx) * (1 - ty) + h[x0 + 1, y0] * tx * (1 - ty) + h[x0, y0 + 1] * (1 - tx) * ty + h[x0 + 1, y0 + 1] * tx * ty)
        out.append({"x": x, "y": y, "z": round(float(z), 3), "inside": bool(inside)})
    return out


def _install(h, ceiling):
    fd, path = tempfile.mkstemp(prefix="heights_", suffix=".r32")
    os.close(fd)
    try:
        h.astype("<f4").tofile(path)
        return _send("bridge.terrain.set_heights_raw %s %g" % (path.replace(os.sep, "/"), ceiling), 120)
    finally:
        os.remove(path)


def backup_heights(label="before"):
    """copy the open level's heightmap to engine_data/terrain_backups (the newest KEEP_BACKUPS are kept) -> the backup's path"""
    h, info = read_heights()
    os.makedirs(BACKUPS, exist_ok=True)
    path = os.path.join(BACKUPS, "%s_%s_%dx%d_max%g.r32" % (current_level()["name"] or "level", time.strftime("%Y%m%d_%H%M%S"), info["width"], info["height"], info["max_height_setting"]))
    h.astype("<f4").tofile(path)
    # oldest first by modification time (names start with the level, so sorting by name would rank other levels' files over this one); the file just written is never removed
    old = sorted((os.path.join(BACKUPS, f) for f in os.listdir(BACKUPS) if f.endswith(".r32")), key=os.path.getmtime)
    for f in old[:-KEEP_BACKUPS]:
        if os.path.abspath(f) != os.path.abspath(path):
            os.remove(f)
    return path


def restore_heights(path):
    """put a backup_heights() file back; its size must match the open level's terrain"""
    if not os.path.isfile(path) or os.path.dirname(os.path.abspath(path)) != os.path.abspath(BACKUPS):
        raise WorldBuildError("only a backup written by this tool can be restored (engine_data/terrain_backups/*.r32): %s" % path)
    info = terrain_info()
    m = re.search(r"_(\d+)x(\d+)_max([0-9.]+)\.r32$", path)
    a = np.fromfile(path, dtype="<f4")
    if a.size != info["width"] * info["height"]:
        raise WorldBuildError("the backup holds %d values, the open terrain has %d x %d" % (a.size, info["width"], info["height"]))
    ceiling = float(m.group(3)) if m else max(info["max_height_setting"], float(a.max()) * 1.1)
    r = _install(a.reshape(info["width"], info["height"]), ceiling)
    return {"restored": os.path.basename(path), "reply": r}


def shape_terrain(style="hills", seed=1, base_height=None, max_height=None, flat_zones=None, erosion=0, preview=False):
    """Generate a heightmap for the open level's terrain (terrain_gen.generate) and install it. preview=True only reports the numbers. The old heightmap is backed up first (restore with restore_heights).
    Not saved. -> {stats, backup, installed, terrain, notes}"""
    import terrain_gen as TG
    info = terrain_info()
    if info["width"] != info["height"]:
        raise WorldBuildError("terrain_shape needs a square terrain; this one is %d x %d" % (info["width"], info["height"]))
    water = float(info["water_level"])
    if base_height is None:
        base_height = max(water - 8.0, 1.0) if style == "island" else water + 4.0
    try:
        h = TG.generate(info["width"], info["unit"], seed, style, float(base_height), None if max_height is None else float(max_height), flat_zones, int(erosion))
    except TG.TerrainGenError as exc:
        raise WorldBuildError(str(exc)) from exc
    stats = TG.stats(h, info["unit"], water, flat_zones)
    ceiling = max(float(info["max_height_setting"]), math.ceil(float(h.max()) * 1.1))
    rep = {"style": style, "seed": seed, "terrain": {"samples": info["width"], "unit_m": info["unit"], "size_m": info["size_meters"], "water_level": water}, "stats": stats,
           "ceiling_set_to": ceiling if not preview else None}
    if preview:
        rep["installed"] = False
        return rep
    rep["backup"] = backup_heights()
    reply = _install(h, ceiling)
    after = terrain_info()
    rep.update(installed=True, clamped_below_zero=reply.get("clampedBelowZero"), clamped_at_ceiling=reply.get("clampedAtCeiling"), now={"min": after["min"], "max": after["max"], "avg": after["avg"]})
    rep["notes"] = ["not saved (save_level); the ground texture is not baked yet (terrain_layers action='bake')", "put things on the ground with terrain_height(points) for their z", "restore the old ground: terrain_shape(restore=<backup path>)"]
    return rep


# ---------------------------------------------------------------- terrain layers
def layers_list():
    return _send("bridge.terrain.list_layers", 30)


def layers_add(material=None, texture=None, name=None, altitude=None, slope=None):
    """Create a terrain layer (the editor's terrain.create_layer), optionally give it a material (a .mtl under Assets, e.g. materials/ashvale/grass) or a texture, and its altitude (m) / slope (degrees) rules
    -> the layer list. Not saved."""
    before = layers_list()
    _py("terrain.create_layer()")
    after = layers_list()
    if len(after) != len(before) + 1:
        raise WorldBuildError("terrain.create_layer did not add a layer (%d before, %d after)" % (len(before), len(after)))
    idx = after[-1]["index"]
    _send("bridge.terrain.select_layer %d" % idx, 30)
    if material:
        _send("bridge.terrain.set_layer_material %d %s" % (idx, material), 60)
    if texture:
        _send("bridge.terrain.set_layer_texture %s" % texture, 60)
    if altitude or slope:
        a0, a1 = (altitude or [0, 1024])
        s0, s1 = (slope or [0, 90])
        _send("bridge.terrain.set_layer_params %d %g %g %g %g" % (idx, a0, a1, s0, s1), 30)
    return {"index": idx, "layers": layers_list()}


def bake(repeat=None, export=True, resolution=2048, multiplier=8):
    """Bake every textured layer into the terrain's colour texture (generate_surface_texture: reports coverage per layer) and compile it (export_texture: without it a created level's terrain is BLACK) -> report"""
    cmd = "bridge.terrain.generate_surface_texture" + ("" if repeat is None else " %g" % repeat)
    rep = {"surface": _send(cmd, 600)}
    if export:
        rep["export"] = _send("bridge.terrain.export_texture %d %g" % (int(resolution), float(multiplier)), 600)
    return rep


# ---------------------------------------------------------------- vegetation
def _mesh_exists(path):
    p = path.replace("\\", "/")
    if p.lower().startswith("%engine%/"):
        # the engine's own assets (EngineAssets/...) are inside engine/engine.pak under the engine install
        # CRYENGINE_ENGINE_ASSETS: the engine's `engine` folder; else <CRYENGINE_ENGINE_DIR>/engine; else the Launcher's default 5.7.1 install
        root = os.environ.get("CRYENGINE_ENGINE_ASSETS") or (os.path.join(os.environ["CRYENGINE_ENGINE_DIR"], "engine") if os.environ.get("CRYENGINE_ENGINE_DIR") else
                                                             os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1\engine"))
        if not os.path.isdir(root):
            raise WorldBuildError("the engine's asset folder %s does not exist: set CRYENGINE_ENGINE_ASSETS (or CRYENGINE_ENGINE_DIR) to the engine install" % root)
        try:
            import crypak
            return crypak.read_asset(root, p[9:]) is not None
        except Exception:                                      # noqa: BLE001
            return False
    if os.path.isfile(os.path.join(ASSETS, p)):
        return True
    try:
        import crypak
        gs = os.environ.get("GAMESDK_ASSETS") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Assets\crytek\cryengine-gamesdk-sample-project\5.7.1\gamesdk")
        return os.path.isdir(gs) and crypak.read_asset(gs, p) is not None
    except Exception:                                          # noqa: BLE001
        return False


def veg_list():
    return _send("bridge.vegetation.list", 60)


def veg_add(mesh, spacing=8.0, size=1.0, size_var=0.0, min_alt=None, max_alt=None, min_slope=None, max_slope=None):
    """Create a vegetation brush from a mesh (.cgf). spacing = the average distance in metres between instances (smaller = denser). The mesh path is CHECKED FIRST: the engine creates a brush for a mesh that
    failed to load and no tool removes a brush. -> {index, brush}"""
    if not _mesh_exists(mesh):
        raise WorldBuildError("no mesh %r under Assets, the GameSDK sample's paks or the engine's assets: nothing was added (the engine would have made a broken brush that cannot be removed)" % mesh)
    args = [mesh, "%g" % spacing, "%g" % size, "%g" % size_var]
    opt = [min_alt, max_alt, min_slope, max_slope]
    if any(v is not None for v in opt):
        args += ["%g" % (v if v is not None else d) for v, d in zip(opt, (0, 4096, 0, 90))]
    r = _send("bridge.vegetation.add " + " ".join(args), 60)
    if "Failed to load" in str(r.get("log", "")):
        raise WorldBuildError("the engine could not load %r: %s" % (mesh, str(r.get("log"))[:200]))
    return r


def veg_paint(index, x1, y1, x2, y2, clear=False):
    """Scatter (or with clear=True remove) a brush's instances over the world rectangle (metres), honouring its density, altitude and slope rules -> the verb's counts. Not saved."""
    for v in (x1, y1, x2, y2):
        if not isinstance(v, (int, float)):
            raise WorldBuildError("the rectangle is four numbers in world metres")
    return _send("bridge.vegetation.%s %d %g %g %g %g" % ("clear" if clear else "paint", int(index), x1, y1, x2, y2), 600)


def veg_params(index, **kv):
    """change a brush: size sizevar density bending airadius hideable castshadow elevmin elevmax slopemin slopemax hidden"""
    allowed = {"size", "sizevar", "density", "bending", "airadius", "hideable", "castshadow", "elevmin", "elevmax", "slopemin", "slopemax", "hidden"}
    bad = set(kv) - allowed
    if bad or not kv:
        raise WorldBuildError("params are some of %s (got %s)" % (", ".join(sorted(allowed)), ", ".join(sorted(bad)) or "none"))
    return _send("bridge.vegetation.params %d %s" % (int(index), " ".join("%s %g" % (k, float(v)) for k, v in kv.items())), 60)
