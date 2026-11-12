#!/usr/bin/env python3
"""Long-running jobs for the MCP server: start one, come back, read its log. A Claude background command dies after 10 minutes and
a tool call has its own timeout, but a test group, a package, a shader-cache refresh or a voice render takes longer than either.

    jobs.py start <kind> [json params]     jobs.py status <id>     jobs.py log <id> [tail]     jobs.py list     jobs.py stop <id>

SAFETY. This is not a command runner. A job is one of KINDS below: a script file of this repository (looked up by file name in the tool folders) with arguments that were validated here (names from fixed
lists, numbers, a build name, console commands from a short character set). No shell is involved, nothing is read from a job's output as an
instruction, and nothing here downloads anything. Six kinds close the editor (tests, package, shader_cache, shoot, visual, cvar_probe: a graceful save-and-close;
all except package then launch the game); they refuse while
Sandbox.exe runs unless `allow_editor_close` is true, and only one of the kinds that need the machine to themselves runs at a time.

A job is `python -u bridge/jobs.py _run <id>` started DETACHED (it outlives the MCP server and the shell), which starts the script, waits, and writes the exit
code into job_runs/<id>.json. job_runs/<id>.log is the script's own output. The last KEEP jobs are kept.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import subprocess
import sys
import time

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ROOT = os.path.dirname(PROJECT)
JOBS = os.path.join(HERE, "job_runs")
KEEP = 30
DETACHED = 0x00000008 | 0x00000200            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
# The deploy lock lives in the shared workspace folder (state/ by default; CRYENGINE_WORKSPACE, CRYENGINE_STATE_DIR or CLAUDE_STATE_DIR override), the same folder bridge_watchdog/safe_close use
LOCK = os.environ.get("CRYENGINE_DEPLOY_LOCK") or _paths.workspace("claude_deploy_in_progress")
# CRYENGINE_PACKAGE_PREFIX: name prefix of the packaged-build folders next to the project (<prefix>_final_profile / <prefix>_final_release); default "Ashvale"
_PREFIX = os.environ.get("CRYENGINE_PACKAGE_PREFIX") or "Ashvale"
BUILDS = {"profile": os.path.join(ROOT, _PREFIX + "_final_profile"), "release": os.path.join(ROOT, _PREFIX + "_final_release")}

# kind -> (script, exclusive: needs the machine and the display to itself, closes_editor, launches_game, what it is)
KINDS = {
    "tests": ("final_packages.py", True, True, True,
              "package the game and run the test groups the changes call for (groups: stale ones, or named, or all = the ~30 minute release gate)"),
    "package": ("final_packages.py", True, True, False, "Crytek's packager for a configuration into a VERSIONED folder (Ashvale_<date>_<hash>, the old name is a junction to the newest), after the shared preflight checks; "
                "build=true compiles Game.dll first; the shader paks installed, both bin folders cleaned, symbols archived; no tests"),
    "build_game": ("build_game.py", False, False, False, "compile Game.dll for Release or Profile (cmake under the build lock) and return the MSVC error summary; never closes or starts the editor "
                   "(a Profile build with the editor open compiles but cannot link: exit 2)"),
    "shader_cache": ("shader_cache_refresh.py", True, True, True,
                     "regenerate the shipped shader cache (needed after ANY change to a material's flags or texture slots); ~10 to 20 minutes"),
    "shoot": ("view_shots.py", True, True, True, "launch the packaged game and take pictures from chosen places (use shoot_game)"),
    "visual": ("visual_check.py", True, True, True,
               "visual regression: 20 fixed views of the packaged game (houses and landscape by day, six places at night), each captured twice, compared with the accepted baseline"),
    "cvar_probe": ("cvar_probe.py", True, True, True,
                   "launch the packaged game, set console variables and report which the engine accepted, refused or does not have in THAT build (measures the cvar catalog's Release rule)"),
    "ci": ("ashvale_ci.py", True, False, False, "the gameplay regression harness in the open editor (needs Sandbox with the level; 70-110 s)"),
    "editor_suite": ("editor_suite.py", True, False, False, "every test_*.py of the editor-only tools, one after the other, with the level's object count, the open dialogs and leftover zz_mcp_* files checked around the run "
                     "(needs Sandbox with the level for the live parts; about 7 minutes; never launches the game or closes the editor)"),
    "voices": ("make_voices.py", False, False, False, "render the dialogue voice lines with the offline TTS (Piper) into Assets/audio"),
    "music": ("make_music.py", False, False, False, "synthesise the music loops and rewrite their audio library"),
    "textures": ("texture_lab.py", False, False, False, "make procedural surface textures (diffuse, normal, height) and compile them with the engine's RC"),
    "selftest": ("job_selftest.py", False, False, False, "a harmless few-second job that proves the job machinery (start, status, log, exit code)"),
}

PARAMS = {
    "tests": {"groups": "'stale' (default: what changed since each group last passed) | 'all' (the ~30 minute release gate) | a list of group names",
              "profile": "bool: also package Profile and run its shader tour", "no_package": "bool: test the package already on disk",
              "build": "bool: compile Game.dll first (build_game)", "skip_checks": "list of 'name=reason' preflight overrides (blocks RELEASE_OK.json)",
              "accept_count_change": "bool: safe_close's object-count guard (see `package`)"},
    "package": {"config": "'release' (default) | 'profile'", "build": "bool: compile Game.dll for that configuration first (build_game)",
                "skip_checks": "list of 'name=reason' preflight overrides (reason of 8+ characters; recorded in the package and it blocks RELEASE_OK.json)",
                "accept_count_change": "bool: safe_close's object-count guard: close the editor although its object count differs from the latest snapshot (look at diff_snapshot first)"},
    "build_game": {"config": "'release' (default) | 'profile'"},
    "shader_cache": {},
    "shoot": {"build": "'release' | 'profile'", "shots": "list of lines (see shoot_game)", "time_of_day": "hour 0..24"},
    "visual": {"accept": "bool: store this run as the new baseline (do it once, on a package you have looked at and approve of)",
               "inject_fault": "bool: darken the picture by 30 percent: the day views must be flagged (proves the harness can fail)",
               "calibrate": "bool: one more launch of the UNCHANGED game, added to what the check has learned about launch-to-launch variation (refused if the package is not the baseline's)"},
    "cvar_probe": {"build": "'release' (default) | 'profile'", "cvars": "list of 'name value' lines (a number); omit to let the catalog choose",
                   "sample": "1..20: how many variables per predicted class (settable / refused / absent) the catalog chooses when cvars is omitted (default 8)"},
    "ci": {},
    "editor_suite": {"only": "list of test names without test_ and .py (default: all, see editor_suite.py --list)"},
    "voices": {"sapi": "bool: use Windows' voices instead of Piper (without Piper installed this is the only way; the Windows voices are of lower quality)"},
    "music": {"tracks": "list from the loops in make_music.py (menu, day, night, danger, end); default all"},
    "textures": {"surfaces": "list of recipe names (texture_lab SURFACES); default all", "size": "128..2048, default 512"},
    "selftest": {"seconds": "1..60", "exit_code": "0 | 1"},
}


def kinds():
    """What can be started, with each kind's parameters and what it takes from the machine."""
    return [{"kind": k, "what": v[4], "params": PARAMS.get(k, {}), "needs_the_machine_to_itself": v[1], "closes_the_editor": v[2], "launches_the_game": v[3]}
            for k, v in KINDS.items()]


SHOT_LABEL = r"[A-Za-z0-9_\-]{1,40}"
NUM = r"-?\d+(?:\.\d+)?"
SHOT_PATTERNS = [
    r"^%s %s %s %s(?: %s){0,2}$" % (SHOT_LABEL, NUM, NUM, NUM, NUM),
    r"^%s house:[A-Za-z]{2,24}:[SNWE]:\d+(?:\.\d+)?(?: %s)?$" % (SHOT_LABEL, NUM),
    r"^=%s$" % SHOT_LABEL,
    r"^[!~][A-Za-z0-9_ .\-+=*]{1,160}$",
    r"^\?[A-Za-z_][A-Za-z0-9_]{1,60} -?[0-9]{1,6}(?:\.[0-9]{1,4})?$",       # a verified console variable: `?r_AntialiasingMode 1`
]
BLOCKED_COMMANDS = ("exec", "quit", "bind", "alias", "unbind", "disconnect", "unload")


def run(cmd, **kw):
    """subprocess.run that never inherits this process's stdin. Under MCP stdio that is the PROTOCOL pipe: a child that inherits it can block (for example a
    script that takes 0.6 s on its own timed out at 120 s inside the server; ffmpeg would also read keystrokes from it). Every child the server starts
    must come through here (or pass stdin itself)."""
    if "input" not in kw:                                     # `input=` supplies its own pipe, and subprocess.run refuses both
        kw.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.run(cmd, **kw)


class JobError(ValueError):
    pass


def _source_keys(filename, variable):
    """The keys of a module-level dict literal, read from the source: the server must not import numpy-heavy modules to validate a name."""
    text = open(_paths.script(filename), encoding="utf-8").read()
    m = re.search(r"^%s = \{(.*?)\}" % variable, text, re.S | re.M)           # the values are function names: the first } closes the dict
    return re.findall(r'"([A-Za-z_]+)":', m.group(1)) if m else []


def test_groups():
    sys.path.insert(0, HERE)
    import final_packages
    return list(final_packages.GROUPS)


def plan():
    """What the `tests` job would run and why (final_packages --dry-run): reads files, changes nothing, takes a few seconds."""
    try:
        r = run([sys.executable, _paths.script("final_packages.py"), "--dry-run"], capture_output=True, text=True, cwd=HERE, timeout=40)
    except subprocess.TimeoutExpired as exc:
        raise JobError("the dry run did not finish in 40 s; partial output: %r %r" % ((exc.stdout or b"")[-300:], (exc.stderr or b"")[-300:]))
    return [l for l in r.stdout.splitlines() if l.strip()]


def sandbox_running():
    return "Sandbox.exe" in run(["tasklist", "/FI", "IMAGENAME eq Sandbox.exe"], capture_output=True, text=True).stdout


def _alive(pid):
    if not pid:
        return False
    r = run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"], capture_output=True, text=True)
    return ('"%d"' % pid) in r.stdout and "python" in r.stdout.lower()


def _names(value, allowed, what):
    items = [value] if isinstance(value, str) else list(value or [])
    bad = [i for i in items if i not in allowed]
    if bad:
        raise JobError("unknown %s %s; choose from %s" % (what, bad, sorted(allowed)))
    return items


SKIP_CHECK = re.compile(r"^[a-z_]+(?::[A-Za-z0-9_\-]+)?=[^\r\n]{8,200}$")


def _common_args(p):
    """the arguments `tests` and `package` share: build, preflight overrides, the editor-close guard override"""
    args = []
    if p.get("build"):
        args.append("--build")
    if p.get("accept_count_change"):
        args.append("--accept-count-change")
    skips = p.get("skip_checks") or []
    if isinstance(skips, str):
        skips = [skips]
    for s in skips:
        if not SKIP_CHECK.match(s):
            raise JobError("skip_checks entries look like 'level_check:example=unreachable stock level, deleted before release' (check name, '=', a reason of 8 to 200 characters), got %r" % s)
        args += ["--skip-check", s]
    return args


def stale_report():
    """Which configurations' Game.dll is older than the sources, each on its own (a Release build says nothing about Profile) -> {config: {"stale": bool, "files": [...], "dll": path}}"""
    sys.path.insert(0, HERE)
    import preflight
    out = {}
    for config in ("release", "profile"):
        files = preflight.stale_sources(config)
        out[config] = {"stale": bool(files), "files": files[:8], "n_files": len(files), "dll": preflight.game_dll(config)}
    return out


def _build_dir(build):
    if build in BUILDS:
        return BUILDS[build]
    raise JobError("build must be 'release' or 'profile' (the packages made by the `package` job)")


def build_argv(kind, params, job_id):
    """The validated argument list for a kind. Raises JobError with what to fix."""
    p = dict(params or {})
    if kind == "tests":
        groups = p.get("groups", "stale")
        args = _common_args(p)
        if groups == "all":
            args.append("--all")
        elif groups != "stale":
            args += ["--groups", ",".join(_names(groups, test_groups(), "test group"))]
        if p.get("profile"):
            args.append("--profile")
        if p.get("no_package"):
            args.append("--no-package")
        return args
    if kind == "package":
        config = p.get("config", "release")
        if config not in ("release", "profile"):
            raise JobError("config must be 'release' or 'profile'")
        return ["--package-only", "--configs", config] + _common_args(p)
    if kind == "build_game":
        config = p.get("config", "release")
        if config not in ("release", "profile"):
            raise JobError("config must be 'release' or 'profile'")
        return [config]
    if kind in ("shader_cache", "ci"):
        return []
    if kind == "visual":
        modes = [k for k in ("accept", "inject_fault", "calibrate") if p.get(k)]
        if len(modes) > 1:
            raise JobError("%s cannot be combined: accept would store a fault or a calibration run as the baseline, and calibrating on a faulted run teaches the check to ignore it" % " + ".join(modes))
        return ["--out", os.path.join(JOBS, job_id + "_shots")] + (["--accept"] if p.get("accept") else []) + (["--inject-fault"] if p.get("inject_fault") else []) + (["--calibrate"] if p.get("calibrate") else [])
    if kind == "cvar_probe":
        build = _build_dir(p.get("build", "release"))
        if not os.path.isdir(build):
            raise JobError("%s does not exist: run the `package` job first" % build)
        lines = p.get("cvars")
        if isinstance(lines, str):
            lines = lines.splitlines()
        lines = [l.strip() for l in (lines or []) if l.strip()]
        if not lines:
            sample = int(p.get("sample", 8))
            if not 1 <= sample <= 20:
                raise JobError("sample is 1 to 20")
            sys.path.insert(0, HERE)
            import cvar_catalog
            try:
                lines = ["%s %s" % (n, v) for n, v, _pred in cvar_catalog.pick_probes(sample)]
            except RuntimeError as exc:
                raise JobError(str(exc))
        if len(lines) > 90:
            raise JobError("at most 90 variables per job")
        for l in lines:
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]{1,60} -?[0-9]{1,6}(?:\.[0-9]{1,4})?$", l):
                raise JobError("probe line not understood: %r (name value: a number)" % l)
        os.makedirs(JOBS, exist_ok=True)
        probes = os.path.join(JOBS, job_id + ".probes.txt")
        open(probes, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        return [build, os.path.join(JOBS, job_id + "_probe", "result.json"), probes]       # a folder: a stray .json beside the records would be read as a job
    if kind == "editor_suite":
        known = {f[5:-3] for f in os.listdir(os.path.join(HERE, "tests")) if f.startswith("test_") and f.endswith(".py")}
        return _names(p.get("only") or [], known, "test")
    if kind == "selftest":
        seconds, code = int(p.get("seconds", 4)), int(p.get("exit_code", 0))
        if not 1 <= seconds <= 60 or code not in (0, 1):
            raise JobError("seconds is 1 to 60 and exit_code 0 or 1")
        return [str(seconds), str(code)]
    if kind == "voices":
        piper = os.path.join(HERE, "tts_piper", "piper", "piper.exe")
        if p.get("sapi"):
            return ["--sapi"]
        if not os.path.isfile(piper):
            raise JobError("Piper (the offline neural TTS) is not installed in tts_piper, and without it the lines would be rendered with Windows' "
                           "own voices, which sound poor. Installing it is a DOWNLOAD (piper_windows_amd64.zip, 22,477,236 bytes, from "
                           "github.com/rhasspy/piper release 2023.11.14-2, plus a voice .onnx of 63,201,294 bytes from huggingface.co/rhasspy/piper-voices): "
                           "ask the user for permission with those names and sizes, then do it. Or pass sapi=true to use the Windows voices.")
        return []
    if kind == "music":
        return _names(p.get("tracks") or [], set(_source_keys("make_music.py", "TRACKS")), "music track")
    if kind == "textures":
        surfaces = _names(p.get("surfaces") or [], set(_source_keys("texture_lab.py", "SURFACES")), "surface")
        size = int(p.get("size", 512))
        if size not in (128, 256, 512, 1024, 2048):
            raise JobError("size must be 128, 256, 512, 1024 or 2048")
        return surfaces + ["--size", str(size)]
    if kind == "shoot":
        build = _build_dir(p.get("build", "release"))
        if not os.path.isdir(build):
            raise JobError("%s does not exist: run the `package` job first" % build)
        lines = p.get("shots")
        if isinstance(lines, str):
            lines = lines.splitlines()
        lines = [l.strip() for l in (lines or []) if l.strip()]
        if not lines:
            raise JobError("shots is empty: lines like 'label x y yaw [z]', 'label house:Chapel:S:12', '!console command', '=label'")
        if len(lines) > 60:
            raise JobError("at most 60 lines per job (each picture costs seconds and the images cost context)")
        for l in lines:
            if not any(re.match(pat, l) for pat in SHOT_PATTERNS):
                raise JobError("shot line not understood: %r (label x y yaw [z] | label house:Name:S|N|W|E:metres | !cmd | ~cmd | =label)" % l)
            if l[0] in "!~" and l[1:].split()[0].lower() in BLOCKED_COMMANDS:
                raise JobError("the console command %r is not allowed here" % l.split()[0][1:])
        tod = float(p.get("time_of_day", 12))
        if not 0 <= tod <= 24:
            raise JobError("time_of_day is an hour from 0 to 24")
        out = os.path.join(JOBS, job_id + "_shots")
        shots = os.path.join(JOBS, job_id + ".shots.txt")
        os.makedirs(JOBS, exist_ok=True)
        open(shots, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        return [build, out, shots, "--tod", "%g" % tod]
    raise JobError("unknown job kind %r; kinds: %s" % (kind, ", ".join(KINDS)))


# ---------------------------------------------------------------- records
def _file(job_id, ext):
    if not re.match(r"^[0-9]{8}_[0-9]{6}_[a-z_]+$", job_id):
        raise JobError("not a job id: %r" % job_id)
    return os.path.join(JOBS, job_id + ext)


def _save(rec):
    tmp = _file(rec["id"], ".json.tmp")
    json.dump(rec, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, _file(rec["id"], ".json"))


def _load(job_id):
    try:
        return json.load(open(_file(job_id, ".json"), encoding="utf-8"))
    except OSError:
        raise JobError("no such job %r (list_jobs shows them)" % job_id)


def _refresh(rec):
    """Fold in what the wrapper cannot say about itself: it died without writing an exit code."""
    if rec["state"] == "running" and not _alive(rec.get("pid")):
        rec = _load(rec["id"])                                  # it may have finished between the read and the check
        if rec["state"] == "running":
            rec.update(state="lost", ended=time.time(), note="the job's process is gone and wrote no exit code (killed, or the machine slept/restarted)")
            _save(rec)
    return rec


def list_jobs(limit=15):
    if not os.path.isdir(JOBS):
        return []
    recs = []
    for n in sorted(os.listdir(JOBS), reverse=True):
        if n.endswith(".json"):
            try:
                recs.append(_refresh(_load(n[:-5])))
            except (JobError, ValueError, KeyError):          # a file that is not a job record (no state) is not a job
                continue
        if len(recs) >= limit:
            break
    return [_brief(r) for r in recs]


def _brief(rec):
    end = rec.get("ended") or time.time()
    return {"id": rec["id"], "kind": rec["kind"], "state": rec["state"], "elapsed_s": round(end - rec["started"]), "params": rec["params"]}


def _tail(path, n):
    try:
        data = open(path, "rb").read()
    except OSError:
        return []
    return data.decode("utf-8", "replace").splitlines()[-n:]


def _result(rec):
    """What the job's output says it achieved: the exit code alone says nothing for a test run (final_packages exits 0 when tests fail)."""
    lines = _tail(_file(rec["id"], ".log"), 4000)
    if rec["kind"] == "tests":
        summary = [l for l in lines if l.startswith("SUMMARY")]
        if not summary:
            return {"verdict": "no SUMMARY line yet" if rec["state"] == "running" else "no SUMMARY line: nothing ran or it stopped early"}
        s = summary[-1]
        timing = [l for l in lines if l.startswith("TIME")]
        return {"verdict": "FAIL" if re.search(r"FAILED|rc-?\d", s) else "pass", "summary": s, "time": timing[-1] if timing else ""}
    if rec["kind"] == "visual":
        line = [l for l in lines if l.startswith("RESULT visual")]
        if not line:
            return {"verdict": "no RESULT line yet" if rec["state"] == "running" else "no RESULT line: it stopped early (read the log)"}
        text = line[-1]
        bad = (" 0 changed" not in text and "baseline accepted" not in text and "CAN fail: PASS" not in text and "calibration run added" not in text) or "FAIL" in text or "no baseline" in text or "refused" in text
        return {"verdict": "FAIL" if bad else "pass", "summary": text}
    if rec["kind"] == "cvar_probe":
        line = [l for l in lines if l.startswith("RESULT cvar_probe")]
        if not line:
            return {"verdict": "no RESULT line yet" if rec["state"] == "running" else "no RESULT line: it stopped early (read the log)"}
        m = re.search(r"predicted (\d+) of (\d+)", line[-1])
        ok = bool(m) and m.group(1) == m.group(2) and "ERRORS" not in line[-1]
        return {"verdict": "pass" if ok else "FAIL", "summary": line[-1], "disagreements": [l for l in lines if l.startswith("DISAGREE")][:12]}
    if rec["kind"] == "build_game":
        line = [l for l in lines if l.startswith("RESULT build_game")]
        if not line:
            return {"verdict": "no RESULT line yet" if rec["state"] == "running" else "no RESULT line: it stopped early (read the log)"}
        errs = [l.strip() for l in lines if re.search(r"error (C|LNK)\d+", l)][:12]
        return {"verdict": "pass" if line[-1].endswith("BUILD OK") else "FAIL", "summary": line[-1], "errors": errs}
    if rec["kind"] == "shoot":
        out = os.path.join(JOBS, rec["id"] + "_shots")
        pics = sorted(f for f in os.listdir(out) if f.lower().endswith(".png")) if os.path.isdir(out) else []
        return {"verdict": "%d picture(s)" % len(pics), "pictures": pics}
    if rec["state"] == "running":
        return {}
    return {"verdict": "ok" if rec.get("exit_code") == 0 else "exit code %s" % rec.get("exit_code")}


def status(job_id, tail=6):
    rec = _refresh(_load(job_id))
    out = _brief(rec)
    out.update(_result(rec))
    if rec.get("exit_code") is not None:
        out["exit_code"] = rec["exit_code"]
    if rec.get("note"):
        out["note"] = rec["note"]
    out["log_tail"] = _tail(_file(job_id, ".log"), tail)
    return out


def log(job_id, tail=60, contains=""):
    _load(job_id)
    lines = _tail(_file(job_id, ".log"), 20000)
    if contains:
        lines = [l for l in lines if contains.lower() in l.lower()]
    return lines[-max(1, min(int(tail), 400)):]


def shots(job_id):
    """Absolute paths of a shoot job's pictures."""
    out = os.path.join(JOBS, job_id + "_shots")
    _load(job_id)
    return [os.path.join(out, f) for f in sorted(os.listdir(out)) if f.lower().endswith(".png")] if os.path.isdir(out) else []


def start(kind, params=None, allow_editor_close=False):
    if kind not in KINDS:
        raise JobError("unknown job kind %r; kinds: %s" % (kind, "; ".join("%s (%s)" % (k, v[4]) for k, v in KINDS.items())))
    script, exclusive, closes, launches, what = KINDS[kind]
    job_id = time.strftime("%Y%m%d_%H%M%S") + "_" + kind
    argv = build_argv(kind, params, job_id)
    if closes and sandbox_running() and not allow_editor_close:
        raise JobError("this job closes the editor (it saves the level and shuts Sandbox down, then launches the game) and Sandbox.exe is running. "
                       "Tell the user, and pass allow_editor_close=true if that is fine.")
    if kind == "ci" and not sandbox_running():
        raise JobError("the regression harness plays the game inside the open editor: Sandbox.exe is not running")
    if exclusive:
        busy = [j for j in list_jobs(50) if j["state"] == "running" and KINDS[j["kind"]][1]]
        if busy:
            raise JobError("job %s (%s) is running and needs the machine to itself; wait for it or stop_job it" % (busy[0]["id"], busy[0]["kind"]))
    os.makedirs(JOBS, exist_ok=True)
    _prune()
    rec = {"id": job_id, "kind": kind, "params": params or {}, "script": script, "argv": argv, "state": "running", "started": time.time(),
           "closes_editor": closes and bool(allow_editor_close), "launches_game": launches}
    _save(rec)
    logf = open(_file(job_id, ".log"), "wb")
    p = subprocess.Popen([sys.executable, "-u", os.path.abspath(__file__), "_run", job_id], cwd=HERE, stdin=subprocess.DEVNULL, stdout=logf,
                         stderr=subprocess.STDOUT, creationflags=DETACHED)
    rec["pid"] = p.pid
    _save(rec)
    return {"id": job_id, "kind": kind, "started": True, "what": what, "next": "job_status(%r) to see how it is going; read_job_log for the output" % job_id}


def stop(job_id):
    rec = _refresh(_load(job_id))
    if rec["state"] != "running":
        return {"id": job_id, "state": rec["state"], "note": "not running"}
    run(["taskkill", "/PID", str(rec["pid"]), "/T", "/F"], capture_output=True)
    if rec.get("child_pid"):
        run(["taskkill", "/PID", str(rec["child_pid"]), "/T", "/F"], capture_output=True)
    if rec.get("launches_game"):
        run(["taskkill", "/F", "/IM", "GameLauncher.exe"], capture_output=True)        # a game this job started: it was detached from the job's tree
    rec = _load(job_id)
    rec.update(state="stopped", ended=time.time())
    _save(rec)
    if rec.get("closes_editor") or rec.get("launches_game"):
        try:
            os.remove(LOCK)                                      # the killed script cannot remove its own watchdog lock; the watchdog reopens the editor
        except OSError:
            pass
        try:
            sys.path.insert(0, HERE)
            import safe_close
            safe_close.release()                                 # ... and the "closed on purpose" marker it wrote, or the watchdog would honour it for hours
        except Exception:                                        # noqa: BLE001
            pass
    return {"id": job_id, "state": "stopped", "note": "stopped; the editor is closed if the job had closed it (the watchdog reopens it within minutes)"}


def _prune():
    names = sorted(n[:-5] for n in os.listdir(JOBS) if n.endswith(".json"))
    for old in names[:-KEEP]:
        for n in os.listdir(JOBS):
            if n == old + ".json" or n == old + ".log" or n == old + ".shots.txt" or n == old + "_shots":
                path = os.path.join(JOBS, n)
                if os.path.isdir(path):
                    import shutil
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)


# ---------------------------------------------------------------- the detached wrapper
def _run(job_id):
    rec = _load(job_id)
    print("job %s (%s) started %s" % (job_id, rec["kind"], time.strftime("%H:%M:%S")), flush=True)
    try:
        if rec.get("closes_editor"):
            sys.path.insert(0, HERE)
            import final_packages
            final_packages.close_sandbox(reason="job " + job_id)  # safe_close: snapshot, verified save, close through the bridge, the close-time prompt answered by name; REFUSES a dirty level (raises CloseRefused)
        cmd = [sys.executable, "-u", _paths.script(rec["script"])] + rec["argv"]
        print("running: " + " ".join(cmd[2:]), flush=True)
        child = subprocess.Popen(cmd, cwd=HERE, stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=subprocess.STDOUT)
        rec = _load(job_id)
        rec["child_pid"] = child.pid
        _save(rec)
        code = child.wait()
    except Exception as exc:                                    # noqa: BLE001 - the record must say what happened
        print("job wrapper failed: %r" % exc, flush=True)
        code = -1
    finally:
        if rec.get("closes_editor"):
            try:
                import safe_close
                safe_close.release()                            # the job is over: the watchdog may reopen the editor (and the level that was open)
            except Exception:                                   # noqa: BLE001
                pass
    rec = _load(job_id)
    if rec["state"] == "running":
        rec.update(state="done" if code == 0 else "failed", exit_code=code, ended=time.time())
        _save(rec)
    print("job finished: exit %s after %.0f s" % (code, time.time() - rec["started"]), flush=True)


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    try:
        if a[0] == "_run":
            _run(a[1])
        elif a[0] == "start":
            print(json.dumps(start(a[1], json.loads(a[2]) if len(a) > 2 else {}, "--allow-editor-close" in a), indent=1))
        elif a[0] == "status":
            print(json.dumps(status(a[1]), indent=1))
        elif a[0] == "log":
            print("\n".join(log(a[1], int(a[2]) if len(a) > 2 else 60)))
        elif a[0] == "list":
            print(json.dumps(list_jobs(), indent=1))
        elif a[0] == "stop":
            print(json.dumps(stop(a[1]), indent=1))
        else:
            print(__doc__)
            return 2
    except JobError as exc:
        print("refused: %s" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
