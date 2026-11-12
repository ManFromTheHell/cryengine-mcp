#!/usr/bin/env python3
"""Compile Game.dll for a configuration and say, in a few lines, what happened. The `build_game` job and `package build=true` run this.

    build_game.py [release|profile] [--json]

It runs game_build_locked.py (cmake --build under a lock, so two builds never write the same output) and folds its output into one verdict:

  BUILD OK / BUILD FAILED / COMPILED, LINK REFUSED / NO LOCK  +  the counts of MSVC errors and warnings and the FIRST errors as `file(line): error C####: text`.

It NEVER closes, starts or restarts the editor. A Profile build writes the DLL the open editor holds, so with the editor open it compiles and then reports LNK1168 (exit 2: a compile
check only); close the editor first (the `package` job does, through safe_close) or build Release, which the editor does not hold. Exit: 0 built and linked, 1 errors, 2 compiled but not
linked, 3 the build lock was not obtained.
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
ERROR = re.compile(r"^(?P<file>.*?)\((?P<line>\d+)(?:,\d+)?\):\s*(?:fatal )?error\s+(?P<code>[A-Z]+\d+):\s*(?P<text>.*?)(?:\s*\[.*\])?$")
WARNING = re.compile(r":\s*warning\s+[A-Z]+\d+:")


def summarize(output, code):
    """-> {"verdict", "errors": [..first 12..], "n_errors", "n_warnings", "returncode"} from game_build_locked's printed lines and exit code"""
    lines = [l.rstrip() for l in output.splitlines()]
    errs = []
    for l in lines:
        m = ERROR.match(l.strip())
        if m:
            errs.append("%s(%s): error %s: %s" % (os.path.basename(m.group("file")), m.group("line"), m.group("code"), m.group("text")[:200]))
        elif re.search(r"\bLNK\d{4}\b.*error|fatal error LNK", l):
            errs.append(l.strip()[:240])
    seen, uniq = set(), []
    for e in errs:
        if e not in seen:
            seen.add(e)
            uniq.append(e)
    verdict = {0: "BUILD OK", 1: "BUILD FAILED", 2: "COMPILED, LINK REFUSED (the editor holds the DLL: a compile check only)", 3: "NO LOCK (another build is running)"}.get(code, "BUILD FAILED (exit %s)" % code)
    return {"verdict": verdict, "returncode": code, "n_errors": len(uniq), "errors": uniq[:12], "n_warnings": sum(1 for l in lines if WARNING.search(l)), "tail": lines[-3:]}


def build(config="release", timeout=1900):
    """Run the locked build -> summary dict (plus "seconds" and "config"). Never raises on a failed build."""
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, _paths.script("game_build_locked.py"), config], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, errors="replace", timeout=timeout, cwd=HERE)
        out, code = r.stdout, r.returncode
    except subprocess.TimeoutExpired as exc:
        out, code = (exc.stdout or "") if isinstance(exc.stdout, str) else "", 1
        out += "\nbuild timed out after %d s" % timeout
    s = summarize(out, code)
    s.update(config=config, seconds=round(time.time() - t0, 1))
    return s


def main():
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    config = (a[0] if a else "release").lower()
    if config not in ("release", "profile"):
        print(__doc__)
        return 2
    s = build(config)
    if "--json" in sys.argv:
        print(json.dumps(s, indent=1))
    else:
        print("%s (%s, %.0f s): %d error(s), %d warning(s)" % (s["verdict"], config, s["seconds"], s["n_errors"], s["n_warnings"]))
        for e in s["errors"]:
            print("   " + e)
        print("RESULT build_game %s: %s" % (config, s["verdict"]))
    return s["returncode"] if s["returncode"] in (0, 1, 2, 3) else 1


if __name__ == "__main__":
    sys.exit(main())
