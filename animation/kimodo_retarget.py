#!/usr/bin/env python3
"""Retarget NVIDIA Kimodo motion (the SOMA 77-joint human skeleton, text-to-motion) onto the villagers' Bip01 rig. Project Python, numpy only.

    python animation/kimodo_retarget.py <kimodo.npz> <out_bip01.npz> [--bindpose sdk_player_bindpose.json] [--skeleton soma77_skeleton.json]

The same method as motionbricks_retarget.py (G1 robot), with the extra detail a human skeleton offers, verified there against the engine:
  * LIMBS by direction: a human bone is turned by the minimal swing that takes its bind direction to the matching SOMA bone's direction; the fingers (thumb 3 bones, the four fingers 4 each) too.
  * HINGES: knees and elbows are hinges (the FBX rig limits X/Y rotation of Calf and Forearm, rc.exe silently clips anything else): motionbricks_retarget.apply_hinge builds the upper bone from the plane of the limb.
  * PELVIS, TORSO and HEAD by frame transfer: pelvis (hip line + spine), torso (shoulder line + spine), head (eye line + head axis); each frame's world rotation relative to its own T-pose is applied to the human bind
    rotation; the four spine bones share the pelvis -> torso turn, the neck is halfway torso -> head.
  * HANDS by a palm frame (middle-finger direction + the line across the knuckles), so the wrist roll is kept.
  * CLAVICLES by direction (the shoulder girdle: the G1 had none).
  * ROOT: the pelvis path scaled by the leg-length ratio; the height from the LOWEST contact point of the whole body (soles, but also back, pelvis, head, hands when the body is on the ground: a death clip
    or a sit-down must not sink into the floor).
Kimodo's axes are the G1's: Y up, Z forward, X = the character's left, metres.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import json
import os
import sys

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import motionbricks_retarget as MR  # noqa: E402
from motionbricks_retarget import frame_from, mat_to_q, q_to_mat, qconj, qmul, qnorm, qslerp, qswing, to_cry  # noqa: E402

SPINE = ["Bip01 Spine", "Bip01 Spine1", "Bip01 Spine2", "Bip01 Spine3"]
# human joint, SOMA start, SOMA end, human child (its bind position gives the bone's bind direction; None: continue the parent's direction)
FINGERS = {"thumb": (["Finger0", "Finger01", "Finger02"], "Thumb", ["1", "2", "3", "End"]),
           "index": (["Finger10", "Finger1", "Finger11", "Finger12"], "Index", ["1", "2", "3", "4", "End"]),
           "middle": (["Finger20", "Finger2", "Finger21", "Finger22"], "Middle", ["1", "2", "3", "4", "End"]),
           "ring": (["Finger30", "Finger3", "Finger31", "Finger32"], "Ring", ["1", "2", "3", "4", "End"]),
           "pinky": (["Finger40", "Finger4", "Finger41", "Finger42"], "Pinky", ["1", "2", "3", "4", "End"])}
# what the body can rest on: (human joint, radius above the ground when it touches it)
CONTACT = {"Bip01 Pelvis": 0.10, "Bip01 Spine1": 0.10, "Bip01 Spine3": 0.10, "Bip01 Head": 0.09, "Bip01 L Hand": 0.04, "Bip01 R Hand": 0.04, "Bip01 L Forearm": 0.04, "Bip01 R Forearm": 0.04,
           "Bip01 L UpperArm": 0.05, "Bip01 R UpperArm": 0.05, "Bip01 L Calf": 0.06, "Bip01 R Calf": 0.06, "Bip01 L Thigh": 0.08, "Bip01 R Thigh": 0.08, "Bip01 L Foot": 0.085, "Bip01 R Foot": 0.085,
           "Bip01 L Toe0": 0.03, "Bip01 R Toe0": 0.03}


class Soma:
    def __init__(self, path):
        d = json.load(open(path))
        self.names = d["names"]
        self.ix = {n: i for i, n in enumerate(self.names)}
        self.parent = np.array(d["parents"])
        self.neutral = np.array(d["neutral_joints"], dtype=np.float64)


def limb_table(side):
    """(human joint, soma start, soma end, human child or None) for one side ('L' / 'R')."""
    S = "Left" if side == "L" else "Right"
    H = "Bip01 %s " % side
    t = [(H + "Clavicle", S + "Shoulder", S + "Arm", H + "UpperArm"),
         (H + "Thigh", S + "Leg", S + "Shin", H + "Calf"), (H + "Calf", S + "Shin", S + "Foot", H + "Foot"), (H + "Foot", S + "Foot", S + "ToeBase", H + "Toe0"),
         (H + "UpperArm", S + "Arm", S + "ForeArm", H + "Forearm"), (H + "Forearm", S + "ForeArm", S + "Hand", H + "Hand")]
    for _, (hj, sn, parts) in FINGERS.items():
        for k, name in enumerate(hj):
            child = (H + hj[k + 1]) if k + 1 < len(hj) else None
            t.append((H + name, "%sHand%s%s" % (S, sn, parts[k]), "%sHand%s%s" % (S, sn, parts[k + 1]), child))
    return t


HINGES = [("Bip01 L Thigh", "Bip01 L Calf", "LeftLeg", "LeftShin", "LeftFoot"), ("Bip01 R Thigh", "Bip01 R Calf", "RightLeg", "RightShin", "RightFoot"),
          ("Bip01 L UpperArm", "Bip01 L Forearm", "LeftArm", "LeftForeArm", "LeftHand"), ("Bip01 R UpperArm", "Bip01 R Forearm", "RightArm", "RightForeArm", "RightHand")]


def palm_frame(origin, middle, index, pinky):
    """Frame (columns x y z): x along the hand (origin -> middle finger root), z across the knuckles (pinky -> index), y = z cross x. Arrays (..., 3)."""
    x = middle - origin
    x = x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-12)
    b = index - pinky
    z = b - np.sum(b * x, axis=-1, keepdims=True) * x
    z = z / np.maximum(np.linalg.norm(z, axis=-1, keepdims=True), 1e-12)
    y = np.cross(z, x)
    return np.stack([x, y, z], axis=-1)


def retarget(posed_joints, human, soma, fps=30.0):
    pos = to_cry(posed_joints).astype(np.float64)           # (F, 77, 3) metres in CRYENGINE axes
    neutral = to_cry(soma.neutral)
    F, J = len(pos), len(human.names)
    sx = soma.ix
    g = lambda n: pos[:, sx[n]]
    g0 = lambda n: neutral[sx[n]][None]
    leg = lambda get: np.linalg.norm(get("LeftShin") - get("LeftLeg"), axis=-1) + np.linalg.norm(get("LeftFoot") - get("LeftShin"), axis=-1)
    scale = float(human.leg_length() / max(float(leg(g0)[0]), 1e-6))

    def pelvis_frame(get):
        return frame_from(get("RightLeg") - get("LeftLeg"), get("Spine1") - get("Hips"))

    def torso_frame(get):
        return frame_from(get("RightArm") - get("LeftArm"), get("Neck1") - get("Spine2"))

    def head_frame(get):
        return frame_from(get("RightEye") - get("LeftEye"), get("HeadEnd") - get("Head"))
    delta = {}
    for key, fn in (("pelvis", pelvis_frame), ("torso", torso_frame), ("head", head_frame)):
        Ft, F0 = fn(g), fn(g0)[0]
        delta[key] = mat_to_q(np.einsum("fij,jk->fik", Ft, F0.T))
    ix = human.ix
    Rw = np.zeros((F, J, 4))
    Rw[:, :] = human.aq[None]
    driven = np.zeros(J, bool)

    def setw(name, d):
        j = ix[name]
        Rw[:, j] = qmul(d, np.broadcast_to(human.aq[j], (F, 4)))
        driven[j] = True
    setw("Bip01 Pelvis", delta["pelvis"])
    for k, n in enumerate(SPINE):
        setw(n, qslerp(delta["pelvis"], delta["torso"], (k + 1) / float(len(SPINE))))
    setw("Bip01 Neck", qslerp(delta["torso"], delta["head"], 0.5))
    setw("Bip01 Head", delta["head"])

    for side in ("L", "R"):
        for hname, a, b, child in limb_table(side):
            if hname not in ix or a not in sx or b not in sx:
                continue
            j = ix[hname]
            if child is not None and child in ix:
                bind_dir = human.at[ix[child]] - human.at[j]
            else:                                            # the last bone of a finger: it continues its parent's direction
                bind_dir = human.at[j] - human.at[human.parent[j]]
            target = g(b) - g(a)
            ok = np.linalg.norm(target, axis=-1, keepdims=True) > 1e-6
            sw = qswing(np.broadcast_to(bind_dir, target.shape), np.where(ok, target, bind_dir))
            Rw[:, j] = qmul(sw, np.broadcast_to(human.aq[j], (F, 4)))
            driven[j] = True
    for up, lo, ga, gb, gc in HINGES:
        MR.apply_hinge(human, Rw, ix, g, up, lo, ga, gb, gc)

    # hands: the palm frame keeps the wrist roll
    for side in ("L", "R"):
        S = "Left" if side == "L" else "Right"
        H = "Bip01 %s " % side
        jh = ix[H + "Hand"]
        B0 = palm_frame(human.at[jh], human.at[ix[H + "Finger20"]], human.at[ix[H + "Finger10"]], human.at[ix[H + "Finger40"]])
        T = palm_frame(g(S + "Hand"), g("%sHandMiddle1" % S), g("%sHandIndex1" % S), g("%sHandPinky1" % S))
        R = T @ B0.T[None] @ q_to_mat(human.aq[jh])[None]
        Rw[:, jh] = mat_to_q(R)
        driven[jh] = True

    # local rotations and world positions of the whole rig
    L = np.zeros((F, J, 4))
    L[:] = human.rq[None]
    for j in range(J):
        p = human.parent[j]
        if p < 0:
            continue
        if driven[j]:
            L[:, j] = qnorm(qmul(qconj(Rw[:, p]), Rw[:, j]))
        else:
            Rw[:, j] = qnorm(qmul(Rw[:, p], np.broadcast_to(human.rq[j], (F, 4))))
    for j in range(J):
        p = human.parent[j]
        if p >= 0 and driven[j]:
            L[:, j] = qnorm(qmul(qconj(Rw[:, p]), Rw[:, j]))
    root_xy = (pos[:, sx["Hips"], :2] - pos[0, sx["Hips"], :2]) * scale
    pelvis_world = np.zeros((F, 3))
    pelvis_world[:, :2] = root_xy
    pelvis_world[:, 2] = human.at[ix["Bip01 Pelvis"], 2]
    P = np.zeros((F, J, 3))
    for j in range(J):
        p = human.parent[j]
        if p < 0:
            continue
        P[:, j] = pelvis_world if j == ix["Bip01 Pelvis"] else P[:, p] + MR.qrot(Rw[:, p], np.broadcast_to(human.rt[j], (F, 3)))
    low = np.min(np.stack([P[:, ix[n], 2] - r for n, r in CONTACT.items()], axis=1), axis=1)
    shift = -low
    pelvis_world[:, 2] += shift
    P[:, :, 2] += shift[:, None]
    return {"local_q": L, "pelvis": pelvis_world, "world_q": Rw, "world_p": P, "fps": float(fps), "scale": scale, "names": human.names, "parent": human.parent, "driven": driven}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("out")
    ap.add_argument("--bindpose", default=os.path.join(_paths.MOTIONBRICKS_ROOT, "clips", "sdk_player_bindpose.json"))
    ap.add_argument("--skeleton", default=os.path.join(_paths.MOTIONBRICKS_ROOT, "clips", "soma77_skeleton.json"))
    a = ap.parse_args()
    d = np.load(a.clip)
    human, soma = MR.Human(a.bindpose), Soma(a.skeleton)
    r = retarget(d["posed_joints"], human, soma)
    np.savez_compressed(a.out, local_q=r["local_q"], pelvis=r["pelvis"], world_p=r["world_p"].astype(np.float32), fps=r["fps"], names=np.array(r["names"]), parent=r["parent"], scale=r["scale"])
    print("retargeted %d frames: %d of %d joints driven, scale %.3f; pelvis height %.3f..%.3f m; root travel %.2f m -> %s" % (
        len(r["pelvis"]), int(r["driven"].sum()), len(r["names"]), r["scale"], r["pelvis"][:, 2].min(), r["pelvis"][:, 2].max(), float(np.linalg.norm(r["pelvis"][-1, :2] - r["pelvis"][0, :2])), a.out))


if __name__ == "__main__":
    main()
