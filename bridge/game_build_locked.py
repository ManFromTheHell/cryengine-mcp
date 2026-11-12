#!/usr/bin/env python3
"""Compile Game.dll with a LOCK, so several agents/sessions never build at once (two msbuilds of one project corrupt each other's output).

    python bridge/game_build_locked.py [release|profile]        default release

Release writes bin/win_x64_release/Game.dll (the editor does NOT hold that one, so it links); Profile writes bin/win_x64/Game.dll, which the open editor holds: a Profile build then compiles and
reports LNK1168 at the link (a pure compile check). Prints the MSVC errors/warnings of THIS build and the verdict; exit 0 = compiled and linked, 1 = errors, 2 = compiled but the link was refused (Profile with
the editor open), 3 = lock not obtained. Never closes or starts the editor.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import subprocess
import sys
import time

# CMAKE_EXE overrides; otherwise cmake from PATH, otherwise the default install location
CMAKE = os.environ.get("CMAKE_EXE") or shutil.which("cmake") or r"C:\Program Files\CMake\bin\cmake.exe"
PROJECT = __import__("project_root").PROJECT
BUILD = os.path.join(PROJECT, "solutions", "win64")
# GAME_BUILD_LOCK overrides the lock directory (default: <project>/user/game_build.lock)
LOCK = os.environ.get("GAME_BUILD_LOCK") or os.path.join(PROJECT, "user", "game_build.lock")


def main():
    config = {"release": "Release", "profile": "Profile"}.get((sys.argv[1] if len(sys.argv) > 1 else "release").lower())
    if config is None:
        raise SystemExit(__doc__)
    if not os.path.isfile(CMAKE):
        print("cmake not found (%s); install CMake or set CMAKE_EXE" % CMAKE)
        return 1
    try:
        os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    except OSError as e:
        print("cannot create build lock folder: %s" % e)
        return 3
    t0 = time.time()
    while True:
        try:
            os.mkdir(LOCK)
            break
        except FileNotFoundError as e:
            print("cannot create build lock: %s" % e)
            return 3
        except FileExistsError:
            if time.time() - os.path.getmtime(LOCK) > 1800:       # a lock older than 30 minutes belonged to a dead build
                try:
                    os.rmdir(LOCK)
                except OSError:
                    pass
                continue
            if time.time() - t0 > 1500:
                print("could not get the build lock in 25 minutes (another build is running)")
                return 3
            time.sleep(5)
    try:
        r = subprocess.run([CMAKE, "--build", BUILD, "--target", "Game", "--config", config], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", timeout=1500)
        out = r.stdout
        lines = [l for l in out.splitlines() if (": error " in l or ": warning C" in l or "LNK" in l or "fatal error" in l)]
        for l in lines[:60]:
            print(l)
        if "LNK1168" in out and ": error C" not in out and "error C" not in out:
            print("compiled; the link was refused because the editor holds the DLL (a compile check only)")
            return 2
        print("%s build %s in %.0f s" % (config, "OK" if r.returncode == 0 else "FAILED", time.time() - t0))
        return 0 if r.returncode == 0 else 1
    finally:
        try:
            os.rmdir(LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
