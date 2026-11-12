#!/usr/bin/env python3
"""Record SCRIPTED MotionBricks scenarios (starts, stops, turns on the spot), headless. Run with the MotionBricks venv's Python, NOT the project's:

    <MOTIONBRICKS_ROOT>\\venv\\Scripts\\python.exe motionbricks_scenario.py <scenario> [<scenario> ...] [--seed 1234] [--out <MOTIONBRICKS_ROOT>\\clips]
    ... motionbricks_scenario.py --list

motionbricks_record.py holds one key state for the whole recording (a steady gait). This one changes the controller's signals over time, which is how the model is asked for a START (idle, then a walking
style), a STOP (a walking style, then idle) and a TURN ON THE SPOT (idle with the facing direction rotated): the signals the demo's WASD controller makes are movement_direction (where to go), facing_direction
(where to look) and the mode, as vectors in the MuJoCo frame (+X forward at the start, +Y left); the model generates the transition. A segment is (frames at 30 fps, mode, movement angle deg, facing angle deg,
moving): moving=False repeats the demo's idle rule (the velocity and heading carried on). Output <out>/<scenario>_<seed>.npz in the format of motionbricks_record.py (qpos, positions, rotations, fps, ...).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import os
import sys
import time

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import motionbricks_record as MRC  # noqa: E402  (also installs the keyboard stub and the torch.distributed shim)

STYLES = {"": "walk", "scared": "walk_scared", "injured": "injured_walk", "stealth": "walk_stealth", "slow": "slow_walk"}


def scenarios():
    s = {}
    for tag, mode in STYLES.items():
        suf = ("_" + tag) if tag else ""
        s["start" + suf] = [(30, "idle", 0, 0), (120, mode, 0, 0)]
        s["stop" + suf] = [(120, mode, 0, 0), (100, "idle", 0, 0)]
    s["turn_left_90"] = [(45, "idle", 0, 0), (130, "idle", 0, 90)]
    s["turn_right_90"] = [(45, "idle", 0, 0), (130, "idle", 0, -90)]
    s["turn_180"] = [(45, "idle", 0, 0), (160, "idle", 0, 180)]
    s["idle"] = [(240, "idle", 0, 0)]
    return s


def run(demo, mode_names, segments, seed):
    import torch as t
    agent, controller = demo.full_agent, demo.controller
    np.random.seed(seed)
    t.manual_seed(seed)
    agent.reset()
    frames = []
    seg_of = []
    last_move = np.array([1.0, 0.0, 0.0])
    last_face = np.array([1.0, 0.0, 0.0])
    for si, (n, mode, move_deg, face_deg, *rest) in enumerate([tuple(x) + (None,) if len(x) == 4 else tuple(x) for x in segments]):
        for _ in range(n):
            qpos = agent.get_next_frame()
            context = agent.get_context_mujoco_qpos()
            demo.mj_data.qpos[:] = qpos
            frames.append(np.array(qpos, dtype=np.float32).reshape(-1))
            seg_of.append(si)
            mv = np.array([np.cos(np.radians(move_deg)), np.sin(np.radians(move_deg)), 0.0])
            fc = np.array([np.cos(np.radians(face_deg)), np.sin(np.radians(face_deg)), 0.0])
            if mode == "idle":
                mv = last_move
                if face_deg == 0 and si == 0:
                    fc = last_face
            else:
                last_move = mv
            signals = {"movement_direction": t.from_numpy(mv).view([1, -1]), "facing_direction": t.from_numpy(fc).view([1, -1]), "mode": t.tensor([[mode_names.index(mode)]]),
                       "allowed_pred_num_tokens": controller.get_default_allowed_pred_num_tokens(mode_names.index(mode)), "context_mujoco_qpos": context}
            last_face = fc
            with t.no_grad():
                agent.generate_new_frames(signals, controller.get_controller_dt() * 2.0)
    q = np.stack(frames)
    conv = agent._converter
    dev = next(agent.parameters()).device if any(True for _ in agent.parameters()) else "cpu"
    with t.no_grad():
        pos, rot = conv.convert_mujoco_qpos_to_motion_transforms(t.from_numpy(q)[None].float().to(dev))
    sk = agent._motion_rep.skeleton
    return q, pos[0].cpu().numpy(), rot[0].cpu().numpy(), sk, np.array(seg_of)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--out", default=os.path.join(_paths.MOTIONBRICKS_ROOT, "clips"))
    a = ap.parse_args()
    sc = scenarios()
    if a.list or not a.names:
        for k, v in sc.items():
            print("%-22s %s" % (k, v))
        return
    from motionbricks.motion_backbone.demo.clips import clip_holder_G1
    mode_names = list(clip_holder_G1.CLIPS.keys())
    demo = MRC.build(MRC.make_args(a.seed))
    os.makedirs(a.out, exist_ok=True)
    for name in a.names:
        t0 = time.time()
        q, pos, rot, sk, seg = run(demo, mode_names, sc[name], a.seed)
        path = os.path.join(a.out, "%s_%d.npz" % (name, a.seed))
        np.savez_compressed(path, qpos=q, positions=pos, rotations=rot, fps=30.0, mode=name, direction="scenario", segments=seg,
                            joint_names=np.array(sk.bone_order_names),
                            joint_parents=np.array([sk.bone_order_names.index(sk.bone_parents[n]) if sk.bone_parents.get(n) in sk.bone_order_names else -1 for n in sk.bone_order_names]),
                            neutral_joints=sk.neutral_joints.cpu().numpy() if hasattr(sk.neutral_joints, "cpu") else np.asarray(sk.neutral_joints))
        print("%s: %d frames in %.1f s; root travelled %.2f m -> %s" % (name, len(q), time.time() - t0, float(np.linalg.norm(q[-1, :2] - q[0, :2])), path), flush=True)


if __name__ == "__main__":
    main()
