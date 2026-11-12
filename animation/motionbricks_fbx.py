#!/usr/bin/env python3
"""Write animation curves into a copy of a Crytek animation FBX (Bip01 rig), so the Resource Compiler turns them into a .caf of the villagers' skeleton. Project Python, numpy only.

    from motionbricks_fbx import Template, euler_xyz_deg
    t = Template(<a Crytek locomotion FBX>)
    t.write(out_fbx, {model name: (T (F,3) cm, R (F,3) degrees)}, fps=30)

How the engine reads such a file was MEASURED on the Crytek run clip (bridge.character.samplepose against the same clip compiled by the pak and by rc.exe):
  * engine world pose = M . (FBX world pose), M = +90 degrees about X (FBX Y up, forward -Z  ->  engine Z up, forward +Y), metres, minus the locator's position at the clip's first frame;
    joint frames coincide, so a joint's FBX local rotation (PreRotation . Lcl) EQUALS the engine's parent-relative rotation and its Lcl Translation is 100 x the engine's relative translation (cm).
    Held to 0.1 degree and 1 cm over the body joints (fingers, twist and IK helper joints are the engine's own business).
  * the FBX root 'Bip01' is the biped's centre of mass (Max): it carries the pelvis position and heading and 'Bip01 Pelvis' is a constant child of it. The RC re-roots the clip on 'Locator_Locomotion'
    (the motion node): the engine's Bip01 is the locator's path and the engine's Pelvis is relative to it.
  * the take's LocalStart is honoured: this FBX starts at 1/24 s, so the .caf began one 30 fps frame late. write() puts LocalStart/ReferenceStart/TimeSpanStart at 0.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import math
import os
import sys

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import fbx_binary  # noqa: E402
import fbx_root_motion as RM  # noqa: E402

FBX_SECOND = 46186158000
M = np.array([[1.0, 0, 0], [0, 0, -1.0], [0, 1.0, 0]])      # FBX (Y up, forward -Z) -> engine (Z up, forward +Y)


def rot_x(a):
    c, s = np.cos(a), np.sin(a)
    o, z = np.ones_like(a), np.zeros_like(a)
    return np.stack([np.stack([o, z, z], -1), np.stack([z, c, -s], -1), np.stack([z, s, c], -1)], -2)


def rot_y(a):
    c, s = np.cos(a), np.sin(a)
    o, z = np.ones_like(a), np.zeros_like(a)
    return np.stack([np.stack([c, z, s], -1), np.stack([z, o, z], -1), np.stack([-s, z, c], -1)], -2)


def rot_z(a):
    c, s = np.cos(a), np.sin(a)
    o, z = np.ones_like(a), np.zeros_like(a)
    return np.stack([np.stack([c, -s, z], -1), np.stack([s, c, z], -1), np.stack([z, z, o], -1)], -2)


def matrix_from_euler_xyz_deg(e):
    """FBX eEulerXYZ: X first, so Rz . Ry . Rx. e: (..., 3) degrees -> (..., 3, 3)."""
    r = np.radians(np.asarray(e, dtype=np.float64))
    return rot_z(r[..., 2]) @ rot_y(r[..., 1]) @ rot_x(r[..., 0])


def euler_xyz_deg(m):
    """The inverse of matrix_from_euler_xyz_deg for a sequence of rotation matrices (F, 3, 3) -> (F, 3) degrees, continuous in time: each frame takes the one of the two equivalent Euler triples that is
    nearest the previous frame, near the gimbal singularity (Y = +-90) the third angle is pinned to 0, and the result is unwrapped so no key jumps by 360."""
    m = np.asarray(m, dtype=np.float64)
    u, _, vt = np.linalg.svd(m)                            # the input's own error (a quaternion stored to 9 digits is a few 1e-7 off a rotation) is amplified near the gimbal singularity: use the nearest true rotation
    m = u @ vt
    F = len(m)
    out = np.zeros((F, 3))
    for f in range(F):
        a_m = m[f]
        sb = float(np.clip(-a_m[2, 0], -1.0, 1.0))
        b = math.asin(sb)
        cb = math.cos(b)
        if cb > 1e-5:
            a = math.atan2(a_m[2, 1], a_m[2, 2])
            c = math.atan2(a_m[1, 0], a_m[0, 0])
            alt = (a + math.pi, math.pi - b, c + math.pi)
        else:                                                  # gimbal lock: only a -+ c is defined
            a = math.atan2(sb * a_m[0, 1], a_m[1, 1])
            c = 0.0
            alt = None
        cand = [(a, b, c)] + ([alt] if alt else [])
        if f == 0:
            pick = cand[0]
        else:
            prev = out[f - 1]
            def dist(t):
                d = np.degrees(np.array(t)) - prev
                d = (d + 180.0) % 360.0 - 180.0
                return float(np.sum(d * d))
            pick = min(cand, key=dist)
        out[f] = np.degrees(np.array(pick))
    return np.degrees(np.unwrap(np.radians(out), axis=0))


def snap_axes(r):
    """The nearest rotation whose columns are +-axes (a clean 90-degree-multiple frame) to rotation r (3,3)."""
    out = np.zeros((3, 3))
    for j in range(3):
        k = int(np.argmax(np.abs(r[:, j])))
        out[k, j] = 1.0 if r[k, j] > 0 else -1.0
    if np.linalg.det(out) < 0:
        raise ValueError("cannot snap %s to a proper rotation" % r)
    return out


def _s(prop):
    return prop[1].split(b"\x00")[0].decode("latin-1") if prop[0] == "S" else prop[1]


class Template:
    """A Crytek animation FBX: the rig, its constants and the curve objects to overwrite."""

    def __init__(self, path):
        self.path = path
        self.scene = RM.Scene(path)
        self.version, self.nodes, self.footer = fbx_binary.read(path)
        top = {n.name: n for n in self.nodes}
        self.top = top
        objs = top["Objects"].children
        conns = top["Connections"].children
        self.curve_node = {}                               # model id -> {property: curve node id}
        self.curve_of = {}                                 # curve node id -> {'d|X': curve Node, ...}
        curve_by_id = {o.props[0][1]: o for o in objs if o.name == "AnimationCurve"}
        cnode_by_id = {o.props[0][1]: o for o in objs if o.name == "AnimationCurveNode"}
        for c in conns:
            if c.name != "C" or _s(c.props[0]) != "OP":
                continue
            ch, pa, prop = c.props[1][1], c.props[2][1], _s(c.props[3])
            if ch in cnode_by_id and pa in self.scene.models:
                self.curve_node.setdefault(pa, {})[prop] = ch
            elif ch in curve_by_id and pa in cnode_by_id:
                self.curve_of.setdefault(pa, {})[prop] = curve_by_id[ch]
        self.name_id = {m["name"]: mid for mid, m in self.scene.models.items()}
        self.stack = next((o for o in objs if o.name == "AnimationStack"), None)

    def animated_models(self):
        """Names of the models that have a translation and a rotation curve to overwrite."""
        out = []
        for mid, props in self.curve_node.items():
            if "Lcl Translation" in props and "Lcl Rotation" in props:
                out.append(self.scene.models[mid]["name"])
        return out

    def pre_post(self, name):
        pr = self.scene.models[self.name_id[name]]["props"]
        pre = matrix_from_euler_xyz_deg(pr["PreRotation"]) if "PreRotation" in pr else np.eye(3)
        post = matrix_from_euler_xyz_deg(pr["PostRotation"]) if "PostRotation" in pr else np.eye(3)
        return pre, post

    def local_at(self, name, sec):
        """Template local (T cm, R degrees) at a time."""
        mid = self.name_id[name]
        m = self.scene.models[mid]
        tr, ro = m["anim"].get("Lcl Translation"), m["anim"].get("Lcl Rotation")
        t = tr.at(sec) if tr else m["props"].get("Lcl Translation", [0, 0, 0])
        r = ro.at(sec) if ro else m["props"].get("Lcl Rotation", [0, 0, 0])
        return np.array(t, dtype=np.float64), np.array(r, dtype=np.float64)

    def lcl_from_total(self, name, total):
        """Lcl Rotation matrices (F,3,3) whose PreRotation . R . PostRotation^-1 equals the wanted total local rotation (F,3,3)."""
        pre, post = self.pre_post(name)
        return np.einsum("ij,fjk,kl->fil", pre.T, total, post)

    def _set_curve(self, curve, ticks, values):
        n = len(ticks)
        for ch in curve.children:
            if ch.name == "KeyTime":
                ch.props = [("l", [int(t) for t in ticks])]
            elif ch.name == "KeyValueFloat":
                ch.props = [("f", [float(v) for v in values])]
            elif ch.name == "KeyAttrRefCount":
                ch.props = [("i", [n])]              # one attribute segment (KeyAttrFlags / KeyAttrDataFloat keep the template's single entry) covering every key
        if not any(ch.name == "KeyAttrRefCount" for ch in curve.children):
            raise ValueError("curve has no KeyAttrRefCount")
        flags = next(ch for ch in curve.children if ch.name == "KeyAttrFlags")
        if len(flags.props[0][1]) != 1:
            raise ValueError("curve has %d attribute segments: only the single-segment layout is rewritten" % len(flags.props[0][1]))

    def write(self, out_path, curves, fps=30.0):
        """curves: {model name: (T (F,3) cm, R (F,3) degrees)}, all with the same F. Models of the template that have curves but are not in `curves` keep their own (their keys beyond the clip are ignored by the take span)."""
        F = None
        for name, (t, r) in curves.items():
            if name not in self.name_id or self.name_id[name] not in self.curve_node:
                raise KeyError("model %r has no animation curves in the template" % name)
            F = len(t) if F is None else F
            if len(t) != F or len(r) != F:
                raise ValueError("%s: %d translation keys, %d rotation keys, expected %d" % (name, len(t), len(r), F))
        ticks = [int(round(i * FBX_SECOND / fps)) for i in range(F)]
        for name, (t, r) in curves.items():
            mid = self.name_id[name]
            for prop, arr in (("Lcl Translation", t), ("Lcl Rotation", r)):
                cn = self.curve_node[mid][prop]
                for k, axis in enumerate(("d|X", "d|Y", "d|Z")):
                    self._set_curve(self.curve_of[cn][axis], ticks, np.asarray(arr)[:, k])
        # every template curve that is not replaced is cut to the clip's key count too (a longer one would carry the template's own motion past the end of ours)
        done = {self.name_id[n] for n in curves}
        for mid, props in self.curve_node.items():
            if mid in done:
                continue
            for prop, cn in props.items():
                for axis, curve in self.curve_of.get(cn, {}).items():
                    kt = next(c for c in curve.children if c.name == "KeyTime").props[0][1]
                    kv = next(c for c in curve.children if c.name == "KeyValueFloat").props[0][1]
                    hold = kv[min(1, len(kv) - 1)]
                    self._set_curve(curve, ticks, [hold] * F)
        stop = ticks[-1]
        if self.stack is not None:
            for p in self.stack.find("Properties70").children:
                if _s(p.props[0]) in ("LocalStart", "ReferenceStart"):
                    p.props[4] = ("L", 0)
                elif _s(p.props[0]) in ("LocalStop", "ReferenceStop"):
                    p.props[4] = ("L", stop)
        takes = self.top.get("Takes")
        if takes is not None:
            for tk in takes.children:
                for c in tk.children:
                    if c.name in ("LocalTime", "ReferenceTime"):
                        c.props = [("L", 0), ("L", stop)]
        gs = self.top.get("GlobalSettings")
        if gs is not None:
            for p in gs.find("Properties70").children:
                if _s(p.props[0]) == "TimeSpanStart":
                    p.props[4] = ("L", 0)
                elif _s(p.props[0]) == "TimeSpanStop":
                    p.props[4] = ("L", stop)
        fbx_binary.write(out_path, self.version, self.nodes, self.footer)
        return F


def main():
    ap = __import__("argparse").ArgumentParser(description="Self-test of the writer: re-write the template's own motion (frames start..start+count) through Template.write")
    ap.add_argument("template")
    ap.add_argument("out")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--count", type=int, default=24)
    a = ap.parse_args()
    t = Template(a.template)
    curves = {}
    for name in t.animated_models():
        ts, rs = [], []
        for f in range(a.start, a.start + a.count):
            tt, rr = t.local_at(name, f / 30.0)
            ts.append(tt)
            rs.append(rr)
        curves[name] = (np.array(ts), np.array(rs))
    n = t.write(a.out, curves)
    print("wrote %d keys for %d models -> %s" % (n, len(curves), a.out))


if __name__ == "__main__":
    main()
