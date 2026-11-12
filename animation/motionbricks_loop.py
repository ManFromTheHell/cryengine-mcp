#!/usr/bin/env python3
"""Cut a seamless walking cycle out of a retargeted MotionBricks clip (motionbricks_retarget.py output), numpy only.

    python animation/motionbricks_loop.py <clip_bip01.npz> [--min 0.6] [--max 2.4] [--skip 1.0]

The generated motion is a long walk, not a cycle. A cycle is a stretch [i0, i0 + n) whose last pose runs into its first: found as the pair of frames whose BODY POSE (feet, hands, head and pelvis height seen from
the pelvis in its own heading, plus their velocities) is nearest, over a period of 0.6 .. 2.4 s (a gait cycle holds both feet, so the left/right-sensitive features pick whole strides, not half ones), after
skipping the first second (the generator's start-up). The seam is closed by a short crossfade, the travel becomes a straight line at the mean speed along the mean travel direction (the 'locator': that is what
the engine's animation-driven movement follows) and everything else is kept relative to it.
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
import motionbricks_retarget as MR  # noqa: E402


class Retargeted:
    """The npz written by motionbricks_retarget.py plus the bind pose it was made for."""

    def __init__(self, npz_path, bindpose_path):
        d = np.load(npz_path, allow_pickle=True)
        self.local_q = d["local_q"]                       # (F, J, 4) engine parent-relative rotations
        self.pelvis = d["pelvis"]                         # (F, 3) pelvis world position, ground at z = 0
        self.world_p = d["world_p"].astype(np.float64)    # (F, J, 3)
        self.fps = float(d["fps"])
        self.names = [str(n) for n in d["names"]]
        self.human = MR.Human(bindpose_path)
        assert self.names == self.human.names, "the retargeted clip is for another skeleton than the bind pose given"
        self.ix = self.human.ix

    def world_q(self):
        """World rotation of every joint, from the local ones and the bind rotation of the root."""
        F, J = self.local_q.shape[:2]
        w = np.zeros((F, J, 4))
        for j in range(J):
            p = self.human.parent[j]
            w[:, j] = np.broadcast_to(self.human.aq[j], (F, 4)) if p < 0 else MR.qnorm(MR.qmul(w[:, p], self.local_q[:, j]))
        return w


def pelvis_heading(r, wq=None):
    """Unit forward vector (F, 3) of the pelvis: the villager's forward (+Y in its bind pose) carried by the pelvis' world rotation."""
    wq = r.world_q() if wq is None else wq
    j = r.ix["Bip01 Pelvis"]
    local_forward = MR.qrot(MR.qconj(r.human.aq[j]), np.array([0.0, 1.0, 0.0]))
    f = MR.qrot(wq[:, j], np.broadcast_to(local_forward, (len(wq), 3)))
    return f


def yaw_of(v):
    return np.arctan2(-v[..., 0], v[..., 1])              # 0 = +Y (forward), positive turns left (counter-clockwise from above)


def rz(psi):
    c, s = np.cos(psi), np.sin(psi)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


BODY = ["Bip01 L Foot", "Bip01 R Foot", "Bip01 L Toe0", "Bip01 R Toe0", "Bip01 L Hand", "Bip01 R Hand", "Bip01 Head", "Bip01 L Forearm", "Bip01 R Forearm", "Bip01 L Calf", "Bip01 R Calf"]


def pose_features(r):
    """(F, K) body pose features: joint positions relative to the pelvis, turned into the pelvis' heading, plus pelvis height and the velocity of the same."""
    wq = r.world_q()
    yaw = yaw_of(pelvis_heading(r, wq))
    pel = r.world_p[:, r.ix["Bip01 Pelvis"]]
    feats = []
    for n in BODY:
        rel = r.world_p[:, r.ix[n]] - pel
        c, s = np.cos(-yaw), np.sin(-yaw)
        x = c * rel[:, 0] - s * rel[:, 1]
        y = s * rel[:, 0] + c * rel[:, 1]
        feats.append(np.stack([x, y, rel[:, 2]], -1))
    pos = np.concatenate(feats + [pel[:, 2:3]], axis=-1)
    vel = np.gradient(pos, axis=0) * r.fps * 0.15       # a velocity term, weighted so a 1 m/s change counts like 15 cm of position
    return np.concatenate([pos, vel], axis=-1)


def find_cycle(r, min_s=0.6, max_s=2.4, skip_s=1.0, tail_s=0.5):
    """Best (i0, n) and its feature distance; also returns the distance of the best candidate of every period for reporting."""
    F = len(r.local_q)
    f = pose_features(r)
    lo, hi = int(round(min_s * r.fps)), int(round(max_s * r.fps))
    start = int(round(skip_s * r.fps))
    end = F - int(round(tail_s * r.fps))
    best = None
    per_period = {}
    for n in range(lo, hi + 1):
        for i0 in range(start, end - n):
            # compare a short window so the velocity of the seam matches too
            d = float(np.sqrt(np.mean((f[i0] - f[i0 + n]) ** 2)))
            if n not in per_period or d < per_period[n][0]:
                per_period[n] = (d, i0)
    # the shortest period within 25 % (+ a floor) of the global minimum: shorter cycles loop more often but are as good
    gmin = min(v[0] for v in per_period.values())
    ok = [n for n, v in per_period.items() if v[0] <= gmin * 1.25 + 0.004]
    n = min(ok)
    return per_period[n][1], n, per_period[n][0], gmin, per_period


def build_loop(r, i0, n, close_frames=6):
    """The cycle as clip data in the engine's terms. Returns a dict:
        local_q (n+1, J, 4)   parent-relative rotations, frame n == frame 0 (the pelvis' is turned so the travel runs along +Y)
        pelvis_rel (n+1, 3)   pelvis position relative to the locator's ground position (z = height)
        loc (n+1, 3)          the locator's path: a straight line along +Y at the mean speed, z = 0
        speed                 metres per second
        drift_deg, side_m     facing of the pelvis relative to the travel, mean lateral offset of the travel from the line (both reported)"""
    F, J = r.local_q.shape[:2]
    pel = r.world_p[:, r.ix["Bip01 Pelvis"]]
    # travel direction: the net displacement of the cycle; turn the whole motion about the vertical so it runs along +Y
    disp = pel[i0 + n, :2] - pel[i0, :2]
    speed_pf = float(np.linalg.norm(disp)) / n
    psi = np.arctan2(-disp[0], disp[1])                 # yaw of the displacement
    R = rz(-psi)                                        # takes the displacement to +Y
    q_turn = np.array([np.cos(-psi / 2), 0.0, 0.0, np.sin(-psi / 2)])
    # frames beyond the cycle (needed for the crossfade) must exist
    K = close_frames
    if i0 + n + K >= F:
        raise ValueError("not enough frames after the cycle for a %d-frame crossfade" % K)
    pelvis_j = r.ix["Bip01 Pelvis"]
    lq = r.local_q.copy()
    lq[:, pelvis_j] = MR.qnorm(MR.qmul(np.broadcast_to(q_turn, (F, 4)), lq[:, pelvis_j]))
    # pelvis path in the turned frame, relative to the cycle's first frame
    rel_xy = (pel[:, :2] - pel[i0, :2]) @ R[:2, :2].T
    height = pel[:, 2]
    t = np.arange(F) - i0
    v = speed_pf
    pr = np.zeros((F, 3))                               # pelvis relative to the locator (the locator is at (0, v*t))
    pr[:, 0] = rel_xy[:, 0]
    pr[:, 1] = rel_xy[:, 1] - v * t
    pr[:, 2] = height
    # closing the seam: frame n must equal frame 0; the last K frames blend from the cycle into the frames that FOLLOW it, which resemble the start
    out_q = np.zeros((n + 1, J, 4))
    out_pr = np.zeros((n + 1, 3))
    for k in range(n + 1):
        out_q[k] = lq[i0 + k]
        out_pr[k] = pr[i0 + k]
    for k in range(n - K + 1, n + 1):
        w = (k - (n - K)) / float(K + 1)
        a = lq[i0 + k]                                  # the cycle's own frame
        b = lq[i0 + k - n]                              # the frame one cycle earlier: it is what the cycle's frame should be at its own phase
        out_q[k] = MR.qslerp(a, b, w)
        out_pr[k] = (1 - w) * pr[i0 + k] + w * pr[i0 + k - n]
    out_q[n] = out_q[0]                                 # an exact duplicate, so the wrap is a continuation and not a pose jump
    out_pr[n] = out_pr[0]
    # pelvis relative path was measured against the locator at v*t: at frame n the relative y equals that of frame 0 by construction of v, and x/z are blended above
    loc = np.zeros((n + 1, 3))
    loc[:, 1] = v * np.arange(n + 1)
    # reporting
    heading = yaw_of(pelvis_heading(r))
    dev = np.degrees(np.arctan2(np.sin(heading[i0:i0 + n] - psi), np.cos(heading[i0:i0 + n] - psi)))
    return {"local_q": out_q, "pelvis_rel": out_pr, "loc": loc, "speed": speed_pf * r.fps, "facing_vs_travel_deg": float(np.mean(dev)), "side_m": float(np.mean(rel_xy[i0:i0 + n, 0])),
            "fps": r.fps, "psi_deg": float(np.degrees(psi)), "n": n, "i0": i0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("bindpose")
    ap.add_argument("--min", type=float, default=0.6)
    ap.add_argument("--max", type=float, default=2.4)
    ap.add_argument("--skip", type=float, default=1.0)
    a = ap.parse_args()
    r = Retargeted(a.clip, a.bindpose)
    i0, n, d, gmin, per = find_cycle(r, a.min, a.max, a.skip)
    loop = build_loop(r, i0, n)
    print("%s: cycle starts at frame %d, %d frames = %.2f s (feature distance %.4f, best of all periods %.4f); speed %.2f m/s, facing vs travel %.1f deg, sideways offset %.3f m" % (
        os.path.basename(a.clip), i0, n, n / r.fps, d, gmin, loop["speed"], loop["facing_vs_travel_deg"], loop["side_m"]))


if __name__ == "__main__":
    main()
