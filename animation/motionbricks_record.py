#!/usr/bin/env python3
"""Record NVIDIA MotionBricks motion to a file, headless (no viewer, no keyboard). Run with the MotionBricks venv's Python, NOT the project's:

    <MOTIONBRICKS_ROOT>\\venv\\Scripts\\python.exe motionbricks_record.py <mode> [--steps 600] [--seed 1234] [--out <MOTIONBRICKS_ROOT>\\clips] [--direction forward|idle]
    ... motionbricks_record.py --list          # the style names (modes) the released checkpoint knows

What it does (the demo's own headless loop, scripts/interactive_demo_g1.py with --has_viewer 0): builds the model with motionbricks.motion_backbone.demo.utils.navigation_demo ('cuda', which is the AMD GPU under
ROCm PyTorch), then every step takes the agent's next frame, builds the control signals with the demo's WASD controller (a fixed pretend camera looking along +X and a pretend key state: forward, plus the style's
key) and lets the agent generate more. The recorded frames are saved as <out>/<mode>_<seed>.npz: qpos (F, 36: root xyz, root quaternion w x y z, 29 hinge angles, MuJoCo Z-up), the G1's 34 global joint positions
(F, 34, 3) and global rotation matrices (F, 34, 3, 3) in the motion space (Y-up, Z-forward: the clip holder's own conversion), fps, the joint names and parents and the rest pose.

The released checkpoint drives the Unitree G1 ROBOT skeleton (no head, neck or fingers); nothing here changes the model. Source (read-only reference, Apache 2.0): <MOTIONBRICKS_ROOT>\\repo\\motionbricks.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import os
import sys
import time
import types

import numpy as np

REPO = os.path.join(_paths.MOTIONBRICKS_ROOT, "repo", "motionbricks")
# the demo imports the Windows 'keyboard' package at load time to poll keys; headless it is never asked, so a stub keeps a global keyboard hook out of the process
sys.modules.setdefault("keyboard", types.SimpleNamespace(is_pressed=lambda key: False))


def shim_distributed():
    """AMD's Windows ROCm PyTorch is built WITHOUT torch.distributed (torch.distributed.group does not exist), and vector_quantize_pytorch's lookup_free_quantization imports torch.distributed.nn
    unconditionally at load time; it uses dist_nn.all_reduce only when training across processes. Give it a stub that fails loudly if anything ever calls it ."""
    import torch
    import torch.distributed as dist

    def _unavailable(*args, **kwargs):
        raise RuntimeError("torch.distributed is not built into this PyTorch (AMD Windows ROCm): this call is for multi-process training")
    # the functions the libraries ask (vector_quantize_pytorch, motionbricks): 'is it distributed?' must answer no; the collective calls are only reached when it says yes
    for name, fn in (("is_initialized", lambda: False), ("get_world_size", lambda *a, **k: 1), ("get_rank", lambda *a, **k: 0), ("all_reduce", _unavailable), ("broadcast", _unavailable),
                     ("barrier", _unavailable), ("all_gather", _unavailable)):
        if not hasattr(dist, name):
            setattr(dist, name, fn)
    try:
        from torch.distributed import nn as _nn  # noqa: F401
        return False
    except ImportError:
        pass
    stub = types.ModuleType("torch.distributed.nn")
    stub.all_reduce = stub.all_gather = stub.broadcast = _unavailable
    sys.modules["torch.distributed.nn"] = stub
    dist.nn = stub
    return True


shim_distributed()


def build(args_ns):
    os.chdir(REPO)                                   # the demo's default asset and checkpoint paths are relative to it
    from motionbricks.motion_backbone.demo.utils import navigation_demo
    return navigation_demo(args_ns)


def make_args(seed):
    from types import SimpleNamespace
    return SimpleNamespace(
        humanoid_xml="assets/skeletons/g1/scene_29dof.xml", result_dir="./out", data_root="./datasets", explicit_dataset_folder=None, reprocess_clips=0,
        controller="wasd", lookat_movement_direction=0, has_viewer=0, pre_filter_qpos=1, source_root_realignment=1, target_root_realignment=1, force_canonicalization=1,
        skip_ending_target_cond=0, random_speed_scale=0, speed_scale=[0.8, 1.2], generate_dt=2.0, max_steps=10000, random_seed=seed, num_runs=1,
        use_qpos=1, planner="default", allowed_mode=None, clips="G1", return_model_configs=True, return_dataloader=True, recording_dir=None, EXP="default")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--steps", type=int, default=600)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--out", default=os.path.join(_paths.MOTIONBRICKS_ROOT, "clips"))
    ap.add_argument("--direction", default="forward", choices=["forward", "idle"])
    a = ap.parse_args()
    import torch as t
    demo = build(make_args(a.seed))
    from motionbricks.motion_backbone.demo.clips import clip_holder_G1
    modes = list(clip_holder_G1.CLIPS.keys())
    keys = clip_holder_G1.DEFAULT_KEYS
    if a.list or not a.mode:
        for m in modes:
            print("%-18s key '%s'" % (m, keys.get(m, "")))
        return
    if a.mode not in modes:
        raise SystemExit("unknown mode %r; one of %s" % (a.mode, modes))
    np.random.seed(a.seed)
    t.manual_seed(a.seed)
    agent, controller = demo.full_agent, demo.controller
    agent.reset()
    cam = types.SimpleNamespace(lookat=np.zeros(3), distance=3.0, azimuth=0.0, elevation=-20.0)   # the pretend camera looks along +X, so 'w' walks along +X
    viewer = types.SimpleNamespace(cam=cam)
    pressed = {k: False for k in "wasdxzcvbrtfgqe"}
    pressed["left"] = pressed["right"] = pressed["up"] = pressed["down"] = pressed["shift"] = pressed["ctrl"] = pressed["enter"] = False
    if a.direction == "forward":
        pressed["w"] = True
    style_key = keys.get(a.mode, "")
    if style_key:
        pressed[style_key] = True
    frames = []
    t0 = time.time()
    for step in range(a.steps):
        qpos = agent.get_next_frame()
        context_qpos = agent.get_context_mujoco_qpos()
        demo.mj_data.qpos[:] = qpos
        frames.append(np.array(qpos, dtype=np.float32).reshape(-1))
        signals = controller.generate_control_signals(viewer, demo.mj_model, demo.mj_data, visualize=False,
                                                      control_info={"force_idle": False, "allowed_mode": None, "key_pressed": dict(pressed)})
        signals["context_mujoco_qpos"] = context_qpos
        with t.no_grad():
            agent.generate_new_frames(signals, controller.get_controller_dt() * 2.0)
    secs = time.time() - t0
    q = np.stack(frames)
    conv = agent._converter
    dev = next(agent.parameters()).device if any(True for _ in agent.parameters()) else "cpu"
    with t.no_grad():
        pos, rot = conv.convert_mujoco_qpos_to_motion_transforms(t.from_numpy(q)[None].float().to(dev))
    sk = agent._motion_rep.skeleton
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "%s_%d.npz" % (a.mode, a.seed))
    np.savez_compressed(path, qpos=q, positions=pos[0].cpu().numpy(), rotations=rot[0].cpu().numpy(), fps=float(agent._fps), mode=a.mode, direction=a.direction,
                        joint_names=np.array(sk.bone_order_names), joint_parents=np.array([sk.bone_order_names.index(sk.bone_parents[n]) if sk.bone_parents.get(n) in sk.bone_order_names else -1 for n in sk.bone_order_names]),
                        neutral_joints=sk.neutral_joints.cpu().numpy() if hasattr(sk.neutral_joints, "cpu") else np.asarray(sk.neutral_joints))
    dist = float(np.linalg.norm(q[-1, :2] - q[0, :2]))
    print("%s: %d frames at %.0f fps in %.1f s (%.0f frames/s generated); root travelled %.2f m; wrote %s" % (a.mode, len(q), float(agent._fps), secs, len(q) / max(secs, 1e-6), dist, path))


if __name__ == "__main__":
    main()
