#!/usr/bin/env python3
"""Author and look at particle effects (ENGINE_LEARNING_PLAN G4): the engine's .pfx format, written from the engine's own catalog and checked by the engine's own loader.

    compose(components)         a document from a plain description; every value starts from the engine's DEFAULT for that feature; an unknown feature or parameter is REFUSED
                                (the engine would drop it silently: particle_catalog.check shows it)
    write_effect(path, comps)   compose, write under Assets/Particles, load it through the engine (the editor must be open) and report what the engine kept
    preview(effect, position)   look at an effect in the EDITOR viewport (a scratch entity with a Particle Emitter component; the game is not run), with a control picture first

A component is {"name": "embers", "parent": "", "features": [{"feature": "Spawn: Rate", "params": {"Amount": {"value": 80}}, "enabled": true}, ...]}.
A parameter that carries modifiers is {"value": 0.35, "modifiers": [{"Random": {"Amount": 0.4}}]}; a colour parameter calls the list "Modifiers".
What the engine renders: a feature without a texture renders as a tinted blob; textures/sprites/glow/glow_a.dds with BlendMode Additive is the glowing-ember look;
fire/ember.dds, glow_a and smoke_a with the default Alpha blend render DARK (their RGB is dark: they are made for additive or lit use).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import copy
import json
import os
import sys
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import particle_catalog as P  # noqa: E402

ParticleError = P.ParticleError


def _merge(base, patch):
    out = dict(base)
    for k, v in patch.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _validate(defaults, given, path):
    """Every key in `given` must exist in the engine's DEFAULT for that feature. A modifier list may sit under 'modifiers' (or 'Modifiers' for a colour): each must be a kind the engine
    registers. -> a list of problems."""
    problems = []
    if not isinstance(given, dict) or not isinstance(defaults, dict):
        return problems
    mods = {m["name"] for m in P.modifiers()}
    for k, v in given.items():
        if k in ("modifiers", "Modifiers"):
            for m in v if isinstance(v, list) else []:
                kind = next(iter(m)) if isinstance(m, dict) and len(m) == 1 else None
                if kind not in mods:
                    problems.append("%s/%s: %r is not a modifier the engine registers (%s)" % (path, k, kind, ", ".join(sorted(mods))))
            continue
        if k == "Enabled":
            continue
        if k not in defaults:
            problems.append("%s/%s: the engine has no such parameter (it has: %s)" % (path, k, ", ".join(defaults) or "none"))
            continue
        problems += _validate(defaults[k], v, path + "/" + k)
    return problems


def compose(components):
    problems, comps = [], []
    if not isinstance(components, list) or not components:
        raise ParticleError("components is a list of {name, features: [{feature, params}]}")
    for ci, comp in enumerate(components):
        name = comp.get("name") or "Component%02d" % (ci + 1)
        feats = []
        for fi, f in enumerate(comp.get("features", [])):
            where = "%s[%d]" % (name, fi)
            row = P.get(f.get("feature", ""))
            if not row:
                near, _ = P.search(f.get("feature", ""), limit=4)
                problems.append("%s: no feature %r%s" % (where, f.get("feature"), " (did you mean: %s)" % ", ".join(r["full"] for r in near) if near else ""))
                continue
            body = copy.deepcopy(row["defaults"])
            params = f.get("params") or {}
            if isinstance(body, dict):
                bad = _validate(row["defaults"], params, where + ":" + row["full"])
                problems += bad
                if not bad:
                    body = _merge(body, params)
            elif params:
                body = params                           # a Child feature is a list of component names
            if f.get("enabled") is False and isinstance(body, dict):
                body["Enabled"] = False
            feats.append({row["class_name"]: body})
        entry = {"Name": name, "Stats": [], "nodePos": {"x": 0.0, "y": 0.0}, "Features": feats}
        if comp.get("parent"):
            entry["Parent"] = comp["parent"]
        comps.append({"Component": entry})
    if problems:
        raise ParticleError("the effect was not written, %d problem(s):\n  - %s" % (len(problems), "\n  - ".join(problems)))
    return {"Version": 13, "Attributes": {"Attributes": []}, "Components": comps}


def effect_path(path):
    """-> (name the engine uses, absolute file). Refuses anything outside Assets/Particles."""
    rel = path.replace(chr(92), "/").lstrip("/")
    if rel.lower().startswith("assets/"):
        rel = rel[7:]
    if not rel.lower().startswith("particles/") or not rel.lower().endswith(".pfx") or ".." in rel.split("/"):
        raise ParticleError("a particle effect lives under Assets/Particles and ends in .pfx (got %r)" % path)
    return rel, os.path.join(P.PROJECT, "Assets", rel.replace("/", os.sep))


def write_effect(path, components, overwrite=False):
    """Write the effect, load it with the engine's loader and say what the engine kept. Never replaces a file unless overwrite=True; the editor's CryPak caches a loose asset, so an
    overwritten effect keeps showing the old one until the editor restarts: a new name is better."""
    rel, full = effect_path(path)
    if os.path.exists(full) and not overwrite:
        raise ParticleError("%s exists: pass overwrite=True to replace it (the editor may keep showing the old one until it restarts; a new name avoids that)" % rel)
    doc = compose(components)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent="\t")
    rep = P.check(full, keep_output=False)
    return {"path": "Assets/" + rel, "effect_name": rel, "components": len(doc["Components"]), "features": sum(len(c["Component"]["Features"]) for c in doc["Components"]),
            "engine_check": {k: rep[k] for k in ("loaded", "ok", "dropped", "changed", "disabled_by_engine", "defaults_added")}}


def preview(effect, position, distance=6.0, wait_s=4.0, name="zz_pfx_preview"):
    """Look at an effect in the EDITOR viewport: a scratch entity with an EMPTY Particle Emitter component is created and two control pictures are taken, then the emitter is assigned `effect` and the
    picture is taken after `wait_s`, and the entity is deleted again (the object count is checked: a graceful editor close would SAVE a leftover). -> {picture, bright_pixels (in the middle box, brighter than the control by 40 levels) and bright_pixels_noise (two control pictures: the same count), box_mean_abs_diff and box_noise, changed_pixels, ...}"""
    import numpy as np
    import debug_views as D
    import object_props as O
    import shot_compare as S
    rel, full = effect_path(effect)
    if not os.path.isfile(full):
        raise ParticleError("no such effect: %s" % rel)
    if len(position) != 3:
        raise ParticleError("position is x, y, z in metres")
    x, y, z = [float(c) for c in position]
    eye, target = [x, y - float(distance), z + 1.0], [x, y, z + 0.5]
    with O.scratch(name):
        # the control is taken WITH the scratch entity and its (empty) emitter already there: the editor draws an icon and a selection box for the entity, and that must not count as the effect
        O.create("EntityWithComponent", name, [x, y, z])
        added = P._bridge("bridge.entity.add_component %s Particle Emitter" % name, 30)
        c0 = S.load(D.shoot(name + "_ctrl0", eye, target))
        c1 = S.load(D.shoot(name + "_ctrl1", eye, target))
        noise = S.diff_stats(c0, c1)["mean_abs_diff"]
        cur = P._bridge("bridge.component.properties %s CParticleComponent" % name, 30)["properties"]
        cur["FilePath"] = rel
        P._bridge("bridge.component.properties %s CParticleComponent %s" % (name, json.dumps(cur, separators=(",", ":"))), 30)
        time.sleep(float(wait_s))
        pic = D.shoot(name + "_with", eye, target)
        img = S.load(pic)
        changed = np.abs(img.astype(int) - c0.astype(int)).max(axis=2) > 24
        n = int(changed.sum())
        # the grass sways: two pictures of the SAME view differ in some pixels too, so what counts is the excess over that. The box is the middle of the pane, above the ground: the plume
        # rises there (the camera looks at the emitter), and little else moves in it.
        h, w = img.shape[:2]
        y0, y1, x0, x1 = int(.05 * h), int(.65 * h), int(.25 * w), int(.75 * w)
        lum = lambda a: a[y0:y1, x0:x1].astype(int).mean(axis=2)           # noqa: E731
        bright = int(((lum(img) - lum(c0)) > 40).sum())
        bright_noise = int(((lum(c1) - lum(c0)) > 40).sum())
        n_noise = int((np.abs(c1.astype(int) - c0.astype(int)).max(axis=2) > 24).sum())
        res = {"picture": pic, "emitter_at": [x, y, z], "eye": eye, "bright_pixels": bright, "bright_pixels_noise": bright_noise,
               "box_mean_abs_diff": round(float(np.abs(img[y0:y1, x0:x1].astype(int) - c0[y0:y1, x0:x1].astype(int)).mean()), 3),
               "box_noise": round(float(np.abs(c1[y0:y1, x0:x1].astype(int) - c0[y0:y1, x0:x1].astype(int)).mean()), 3),
               "changed_pixels": n, "noise_pixels": n_noise, "mean_rgb_changed": img[changed].mean(axis=0).round(0).tolist() if n else None,
               "mean_rgb_control_there": c0[changed].mean(axis=0).round(0).tolist() if n else None, "mean_abs_diff": S.diff_stats(c0, img)["mean_abs_diff"], "noise": noise,
               "component": added.get("component")}
    return res


if __name__ == "__main__":
    print(__doc__)
