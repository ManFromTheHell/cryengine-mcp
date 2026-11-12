#!/usr/bin/env python3
"""What `ashvale_ci.py --build` runs.

    rebuild_game.py [profile|release]       default profile (the DLL the open editor plays)

Compiles Game.dll through build_game.py (cmake under the build lock) and prints `build ok` ONLY when it compiled AND linked. It never closes, starts or restarts the editor: a Profile DLL is
held by the open editor, so with the editor open the link is refused (LNK1168) and this says so and fails, instead of reporting a build that did not happen. To build Profile, close the
editor first (safe_close.py close) or use the `package` job with build=true, which does that in the right order.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import build_game  # noqa: E402


def main():
    config = (sys.argv[1] if len(sys.argv) > 1 else "profile").lower()
    if config not in ("profile", "release"):
        print(__doc__)
        return 2
    s = build_game.build(config)
    for e in s["errors"]:
        print("   " + e)
    print("%s (%s, %.0f s, %d error(s), %d warning(s))" % (s["verdict"], config, s["seconds"], s["n_errors"], s["n_warnings"]))
    if s["returncode"] == 0:
        print("build ok")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
