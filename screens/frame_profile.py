#!/usr/bin/env python3
"""The engine's own FRAME PROFILER, run on the open editor's viewport (ENGINE_LEARNING_PLAN K4 / F8): where the CPU time of a frame goes, per thread and per function, without the game.

The manual (Loadtime Profiling, Profiling and Debugging) describes the boot profiler: it records scope markers (CRY_PROFILE_REGION / LOADING_TIME_PROFILE_SECTION) into sessions and writes them as XML to
`%USER%/TestResults/bp_<session>.xml` (to be opened in ProfVis.exe). Besides the boot and level sessions, the console command `sys_bp_frames <n>` records the next n frames as the session "frames"
(BootProfiler.cpp StartFrameProfilingCmd) -> `bp_frames.xml`. Observed in the editor: it writes nothing while `sys_bp_enabled` is 0 (its default), and the editor only produces frames while it is
kept active (`ed_keepEditorActive`, held for the capture and released again). The file is `<root><thread name totalTimeMS totalBlocks><block name totalTimeMS startTime stopTime args>...` (blocks nest; blocks
under `sys_bp_time_threshold` ms, default 0.1, are not written). One frame = one top-level `CSystem::Update` block of the Main thread plus what follows it up to the next one.

  P001 (information) the main thread's frame time and the functions that cost the most            P003 a HITCH: a frame much longer than the median, and the function that made it so
  P002 the main thread spent time in the log (CLog::LogV): something writes log lines in its frame  P004 (information) how long the render thread waited for the main thread

What it measures is the EDITOR's viewport (a ~700 x 850 panel at sys_spec 3, with the editor's own work), not the shipped game: use it to compare one state of the scene with another, and to find work that
happens every frame or every second. A comparison needs a control (`compare`): the same capture twice unchanged shows the noise a difference has to exceed.

    python screens/frame_profile.py [frames]        capture and print the summary
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import collections
import os
import statistics
import sys
import time
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
RESULT = os.path.join(PROJECT, "user", "TestResults", "bp_frames.xml")
FRAME_MARKER = "CSystem::Update"
LOG_CALLS_WARN = 3                  # log writes inside the captured frames before P002 speaks (steady-state frames normally write none)
LOG_MS_PER_CALL_WARN = 0.05         # a log write that costs less is not worth a finding

sys.path.insert(0, HERE)


class ProfileError(RuntimeError):
    pass


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def parse(path=RESULT, frame_marker=FRAME_MARKER):
    """-> {frames, main_ms, frame_ms: [per frame], frame_self: [{function: self ms}], threads: [{name, total_ms, blocks}], functions: {name: {self, total, calls, threads}}}"""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ProfileError("cannot read the profile %s: %s" % (path, exc)) from exc
    threads = []
    funcs = {}
    frame_ms, frame_self = [], []

    def walk(block, self_sink, thread_name):
        t = float(block.get("totalTimeMS", "0"))
        kids = block.findall("block")
        own = t - sum(float(k.get("totalTimeMS", "0")) for k in kids)
        name = block.get("name", "")
        f = funcs.setdefault(name, {"self": 0.0, "total": 0.0, "calls": 0, "threads": set()})
        f["self"] += own
        f["total"] += t
        f["calls"] += 1
        f["threads"].add(thread_name)
        if self_sink is not None:
            self_sink[name] += own
        for k in kids:
            walk(k, self_sink, thread_name)

    for th in root.findall("thread"):
        name = th.get("name", "")
        threads.append({"name": name, "total_ms": float(th.get("totalTimeMS", "0")), "blocks": int(th.get("totalBlocks", "0") or 0)})
        if name == "Main":
            current = None
            for b in th.findall("block"):
                if b.get("name") == frame_marker or current is None:
                    current = {"ms": 0.0, "self": collections.Counter()}
                    frame_ms.append(0.0)
                    frame_self.append(current["self"])
                frame_ms[-1] += float(b.get("totalTimeMS", "0"))
                walk(b, current["self"], name)
        else:
            for b in th.findall("block"):
                walk(b, None, name)
    return {"frames": len(frame_ms), "main_ms": sum(frame_ms), "frame_ms": frame_ms, "frame_self": frame_self, "threads": threads, "functions": funcs}


def summary(profile, top=10):
    n = max(profile["frames"], 1)
    ranked = sorted(profile["functions"].items(), key=lambda kv: kv[1]["self"], reverse=True)[:top]
    fm = profile["frame_ms"] or [0.0]
    return {"frames": profile["frames"], "main_ms_per_frame": round(profile["main_ms"] / n, 3), "median_frame_ms": round(statistics.median(fm), 3), "max_frame_ms": round(max(fm), 3),
            "top_self": [{"name": k, "self_ms_per_frame": round(v["self"] / n, 4), "total_ms_per_frame": round(v["total"] / n, 4), "calls_per_frame": round(v["calls"] / n, 2), "threads": len(v["threads"])} for k, v in ranked],
            "threads": [{"name": t["name"], "total_ms": round(t["total_ms"], 2)} for t in profile["threads"]]}


def hitches(profile, ratio=1.8, min_excess_ms=1.0, top=3):
    """Frames much longer than the median -> [{frame, ms, median_ms, causes: [(function, extra ms vs a normal frame)]}]"""
    fm = profile["frame_ms"]
    if len(fm) < 5:
        return []
    med = statistics.median(fm)
    names = set()
    for fs in profile["frame_self"]:
        names.update(fs)
    typical = {name: statistics.median([fs.get(name, 0.0) for fs in profile["frame_self"]]) for name in names}
    out = []
    for i, ms in enumerate(fm):
        if ms > med * ratio and ms - med >= min_excess_ms:
            extra = sorted(((name, profile["frame_self"][i].get(name, 0.0) - typical.get(name, 0.0)) for name in typical), key=lambda kv: kv[1], reverse=True)[:top]
            out.append({"frame": i, "ms": round(ms, 3), "median_ms": round(med, 3), "causes": [(n, round(x, 3)) for n, x in extra if x > 0.05]})
    return out


def check(profile):
    """-> {"findings", "summary", "hitches"}"""
    findings = []
    n = max(profile["frames"], 1)
    s = summary(profile)
    if profile["frames"] == 0:
        findings.append(_finding("error", "P000", "bp_frames.xml", "no frame in the profile: the Main thread has no %s block" % FRAME_MARKER))
        return {"findings": findings, "summary": s, "hitches": []}
    top = s["top_self"][0] if s["top_self"] else {"name": "?", "self_ms_per_frame": 0.0}
    findings.append(_finding("info", "P001", "Main thread", "%d frames: %.2f ms per frame on the main thread (median %.2f, longest %.2f); the largest single cost is %s (%.2f ms per frame, all threads)"
                             % (profile["frames"], s["main_ms_per_frame"], s["median_frame_ms"], s["max_frame_ms"], top["name"], top["self_ms_per_frame"])))
    log = profile["functions"].get("CLog::LogV")
    if log and "Main" in log["threads"] and log["calls"] >= LOG_CALLS_WARN and log["self"] / log["calls"] >= LOG_MS_PER_CALL_WARN:
        share = 100.0 * log["self"] / profile["main_ms"] if profile["main_ms"] > 0 else 0.0
        findings.append(_finding("warning", "P002", "CLog::LogV", "%d log writes cost %.2f ms (%.2f ms per call, %.1f%% of the main thread's %.1f ms in these %d frames): something logs from inside the frame"
                                 % (log["calls"], log["self"], log["self"] / log["calls"], share, profile["main_ms"], profile["frames"]),
                                 "find the CryLog / trace call that runs in a component's update (a once-per-second trace shows up as one hitch frame)"))
    hs = hitches(profile)
    for h in hs[:5]:
        findings.append(_finding("warning", "P003", "frame %d" % h["frame"], "a hitch: this frame took %.2f ms against a median of %.2f; extra cost: %s"
                                 % (h["ms"], h["median_ms"], ", ".join("%s +%.2f ms" % c for c in h["causes"]) or "spread over many functions")))
    rt = [t for t in profile["threads"] if t["name"] == "RenderThread"]
    wait = profile["functions"].get("SRenderThread::WaitFlushCond")
    if rt and wait and rt[0]["total_ms"] > 0:
        findings.append(_finding("info", "P004", "RenderThread", "the render thread waited for the main thread %.0f%% of the time (%.1f of %.1f ms)" % (100.0 * wait["self"] / rt[0]["total_ms"], wait["self"], rt[0]["total_ms"])))
    return {"findings": findings, "summary": s, "hitches": hs}


def compare(a, b, a2=None, top=8, floor_ms=0.05, noise_factor=3.0):
    """Per-frame self time of every function in profile a (before) against b (after); a2 is the SAME state as a captured again: the noise a difference has to beat (3 x, and at least floor_ms)
    -> {"changed": [{name, before, after, delta, noise}], "unchanged_checked": n}"""
    def per_frame(p):
        n = max(p["frames"], 1)
        return {k: v["self"] / n for k, v in p["functions"].items()}
    pa, pb = per_frame(a), per_frame(b)
    p2 = per_frame(a2) if a2 else {}
    changed = []
    for name in set(pa) | set(pb):
        before, after = pa.get(name, 0.0), pb.get(name, 0.0)
        noise = abs(before - p2.get(name, before)) if a2 else 0.0
        if abs(after - before) > max(floor_ms, noise_factor * noise):
            changed.append({"name": name, "before": round(before, 4), "after": round(after, 4), "delta": round(after - before, 4), "noise": round(noise, 4)})
    changed.sort(key=lambda r: abs(r["delta"]), reverse=True)
    return {"changed": changed[:top], "functions_compared": len(set(pa) | set(pb))}


def _cvar_get(name):
    import debug_views
    return str(debug_views._cvar_get(name))


def _cvar_set(name, value):
    import debug_views
    debug_views._cvar_set(name, value)


def capture(frames=240, timeout=40.0, cvars=None, settle=0.8, path=RESULT):
    """Record the next `frames` frames of the open editor (sys_bp_frames) and parse them. cvars {name: value}: set for the capture and put back. The boot profiler is switched on for it and off again,
    and the editor is kept active (ed_keepEditorActive) for the length of the capture only: a long hold has ended in the AMD driver's crash on this machine. 240 frames are about 1.2 s: long enough to catch
    something that happens once a second."""
    import bridge_py
    import editor_state
    frames = int(frames)
    if not 5 <= frames <= 2000:
        raise ProfileError("frames must be 5 to 2000")
    saved = {}
    was_bp = None
    try:
        was_bp = _cvar_get("sys_bp_enabled")
        for k, v in (cvars or {}).items():
            saved[k] = _cvar_get(k)
            _cvar_set(k, v)
        _cvar_set("sys_bp_enabled", 1)
        editor_state.hold_active()
        time.sleep(settle)                                       # let the viewport draw with the new state before recording
        t0 = time.time()
        r = bridge_py.send("sys_bp_frames %d" % frames, 20)
        if not r.get("ok"):
            raise ProfileError("sys_bp_frames was refused: %s" % str(r)[:120])
        while time.time() - t0 < timeout:
            time.sleep(0.4)
            if os.path.isfile(path) and os.path.getmtime(path) > t0 - 0.5:
                time.sleep(0.8)                                  # let the writer finish (the file is written in one go after the last frame)
                break
        else:
            raise ProfileError("no profile appeared in %s within %d s (is the editor drawing frames?)" % (path, timeout))
    finally:
        try:
            editor_state.release_active()
        finally:
            if was_bp is not None:
                _cvar_set("sys_bp_enabled", was_bp)
            for k, v in saved.items():
                _cvar_set(k, v)
    return parse(path)


def main():
    frames = int(sys.argv[1]) if len(sys.argv) > 1 else 240
    prof = capture(frames)
    rep = check(prof)
    s = rep["summary"]
    print("%d frames, main thread %.2f ms per frame (median %.2f, longest %.2f)" % (s["frames"], s["main_ms_per_frame"], s["median_frame_ms"], s["max_frame_ms"]))
    for r in s["top_self"]:
        print("  %7.3f ms/frame self  %7.3f total  x%-6.1f %2d thread(s)  %s" % (r["self_ms_per_frame"], r["total_ms_per_frame"], r["calls_per_frame"], r["threads"], r["name"]))
    for f in rep["findings"]:
        print("  %-7s %s %s: %s" % (f["severity"], f["code"], f["where"], f["message"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
