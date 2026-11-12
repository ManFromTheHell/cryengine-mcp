#!/usr/bin/env python3
"""Retarget recorded MotionBricks motion (the Unitree G1 skeleton) onto the villagers' human rig (sdk_player: Bip01), numpy only.

    python animation/motionbricks_retarget.py <clip.npz> <bindpose.json> [--out <retargeted.npz>] [--preview <png>]

bindpose.json is the engine's own bind pose of the character (bridge.character.bindpose: 163 joints, absolute and parent-relative transforms, CRYENGINE character space: Z up, Y forward, X right, metres,
quaternions w x y z). Method, per frame (the two rigs have different proportions AND different rest poses, so no rotation is copied):
  * LIMBS by direction: a human bone is turned by the minimal swing that takes its bind direction (joint -> child joint) to the direction of the matching G1 bone (hip->knee, knee->ankle, ankle->toe,
    shoulder->elbow, elbow->wrist, wrist->hand). The bone keeps its bind twist. That works whatever the rest poses are (the G1 stands with arms hanging; the villager's arms are held out a little).
  * PELVIS and TORSO by frame: each has a frame built from two vectors (the hip line and the spine; the shoulder line and the spine); its WORLD rotation relative to the G1's own rest frame is applied to the
    human bind rotation (both rigs rest upright and facing forward, so rest frames agree). The four spine bones share the pelvis->torso turn equally; neck and head follow the torso.
  * Everything the G1 does not have (clavicles, fingers, twist and helper joints) keeps its bind LOCAL pose.
  * ROOT: the pelvis ground path is the G1's, scaled by the leg-length ratio; its height is set each frame so the lowest sole point just touches the ground.
The G1 has no head, neck or fingers and no shoulder blades: those stay still. Output: local rotations (F, 163, 4 wxyz), the pelvis position, the world positions of every joint (for checks) and fps.
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

# human joint -> (G1 start joint, G1 end joint, human child joint whose bind direction is the bone's)
LIMBS = [
    ("Bip01 L Thigh", "left_hip_pitch_skel", "left_knee_skel", "Bip01 L Calf"), ("Bip01 L Calf", "left_knee_skel", "left_ankle_roll_skel", "Bip01 L Foot"),
    ("Bip01 L Foot", "left_ankle_roll_skel", "left_toe_base", "Bip01 L Toe0"),
    ("Bip01 R Thigh", "right_hip_pitch_skel", "right_knee_skel", "Bip01 R Calf"), ("Bip01 R Calf", "right_knee_skel", "right_ankle_roll_skel", "Bip01 R Foot"),
    ("Bip01 R Foot", "right_ankle_roll_skel", "right_toe_base", "Bip01 R Toe0"),
    ("Bip01 L UpperArm", "left_shoulder_pitch_skel", "left_elbow_skel", "Bip01 L Forearm"), ("Bip01 L Forearm", "left_elbow_skel", "left_wrist_roll_skel", "Bip01 L Hand"),
    ("Bip01 L Hand", "left_wrist_roll_skel", "left_hand_roll_skel", "Bip01 L Finger2"),
    ("Bip01 R UpperArm", "right_shoulder_pitch_skel", "right_elbow_skel", "Bip01 R Forearm"), ("Bip01 R Forearm", "right_elbow_skel", "right_wrist_roll_skel", "Bip01 R Hand"),
    ("Bip01 R Hand", "right_wrist_roll_skel", "right_hand_roll_skel", "Bip01 R Finger2"),
]
# The villagers' knees and elbows are HINGES: the FBX rig limits the Lcl X and Y rotation of Calf and Forearm (RotationMinX/MinY/MaxX/MaxY active, Max biped), so rc.exe keeps only the rotation about
# local Z and anything else is clipped (measured: the engine's calf had 12 degrees less than written, the forearm 35). The upper bone must therefore take the twist: its frame is built from the
# bone direction and the plane the three joints span (the hinge axis is that plane's normal), and the lower bone only bends about it.
# human upper joint, human lower joint, G1 start / middle / end joint
HINGES = [
    ("Bip01 L Thigh", "Bip01 L Calf", "left_hip_pitch_skel", "left_knee_skel", "left_ankle_roll_skel"),
    ("Bip01 R Thigh", "Bip01 R Calf", "right_hip_pitch_skel", "right_knee_skel", "right_ankle_roll_skel"),
    ("Bip01 L UpperArm", "Bip01 L Forearm", "left_shoulder_pitch_skel", "left_elbow_skel", "left_wrist_roll_skel"),
    ("Bip01 R UpperArm", "Bip01 R Forearm", "right_shoulder_pitch_skel", "right_elbow_skel", "right_wrist_roll_skel"),
]
SPINE = ["Bip01 Spine", "Bip01 Spine1", "Bip01 Spine2", "Bip01 Spine3"]
FOLLOW_TORSO = ["Bip01 Neck", "Bip01 Head"]


# ----------------------------------------------------------------------------- quaternions (w, x, y, z)
def qmul(a, b):
    aw, ax, ay, az = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    bw, bx, by, bz = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack([aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw], axis=-1)


def qconj(q):
    return q * np.array([1.0, -1.0, -1.0, -1.0])


def qnorm(q):
    return q / np.maximum(np.linalg.norm(q, axis=-1, keepdims=True), 1e-12)


def qrot(q, v):
    qv = np.concatenate([np.zeros(v.shape[:-1] + (1,)), v], axis=-1)
    return qmul(qmul(q, qv), qconj(q))[..., 1:]


def qswing(a, b):
    """The shortest rotation taking unit vector a to unit vector b (broadcasting)."""
    a = a / np.maximum(np.linalg.norm(a, axis=-1, keepdims=True), 1e-12)
    b = b / np.maximum(np.linalg.norm(b, axis=-1, keepdims=True), 1e-12)
    d = np.sum(a * b, axis=-1, keepdims=True)
    axis = np.cross(a, b)
    q = np.concatenate([1.0 + d, axis], axis=-1)
    anti = (d[..., 0] < -0.999999)
    if np.any(anti):                                         # opposite vectors: any perpendicular axis
        perp = np.cross(a, np.array([1.0, 0.0, 0.0]))
        perp = np.where(np.linalg.norm(perp, axis=-1, keepdims=True) < 1e-6, np.cross(a, np.array([0.0, 1.0, 0.0])), perp)
        q = np.where(anti[..., None], np.concatenate([np.zeros_like(d), perp / np.maximum(np.linalg.norm(perp, axis=-1, keepdims=True), 1e-12)], axis=-1), q)
    return qnorm(q)


def qslerp(a, b, t):
    d = np.sum(a * b, axis=-1, keepdims=True)
    b = np.where(d < 0, -b, b)
    d = np.abs(d)
    theta = np.arccos(np.clip(d, -1.0, 1.0))
    s = np.sin(theta)
    wa = np.where(s < 1e-6, 1.0 - t, np.sin((1.0 - t) * theta) / np.maximum(s, 1e-12))
    wb = np.where(s < 1e-6, t, np.sin(t * theta) / np.maximum(s, 1e-12))
    return qnorm(wa * a + wb * b)


def mat_to_q(m):
    """rotation matrices (..., 3, 3) -> quaternions (..., 4), robust branch-free-ish (Shepperd)."""
    m = np.asarray(m, dtype=np.float64)
    t = m[..., 0, 0] + m[..., 1, 1] + m[..., 2, 2]
    q = np.zeros(m.shape[:-2] + (4,))
    for idx in np.ndindex(m.shape[:-2]):
        a = m[idx]
        tr = t[idx]
        if tr > 0:
            s = np.sqrt(tr + 1.0) * 2
            q[idx] = [0.25 * s, (a[2, 1] - a[1, 2]) / s, (a[0, 2] - a[2, 0]) / s, (a[1, 0] - a[0, 1]) / s]
        elif a[0, 0] > a[1, 1] and a[0, 0] > a[2, 2]:
            s = np.sqrt(1.0 + a[0, 0] - a[1, 1] - a[2, 2]) * 2
            q[idx] = [(a[2, 1] - a[1, 2]) / s, 0.25 * s, (a[0, 1] + a[1, 0]) / s, (a[0, 2] + a[2, 0]) / s]
        elif a[1, 1] > a[2, 2]:
            s = np.sqrt(1.0 + a[1, 1] - a[0, 0] - a[2, 2]) * 2
            q[idx] = [(a[0, 2] - a[2, 0]) / s, (a[0, 1] + a[1, 0]) / s, 0.25 * s, (a[1, 2] + a[2, 1]) / s]
        else:
            s = np.sqrt(1.0 + a[2, 2] - a[0, 0] - a[1, 1]) * 2
            q[idx] = [(a[1, 0] - a[0, 1]) / s, (a[0, 2] + a[2, 0]) / s, (a[1, 2] + a[2, 1]) / s, 0.25 * s]
    return qnorm(q)


def q_to_mat(q):
    w, x, y, z = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    return np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
                     np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
                     np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)], -2)


# ----------------------------------------------------------------------------- inputs
def to_cry(p):
    """G1 motion space (Y up, Z forward, X to the character's LEFT) -> CRYENGINE character space (Z up, Y forward, X right)."""
    p = np.asarray(p, dtype=np.float64)
    return np.stack([-p[..., 0], p[..., 2], p[..., 1]], axis=-1)


class Human:
    def __init__(self, bind_path):
        d = json.load(open(bind_path))
        self.names = [j["n"] for j in d["joints"]]
        self.ix = {n: i for i, n in enumerate(self.names)}
        self.parent = np.array([j["p"] for j in d["joints"]])
        self.aq = np.array([j["aq"] for j in d["joints"]], dtype=np.float64)
        self.at = np.array([j["at"] for j in d["joints"]], dtype=np.float64)
        self.rq = np.array([j["rq"] for j in d["joints"]], dtype=np.float64)
        self.rt = np.array([j["rt"] for j in d["joints"]], dtype=np.float64)
        assert all(p < i for i, p in enumerate(self.parent) if p >= 0), "parents must come before children"

    def leg_length(self):
        g = lambda n: self.at[self.ix[n]]
        return np.linalg.norm(g("Bip01 L Calf") - g("Bip01 L Thigh")) + np.linalg.norm(g("Bip01 L Foot") - g("Bip01 L Calf"))


def frame_from(lateral, up):
    """An orthonormal frame (columns right, forward, up) from a right-pointing and an up-pointing vector (arrays (..., 3))."""
    r = lateral / np.maximum(np.linalg.norm(lateral, axis=-1, keepdims=True), 1e-12)
    u = up - np.sum(up * r, axis=-1, keepdims=True) * r
    u = u / np.maximum(np.linalg.norm(u, axis=-1, keepdims=True), 1e-12)
    f = np.cross(u, r)                                       # right x forward = up  =>  forward = up x right
    return np.stack([r, f, u], axis=-1)


def apply_hinge(human, Rw, ix, g, up, lo, ga, gb, gc):
    """Replace the swing solution of one limb's upper and lower bone (human joints up, lo; G1/SOMA joints ga, gb, gc: start, middle, end) in the world rotations Rw (F, J, 4) by the hinge-constrained pair."""
    F = Rw.shape[0]
    ju, jl = ix[up], ix[lo]
    u = g(gb) - g(ga)
    u = u / np.maximum(np.linalg.norm(u, axis=-1, keepdims=True), 1e-12)
    v = g(gc) - g(gb)
    v = v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)
    n_raw = np.cross(u, v)
    s = np.linalg.norm(n_raw, axis=-1, keepdims=True)                      # sine of the bend: 0 when the limb is straight and the plane is undefined
    a0 = human.at[jl] - human.at[ju]
    a0 = a0 / np.linalg.norm(a0)
    z0 = q_to_mat(human.aq[jl])[:, 2]                                      # the hinge axis at bind (world)
    z0 = z0 - np.dot(z0, a0) * a0
    z0 = z0 / np.linalg.norm(z0)
    B0 = np.stack([a0, np.cross(z0, a0), z0], axis=-1)                     # the upper bone's bind frame: x along the bone, z the hinge axis
    z_local = q_to_mat(human.aq[ju]).T @ z0                                # the hinge axis in the upper joint's own frame
    z_sw = qrot(Rw[:, ju], np.broadcast_to(z_local, (F, 3)))               # where the swing solution had it: used to pick the sign and when the limb is straight
    n_hat = n_raw / np.maximum(s, 1e-9)
    n_hat = np.where(np.sum(n_hat * z_sw, axis=-1, keepdims=True) < 0, -n_hat, n_hat)
    w = np.clip(s / 0.15, 0.0, 1.0)
    n = w * n_hat + (1.0 - w) * z_sw
    n = n - np.sum(n * u, axis=-1, keepdims=True) * u
    n = n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-12)
    T = np.stack([u, np.cross(n, u), n], axis=-1)                          # target frame, columns x y z
    Ru = T @ B0.T[None] @ q_to_mat(human.aq[ju])[None]
    Rw[:, ju] = mat_to_q(Ru)
    d = np.einsum("fji,fj->fi", Ru, v)                                     # the lower bone's direction in the upper joint's frame
    phi = np.arctan2(d[:, 1], d[:, 0])
    qz = np.stack([np.cos(phi / 2), np.zeros(F), np.zeros(F), np.sin(phi / 2)], axis=-1)
    Rw[:, jl] = qmul(Rw[:, ju], qz)


def retarget(clip, human):
    pos = to_cry(clip["positions"])                        # (F, 34, 3) metres; includes the root's travel
    names = [str(n) for n in clip["joint_names"]]
    gix = {n: i for i, n in enumerate(names)}
    neutral = to_cry(clip["neutral_joints"])               # (34, 3) rest pose in the same space
    F, J = len(pos), len(human.names)
    g = lambda n: pos[:, gix[n]]
    g0 = lambda n: neutral[gix[n]]

    # scale: the human's leg is longer than the G1's
    g1_leg = np.linalg.norm(g0("left_knee_skel") - g0("left_hip_pitch_skel")) + np.linalg.norm(g0("left_ankle_roll_skel") - g0("left_knee_skel"))
    scale = human.leg_length() / max(g1_leg, 1e-6)

    # pelvis and torso frames
    def pelvis_frame(get):
        return frame_from(get("right_hip_pitch_skel") - get("left_hip_pitch_skel"), get("waist_pitch_skel") - get("pelvis_skel"))

    def torso_frame(get):
        mid = 0.5 * (get("left_shoulder_pitch_skel") + get("right_shoulder_pitch_skel"))
        return frame_from(get("right_shoulder_pitch_skel") - get("left_shoulder_pitch_skel"), mid - get("waist_pitch_skel"))
    Fp, Ft = pelvis_frame(g), torso_frame(g)
    Fp0 = pelvis_frame(lambda n: g0(n)[None])[0]
    Ft0 = torso_frame(lambda n: g0(n)[None])[0]
    dP = mat_to_q(np.einsum("fij,jk->fik", Fp, Fp0.T))     # world delta from the G1's rest orientation: F(t) F(rest)^-1
    dT = mat_to_q(np.einsum("fij,jk->fik", Ft, Ft0.T))

    Rw = np.zeros((F, J, 4))                               # world rotations of every joint
    driven = np.zeros(J, bool)
    Rw[:, :] = human.aq[None]
    ix = human.ix

    def setw(name, delta):
        j = ix[name]
        Rw[:, j] = qmul(delta, np.broadcast_to(human.aq[j], (F, 4)))
        driven[j] = True
    setw("Bip01 Pelvis", dP)
    for k, n in enumerate(SPINE):
        setw(n, qslerp(dP, dT, (k + 1) / float(len(SPINE))))
    for n in FOLLOW_TORSO:
        setw(n, dT)
    for hname, a, b, child in LIMBS:
        if hname not in ix or child not in ix:
            continue
        j = ix[hname]
        bind_dir = human.at[ix[child]] - human.at[j]
        target = g(b) - g(a)
        sw = qswing(np.broadcast_to(bind_dir, target.shape), target)
        Rw[:, j] = qmul(sw, np.broadcast_to(human.aq[j], (F, 4)))
        driven[j] = True

    # hinge joints: replace the swing solution of the upper and lower bone of each limb by the hinge-constrained pair
    for up, lo, ga, gb, gc in HINGES:
        apply_hinge(human, Rw, ix, g, up, lo, ga, gb, gc)

    # local rotations: driven joints from the world rotations, the rest keep their bind LOCAL rotation under the (moving) parent
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
    # a driven joint's parent may itself be undriven (e.g. the clavicle under the spine): recompute those worlds in order so children see the right parent
    for j in range(J):
        p = human.parent[j]
        if p >= 0 and driven[j]:
            L[:, j] = qnorm(qmul(qconj(Rw[:, p]), Rw[:, j]))

    # root: ground path scaled, pelvis height from the lowest sole point
    root_xy = (g("pelvis_skel")[:, :2] - g("pelvis_skel")[0, :2]) * scale
    pelvis_world = np.zeros((F, 3))
    pelvis_world[:, :2] = root_xy
    pelvis_world[:, 2] = human.at[ix["Bip01 Pelvis"], 2]
    P = np.zeros((F, J, 3))
    for j in range(J):
        p = human.parent[j]
        if p < 0:
            P[:, j] = 0.0
        elif j == ix["Bip01 Pelvis"]:
            P[:, j] = pelvis_world
        else:
            P[:, j] = P[:, p] + qrot(Rw[:, p], np.broadcast_to(human.rt[j], (F, 3)))
    # sole points: heel under the ankle (bind: ankle 0.092 above the ground) and the toe joint (0.034)
    sole = np.minimum(P[:, ix["Bip01 L Foot"], 2] - 0.085, P[:, ix["Bip01 R Foot"], 2] - 0.085)
    sole = np.minimum(sole, np.minimum(P[:, ix["Bip01 L Toe0"], 2] - 0.030, P[:, ix["Bip01 R Toe0"], 2] - 0.030))
    shift = -sole
    pelvis_world[:, 2] += shift
    P[:, :, 2] += shift[:, None]
    return {"local_q": L, "pelvis": pelvis_world, "world_q": Rw, "world_p": P, "fps": float(clip["fps"]), "scale": scale, "names": human.names, "parent": human.parent, "driven": driven}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("bindpose")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    clip = dict(np.load(a.clip, allow_pickle=True))
    human = Human(a.bindpose)
    r = retarget(clip, human)
    out = a.out or os.path.splitext(a.clip)[0] + "_bip01.npz"
    np.savez_compressed(out, local_q=r["local_q"], pelvis=r["pelvis"], world_p=r["world_p"].astype(np.float32), fps=r["fps"], names=np.array(r["names"]), parent=r["parent"], scale=r["scale"])
    drv = int(r["driven"].sum())
    print("retargeted %d frames: %d of %d joints driven, scale %.3f (human leg %.3f m); pelvis height %.3f..%.3f m; travel %.2f m -> %s" % (
        len(r["pelvis"]), drv, len(r["names"]), r["scale"], human.leg_length(), r["pelvis"][:, 2].min(), r["pelvis"][:, 2].max(), float(np.linalg.norm(r["pelvis"][-1, :2] - r["pelvis"][0, :2])), out))


if __name__ == "__main__":
    main()
