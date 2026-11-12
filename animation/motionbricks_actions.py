#!/usr/bin/env python3
"""MotionBricks scenarios (motionbricks_scenario.py, recorded in the MotionBricks venv) -> the villagers' START, STOP and IDLE clips. Project Python, numpy only.

    python animation/motionbricks_actions.py [name ...] [--no-rc] [--out <folder for the .caf>]

  mb_start_<gait>   standing -> walking in that gait. Cut from just before the body moves to the moment it reaches its cruise speed, at the frame whose pose is nearest to FRAME 0 OF THE GAIT'S WALK
                    LOOP (so the walk loop can take over from the start clip's last frame without a jump).
  mb_stop_<gait>    walking -> standing. From just before the body slows down to a settled standing pose.
  mb_idle           the model's idle mode as a loop. MEASURED: idle mode is a held stance (no joint moves more than 1.2 cm in 8 s), so it is the pose the start and stop clips begin and end in, NOT a
                    living idle (the library's relaxed idle breathes and shifts; keep that for villagers standing about).

The root travels with the body (the locator follows the pelvis' ground path), so the engine reports the real distance the clip covers; manifest keys `root_travel` and `speed_profile` (metres per second
at 0.1 s steps, the locator's speed) are what a controller needs to move the body the way the clip does.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import os
import sys

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import anim_oneshot as AO  # noqa: E402
import motionbricks_clip as MC  # noqa: E402
import motionbricks_fbx as MF  # noqa: E402
import motionbricks_retarget as MR  # noqa: E402

# MOTIONBRICKS_ROOT: the folder of the MotionBricks install that holds clips/ (the recordings) and work/ (default <repository>/anim_work/motionbricks)
MB_ROOT = _paths.MOTIONBRICKS_ROOT
CLIPS_DIR = os.path.join(MB_ROOT, "clips")
OUT = os.path.join(__import__("project_root").PROJECT, "Assets", "Animations", "human", "male", "motionbricks")
WORK = os.path.join(MB_ROOT, "work")
SEED = 1234
# gait -> (scenario tag, the recorded walk the gait's loop was cut from, the loop's clip name)
GAITS = {"slow": ("slow", "slow_walk", "mb_walk_slow"), "scared": ("scared", "walk_scared", "mb_walk_scared"), "injured": ("injured", "injured_walk", "mb_walk_injured"),
         "stealth": ("stealth", "walk_stealth", "mb_walk_stealth")}


def clip_names():
    return ["mb_idle"] + ["mb_start_" + g for g in GAITS] + ["mb_stop_" + g for g in GAITS]


def load_scenario(name, human):
    d = dict(np.load(os.path.join(CLIPS_DIR, "%s_%d.npz" % (name, SEED)), allow_pickle=True))
    return MR.retarget(d, human)


def ground_speed(r, w=6):
    """m/s of the pelvis' ground path, smoothed over +-w frames."""
    p = AO.lowpass(np.asarray(r["pelvis"], dtype=np.float64)[:, :2], w)
    v = np.linalg.norm(np.gradient(p, axis=0), axis=1) * r["fps"]
    return v


def pose_features(r, human):
    P = np.asarray(r["world_p"], dtype=np.float64)
    pel = P[:, human.ix["Bip01 Pelvis"]]
    joints = [human.ix[n] for n in ("Bip01 Head", "Bip01 L Hand", "Bip01 R Hand", "Bip01 L Foot", "Bip01 R Foot", "Bip01 L Calf", "Bip01 R Calf", "Bip01 Spine3")]
    f = (P[:, joints] - pel[:, None, :]).reshape(len(P), -1)
    return np.concatenate([f, np.gradient(f, axis=0) * r["fps"] * 0.15], axis=1)


def loop_frame0_features(human, rec, manifest_clip):
    """Features of frame 0 of a walking gait's loop: the recording the loop was cut from, at the frame it starts on."""
    r = MR.retarget(dict(np.load(os.path.join(CLIPS_DIR, "%s_%d.npz" % (rec, SEED)), allow_pickle=True)), human)
    return pose_features(r, human)[int(manifest_clip["cycle_from_frame"])]


def cut_start(r, human, target):
    v = ground_speed(r)
    cruise = float(np.median(v[-40:]))
    mask = v > 0.05
    if not mask.any():
        raise ValueError("no movement above 0.05 m/s: the recording never starts walking")
    move = int(np.argmax(mask))
    f0 = max(0, move - 5)
    after = v[move:] >= 0.92 * cruise
    if not after.any():
        raise ValueError("the recording never reaches 92 % of its cruise speed after it starts moving")
    reached = move + int(np.argmax(after))
    F = pose_features(r, human)
    lo, hi = reached, min(len(v) - 1, reached + 40)
    d = np.sqrt(np.mean((F[lo:hi + 1] - target[None]) ** 2, axis=1))
    f1 = lo + int(np.argmin(d))
    return f0, f1, cruise, float(d.min())


def cut_stop(r, human):
    v = ground_speed(r)
    cruise = float(np.median(v[40:100]))
    rest = (v < 0.03) & (np.arange(len(v)) > 100)
    if not rest.any():
        raise ValueError("the body never comes to rest after frame 100")
    end = int(np.argmax(rest))                                                # the body has come to rest
    a = end
    while a > max(0, end - 36) and v[a] < 0.92 * cruise:            # at most 1.2 s of slowing down (a limp's speed swings with each step, so the 92 % mark is not a clean edge)
        a -= 1
    return max(0, a - 6), min(len(v) - 1, end + 8), cruise


def speed_profile(clip, step=0.1):
    """Locator ground speed (m/s) every `step` seconds over the clip."""
    loc = clip["loc"][:, :2]
    fps = clip["fps"]
    t = np.arange(len(loc)) / fps
    out = []
    for k in range(int(np.floor(t[-1] / step + 1e-6))):
        a, b = k * step, (k + 1) * step
        pa = np.array([np.interp(a, t, loc[:, c]) for c in (0, 1)])
        pb = np.array([np.interp(b, t, loc[:, c]) for c in (0, 1)])
        out.append(round(float(np.linalg.norm(pb - pa) / step), 3))
    return out


def build(name, tpl, out_dir, compile_caf=True):
    human = MR.Human(MC.BINDPOSE)
    import json
    manifest = json.load(open(MC.MANIFEST))["clips"]
    info = {"seed": SEED, "kind": "oneshot", "model": "MotionBricks (G1 locomotion, retargeted)", "checks": {"loop": False, "feet_on_ground": True}}
    if name == "mb_idle":
        r = load_scenario("idle", human)
        w = AO.slice_pad(r, 30, 30 + 90)
        i0, n, d = AO.best_inplace_loop(w, human, min_s=1.0, max_s=2.5, skip_s=0.1, tail_s=0.1)
        clip = AO.make_inplace_loop(w, human, max(i0, 8), n)
        info.update({"style": "idle", "kind": "loop", "group": "idle", "source": "scenario idle", "checks": {"loop": True, "feet_on_ground": True}, "cycle_from_frame": i0, "feature_distance": round(d, 4)})
    elif name.startswith("mb_start_") or name.startswith("mb_stop_"):
        kind, g = name.split("_")[1], name.split("_", 2)[2]
        tag, rec, loop_name = GAITS[g]
        r = load_scenario("%s_%s" % (kind, tag), human)
        if kind == "start":
            f0, f1, cruise, dist = cut_start(r, human, loop_frame0_features(human, rec, manifest[loop_name]))
            info["pose_distance_to_loop_frame0"] = round(dist, 4)
        else:
            f0, f1, cruise = cut_stop(r, human)
        clip = AO.make_clip(AO.slice_pad(r, f0, f1), human, "path", "fixed", smooth=8)
        info.update({"style": "%s_%s" % (kind, g), "group": kind, "gait": g, "source": "scenario %s_%s" % (kind, tag), "cruise_m_s": round(cruise, 3), "cut_frames": [int(f0), int(f1)],
                     "loop_clip": loop_name, "speed_profile": speed_profile(clip)})
    else:
        raise ValueError(name)
    pel = np.asarray(clip["pelvis_rel"])[:, 2]
    info["pelvis_height"] = [round(float(pel[0]), 3), round(float(pel.min()), 3), round(float(pel[-1]), 3)]
    exp, caf, size = MC.finish(name, clip, human, tpl, out_dir, WORK, info, compile_caf)
    return exp, caf, size, clip


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--no-rc", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    tpl = MF.Template(MC.TEMPLATE)
    for name in (a.names or clip_names()):
        exp, caf, size, clip = build(name, tpl, a.out, not a.no_rc)
        travel = float(np.linalg.norm(clip["loc"][-1, :2] - clip["loc"][0, :2]))
        print("%-18s %3d frames %.2f s  FK max %.4f m  root travel %.2f m  %s" % (name, exp["frames"], exp["duration"], exp["fk_check_max_m"], travel, caf or "(no rc)"))


if __name__ == "__main__":
    main()
