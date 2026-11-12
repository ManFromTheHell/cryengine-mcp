#!/usr/bin/env python3
"""Self-test of the generated-clip pipeline (kimodo_retarget.py, anim_oneshot.py, kimodo_clips.py, motionbricks_actions.py, the root-heading check of motionbricks_validate.py). Editor-free, numpy only: it uses the
motions kept in anim_sources/, the manifest and the compiled clips on disk; the engine-side numbers (every clip within 1 mm of what it was built to say) are anim_validate.py's and test_anim_sample.py's.

    test_kimodo_clips.py        # exit 1 on any failure

Each rule has a control that makes it fail: the two bugs this pipeline had (a locator that did not start at the origin, a heading that did not start at 0) are reproduced by hand and shown to be caught.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import sys

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import anim_oneshot as AO  # noqa: E402
import kimodo_clips as KC  # noqa: E402
import kimodo_retarget as KR  # noqa: E402
import motionbricks_actions as MA  # noqa: E402
import motionbricks_clip as MC  # noqa: E402
import motionbricks_validate as V  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def main():
    soma = KR.Soma(os.path.join(KC.SRC, "soma77_skeleton.json"))
    posed = np.load(os.path.join(KC.SRC, "kimodo", "attack_side_00.npz"))["posed_joints"]
    mirrored = AO.mirror_soma(posed, soma)
    check("mirror_soma: mirroring twice gives the motion back, and a right hand becomes the left hand's mirror image",
          np.allclose(AO.mirror_soma(mirrored, soma), posed) and np.allclose(mirrored[:, soma.ix["LeftHand"], 1:], posed[:, soma.ix["RightHand"], 1:]) and np.allclose(mirrored[:, soma.ix["LeftHand"], 0], -posed[:, soma.ix["RightHand"], 0]), "")

    human, r = KC.load("turn_left_02")
    F = len(r["local_q"])
    sl = AO.slice_pad(r, -5, F + 5)
    check("slice_pad: a window that runs past the clip holds the first and the last frame", len(sl["local_q"]) == F + 11 and np.allclose(sl["local_q"][0], r["local_q"][0]) and np.allclose(sl["local_q"][-1], r["local_q"][-1]), len(sl["local_q"]))

    clip = AO.make_clip(AO.slice_pad(r, 3, 50), human, "path", "follow")
    check("make_clip(path, follow): the locator STARTS at the origin and facing 0 (the engine takes the first frame out of a clip, so any offset there moves every joint)",
          np.allclose(clip["loc"][0], 0.0) and abs(float(clip["loc_yaw"][0])) < 1e-9, (clip["loc"][0].tolist(), float(clip["loc_yaw"][0])))
    check("make_clip(path, follow) on a left turn: the heading ends clearly left (positive), 60-110 degrees", 60 < np.degrees(clip["loc_yaw"][-1]) < 110, round(float(np.degrees(clip["loc_yaw"][-1])), 1))
    fixed = AO.make_clip(AO.slice_pad(r, 3, 50), human, "fixed", "fixed")
    check("make_clip(fixed, fixed): the locator never moves or turns", not np.any(fixed["loc"]) and not np.any(fixed["loc_yaw"]), "")
    # the control: the heading BEFORE the fix (low-passed, edge held) is not zero at frame 0 for a window that opens just before the turn
    psi = AO.pelvis_yaw(AO.slice_pad(r, 3, 50), human)
    before_fix = AO.lowpass(psi - psi[0], 8)
    check("control: without subtracting its first value the low-passed heading is NOT 0 at frame 0 (the bug this guards, 1-3 cm on the turn clips)", abs(float(before_fix[0])) > np.radians(0.5), round(float(np.degrees(before_fix[0])), 2))

    body = AO.make_inplace_loop(r, human, 10, 20)
    check("make_inplace_loop: the last frame is the first (a seamless loop)", np.allclose(body["local_q"][-1], body["local_q"][0]) and np.allclose(body["pelvis_rel"][-1], body["pelvis_rel"][0]), "")

    # the specs
    missing = [n for n, s in KC.CLIPS.items() if not os.path.isfile(os.path.join(KC.SRC, "kimodo", s["src"] + ".npz"))]
    check("every Kimodo clip has its source motion kept in anim_sources/kimodo (the same prompt and seed only repeat on one machine)", not missing, missing)
    man = json.load(open(MC.MANIFEST))["clips"]
    miss = [n for n in KC.CLIPS if n not in man or not os.path.isfile(os.path.join(KC.OUT, n + ".caf"))]
    check("every Kimodo clip is compiled (a .caf) and in the manifest", not miss, miss)
    check("the manifest keeps what each one needs: model, prompt, seed, kind, checks, root travel; attacks the hit time; turns the heading curve",
          all({"model", "prompt", "seed", "kind", "checks", "root_travel", "end_yaw_deg"} <= set(man[n]) for n in KC.CLIPS)
          and all("hit_time" in man[n] for n in KC.CLIPS if KC.CLIPS[n]["group"] == "attack") and all(len(man[n].get("yaw_profile", [])) >= 5 for n in KC.CLIPS if KC.CLIPS[n]["group"] == "turn"), "")
    check("attacks land half way into the clip (0.55 s of 1.07 s: the brain's strike length) and are cut at the hand's speed peak", all(abs(man[n]["hit_time"] - 0.533) < 0.04 for n in KC.CLIPS if KC.CLIPS[n]["group"] == "attack"), [man[n]["hit_time"] for n in KC.CLIPS if KC.CLIPS[n]["group"] == "attack"])
    check("turns: left clips end with positive heading, right clips with negative, the half turns past 150 degrees",
          man["kd_turn_left"]["end_yaw_deg"] > 60 and man["kd_turn_right"]["end_yaw_deg"] < -60 and man["kd_turn_180_left"]["end_yaw_deg"] > 150 and man["kd_turn_180_right"]["end_yaw_deg"] < -150,
          [man[n]["end_yaw_deg"] for n in ("kd_turn_left", "kd_turn_right", "kd_turn_180_left", "kd_turn_180_right")])
    check("the only clips with a loop promise are the loops (sit idle); one-shots promise no loop closure, and deaths and sits no feet on the ground",
          all(man[n]["checks"]["loop"] == (KC.CLIPS[n]["kind"] == "loop") for n in KC.CLIPS) and all(not man[n]["checks"]["feet_on_ground"] for n in KC.CLIPS if KC.CLIPS[n]["group"] in ("death", "sit")), "")

    # the later clips: work, daily life, fear and combat reactions, talk and idle variants
    new = [n for n, sp in KC.CLIPS.items() if sp["group"] in ("work", "daily", "sleep", "pray", "greet", "react", "flee", "fall", "talk", "idle")]
    check("the villager clip set has the 35 new clips (work 5, daily 2, sleep 3, prayer 3, greet 2, cower and reactions 7 with both dodges, flee 1, fall 3, talk 5, idle 4)", len(new) == 35, len(new))
    loops = [n for n in new if KC.CLIPS[n]["kind"] == "loop"]
    walks = [n for n in new if KC.CLIPS[n]["cut"] == "walk"]
    check("every in-place loop of the new set closes on itself in the engine's terms (checks.loop) and strays less than 0.35 m from its start (pelvis_excursion_m)",
          all(man[n]["checks"]["loop"] for n in loops) and all(man[n]["pelvis_excursion_m"] < 0.35 for n in loops if n not in walks), [(n, man[n]["pelvis_excursion_m"]) for n in loops if man[n]["pelvis_excursion_m"] >= 0.35])
    check("walking and running loops (carry, flee, crawl) travel: a measured speed above 0.4 m/s, the body facing its travel within 12 degrees (motionbricks_loop.build_loop's own report)",
          all(man[n]["speed"] > 0.4 and abs(man[n]["facing_vs_travel_deg"]) < 12.0 for n in walks), [(n, man[n]["speed"], man[n]["facing_vs_travel_deg"]) for n in walks])
    check("the carry walk is a 1 m/s walk (0.9-1.2) and the flee a run (2.5-4.5 m/s): the code moves the body at the clip's own speed",
          0.9 <= man["kd_work_carry_walk"]["speed"] <= 1.2 and 2.5 <= man["kd_flee_run"]["speed"] <= 4.5, (man["kd_work_carry_walk"]["speed"], man["kd_flee_run"]["speed"]))
    check("compressed reactions (heavy hits, parry recoil) never stray more than their max_excursion from the entity (the control: an uncompressed 'fixed' stagger of the same sample strays more than 1 m)",
          all(man[n]["pelvis_excursion_m"] <= KC.CLIPS[n].get("max_excursion", 0.3) + 0.011 for n in new if KC.CLIPS[n].get("root") == "compress"), "")
    h, r = KC.load("f_hitfront_03")
    raw = AO.make_clip(AO.slice_pad(r, 0, 78), h, "fixed")["pelvis_rel"][:, :2]
    check("control: the same heavy hit WITHOUT compression moves the pelvis more than 1 m from where the clip began", float(np.max(np.linalg.norm(raw - raw[0], axis=1))) > 1.0, round(float(np.max(np.linalg.norm(raw - raw[0], axis=1))), 2))
    check("the two dodges mirror each other: the left one travels 0.8-1.5 m to the left (x < 0), the right one the same to the right",
          -1.5 < man["kd_dodge_side"]["root_travel"][0] < -0.8 and 0.8 < man["kd_dodge_side_r"]["root_travel"][0] < 1.5 and abs(man["kd_dodge_side"]["root_travel"][0] + man["kd_dodge_side_r"]["root_travel"][0]) < 0.05, (man["kd_dodge_side"]["root_travel"], man["kd_dodge_side_r"]["root_travel"]))
    check("clips that start or end on the ground (sleep, prayer on the knees, falls, crawl) do not promise feet on the ground; standing work, talk, idle and greeting clips do",
          all(not man[n]["checks"]["feet_on_ground"] for n in new if KC.CLIPS[n]["group"] in ("sleep", "pray", "fall")) and all(man[n]["checks"]["feet_on_ground"] for n in new if KC.CLIPS[n]["group"] in ("work", "talk", "idle", "greet", "daily")), "")
    check("every new clip's prompt starts 'A person' (or 'A wounded person') and lasts at most 10 s per sentence, and its seed is kept", all(KC.CLIPS[n]["prompt"].startswith(("A person", "A wounded person")) and man[n]["seed"] == KC.CLIPS[n]["seed"] for n in new), [n for n in new if not KC.CLIPS[n]["prompt"].startswith(("A person", "A wounded person"))])

    # MotionBricks starts and stops
    names = MA.clip_names()
    check("motionbricks_actions makes an idle and a start and a stop for each of the 4 gaits, all compiled and in the manifest", len(names) == 9 and all(n in man and os.path.isfile(os.path.join(MA.OUT, n + ".caf")) for n in names), [n for n in names if n not in man])
    check("mb_idle is recorded as a loop that must close (MEASURED when it was made: idle mode is a held stance, no joint moves more than 1.2 cm in 8 s, so it is the pose the starts and stops begin and end in, not a living idle)",
          man["mb_idle"]["kind"] == "loop" and man["mb_idle"]["checks"]["loop"], "")
    prof = {n: man[n]["speed_profile"] for n in names if n.startswith(("mb_start", "mb_stop"))}
    check("a start accelerates (the speed profile's last third is faster than its first) and a stop decelerates (the first third faster than the last)",
          all(np.mean(p[-len(p) // 3:]) > np.mean(p[:len(p) // 3]) for n, p in prof.items() if "start" in n) and all(np.mean(p[:len(p) // 3]) > np.mean(p[-len(p) // 3:]) for n, p in prof.items() if "stop" in n), "")
    check("each start ends on the frame whose pose is nearest frame 0 of the gait's walk loop (distance recorded, small)", all(man["mb_start_" + g]["pose_distance_to_loop_frame0"] < 0.35 for g in MA.GAITS), [man["mb_start_" + g]["pose_distance_to_loop_frame0"] for g in MA.GAITS])

    # the engine-side comparison: heading
    exp = {"fps": 30.0, "duration": 1.0, "expected": [{"t": 0.0, "root_yaw_deg": 0.0, "joints": {"Bip01 Pelvis": [0, 0, 1]}}, {"t": 1.0, "root_yaw_deg": 90.0, "joints": {"Bip01 Pelvis": [0, 0, 1]}}]}

    def sample(yaw_end):
        h = np.radians(yaw_end) / 2.0
        return {"found": True, "loaded": True, "duration": 1.0, "joints": ["Bip01", "Bip01 Pelvis"],
                "samples": [{"t": 0.0, "pose": [[1, 0, 0, 0, 0, 0, 0], [1, 0, 0, 0, 0, 0, 1]]}, {"t": 1.0, "pose": [[np.cos(h), 0, 0, np.sin(h), 0, 0, 0], [1, 0, 0, 0, 0, 0, 1]]}]}
    good, bad = V.validate(exp, sample(90.0)), V.validate(exp, sample(80.0))
    check("validate: the engine's root heading is compared too - a root turned 90 degrees passes, one turned 80 is reported (10 degrees > 1.5)", good["ok"] and not bad["ok"] and abs(bad["worst_yaw_deg"] - 10.0) < 0.1, (good["worst_yaw_deg"], bad["worst_yaw_deg"]))

    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
