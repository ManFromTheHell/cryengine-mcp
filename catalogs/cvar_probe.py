#!/usr/bin/env python3
"""Set console variables in the PACKAGED game and report what the engine did (a measurement of cvar_catalog's settable() rule).

    cvar_probe.py <build dir> <out json> <probe file | ->

One probe per line: `name value`. For each the game's own bridge is asked for the value (bridge.cvar_get), told to set it (bridge.cvar_set: ICVar::Set,
the call that goes through CXConsole::OnBeforeVarChange, the gate that refuses cheat/const/read-only variables outside dev mode) and asked again.
  accepted  the value changed to the one asked for   refused  the variable exists and kept its value   absent  the build has no such variable
Each line is also compared with what the catalog predicted for that build. The last line, RESULT cvar_probe, says how many agreed.
Refuses to run while Sandbox.exe is up (the job layer closes the editor first); it launches the game, so it needs the display to itself.
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
sys.path.insert(0, HERE)
import cvar_catalog as C                    # noqa: E402

BUILD = OUT = PROBES = N = None             # set in main(): night_test (its LAUNCHER / send for this build) reads sys.argv when it is imported


# Two throwaway config files, to see what the engine does with a line in each (SystemInit.cpp): user.cfg is loaded through the CVAR WHITELIST sink,
# %USER%/game.cfg with no sink (only the per-variable flag gate applies). Each holds a plain variable and a cheat/const one, none at its default value.
TEMP_CFG = [(("user.cfg",), "user.cfg (whitelist sink)", [("e_ProcVegetationMaxSectorsInCache", "20"), ("ai_DrawFireEffectMaxAngle", "9")]),
            (("user", "game.cfg"), "user/game.cfg (no sink)", [("p_joint_damage_accum_threshold", "1.2"), ("ai_UseSmartPathFollower", "0")])]


def write_temp_cfgs():
    """Create the throwaway cfg files that do not exist yet (an existing one is somebody's settings: never touched). -> [(path, label, vars)] created."""
    made = []
    for parts, label, items in TEMP_CFG:
        path = os.path.join(BUILD, *parts)
        if os.path.exists(path):
            print("TEMPCFG %s exists: not touched, not tested" % label, flush=True)
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("".join("%s=%s\n" % kv for kv in items))
        made.append((path, label, items))
    return made


def number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def value_of(reply):
    return str(json.loads(reply.get("output") or "{}").get("value", ""))


def probe_one(name, wanted):
    """-> {name, wanted, before, after, state}. Never raises: a bridge error is a result. If the game already holds the wanted value
    the probe asks for another one (wanted+1), because 'it holds it' proves nothing about whether the variable can be changed."""
    rec = {"name": name, "wanted": wanted, "before": None, "after": None, "state": "error"}
    try:
        reply = N.send("bridge.cvar_get " + name, 10)
        if not reply.get("ok", True):
            rec["state"] = "absent" if "no such cvar" in json.dumps(reply) else "error"
            rec["error"] = reply.get("error")
            return rec
        rec["before"] = value_of(reply)
        if number(rec["before"]) is not None and number(rec["before"]) == number(wanted):
            rec["wanted"] = wanted = "%g" % (number(wanted) + 1)
        N.send("bridge.cvar_set %s %s" % (name, wanted), 10)
        rec["after"] = value_of(N.send("bridge.cvar_get " + name, 10))      # asked again: the answer to the set call is not trusted on its own
    except Exception as exc:                                                 # noqa: BLE001
        rec["error"] = str(exc)
        return rec
    w, a = number(wanted), number(rec["after"])
    took = abs(w - a) < 1e-6 if (w is not None and a is not None) else rec["after"] == wanted
    rec["state"] = "accepted" if took else "refused"
    return rec


def main():
    global BUILD, OUT, PROBES, N
    if len(sys.argv) < 4:
        raise SystemExit("usage: cvar_probe.py <build dir> <out json> <probe file | ->")
    BUILD = os.path.abspath(sys.argv[1])
    OUT = os.path.abspath(sys.argv[2])
    PROBES = sys.argv[3]
    sys.argv = [sys.argv[0], BUILD]
    import night_test
    N = night_test
    if not N.LAUNCHER:
        raise SystemExit("no GameLauncher.exe under %s (bin/win_x64_release or bin/win_x64)" % BUILD)
    lines = [l.strip() for l in (sys.stdin if PROBES == "-" else open(PROBES, encoding="utf-8")) if l.strip() and not l.startswith("#")]
    probes = []
    for l in lines:
        m = re.match(r"^([A-Za-z_]\w{1,60})\s+(-?[0-9]{1,6}(?:\.[0-9]{1,4})?)$", l)
        if not m:
            raise SystemExit("probe line not understood: %r (name value, a number)" % l)
        probes.append((m.group(1), m.group(2)))
    running = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Sandbox.exe"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    if "Sandbox.exe" in running:
        raise SystemExit("Sandbox.exe is running: close the editor first (start_job does that, with allow_editor_close=true)")
    if "GameLauncher.exe" in subprocess.run(["tasklist", "/FI", "IMAGENAME eq GameLauncher.exe"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout:
        raise SystemExit("GameLauncher.exe is running: close it first (this probe does not kill games it did not start)")
    build = "release" if os.path.basename(os.path.dirname(N.LAUNCHER)).lower() == "win_x64_release" else "profile"       # from the binaries the harness launches, not from the folder name
    print("build %s: %s" % (build, N.LAUNCHER), flush=True)
    # command-line variables: three that tell whether `+name value` takes effect in this build (a plain one with a changed value, one that is cheat/const,
    # and the window size the harness always passes). A Release build only applies whitelisted ones (CVARS_WHITELIST, an empty list unless the build adds one).
    temp = write_temp_cfgs()
    startargs = [("ai_AmbientFireQuota", "7"), ("e_Brushes", "0"), ("r_Width", "960"), ("r_Height", "540")]
    argv = [N.LAUNCHER, "-claude_test", "+r_Fullscreen", "0", "+s_IgnoreWindowFocus", "1"] + [a for n, v in startargs for a in ("+" + n, v)]
    p = subprocess.Popen(argv,
                         cwd=os.path.dirname(N.LAUNCHER), creationflags=0x8 | 0x200, stdin=subprocess.DEVNULL)
    rows = []
    try:
        ready = False
        for _ in range(90):
            time.sleep(3)
            try:
                o = N.log_size(); N.send("ash_status", 5); time.sleep(0.5)
                if "status: ticks" in N.log_since(o):
                    ready = True
                    break
            except Exception:                                     # noqa: BLE001 - the game is still starting
                pass
        if not ready:
            raise SystemExit("the game did not answer within 270 s")
        applied = 0
        for name, wanted in startargs:
            got = None
            try:
                got = value_of(N.send("bridge.cvar_get " + name, 10))
            except Exception as exc:                              # noqa: BLE001
                got = "error: %s" % exc
            ok = number(got) is not None and abs(number(got) - number(wanted)) < 1e-6
            applied += ok
            print("STARTARG +%-22s wanted %-6s engine says %-8s -> %s" % (name, wanted, got, "applied" if ok else "IGNORED"), flush=True)
        print("STARTARGS %d of %d command-line variables took effect in the %s build" % (applied, len(startargs), build), flush=True)
        for _path, label, items in temp:
            for name, want in items:
                reply = N.send("bridge.cvar_get " + name, 10)
                got = value_of(reply) if reply.get("ok", True) else "no such variable"
                ok = number(got) is not None and abs(number(got) - number(want)) < 1e-6
                print("TEMPCFG %-26s %-34s wanted %-6s engine says %-8s -> %s" % (label, name, want, got, "took effect" if ok else "IGNORED"), flush=True)
        # the package's own .cfg files go through the same whitelist: report what each line did (a line whose value equals the variable's default proves nothing, and says so)
        for cfg in ("system.cfg", "game.cfg", "user.cfg"):
            path = os.path.join(BUILD, cfg)
            if not os.path.isfile(path) or any(path == t[0] for t in temp):          # not the throwaway files this run wrote itself
                continue
            for raw in open(path, encoding="utf-8", errors="replace"):
                line = raw.split("--")[0].strip()
                m = re.match(r"^([A-Za-z_]\w+)\s*=\s*(-?[0-9.]+)$", line)
                if not m:
                    continue
                name, want = m.groups()
                entry = C.get(name)
                try:
                    reply = N.send("bridge.cvar_get " + name, 10)
                    got = value_of(reply) if reply.get("ok", True) else "no such variable"
                except Exception as exc:                          # noqa: BLE001
                    got = "error: %s" % exc
                ok = number(got) is not None and abs(number(got) - number(want)) < 1e-6
                print("CFG %-11s %-24s wanted %-6s engine says %-16s -> %s" % (cfg, name, want, got, "took effect" if ok else ("not in this build" if got == "no such variable" else "IGNORED")), flush=True)
        for name, wanted in probes:
            rec = probe_one(name, wanted)
            rec["build"] = build
            rows.append(rec)
            print("PROBE %-34s wanted %-6s before %-8s after %-8s -> %s" % (name, wanted, rec["before"], rec["after"], rec["state"]), flush=True)
    finally:
        try:
            N.send("quit", 5)
        except Exception:                                         # noqa: BLE001
            pass
        time.sleep(3)
        if p.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, stdin=subprocess.DEVNULL)       # only the launcher this run started
        for path, label, _items in temp:                          # only what this run created
            try:
                os.remove(path)
            except OSError as exc:
                print("TEMPCFG could not remove %s: %s" % (path, exc), flush=True)
            userdir = os.path.dirname(path)
            if os.path.basename(userdir) == "user" and not os.listdir(userdir):
                os.rmdir(userdir)
    cmp = C.compare_probe(rows)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"build": build, "rows": rows, "comparison": cmp}, open(OUT, "w", encoding="utf-8"), indent=1)
    for d in cmp["disagreements"]:
        print("DISAGREE %(name)s flags=%(flags)s predicted %(predicted)s, measured %(measured)s (before %(before)s after %(after)s)" % d, flush=True)
    parts = ", ".join("%s %d/%d" % (k, v[0], v[1]) for k, v in sorted(cmp["by_class"].items()))
    if cmp["undecided"]["present"] or cmp["undecided"]["absent"]:
        print("UNDECIDED the catalog could not tell whether these exist in Release: present %s; absent %s" % (cmp["undecided"]["present"], cmp["undecided"]["absent"]), flush=True)
        parts += "; undecided %d present, %d absent (not scored)" % (len(cmp["undecided"]["present"]), len(cmp["undecided"]["absent"]))
    errors = sum(1 for r in rows if r["state"] == "error")
    print("RESULT cvar_probe %s: %d probed, catalog predicted %d of %d (%s)%s" % (build, len(rows), cmp["agree"], cmp["total"], parts,
                                                                        ", %d ERRORS" % errors if errors else ""), flush=True)


if __name__ == "__main__":
    main()
