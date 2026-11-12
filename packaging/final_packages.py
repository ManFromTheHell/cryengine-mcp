#!/usr/bin/env python3
"""Package the game and run the tests that the changes since the last green run call for.

    final_packages.py                 the stale groups only (what changed since each group last passed); nothing changed = nothing runs
    final_packages.py --all           every group: the release gate (~30 min). The only run that can write RELEASE_OK.json
    final_packages.py --groups a,b    named groups: smoke shaders menus audio items nights world perf undercroft
    final_packages.py --only t1,t2    named tests (their group is stamped only if it was run whole)
    final_packages.py --dry-run       print what would run and why, change nothing
    options: --profile (also package Profile and run its shader tour), --no-package, --configs release|profile,release,
             --package-only (package, install the shader paks, run no test), --build (compile Game.dll for each configuration first, build_game.py),
             --skip-check name=reason (a preflight check overridden on purpose; recorded in the package and it blocks RELEASE_OK.json),
             --accept-count-change / --kill-unresponsive (safe_close's overrides, see safe_close.py), --keep-good N (versioned packages kept)

1. Closes Sandbox through safe_close.close_editor: a snapshot, a verified save, bridge.window.close (a close-time prompt is answered by name); it REFUSES a dirty level (scratch objects, an
   object count the latest snapshot does not know) and never kills an editor whose state is unknown. The "closed on purpose" marker keeps the watchdog from reopening it until this run ends.
2. preflight.run(config, 'pre'): malformed XML, unshippable audio, pre-split textures outside the paks, level_check for EVERY level, scratch objects saved in a level, Game.dll older than its
   sources. A check that could not run is a failure.
3. Crytek's packager (package_project.py) for win_x64:Release (Profile with --profile) into ../Ashvale_staging_<config>: the previous package is untouched until the new one is finished.
4. Installs the three paks from the shader stage's Engine folder (<workspace>/ShaderPak/Engine by default; shader_cache_refresh.py) over the package's engine/shadercache.pak, shadercachestartup.pak, shadersbin.pak (manual: "copied into the
   Engine folder in the final build"), cleans both bin folders through package_clean (unit-test exes, Game_Server.exe, VR/Steam/Discord/Wwise/Fmod/Adx2 DLLs the dependency walk proves unneeded),
   runs preflight 'post' (resource compiler log, every texture packaged, the allowlist test, the Game.dll of the right configuration).
5. Finishes it: ashvale_version.txt (date, Game.dll sha, shader pak shas, group digests, overrides) and the package hash, the folder renamed ../Ashvale_<yyyymmdd>_<hash8>, symbols archived beside it
   (Game.dll, the matching Game.pdb, modules.json), ../Ashvale_final_release (a junction) pointed at it, the oldest versioned packages beyond the last N good ones pruned.
6. Runs the selected tests, prints each one's RESULT lines and the time each took. The complete output of every test and a run.json (test, group, duration, verdict, exit code, Game.dll sha, package
   hash) go to ci_runs/final_<time>/; final_packages.log is the latest run's summary. A group's digest (size and time of every file that can change what its tests see) is stored in
   suite_stamp.json when all its tests pass.
7. After `--all` passes on the package, with the project unchanged during the run, the packaged files unchanged and no preflight override: RELEASE_OK.json in the package.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import glob
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
# Environment overrides (defaults unchanged): CRYENGINE_PACKAGE_DIR (where packages are written, same variable as release_manifest.py),
# CRYENGINE_SHADER_PAK_DIR (the shader paks), CRYENGINE_DEPLOY_LOCK (the watchdog hold-off file), CRYENGINE_RC_LOG (the resource compiler's log).
ROOT = os.environ.get("CRYENGINE_PACKAGE_DIR") or os.path.dirname(PROJECT)
PY = sys.executable
# the shader paks: the Engine folder of the stage shader_cache_refresh.py fills (CRYENGINE_SHADER_STAGE, default <workspace>/ShaderPak)
PAKS = os.environ.get("CRYENGINE_SHADER_PAK_DIR") or os.path.join(os.environ.get("CRYENGINE_SHADER_STAGE") or _paths.workspace("ShaderPak"), "Engine")
LOCK = os.environ.get("CRYENGINE_DEPLOY_LOCK") or _paths.workspace("claude_deploy_in_progress")
BUILDS = {"profile": os.path.join(ROOT, "Ashvale_final_profile"), "release": os.path.join(ROOT, "Ashvale_final_release")}
LOG = os.path.join(HERE, "final_packages.log")
RUNS = os.path.join(HERE, "ci_runs")
RC_LOG = os.environ.get("CRYENGINE_RC_LOG") or os.path.expandvars("%LOCALAPPDATA%/Programs/CRYENGINE Launcher/Engines/crytek/cryengine-57-lts/5.7.1/Tools/rc/rc_log.log")
RC_TEMP = os.path.join(PROJECT, "rc_out_PC_pure")          # RC's output for a game build; the stock cleanup leaves it in place
stop = threading.Event()
RUN_LOG = {"path": None}                                    # the full log of THIS run (ci_runs/final_<time>/run.log), set by main
CLOSE_OPTS = {}                                             # safe_close overrides from the command line

sys.path.insert(0, HERE)


def out(msg):
    print(msg, flush=True)
    for path in (LOG, RUN_LOG["path"]):
        if path:
            with open(path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")


def keep_lock():
    while not stop.is_set():
        os.makedirs(os.path.dirname(LOCK), exist_ok=True)
        with open(LOCK, "w") as f:
            f.write("final_packages\n")
        stop.wait(10)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()[:16]


def bridge(cmd, timeout=20):
    with socket.create_connection(("127.0.0.1", 37778), timeout) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps({"id": 1, "cmd": cmd}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))


def sandbox_running():
    return "Sandbox.exe" in subprocess.run(["tasklist", "/FI", "IMAGENAME eq Sandbox.exe"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout


def close_sandbox(**kw):
    """Save and close the editor through safe_close (the one place that decides). Raises safe_close.CloseRefused when it refuses: a dirty level, an unknown object count, an editor that
    does not answer. Other scripts call this by name (night_test, hills_views, lighting_probe, ...), so the name and the no-argument call stay."""
    import safe_close
    opts = dict(CLOSE_OPTS)
    opts.update(kw)
    opts.setdefault("reason", os.path.basename(sys.argv[0]) or "final_packages")
    res = safe_close.close_editor(out=out, **opts)
    if not res["ok"]:
        raise safe_close.CloseRefused(res)
    return res


# ----------------------------------------------------------------------------------- checks: kept under their old names, the code is in preflight.py
def stale_sources(config):
    import preflight
    return preflight.stale_sources(config)


def unpackaged_textures(dest):
    import preflight
    return preflight.unpackaged_textures(dest)


def malformed_assets():
    import preflight
    return preflight.malformed_assets()


def unshippable_audio():
    import preflight
    return preflight.unshippable_audio()


def rc_failures(since):
    import preflight
    return preflight.rc_failures(since, RC_LOG)


def install_shader_paks(dest):
    """the three paks from PAKS (the shader stage's Engine folder) over the package's engine/ ones -> {file: sha}"""
    shas = {}
    for pak, name in (("ShaderCache.pak", "shadercache.pak"), ("ShaderCacheStartup.pak", "shadercachestartup.pak"),
                      ("ShadersBin.pak", "shadersbin.pak")):
        target = next((p for p in glob.glob(os.path.join(dest, "engine", "*.pak")) if os.path.basename(p).lower() == name), None)
        if target is None:
            target = os.path.join(dest, "engine", name)
        shutil.copyfile(os.path.join(PAKS, pak), target)
        shas[name] = sha(target)
        out("   installed %-24s %s" % (os.path.basename(target), shas[name]))
    return shas


def package(config, dest, skips=None, run_info=None):
    """Pre-package checks, Crytek's packager into `dest`, the shader paks, the bin cleaning, the post-package checks -> a dict about the package. Exits the process (1) on any failure.
    `dest` is wiped first: callers that must not lose the previous package give it a staging folder (package_versioned does)."""
    import package_clean
    import preflight
    skips = dict(skips or {})
    rep = preflight.run(config, "pre", skips=skips)
    out(preflight.format_report(rep))
    if run_info is not None:
        run_info.setdefault("preflight", []).append(rep)
    if not rep["ok"]:
        out("preflight blocked the package. A check that cannot run counts as a failure; an override is --skip-check name=reason (recorded, and it blocks RELEASE_OK.json).")
        sys.exit(1)
    shutil.rmtree(RC_TEMP, ignore_errors=True)                # never let a stale converted file ride along
    rc_since = time.time()
    shutil.rmtree(dest, ignore_errors=True)
    t0 = time.time()
    r = subprocess.run([PY, _paths.script("package_project.py"), __import__("project_root").CRYPROJECT,
                        "--config", "win_x64:" + config.capitalize(), "--out", dest], capture_output=True, text=True, timeout=3600, stdin=subprocess.DEVNULL)
    bindir = os.path.join(dest, "bin", "win_x64")
    ok = os.path.isfile(os.path.join(bindir, "Game.dll"))
    out("packaged %s in %.0f s: %s" % (config, time.time() - t0, ok))
    if not ok:
        out((r.stdout + r.stderr)[-1500:])
        sys.exit(1)
    want = preflight.game_dll(config)
    out("   Game.dll %s, project %s build %s" % (sha(os.path.join(bindir, "Game.dll")), config, sha(want)))
    shas = install_shader_paks(dest)
    plan = package_clean.clean(dest, config)
    out("   package_clean removed %d file(s): %s" % (len(plan["remove"]), ", ".join(os.path.basename(p) for p in plan["remove"])))
    for rel, why in plan["refused"].items():
        out("   kept %s: %s" % (rel, why))
    post = preflight.run(config, "post", dest=dest, skips=skips, rc_since=rc_since, rc_log=RC_LOG)
    out(preflight.format_report(post))
    if run_info is not None:
        run_info["preflight"].append(post)
    if not post["ok"]:
        if any(c["name"] == "textures_packaged" and c["status"] == "fail" for c in post["checks"]):
            out("   add them to Assets/GameSDKTextures.pak (engine Tools/7za.exe a -tzip -mx0, paths relative to Assets): `python pak_textures.py %s` does it; then package again" % config)
        sys.exit(1)
    ai = os.path.join(dest, "bin", "win_x64_release" if config == "release" else "win_x64", "CryAISystem.dll")      # a Release package links the engine modules into the launcher
    out("   CryAISystem.dll %s" % (sha(ai) if os.path.isfile(ai) else "not a separate module (Release)"))
    applied = {}
    for c in rep["checks"] + post["checks"]:
        if c["status"] == "skipped":
            applied[c["name"]] = skips.get(c["name"]) or skips.get(c["name"].split(":")[0]) or "?"
    return {"config": config, "game_dll_sha": sha(os.path.join(bindir, "Game.dll")), "shader_paks": shas, "overrides": applied}


def package_versioned(config, digests, skips=None, run_info=None, keep_good=None):
    """package() into the staging folder, then version it: hash, ashvale_version.txt, Ashvale_<yyyymmdd>_<hash8>, the symbols archive, the junction under the old name, the prune -> the package folder"""
    import package_symbols
    import release_manifest as RM
    staging = RM.staging_dir(config)
    info = package(config, staging, skips, run_info)
    vinfo = RM.build_info(staging, config, info["game_dll_sha"], info["shader_paks"], digests, info["overrides"])
    path, full = RM.finalize(staging, config, vinfo)
    out("   version: %s (package hash %s)" % (os.path.basename(path), full[:16]))
    try:
        sym = package_symbols.archive(path, config)
        out("   symbols: %s, %d PDB(s) archived%s" % (os.path.basename(sym["out_dir"]), len(sym["pdbs_archived"]), ("; PROBLEMS: " + "; ".join(sym["problems"])) if sym["problems"] else ""))
    except Exception as exc:                                  # noqa: BLE001 - a symbols failure is reported loudly but the package stands
        out("   symbols: NOT ARCHIVED (%s: %s)" % (type(exc).__name__, exc))
    out("   promoted: " + RM.promote(path, config))
    pr = RM.prune(config, keep_good or RM.KEEP_GOOD, really=True)
    if pr["delete"]:
        out("   pruned old packages: " + ", ".join(pr["delete"]))
    if run_info is not None:
        run_info.setdefault("packages", {})[config] = {"path": path, "hash": full, "game_dll_sha": info["game_dll_sha"], "overrides": info["overrides"]}
    return path


# name -> (command, lines to echo). The group each belongs to is in GROUPS.
TESTS = {
    "shader_release": (["shader_tour.py", "{release}", "--label", "final_release"], ("RESULT", "misses by")),
    "shader_profile": (["shader_tour.py", "{profile}", "--label", "final_profile"], ("RESULT", "misses by")),
    "pixels": (["shader_pixel_test.py", "{release}"], ("RESULT", "PASS", "FAIL")),
    "playthrough": (["release_playthrough.py", "{release}"], ("RESULT", "FAIL")),
    "undercroft": (["undercroft_playthrough.py", "{release}"], ("RESULT", "CHECK|FAIL")),
    "ui": (["ui_screens.py", "{release}", os.path.join(ROOT, "Ashvale_final_ui")], ("RESULT", "saved", "FAIL")),
    "menus": (["ui_menu_test.py", "{release}", os.path.join(ROOT, "Ashvale_final_menu_frames")], ("RESULT", "CHECK|FAIL")),
    "loading": (["loading_screen_test.py", "{release}", os.path.join(ROOT, "Ashvale_final_loading_frames")], ("RESULT", "CHECK|FAIL")),
    "soundscape": (["soundscape_test.py", "{release}"], ("RESULT", "CHECK|FAIL")),
    "pickups": (["pickup_test.py", "{release}"], ("RESULT", "CHECK|FAIL")),
    "gameplay": (["gameplay_test.py", "{release}"], ("RESULT", "CHECK|FAIL")),
    "nights": (["night_test.py", "{release}"], ("RESULT", "CHECK|FAIL")),
    "features": (["package_features_test.py", "{release}"], ("RESULT", "FAIL")),
    "door": (["door_crossing_test.py", "--build", "{release}"], ("RESULT", "CHECK|FAIL")),
    "perf": (["perf_tour.py", "{release}", "--label", "final_release"], ("RESULT", "CHECK|FAIL")),
}

# Which tests run when. A group is stale when a file matching its patterns (relative to the project, lower case, fnmatch: '*'
# crosses folders) changed since the last time ALL of its tests passed (suite_stamp.json), or when one of its own test scripts
# changed. "smoke" is the whole game played once; the others each guard one area. `--all` runs every group (the release gate).
# Grouping keeps a run short: running every test each time is slow and several tests cover the same ground.
# "undercroft": chapter II (the hatch, the dungeon, the vault, the way back) is gated by undercroft_playthrough.py.
COMMON_ART = ["assets/materials/*", "assets/objects/*", "assets/*.dds", "assets/*.tif", "assets/gamesdktextures.pak"]
GROUPS = {
    "smoke": (["playthrough"], ["code/*", "assets/*", "game.cryproject"]),
    "shaders": (["shader_release", "pixels"],
                COMMON_ART + ["assets/gameshaders/*", "assets/libs/particles/*", "assets/libs/environmentpresets/*", "assets/libs/ui/*",
                              "game.cryproject", "code/components/worldtools.*", "code/components/nightlamp.*", "code/components/interactable.*",
                              "code/components/particleprop.h", "code/components/cinematics.*"]),
    "menus": (["menus", "loading", "ui"],
              ["code/components/ashvaleui.*", "code/components/ashvalemenus.cpp", "code/components/ashvalesettings.*", "code/components/ashvalegame.*",
               "code/components/player.*", "assets/libs/ui/*", "assets/localization/*"]),
    "audio": (["soundscape"],
              ["code/components/soundscape.*", "code/components/ashvalesettings.*", "code/components/ashvalegame.*", "code/components/interactable.*",
               "code/components/worldtools.*", "code/components/daycycle.*", "assets/audio/*"]),
    "items": (["pickups", "gameplay"],
              ["code/components/ashvaleobjects.*", "code/components/player.*", "code/components/ashvalegame.*", "code/components/ashvalemenus.cpp",
               "code/components/npc*", "code/components/banditbrain.*", "assets/libs/ashvale/dialogue.xml", "assets/objects/ashvale/weapons/*",
               "assets/materials/*", "assets/levels/*", "code/components/leveltravel.*"]),
    "nights": (["nights"],
               ["code/components/nightlamp.*", "code/components/daycycle.*", "code/components/interactable.*", "code/components/worldtools.*",
                "code/components/villagerbrain.*", "code/components/player.*", "code/components/particleprop.h", "assets/libs/particles/*",
                "assets/libs/environmentpresets/*", "assets/levels/*", "assets/materials/*"]),
    "world": (["features", "door"],
              ["code/components/npc*", "code/components/villager*", "code/components/banditbrain.*", "code/components/jumplink.*",
               "code/components/farmgate.*", "code/components/villageobjects.*", "code/components/cinematics.*", "code/components/leveltravel.*", "assets/levels/*",
               "assets/libs/ashvale/*", "assets/animations/*"]),
    "perf": (["perf"], COMMON_ART + ["assets/levels/*", "assets/libs/particles/*", "assets/libs/environmentpresets/*", "game.cryproject"]),
    "undercroft": (["undercroft"],
                   COMMON_ART + ["assets/levels/*", "assets/libs/ashvale/*", "assets/libs/environmentpresets/*", "assets/audio/*", "assets/animations/*", "assets/surfacetypes.xml",
                                 "code/components/leveltravel.*", "code/components/ashvalegame.*", "code/components/ashvaleui.*", "code/components/ashvalemenus.cpp",
                                 "code/components/npc*", "code/components/banditbrain.*", "code/components/villager*", "code/components/player.*", "code/components/ashvaleobjects.*",
                                 "code/components/interactable.*", "code/components/soundscape.*", "code/components/nightlamp.*", "code/components/worldtools.*",
                                 "mcp_server/uc_stand.json", "game.cryproject"]),
}
STAMP = os.path.join(HERE, "suite_stamp.json")
IGNORED = (".thmb.png", ".cryasset", ".bak", ".tmp")
SHARED_SCRIPTS = ["mcp_server/window_capture.py", "mcp_server/night_test.py"]


def project_files():
    """(relative lower-case path, size, mtime_ns) of the files that can change what a test sees."""
    files = []
    for top in ("Code", "Assets", "mcp_server"):
        for d, _, names in os.walk(os.path.join(PROJECT, top)):
            if "__pycache__" in d:
                continue
            for n in names:
                if n.lower().endswith(IGNORED):
                    continue
                path = os.path.join(d, n)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                files.append((os.path.relpath(path, PROJECT).replace("\\", "/").lower(), st.st_size, st.st_mtime_ns))
    cryproject = os.path.join(PROJECT, "Game.cryproject")
    if os.path.isfile(cryproject):
        st = os.stat(cryproject)
        files.append(("game.cryproject", st.st_size, st.st_mtime_ns))
    return files


def group_digests(files):
    import fnmatch
    digests = {}
    for name, (tests, patterns) in GROUPS.items():
        scripts = ["mcp_server/" + TESTS[t][0][0].lower() for t in tests if t in TESTS]
        pats = patterns + scripts + SHARED_SCRIPTS
        picked = sorted(f for f in files if any(fnmatch.fnmatch(f[0], pat) for pat in pats))
        digests[name] = hashlib.sha1(json.dumps(picked).encode()).hexdigest()[:16]
    return digests


def load_stamp():
    try:
        return json.load(open(STAMP))
    except (OSError, ValueError):
        return {}


def select(argv, digests):
    """The groups to run: --all, --groups a,b, --only test (its group), else the stale ones."""
    stamp = load_stamp()
    if "--all" in argv:
        return list(GROUPS)
    if "--groups" in argv:
        return argv[argv.index("--groups") + 1].split(",")
    if "--only" in argv:
        wanted = argv[argv.index("--only") + 1].split(",")
        return [g for g, (tests, _) in GROUPS.items() if any(t in wanted for t in tests)]
    return [g for g in GROUPS if stamp.get(g) != digests[g]]


def run_tests(groups, digests, with_profile, only=None, run_dir=None, run_info=None):
    """Run the groups' tests. The COMPLETE output of each goes to <run_dir>/<test>.txt (nothing is cut), every test's record to run_info['tests'] -> the summary list [(name, rc, failed, seconds)]"""
    summary = []
    stamp = load_stamp()
    t_all = time.time()
    for group in groups:
        tests = list(GROUPS[group][0])
        if group == "shaders" and with_profile:
            tests.insert(1, "shader_profile")
        ok_group = True
        ran_all = True
        for name in tests:
            if only and name not in only:
                ran_all = False
                continue
            args, keep = TESTS[name]
            cmd = [PY, "-u", _paths.script(args[0])] + [a.format(**BUILDS) for a in args[1:]]
            t0 = time.time()
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, cwd=HERE, stdin=subprocess.DEVNULL)
            secs = time.time() - t0
            if run_dir:
                with open(os.path.join(run_dir, name + ".txt"), "w", encoding="utf-8", errors="replace") as f:
                    f.write("# %s\n# exit %d, %.1f s\n\n--- stdout ---\n%s\n--- stderr ---\n%s\n" % (" ".join(cmd[2:]), r.returncode, secs, r.stdout, r.stderr))
            with open(LOG, "a", encoding="utf-8") as f:
                f.write("\n######## %s\n%s\n%s\n" % (name, r.stdout, r.stderr[-3000:]))
            lines = [l for l in r.stdout.splitlines() if l.lstrip().startswith(keep) or any(k in l for k in keep[:1])]
            out("== %s: exit %d, %.0f s" % (name, r.returncode, secs))
            for l in lines[-12:]:
                out("   " + l[:220])
            failed = sum(1 for l in r.stdout.splitlines() if l.startswith("CHECK|FAIL"))
            # "RESULT name: 37 of 39" style lines: fewer passed than checked is a failure even with exit code 0
            for m in re.finditer(r"RESULT [^\n]*?: (\d+) of (\d+)", r.stdout):
                failed = max(failed, int(m.group(2)) - int(m.group(1)))
            summary.append((name, r.returncode, failed, secs))
            if run_info is not None:
                run_info.setdefault("tests", []).append({"test": name, "group": group, "duration_s": round(secs, 1), "returncode": r.returncode, "failed_checks": failed,
                                                         "verdict": "pass" if not (r.returncode or failed) else "fail", "result_lines": lines[-4:], "output_file": name + ".txt" if run_dir else None})
            if r.returncode or failed:
                ok_group = False
        if ok_group and ran_all:
            stamp[group] = digests[group]           # the digest from BEFORE the run: an edit made during it counts as a change
        elif not ok_group:
            stamp.pop(group, None)
    json.dump(stamp, open(STAMP, "w"), indent=1)
    out("\nSUMMARY " + ", ".join("%s=%s" % (n, ("rc%d" % rc) if rc else ("FAILED %d" % f if f else "ok")) for n, rc, f, _ in summary))
    out("TIME %.0f s: %s" % (time.time() - t_all, ", ".join("%s %.0f" % (n, t) for n, _, _, t in summary)))
    return summary


def parse_skips(argv):
    import preflight
    return preflight.parse_skips([argv[i + 1] for i, a in enumerate(argv) if a == "--skip-check" and i + 1 < len(argv)])


def main():
    if any(a in ("-h", "--help", "/?") for a in sys.argv[1:]):
        print(__doc__)
        return
    argv = sys.argv[1:]
    digests = group_digests(project_files())
    groups = select(argv, digests)
    if "--dry-run" in argv:
        stamp = load_stamp()
        for g in GROUPS:
            why = "stale" if stamp.get(g) != digests[g] else "unchanged since its last green run"
            print("%-10s %-5s %s   tests: %s" % (g, "RUN" if g in groups else "skip", why, ", ".join(GROUPS[g][0])))
        try:
            import preflight
            skips = parse_skips(argv)
            for cfg in (["release"] if "--configs" not in argv else argv[argv.index("--configs") + 1].split(",")):
                print(preflight.format_report(preflight.run(cfg, "pre", skips=skips)))
            import release_manifest as RM
            print("versioned packages: " + (", ".join("%s%s" % (p["name"], " GOOD" if p["good"] else "") for p in RM.versioned_packages()) or "none yet"))
        except Exception as exc:                             # noqa: BLE001 - the dry run reports, it never fails on the report
            print("(preflight report unavailable: %s)" % exc)
        return
    package_only = "--package-only" in argv                  # the packager and the shader-cache install, no tests (the MCP `package` job)
    try:
        skips = parse_skips(argv)
    except ValueError as exc:
        print("refused: %s" % exc)
        sys.exit(2)
    for i, a in enumerate(argv):
        if a == "--accept-count-change":
            CLOSE_OPTS["accept_count_change"] = True
        elif a == "--kill-unresponsive":
            CLOSE_OPTS["kill_unresponsive"] = True
    CLOSE_OPTS["hold_minutes"] = 360
    started = time.time()
    run_id = "final_" + time.strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(RUNS, run_id)
    os.makedirs(run_dir, exist_ok=True)
    RUN_LOG["path"] = os.path.join(run_dir, "run.log")
    open(LOG, "w").close()
    run_info = {"run": run_id, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "argv": argv, "skips": skips, "groups": groups}
    if not groups and not package_only:
        out("Nothing changed since every group's last green run: nothing to run. (--all runs everything.)")
        RUN_LOG["path"] = None
        shutil.rmtree(run_dir, ignore_errors=True)               # nothing ran: no record
        return
    out("groups: %s" % ", ".join(groups))
    only = argv[argv.index("--only") + 1].split(",") if "--only" in argv else None
    with_profile = "--profile" in argv
    keep_good = int(argv[argv.index("--keep-good") + 1]) if "--keep-good" in argv else None
    threading.Thread(target=keep_lock, daemon=True).start()
    # NO display keep-awake request (no SetThreadExecutionState call): the display's idle timer is the machine owner's setting.
    summary, exit_code = [], 0
    try:
        try:
            close_sandbox()
        except Exception as exc:                             # noqa: BLE001 - CloseRefused carries the reasons, already printed by safe_close
            out("the editor was NOT closed, nothing was packaged or tested: %s" % exc)
            run_info["verdict"] = "refused: editor not closed"
            exit_code = 1
            raise SystemExit(exit_code)
        if "--no-package" not in argv:
            configs = argv[argv.index("--configs") + 1].split(",") if "--configs" in argv else (["profile", "release"] if with_profile else ["release"])
            for config in configs:
                if "--build" in argv:
                    import build_game
                    b = build_game.build(config)
                    out("build %s: %s, %d error(s), %.0f s" % (config, b["verdict"], b["n_errors"], b["seconds"]))
                    for e in b["errors"]:
                        out("   " + e)
                    run_info.setdefault("builds", {})[config] = {k: b[k] for k in ("verdict", "n_errors", "n_warnings", "seconds")}
                    if b["returncode"] != 0:
                        run_info["verdict"] = "build failed"
                        exit_code = 1
                        raise SystemExit(exit_code)
                package_versioned(config, digests, skips, run_info, keep_good)
        if package_only:
            out("packaged only (--package-only): no tests run")
        else:
            summary = run_tests(groups, digests, with_profile, only, run_dir, run_info)
            if "--all" in argv:
                import release_manifest as RM
                target = os.path.realpath(BUILDS["release"])
                after = group_digests(project_files())
                try:
                    written, why = RM.write_release_ok(target, "release", list(GROUPS), groups, summary, digests, after, (run_info.get("packages", {}).get("release") or {}).get("overrides") or {}, started)
                except Exception as exc:                     # noqa: BLE001
                    written, why = False, ["could not evaluate: %s" % exc]
                out("RELEASE_OK.json: %s" % ("WRITTEN for " + os.path.basename(target) if written else "NOT written: " + "; ".join(why)))
                run_info["release_ok"] = written
        run_info["verdict"] = "fail" if any(rc or f for _, rc, f, _ in summary) else "pass"
    finally:
        run_info["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
        run_info["seconds"] = round(time.time() - started)
        try:
            for cfg, p in run_info.get("packages", {}).items():
                run_info["packages"][cfg]["game_dll_sha_now"] = p.get("game_dll_sha")
            with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as f:
                json.dump(run_info, f, indent=1, default=str)
        except OSError:
            pass
        stop.set()
        try:
            os.remove(LOCK)
        except OSError:
            pass
        try:
            import safe_close
            safe_close.release()                              # the editor may come back: the watchdog reopens it (and the level that was open)
        except Exception:                                     # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
