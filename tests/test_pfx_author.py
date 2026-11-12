#!/usr/bin/env python3
"""Self-test of pfx_author.py: composing effects from the engine's catalog (offline), then, with the editor open, writing effects through the engine's loader and looking at them in the editor
viewport against controls (the game is not run; the level is not changed: the scratch entity is deleted and counted). About 60 s live.

    test_pfx_author.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import shutil
import sys
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import particle_catalog as P
import pfx_author as A

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-112s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:50]))


def refused(components):
    try:
        A.compose(components)
        return ""
    except P.ParticleError as exc:
        return str(exc)


EMBERS = [{"name": "embers", "features": [
    {"feature": "Render: Sprites", "params": {"FacingMode": "Camera"}},
    {"feature": "Spawn: Rate", "params": {"Amount": {"value": 80.0}}},
    {"feature": "Life: Time", "params": {"LifeTime": {"value": 3.0}}},
    {"feature": "Field: Size", "params": {"value": {"value": 0.35, "modifiers": [{"Random": {"Amount": 0.4}}]}}},
    {"feature": "Field: Color", "params": {"Color": {"Color": {"r": 255, "g": 120, "b": 20, "a": 255}}}},
    {"feature": "Location: Sphere", "params": {"Radius": {"value": 0.4}}},
    {"feature": "Velocity: Cone", "params": {"Angle": {"value": 25.0}, "Velocity": {"value": 2.0}}},
    {"feature": "Motion: Physics", "params": {"UniformAcceleration": {"x": 0.0, "y": 0.0, "z": 0.6}}},
    {"feature": "Appearance: Material", "params": {"Texture": "textures/sprites/glow/glow_a.dds"}},
    {"feature": "Appearance: Opacity", "params": {"BlendMode": "Additive"}}]}]


def main():
    doc = A.compose(EMBERS)
    feats = doc["Components"][0]["Component"]["Features"]
    names = [next(iter(f)) for f in feats]
    check("compose names features as the engine's file does (RenderSprites, SpawnRate, ... and FieldOpacity for Appearance: Opacity)", names == ["RenderSprites", "SpawnRate", "LifeTime", "FieldSize", "FieldColor", "LocationSphere", "VelocityCone", "MotionPhysics", "AppearanceMaterial", "FieldOpacity"], names)
    rs = feats[0]["RenderSprites"]
    check("a value starts from the engine's default and only what was given is changed (FacingMode Camera; AspectRatio, FlipU... stay default)", rs["FacingMode"] == "Camera" and rs["AspectRatio"] == 1.0 and rs["FlipU"] is False, rs)
    check("a modifier list on a parameter is carried (FieldSize value 0.35 with a Random modifier)", feats[3]["FieldSize"]["value"] == {"value": 0.35, "modifiers": [{"Random": {"Amount": 0.4}}]}, feats[3])
    msg = refused([{"name": "x", "features": [{"feature": "Render: Sprite"}, {"feature": "Spawn: Rate", "params": {"Amnt": {"value": 1}}},
                                               {"feature": "Field: Size", "params": {"value": {"value": 1, "modifiers": [{"Wobble": {}}]}}}]}])
    check("every problem is listed at once: an unknown feature (with the nearest names), an unknown parameter (with the real ones), an unknown modifier",
          "no feature 'Render: Sprite'" in msg and "Render: Sprites" in msg and "Amnt" in msg and "Amount" in msg and "'Wobble' is not a modifier" in msg and "3 problem(s)" in msg, msg[:60].replace("\n", " "))
    off = A.compose([{"name": "x", "features": [{"feature": "Spawn: Rate", "enabled": False}]}])
    check("a feature can be written switched off", off["Components"][0]["Component"]["Features"][0]["SpawnRate"]["Enabled"] is False, "")
    check("a Child feature takes a list of component names", A.compose([{"name": "p", "features": [{"feature": "Child: OnBirth", "params": ["spark"]}]}])["Components"][0]["Component"]["Features"][0] == {"ChildOnBirth": ["spark"]}, "")
    check("a legacy name works (FieldOpacity, RenderGPU Sprites) and means the current feature", [next(iter(f)) for f in A.compose([{"name": "q", "features": [{"feature": "FieldOpacity"}, {"feature": "RenderGPU Sprites"}]}])["Components"][0]["Component"]["Features"]] == ["FieldOpacity", "GPU ParticlesSprites"], "")
    bad = [refused([]), refused([{"name": "x", "features": []}])]
    check("an empty description is refused; an effect with no features is composable (the engine decides)", "components is a list" in bad[0] and bad[1] == "", bad)
    ok_paths = [A.effect_path("Particles/a/b.pfx")[0], A.effect_path("Assets/Particles/a/b.pfx")[0], A.effect_path("assets\\particles\\a\\b.pfx")[0]]
    check("effect paths: Particles/.., Assets/Particles/.. and backslashes all mean the same effect (Windows paths: case does not matter)", len({p.lower() for p in ok_paths}) == 1, ok_paths[0])
    bads = []
    for p in ("textures/a.pfx", "Particles/../x.pfx", "Particles/a/b.txt", "Particles/../../Windows/x.pfx"):
        try:
            A.effect_path(p)
        except P.ParticleError:
            bads.append(p)
    check("a path outside Assets/Particles, with '..', or not .pfx is refused", len(bads) == 4, bads)

    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                           # noqa: BLE001
        live = False
        print("  --   the editor is not open: the live checks are skipped")
    if live:
        import object_props as O
        stamp = time.strftime("%H%M%S")
        folder = os.path.join(P.PROJECT, "Assets", "Particles", "zz_test_" + stamp)
        try:
            r = A.write_effect("Particles/zz_test_%s/embers.pfx" % stamp, EMBERS)
            ec = r["engine_check"]
            check("write_effect writes the file and the ENGINE's loader keeps all of it (loaded, nothing dropped or changed or switched off)", ec["loaded"] and ec["ok"] and not ec["dropped"] and r["features"] == 10, ec)
            try:
                A.write_effect("Particles/zz_test_%s/embers.pfx" % stamp, EMBERS)
                again = False
            except P.ParticleError:
                again = True
            check("a second write to the same name is refused without overwrite=True", again, "")
            dup = [{"name": "dup", "features": [{"feature": "Render: Sprites"}, {"feature": "Render: Sprites"}, {"feature": "Spawn: Rate"}, {"feature": "Life: Time"}, {"feature": "Field: Size"}]}]
            r2 = A.write_effect("Particles/zz_test_%s/dup.pfx" % stamp, dup)
            check("what compose cannot know the engine tells: two Render features in one component -> the second is switched off (engine_check.disabled_by_engine)", len(r2["engine_check"]["disabled_by_engine"]) == 1, r2["engine_check"]["disabled_by_engine"])
            still = A.write_effect("Particles/zz_test_%s/quiet.pfx" % stamp, [{"name": "q", "features": [{"feature": "Render: Sprites"}, {"feature": "Spawn: Rate", "params": {"Amount": {"value": 0.0}}},
                                                                                                   {"feature": "Life: Time"}, {"feature": "Field: Size"}]}])
            check("the control effect (spawn rate 0) also loads clean", still["engine_check"]["ok"], still["engine_check"])
            before = O.object_count()
            pos = [487, 392, 40]
            for attempt in (1, 2):                      # a picture measurement: the scene sways and drifts; one repeat is allowed before the check fails
                live_pic = A.preview("Particles/zz_test_%s/embers.pfx" % stamp, pos, wait_s=6)
                quiet_pic = A.preview("Particles/zz_test_%s/quiet.pfx" % stamp, pos, wait_s=6)
                if live_pic["bright_pixels"] > 300 and quiet_pic["bright_pixels"] < 0.1 * live_pic["bright_pixels"] and live_pic["box_mean_abs_diff"] > 3 * max(live_pic["box_noise"], 0.1):
                    break
            check("preview, positive: the emitting effect makes %d pixels brighter in the middle of the pane (two pictures of the same view: %d), the box differs by %.2f against the noise's %.2f" % (live_pic["bright_pixels"], live_pic["bright_pixels_noise"], live_pic["box_mean_abs_diff"], live_pic["box_noise"]),
                  live_pic["bright_pixels"] > 300 and live_pic["bright_pixels"] > 5 * live_pic["bright_pixels_noise"] + 100 and live_pic["box_mean_abs_diff"] > 3 * max(live_pic["box_noise"], 0.1), live_pic["bright_pixels"])
            check("preview, negative control: the effect that spawns nothing is no different from the noise (%d bright pixels against %d; box %.2f against %.2f)" % (quiet_pic["bright_pixels"], quiet_pic["bright_pixels_noise"], quiet_pic["box_mean_abs_diff"], quiet_pic["box_noise"]),
                  quiet_pic["bright_pixels"] < 0.1 * live_pic["bright_pixels"] and quiet_pic["box_mean_abs_diff"] < 2 * max(quiet_pic["box_noise"], 0.1), quiet_pic["bright_pixels"])
            check("the scratch entities are gone: the level holds the objects it held before (%d)" % before, O.object_count() == before and not O.names_like("zz_pfx"), O.object_count())
            check("the changed pixels are brighter than the control there (Additive glow adds light: %s against %s)" % (live_pic["mean_rgb_changed"], live_pic["mean_rgb_control_there"]), sum(live_pic["mean_rgb_changed"]) > sum(live_pic["mean_rgb_control_there"]), "")
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    bad_n = results.count(False)
    print("pfx author test: %s (%d checks)" % ("PASS" if not bad_n else "%d FAILED" % bad_n, len(results)))
    return 1 if bad_n else 0


if __name__ == "__main__":
    sys.exit(main())
