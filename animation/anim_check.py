#!/usr/bin/env python3
"""Character animation data against the names the ENGINE resolved (ENGINE_LEARNING_PLAN B6 rest, C2), from the open editor, without running the game.

The plugin verb `bridge.character.names` creates a character (ICharacterManager::CreateInstance) and writes its skeleton's joint names and its animation set's names: the lists the engine itself
resolves a chrparams, a blend space or a Mannequin fragment against. This module checks the project's own files against them:

  A001 a chrparams names a joint the skeleton does not have (IK end effector / root / chain, AimIK / LookIK rotation and position lists, weapon joints, IK target and blend bones,
       LOD joint lists): the engine ignores the entry or the IK handle silently
  A002 a chrparams animation-list entry whose path does not exist (the $-entries: events database, include, face library, tracks database)
  A003 a blend space (.bspace) example names an animation the character's animation set does not have
  A004 a blend space index is out of range (a Pseudo point refers to a point that does not exist yet, a Face to a point beyond examples + pseudo points)
  A005 a blend space dimension or extraction parameter is not one of the engine's 14 motion parameters (CharacterManager::GetMotionParameterDetails)
  A006 a blend space with fewer than 3 points, or no Blendable faces (nothing to interpolate)
  A007 (information) a clip the project uses carries an `audio_trigger` animation event whose trigger no audio library of the project defines: since 5.7 the ENGINE plays animation audio
       events itself (ca_SkeletonEffectsPlayAudioInEngine = 1, a fixed value), so a trigger with that name would sound, and without one nothing plays
  A008 (information) the same for an `effect` event whose particle effect the project's legacy libraries do not have

    python animation/anim_check.py [character.cdf]        default Objects/Characters/Human/sdk_player/sdk_player.cdf (the game's)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
DATA = os.path.join(HERE, "engine_data")
SCRATCH = os.path.join(DATA, "anim_scratch")
CHARACTER = "Objects/Characters/Human/sdk_player/sdk_player.cdf"
MOTION_PARAMS = ("MoveSpeed", "TurnSpeed", "TravelAngle", "TravelSlope", "TurnAngle", "TravelDist", "StopLeg", "BlendWeight", "BlendWeight2", "BlendWeight3", "BlendWeight4", "BlendWeight5", "BlendWeight6", "BlendWeight7")
JOINT_ATTRS = ("JointName", "EndEffector", "Root", "J0", "J1", "J2", "J3", "J4", "Target", "ParameterJoint", "StartJoint", "ReferenceJoint")           # ADIKTarget's Weight is a joint too, but ImpactJoint's Weight is a number

sys.path.insert(0, HERE)


class AnimError(RuntimeError):
    pass


def _slash(p):
    return p.replace(chr(92), "/")


def names(character=CHARACTER):
    """{file, created, skeleton, joints: [...], animations: [...]} from the editor."""
    import bridge_py
    os.makedirs(SCRATCH, exist_ok=True)
    out = os.path.join(SCRATCH, "names_%d.json" % os.getpid())
    if os.path.exists(out):
        os.remove(out)
    r = bridge_py.send("bridge.character.names %s | %s" % (_slash(out), character), 60)
    if not r.get("ok"):
        raise AnimError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % r.get("error"))
    with open(out, encoding="utf-8") as f:
        d = json.load(f)
    os.remove(out)
    return d


def _walk(root, *dirs):
    for r, _, files in os.walk(root):
        for f in files:
            yield os.path.join(r, f)


def project_files(ext):
    return sorted(_slash(os.path.relpath(p, ASSETS)) for p in _walk(ASSETS) if p.lower().endswith(ext))


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def chrparams_refs(text):
    """-> ([(joint name, where in the file)], [(animation entry name, path)]) from a chrparams' text."""
    root = ET.fromstring(re.sub(r"<\?xml[^>]*\?>", "", text).strip())
    joints, entries = [], []
    for el in root.iter():
        if el.tag in ("Animation",):
            if el.get("name") and el.get("path"):
                entries.append((el.get("name"), el.get("path")))
            continue
        for attr in JOINT_ATTRS + (("Weight",) if el.tag == "ADIKTarget" else ()):
            v = el.get(attr)
            if v:
                joints.append((v, "<%s %s>" % (el.tag, attr)))
        if el.tag == "Joint" and el.get("name"):
            joints.append((el.get("name"), "<Joint name> (LOD list)"))
    return joints, entries


def check_chrparams(path, text, joint_names, exists=None):
    findings = []
    have = set(joint_names)
    low = {j.lower() for j in joint_names}
    try:
        joints, entries = chrparams_refs(text)
    except ET.ParseError as exc:
        return [_finding("error", "A001", path, "not well-formed XML: %s" % exc)]
    seen = set()
    for j, where in joints:
        if j in have or j.lower() in low or (j, where) in seen:
            continue
        seen.add((j, where))
        findings.append(_finding("warning" if "LOD list" in where else "error", "A001", path, "%s names joint '%s', which the skeleton does not have (the engine resolved %d joints)%s" % (where, j, len(joint_names),
                                 ": a LOD list only says which joints stay in a LOD, so a name that matches nothing changes nothing" if "LOD list" in where else ""),
                                 "use a joint name from the skeleton (bridge.character.names lists them)"))
    if exists is not None:
        for name, p in entries:
            if name.startswith("$") and not exists(p):
                findings.append(_finding("error", "A002", path, "animation-list entry %s names '%s', which does not exist" % (name, p)))
    return findings


def check_bspace(path, text, animation_names):
    findings = []
    low = {a.lower() for a in animation_names}
    try:
        root = ET.fromstring(re.sub(r"<\?xml[^>]*\?>", "", text).strip())
    except ET.ParseError as exc:
        return [_finding("error", "A006", path, "not well-formed XML: %s" % exc)]
    examples = [e.get("AName") or "" for e in root.findall("./ExampleList/Example")]
    for i, a in enumerate(examples):
        if a.lower() not in low:
            findings.append(_finding("error", "A003", path, "example %d names animation '%s', which the character's animation set does not have (%d animations)" % (i, a, len(animation_names)),
                                     "check the name (the set holds the .caf stems of the character's animation list)"))
    for sec in ("Dimensions", "AdditionalExtraction"):
        for p in root.findall("./%s/Param" % sec):
            if (p.get("name") or "") not in MOTION_PARAMS:
                findings.append(_finding("error", "A005", path, "%s parameter '%s' is not one of the engine's motion parameters (%s)" % (sec, p.get("name"), ", ".join(MOTION_PARAMS[:6]) + " ..."),
                                         "CharacterManager::GetMotionParameterDetails lists them"))
    pseudo = root.findall("./ExamplePseudo/Pseudo")
    total = len(examples) + len(pseudo)
    for k, p in enumerate(pseudo):
        for key in ("p0", "p1"):
            idx = int(p.get(key) or -1)
            if idx < 0 or idx >= len(examples) + k:
                findings.append(_finding("error", "A004", path, "Pseudo point %d: %s=%d refers to a point that does not exist yet (only %d points precede it)" % (len(examples) + k, key, idx, len(examples) + k)))
    faces = root.findall("./Blendable/Face")
    for k, f in enumerate(faces):
        for key in ("p0", "p1", "p2", "p3"):
            if f.get(key) is not None and not (0 <= int(f.get(key)) < total):
                findings.append(_finding("error", "A004", path, "Face %d: %s=%s is beyond the %d points (examples + pseudo points)" % (k, key, f.get(key), total)))
    if total < 3 or not faces:
        findings.append(_finding("warning", "A006", path, "%d point(s) and %d face(s): a blend space needs 3+ points and faces to interpolate" % (total, len(faces))))
    return findings


def _xml(text):
    return ET.fromstring(re.sub(r"<\?xml[^>]*\?>", "", text).strip())


def used_clips(assets=ASSETS):
    """The animation stems the project's own data plays: Mannequin databases (<Animation name=...>) and blend-space examples -> {stem (lower case): the file}."""
    out = {}
    for p in project_files(".adb"):
        try:
            root = _xml(open(os.path.join(assets, p.replace("/", os.sep)), encoding="utf-8", errors="replace").read())
        except ET.ParseError:
            continue
        for el in root.iter("Animation"):
            if el.get("name"):
                out.setdefault(el.get("name").lower(), p)
    for p in project_files(".bspace"):
        try:
            root = _xml(open(os.path.join(assets, p.replace("/", os.sep)), encoding="utf-8", errors="replace").read())
        except ET.ParseError:
            continue
        for e in root.findall("./ExampleList/Example"):
            if e.get("AName"):
                out.setdefault(e.get("AName").lower(), p)
    return out


def read_game_file(path, assets=ASSETS, names=None):
    """The text of a game path: loose under Assets / Engine / GameSDK, else inside a pak (the GameSDK's paks store backslash names) -> str | None"""
    import zipfile
    want = _slash(path).lower()
    nm = names
    if nm is None:
        from asset_lint import Names
        nm = Names()
    for root in nm.loose_roots:
        full = os.path.join(root, want.replace("/", os.sep))
        if os.path.isfile(full):
            return open(full, encoding="utf-8", errors="replace").read()
    for pak in nm._paks():
        try:
            z = zipfile.ZipFile(pak)
        except Exception:                                                      # noqa: BLE001
            continue
        for n in z.namelist():
            if _slash(n).lower() == want:
                zi = z.getinfo(n)
                zi.orig_filename = zi.orig_filename.replace("/", chr(92))
                return z.read(zi).decode("utf-8", "replace")
    return None


def clip_events(text):
    """An .animevents file's text -> {clip stem (lower case): [(event name, parameter, time)]}"""
    root = _xml(text)
    out = {}
    for a in root.iter("animation"):
        stem = re.sub(r"\.caf$", "", os.path.basename(_slash(a.get("name") or "")).lower())
        out.setdefault(stem, []).extend((e.get("name") or "", e.get("parameter") or "", e.get("time") or "") for e in a.iter("event"))
    return out


def check_events(used, events, triggers, effects, events_file="events.animevents"):
    """The events of the clips the project uses against the project's audio triggers (A007) and particle effects (A008) -> [finding]"""
    findings = []
    trig = {t.lower() for t in triggers}
    fx = {e.lower() for e in effects}
    for stem, where in sorted(used.items()):
        for name, param, when in events.get(stem, []):
            if name == "audio_trigger" and param.lower() not in trig:
                findings.append(_finding("info", "A007", "%s (%s)" % (stem, where), "plays the audio_trigger event '%s' at %ss (from %s) and no audio library of the project defines that trigger: the engine plays animation "
                                         "audio events itself (ca_SkeletonEffectsPlayAudioInEngine = 1), so nothing sounds; defining a trigger with this name in an ACE library would give the clip a sound" % (param, when, events_file)))
            elif name == "effect" and param and param.lower() not in fx:
                findings.append(_finding("info", "A008", "%s (%s)" % (stem, where), "plays the effect event '%s' (from %s), which the project's particle libraries do not have: nothing is spawned" % (param, events_file)))
    return findings


def particle_effect_names(assets=ASSETS):
    """The legacy particle library effects (<library>.<name>) of the project, lower case."""
    out = set()
    base = os.path.join(assets, "libs", "particles")
    if os.path.isdir(base):
        for f in os.listdir(base):
            if f.lower().endswith(".xml"):
                try:
                    root = ET.parse(os.path.join(base, f)).getroot()
                except ET.ParseError:
                    continue
                lib = (root.get("Name") or os.path.splitext(f)[0]).lower()
                out.update(lib + "." + (p.get("Name") or "").lower() for p in root.iter("Particles"))
    return out


def check(character=CHARACTER):
    """-> {ok, errors, warnings, findings, character: {file, joints, animations}, checked: {chrparams: [...], bspace: [...]}}"""
    d = names(character)
    if not d.get("created"):
        raise AnimError("the engine cannot create character '%s'" % character)
    findings, checked = [], {"chrparams": [], "bspace": []}
    skeleton = _slash(d.get("skeleton") or "").lower()
    base = os.path.splitext(os.path.basename(skeleton))[0]
    try:
        from asset_lint import Names
        nm = Names()
        exists = lambda p: nm.exists(p)                                        # noqa: E731
    except Exception:                                                          # noqa: BLE001
        exists = None
    for p in project_files(".chrparams"):
        if os.path.splitext(os.path.basename(p))[0].lower() != base:
            continue                                                           # a chrparams belongs to the skeleton of the same name
        checked["chrparams"].append(p)
        ctext = open(os.path.join(ASSETS, p.replace("/", os.sep)), encoding="utf-8", errors="replace").read()
        findings.extend(check_chrparams(p, ctext, d["joints"], exists))
        try:
            ev_path = next((path for name, path in chrparams_refs(ctext)[1] if name == "$AnimEventDatabase"), None)
            ev_text = read_game_file(ev_path, ASSETS) if ev_path else None
            if ev_text:
                import ace_catalog
                findings.extend(check_events(used_clips(), clip_events(ev_text), ace_catalog.load()["triggers"], particle_effect_names(), ev_path))
                checked["events"] = ev_path
        except Exception as exc:                                               # noqa: BLE001 - the events check is extra; the rest stands
            findings.append(_finding("info", "A007", p, "the animation events file could not be read: %s" % str(exc)[:100]))
    for p in project_files(".bspace"):
        checked["bspace"].append(p)
        findings.extend(check_bspace(p, open(os.path.join(ASSETS, p.replace("/", os.sep)), encoding="utf-8", errors="replace").read(), d["animations"]))
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "findings": findings, "character": {"file": d["file"], "skeleton": d.get("skeleton"), "joints": len(d["joints"]), "animations": len(d["animations"])}, "checked": checked}


def main():
    r = check(sys.argv[1] if len(sys.argv) > 1 else CHARACTER)
    print("%s: %d error(s), %d warning(s); %s; checked %s" % ("OK" if r["ok"] else "PROBLEMS", r["errors"], r["warnings"], r["character"], r["checked"]))
    for f in r["findings"]:
        print("  %-7s %s  %s\n          %s" % (f["severity"], f["code"], f["where"], f["message"][:260]))


if __name__ == "__main__":
    main()
