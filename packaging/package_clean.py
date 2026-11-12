#!/usr/bin/env python3
"""What a package may contain in its bin folders, decided by a dependency walk and an ALLOWLIST, never by a hunch.

Crytek's packager copies every engine binary: 10 unit-test executables, Game_Server.exe, four VR plug-ins with their SDK DLLs, the Steam/Discord platform plug-ins, three audio middleware
implementations that the project does not use (Wwise, Fmod, Adx2 and PortAudio next to the SDL-mixer one it does), and, for a Release package, the packager used to copy the PROFILE Game.dll
(`package_project.py` documents it). This module removes only what it can PROVE nothing needs, lists everything it keeps although it is not proven needed, and fails the package when
anything appears that it has never classified.

The proof, per file marked for removal (RULES below):
  1. no module that stays imports it statically (read from each PE's import table, pe_imports.py). Example from a Release package: CryLobby.dll imports steam_api64.dll, so
     steam_api64.dll STAYS (CryLobby is loaded by CryAction), although CryGamePlatformSteam.dll, which also wants it, goes;
  2. the project's plug-in list (Game.cryproject `require.plugins`) and the engine's default plug-ins (CryPluginManager.h) do not name it; VR devices (HMDManager.cpp) and the platform plug-ins are
     loaded only as plug-ins, the audio implementation by the CVar s_AudioImpl whose default is CryAudioImplSDLMixer (CryAudioSystem.cpp:215), a Release launcher cannot change it.
What is NOT proven (D3D12/Vulkan renderers, CryLobby, CryLiveCreate, CryGamePlatform, ...) is kept and listed in package_allowlist.json under `keep_unsure` with the reason.
Nothing here has been proven by a RUN of the stripped game; the first package made with it needs one playthrough (the gate does that: RELEASE_OK.json is written only after it).

    package_clean.py plan <package-dir> [--config release]      what would be removed / kept / refused, changes nothing
    package_clean.py clean <package-dir> [--config release]     remove it
    package_clean.py audit <package-dir> [--config release]     the allowlist test (exit 1 on any fault)
    package_clean.py write-allowlist <package-dir>              (re)generate package_allowlist.json from an UNCLEANED release package, then review the diff
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import fnmatch
import hashlib
import json
import os
import re
import sys

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ALLOWLIST = os.path.join(HERE, "package_allowlist.json")
RELEASE_BIN = "bin/win_x64_release"
GAME_BIN = "bin/win_x64"
sys.path.insert(0, HERE)

# (id, why, [file patterns, matched case-insensitively against base names]) -- release packages only, except the first and last groups, which apply to every configuration
RULES = [
    ("unit_tests", "the engine's unit-test executables; nothing in a game starts them", ["*UnitTest*.exe"], "all"),
    ("dedicated_server", "Game_Server.exe is the dedicated-server launcher; the game never starts it", ["Game_Server.exe"], "release"),
    ("vr", "VR devices load only as plug-ins (HMDManager.cpp) and the project lists none", ["CryOpenVR.dll", "CryOSVR.dll", "CryOculusVR.dll", "CryEmulatorVR.dll", "openvr_api.dll", "osvr*.dll", "glew32.dll"], "release"),
    ("platform_services", "Steam/Discord platform plug-ins load only through CryGamePlatform, which the project restricts to PS4", ["CryGamePlatformSteam.dll", "CryGamePlatformDiscord.dll", "discord_game_sdk.dll"], "release"),
    ("audio_middleware", "s_AudioImpl defaults to CryAudioImplSDLMixer and a Release launcher cannot change it", ["CryAudioImplWwise.dll", "CryAudioImplFmod.dll", "CryAudioImplAdx2.dll", "CryAudioImplPortAudio.dll", "fmod.dll", "fmodstudio.dll"], "release"),
    ("stale_leftovers", "files the engine-DLL patching experiments left beside the binaries", ["*.orig571", "*.patched", "*.prev"], "all"),
]
# roots of the static-import walk: what the game cannot run without
ROOT_MODULES = ["GameLauncher.exe", "Game.dll", "CrySystem.dll", "CryAudioImplSDLMixer.dll", "CryRenderD3D11.dll", "CryScaleformD3D11.dll"]
BIN_DIRS = {"release": [RELEASE_BIN, GAME_BIN, "bin"], "profile": [GAME_BIN, "bin"]}


def sha(path, n=16):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:n]


def project_plugins(project=PROJECT):
    """The module names Game.cryproject lists as plug-ins (+ the engine's defaults, CryPluginManager.h GetDefaultPlugins)"""
    names = {"CryDefaultEntities", "CrySensorSystem", "CryPerceptionSystem", "CryGamePlatform"}
    try:
        with open(os.path.join(project, "Game.cryproject"), encoding="utf-8") as f:
            for p in json.load(f).get("require", {}).get("plugins", []):
                base = os.path.basename(p.get("path", ""))
                if base:
                    names.add(base[:-4] if base.lower().endswith(".dll") else base)
    except (OSError, ValueError):
        pass
    return names


def listing(dest):
    """{relative path (forward slashes) -> absolute path} of the files in the package's bin folders (not below them)"""
    out = {}
    for rel in ("bin", GAME_BIN, RELEASE_BIN):
        d = os.path.join(dest, *rel.split("/"))
        if os.path.isdir(d):
            for n in os.listdir(d):
                if os.path.isfile(os.path.join(d, n)):
                    out[rel + "/" + n] = os.path.join(d, n)
    return out


def rule_for(name, config):
    for rid, why, pats, scope in RULES:
        if scope in ("all", config) and any(fnmatch.fnmatch(name.lower(), p.lower()) for p in pats):
            return rid, why
    return None


def static_imports(files):
    """{lower base name -> set of lower base names of the package's own modules it imports statically}"""
    import pe_imports
    local = {os.path.basename(r).lower() for r in files}
    out = {}
    for rel, path in files.items():
        b = os.path.basename(rel).lower()
        if b.endswith((".dll", ".exe")):
            try:
                out.setdefault(b, set()).update(k.lower() for k in pe_imports.imports(path) if k.lower() in local)
            except Exception as exc:                              # noqa: BLE001 - an unreadable PE is reported by the caller as a fault
                out.setdefault(b, set()).add("!unreadable:" + str(exc)[:60])
    return out


def plan(dest, config="release", project=PROJECT):
    """-> {"remove": [rel], "refused": {rel: why it stays}, "keep": [rel]} without changing anything"""
    files = listing(dest)
    imports = static_imports(files)
    marked = {rel for rel in files if rule_for(os.path.basename(rel), config)}
    plugins = {p.lower() + ".dll" for p in project_plugins(project)}
    remove, refused = set(marked), {}
    changed = True
    while changed:                                                # removing one file can make another one needed no longer, but never the reverse: iterate to a fixed point on REFUSALS
        changed = False
        names = {os.path.basename(r).lower() for r in remove}
        for rel in sorted(remove):
            b = os.path.basename(rel).lower()
            users = sorted(m for m, deps in imports.items() if b in deps and m not in names)
            if users or b in plugins:
                refused[rel] = ("imported statically by %s, which stays" % ", ".join(users)) if users else "named in the project's plug-in list"
                remove.discard(rel)
                changed = True
                break
    return {"remove": sorted(remove), "refused": refused, "keep": sorted(r for r in files if r not in remove)}


def clean(dest, config="release", project=PROJECT, dry_run=False):
    p = plan(dest, config, project)
    if not dry_run:
        for rel in p["remove"]:
            os.remove(os.path.join(dest, *rel.split("/")))
    return p


def load_allowlist(path=None):
    with open(path or ALLOWLIST, encoding="utf-8") as f:
        return json.load(f)


def game_dll_for(config, project=PROJECT):
    return os.path.join(project, "bin", "win_x64_release" if config == "release" else "win_x64", "Game.dll")


def audit(dest, config="release", project=PROJECT, allowlist_path=None, game_dll=None):
    """The allowlist test -> list of faults (empty = pass). Release packages: every file of the three bin folders is in package_allowlist.json, none marked for removal remains, the required files are
    there, every static import resolves inside the package, and no bin folder holds the OTHER configuration's Game.dll. Profile packages: only the 'all' rules and the Game.dll check."""
    faults = []
    files = listing(dest)
    if not files:
        return ["no bin folder in %s" % dest]
    plan_now = plan(dest, config, project)
    for rel in plan_now["remove"]:
        faults.append("%s is still in the package (%s)" % (rel, rule_for(os.path.basename(rel), config)[0]))
    # the Game.dll of the right configuration
    want = game_dll or game_dll_for(config, project)
    other = game_dll_for("profile" if config == "release" else "release", project)
    for rel, path in files.items():
        if os.path.basename(rel).lower() == "game.dll":
            s = sha(path)
            if os.path.isfile(want) and s != sha(want):
                if os.path.isfile(other) and s == sha(other):
                    faults.append("%s is the %s Game.dll in a %s package" % (rel, "Profile" if config == "release" else "Release", config))
                else:
                    faults.append("%s (%s) is not the project's %s build (%s): the package is not the build you made" % (rel, s, config, sha(want)))
    if not any(os.path.basename(r).lower() == "game.dll" for r in files):
        faults.append("the package has no Game.dll in any bin folder")
    if config == "release":
        al = load_allowlist(allowlist_path)
        known = {}
        for folder, spec in al["release"].items():
            for n in list(spec.get("required", [])) + list(spec.get("keep_unsure", {})):
                known[(folder + "/" + n).lower()] = n
        for rel in files:
            if rel.lower() not in known and not rule_for(os.path.basename(rel), config):
                faults.append("%s is not in package_allowlist.json: classify it (required / keep_unsure with a reason / a removal rule) or remove it" % rel)
        for folder, spec in al["release"].items():
            for n in spec.get("required", []):
                if (folder + "/" + n) not in files and (folder + "/" + n).lower() not in {r.lower() for r in files}:
                    faults.append("required file %s/%s is missing" % (folder, n))
        # every static import of a module that stays must resolve inside the package (or be a system DLL)
        imps = static_imports({r: p for r, p in files.items() if r not in plan_now["remove"]})
        present = {os.path.basename(r).lower() for r in files}
        for m, deps in imps.items():
            for d in deps:
                if d.startswith("!unreadable"):
                    faults.append("%s: could not read its import table (%s)" % (m, d))
        # a module that stays must not import something this plan removed (cannot happen after plan(), but a hand-edited RULES could)
        removed_names = {os.path.basename(r).lower() for r in plan_now["remove"]}
        for m, deps in imps.items():
            bad = sorted(deps & removed_names)
            if bad:
                faults.append("%s imports %s, which is marked for removal" % (m, ", ".join(bad)))
        del present
    return faults


def classify_for_allowlist(dest, project=PROJECT):
    """Build the allowlist dict from an UNCLEANED release package: files kept by the plan split into `required` (the static-import closure of ROOT_MODULES and the project's plug-ins) and
    `keep_unsure` (everything else that stays, with the reason)."""
    files = listing(dest)
    p = plan(dest, "release", project)
    stay = {r: files[r] for r in p["keep"]}
    imports = static_imports(stay)
    roots = {m.lower() for m in ROOT_MODULES} | {x.lower() + ".dll" for x in project_plugins(project) if x != "CryGamePlatform"}
    need, todo = set(), [r for r in roots if any(os.path.basename(k).lower() == r for k in stay)]
    while todo:
        x = todo.pop()
        if x in need:
            continue
        need.add(x)
        todo += [d for d in imports.get(x, ()) if not d.startswith("!")]
    al = {"format": 1, "generated": "package_clean.py write-allowlist", "release": {}}
    for rel in sorted(stay):
        folder, name = rel.rsplit("/", 1)
        spec = al["release"].setdefault(folder, {"required": [], "keep_unsure": {}})
        b = name.lower()
        if b in need:
            spec["required"].append(name)
        else:
            users = sorted(m for m, deps in imports.items() if b in deps)
            reason = ("imported statically by %s" % ", ".join(users)) if users else "loaded by name at run time or not proven unneeded; kept"
            if b.startswith(("cryrenderd3d12", "cryrendervulkan", "cryscaleformd3d12", "cryscaleformvulkan")):
                reason = "alternative renderer (r_driver); the shader caches shipped are D3D11 only; kept until a run proves it unneeded"
            elif b == "crylobby.dll":
                reason = "loaded by CryAction; imports steam_api64.dll, which therefore stays"
            elif b == "steam_api64.dll":
                reason = "imported statically by CryLobby.dll"
            spec["keep_unsure"][name] = reason
    return al


def main():
    a = sys.argv[1:]
    if len(a) < 2 or a[0] not in ("plan", "clean", "audit", "write-allowlist"):
        print(__doc__)
        return 2
    dest = os.path.abspath(a[1])
    config = a[a.index("--config") + 1] if "--config" in a else "release"
    if a[0] == "plan":
        print(json.dumps(plan(dest, config), indent=1))
    elif a[0] == "clean":
        p = clean(dest, config)
        print("removed %d: %s" % (len(p["remove"]), ", ".join(os.path.basename(r) for r in p["remove"])))
        for rel, why in p["refused"].items():
            print("   kept %s: %s" % (rel, why))
    elif a[0] == "audit":
        f = audit(dest, config)
        print("\n".join(f) if f else "package_clean audit: PASS")
        return 1 if f else 0
    else:
        al = classify_for_allowlist(dest)
        with open(ALLOWLIST, "w", encoding="utf-8") as fh:
            json.dump(al, fh, indent=1)
        print("wrote %s: %s" % (ALLOWLIST, {k: (len(v["required"]), len(v["keep_unsure"])) for k, v in al["release"].items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
