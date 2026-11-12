#!/usr/bin/env python3
"""Self-test of terrain_gen.py (offline) and world_build.py (live, on a scratch level that is deleted again): a new level, the terrain's shape and layout, ground height at a point, backup and restore, terrain layers
and their bake, vegetation (including the refusal of a mesh that does not exist), saving, and getting back to the level the editor had open.

    test_world_build.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import terrain_gen as TG
import world_build as W

results = []
SCRATCH = "zz_mcp_wb_level"


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:70]))


def ctc_size():
    """bytes of terrain/cover.ctc (the compiled terrain colour) inside the scratch level's level.pak, or 0 if it is not there"""
    import zipfile
    try:
        with zipfile.ZipFile(os.path.join(W.LEVELS, SCRATCH, "level.pak")) as z:
            return z.getinfo("terrain/cover.ctc").file_size
    except (OSError, KeyError, zipfile.BadZipFile):
        return 0


def raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return True
    return False


def offline():
    a = TG.generate(128, 4.0, 3, "hills", 20.0)
    check("generate: a float32 [128, 128] array of finite metres", a.dtype == np.float32 and a.shape == (128, 128) and np.isfinite(a).all(), (a.dtype, a.shape))
    check("the same seed gives the same ground, another seed another", np.array_equal(a, TG.generate(128, 4.0, 3, "hills", 20.0)) and not np.array_equal(a, TG.generate(128, 4.0, 4, "hills", 20.0)), "")
    styles = {s: TG.generate(128, 4.0, 3, s, 20.0) for s in TG.STYLES}
    check("the four styles are four different grounds", len({float(v.mean()) for v in styles.values()}) == 4, {k: round(float(v.mean()), 1) for k, v in styles.items()})
    check("flat is flat (under 5 cm of ripple); hills stay between base and base + 6% of the extent; mountains rise higher than hills", np.ptp(styles["flat"]) < 0.1 and 20.0 <= styles["hills"].min() and styles["hills"].max() <= 20.0 + 0.06 * 512 + 0.1 and styles["mountains"].max() > styles["hills"].max(),
          (round(float(np.ptp(styles["flat"])), 3), round(float(styles["hills"].max()), 1), round(float(styles["mountains"].max()), 1)))
    check("hills at the default relief are gentle: under 5% of the ground steeper than 30 degrees", TG.slope_fraction(styles["hills"], 4.0) < 0.05, TG.slope_fraction(styles["hills"], 4.0))
    check("the island is below the water at its edge and above it in the middle (base 8 m, water 16 m)", (lambda i: i[0, 0] < 16 < i[64, 64])(TG.generate(128, 4.0, 3, "island", 8.0, 60.0)), "")
    zone = [{"x": 100.0, "y": 200.0, "radius": 24.0, "height": 40.0}]
    z = TG.generate(128, 4.0, 3, "mountains", 20.0, None, zone)
    check("LAYOUT: a flat zone at world x=100, y=200 (unit 4) is at index [25, 50] of the array: 40 m there, and NOT at [50, 25]", abs(float(z[25, 50]) - 40.0) < 0.01 and abs(float(z[50, 25]) - 40.0) > 0.5, (float(z[25, 50]), float(z[50, 25])))
    st = TG.stats(z, 4.0, 16.0, zone)
    check("stats: the zone's height is 40 with no spread, and the report names min, max, mean, steep percent and the water share", st["flat_zones"][0]["height"] == 40.0 and st["flat_zones"][0]["spread"] < 0.01 and {"min", "max", "mean", "steeper_than_30_degrees_percent", "below_water_percent"} <= set(st), st)
    auto = TG.generate(128, 4.0, 3, "hills", 20.0, None, [{"x": 100.0, "y": 200.0, "radius": 24.0}])
    check("a zone without a height takes the ground's mean there (flat, inside the range)", 20.0 <= auto[25, 50] <= 36.0 and TG.stats(auto, 4.0, None, [{"x": 100.0, "y": 200.0, "radius": 24.0}])["flat_zones"][0]["spread"] < 0.01, float(auto[25, 50]))
    e0, e1 = TG.generate(64, 4.0, 3, "mountains", 20.0), TG.generate(64, 4.0, 3, "mountains", 20.0, None, None, 4000)
    check("erosion changes the ground and keeps it finite and inside the range", not np.array_equal(e0, e1) and np.isfinite(e1).all() and e1.min() >= 0, round(float(np.abs(e1 - e0).mean()), 3))
    check("the default relief scales with the terrain: 1,024 m of mountains rise higher than 256 m of them", TG.generate(256, 4.0, 3, "mountains", 20.0).max() > TG.generate(64, 4.0, 3, "mountains", 20.0).max(), "")
    for label, fn in (("an unknown style", lambda: TG.generate(64, 4.0, 1, "volcano", 20.0)), ("a size under 16", lambda: TG.generate(8, 4.0, 1, "hills", 20.0)), ("base above max", lambda: TG.generate(64, 4.0, 1, "hills", 50.0, 40.0)),
                      ("a zone outside the terrain", lambda: TG.generate(64, 1.0, 1, "hills", 20.0, None, [{"x": 900, "y": 900, "radius": 5}])), ("a zone without a radius", lambda: TG.generate(64, 1.0, 1, "hills", 20.0, None, [{"x": 5, "y": 5}])),
                      ("too many droplets", lambda: TG.generate(64, 1.0, 1, "hills", 20.0, None, None, 10 ** 9))):
        check("refused: %s" % label, raises(fn, TG.TerrainGenError), "")
    check("names and sizes are refused before the editor is asked", raises(lambda: W.new_level("1bad"), W.WorldBuildError) and raises(lambda: W.new_level("fine", resolution=100), W.WorldBuildError) and raises(lambda: W.new_level("fine", unit_size=99), W.WorldBuildError)
          and raises(lambda: W.delete_scratch_level("ashvale"), W.WorldBuildError), "")
    # a throw-away levels folder holding a fake level "dup": no project level name is assumed, and the editor is never asked
    import tempfile
    real_levels, tmp_levels = W.LEVELS, tempfile.mkdtemp(prefix="zz_mcp_wb_levels_")
    try:
        os.makedirs(os.path.join(tmp_levels, "dup"))
        W.LEVELS = tmp_levels
        check("a level that already exists is refused (nothing is overwritten): dup", raises(lambda: W.new_level("dup"), W.WorldBuildError), "")
    finally:
        W.LEVELS = real_levels
        shutil.rmtree(tmp_levels, ignore_errors=True)


def live():
    if os.environ.get("MCP_TEST_LIVE") != "1":
        print("  (live part skipped: set MCP_TEST_LIVE=1; it saves the open level and switches the editor to a scratch level)")
        return False
    try:
        start = W.current_level()
    except Exception as exc:                                              # noqa: BLE001
        print("  (the editor does not answer: %s)" % str(exc)[:80])
        return False
    if not start["name"] or start["name"].startswith("zz_mcp_"):
        print("  (the editor has %r open: the live checks need a real level to come back to)" % start["name"])
        return False
    check("live precondition: the editor has a real level open (%s, %s objects)" % (start["name"], start["objects"]), True, "")
    import shutil
    W.delete_scratch_level(SCRATCH) if os.path.isdir(os.path.join(W.LEVELS, SCRATCH)) else None
    made = False
    try:
        rep = W.new_level(SCRATCH, 128, 4.0)
        made = True
        check("new_level: the level exists on disk, is the one open, is empty, and its terrain is 128 x 128 at 4 m (512 m), flat at 32 m with water at 16", os.path.isfile(os.path.join(W.LEVELS, SCRATCH, SCRATCH + ".level")) and W.current_level() == {"name": SCRATCH, "objects": 0}
              and rep["terrain"]["width"] == 128 and rep["terrain"]["unit"] == 4 and rep["terrain"]["min"] == rep["terrain"]["max"] == 32 and rep["terrain"]["water_level"] == 16, rep["terrain"])
        check("new_level saved the level it left (%s) and says so" % start["name"], rep["left"] == start["name"] and rep["left_saved"] is True, (rep["left"], rep["left_saved"]))
        check("control: a second level with the same name is refused", raises(lambda: W.new_level(SCRATCH), W.WorldBuildError), "")

        zones = [{"x": 200.0, "y": 200.0, "radius": 40.0, "height": 45.0}]
        pre = W.shape_terrain("mountains", 3, 20.0, None, zones, 0, preview=True)
        check("shape_terrain preview reports the numbers and installs NOTHING (the ground is still flat at 32)", pre["installed"] is False and W.terrain_info()["min"] == W.terrain_info()["max"] == 32 and "stats" in pre, pre["stats"]["max"])
        done = W.shape_terrain("mountains", 3, 20.0, None, zones, 0)
        gen = TG.generate(128, 4.0, 3, "mountains", 20.0, None, zones)
        back, info = W.read_heights()
        check("shape_terrain installed it: the engine reports the generated min/max/avg, nothing was clamped, and the heightmap read back is EXACTLY what was generated",
              done["installed"] and done["clamped_below_zero"] == 0 and done["clamped_at_ceiling"] == 0 and abs(info["max"] - float(gen.max())) < 0.01 and np.array_equal(back, gen), (info["max"], float(gen.max())))
        check("the old ground was backed up first (a flat 32 m map) and the backup file exists", os.path.isfile(done["backup"]) and float(np.fromfile(done["backup"], dtype="<f4").max()) == 32.0, os.path.basename(done["backup"]))
        pts = W.height_at([[200, 200], [100, 300], [300, 100], [-5, 10], [201.5, 202.25]])
        check("height_at: the flat zone's centre is 45 m, a grid point is the heightmap's own value ([x, y]: 100,300 -> [25, 75], not 300,100), a point off the terrain says inside false",
              abs(pts[0]["z"] - 45.0) < 0.01 and abs(pts[1]["z"] - float(gen[25, 75])) < 0.002 and abs(pts[2]["z"] - float(gen[75, 25])) < 0.002 and abs(pts[1]["z"] - pts[2]["z"]) > 0.05 and pts[3]["inside"] is False and pts[0]["inside"], pts[:3])
        check("height_at between grid points is the bilinear blend (inside the four corners' range)", min(gen[50:52, 50:52].ravel()) - 1e-3 <= W.height_at([[201.5, 202.25]])[0]["z"] <= max(gen[50:52, 50:52].ravel()) + 1e-3, W.height_at([[201.5, 202.25]])[0]["z"])
        r = W.restore_heights(done["backup"])
        flat = W.terrain_info()
        check("restore_heights puts the flat ground back (min = max = 32); a file that is not one of its backups is refused", flat["min"] == flat["max"] == 32 and raises(lambda: W.restore_heights(os.path.join(W.HERE, "make_heightmap.py")), W.WorldBuildError), flat["max"])
        W.shape_terrain("hills", 5, 20.0, None, zones, 0)

        ls = W.layers_list()
        added = W.layers_add(altitude=[16, 200], slope=[0, 45])
        check("terrain_layers: a new level has one layer (Default); add makes a second with the altitude and slope rules given", len(ls) == 1 and len(added["layers"]) == 2 and added["layers"][1]["altitudeStart"] == 16 and added["layers"][1]["altitudeEnd"] == 200 and added["layers"][1]["slopeMaxAngle"] == 45, added["layers"][1])
        b = W.bake(export=True, resolution=512, multiplier=8)
        small = ctc_size()
        check("bake: the surface texture was generated (the verb's reply is ok) and the terrain texture compiled (export ok)", b["surface"].get("ok") is not False and b["export"].get("ok") is not False and small, str(b)[:70])
        # REGRESSION (2026-10-13): export_texture used SetAutoExportMode, which made the engine ignore the resolution (always 16384 px) and run the indirect-lighting pass, whose SHLighting threads are never
        # ended: the SECOND export in one editor session could stop the editor with "Error spawning SHLighting__0 thread". Two exports at different sizes, then the size must follow the resolution.
        b2 = W.bake(export=True, resolution=1024, multiplier=8)
        big = ctc_size()
        check("bake twice in one session: the second export works and the compiled texture follows the resolution (512 px -> %s bytes, 1024 px -> %s bytes: about 4x)" % (small, big),
              b2["export"].get("ok") is not False and small and big and 2.5 < big / float(small) < 6.0, (small, big))

        check("vegetation: a new level has no brushes", W.veg_list().get("totalInstances") == 0 and W.veg_list().get("objects") == [], W.veg_list())
        mesh = "%ENGINE%/EngineAssets/Objects/primitive_cylinder.cgf"
        brush = W.veg_add(mesh, spacing=12.0, size=0.5)
        check("veg_add: a mesh that exists becomes brush 0", brush.get("index") == 0 and len(W.veg_list()["objects"]) == 1, str(brush)[:70])
        check("CONTROL: a mesh that does not exist is refused up front and NO broken brush is created (the engine would have said ok)", raises(lambda: W.veg_add("objects/zz_no_such_tree.cgf"), W.WorldBuildError) and len(W.veg_list()["objects"]) == 1, "")
        placed = W.veg_paint(0, 100, 100, 300, 300)
        n = W.veg_list()["totalInstances"]
        check("veg_paint scatters instances inside the rectangle (the engine reports a count, the map holds the same)", n > 0 and n == placed.get("placed", n), (n, placed.get("placed")))
        W.veg_paint(0, 0, 0, 512, 512, clear=True)
        check("veg_paint with clear=True removes them again", W.veg_list()["totalInstances"] == 0, W.veg_list()["totalInstances"])
        before_density = W.veg_list()["objects"][0]["density"]
        pr = W.veg_params(0, size=1.5, density=9.0)
        after_density = W.veg_list()["objects"][0]["density"]
        check("veg_params changes a brush: the reply lists what it applied and the brush list shows the new density (read back: %g -> %g)" % (before_density, after_density),
              pr.get("applied", {}).get("size") == 1.5 and pr.get("applied", {}).get("density") == 9.0 and abs(after_density - 9.0) < 1e-6 and before_density != after_density, pr.get("applied"))
        check("veg_params refuses a key the brush does not have, and an empty change", raises(lambda: W.veg_params(0, bogus=1), W.WorldBuildError) and raises(lambda: W.veg_params(0), W.WorldBuildError), "")

        sv = W.save_level()
        check("save_level saves the open level and says which", sv["saved"] and sv["level"] == SCRATCH, sv)
    finally:
        try:
            back = W.open_level(start["name"], save_current=False, discard_current=True)
            check("back to %s: the editor has it open again with its %s objects" % (start["name"], start["objects"]), back["level"] == start["name"] and W.current_level() == start, W.current_level())
        finally:
            if made or os.path.isdir(os.path.join(W.LEVELS, SCRATCH)):
                gone = W.delete_scratch_level(SCRATCH)
                check("the scratch level's files are deleted (%d paths)" % len(gone["deleted"]), not os.path.exists(os.path.join(W.LEVELS, SCRATCH)) and not os.path.exists(os.path.join(W.LEVELS, SCRATCH + ".level.cryasset")), gone["deleted"])
    return True


def main():
    offline()
    try:
        live()
    except W.WorldBuildError as exc:
        check("live part ran without a tool error", False, exc)
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
