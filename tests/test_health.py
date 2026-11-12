#!/usr/bin/env python3
"""Self-test of health.py: package_freshness against a fixture project and package, one planted case for each reason it can give and a clean twin,
then the whole report on the real machine for its structure.

    test_health.py          # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import final_packages as F
import health as H
import jobs as J

FIX = os.path.join(HERE, "job_runs", "_health_test")
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-66s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:80]))


def put(path, data=b"x", age=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(data)
    t = time.time() - age
    os.utime(path, (t, t))


def build_fixture():
    shutil.rmtree(FIX, ignore_errors=True)
    proj, pkg = os.path.join(FIX, "Project"), os.path.join(FIX, "Build")
    # times, oldest first: assets and code (3 h ago), the project's Game.dll (2 h), the package (1 h)
    put(os.path.join(proj, "Assets", "a.txt"), age=3 * 3600)
    put(os.path.join(proj, "Code", "x.cpp"), age=3 * 3600)
    put(os.path.join(proj, "bin", "win_x64_release", "Game.dll"), b"DLL1", 2 * 3600)
    put(os.path.join(pkg, "config.pak"), age=3600)
    put(os.path.join(pkg, "Assets", "Data.pak"), age=3600)
    put(os.path.join(pkg, "bin", "win_x64", "Game.dll"), b"DLL1", 3600)
    return proj, pkg


def main():
    proj, pkg = build_fixture()
    saved = (H.PROJECT, F.PROJECT, dict(J.BUILDS), F.stale_sources)
    H.PROJECT = F.PROJECT = proj
    real_stale = F.stale_sources

    def stale_in_fixture(config):
        """final_packages.stale_sources now delegates to preflight, whose project is bound when preflight is imported: give it the fixture project."""
        try:
            import preflight
            return preflight.stale_sources(config, project=proj)
        except ImportError:
            return real_stale(config)
    F.stale_sources = stale_in_fixture
    J.BUILDS["release"] = pkg
    try:
        f = H.package_freshness("release")
        check("clean twin: a package newer than every asset and the code is fresh", f["exists"] and not f["stale"] and f["reasons"] == [], f)
        put(os.path.join(proj, "Assets", "a.txt"), age=0)
        f = H.package_freshness("release")
        check("an asset changed after the package: stale, names the asset", f["stale"] and any("asset file" in r and "a.txt" in r for r in f["reasons"]), f["reasons"])
        put(os.path.join(proj, "Assets", "a.txt"), age=3 * 3600)
        put(os.path.join(proj, "Assets", "levels", "x", "x.level"), age=0)
        f = H.package_freshness("release")
        check("only the level re-saved: stale but SOFT (the editor saves it on every close)", f["stale"] and f["only_level_resaved"] and "only level files" in f["reasons"][0], f["reasons"])
        put(os.path.join(proj, "Assets", "a.txt"), age=0)
        f = H.package_freshness("release")
        check("control: the level AND a real asset changed is not soft", f["stale"] and not f["only_level_resaved"], f["reasons"])
        put(os.path.join(proj, "Assets", "a.txt"), age=3 * 3600)
        os.remove(os.path.join(proj, "Assets", "levels", "x", "x.level"))
        put(os.path.join(proj, "Assets", "level.bak"), age=0)
        put(os.path.join(proj, "Assets", "thing.cryasset"), age=0)
        check("control: .bak and .cryasset files (the editor writes them) do not make it stale", not H.package_freshness("release")["stale"])
        os.remove(os.path.join(proj, "Assets", "level.bak"))
        os.remove(os.path.join(proj, "Assets", "thing.cryasset"))
        put(os.path.join(proj, "Code", "x.cpp"), age=0)
        f = H.package_freshness("release")
        check("code newer than the project's Game.dll: stale, says it was not rebuilt", f["stale"] and any("not rebuilt" in r for r in f["reasons"]), f["reasons"])
        put(os.path.join(proj, "Code", "x.cpp"), age=3 * 3600)
        put(os.path.join(pkg, "bin", "win_x64", "Game.dll"), b"DLL2", 3600)
        f = H.package_freshness("release")
        check("a different Game.dll in the package: stale", f["stale"] and any("Game.dll" in r for r in f["reasons"]), f["reasons"])
        put(os.path.join(pkg, "bin", "win_x64", "Game.dll"), b"DLL1", 3600)
        check("restored: fresh again", not H.package_freshness("release")["stale"])
        shutil.rmtree(pkg)
        f = H.package_freshness("release")
        check("no package at all: does not exist, stale, says how to make one", not f["exists"] and f["stale"] and "package" in f["reasons"][0], f["reasons"])
    finally:
        H.PROJECT, F.PROJECT, F.stale_sources = saved[0], saved[1], saved[3]
        J.BUILDS.clear()
        J.BUILDS.update(saved[2])
        shutil.rmtree(FIX, ignore_errors=True)
    # --- the whole report on the real machine
    r = H.check()
    names = [i["name"] for i in r["items"]]
    check("the report covers editor, watchdog, both packages, shader cache, jobs, disk", all(any(n.startswith(k) for n in names) for k in
          ("editor", "watchdog", "package release", "package profile", "shader cache", "jobs", "disk")), names)
    check("T-05/T-33: the report also covers the MCP server's own staleness and the catalog stamps", all(any(n.startswith(k) for n in names) for k in ("mcp server", "catalogs")), names)
    import server_support as SS
    mod = os.path.join(FIX, "m_stale.py")
    put(mod, b"x=1", age=0)
    saved_state = (SS.SERVER_START, dict(SS._SNAPSHOT))
    SS.SERVER_START = time.time() - 3600
    SS._SNAPSHOT.clear()
    SS._SNAPSHOT[mod] = ("m_stale", time.time() - 7200)
    stale_items = [i for i in H.check()["items"] if i["name"] == "mcp server"]
    check("T-05: a module edited after the server started makes health_check warn 'restart the MCP connector to load: ...'", stale_items and stale_items[0]["status"] == "warn" and "restart the MCP connector to load" in stale_items[0]["detail"], stale_items)
    SS.SERVER_START = time.time() + 3600
    fresh_items = [i for i in H.check()["items"] if i["name"] == "mcp server"]
    check("control: nothing edited after the start -> ok", fresh_items and fresh_items[0]["status"] == "ok", fresh_items)
    SS.SERVER_START, snap = saved_state
    SS._SNAPSHOT.clear()
    SS._SNAPSHOT.update(snap)
    shutil.rmtree(FIX, ignore_errors=True)
    check("every item has a valid status and the counts agree", all(i["status"] in ("ok", "info", "warn", "fail") for i in r["items"])
          and r["failures"] == sum(i["status"] == "fail" for i in r["items"]) and r["warnings"] == sum(i["status"] == "warn" for i in r["items"]), r["summary"])
    check("a warning or failure always comes with a fix", all(i["fix"] for i in r["items"] if i["status"] in ("warn", "fail")), [i["name"] for i in r["items"] if i["status"] in ("warn", "fail") and not i["fix"]])
    live = H.package_freshness("release")
    check("the real release package's freshness is answered (exists: %s, stale: %s)" % (live["exists"], live["stale"]), isinstance(live["exists"], bool) and (live["exists"] or bool(live["reasons"])), live["reasons"])
    # --- wait_for_editor: the timing logic with a fake clock (nothing sleeps)
    now = [0.0]
    clock, sleep = (lambda: now[0]), (lambda dt: now.__setitem__(0, now[0] + dt))
    seq = iter([("unreachable", "refused"), ("unreachable", "refused"), ("no-level", "terrain empty"), ("level", "ok")])
    w = H.wait_for_editor(120, 5, True, _state=lambda: next(seq), _sleep=sleep, _clock=clock)
    check("wait_for_editor: waits through unreachable and no-level, returns when the level is loaded (4 polls, 15 s)", w["ok"] and w["state"] == "level" and w["polls"] == 4 and w["waited_s"] == 15.0, w)
    now[0] = 0.0
    w = H.wait_for_editor(120, 5, True, _state=lambda: ("level", "ok"), _sleep=sleep, _clock=clock)
    check("wait_for_editor: an editor that is already up returns at once (1 poll, 0 s)", w["ok"] and w["polls"] == 1 and w["waited_s"] == 0.0, w)
    now[0] = 0.0
    w = H.wait_for_editor(60, 5, False, _state=iter([("unreachable", "x"), ("answers", "bridge up, no terrain query")]).__next__, _sleep=sleep, _clock=clock)
    check("wait_for_editor: require_level=false returns as soon as the bridge answers", w["ok"] and w["state"] == "answers", w)
    now[0] = 0.0
    w = H.wait_for_editor(30, 5, True, _state=lambda: ("unreachable", "refused"), _sleep=sleep, _clock=clock)
    check("wait_for_editor: a timeout is ok=false, after about the timeout, with a diagnosis", (not w["ok"]) and 20 <= w["waited_s"] <= 30 and set(w["diagnosis"]) >= {"sandbox_running", "watchdog_last_action", "watchdog_lock", "jobs_running"}, (w["waited_s"], list(w["diagnosis"])))
    for bad in ((0, 5), (1000, 5), (30, 0)):
        try:
            H.wait_for_editor(bad[0], bad[1], _state=lambda: ("level", ""), _sleep=sleep, _clock=clock)
            check("wait_for_editor: refuses timeout/poll %s" % (bad,), False)
        except ValueError:
            check("wait_for_editor: refuses timeout/poll %s" % (bad,), True)
    import server as SV
    hard = {"stale": True, "only_level_resaved": False, "reasons": ["3 asset file(s) changed after the package was made"]}
    soft = {"stale": True, "only_level_resaved": True, "reasons": ["1 asset file(s) changed (only level files)"]}
    check("shoot_game's note: nothing when fresh, a note when only the level was re-saved, a WARNING when the package is really stale",
          SV._stale_note("release", {"stale": False, "reasons": []}) == "" and SV._stale_note("release", soft).startswith("Note:")
          and SV._stale_note("release", hard).startswith("WARNING") and "package" in SV._stale_note("release", hard))
    check("the fixture is gone and the real settings are restored", not os.path.exists(FIX) and H.PROJECT == saved[0] and J.BUILDS == saved[2])
    bad = results.count(False)
    print("health test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
