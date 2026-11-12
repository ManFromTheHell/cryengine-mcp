#!/usr/bin/env python3
"""One-shot and in-place-loop clips out of a retargeted motion (kimodo_retarget.retarget / motionbricks_retarget.retarget result dicts). Project Python, numpy only.

The clip dict these functions return is the one motionbricks_clip.build_curves / loop_world / fk_check / expected_samples already take (the walking cycles use it): local_q (F, J, 4) parent-relative
rotations, pelvis_rel (F, 3) the pelvis relative to the locator, loc (F, 3) the locator's ground path (z = 0), loc_yaw (F,) the locator's heading (0 = +Y, positive turns left), speed, fps.

  slice_pad(r, f0, f1)            frames f0..f1 inclusive of a retarget result, with the first/last frame held where the window runs past the clip
  mirror_soma(posed, soma)        a Kimodo motion mirrored left <-> right (positions: swap Left*/Right* joints and negate X)
  pelvis_yaw(r, human)            the pelvis' heading per frame (radians, unwrapped, 0 = +Y)
  make_clip(r, human, root, ...)  root='fixed': the locator stays at the start (the body moves relative to it); 'compress': as fixed with the pelvis' travel scaled down to max_excursion; 'path': it follows the pelvis' ground path, low-passed (the body keeps near it: a
                                  fall, a sit); yaw='fixed': heading stays 0; 'follow': the locator turns with the pelvis, low-passed (a turn on the spot)
  make_inplace_loop(r, human, i0, n)   a seamless in-place loop of n frames (the last frame equals the first) closed by a crossfade with the frames before it
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import motionbricks_retarget as MR  # noqa: E402


def slice_pad(r, f0, f1):
    F = len(r["local_q"])
    idx = np.clip(np.arange(f0, f1 + 1), 0, F - 1)
    out = dict(r)
    for k in ("local_q", "pelvis", "world_p", "world_q"):
        if k in r:
            out[k] = np.asarray(r[k])[idx]
    return out


def mirror_soma(posed, soma):
    """posed (F, 77, 3) in Kimodo's axes (Y up, Z forward, X = the character's left): the mirror image about the character's own plane."""
    partner = np.arange(len(soma.names))
    for i, n in enumerate(soma.names):
        if n.startswith("Left"):
            partner[i] = soma.ix["Right" + n[4:]]
        elif n.startswith("Right"):
            partner[i] = soma.ix["Left" + n[5:]]
    out = posed[:, partner].copy()
    out[..., 0] *= -1.0
    return out


def pelvis_yaw(r, human):
    j = human.ix["Bip01 Pelvis"]
    local_forward = MR.qrot(MR.qconj(human.aq[j]), np.array([0.0, 1.0, 0.0]))
    wq = r["world_q"][:, j]
    f = MR.qrot(wq, np.broadcast_to(local_forward, (len(wq), 3)))
    return np.unwrap(np.arctan2(-f[:, 0], f[:, 1]))


def lowpass(x, w):
    """Moving average over +-w frames, the clip's edges held (axis 0)."""
    if w <= 0:
        return x.copy()
    pad = np.concatenate([np.repeat(x[:1], w, axis=0), x, np.repeat(x[-1:], w, axis=0)], axis=0)
    k = np.ones(2 * w + 1) / (2 * w + 1)
    return np.stack([np.convolve(pad[:, c], k, mode="valid") for c in range(x.shape[1])], axis=1) if x.ndim == 2 else np.convolve(pad, k, mode="valid")


def make_clip(r, human, root="fixed", yaw="fixed", smooth=8, max_excursion=0.3):
    """root='compress': the locator stays put and the pelvis' ground path is scaled down (the whole path by one factor, so its shape is kept) until it never strays more than max_excursion metres from where the
    clip began: a heavy hit that staggers a body 1.4 m is played as a stagger on the spot (the feet slide a little instead of the body leaving its collider and snapping back at the clip's end)."""
    F = len(r["local_q"])
    pel = np.asarray(r["pelvis"], dtype=np.float64)
    if root == "compress":
        pel = pel.copy()
        d = pel[:, :2] - pel[0, :2]
        far = float(np.max(np.linalg.norm(d, axis=1)))
        pel[:, :2] = pel[0, :2] + d * min(1.0, max_excursion / max(far, 1e-9))
        root = "fixed"
    lq = np.array(r["local_q"], dtype=np.float64)
    loc = np.zeros((F, 3))
    loc_yaw = np.zeros(F)
    if root == "path":
        loc[:, :2] = lowpass(pel[:, :2] - pel[0, :2], smooth)
        loc[:, :2] -= loc[0, :2]          # the locator STARTS at the origin: the engine subtracts the locator's first-frame position from the whole clip, so any offset there would move every joint
    pr = pel - loc
    if yaw == "follow":
        psi = pelvis_yaw(r, human)
        loc_yaw = lowpass(psi - psi[0], smooth)
        loc_yaw = loc_yaw - loc_yaw[0]       # the heading STARTS at 0 as the position does: the engine takes the locator's first frame out of the clip, so a few degrees there turned every joint (a few degrees there moved joints by 1-3 cm on the turn clips)
    mean_speed = float(np.linalg.norm(loc[-1, :2] - loc[0, :2])) / max(F - 1, 1) * r["fps"]
    return {"local_q": lq, "pelvis_rel": pr, "loc": loc, "loc_yaw": loc_yaw, "speed": mean_speed, "fps": r["fps"], "n": F - 1, "root": root, "yaw": yaw}


def make_inplace_loop(r, human, i0, n, K=8):
    """frames i0 .. i0+n of the retargeted motion as a loop: out[k] = motion[i0+k], and over the last K frames the motion crossfades into the frames BEFORE i0 (which lead into its first), so the wrap is a
    continuation; the last frame is the first. Needs i0 >= K (the earlier frames are held at the clip's start if not)."""
    F = len(r["local_q"])
    idx = np.clip(np.arange(i0 - K, i0 + n + 1), 0, F - 1)
    lq = np.array(r["local_q"], dtype=np.float64)[idx]
    pel = np.asarray(r["pelvis"], dtype=np.float64)[idx]
    base = pel[K, :2].copy()
    pr = pel.copy()
    pr[:, :2] -= base
    out_q = lq[K:K + n + 1].copy()
    out_p = pr[K:K + n + 1].copy()
    for k in range(n - K + 1, n + 1):
        w = (k - (n - K)) / float(K + 1)
        a = lq[K + k]
        b = lq[K + k - n]
        out_q[k] = MR.qslerp(a, b, w)
        out_p[k] = (1 - w) * pr[K + k] + w * pr[K + k - n]
    out_q[n] = out_q[0]
    out_p[n] = out_p[0]
    return {"local_q": out_q, "pelvis_rel": out_p, "loc": np.zeros((n + 1, 3)), "loc_yaw": np.zeros(n + 1), "speed": 0.0, "fps": r["fps"], "n": n, "root": "fixed", "yaw": "fixed"}


def best_inplace_loop(r, human, min_s=1.0, max_s=4.0, skip_s=0.3, tail_s=0.3):
    """The pair of frames whose whole-body pose (all body joints, relative to the pelvis, and their velocity) is nearest, for a stationary clip that should loop. -> (i0, n, distance)."""
    P = np.asarray(r["world_p"], dtype=np.float64)
    pel = P[:, human.ix["Bip01 Pelvis"]]
    joints = [human.ix[n] for n in ("Bip01 Head", "Bip01 L Hand", "Bip01 R Hand", "Bip01 L Foot", "Bip01 R Foot", "Bip01 L Forearm", "Bip01 R Forearm", "Bip01 L Calf", "Bip01 R Calf", "Bip01 Spine3")]
    f = (P[:, joints] - pel[:, None, :]).reshape(len(P), -1)
    f = np.concatenate([f, np.gradient(f, axis=0) * r["fps"] * 0.15], axis=1)
    fps = r["fps"]
    lo, hi = int(round(min_s * fps)), int(round(max_s * fps))
    start, end = int(round(skip_s * fps)), len(P) - int(round(tail_s * fps))
    best = None
    for n in range(lo, min(hi, end - start - 1) + 1):
        d = np.sqrt(np.mean((f[start:end - n] - f[start + n:end]) ** 2, axis=1))
        i = int(np.argmin(d))
        score = float(d[i]) * (1.0 + 0.1 * (n - lo) / max(hi - lo, 1))      # a slight preference for the shorter loop
        if best is None or score < best[0]:
            best = (score, start + i, n, float(d[i]))
    return best[1], best[2], best[3]
