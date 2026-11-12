#!/usr/bin/env python3
"""What the ENGINE computes from a clip, from the open editor, without running the game: the pose of every joint at chosen moments of the clip.

The plugin verb `bridge.character.samplepose` creates the character, starts the clip on layer 0 and steps it to each normalized time (ISkeletonAnim::SetLayerNormalizedTime, no real time passes), runs the
animation job (StartAnimationProcessing / FinishAnimationComputations) and reads every joint's absolute transform (ISkeletonPose::GetAbsJointByID: character space, metres, Z up, +Y forward). This module asks for
it and summarises: the clip's duration, the key joints at each time, the travel speed of the root, the lowest the feet get. It is the measuring instrument of the MotionBricks pipeline (motionbricks_validate.py
compares it with what the clip was built to say) and answers any 'does this clip really do what I think' question: a converted FBX, a library clip, a retargeted motion.

Two facts the verb accounts for, both silent: CryAnimation updates a character only ONCE per engine frame ('several pre-updates' warning, the job is skipped), unless the character is in an edit
mode (ICharacterInstance::SetCharEditMode), so without it every time returns the first pose; and a loose .caf written after the skeleton was first loaded is not in its animation set until
ReloadCHRPARAMS (the character tool's own button), which the verb does when asked (here: when the clip is not found).

    python animation/anim_sample.py <clip> [0,0.25,0.5,0.75] [--character <.cdf>] [--joints "Bip01 Head,Bip01 L Hand"]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
DATA = os.path.join(HERE, "engine_data")
SCRATCH = os.path.join(DATA, "anim_scratch")
CHARACTER = "Objects/Characters/Human/sdk_player/sdk_player.cdf"
KEY_JOINTS = ["Bip01", "Bip01 Pelvis", "Bip01 Spine3", "Bip01 Head", "Bip01 L Hand", "Bip01 R Hand", "Bip01 L Foot", "Bip01 R Foot", "Bip01 L Toe0", "Bip01 R Toe0", "Locator_Locomotion"]
ANKLE_HEIGHT = 0.085     # the villager's ankle joint is about 9 cm above the sole in its bind pose; the toe joint 3 cm


class SampleError(RuntimeError):
    pass


def _slash(p):
    return p.replace(chr(92), "/")


def raw(clip, times, character=CHARACTER, step=1.0 / 30.0, reload=False):
    """The engine's answer as the plugin writes it: {anim, found, loaded, duration, joints, samples: [{t, pose: [[w x y z, tx ty tz, rel w x y z, rel tx ty tz] per joint]}]}."""
    import bridge_py
    os.makedirs(SCRATCH, exist_ok=True)
    out = os.path.join(SCRATCH, "sample_%d.json" % os.getpid())
    if os.path.exists(out):
        os.remove(out)
    cmd = "bridge.character.samplepose %s | %s | %s | %s | %g%s" % (_slash(out), character, clip, ",".join("%g" % t for t in times), step, " | reload" if reload else "")
    r = bridge_py.send(cmd, 120)
    if not r.get("ok"):
        raise SampleError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % r.get("error"))
    with open(out, encoding="utf-8") as f:
        d = json.load(f)
    os.remove(out)
    return d


def sample(clip, times=(0.0, 0.25, 0.5, 0.75), character=CHARACTER, joints=None):
    """Summary of the clip as the engine plays it. times are normalized (0 = first frame, 1 = last). joints: names to report (default the key joints of a walking body)."""
    times = [float(t) for t in times]
    if not times or any(t < 0.0 or t > 1.0 for t in times):
        raise SampleError("times must be normalized, 0..1")
    d = raw(clip, times, character)
    if not d.get("found"):
        d = raw(clip, times, character, reload=True)       # a .caf written since the skeleton was loaded: rescan the animation folders
    if not d.get("found"):
        raise SampleError("the character's animation set has no clip %r (even after a reload): is the .caf in a folder its .chrparams collects?" % clip)
    if not d.get("loaded"):
        raise SampleError("clip %r is in the set but did not stream in" % clip)
    names = d["joints"]
    ix = {n: i for i, n in enumerate(names)}
    want = [j for j in (joints or KEY_JOINTS) if j in ix]
    missing = [j for j in (joints or KEY_JOINTS) if j not in ix]
    out = {"clip": clip, "duration": d["duration"], "character": character, "joints_in_skeleton": len(names), "samples": [], "missing_joints": missing}
    lowest = None
    for s in d["samples"]:
        pos = {j: [round(v, 4) for v in s["pose"][ix[j]][4:7]] for j in want}
        out["samples"].append({"t": s["t"], "joints": pos})
        for foot, toe in (("Bip01 L Foot", "Bip01 L Toe0"), ("Bip01 R Foot", "Bip01 R Toe0")):
            if foot in ix and toe in ix:
                z = min(s["pose"][ix[foot]][6] - ANKLE_HEIGHT, s["pose"][ix[toe]][6] - 0.03)
                lowest = z if lowest is None else min(lowest, z)
    out["lowest_sole_m"] = None if lowest is None else round(lowest, 4)
    # the ROOT joint (Bip01) carries the clip's travel: the RC re-roots a clip on its locator, so Bip01 is the locator's path. (The 'Locator_Locomotion' joint of the skeleton is a dummy: in a clip converted from an
    # FBX it can carry the path a second time, relative to the root, so reading it doubles the speed.)
    root = "Bip01" if "Bip01" in want else ("Bip01 Pelvis" if "Bip01 Pelvis" in want else None)
    if root and len(d["samples"]) >= 2 and d["duration"] > 0:
        a, b = out["samples"][0], out["samples"][-1]
        dt = (b["t"] - a["t"]) * d["duration"]
        if dt > 0:
            dx = [b["joints"][root][k] - a["joints"][root][k] for k in range(3)]
            out["root_travel_m"] = [round(v, 4) for v in dx]
            out["root_ground_speed_m_s"] = round((dx[0] ** 2 + dx[1] ** 2) ** 0.5 / dt, 3)
            out["root_joint"] = root
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return
    opts = {}
    for k in ("--character", "--joints"):
        if k in sys.argv:
            opts[k] = sys.argv[sys.argv.index(k) + 1]
            args = [a for a in args if a != opts[k]]
    times = [float(x) for x in args[1].split(",")] if len(args) > 1 else (0.0, 0.25, 0.5, 0.75)
    r = sample(args[0], times, opts.get("--character", CHARACTER), opts["--joints"].split(",") if "--joints" in opts else None)
    print(json.dumps(r, indent=1))


if __name__ == "__main__":
    main()
