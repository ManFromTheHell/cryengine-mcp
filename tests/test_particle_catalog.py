#!/usr/bin/env python3
"""Self-test of particle_catalog.py: the catalog the engine's registry wrote (offline), the comparison logic on synthetic effects (offline), then, with the editor open, real round trips of
effects through the engine's own loader with positive and negative controls (the game is not run; nothing in the level changes). About 10 s.

    test_particle_catalog.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import copy
import json
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import particle_catalog as P

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-110s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:50]))

def check_src(name, ok, detail=""):
    """a check that compares the catalog with the engine source: skipped when CRYENGINE_SOURCE is not set"""
    if not P.SOURCE:
        print("  --   SKIPPED (needs a CRYENGINE source checkout, set CRYENGINE_SOURCE): " + name[:70])
        return
    check(name, ok, detail)



def main():
    feats = P.features()
    by_class = {f["class_name"]: f for f in feats}
    check("the registry holds the engine's features (%d), each with a unique file name" % len(feats), len(feats) >= 50 and len(by_class) == len(feats), len(feats))
    check("a feature's editor name is 'Group: Feature' and its file name is the two joined (RenderSprites, SpawnRate, FieldSize)", all(f["full"] == f["group"] + ": " + f["feature"] for f in feats) and {"RenderSprites", "SpawnRate", "FieldSize"} <= set(by_class), "")
    odd = [f["full"] for f in feats if f["class_name"] != f["group"] + f["feature"]]
    check("the name a .pfx holds a feature under is its group and name joined, read from the engine's own save: 53 of 54; the one exception is Appearance: Opacity, which the engine writes as FieldOpacity (a legacy name registered after it)",
          odd == ["Appearance: Opacity"] and P.get("FieldOpacity") is P.get("Appearance: Opacity") and P.get("AppearanceOpacity") is P.get("FieldOpacity"), odd)
    check("get() takes the editor name, the file name or a spaced name; an unknown one is None",
          P.get("Render: Sprites") is P.get("RenderSprites") is P.get("render sprites") and P.get("NoSuchFeature") is None, "")
    rows, total = P.search("", "Spawn")
    check("search by group (Spawn: Count, Rate, Distance, Density)", total == 4 and {r["feature"] for r in rows} == {"Count", "Rate", "Distance", "Density"}, total)
    rows, total = P.search("opacity")
    check("search by word finds a feature by name (Opacity) and by parameter name (FlipU -> Render: Sprites)", any("Opacity" in r["full"] for r in rows) and "Render: Sprites" in [r["full"] for r in P.search("flipu")[0]], [r["full"] for r in rows][:3])
    roles = {f["role_default"] for f in feats if f["role_default"]}
    check("the five features the engine adds by default for a missing role are known (Life, Spawn, Size, Motion, Child)", roles == {"Life", "Spawn", "Size", "Motion", "Child"}, sorted(roles))

    mods = {m["name"]: m for m in P.modifiers()}
    reg = P.modifier_names_in_source()
    check_src("every modifier the source registers (%d registrations) is in the captured catalog (%d kinds); 'Attribute' and 'Inherit' are registered twice (float and colour)" % (len(reg), len(mods)),
          {m["name"] for m in reg} == set(mods) and len(reg) == 13 and len(mods) == 11 and len(mods["Inherit"]["source"]) == 2, sorted(mods))
    check("the engine, not the source, says where a modifier fits: Random sits on floats and not on colours, ColorRandom the other way (the control), Inherit on both",
          mods["Random"]["kept_by"]["float, per particle"] and not mods["Random"]["kept_by"]["colour, per particle"] and mods["ColorRandom"]["kept_by"]["colour, per particle"] and not mods["ColorRandom"]["kept_by"]["float, per particle"]
          and all(mods["Inherit"]["kept_by"].values()), "")
    check("a modifier carries its parameters with the engine's defaults (Wave: Domain, Wave, Amplitude, Bias, Inverse)", {"Domain", "Wave", "Amplitude", "Bias", "Inverse"} <= set(mods["Wave"]["defaults"]["float, per particle"]), list(mods["Wave"]["defaults"]["float, per particle"]))

    eff = P.effects()
    v13 = {k: v for k, v in eff.items() if json.loads(v).get("Version") == 13}
    check_src("the project's effects and the GameSDK sample's are read (%d effects, %d in the engine's current format)" % (len(eff), len(v13)), len(eff) >= 19 and sum(1 for k in eff if k.startswith("project:")) >= 3 and len(v13) >= 5, len(eff))
    legacy = {a["name"] for a in P.aliases()}
    known = set(by_class) | {f["group"] + f["feature"] for f in feats}
    used_v13 = {n for t in v13.values() for n in P.feature_names_in(t)}
    check("control: every feature the current-format effects name exists in the catalog (%d feature names used)" % len(used_v13), not (used_v13 - known), sorted(used_v13 - known))
    used_old = {n for k, t in eff.items() if k not in v13 for n in P.feature_names_in(t)}
    check("the older effects name only catalog features or the engine's 13 legacy names (%d names; %d of them legacy, e.g. SecondGenOnSpawn, RenderGPU Sprites)" % (len(used_old), len(used_old & legacy)), not (used_old - known - legacy) and len(used_old & legacy) >= 3, sorted(used_old - known - legacy))
    al = {a["name"]: a for a in P.aliases()}
    check_src("the 13 legacy names the source registers are all measured through the engine: FieldOpacity stays written as FieldOpacity, LifeImmortal becomes Life: Time (written LifeTime), the SecondGen/Kill names keep nothing as a bare feature",
          len(P.legacy_names_in_source()) == 13 == len(al) and al["FieldOpacity"]["becomes"] == "Appearance: Opacity" and al["FieldOpacity"]["written_as"] == "FieldOpacity" and al["LifeImmortal"]["becomes"] == "Life: Time" and al["LifeImmortal"]["written_as"] == "LifeTime"
          and all(al[n]["becomes"] is None for n in ("SecondGenOnSpawn", "SecondGenOnDeath", "SecondGenOnCollide", "KillOnParentDeath")) and P.get("RenderGPU Sprites")["full"] == "GPU Particles: Sprites", len(al))

    # the comparison logic, on synthetic effects
    base = {"Version": 13, "Components": [{"Component": {"Name": "a", "Features": [{"RenderSprites": {"FlipU": False}}, {"LifeTime": {"LifeTime": {"value": 2.0}}}, {"FieldSize": {"value": {"value": 1.0, "modifiers": [{"Random": {"Amount": 0.5}}]}}}]}}]}
    check("an effect compared with itself differs in nothing (the fixed point)", P.diff_effect(base, copy.deepcopy(base)) == {"dropped": [], "changed": [], "disabled_by_engine": [], "defaults_added": 0}, "")
    eng = copy.deepcopy(base)
    eng["Components"][0]["Component"]["Features"].pop(1)
    d = P.diff_effect(base, eng)
    check("a feature the engine dropped from the middle is reported by name and does not shift the rest (RenderSprites and FieldSize still match)", [x["path"].split("/")[-1] for x in d["dropped"]] == ["LifeTime"] and d["dropped"][0]["kind"] == "feature" and not d["changed"], d["dropped"])
    eng = copy.deepcopy(base)
    eng["Components"][0]["Component"]["Features"][2]["FieldSize"]["value"]["modifiers"] = []
    eng["Components"][0]["Component"]["Features"][0]["RenderSprites"]["FlipU"] = True
    eng["Components"][0]["Component"]["Features"][0]["RenderSprites"]["Extra"] = 1
    d = P.diff_effect(base, eng)
    check("a dropped modifier, a changed value and a filled-in default are told apart", [x["kind"] for x in d["dropped"]] == ["modifier"] and len(d["changed"]) == 1 and d["changed"][0]["path"].endswith("FlipU") and d["defaults_added"] == 1, (d["dropped"], d["changed"]))
    eng = copy.deepcopy(base)
    eng["Components"][0]["Component"]["Features"][1]["LifeTime"]["Enabled"] = False
    check("a feature the engine switched off is reported as such", P.diff_effect(base, eng)["disabled_by_engine"] != [], "")
    src2 = copy.deepcopy(base)
    src2["Components"][0]["Component"]["Parent"] = ""
    src2["Components"][0]["Component"]["Stats"] = []
    check("an empty value the engine leaves out is not a loss ('Parent': '', 'Stats': [])", P.diff_effect(src2, base)["dropped"] == [], "")
    eng = copy.deepcopy(base)
    eng["Components"][0]["Component"]["Features"][1]["LifeTime"]["LifeTime"]["value"] = 2.000001
    check("floats within 1e-5 are the same, and 2 is 2.0", P.diff_effect(base, eng)["changed"] == [] and P._same(2, 2.0) and not P._same(True, 1), "")
    two = {"Version": 13, "Components": [{"Component": {"Name": "x", "Features": []}}, {"Component": {"Name": "y", "Features": [{"FieldSize": {"value": {"value": 3.0}}}]}}]}
    swapped = {"Version": 13, "Components": [two["Components"][1], two["Components"][0]]}
    check("components are matched by NAME (the engine writes a child after its parent), so a reorder is no difference", P.diff_effect(two, swapped)["dropped"] == [] and P.diff_effect(two, swapped)["changed"] == [], "")

    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                           # noqa: BLE001
        live = False
        print("  --   the editor is not open: the live checks are skipped")
    if live:
        snow = eff["sample:Particles/examples/omni/snow.pfx"]
        r = P.check_text(snow, "t_snow", keep_output=True)
        out_text = open(os.path.join(P.SCRATCH, "t_snow.engine.pfx"), encoding="utf-8").read()
        r2 = P.check_text(out_text, "t_snow2", keep_output=True)
        check("control: an effect written by the current engine loads with NO difference, and what the engine writes loads to the same text again (a fixed point)",
              r["loaded"] and r["ok"] and r2["ok"] and open(os.path.join(P.SCRATCH, "t_snow2.engine.pfx"), encoding="utf-8").read() == out_text, (r["ok"], r2["ok"]))
        probe = {"Version": 13, "Attributes": {"Attributes": []}, "Components": [{"Component": {"Name": "probe", "Nodepos": {}, "Features": [
            {"RenderSprites": {}},
            {"FieldOpacity": {"value": {"value": 1.0, "modifiers": [{"Random": {"Amount": 0.3}}, {"NoSuchModifier": {}}]}, "AlphaScale": {"x": 0.3, "y": 0.7}, "ClipRange": {"start": 0.2, "end": 0.8}}},
            {"NoSuchFeature": {"A": 1}},
            {"SpawnCount": {"Amount": {"value": 5.0}, "Bogus": 3}}]}}]}
        r = P.check_text(json.dumps(probe), "t_probe", keep_output=False)
        paths = [d["path"].split("Features/")[-1] for d in r["dropped"]]
        check("positive control: an unknown feature, an unknown modifier, an unknown key and the OLD name of a parameter (AlphaScale x/y) are each reported as dropped",
              {"NoSuchFeature", "FieldOpacity/value/modifiers/NoSuchModifier", "SpawnCount/Bogus", "FieldOpacity/AlphaScale/x", "FieldOpacity/AlphaScale/y"} <= set(paths) and not r["ok"], paths)
        check("negative control: what the engine knows survives untouched (the Random modifier's Amount 0.3 and ClipRange 0.2/0.8 are not in the dropped or changed lists)",
              not any("Random" in d["path"] and not d["path"].endswith("NoSuchModifier") for d in r["dropped"]) and not any("ClipRange" in d["path"] for d in r["dropped"] + r["changed"]), "")
        dup = {"Version": 13, "Attributes": {"Attributes": []}, "Components": [{"Component": {"Name": "dup", "Features": [
            {"RenderSprites": {}}, {"RenderSprites": {}}, {"LifeTime": {"LifeTime": {"value": 3.0}}}, {"LifeTime": {"LifeTime": {"value": 7.0}}}, {"SpawnRate": {}}, {"FieldSize": {}}]}}]}
        r = P.check_text(json.dumps(dup), "t_dup", keep_output=False)
        check("a second Render and a second Life feature in one component are switched off by the engine and reported (2 disabled), the first of each stays",
              len(r["disabled_by_engine"]) == 2 and all("#2" in x for x in r["disabled_by_engine"]), r["disabled_by_engine"])
        r = P.check_text(eff["project:Assets/Particles/examples/omni/dust.pfx"], "t_dust", keep_output=False)
        paths = [d["path"].split("Features/")[-1] for d in r["dropped"]]
        check("the project's own dust.pfx carries keys this engine ignores (SpawnCount/Mode, LocationOmni/SpawnOffset, AlphaScale x/y): found, none of them a feature", {"SpawnCount/Mode", "LocationOmni/SpawnOffset", "FieldOpacity/AlphaScale/x"} <= set(paths) and all(d["kind"] == "key" for d in r["dropped"]), paths[:4])
        old = P.check_text(eff["sample:Particles/examples/fireworks.pfx"], "t_fw", keep_output=False)
        check("a version-8 effect is reported as CONVERTED (version 8 -> 13) with the caveat that dropped entries may have been moved", old["converted"] and old["version"] == 8 and old["engine_version"] == 13 and "CONVERTS" in old["note"] and old["loaded"], (old["version"], old["engine_version"]))
        for f in os.listdir(P.SCRATCH):
            if f.startswith("t_"):
                os.remove(os.path.join(P.SCRATCH, f))

    bad_n = results.count(False)
    print("particle catalog test: %s (%d checks)" % ("PASS" if not bad_n else "%d FAILED" % bad_n, len(results)))
    return 1 if bad_n else 0


if __name__ == "__main__":
    sys.exit(main())
