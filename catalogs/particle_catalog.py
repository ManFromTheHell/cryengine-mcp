#!/usr/bin/env python3
"""The particle system (pfx2) as a catalog and a validator (ENGINE_LEARNING_PLAN G4).

A .pfx effect is JSON: components, each with features (and each parameter with modifiers). The engine registers 54 feature kinds and 13 modifier kinds; the editor plugin's bridge verbs
walk the engine's own registry (`bridge.particles.features`: every feature created by its own factory and serialised with its DEFAULT values, under the name a .pfx file uses) and load a
file through the engine's own effect loader (`bridge.particles.roundtrip`: what the engine HOLDS of your file). This module reads those and compares. Nothing here guesses a parameter name.

    python catalogs/particle_catalog.py --capture            needs the editor: writes engine_data/particle_features.json and engine_data/particle_modifiers.json
    python catalogs/particle_catalog.py <text>               search the features   (--group Spawn)
    python catalogs/particle_catalog.py --check <file.pfx>   what the engine keeps of the file (needs the editor)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import glob
import json
import os
import re
import sys

import crypak

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
DATA = os.path.join(HERE, "engine_data")
FEATURES = os.path.join(DATA, "particle_features.json")
MODIFIERS = os.path.join(DATA, "particle_modifiers.json")
ALIASES = os.path.join(DATA, "particle_aliases.json")
SCRATCH = os.path.join(DATA, "pfx_scratch")
SOURCE = _paths.ENGINE_SOURCE   # env CRYENGINE_SOURCE; "" when unset (the source-reading steps then skip)
PS_SOURCE = "Code/CryEngine/Cry3DEngine/ParticleSystem"

# EFeatureType, ParticleFeature.h:20-32: the roles a component needs filled. A component may enable only ONE feature of the Life, Motion and Render roles; the engine disables the later
# ones when it loads the effect (measured: two Render: Sprites -> the second saved as "Enabled": false). A role no feature fills is filled by the feature whose default_for_type is that role.
FEATURE_TYPE_BITS = {1: "Life", 2: "Spawn", 4: "Render", 8: "Effect", 16: "Size", 32: "Motion", 64: "Child"}
ONE_ONLY_ROLES = ("Life", "Motion", "Render")

# Where a modifier can sit: the parameter's host decides which kinds the engine keeps. Each host is a feature and the path to the parameter inside it.
HOSTS = {
    "float, per particle": ("FieldOpacity", ["value"]),
    "float, per spawner": ("SpawnCount", ["Amount"]),
    "size, per particle": ("FieldSize", ["value"]),
    "colour, per particle": ("FieldColor", ["Color"]),
}
# a colour parameter calls its list "Modifiers", every other kind "modifiers" (as the engine writes them)
MOD_KEY = {"colour, per particle": "Modifiers"}

_CACHE = {}


class ParticleError(Exception):
    pass


def _bridge(command, timeout=60):
    import bridge_py
    reply = bridge_py.send(command, timeout)
    if not reply.get("ok"):
        raise ParticleError("the editor refused: %s (is the bridge plugin new enough? deploy it with bridge_devloop.py cycle)" % reply.get("error"))
    return json.loads(reply["output"])


def _slash(p):
    return p.replace(chr(92), "/")


# ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- features

def capture_features(path=FEATURES):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return _bridge("bridge.particles.features " + _slash(path), 120)


def features(path=FEATURES):
    mt = os.path.getmtime(path) if os.path.exists(path) else 0
    if _CACHE.get("fpath") != path or _CACHE.get("fmt") != mt:
        if not mt:
            raise ParticleError("no catalog at %s: run  python catalogs/particle_catalog.py --capture  with the editor open" % path)
        rows = json.load(open(path, encoding="utf-8"))
        for r in rows:
            body = json.loads(r["json"])["Components"][0]["Component"]["Features"][0] if r.get("json") else {}
            # the name a .pfx file holds a feature under: its group and name joined (SERIALIZATION_CLASS_NAME(.., GroupName FeatureName, ..)), except where the engine registered a LEGACY name after it
            # (Appearance: Opacity is written "FieldOpacity"). Read from the engine's own save, not guessed.
            r["class_name"] = next(iter(body)) if body else r["group"] + r["feature"]
            r["defaults"] = next(iter(body.values())) if body else {}
            bits = r["default_for_type"]
            r["role_default"] = FEATURE_TYPE_BITS.get(bits, "") if bits else ""
        _CACHE.update(fpath=path, fmt=mt, features=rows)
    return _CACHE["features"]


def search(text="", group="", limit=25, path=FEATURES):
    words = [w for w in re.split(r"\s+", text.lower().strip()) if w]
    rows = []
    for r in features(path):
        if group and r["group"].lower() != group.lower():
            continue
        hay = (r["full"] + " " + r["class_name"] + " " + " ".join(r["defaults"])).lower()
        if all(w in hay for w in words):
            rows.append((0 if all(w in (r["full"] + r["class_name"]).lower() for w in words) else 1, r["full"].lower(), r))
    rows.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in rows[:limit]], len(rows)


def get(name, path=FEATURES):
    """One feature by its editor name ('Render: Sprites'), its file name ('RenderSprites') or 'group feature'; None when there is none."""
    key = re.sub(r"[\s:]+", "", name).lower()
    for r in features(path):
        if key in (re.sub(r"[\s:]+", "", r["full"]).lower(), r["class_name"].lower(), (r["group"] + r["feature"]).lower()):
            return r
    for a in aliases():
        if re.sub(r"[\s:]+", "", a["name"]).lower() == key and a["becomes"]:
            return get(a["becomes"], path)
    return None


def groups(path=FEATURES):
    out = {}
    for r in features(path):
        out.setdefault(r["group"], []).append(r["feature"])
    return out


# ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- legacy names

def legacy_names_in_source():
    """CRY_PFX2_LEGACY_FEATURE(<class>, "<group>", "<name>") in the particle system's source: the 13 extra names the engine reads (and for some, writes). [{type, name, source}]"""
    rows = []
    for fn in (sorted(glob.glob(os.path.join(SOURCE, PS_SOURCE.replace("/", os.sep), "Features", "*.cpp"))) if SOURCE else []):
        for i, line in enumerate(open(fn, encoding="utf-8", errors="replace"), 1):
            m = re.match(r'\s*CRY_PFX2_LEGACY_FEATURE\(\s*(\w+),\s*"([^"]*)",\s*"([^"]*)"\)', line)
            if m:
                rows.append({"type": m.group(1), "name": m.group(2) + m.group(3), "source": "%s/Features/%s:%d" % (PS_SOURCE, os.path.basename(fn), i)})
    return rows


def capture_aliases(path=ALIASES):
    """Give the engine each legacy name as a bare feature and see what it makes of it: `becomes` (the feature it keeps, None when it keeps nothing: those are the old SecondGen/Kill
    features the engine turns into child components or ignores), `written_as` (the name the engine saves it under)."""
    os.makedirs(SCRATCH, exist_ok=True)
    rows = []
    for a in legacy_names_in_source():
        doc = {"Version": 13, "Attributes": {"Attributes": []}, "Components": [{"Component": {"Name": "p", "Features": [{a["name"]: {}}]}}]}
        fin, fout = os.path.join(SCRATCH, "alias.pfx"), os.path.join(SCRATCH, "alias_out.pfx")
        json.dump(doc, open(fin, "w"))
        rep = _bridge("bridge.particles.roundtrip %s | %s" % (_slash(fin), _slash(fout)), 30)
        kept = rep["components"][0]["features"] if rep["components"] else []
        written = list(json.load(open(fout, encoding="utf-8"))["Components"][0]["Component"]["Features"]) if kept else []
        rows.append(dict(a, becomes=kept[0]["full"] if kept else None, written_as=next(iter(written[0])) if written else None))
    json.dump(rows, open(path, "w", encoding="utf-8"), indent=1)
    return rows


def aliases(path=ALIASES):
    mt = os.path.getmtime(path) if os.path.exists(path) else 0
    if _CACHE.get("apath") != path or _CACHE.get("amt") != mt:
        if not mt:
            raise ParticleError("no alias catalog at %s: run  python catalogs/particle_catalog.py --capture  with the editor open" % path)
        _CACHE.update(apath=path, amt=mt, aliases=json.load(open(path, encoding="utf-8")))
    return _CACHE["aliases"]


# ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- modifiers

def modifier_names_in_source():
    """The modifier kinds the engine registers, from SERIALIZATION_CLASS_NAME(<Base>, <Class>, "<name>", "<label>") in the particle system's source (a read-only reference):
    [{name, label, base, source: 'file:line'}]."""
    rows = []
    for fn in (sorted(glob.glob(os.path.join(SOURCE, PS_SOURCE.replace("/", os.sep), "Features", "*.cpp"))) if SOURCE else []):
        for i, line in enumerate(open(fn, encoding="utf-8", errors="replace"), 1):
            m = re.match(r'\s*SERIALIZATION_CLASS_NAME\((I\w*Modifier),\s*\w+,\s*"([^"]+)",\s*"([^"]+)"\)', line)
            if m:
                rows.append({"name": m.group(2), "label": m.group(3), "base": m.group(1), "source": "%s/Features/%s:%d" % (PS_SOURCE, os.path.basename(fn), i)})
    return rows


def _host_doc(host_names, mods, key="modifiers"):
    """An effect with one component holding the host feature whose parameter carries every candidate modifier."""
    doc_features = []
    for feat, route in host_names:
        body = {key: [{m: {}} for m in mods]}
        for k in reversed(route):
            body = {k: body}
        doc_features.append({feat: body})
    return {"Version": 13, "Attributes": {"Attributes": []}, "Components": [{"Component": {"Name": "probe", "Parent": "", "Stats": [], "nodePos": {"x": 0, "y": 0}, "Features": [{"RenderSprites": {}}] + doc_features}}]}


def capture_modifiers(path=MODIFIERS):
    """Which modifier kinds each host keeps, and every modifier's parameters with the engine's defaults: one round trip per host through the engine's own loader."""
    os.makedirs(SCRATCH, exist_ok=True)
    src = modifier_names_in_source()
    names = [m["name"] for m in src]
    by_name = {}
    for m in src:                                    # 'Attribute' and 'Inherit' are registered twice (a float and a colour kind): the same name, told apart by the host
        by_name.setdefault(m["name"], []).append(m)
    kept = {n: {} for n in names}
    defaults = {n: {} for n in names}
    for host, (feat, route) in HOSTS.items():
        mkey = MOD_KEY.get(host, "modifiers")
        doc = _host_doc([(feat, route)], sorted(set(names)), mkey)
        fin, fout = os.path.join(SCRATCH, "mod_probe.pfx"), os.path.join(SCRATCH, "mod_probe_out.pfx")
        json.dump(doc, open(fin, "w"), indent=1)
        _bridge("bridge.particles.roundtrip %s | %s" % (_slash(fin), _slash(fout)), 60)
        out = json.load(open(fout, encoding="utf-8"))
        node = [f for f in out["Components"][0]["Component"]["Features"] if feat in f][0][feat]
        for k in route:
            node = node[k]
        got = {}
        for e in node.get(mkey, []):
            (k, v), = e.items()
            got[k] = v
        for n in set(names):
            kept[n][host] = n in got
            if n in got:
                defaults[n].setdefault(host, got[n])
    rows = []
    for n in sorted(set(names)):
        first = by_name[n][0]
        rows.append({"name": n, "label": first["label"], "bases": sorted({m["base"] for m in by_name[n]}), "source": [m["source"] for m in by_name[n]],
                     "kept_by": kept[n], "defaults": defaults[n]})
    json.dump(rows, open(path, "w", encoding="utf-8"), indent=1)
    return rows


def modifiers(path=MODIFIERS):
    mt = os.path.getmtime(path) if os.path.exists(path) else 0
    if _CACHE.get("mpath") != path or _CACHE.get("mmt") != mt:
        if not mt:
            raise ParticleError("no modifier catalog at %s: run  python catalogs/particle_catalog.py --capture  with the editor open" % path)
        _CACHE.update(mpath=path, mmt=mt, modifiers=json.load(open(path, encoding="utf-8")))
    return _CACHE["modifiers"]


# ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- effects

def sample_pak():
    hits = glob.glob(r"C:/Users/*/AppData/Local/Programs/CRYENGINE Launcher/Assets/crytek/cryengine-gamesdk-sample-project/*/gamesdk/gamedata.pak")
    return hits[0] if hits else ""


def effects():
    """Every .pfx the project and the GameSDK sample project hold: {label: json text}. (Read-only; the sample's come out of its pak in memory.)"""
    out = {}
    for fn in glob.glob(os.path.join(PROJECT, "Assets", "**", "*.pfx"), recursive=True):
        out["project:" + os.path.relpath(fn, PROJECT).replace(chr(92), "/")] = open(fn, encoding="utf-8").read()
    pak = sample_pak()
    if pak:
        for e in crypak.entries(pak):
            name = e["name"].replace(chr(92), "/")
            if name.lower().endswith(".pfx"):
                out["sample:" + name] = crypak.read(dict(e, pak=pak)).decode("utf-8")
    return out


def feature_names_in(text):
    """The feature kinds an effect names, in file order (every component)."""
    names = []
    for comp in json.loads(text).get("Components", []):
        for f in comp["Component"].get("Features", []):
            names.extend(f)
    return names


def usage():
    """{feature class name: number of effects (project + sample) using it}"""
    counts = {}
    for text in effects().values():
        for n in set(feature_names_in(text)):
            counts[n] = counts.get(n, 0) + 1
    return counts


# ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- the check

def _empty(v):
    return v is None or v == "" or v == [] or v == {}


def _same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 1e-5 * max(1.0, abs(a), abs(b))
    return a == b


def _short(v):
    s = json.dumps(v)
    return s if len(s) <= 60 else s[:57] + "..."


def _tagged(lst):
    return bool(lst) and all(isinstance(e, dict) and len(e) == 1 for e in lst)


_KIND = {"Features": "feature", "modifiers": "modifier", "Components": "component"}


def _walk(src, out, path, rep, parent_key=""):
    if isinstance(src, dict) and isinstance(out, dict):
        if "Enabled" in out and out["Enabled"] is False and src.get("Enabled", True) is not False:
            rep["disabled_by_engine"].append(path or "/")
        for k, v in src.items():
            if k not in out:
                if not _empty(v):
                    rep["dropped"].append({"path": path + "/" + k, "kind": "key", "value": _short(v)})
                continue
            _walk(v, out[k], path + "/" + k, rep, k)
        rep["defaults_added"] += sum(1 for k in out if k not in src)
    elif isinstance(src, list) and isinstance(out, list):
        if parent_key == "Components" and _tagged(src) and all("Component" in e for e in src + out):
            # components are matched by NAME: the engine writes them in its own order (a child after its parent), so the position says nothing
            out_by = {e["Component"].get("Name"): e["Component"] for e in out}
            for i, e in enumerate(src):
                comp = e["Component"]
                name = comp.get("Name", "#%d" % (i + 1))
                if name in out_by:
                    _walk(comp, out_by[name], "%s/Component[%s]" % (path, name), rep, "Component")
                else:
                    rep["dropped"].append({"path": "%s/Component[%s]" % (path, name), "kind": "component", "value": ""})
            rep["defaults_added"] += max(0, len(out) - len(src))
        elif _tagged(src) and (_tagged(out) or not out):
            seen_o = {}
            for e in out:
                (t, _), = e.items()
                seen_o.setdefault(t, []).append(e[t])
            seen_s = {}
            for e in src:
                (t, v), = e.items()
                i = seen_s.get(t, 0)
                seen_s[t] = i + 1
                label = "%s#%d" % (t, i + 1) if sum(1 for x in src if t in x) > 1 else t
                if i >= len(seen_o.get(t, [])):
                    rep["dropped"].append({"path": path + "/" + label, "kind": _KIND.get(parent_key, "item"), "value": ""})
                else:
                    _walk(v, seen_o[t][i], path + "/" + label, rep, t)
            for t, vs in seen_o.items():
                extra = len(vs) - sum(1 for x in src if t in x)
                if extra > 0:
                    rep["defaults_added"] += extra
        else:
            for i, v in enumerate(src):
                if i < len(out):
                    _walk(v, out[i], "%s[%d]" % (path, i), rep, parent_key)
                elif not _empty(v):
                    rep["dropped"].append({"path": "%s[%d]" % (path, i), "kind": "item", "value": _short(v)})
    elif not _same(src, out):
        rep["changed"].append({"path": path, "was": _short(src), "now": _short(out)})


def diff_effect(src, out):
    """What the engine did to an effect: `dropped` (features, modifiers and keys it did not recognise, or whose name changed between versions: they fall back to the default),
    `changed` (a value it clamped or converted), `disabled_by_engine` (a second Life/Motion/Render feature in one component), `defaults_added` (parameters it filled in)."""
    rep = {"dropped": [], "changed": [], "disabled_by_engine": [], "defaults_added": 0}
    _walk(src, out, "", rep)
    return rep


def check(path, keep_output=True):
    """Load a .pfx through the engine's own loader (the editor must be open) and say what the engine holds of it. `path`: absolute, or relative to the project.
    The engine's rewrite is written to engine_data/pfx_scratch/<name>.engine.pfx and never replaces the original."""
    tried = [path] if os.path.isabs(path) else [os.path.join(PROJECT, path), os.path.join(PROJECT, "Assets", path)]      # the project's root, then Assets (how every other tool names an asset: libs/particles/x.pfx)
    full = next((t for t in tried if os.path.isfile(t)), None)
    if full is None:
        raise ParticleError("no such file: tried %s" % " and ".join(tried))
    text = open(full, encoding="utf-8").read()
    try:
        src = json.loads(text)
    except ValueError as exc:
        raise ParticleError("not JSON (%s): a .pfx is a JSON document" % exc)
    return check_text(text, os.path.splitext(os.path.basename(full))[0], keep_output)


def check_text(text, name="effect", keep_output=True):
    os.makedirs(SCRATCH, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    fin, fout = os.path.join(SCRATCH, safe + ".in.pfx"), os.path.join(SCRATCH, safe + ".engine.pfx")
    with open(fin, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    rep = _bridge("bridge.particles.roundtrip %s | %s" % (_slash(fin), _slash(fout)), 60)
    os.remove(fin)
    out = json.load(open(fout, encoding="utf-8"))
    if not keep_output:
        os.remove(fout)
    src = json.loads(text)
    d = diff_effect(src, out)
    version, engine_version = src.get("Version"), out.get("Version")
    converted = isinstance(version, int) and isinstance(engine_version, int) and version < engine_version
    d["changed"] = [c for c in d["changed"] if c["path"] != "/Version"]
    d.update(loaded=rep["loaded"], components=rep["components"], output=_slash(fout) if keep_output else "", version=version, engine_version=engine_version, converted=converted)
    d["ok"] = bool(rep["loaded"]) and not (d["dropped"] or d["changed"] or d["disabled_by_engine"])
    d["note"] = ("a version-%s file: the engine CONVERTS older structures (renamed parameters, restructured values) while it loads, so entries listed as dropped may have been moved rather than lost; "
                 "compare the engine's own version (`output`)" % version) if converted else ""
    return d


def main():
    args = sys.argv[1:]
    if "--capture" in args:
        print(capture_features())
        print("%d modifier kinds" % len(capture_modifiers()))
        print("%d legacy names" % len(capture_aliases()))
        return
    if "--check" in args:
        r = check(args[args.index("--check") + 1])
        print("loaded" if r["loaded"] else "NOT LOADED", "-", "%d component(s)" % len(r["components"]), "- %d defaults filled in" % r["defaults_added"])
        for k in ("dropped", "changed", "disabled_by_engine"):
            for it in r[k]:
                print("  %-18s %s" % (k, it if isinstance(it, str) else "%s  %s" % (it["path"], it.get("value", "") or ("%s -> %s" % (it.get("was"), it.get("now"))))))
        print("engine's version:", r["output"])
        return
    group = args[args.index("--group") + 1] if "--group" in args else ""
    text = " ".join(a for a in args if not a.startswith("--") and a != group)
    rows, total = search(text, group, 60)
    for r in rows:
        print("%-24s %-22s %s" % (r["full"], r["class_name"], ", ".join(r["defaults"])[:80]))
    print("%d of %d shown" % (len(rows), total))


if __name__ == "__main__":
    main()
