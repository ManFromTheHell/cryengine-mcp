#!/usr/bin/env python3
"""Self-test of the engine-side clip sampler (anim_sample.py, plugin verb bridge.character.samplepose) and of the MotionBricks gait clips it measures. NEEDS the editor with a level open; changes nothing in it.

    test_anim_sample.py        # exit 1 on any failure

The sampler's first version returned the FIRST pose for every time (CryAnimation skips every second update of a character within one engine frame unless it is in an edit mode), and a clip written after the
skeleton loaded was not in its set: so this test asks, with a control for each, that different times give different poses, that the same time gives the same pose, and that what the manifest says of each gait
clip is what the engine computes (duration, travel speed, feet on the ground).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import math
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import anim_sample as S  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def main():
    run = "relaxed_tac_run_nw_fwd_fast_3p_01"
    a = S.sample(run, (0.0, 0.25, 0.5, 0.75))
    b = S.sample(run, (0.0, 0.25, 0.5, 0.75))
    check("the library's run clip is found and loaded: 0.77 s long", abs(a["duration"] - 0.7667) < 0.01, a["duration"])
    lf = [s["joints"]["Bip01 L Foot"] for s in a["samples"]]
    check("different times give different poses (the left foot moves more than 10 cm between t=0 and t=.5): the sampler is not returning one frame for every time",
          sum((lf[0][k] - lf[2][k]) ** 2 for k in range(3)) ** 0.5 > 0.10, [round(v, 2) for v in lf[2]])
    check("the same times give the same poses twice (deterministic)", a["samples"] == b["samples"], "")
    check("the root runs forward at a running speed (2-6 m/s along +Y)", 2.0 < a["root_ground_speed_m_s"] < 6.0 and a["root_travel_m"][1] > 1.0, a.get("root_ground_speed_m_s"))
    try:
        S.sample("no_such_clip_anywhere", (0.0,))
        check("a clip the character does not have is refused", False, "no error")
    except S.SampleError as e:
        check("a clip the character does not have is refused, after a reload of the animation folders", "no clip" in str(e), str(e)[:50])
    try:
        S.sample(run, (0.0, 1.5))
        check("a time beyond the clip is refused", False, "no error")
    except S.SampleError:
        check("a time beyond the clip is refused", True)

    manifest = os.path.join(HERE, "motionbricks_clips.json")
    if os.path.isfile(manifest):
        clips = json.load(open(manifest))["clips"]
        for name, c in sorted(clips.items()):
            try:
                r = S.sample(name, (0.0, 0.25, 0.5, 0.75, 1.0))
            except S.SampleError as e:
                check("%s is in the animation set and loads" % name, False, str(e)[:60])
                continue
            check("%s lasts what the manifest says (%.3f s)" % (name, c["duration"]), abs(r["duration"] - c["duration"]) < 1.5 / c["fps"], r["duration"])
            check("%s travels at the manifest's speed (%.2f m/s) to 3 %%" % (name, c["speed"]), abs(r["root_ground_speed_m_s"] - c["speed"]) < 0.03 * c["speed"] + 0.005, r["root_ground_speed_m_s"])
            # what a clip promises depends on what it is: a walking cycle or an in-place loop must stand on the ground and close on itself; a one-shot (a swing, a fall, a turn) closes on nothing, and a fall or a
            # sit has no feet on the ground (the manifest's `checks` says which: kimodo_clips.py / motionbricks_actions.py write it)
            promises = c.get("checks", {"loop": True, "feet_on_ground": True})
            if promises.get("feet_on_ground", True):
                check("%s stands on the ground: the lowest sole within 2 cm of 0 (not sunk, not floating)" % name, abs(r["lowest_sole_m"]) < 0.02, r["lowest_sole_m"])
            if promises.get("loop", True):
                first, last = r["samples"][0]["joints"], r["samples"][-1]["joints"]
                rel0 = [first["Bip01 Head"][k] - first["Bip01 Pelvis"][k] for k in range(3)]
                rel1 = [last["Bip01 Head"][k] - last["Bip01 Pelvis"][k] for k in range(3)]
                check("%s loops: the head relative to the pelvis is the same at the first and the last frame (1 cm)" % name, max(abs(rel0[k] - rel1[k]) for k in range(3)) < 0.01, [round(rel1[k] - rel0[k], 4) for k in range(3)])
            if c.get("kind") == "oneshot" and "yaw_profile" in c:
                # a turn: the engine's root joint (= the locator) ends the clip facing where the manifest says (positive = left)
                d = S.raw(name, (1.0,))
                ix = {n: i for i, n in enumerate(d["joints"])}
                qw, qx, qy, qz = d["samples"][0]["pose"][ix["Bip01"]][:4]
                yaw = math.degrees(math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz)))
                check("%s turns the root %.1f degrees (the manifest's end_yaw_deg), within 2" % (name, c["end_yaw_deg"]), abs((yaw - c["end_yaw_deg"] + 180.0) % 360.0 - 180.0) < 2.0, round(yaw, 1))
            if c.get("group") == "attack":
                check("%s: the blow lands inside the clip, between 20 %% and 90 %% of it (hit_time %.3f s of %.3f s)" % (name, c["hit_time"], c["duration"]), 0.2 * c["duration"] <= c["hit_time"] <= 0.9 * c["duration"], c["hit_time"])
    else:
        check("the MotionBricks clip manifest exists", False, manifest)

    failed = results.count(False)
    print("%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
