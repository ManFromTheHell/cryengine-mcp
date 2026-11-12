#!/usr/bin/env python3
"""Look at recorded MotionBricks motion without any renderer: a strip of stick figures (side view above, front view below) from a motionbricks_record.py file. Project Python (numpy only).

    python animation/motionbricks_preview.py <clip.npz> [--from 60] [--count 12] [--step 3] [--out <png>]

The G1's 34 joints are the global positions in the motion space (Y up, Z forward); each tile is centred on the pelvis so the walk is seen as a gait, not as travel. Left side red, right side blue, spine/pelvis
grey; the toe joint ends the foot. This is the only way to judge generated motion before the engine: the editor does not play animation.
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
import window_capture  # noqa: E402

LEFT, RIGHT, SPINE = (220, 70, 60), (60, 110, 230), (150, 150, 150)


def chains(names):
    ix = {n: i for i, n in enumerate(names)}
    g = lambda n: ix[n]
    pelvis = g("pelvis_skel")
    out = []
    for side, col, s in (("left", LEFT, "left"), ("right", RIGHT, "right")):
        out.append(([pelvis, g("%s_hip_pitch_skel" % s), g("%s_knee_skel" % s), g("%s_ankle_roll_skel" % s), g("%s_toe_base" % s)], col))
        out.append(([g("waist_pitch_skel"), g("%s_shoulder_pitch_skel" % s), g("%s_elbow_skel" % s), g("%s_wrist_roll_skel" % s), g("%s_hand_roll_skel" % s)], col))
    out.append(([pelvis, g("waist_yaw_skel"), g("waist_pitch_skel")], SPINE))
    return out


def human_chains(names):
    """The same chains on the retargeted human (motionbricks_retarget.py output: world_p in CRYENGINE character space, X right, Y forward, Z up)."""
    ix = {n: i for i, n in enumerate(names)}
    out = []
    for side, col in (("L", LEFT), ("R", RIGHT)):
        out.append(([ix["Bip01 Pelvis"], ix["Bip01 %s Thigh" % side], ix["Bip01 %s Calf" % side], ix["Bip01 %s Foot" % side], ix["Bip01 %s Toe0" % side]], col))
        out.append(([ix["Bip01 Neck"], ix["Bip01 %s Clavicle" % side], ix["Bip01 %s UpperArm" % side], ix["Bip01 %s Forearm" % side], ix["Bip01 %s Hand" % side], ix["Bip01 %s Finger2" % side]], col))
    out.append(([ix[n] for n in ("Bip01 Pelvis", "Bip01 Spine", "Bip01 Spine1", "Bip01 Spine2", "Bip01 Spine3", "Bip01 Neck", "Bip01 Head")], SPINE))
    return out


def human_tile(pos, chain_list, view, size=(170, 250), scale=130.0):
    w, h = size
    img = np.full((h, w, 3), 245, np.uint8)
    ground = h - 22
    img[ground:ground + 2, :] = (120, 170, 120)
    root = pos[chain_list[0][0][0]]                           # the first chain starts at the pelvis (Bip01 itself never moves: the travel is in the pelvis)
    for idx, col in chain_list:
        pts = []
        for j in idx:
            rel = pos[j] - np.array([root[0], root[1], 0.0])
            horiz = rel[1] if view == "side" else rel[0]
            pts.append((w / 2 + horiz * scale, ground - rel[2] * scale))
        for a, b in zip(pts, pts[1:]):
            line(img, a, b, col, 3)
    return img


def line(img, a, b, color, thick=2):
    n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1]))) + 1
    for t in np.linspace(0.0, 1.0, n * 2):
        x, y = int(round(a[0] + (b[0] - a[0]) * t)), int(round(a[1] + (b[1] - a[1]) * t))
        if not (0 <= x < img.shape[1] and 0 <= y < img.shape[0]):      # a point outside the tile is skipped (a negative slice end used to paint a bar down the whole tile: arms raised above the head)
            continue
        img[max(0, y - thick // 2):y + thick // 2 + 1, max(0, x - thick // 2):x + thick // 2 + 1] = color


def tile(pos, chain_list, view, size=(170, 250), scale=170.0):
    w, h = size
    img = np.full((h, w, 3), 245, np.uint8)
    root = pos[0]
    ground = h - 22
    img[ground:ground + 2, :] = (120, 170, 120)
    for idx, col in chain_list:
        pts = []
        for j in idx:
            rel = pos[j] - np.array([root[0], 0.0, root[2]])
            horiz = rel[2] if view == "side" else rel[0]
            pts.append((w / 2 + horiz * scale * (1 if view == "side" else -1), ground - rel[1] * scale))
        for a, b in zip(pts, pts[1:]):
            line(img, a, b, col, 3)
    return img


def samples_sheet(paths, out):
    """One picture of what the ENGINE computes from compiled clips: for each bridge.character.samplepose file a side view row and a front view row (one tile per sampled time), stacked in the order given."""
    rows = []
    for p in paths:
        d = json.load(open(p))
        names = d["joints"]
        cl = human_chains(names)
        pos = [np.array([j[4:7] for j in s["pose"]]) for s in d["samples"]]
        rows.append(np.concatenate([human_tile(q, cl, "side") for q in pos], axis=1))
        rows.append(np.concatenate([human_tile(q, cl, "front") for q in pos], axis=1))
        rows.append(np.full((6, rows[-1].shape[1], 3), 90, np.uint8))        # a divider between clips
    sheet = np.concatenate(rows, axis=0)
    window_capture.save_png(out, sheet.shape[1], sheet.shape[0], [sheet[y].tobytes() for y in range(sheet.shape[0])])
    print("%d clips -> %s (%dx%d)" % (len(paths), out, sheet.shape[1], sheet.shape[0]))


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--samples":
        out = sys.argv[sys.argv.index("--out") + 1]
        samples_sheet([a for a in sys.argv[2:sys.argv.index("--out")]], out)
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--from", dest="start", type=int, default=60)
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    d = np.load(a.clip, allow_pickle=True)
    human = "world_p" in d.files
    if human:
        pos = d["world_p"]
        cl = human_chains([str(n) for n in d["names"]])
        tile_fn = human_tile
    else:
        pos = d["positions"]
        cl = chains([str(n) for n in d["joint_names"]])
        tile_fn = tile
    frames = [a.start + i * a.step for i in range(a.count) if a.start + i * a.step < len(pos)]
    top = np.concatenate([tile_fn(pos[f], cl, "side") for f in frames], axis=1)
    bot = np.concatenate([tile_fn(pos[f], cl, "front") for f in frames], axis=1)
    sheet = np.concatenate([top, bot], axis=0)
    out = a.out or os.path.splitext(a.clip)[0] + "_preview.png"
    window_capture.save_png(out, sheet.shape[1], sheet.shape[0], [sheet[y].tobytes() for y in range(sheet.shape[0])])
    print("frames %s of %d at %.0f fps -> %s (%dx%d)" % (frames, len(pos), float(d["fps"]), out, sheet.shape[1], sheet.shape[0]))


if __name__ == "__main__":
    main()
