#!/usr/bin/env python3
"""MotionBricks style -> a looping .caf for the villagers, in one command. Project Python, numpy only; the MotionBricks clip must already be recorded (motionbricks_record.py, the MotionBricks venv).

    python animation/motionbricks_clip.py <style> <anim name> [--clips <MOTIONBRICKS_ROOT>\\clips] [--seed 1234] [--out <folder for the .caf>] [--no-rc]

    style      the recorded style (walk, slow_walk, walk_scared, injured_walk, walk_stealth, stealth_walk ...): <clips>/<style>_<seed>.npz
    anim name  the clip's name in the engine's animation set, e.g. mb_walk_scared (unique: a name that is already in the set is rejected)

Steps (each measured, see motionbricks_fbx.py for how the engine reads an FBX): retarget G1 -> Bip01 (motionbricks_retarget), cut a seamless cycle (motionbricks_loop), write the curves into a copy of a Crytek
locomotion FBX (motionbricks_fbx), compile with rc.exe (fbx_to_caf) and save <anim name>.expected.json: the world position of the key joints in the engine's terms at normalized times 0, .25, .5, .75 for
motionbricks_validate.py to compare with what the engine itself computes from the compiled .caf (bridge.character.samplepose).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import json
import os
import re
import sys

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import motionbricks_fbx as MF  # noqa: E402
import motionbricks_loop as ML  # noqa: E402
import motionbricks_retarget as MR  # noqa: E402

TEMPLATE = os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Assets\crytek\crysis-animation-assets\1.0.0\animations\human\male\locomotion\run\relaxed\relaxed_tac_run_nw_fwd_fast_3p_01.FBX")
BINDPOSE = os.path.join(_paths.MOTIONBRICKS_ROOT, "clips", "sdk_player_bindpose.json")
# the joints that MOVE the body. Every other animated model of the template (the hand IK blend weights, IK targets, aim, camera, footsteps, HUD) keeps the template's own constant: those numbers are weights
# and helper transforms, not poses (the IK blend joints carry their weight in a tiny translation; giving them the bind offset pulled the hands to the weapon targets, 17 cm off)
BODY_RE = re.compile(r"^Bip01 [LR] (Thigh|Calf|Foot|Toe0|Clavicle|UpperArm|Forearm|Hand|Finger\d+)$|^Bip01 (Pelvis|Spine\d?|Neck|Head)$")
CHECK_JOINTS = ["Bip01 Pelvis", "Bip01 Spine3", "Bip01 Head", "Bip01 L Thigh", "Bip01 L Calf", "Bip01 L Foot", "Bip01 L Toe0", "Bip01 R Thigh", "Bip01 R Calf", "Bip01 R Foot", "Bip01 R Toe0", "Bip01 L UpperArm",
                "Bip01 L Forearm", "Bip01 L Hand", "Bip01 R UpperArm", "Bip01 R Forearm", "Bip01 R Hand", "Locator_Locomotion"]


def loop_world(loop, human):
    """World rotations (n+1, J, 3, 3) and positions (n+1, J, 3), metres, of every joint of the cycle in the engine's terms (Z up, +Y forward, locator at (0, v t, 0) at frame t)."""
    lq = loop["local_q"]
    F, J = lq.shape[:2]
    Rw = np.zeros((F, J, 3, 3))
    P = np.zeros((F, J, 3))
    pel = human.ix["Bip01 Pelvis"]
    for j in range(J):
        p = human.parent[j]
        Rl = MR.q_to_mat(lq[:, j])
        if p < 0:
            Rw[:, j] = MR.q_to_mat(np.broadcast_to(human.aq[j], (F, 4)))
            P[:, j] = 0.0
            continue
        Rw[:, j] = Rw[:, p] @ Rl
        if j == pel:
            P[:, j] = loop["loc"] + loop["pelvis_rel"]
        else:
            P[:, j] = P[:, p] + np.einsum("fij,j->fi", Rw[:, p], human.rt[j])
    # the locator joint is not part of the body: it follows the ground path (its rotation stays the bind one under the root)
    return Rw, P


def build_curves(loop, human, tpl):
    """{model name: (T (F,3) cm, R (F,3) degrees)} for every animated model of the template."""
    lq = loop["local_q"]
    F = len(lq)
    pel_ix = human.ix["Bip01 Pelvis"]
    root = int(np.where(human.parent < 0)[0][0])
    Rw_p = MR.q_to_mat(MR.qnorm(MR.qmul(np.broadcast_to(human.aq[root], (F, 4)), lq[:, pel_ix])))   # engine world rotation of the pelvis
    P_p = (loop["loc"] + loop["pelvis_rel"])                                                           # engine world position of the pelvis (m)

    def mat4(Rm, tm):
        out = np.zeros((len(Rm), 4, 4))
        out[:, :3, :3] = Rm
        out[:, :3, 3] = tm
        out[:, 3, 3] = 1.0
        return out

    # template constants (a steady frame of the run clip: the key at frame 1)
    sec = 1.0 / 30.0
    const = {n: tpl.local_at(n, sec) for n in tpl.animated_models()}
    sc = tpl.scene
    Lp = np.array(sc.local(tpl.name_id["Bip01 Pelvis"], sec))                           # pelvis local (4,4), FBX units
    W_pelvis = mat4(np.einsum("ij,fjk->fik", MF.M.T, Rw_p), (P_p * 100.0) @ MF.M)      # M^T applied to the position: p_fbx = M^T p_eng
    W_bip = W_pelvis @ np.linalg.inv(Lp)[None]
    # locator world: a clean 90-degree frame copied from the template's own locator, at the ground path
    C = MF.snap_axes(np.array(sc.world(tpl.name_id["Locator_Locomotion"], sec))[:3, :3] / 1.0)
    # the locator's heading: an engine turn about the vertical by psi is M^T Rz(psi) M about FBX's up axis; a clip that does not turn (every walk cycle) has psi = 0
    yaw = np.asarray(loop.get("loc_yaw", np.zeros(F)), dtype=np.float64)
    Ryf = np.einsum("ij,fjk,kl->fil", MF.M.T, MF.rot_z(yaw), MF.M)
    W_loc = mat4(Ryf @ C[None], (loop["loc"] * 100.0) @ MF.M)
    L_loc = np.linalg.inv(W_bip) @ W_loc

    curves = {}
    for name in tpl.animated_models():
        if name == "Bip01":
            T = W_bip[:, :3, 3]
            Rl = tpl.lcl_from_total(name, W_bip[:, :3, :3])
        elif name == "Bip01 Pelvis":
            tt, rr = const[name]
            curves[name] = (np.tile(tt, (F, 1)), np.tile(rr, (F, 1)))
            continue
        elif name == "Locator_Locomotion":
            T = L_loc[:, :3, 3]
            Rl = tpl.lcl_from_total(name, L_loc[:, :3, :3])
        elif name in human.ix and BODY_RE.match(name):
            j = human.ix[name]
            T = np.tile(human.rt[j] * 100.0, (F, 1))
            Rl = tpl.lcl_from_total(name, MR.q_to_mat(lq[:, j]))
        else:
            tt, rr = const[name]
            curves[name] = (np.tile(tt, (F, 1)), np.tile(rr, (F, 1)))
            continue
        curves[name] = (T, MF.euler_xyz_deg(Rl))
    return curves


def fk_check(curves, tpl, loop, human):
    """Forward kinematics of the curves written (FBX rules, via the template's own scene machinery) against the cycle's world positions: the writer's own error before the engine is involved."""
    Rw, P = loop_world(loop, human)
    errs = []
    sc = tpl.scene
    F = len(loop["local_q"])
    # evaluate FBX world transforms by composing locals in template hierarchy order, with the curve values for animated models and the template's static values for the rest
    order = []

    def depth(mid):
        d, p = 0, sc.parent.get(mid)
        while p in sc.models:
            d += 1
            p = sc.parent.get(p)
        return d
    mids = sorted(sc.models, key=depth)
    for f in range(F):
        W = {}
        for mid in mids:
            name = sc.models[mid]["name"]
            if name in curves:
                t, r = curves[name][0][f], curves[name][1][f]
            else:
                t, r = tpl.local_at(name, 1.0 / 30.0)
            pre, post = tpl.pre_post(name)
            Rtot = pre @ MF.matrix_from_euler_xyz_deg(r) @ post.T
            L = np.eye(4)
            L[:3, :3], L[:3, 3] = Rtot, t
            p = sc.parent.get(mid)
            W[mid] = (W[p] @ L) if p in W else L
        for name in CHECK_JOINTS:
            if name in human.ix and name != "Locator_Locomotion" and name in tpl.name_id:
                w = W[tpl.name_id[name]]
                pe = MF.M @ (w[:3, 3] / 100.0)
                errs.append(float(np.linalg.norm(pe - P[f, human.ix[name]])))
    return float(np.max(errs)), float(np.median(errs))


def expected_samples(loop, human, times=(0.0, 0.25, 0.5, 0.75)):
    Rw, P = loop_world(loop, human)
    n = len(P) - 1
    yaw = np.degrees(np.asarray(loop.get("loc_yaw", np.zeros(n + 1)), dtype=np.float64))          # the locator's heading (positive = left): the engine's root joint (Bip01) must show it
    out = []
    for t in times:
        # sampled on a KEY FRAME: between two keys the engine blends rotations (slerp) while positions are interpolated linearly here, which differs by decimetres during a fast fall or swing
        t = round(t * n) / float(n)
        f = t * n
        f0 = int(np.floor(f))
        f1 = min(f0 + 1, n)
        w = f - f0
        pos = (1 - w) * P[f0] + w * P[f1]
        out.append({"t": t, "frame": f, "root_yaw_deg": float((1 - w) * yaw[f0] + w * yaw[f1]),
                    "joints": {nm: [float(x) for x in pos[human.ix[nm]]] for nm in CHECK_JOINTS if nm in human.ix and nm != "Locator_Locomotion"}})
    return out


MANIFEST = os.path.join(HERE, "motionbricks_clips.json")


def update_manifest(exp, seed, extra=None):
    """Record a built clip (what villager_anim.py reads: the speed the code moves the body at, and where the clip came from) in motionbricks_clips.json."""
    data = json.load(open(MANIFEST)) if os.path.isfile(MANIFEST) else {"note": "Generated clips of the villagers (MotionBricks locomotion, Kimodo actions): name -> source and measured numbers", "clips": {}}
    entry = {"style": exp["style"], "seed": seed, "frames": exp["frames"], "fps": exp["fps"], "duration": round(exp["duration"], 4), "speed": round(exp["speed"], 3),
             "cycle_from_frame": exp.get("cycle_from_frame", 0), "feature_distance": round(exp.get("feature_distance", 0.0), 4)}
    entry.update(extra or {})
    data["clips"][exp["name"]] = entry
    with open(MANIFEST, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")


def finish(name, loop, human, tpl, out_dir, work_dir, info, compile_caf=True):
    """curves -> FBX forward-kinematics check -> FBX -> expected.json -> manifest -> rc.exe, for any clip dict (a cycle, a one-shot, an in-place loop).
    info: {style, seed, kind ('loop'|'oneshot'), plus whatever else the manifest should keep (source, prompt, hit time ...)}."""
    n = int(loop["n"]) if "n" in loop else len(loop["local_q"]) - 1
    curves = build_curves(loop, human, tpl)
    emax, emed = fk_check(curves, tpl, loop, human)
    os.makedirs(work_dir, exist_ok=True)
    fbx = os.path.join(work_dir, name + ".fbx")
    tpl.write(fbx, curves, fps=loop["fps"])
    exp = {"name": name, "style": info.get("style", ""), "frames": n, "fps": loop["fps"], "duration": n / loop["fps"], "speed": loop["speed"], "cycle_from_frame": info.get("cycle_from_frame", 0),
           "feature_distance": info.get("feature_distance", 0.0), "fk_check_max_m": emax, "fk_check_median_m": emed, "expected": expected_samples(loop, human)}
    with open(os.path.join(work_dir, name + ".expected.json"), "w") as f:
        json.dump(exp, f, indent=1)
    root = loop["loc"][-1, :2] - loop["loc"][0, :2]
    extra = {k: v for k, v in info.items() if k not in ("style", "seed", "cycle_from_frame", "feature_distance")}
    extra.update({"root_travel": [round(float(root[0]), 3), round(float(root[1]), 3)], "end_yaw_deg": round(float(np.degrees(loop["loc_yaw"][-1])) if "loc_yaw" in loop else 0.0, 1)})
    yaw = loop.get("loc_yaw")
    if yaw is not None and float(np.max(np.abs(yaw))) > 1e-4:
        # the locator's heading in degrees every 0.1 s, then the end value: how the code turns the body along with the clip (villager_anim.py writes the normalised curve into the C++ table)
        t = np.arange(len(yaw)) / loop["fps"]
        extra["yaw_profile"] = [round(float(np.degrees(np.interp(s, t, yaw))), 2) for s in np.arange(0.0, t[-1] + 1e-9, 0.1)] + [round(float(np.degrees(yaw[-1])), 2)]
    update_manifest(exp, info.get("seed", 0), extra)
    if compile_caf:
        import fbx_to_caf
        target, size, _ = fbx_to_caf.convert(fbx, out_dir, start_frame=0, end_frame=n + 1)
        return exp, target, size
    return exp, None, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("style")
    ap.add_argument("name")
    ap.add_argument("--clips", default=os.path.join(_paths.MOTIONBRICKS_ROOT, "clips"))
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--out", default=os.path.join(_paths.MOTIONBRICKS_ROOT, "work", "caf_out"))
    ap.add_argument("--work", default=os.path.join(_paths.MOTIONBRICKS_ROOT, "work"))
    ap.add_argument("--no-rc", action="store_true")
    ap.add_argument("--min", type=float, default=0.6)
    ap.add_argument("--max", type=float, default=2.4)
    ap.add_argument("--skip", type=float, default=1.0)
    a = ap.parse_args()
    raw = os.path.join(a.clips, "%s_%d.npz" % (a.style, a.seed))
    ret = os.path.join(a.clips, "%s_%d_bip01.npz" % (a.style, a.seed))
    human = MR.Human(BINDPOSE)
    r = MR.retarget(dict(np.load(raw, allow_pickle=True)), human)
    np.savez_compressed(ret, local_q=r["local_q"], pelvis=r["pelvis"], world_p=r["world_p"].astype(np.float32), fps=r["fps"], names=np.array(r["names"]), parent=r["parent"], scale=r["scale"])
    rt = ML.Retargeted(ret, BINDPOSE)
    i0, n, d, gmin, _ = ML.find_cycle(rt, a.min, a.max, a.skip)
    loop = ML.build_loop(rt, i0, n)
    tpl = MF.Template(TEMPLATE)
    curves = build_curves(loop, human, tpl)
    emax, emed = fk_check(curves, tpl, loop, human)
    os.makedirs(a.work, exist_ok=True)
    fbx = os.path.join(a.work, a.name + ".fbx")
    tpl.write(fbx, curves, fps=rt.fps)
    exp = {"name": a.name, "style": a.style, "frames": n, "fps": rt.fps, "duration": n / rt.fps, "speed": loop["speed"], "cycle_from_frame": i0, "feature_distance": d, "fk_check_max_m": emax, "fk_check_median_m": emed,
           "expected": expected_samples(loop, human)}
    with open(os.path.join(a.work, a.name + ".expected.json"), "w") as f:
        json.dump(exp, f, indent=1)
    update_manifest(exp, a.seed)
    print("%s: cycle %d frames (%.2f s) at %.2f m/s from frame %d; FBX forward kinematics vs the cycle: max %.4f m, median %.5f m -> %s" % (a.name, n, n / rt.fps, loop["speed"], i0, emax, emed, fbx))
    if not a.no_rc:
        import fbx_to_caf
        target, size, _ = fbx_to_caf.convert(fbx, a.out, start_frame=0, end_frame=n + 1)
        print("compiled %s (%d bytes)" % (target, size))


if __name__ == "__main__":
    main()
