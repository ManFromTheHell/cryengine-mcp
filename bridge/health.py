#!/usr/bin/env python3
"""One report of whether the working state can be trusted: the editor and its bridge, the packaged builds, the shader cache, the watchdog, stray locks,
running jobs, disk. Nothing here changes anything.

    health.py [--json]

Why: a picture of the PACKAGED game shows the package, not the project. After an asset or code change, a `shoot_game` / test run before a re-package reports
yesterday's game, and nothing said so. `package_freshness()` is the part `shoot_game` calls; `check()` is the whole report.

What already existed and is reused, not rewritten: final_packages.stale_sources (code newer than the project's Game.dll), final_packages.sha, jobs.sandbox_running,
asset_lint L011 (shader cache older than a material or texture), bridge_watchdog's scheduled task, the bridge's own `bridge.terrain.info`.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import shutil
import sys
import time

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ROOT = os.path.dirname(PROJECT)
sys.path.insert(0, HERE)
import asset_lint as L          # noqa: E402
import jobs as J                # noqa: E402
import final_packages as F      # noqa: E402

LOCK = F.LOCK
WATCHDOG_TASK = "ClaudeBridgeWatchdog"
SKIP = F.IGNORED + (".lyr.bak", ".level.bak")


def _age(seconds):
    seconds = int(seconds)
    return "%dd %dh" % (seconds // 86400, seconds % 86400 // 3600) if seconds >= 86400 else "%dh %dm" % (seconds // 3600, seconds % 3600 // 60) if seconds >= 3600 else "%dm" % (seconds // 60)


def _built(build_dir):
    """When the package was made: the older of the two files the packager always writes (config.pak, Assets/Data.pak)."""
    stamps = [os.path.getmtime(p) for p in (os.path.join(build_dir, "config.pak"), os.path.join(build_dir, "Assets", "Data.pak")) if os.path.isfile(p)]
    return min(stamps) if stamps else None


def package_freshness(build="release"):
    """{exists, built, stale, reasons[]}: is the packaged build older than the project it was made from?
    Reasons: source code newer than the project's Game.dll (never rebuilt), the package's Game.dll not the project's, loose assets changed after the package."""
    build_dir = J.BUILDS.get(build)
    if not build_dir or not os.path.isdir(build_dir):
        return {"exists": False, "stale": True, "reasons": ["no package at %s: run the `package` job" % build_dir]}
    built = _built(build_dir)
    reasons = []
    config = "release" if build == "release" else "profile"
    unbuilt = F.stale_sources(config)
    if unbuilt:
        reasons.append("%d source file(s) are newer than the project's Game.dll (the code was not rebuilt): %s" % (len(unbuilt), ", ".join(unbuilt[:3])))
    project_dll = os.path.join(PROJECT, "bin", "win_x64_release" if config == "release" else "win_x64", "Game.dll")
    package_dll = os.path.join(build_dir, "bin", "win_x64", "Game.dll")
    if os.path.isfile(project_dll) and os.path.isfile(package_dll) and F.sha(project_dll) != F.sha(package_dll):
        reasons.append("the package's Game.dll is not the project's current %s build" % config)
    newer = []
    for d, _, names in os.walk(os.path.join(PROJECT, "Assets")):
        for n in names:
            if n.lower().endswith(SKIP):
                continue
            p = os.path.join(d, n)
            try:
                if built and os.path.getmtime(p) > built + 5:
                    newer.append((os.path.getmtime(p), os.path.relpath(p, os.path.join(PROJECT, "Assets")).replace("\\", "/")))
            except OSError:
                continue
    only_level = False
    if newer:
        newer.sort(reverse=True)
        # the editor SAVES the level when a job closes it (final_packages.close_sandbox does it on purpose), so the level files are newer than the package after almost
        # every job without their content having changed: say so, and let the caller treat it as a soft warning
        only_level = all(f.startswith("levels/") for _, f in newer) and len(reasons) == 0
        reasons.append("%d asset file(s) changed after the package was made (newest: %s, %s earlier)%s" % (
            len(newer), newer[0][1], _age(time.time() - newer[0][0]),
            "; only level files: the editor re-saves the level whenever it is closed, so the content is usually identical and the package is probably still right" if only_level else ""))
    return {"exists": True, "built": time.strftime("%Y-%m-%d %H:%M", time.localtime(built)) if built else None, "age": _age(time.time() - built) if built else None,
            "stale": bool(reasons), "only_level_resaved": only_level, "reasons": reasons}


def _bridge_state():
    try:
        r = F.bridge("bridge.cvar_get r_DisplayInfo", 5)
        if not r.get("ok"):
            return "no-answer", str(r)[:120]
    except Exception as exc:                                    # noqa: BLE001 - any failure to talk is the finding
        return "unreachable", str(exc)[:120]
    try:
        info = json.loads(F.bridge("bridge.terrain.info", 10).get("output", "{}"))
        top = info.get("heightmap", {}).get("elevationMax", 0)
        return ("level" if top and top > 0 else "no-level"), "terrain max elevation %s" % top
    except Exception as exc:                                    # noqa: BLE001
        return "answers", "terrain query failed: %s" % str(exc)[:80]


def _watchdog():
    r = J.run(["powershell", "-NoProfile", "-Command",
               "$i = Get-ScheduledTaskInfo -TaskName %s -ErrorAction Stop; [pscustomobject]@{last=$i.LastRunTime.ToString('s'); result=$i.LastTaskResult; "
               "next=$i.NextRunTime.ToString('s')} | ConvertTo-Json -Compress" % WATCHDOG_TASK], capture_output=True, text=True, timeout=30)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def _watchdog_last_line():
    """The last thing the watchdog logged (what a result of 1 meant)."""
    path = os.path.join(os.path.dirname(LOCK), "claude_watchdog.log")
    try:
        lines = [l.strip() for l in open(path, encoding="utf-8", errors="replace").read().splitlines() if l.strip()]
        started = [l for l in lines if "starting it" in l or "strike" in l or "killed" in l]
        return (started or lines or [""])[-1][:140]
    except OSError:
        return ""


def check():
    items = []

    def add(status, name, detail, fix=""):
        items.append({"status": status, "name": name, "detail": detail, "fix": fix})

    # --- the editor and its bridge
    running = J.sandbox_running()
    state, detail = _bridge_state()
    if not running:
        add("info", "editor", "Sandbox.exe is not running (the watchdog reopens it within 5 minutes unless a lock is held)")
    elif state == "level":
        add("ok", "editor", "Sandbox.exe running, bridge answers, level loaded (%s)" % detail)
    elif state == "no-level":
        add("warn", "editor", "bridge answers but no level is loaded (terrain reads empty)", "load the level (editor_open.py or load_level)")
    else:
        add("fail", "editor", "Sandbox.exe is running but the bridge is %s (%s)" % (state, detail), "wait for a level load to finish; if it persists the watchdog restarts it after two strikes")
    # --- game processes and the watchdog's lock
    game = "GameLauncher.exe" in J.run(["tasklist", "/FI", "IMAGENAME eq GameLauncher.exe"], capture_output=True, text=True).stdout
    if game:
        add("info", "game", "GameLauncher.exe is running (a test or shoot job, or a leftover)")
    if os.path.isfile(LOCK):
        age = time.time() - os.path.getmtime(LOCK)
        running_jobs = [j for j in J.list_jobs(50) if j["state"] == "running"]
        if age > 1800 and not running_jobs and not game:
            add("warn", "watchdog lock", "%s is %s old and nothing is running: the watchdog will never reopen the editor" % (os.path.basename(LOCK), _age(age)), "delete the lock file")
        else:
            add("info", "watchdog lock", "held by a running job or game (%s old); the watchdog stands down" % _age(age))
    wd = _watchdog()
    if wd is None:
        add("info", "watchdog", "scheduled task %s not found or unreadable (optional: it only restarts a crashed editor)" % WATCHDOG_TASK, "bridge_watchdog.py --install")
    else:
        silent = time.time() - time.mktime(time.strptime(wd["last"], "%Y-%m-%dT%H:%M:%S")) > 900
        said = _watchdog_last_line()
        # exit codes, from bridge_watchdog.check(): 0 healthy or standing down for a lock; 1 it just (re)started the editor or logged a strike; the 2670xx values are
        # Task Scheduler's own 'still running' / 'not yet run'
        if silent:
            add("warn", "watchdog", "last ran %s (over 15 minutes ago): it is not firing" % wd["last"], "bridge_watchdog.py --install")
        elif wd["result"] in (0, 267009, 267011):
            add("ok", "watchdog", "last ran %s (result %s), next %s" % (wd["last"], wd["result"], wd["next"]))
        elif wd["result"] == 1:
            add("info", "watchdog", "last ran %s and (re)started the editor or logged a strike: %s" % (wd["last"], said or "see claude_watchdog.log"))
        else:
            add("warn", "watchdog", "last ran %s with result %s: %s" % (wd["last"], wd["result"], said or ""), "read %s; bridge_watchdog.py --install to repair the task" % os.path.join(os.path.dirname(LOCK), "claude_watchdog.log").replace("\\", "/"))
    # --- packages
    for build in ("release", "profile"):
        pf = package_freshness(build)
        if not pf["exists"]:
            add("info", "package %s" % build, pf["reasons"][0] + " (not built yet; optional until you package)", "start_job('package', {'config': '%s'})" % build)
        elif pf["stale"] and pf.get("only_level_resaved"):
            add("info", "package %s" % build, "made %s (%s ago); %s" % (pf["built"], pf["age"], pf["reasons"][0]))
        elif pf["stale"]:
            add("warn", "package %s" % build, "made %s (%s ago) and STALE: %s" % (pf["built"], pf["age"], "; ".join(pf["reasons"])),
                "start_job('package', {'config': '%s'}, allow_editor_close=true) before shoot_game or any test" % build)
        else:
            add("ok", "package %s" % build, "made %s (%s ago), up to date with the project's code and assets" % (pf["built"], pf["age"]))
    # --- the shader cache against the materials and textures (asset_lint L011)
    lint = L.lint(["shaders"], 5)
    cache = [f for f in lint["findings"] if f["code"] == "L011"]
    add("warn" if cache else "ok", "shader cache", cache[0]["message"] if cache else "not older than any material or texture", cache[0]["fix"] if cache else "")
    # --- jobs and disk
    jobs = J.list_jobs(30)
    running_jobs = [j for j in jobs if j["state"] == "running"]
    failed = [j for j in jobs if j["state"] in ("failed", "lost")][:1]
    add("info" if running_jobs else "ok", "jobs", "%d running%s" % (len(running_jobs), (": " + ", ".join("%s (%ss)" % (j["kind"], j["elapsed_s"]) for j in running_jobs)) if running_jobs else "")
        + ("; last failure: %s %s" % (failed[0]["kind"], failed[0]["id"]) if failed else ""))
    free = shutil.disk_usage(PROJECT).free / 1e9
    add("warn" if free < 20 else "ok", "disk", "%.0f GB free on the project's drive" % free, "builds, CI runs (ci_runs) and the shader pak are large" if free < 20 else "")
    # --- the MCP server itself: it keeps the code it started with, so an edited module is not in effect until the connector is restarted
    try:
        import server_support as SS
        rep = SS.stale_report()
        if rep is None:
            add("info", "mcp server", "not running inside the MCP server (this is a command-line report): no staleness to check")
        elif rep["stale"]:
            add("warn", "mcp server", "%s (started %s)" % (rep["restart_note"], rep["started"]), "restart the MCP connector to load: " + ", ".join(rep["stale"]))
        else:
            add("ok", "mcp server", "started %s; no loaded module has been edited since" % rep["started"])
    except Exception as exc:                                    # noqa: BLE001 - a report problem must not hide the rest
        add("info", "mcp server", "staleness check failed: %s" % str(exc)[:100])
    # --- the catalog dumps against the installed build
    try:
        import catalog_stamps as CS
        old = {n: s for n, s in CS.status().items() if s["state"] in ("stale", "unstamped-old", "missing")}
        if old:
            add("warn" if any(s["state"] == "stale" for s in old.values()) else "info", "catalogs", "; ".join("%s: %s" % (n, s["state"]) for n, s in old.items()) + " (the dumps may describe an older plugin/editor build)",
                "re-capture the catalog, then python catalogs/catalog_stamps.py --stamp <name> (details: server_info or python catalogs/catalog_stamps.py)")
        else:
            add("ok", "catalogs", "cvars, editor commands and editor API dumps match the installed build (or are newer than it)")
    except Exception as exc:                                    # noqa: BLE001
        add("info", "catalogs", "stamp check failed: %s" % str(exc)[:100])
    fails = [i for i in items if i["status"] == "fail"]
    warns = [i for i in items if i["status"] == "warn"]
    return {"ok": not fails, "failures": len(fails), "warnings": len(warns),
            "summary": "%d failure(s), %d warning(s)" % (len(fails), len(warns)), "items": items,
            "next": [i["fix"] for i in fails + warns if i["fix"]]}


def editor_diagnosis():
    """Why the editor is not answering: what the watchdog last did, whether Sandbox.exe exists, whether a lock holds the watchdog off, what is running."""
    lock_age = time.time() - os.path.getmtime(LOCK) if os.path.isfile(LOCK) else None
    return {"sandbox_running": J.sandbox_running(), "watchdog_last_action": _watchdog_last_line() or "(no log)",
            "watchdog_lock": ("held, %s old (a job or game is running: the watchdog stands down)" % _age(lock_age)) if lock_age is not None else "none",
            "jobs_running": [j["kind"] for j in J.list_jobs(30) if j["state"] == "running"]}


def wait_for_editor(timeout=240, poll=5, require_level=True, _state=None, _sleep=time.sleep, _clock=time.time):
    """Block until the editor's bridge answers (and, by default, the level is loaded), or the timeout passes. The watchdog reopens a closed editor within 5 minutes and the level
    takes ~15 s to load; the jobs that close the editor are why this is needed. Before this existed the loop was written by hand three times in one evening.
    Returns {ok, state, waited_s, polls, detail} and, when it gave up, the diagnosis."""
    if not 1 <= timeout <= 900 or not 1 <= poll <= 60:
        raise ValueError("timeout is 1 to 900 seconds and poll 1 to 60")
    state_fn = _state or _bridge_state
    t0 = _clock()
    polls = 0
    while True:
        state, detail = state_fn()
        polls += 1
        done = state == "level" or (not require_level and state in ("answers", "no-level", "level"))
        if done:
            return {"ok": True, "state": state, "waited_s": round(_clock() - t0, 1), "polls": polls, "detail": detail}
        if _clock() - t0 + poll > timeout:
            return {"ok": False, "state": state, "waited_s": round(_clock() - t0, 1), "polls": polls, "detail": detail, "diagnosis": editor_diagnosis(),
                    "next": "if the watchdog has not acted and nothing holds its lock, run bridge_watchdog.py by hand; a long level load can need more than %d s" % timeout}
        _sleep(poll)


def main():
    out = check()
    if "--json" in sys.argv:
        print(json.dumps(out, indent=1))
    else:
        print(out["summary"])
        for i in out["items"]:
            print("  %-5s %-14s %s%s" % (i["status"], i["name"], i["detail"], ("\n                       fix: " + i["fix"]) if i["fix"] else ""))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
