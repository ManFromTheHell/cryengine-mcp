"""One command that says whether Ashvale still works: build, play it through, compare to a
baseline, write a report.

    python examples/ashvale/ashvale_ci.py                 # play and compare against the baseline
    python examples/ashvale/ashvale_ci.py --build         # rebuild Game.dll first
    python examples/ashvale/ashvale_ci.py --accept        # play, SHOW the diff against the baseline and the digest of this run; the baseline is NOT changed yet
    python examples/ashvale/ashvale_ci.py --confirm-baseline <digest>   # promote the run that --accept showed (the digest must match); no playing
    python examples/ashvale/ashvale_ci.py --inject-fault  # prove the harness can fail (positive control)

Exit code 0 when the run matches the baseline, 1 when it does not. Every regression this
project hit by hand -- two swords, a player stuck at 0 health, stale screenshots, a crash on the
second entry into game mode -- shows up in one of the three sections below.

The baseline lives in ashvale_baseline.json next to this file. It changes only through a SHOWN diff: --accept plays, prints every REGRESSED / FIXED / NEW / MISSING /
WORSE line against the old baseline and stores the run as ashvale_baseline.pending.json with a digest; --confirm-baseline <digest> then promotes exactly that run. The first baseline
(none exists yet) is written at once. --build runs rebuild_game.py (this folder), which never touches the editor. A "0 failures" run is not proof
on its own, which is why --inject-fault exists: it breaks the story on purpose and the harness
must report it.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import json
import os
import re
import subprocess
import sys
import time

import base64

import bridge_py


class B:
    """build_ashvale.py read a heightmap from a scratch folder of a long-gone session at import time, so this harness could not even start; it needed only py()"""
    @staticmethod
    def py(source):
        out = bridge_py.send("bridge.execute_python.b64 " + base64.b64encode(source.encode()).decode()).get("output", "")
        try:
            log = json.loads(out).get("log", "")
        except Exception:                                 # noqa: BLE001
            log = out
        if "Traceback" in log:
            raise RuntimeError(log)
        return log

HERE = _paths.ROOT
BASELINE = os.path.join(HERE, "ashvale_baseline.json")
PLAYTEST = _paths.script("ashvale_playtest.py")
PENDING = os.path.join(HERE, "ashvale_baseline.pending.json")
REBUILD = _paths.script("rebuild_game.py")
EDITOR_LOG = os.path.join(__import__("project_root").PROJECT, "editor.log")
# Set CRYENGINE_NPC_LOG to the game's NPC log (default: claude_npc.log in the active project folder).
NPC_LOG = os.environ.get("CRYENGINE_NPC_LOG") or os.path.join(__import__("project_root").PROJECT, "claude_npc.log")

# Log lines that mean something is wrong. Counted per run and compared with the baseline, so a
# NEW one fails the build even when every gameplay check still passes.
WATCHED = {
    "invalid_matrix": "Invalid matrix ignored",
    "navmesh_missing": "couldn't find NavMesh triangle",
    "path_failed": "arrived at waypoint",          # counted, then filtered for "(path failed)"
    "collision_unregister": "could not be unregistered from the Collision Avoidance",
    "animation_context": "already has a processing context",
}


def wait_for_level(seconds=240):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if "ashvale" in B.py("import sandbox.general as g\nprint(g.get_current_level_name())"):
                return True
        except Exception:
            pass
        time.sleep(4)
    return False


def run_playtest(outdir):
    os.makedirs(outdir, exist_ok=True)
    proc = subprocess.run([sys.executable, PLAYTEST, outdir], capture_output=True, text=True, timeout=2400)
    out = proc.stdout + proc.stderr
    checks = {}
    for line in out.splitlines():
        # The machine line (CHECK|PASS|label): the readable one carries detail text, which made
        # the names differ between runs and every check look "MISSING".
        if line.startswith("CHECK|"):
            _, verdict, label = line.split("|", 2)
            checks[label.strip()] = (verdict == "PASS")
    deaths = re.search(r"player deaths (\d+)", out)
    return checks, int(deaths.group(1)) if deaths else None, out


def log_counts(editor_offset, npc_offset):
    counts = {}
    editor = open(EDITOR_LOG, "rb").read()[editor_offset:].decode("utf-8", "replace") if os.path.exists(EDITOR_LOG) else ""
    npc = open(NPC_LOG, "rb").read()[npc_offset:].decode("utf-8", "replace") if os.path.exists(NPC_LOG) else ""
    both = editor + npc
    for key, needle in WATCHED.items():
        if key == "path_failed":
            counts[key] = both.count("(path failed)")
        else:
            counts[key] = both.count(needle)
    counts["crashes"] = both.count("[Crashed] ThreadName")
    return counts


def compare(run, base):
    """Returns (ok, lines). A check that passed in the baseline must still pass; a watched log
    line may not increase."""
    lines = []
    ok = True
    for name, passed in sorted(run["checks"].items()):
        was = base["checks"].get(name)
        if was is None:
            lines.append("  NEW CHECK  %-38s %s" % (name, "pass" if passed else "FAIL"))
            ok = ok and passed
        elif was and not passed:
            lines.append("  REGRESSED  %-38s passed before, fails now" % name)
            ok = False
        elif not was and passed:
            lines.append("  FIXED      %-38s fails in the baseline, passes now" % name)
    missing = set(base["checks"]) - set(run["checks"])
    for name in sorted(missing):
        lines.append("  MISSING    %-38s the run never reported it" % name)
        ok = False
    for key, count in sorted(run["logs"].items()):
        was = base["logs"].get(key, 0)
        if count > was:
            lines.append("  WORSE LOG  %-38s %d -> %d" % (key, was, count))
            ok = False
        elif count < was:
            lines.append("  BETTER LOG %-38s %d -> %d" % (key, was, count))
    return ok, lines


def run_digest(run):
    """a short digest of what a baseline would contain (the checks, the watched log counts, the deaths): the number --confirm-baseline must quote"""
    import hashlib
    body = json.dumps({"checks": run["checks"], "logs": run["logs"], "deaths": run["deaths"]}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:12]


def confirm_baseline(digest, pending=PENDING, baseline=BASELINE):
    """promote the pending run to the baseline when `digest` is its digest -> (ok, text)"""
    if not os.path.exists(pending):
        return False, "no pending run: play first with --accept"
    with open(pending, encoding="utf-8") as f:
        run = json.load(f)
    if run_digest(run) != digest:
        return False, "the pending run's digest is %s, not %s: the diff you looked at is not this run" % (run_digest(run), digest)
    os.replace(pending, baseline)
    return True, "baseline replaced by the run of %s" % run.get("when", "?")


_FAULT_ACTIVE = False
_FAULT_UNDO = "import sandbox.object as object\nobject.rename_object('BlackWat_FAULT', 'BlackWat')\nprint('fault removed')"


def main():
    """Run the harness; whatever happens, an injected fault is removed from the level again."""
    global _FAULT_ACTIVE
    try:
        return _main()
    finally:
        if _FAULT_ACTIVE:
            try:
                print(B.py(_FAULT_UNDO).strip())
                _FAULT_ACTIVE = False
            except Exception as exc:                      # noqa: BLE001
                print("WARNING: could not remove the injected fault (%s): rename BlackWat_FAULT back to BlackWat by hand" % exc)


def _main():
    global _FAULT_ACTIVE
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="rebuild Game.dll first")
    ap.add_argument("--accept", action="store_true", help="play, show the diff against the baseline, store the run as PENDING with a digest (the baseline does not change)")
    ap.add_argument("--confirm-baseline", metavar="DIGEST", help="promote the pending run whose digest is DIGEST to the baseline (no playing)")
    ap.add_argument("--inject-fault", action="store_true", help="break the story on purpose, to prove the harness fails")
    ap.add_argument("--out", default=os.path.join(HERE, "ci_runs", time.strftime("%Y%m%d_%H%M%S")))
    args = ap.parse_args()

    if args.confirm_baseline:
        ok, text = confirm_baseline(args.confirm_baseline)
        print(text)
        return 0 if ok else 1
    started = time.time()
    if args.build:
        print("== building Game.dll")
        r = subprocess.run([sys.executable, REBUILD], capture_output=True, text=True, timeout=1800)
        print("  " + (r.stdout.strip().splitlines() or ["(no output)"])[-1])
        if "build ok" not in r.stdout:
            print("BUILD FAILED\n" + r.stdout[-2000:])
            return 1

    if not wait_for_level():
        print("the editor never came back with the level open")
        return 1

    editor_offset = os.path.getsize(EDITOR_LOG) if os.path.exists(EDITOR_LOG) else 0
    npc_offset = os.path.getsize(NPC_LOG) if os.path.exists(NPC_LOG) else 0

    if args.inject_fault and not os.path.exists(BASELINE):
        print("refused: no baseline yet; run once without --inject-fault first")
        return 1
    if args.inject_fault:
        # The fault has to survive the playtest's own ash_newgame, which clears every flag --
        # setting wat_dead beforehand was wiped and the harness reported OK, which would have
        # made this control worthless. Renaming an NPC is level state, so it survives: the
        # kill sequence can no longer find Black Wat.
        print("== injecting a fault (Black Wat renamed out of the way)")
        _FAULT_ACTIVE = True
        print(B.py("import sandbox.object as object\nobject.rename_object('BlackWat', 'BlackWat_FAULT')\nprint('renamed')").strip())

    print("== playing")
    checks, deaths, raw = run_playtest(args.out)
    logs = log_counts(editor_offset, npc_offset)
    run = {"checks": checks, "logs": logs, "deaths": deaths,
           "seconds": round(time.time() - started, 1), "when": time.strftime("%Y-%m-%d %H:%M:%S")}

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "run.json"), "w", encoding="utf-8") as f:
        json.dump(run, f, indent=1)
    with open(os.path.join(args.out, "playtest.txt"), "w", encoding="utf-8") as f:
        f.write(raw)

    passed = sum(1 for v in checks.values() if v)
    print("   %d of %d checks pass, %s player deaths, %.0f s" % (passed, len(checks), deaths, run["seconds"]))
    print("   watched log lines: " + ", ".join("%s=%d" % kv for kv in sorted(logs.items())))

    if not os.path.exists(BASELINE):
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(run, f, indent=1)
        print("== first baseline stored (%s)" % BASELINE)
        return 0
    if args.accept:
        if args.inject_fault:
            print("refused: a run with an injected fault can never become the baseline")
            return 1
        base = json.load(open(BASELINE, encoding="utf-8"))
        _, lines = compare(run, base)
        print("== the baseline of %s would change like this:" % base.get("when", "?"))
        print("\n".join(lines) if lines else "   no difference")
        with open(PENDING, "w", encoding="utf-8") as f:
            json.dump(run, f, indent=1)
        print("== pending run stored; if the diff above is what you want:  python examples/ashvale/ashvale_ci.py --confirm-baseline %s" % run_digest(run))
        return 0

    if args.inject_fault:
        print(B.py(_FAULT_UNDO).strip())
        _FAULT_ACTIVE = False

    base = json.load(open(BASELINE, encoding="utf-8"))
    ok, lines = compare(run, base)
    print("== against the baseline of %s" % base.get("when", "?"))
    print("\n".join(lines) if lines else "   identical")
    print("RESULT: %s" % ("OK" if ok else "REGRESSION"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
