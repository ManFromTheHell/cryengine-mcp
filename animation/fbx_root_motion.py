#!/usr/bin/env python3
"""Root motion of a locomotion FBX, read offline (no engine): turn and travel of the motion node
(Crysis rig: Locator_Locomotion) in WORLD space, the way the RC measures it.

    fbx_root_motion.py <clip.FBX> [...] [--node Locator_Locomotion]

The locator's own Lcl curves say nothing: it is a child of Bip01, which carries a -90 deg
PreRotation (Z-up Max -> Y-up FBX) and most of the motion. So every frame composes
parent * T * PreRotation * R(Euler XYZ) * PostRotation^-1 up the chain and reads the result.

turn_deg   summed per-frame yaw about the world up axis (RC: ReplaceRootByLocator, m_fAssetTurn =
           start->middle + middle->end angle of the locator's forward axis; m_fTurnSpeed =
           that / duration)
speed      ground-plane path length / duration; chord = straight start->end distance / duration

Used to find the real turn rate of the walk-turn clips a blend space extrapolates from: the parameters the engine extracts could not be read at runtime.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import bisect
import math
import sys

import fbx_binary

FBX_SECOND = 46186158000.0
FPS = 30.0


def sval(prop):
    return prop[1].split(b"\x00")[0].decode("latin-1") if prop[0] == "S" else prop[1]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def translate(t):
    return [[1, 0, 0, t[0]], [0, 1, 0, t[1]], [0, 0, 1, t[2]], [0, 0, 0, 1]]


def rot_axis(axis, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    if axis == 0:
        return [[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]]
    if axis == 1:
        return [[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]]
    return [[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def euler_xyz(e):
    # FBX eEulerXYZ: X applied first, so the matrix is Rz * Ry * Rx
    return mat_mul(rot_axis(2, e[2]), mat_mul(rot_axis(1, e[1]), rot_axis(0, e[0])))


def transpose3(m):
    return [[m[j][i] for j in range(3)] + [0] for i in range(3)] + [[0, 0, 0, 1]]


class Channel:
    """One animated property: three curves (or a constant default per axis)."""

    def __init__(self, defaults):
        self.curves = [None, None, None]
        self.defaults = list(defaults)

    def at(self, sec):
        out = []
        for i in range(3):
            c = self.curves[i]
            if c is None:
                out.append(self.defaults[i])
                continue
            t, v = c
            if sec <= t[0]:
                out.append(v[0])
            elif sec >= t[-1]:
                out.append(v[-1])
            else:
                j = bisect.bisect_right(t, sec)
                a = (sec - t[j - 1]) / (t[j] - t[j - 1])
                out.append(v[j - 1] + (v[j] - v[j - 1]) * a)
        return out


class Scene:
    def __init__(self, path):
        _, nodes, _ = fbx_binary.read(path)
        objects = next(n for n in nodes if n.name == "Objects")
        conns = next(n for n in nodes if n.name == "Connections")
        self.up = 1
        gs = next((n for n in nodes if n.name == "GlobalSettings"), None)
        p70 = gs.find("Properties70") if gs else None
        for P in (p70.children if p70 else []):
            if sval(P.props[0]) == "UpAxis":
                self.up = P.props[4][1]
        self.models = {}
        for o in objects.children:
            if o.name != "Model":
                continue
            props = {}
            p = o.find("Properties70")
            for P in (p.children if p else []):
                props[sval(P.props[0])] = [x[1] for x in P.props[4:]]
            self.models[o.props[0][1]] = {"name": sval(o.props[1]), "props": props, "anim": {}}
        curves = {}
        for o in objects.children:
            if o.name == "AnimationCurve":
                t = [x / FBX_SECOND for x in o.find("KeyTime").props[0][1]]
                curves[o.props[0][1]] = (t, list(o.find("KeyValueFloat").props[0][1]))
        cnode_defaults = {}
        for o in objects.children:
            if o.name == "AnimationCurveNode":
                d = [0.0, 0.0, 0.0]
                p = o.find("Properties70")
                for P in (p.children if p else []):
                    k = sval(P.props[0])
                    if k in ("d|X", "d|Y", "d|Z"):
                        d["XYZ".index(k[-1])] = P.props[4][1]
                cnode_defaults[o.props[0][1]] = d
        self.parent = {}
        cnode_of = {}          # curve node -> (model, property)
        curve_of = {}          # curve -> (curve node, axis)
        for c in conns.children:
            kind = sval(c.props[0])
            ch, pa = c.props[1][1], c.props[2][1]
            if kind == "OO" and ch in self.models:
                self.parent[ch] = pa
            elif kind == "OP" and pa in self.models and ch in cnode_defaults:
                cnode_of[ch] = (pa, sval(c.props[3]))
            elif kind == "OP" and ch in curves:
                curve_of[ch] = (pa, sval(c.props[3]))
        for cn, (mid, prop) in cnode_of.items():
            if prop in ("Lcl Translation", "Lcl Rotation"):
                self.models[mid]["anim"][prop] = Channel(cnode_defaults[cn])
        self.t0, self.t1 = None, None
        for cid, (cn, axis) in curve_of.items():
            if cn in cnode_of:
                mid, prop = cnode_of[cn]
                ch = self.models[mid]["anim"].get(prop)
                if ch is not None and axis[-1] in "XYZ":
                    ch.curves["XYZ".index(axis[-1])] = curves[cid]
                    t = curves[cid][0]
                    if len(t) > 1:
                        self.t0 = t[0] if self.t0 is None else min(self.t0, t[0])
                        self.t1 = t[-1] if self.t1 is None else max(self.t1, t[-1])

    def find(self, name):
        return next((mid for mid, m in self.models.items() if m["name"] == name), None)

    def local(self, mid, sec):
        m = self.models[mid]
        pr = m["props"]
        tr = m["anim"].get("Lcl Translation")
        ro = m["anim"].get("Lcl Rotation")
        t = tr.at(sec) if tr else pr.get("Lcl Translation", [0, 0, 0])
        r = ro.at(sec) if ro else pr.get("Lcl Rotation", [0, 0, 0])
        mat = translate(t)
        if "PreRotation" in pr:
            mat = mat_mul(mat, euler_xyz(pr["PreRotation"]))
        mat = mat_mul(mat, euler_xyz(r))
        if "PostRotation" in pr:
            mat = mat_mul(mat, transpose3(euler_xyz(pr["PostRotation"])))
        return mat

    def world(self, mid, sec):
        mat = self.local(mid, sec)
        p = self.parent.get(mid)
        while p in self.models:
            mat = mat_mul(self.local(p, sec), mat)
            p = self.parent.get(p)
        return mat


def root_motion(path, node_name="Locator_Locomotion"):
    sc = Scene(path)
    mid = sc.find(node_name)
    if mid is None:
        return {"error": "no model named %s" % node_name}
    if sc.t0 is None:
        return {"error": "no animation"}
    up = sc.up
    g = [a for a in range(3) if a != up]          # ground-plane axes
    n = max(2, int(round((sc.t1 - sc.t0) * FPS)) + 1)
    frames = [sc.world(mid, sc.t0 + (sc.t1 - sc.t0) * i / (n - 1)) for i in range(n)]

    # heading of the local axis lying flattest in the ground plane at the first frame, kept for the
    # whole clip: robust to the rig's axis convention since only the change in heading is used.
    # (An upright locator has TWO flat axes; re-picking per frame jumps 90 deg between them.)
    best = max(range(3), key=lambda k: math.hypot(frames[0][g[0]][k], frames[0][g[1]][k]))

    def yaw(m):
        return math.atan2(m[g[1]][best], m[g[0]][best])

    turn = 0.0
    for a, b in zip(frames, frames[1:]):
        d = yaw(b) - yaw(a)
        turn += (d + math.pi) % (2 * math.pi) - math.pi
    path_len = sum(math.hypot(b[g[0]][3] - a[g[0]][3], b[g[1]][3] - a[g[1]][3]) for a, b in zip(frames, frames[1:]))
    chord = math.hypot(frames[-1][g[0]][3] - frames[0][g[0]][3], frames[-1][g[1]][3] - frames[0][g[1]][3])
    dur = sc.t1 - sc.t0
    # FBX units are centimetres; heading sign follows the right-handed rotation about the up axis
    # of the file (Y-up: positive = counter-clockwise seen from above = a LEFT turn)
    sign = -1.0 if up == 1 else 1.0   # atan2(z, x) about +Y runs clockwise seen from above
    return {
        "duration_s": dur,
        "turn_deg": math.degrees(turn) * sign,
        "turn_rad_s": turn * sign / dur,
        "speed_m_s": path_len / 100.0 / dur,
        "chord_m_s": chord / 100.0 / dur,
    }


if __name__ == "__main__":
    node = sys.argv[sys.argv.index("--node") + 1] if "--node" in sys.argv else "Locator_Locomotion"
    for p in [a for a in sys.argv[1:] if a.lower().endswith(".fbx")]:
        name = p.replace("\\", "/").rsplit("/", 1)[-1]
        try:
            r = root_motion(p, node)
        except Exception as e:   # one bad file must not hide the rest
            r = {"error": "%s: %s" % (type(e).__name__, e)}
        print("%-48s %s" % (name, "  ".join(("%s %.3f" % kv) if isinstance(kv[1], float) else "%s %s" % kv
                                            for kv in sorted(r.items()))), flush=True)
