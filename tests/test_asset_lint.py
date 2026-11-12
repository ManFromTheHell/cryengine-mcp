#!/usr/bin/env python3
"""Self-test of asset_lint.py: a zero only counts if the linter can fail.

Builds a fixture Assets folder, plants one instance of every failure the linter claims to catch next to a clean twin of the same thing, and expects
exactly the right code on the planted file and nothing on the clean ones. The first version of the sub-material check flagged 174 of Crytek's own
meshes; this test now contains a real stock mesh as the clean twin, so that kind of false alarm cannot come back.

    test_asset_lint.py          # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import struct
import sys
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import asset_lint as L

PROJECT = __import__("project_root").PROJECT
FIXTURE = os.path.join(HERE, ".cache", "lint_fixture")


def write(rel, data, binary=False):
    path = os.path.join(FIXTURE, "Assets", rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if binary else "w", **({} if binary else {"encoding": "utf-8"})) as f:
        f.write(data)
    return path


def patch_cgf_material(src, new_name):
    d = bytearray(open(src, "rb").read())
    sig, ver, count, table = struct.unpack_from("<4sIII", d, 0)
    for i in range(count):
        ctype, cver, cid, size, off = struct.unpack_from("<HHIII", d, table + i * 16)
        if ctype == 0x1014:
            d[off:off + 128] = new_name.encode("latin1").ljust(128, b"\0")
            return bytes(d)
    raise RuntimeError("no material chunk in " + src)


def dds(w, h, dxgi):
    """A DX10 DDS header and a little payload: enough for the linter, which reads the header only."""
    head = bytearray(128)
    head[0:4] = b"DDS "
    struct.pack_into("<IIII", head, 4, 124, 0x1007, h, w)
    head[84:88] = b"DX10"
    return bytes(head) + struct.pack("<IIIII", dxgi, 3, 0, 1, 0) + b"\0" * 64


MTL = '<Material MtlFlags="524416" Shader="Illum" GenMask="%s" StringGenMask="%s" SurfaceType="mat_default"><Textures>%s</Textures></Material>'
TEX = '<Texture Map="%s" File="%s"/>'


class FixtureMissing(Exception):
    """the test's fixture is built from files of the author's project (a Crytek rock mesh and objects/ashvale/barn.cgf): another project skips the test"""


SHADER_PAKS = L.SHADER_PAKS          # as configured by CRYENGINE_SHADER_PAKS / the default, before the fixture run re-points the linter


def build_fixture():
    if os.path.isdir(FIXTURE):
        shutil.rmtree(FIXTURE)
    stock = os.path.join(PROJECT, "Assets", "objects", "natural", "rocks", "forest_rocks")
    rock = os.path.join(stock, "deco_rock_forest_a.cgf")                 # Crytek's: names a sub-material 'proxy_x' vs 'proxy', and 19 declared, 6 used
    rock_mtl = os.path.join(stock, "rock_forest.mtl")
    own = None
    for d, _, files in os.walk(os.path.join(PROJECT, "Assets", "objects", "ashvale")):
        for n in files:
            if n.lower() == "barn.cgf":
                own = os.path.join(d, n)
    for need in (rock, rock_mtl, own):
        if not need or not os.path.isfile(need):
            raise FixtureMissing("the fixture needs %s" % need)
    # --- meshes: clean twin, the original bug (a material that does not exist), and a used sub-material past the .mtl's list
    write("objects/stock/rock.cgf", open(rock, "rb").read(), True)
    write("objects/stock/rock_forest.mtl", open(rock_mtl, encoding="utf-8").read())
    used = L.cgf_material(own)[1] + 1                                    # the real mesh uses several materials: the clean twin's .mtl defines enough
    subs = "".join('<Material Name="m%d" Shader="Illum" GenMask="80000081"><Textures>%s</Textures></Material>' % (i, TEX % ("Diffuse", "textures/fix/good_diff.tif"))
                   for i in range(max(used, 1) + 1))
    write("materials/fix/good.mtl", "<Material><SubMaterials>%s</SubMaterials></Material>" % subs)
    write("textures/fix/good_diff.dds", dds(16, 16, 71), True)
    write("objects/fix/good.cgf", patch_cgf_material(own, "materials/fix/good"), True)
    write("objects/fix/lost.cgf", patch_cgf_material(own, "materials/ashvale/house"), True)                 # L001
    write("objects/fix/short.cgf", patch_cgf_material(rock, "materials/fix/short"), True)                   # L002 (the rock uses id 5)
    write("materials/fix/short.mtl", '<Material><SubMaterials><Material Name="a" Shader="Illum"/><Material Name="b" Shader="Illum"/></SubMaterials></Material>')
    # --- materials
    write("materials/fix/notex.mtl", MTL % ("80000081", "%NORMAL_MAP", TEX % ("Diffuse", "textures/fix/missing_diff.tif")))                               # L003
    # a UI element drawn as a texture ('Board.ui' names the UIElement 'Board' of libs/ui/UIElements): found; a .ui whose element does not exist is L003
    write("libs/ui/UIElements/Board.xml", '<UIElements name="T"><UIElement name="Board"><GFx file="x.gfx" layer="1"/></UIElement></UIElements>')
    write("materials/fix/ui_ok.mtl", MTL % ("80000081", "", TEX % ("Diffuse", "Board.ui")))
    write("materials/fix/ui_missing.mtl", MTL % ("80000081", "", TEX % ("Diffuse", "NoSuchBoard.ui")))                                                     # L003
    POM = "%NORMAL_MAP%OFFSET_BUMP_MAPPING%PARALLAX_OCCLUSION_MAPPING"
    write("materials/fix/pom_noheight.mtl", MTL % ("88020081", POM, TEX % ("Diffuse", "textures/fix/good_diff.tif")))                            # L005
    write("materials/fix/height_unread.mtl", MTL % ("80000081", "%NORMAL_MAP", TEX % ("Diffuse", "textures/fix/good_diff.tif") + TEX % ("Heightmap", "textures/fix/good_diff.tif")))   # L005
    write("materials/fix/pom_ok.mtl", MTL % ("88020081", POM, TEX % ("Diffuse", "textures/fix/good_diff.tif") + TEX % ("Heightmap", "textures/fix/good_diff.tif")))
    # what the ENGINE reads is StringGenMask, not the hex GenMask (MatMan.cpp:467): these three separate the two readings
    write("materials/fix/string_pom_noheight.mtl", MTL % ("80000081", POM, TEX % ("Diffuse", "textures/fix/good_diff.tif")))                   # L005: the string turns POM on, the hex does not
    write("materials/fix/hex_pom_but_string_off.mtl", MTL % ("88020081", "%NORMAL_MAP", TEX % ("Diffuse", "textures/fix/good_diff.tif")))     # NOT flagged: the hex bit is ignored by the engine
    write("materials/fix/ignored_flag.mtl", MTL % ("80000081", "%ALLOW_SILHOUETTE_POM%NORMAL_MAP%NO_SUCH_FLAG", TEX % ("Diffuse", "textures/fix/good_diff.tif")))   # L012 (info)
    # --- surface types: the project defines mat_default and mat_wood; a material using mat_rock is L017, one using mat_wood is the control
    write("libs/MaterialEffects/SurfaceTypes.xml", '<SurfaceTypes><SurfaceType name="mat_default"/><SurfaceType name="mat_wood"/></SurfaceTypes>')
    write("materials/fix/surface_rock.mtl", (MTL % ("80000081", "%NORMAL_MAP", TEX % ("Diffuse", "textures/fix/good_diff.tif"))).replace('SurfaceType="mat_default"', 'SurfaceType="mat_rock"'))     # L017
    write("materials/fix/surface_wood.mtl", (MTL % ("80000081", "%NORMAL_MAP", TEX % ("Diffuse", "textures/fix/good_diff.tif"))).replace('SurfaceType="mat_default"', 'SurfaceType="mat_wood"'))
    # --- textures
    write("textures/fix/bad_displ.dds", dds(64, 64, 77), True)                                           # L006 (BC3 height map)
    write("textures/fix/good_displ.dds", dds(64, 64, 80), True)
    write("textures/fix/odd_diff.dds", dds(60, 60, 71), True)                                            # L006 (not a power of two)
    # --- xml
    write("libs/fix/bad.xml", "<a><!-- a -- b --></a>")                                                   # L004
    write("libs/fix/good.xml", "<a><!-- fine --></a>")
    # --- audio
    write("audio/sdlmixer/assets/loop.wav", b"RIFF", True)                                                # L007
    write("audio/sdlmixer/assets/ok.ogg", b"OggS", True)
    write("audio/sdlmixer/ace/fix.xml", '<AudioSystemData><Triggers><Trigger name="t"><Event name="ok.ogg" type="start"/>'
          '<Event name="nope.ogg" type="start"/></Trigger></Triggers></AudioSystemData>')                 # L008 for nope.ogg only
    # --- licences (L018): a forbidden source named by a file's path, by a reference inside a file, and a clean twin that names the allowed sample project
    write("audio/sdlmixer/assets/neon_noir_owl.ogg", b"OggS", True)                                      # L018 (the path)
    write("libs/fix/from_nn.xml", '<a><!-- copied from neon-noir/1.0.0/GameSDK/audio/owl.ogg --></a>')    # L018 (the content)
    write("libs/fix/gamesdk_ref.xml", '<a><!-- from cryengine-gamesdk-sample-project/5.7.1/gamesdk/audio.pak --></a>')   # clean twin
    # --- level
    write("levels/fix/layers/main.lyr", '<Layer><Objects><Object Prefab="objects/fix/good.cgf" Material="materials/fix/good"/>'
          '<Object Prefab="objects/fix/gone.cgf" Material="materials/fix/vanished"/></Objects></Layer>')     # L010 x2
    # --- Mannequin: an animation that exists, one that does not (L015), a fragment the FragDef does not declare (L016); and a clean database (the control)
    write("Animations/clips/walk_a.caf", b"CAF", True)
    write("Animations/Mannequin/ADB/frags.xml", '<TagDefinition version="2"><Tags><Tag name="Idle"/><Tag name="Walk"/></Tags></TagDefinition>')
    write("Animations/Mannequin/ADB/tags.xml", '<TagDefinition version="2"><Tags/></TagDefinition>')
    write("Animations/Mannequin/ADB/bad.adb", '<AnimDB FragDef="Animations/Mannequin/ADB/frags.xml" TagDef="Animations/Mannequin/ADB/tags.xml"><FragmentList>'
          '<Idle><Fragment><AnimLayer><Animation name="walk_a"/></AnimLayer></Fragment></Idle>'
          '<Walk><Fragment><AnimLayer><Animation name="no_such_clip"/></AnimLayer></Fragment></Walk>'
          '<Dance><Fragment><AnimLayer><Animation name="walk_a"/></AnimLayer></Fragment></Dance></FragmentList></AnimDB>')
    write("Animations/Mannequin/ADB/good.adb", '<AnimDB FragDef="Animations/Mannequin/ADB/frags.xml" TagDef="Animations/Mannequin/ADB/tags.xml"><FragmentList>'
          '<Idle><Fragment><AnimLayer><Animation name="WALK_A"/></AnimLayer></Fragment></Idle></FragmentList></AnimDB>')
    # --- shader cache older than a material
    os.makedirs(os.path.join(FIXTURE, "shaders"), exist_ok=True)
    cache = os.path.join(FIXTURE, "shaders", "ShaderCache.pak")
    open(cache, "wb").write(b"PK")
    old = time.time() - 3600
    os.utime(cache, (old, old))                                                                           # L011


def main():
    try:
        build_fixture()
    except FixtureMissing as exc:
        print("lint self-test: SKIPPED (%s)" % exc)
        return 0
    L.configure(assets=os.path.join(FIXTURE, "Assets"), shader_paks=os.path.join(FIXTURE, "shaders"))
    out = L.lint(None, 500)
    got = {}
    for f in out["findings"]:
        got.setdefault(f["code"], []).append(f["path"].split("/")[-1])
    expect = {
        "L001": ["lost.cgf"], "L002": ["short.cgf"], "L003": ["notex.mtl", "ui_missing.mtl"], "L004": ["bad.xml"],
        "L005": ["pom_noheight.mtl", "height_unread.mtl", "string_pom_noheight.mtl"], "L012": ["ignored_flag.mtl", "ignored_flag.mtl"], "L006": ["bad_displ.dds", "odd_diff.dds"],
        "L007": ["loop.wav"], "L008": ["fix.xml"], "L010": ["main.lyr"], "L011": ["ShaderCache.pak"], "L015": ["bad.adb"], "L016": ["bad.adb"], "L017": ["surface_rock.mtl"], "L018": ["from_nn.xml", "neon_noir_owl.ogg"],   # ONE finding for all heavy meshes without a LOD (the fixture copies the stock rock three times), filed under the heaviest
    }
    bad = 0
    for code, files in expect.items():
        have = sorted(got.get(code, []))
        want = sorted(files)
        if code == "L010":
            want = ["main.lyr", "main.lyr"]                    # the missing mesh and the missing material
        ok = have == want
        bad += 0 if ok else 1
        print("  %s %-5s want %s, got %s" % ("ok  " if ok else "FAIL", code, want, have))
    clean = [c for c in got if c not in expect]
    clean_files = {"good.adb", "frags.xml", "tags.xml", "good.cgf", "rock.cgf", "good.mtl", "pom_ok.mtl", "hex_pom_but_string_off.mtl", "good_displ.dds", "good.xml", "gamesdk_ref.xml", "ok.ogg", "good_diff.dds", "surface_wood.mtl", "SurfaceTypes.xml", "ui_ok.mtl", "Board.xml"}
    false_alarms = [(c, n) for c, names in got.items() for n in names if n in clean_files and c != "L013"]
    ok = not clean and not false_alarms
    bad += 0 if ok else 1
    print("  %s clean twins untouched (stock rock mesh, good mesh/material/height map/xml/audio)%s" % ("ok  " if ok else "FAIL", "" if ok else ": %s %s" % (clean, false_alarms)))
    # a file the editor holds for a moment (its asset scanner opens whatever appears) is read after a short wait; one held for good still fails, quickly
    try:
        import msvcrt
        import threading
        import time as _time
        lockfile = os.path.join(FIXTURE, "locked.bin")
        with open(lockfile, "wb") as f:
            f.write(b"x" * 64)
        fh = open(lockfile, "r+b")
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 32)
        try:
            open(lockfile, "rb").read()
            print("  --   this system lets a locked region be read: the lock cases are skipped")
        except OSError:
            threading.Timer(1.0, lambda: msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 32)).start()
            t0 = _time.time()
            got = L.read_bytes(lockfile)
            ok = len(got) == 64 and 0.8 < _time.time() - t0 < 6
            check_line = "ok  " if ok else "FAIL"
            print("  %s a file locked for a second is read once it is free (read_bytes retries) %s" % (check_line, "%.1f s" % (_time.time() - t0)))
            bad += 0 if ok else 1
            _time.sleep(0.3)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 32)
            try:
                L.read_bytes(lockfile, tries=2, wait=0.05)
                print("  FAIL a file that stays locked is still an error")
                bad += 1
            except PermissionError:
                print("  ok   a file that stays locked is still an error (after the retries, not a hang)")
        finally:
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 32)
            except OSError:
                pass
            fh.close()
    except ImportError:
        print("  --   no msvcrt: the lock cases are skipped")
    # the same linter on the real project must stay clean
    L.configure(assets=os.path.join(PROJECT, "Assets"), shader_paks=SHADER_PAKS)
    real = L.lint(None, 50)
    ok = real["errors"] == 0
    bad += 0 if ok else 1
    print("  %s the real project: %d error(s), %d warning(s); checked %s" % ("ok  " if ok else "FAIL", real["errors"], real["warnings"], real["checked"]))
    shutil.rmtree(FIXTURE, ignore_errors=True)
    print("lint self-test: %s" % ("PASS" if not bad else "%d FAILED" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
