#!/usr/bin/env python3
"""Self-test of level_check.py: a clean synthetic level first (offline control), then each rule on an altered copy, then the real level against the open editor, and a REPLAY of the real incident (the saved
list holding only the engine's default.env while the exported level lists the project's presets).

    test_level_check.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys
import tempfile
import zipfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import level_check as L

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(findings, text="", sev=None):
    return sorted({f["code"] for f in findings if (text in f["message"] or text in f["where"]) and (sev is None or f["severity"] == sev)})


ENV = """<Environment>
 <Fog ViewDistance="8000" ViewDistanceLowSpec="1000" LDRGlobalDensMult="1.0"/>
 <Terrain DetailLayersViewDistRatio="1.0" HeightMapAO="0"/>
 <EnvState SunShadowsMinSpec="1" SunShadowsClipPlaneRange="256.0f" SunShadowsClipPlaneRangeShift="0.0f" UseLayersActivation="0"/>
 <VolumetricCloud HorizonHeight="-1000.0" MaxViewableDistance="100000.0" GlobalCloudNoiseScale="3,3,5" CloudVolumeTexture=""/>
 <Ocean Material="%ENGINE%/EngineAssets/Materials/Water/Ocean_default" CausticDepth="8.0"/>
 <OceanAnimation WindDirection="1.0" WindSpeed="4.0" WavesAmount="1.5" WavesSize="0.4"/>
 <DynTexSource Width="256" Height="256"/>
</Environment>"""
TOD = """<TimeOfDay Time="15"><Presets>
  <Preset Name="libs/environmentpresets/ashvale_clear.env" Default="1"/>
  <Preset Name="libs/environmentpresets/ashvale_rain.env"/>
 </Presets></TimeOfDay>"""
TOD_DEFAULT_ONLY = """<TimeOfDay Time="15"><Presets><Preset Name="%ENGINE%/EngineAssets/Shading/default.env" Default="1"/></Presets></TimeOfDay>"""
LYR = """<Layer><LayerObjects>
 <Object Type="Entity" Name="Lamp1" EntityClass="NightLamp" Pos="1,2,3"><Components><Component TypeName="CNightLampComponent"><properties Kind="WallTorch" Radius="4"/></Component></Components></Object>
 <Object Type="Entity" Name="Crate1" EntityClass="PickupItem" Pos="4,5,6"><Components><Component TypeName="CPickupItemComponent"><properties Mass="6"/></Component></Components></Object>
 <Object Type="EnvironmentProbe" Name="Probe1" Pos="7,8,9"/>
 <Object Type="Brush" Name="Wall1"/>
</LayerObjects></Layer>"""
CODE = 'void SetRaining(bool r) { const char* szStem = r ? "ashvale_rain" : "ashvale_clear"; FindPreset(pTod, szStem); }\n'


def mission(tod=TOD, env=ENV, objects=None):
    objects = objects if objects is not None else (
        '<Entity Name="Lamp1" EntityClass="NightLamp" Pos="1,2,3"><Components><Component TypeName="CNightLampComponent"><properties Kind="WallTorch" Radius="4"/></Component></Components></Entity>'
        '<Entity Name="Crate1" EntityClass="PickupItem" Pos="4,5,6"><Components><Component TypeName="CPickupItemComponent"><properties Mass="6"/></Component></Components></Entity>'
        '<Entity Name="Probe1" EntityClass="EnvironmentLight" Pos="7,8,9"/>')
    return "<Mission>%s%s<Objects>%s</Objects></Mission>" % (tod, env, objects)


def make(env=ENV, tod=TOD, lyr=LYR, code=CODE, pak=None, presets=True):
    root = tempfile.mkdtemp(prefix="zz_mcp_lvl_")
    a = os.path.join(root, "Assets")
    lv = os.path.join(a, "levels", "t")
    os.makedirs(os.path.join(lv, "leveldata"))
    os.makedirs(os.path.join(lv, "layers"))
    os.makedirs(os.path.join(root, "Code"))
    if presets:
        os.makedirs(os.path.join(a, "libs", "environmentpresets"))
        for s in ("ashvale_clear", "ashvale_rain"):
            open(os.path.join(a, "libs", "environmentpresets", s + ".env"), "w").write("x")
    open(os.path.join(lv, "leveldata", "environment.xml"), "w", encoding="utf-8").write(env)
    open(os.path.join(lv, "leveldata", "timeofday.xml"), "w", encoding="utf-8").write(tod)
    open(os.path.join(lv, "layers", "main.lyr"), "w", encoding="utf-8").write(lyr)
    open(os.path.join(root, "Code", "w.cpp"), "w", encoding="utf-8").write(code)
    if pak is not False:
        with zipfile.ZipFile(os.path.join(lv, "level.pak"), "w") as z:
            z.writestr("mission_mission0.xml", pak if pak is not None else mission())
    return root


def env_level(mk, env):
    return mk(env=env, pak=mission(env=env))


def run(root, live="same"):
    a = os.path.join(root, "Assets")
    saved = ["libs/environmentpresets/ashvale_clear.env", "libs/environmentpresets/ashvale_rain.env"]
    return L.check("t", live_presets=saved if live == "same" else live, with_engine=False, assets=a, code_dir=os.path.join(root, "Code"))


def main():
    made = []

    def mk(**kw):
        r = make(**kw)
        made.append(r)
        return r

    try:
        r = run(mk())
        check("control: a consistent synthetic level (settings, saved list, exported copy, entities) has no finding", r["findings"] == [] and r["ok"], codes(r["findings"]))
        check("the game's preset names are read from the code that looks them up (FindPreset)", r["summary"]["code_presets"] == ["ashvale_clear", "ashvale_rain"], r["summary"]["code_presets"])

        r = run(mk(tod=TOD_DEFAULT_ONLY), live=["%ENGINE%/EngineAssets/Shading/default.env"])
        check("positive control (the real incident): a saved list with only the engine's default.env is V001 for both presets, errors", len(codes(r["findings"], "ashvale_", "error")) == 1 and sum(1 for f in r["findings"] if f["code"] == "V001") == 2 and not r["ok"], codes(r["findings"]))
        check("positive control: and V009 says the editor then opens with a look the game never uses", codes(r["findings"], "never uses", "warning") == ["V009"], "")
        check("positive control: the exported level listing presets that the source no longer lists is V003, a warning (export older than the list)", codes(r["findings"], "level.pak", "warning") == ["V003"], codes(r["findings"], "level.pak"))
        r = run(mk(), live=["libs/environmentpresets/ashvale_clear.env"])
        check("positive control: the open editor's list differing from the saved list is V002", codes(r["findings"], "editor", "warning") == ["V002"], "")
        exported_default = mission(tod=TOD_DEFAULT_ONLY)
        r = run(mk(pak=exported_default))
        check("positive control: an EXPORTED level without the preset the game looks for is V003, an error, naming what it lists", codes(r["findings"], "EXPORTED", "error") == ["V003"] and any("default.env" in f["message"] for f in r["findings"] if f["code"] == "V003"), codes(r["findings"]))
        r = run(mk(pak=mission(tod=TOD.replace('<Preset Name="libs/environmentpresets/ashvale_rain.env"/>', '<Preset Name="libs/environmentpresets/ashvale_rain.env"/><Preset Name="libs/environmentpresets/extra.env"/>'))))
        check("positive control: an exported list that has an extra preset (the game's two are there) is a V003 warning", codes(r["findings"], "level.pak", "warning") == ["V003"] and r["ok"], codes(r["findings"]))
        r = run(mk(pak=False))
        check("control: a level that was never exported is not an error (nothing to compare)", r["ok"] and r["summary"]["exported"] is False, "")
        root = mk()
        open(os.path.join(root, "Assets", "levels", "t", "level.pak"), "w").write("not a zip")
        check("positive control: a level.pak that is not an archive is V003, an error", codes(run(root)["findings"], "archive", "error") == ["V003"], "")
        with zipfile.ZipFile(os.path.join(root, "Assets", "levels", "t", "level.pak"), "w") as z:
            z.writestr("levelinfo.xml", "<LevelInfo/>")
        check("positive control: an exported level with no mission file is V003, an error", codes(run(root)["findings"], "mission", "error") == ["V003"], "")
        r = run(mk(code="int main() {}"))
        check("control: code that looks up no preset requires none (nothing to enforce)", r["summary"]["code_presets"] == [] and r["ok"], "")

        # ---- the settings file
        r = run(env_level(mk, ENV.replace('ViewDistance="8000"', 'ViewDistanc="8000"')))
        check("positive control: a misspelt attribute is V005 and offers the right one", codes(r["findings"], "ViewDistanc", "warning") == ["V005"] and any("ViewDistance?" in f["message"] for f in r["findings"] if f["code"] == "V005"), "")
        r = run(env_level(mk, ENV.replace("<Terrain ", "<Terain ").replace("HeightMapAO=\"0\"/>", "HeightMapAO=\"0\"/>")))
        check("positive control: an element the 3D engine does not read is V005", codes(r["findings"], "<Terain>", "warning") == ["V005"], "")
        r = run(env_level(mk, ENV.replace('WavesSize="0.4"', 'WavesSize="lots"')))
        check("positive control: a word where a number is read is V006 (the loader reads 0)", codes(r["findings"], "lots", "warning") == ["V006"], "")
        r = run(env_level(mk, ENV.replace('WavesAmount="1.5"', 'WavesAmount="9"')))
        check("positive control: a value the loader clamps (WavesAmount 9, kept to 0.4..3) is V006", codes(r["findings"], "WavesAmount", "warning") == ["V006"], "")
        r = run(env_level(mk, ENV.replace('ViewDistance="8000"', 'ViewDistance="500"')))
        check("positive control: a view distance under 1000 is V006 (the manual: never below 1000)", any("1000" in f["message"] and f["code"] == "V006" for f in r["findings"]), "")
        r = run(env_level(mk, ENV.replace('GlobalCloudNoiseScale="3,3,5"', 'GlobalCloudNoiseScale="3,3"')))
        check("positive control: a vector with two numbers is V006", codes(r["findings"], "GlobalCloudNoiseScale", "warning") == ["V006"], "")
        r = run(env_level(mk, ENV.replace("%ENGINE%/EngineAssets/Materials/Water/Ocean_default", "materials/ocean_missing")))
        check("positive control: an ocean material that is not under Assets is V007", codes(r["findings"], "ocean_missing", "warning") == ["V007"], "")
        check("control: '256.0f' (the editor writes it) and an engine-asset ocean material are fine", codes(run(mk())["findings"], "SunShadowsClipPlaneRange") == [] and codes(run(mk())["findings"], "Ocean") == [], "")

        # ---- the exported copy against the source
        r = run(mk(pak=mission(env=ENV.replace('WindSpeed="4.0"', 'WindSpeed="9.0"'))))
        check("positive control: an exported setting that differs from environment.xml is V004 and shows both values", codes(r["findings"], "WindSpeed", "warning") == ["V004"] and any("'9.0'" in f["message"] for f in r["findings"]), "")
        r = run(mk(pak=mission(objects='<Entity Name="Lamp1" EntityClass="NightLamp" Pos="1,2,3"><Components><Component TypeName="CNightLampComponent"><properties Kind="WallTorch" Radius="4"/></Component></Components></Entity>')))
        check("positive control: source entities that the export lacks are V008 (Crate1)", codes(r["findings"], "Crate1", "warning") == ["V008"], "")
        r = run(mk(pak=mission(objects='<Entity Name="Lamp1" EntityClass="NightLamp" Pos="1,2,3"><Components><Component TypeName="CNightLampComponent"><properties Kind="WallTorch" Radius="4"/></Component></Components></Entity>'
                                       '<Entity Name="Crate1" EntityClass="PickupItem" Pos="4,5,6"><Components><Component TypeName="CPickupItemComponent"><properties Mass="6"/></Component></Components></Entity>'
                                       '<Entity Name="Probe1" EntityClass="EnvironmentLight" Pos="7,8,9"/><Entity Name="Old1" EntityClass="X" Pos="0,0,0"/>')))
        check("positive control: exported entities that the source no longer has are V008 (Old1)", codes(r["findings"], "Old1", "warning") == ["V008"], "")
        r = run(mk(pak=mission(objects='<Entity Name="Lamp1" EntityClass="NightLamp" Pos="1,2,3"><Components><Component TypeName="CNightLampComponent"><properties Kind="WallTorch" Radius="9"/></Component></Components></Entity>'
                                       '<Entity Name="Crate1" EntityClass="PickupItem" Pos="4,5,6"><Components><Component TypeName="CPickupItemComponent"><properties Mass="6"/></Component></Components></Entity>'
                                       '<Entity Name="Probe1" EntityClass="EnvironmentLight" Pos="7,8,9"/>')))
        check("positive control: a changed component property since the export is V008 (Lamp1)", codes(r["findings"], "Lamp1", "warning") == ["V008"], "")
        r = run(mk(pak=mission(objects='<Entity Name="Lamp1" EntityClass="NightLamp" Pos="9,9,9"><Components><Component TypeName="CNightLampComponent"><properties Kind="WallTorch" Radius="4"/></Component></Components></Entity>'
                                       '<Entity Name="Crate1" EntityClass="PickupItem" Pos="4,5,6"><Components><Component TypeName="CPickupItemComponent"><properties Mass="6"/></Component></Components></Entity>'
                                       '<Entity Name="Probe1" EntityClass="EnvironmentLight" Pos="7,8,9"/>')))
        check("control: an entity that only moved since the export is information, not a warning", codes(r["findings"], "Lamp1", "info") == ["V008"] and r["warnings"] == 0, "")

        # ---- the real level and the real incident
        # The real-level cases need the author's "ashvale" level folder AND an open editor (live_presets); anywhere else they are skipped, not failed.
        real_ok = os.path.isdir(os.path.join(L.ASSETS, "levels", "ashvale", "leveldata"))
        if real_ok:
            try:
                import bridge_py
                bridge_py.send("bridge.camera.get", 10)
            except Exception:                                   # noqa: BLE001
                real_ok = False
        if not real_ok:
            print("  --   SKIP the real-level cases and the incident replay: they need the Ashvale level under %s and an open editor" % L.ASSETS)
        else:
            real = L.check("ashvale")
            check("the real level: no error and no warning (settings clean, presets saved and exported, entities match the export)", real["errors"] == 0 and real["warnings"] == 0, [(f["code"], f["message"][:50]) for f in real["findings"]][:2])
            check("the real level: the saved list, the editor's list and the exported list are the same two presets with ashvale_clear the default", real["summary"]["saved_default"].endswith("ashvale_clear.env") and real["summary"]["live_presets"] is not None and len(real["summary"]["saved_presets"]) == 2, real["summary"]["live_presets"])
            asset = L.ASSETS
            rep = tempfile.mkdtemp(prefix="zz_mcp_lvl_")
            made.append(rep)
            a2 = os.path.join(rep, "Assets")
            shutil.copytree(os.path.join(asset, "levels", "ashvale", "leveldata"), os.path.join(a2, "levels", "ashvale", "leveldata"), ignore=shutil.ignore_patterns("*.dat", "*.bak*"))
            shutil.copytree(os.path.join(asset, "levels", "ashvale", "layers"), os.path.join(a2, "levels", "ashvale", "layers"), ignore=shutil.ignore_patterns("*.bak*"))
            shutil.copyfile(os.path.join(asset, "levels", "ashvale", "level.pak"), os.path.join(a2, "levels", "ashvale", "level.pak"))
            shutil.copytree(os.path.join(asset, "libs", "environmentpresets"), os.path.join(a2, "libs", "environmentpresets"))
            open(os.path.join(a2, "levels", "ashvale", "leveldata", "timeofday.xml"), "w").write(TOD_DEFAULT_ONLY)
            rr = L.check("ashvale", live_presets=["%ENGINE%/EngineAssets/Shading/default.env"], with_engine=False, assets=a2)
            check("REPLAY of the real incident: the real level with the saved list reduced to the engine's default.env is 2 errors (V001 for ashvale_clear and ashvale_rain)", sum(1 for f in rr["findings"] if f["code"] == "V001" and f["severity"] == "error") == 2, codes(rr["findings"]))
            check("REPLAY: and V009 (the engine default is the level's default) and V003 (the export lists other presets than the source)", "V009" in codes(rr["findings"]) and "V003" in codes(rr["findings"]), codes(rr["findings"]))
    finally:
        for r in made:
            shutil.rmtree(r, ignore_errors=True)
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
