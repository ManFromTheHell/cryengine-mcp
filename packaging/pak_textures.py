#!/usr/bin/env python3
"""Put the textures a package is missing into Assets/GameSDKTextures.pak.

Crytek's packager (RC) compiles a plain .dds but refuses a set that is already split (foo.dds, foo.dds.1, foo.dds.1a ...), which is how the GameSDK ships its textures, and it
leaves out some other loose .dds too. `final_packages.package()` finds them after a package is made ("textures missing from the package: N") and stops. The documented fix is to add
those files to Assets/GameSDKTextures.pak with the engine's own 7za (zip, store, paths relative to Assets); this does exactly that.

    python packaging/pak_textures.py [release|profile]     the package folder final_packages names for that configuration (default release)
    python packaging/pak_textures.py --list                only say what would be added

Importing a prop set can bring many such files (one import brought 185 files, 27 MB). Then run the package job again.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import subprocess
import sys

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ENGINE = os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1")
SEVENZA = os.path.join(ENGINE, "Tools", "7za.exe")
PAK = "GameSDKTextures.pak"


def add(missing, assets_dir=os.path.join(PROJECT, "Assets"), pak=PAK, sevenza=SEVENZA, work_dir=HERE):
    """Add the files (paths relative to `assets_dir`) to `assets_dir/pak` with 7za (zip, no compression). -> {added, bytes_before, bytes_after, output}. Nothing else is touched."""
    if not os.path.isfile(sevenza):
        raise RuntimeError("7za.exe not found at %s" % sevenza)
    absent = [m for m in missing if not os.path.isfile(os.path.join(assets_dir, m))]
    if absent:
        raise RuntimeError("not under %s: %s" % (assets_dir, ", ".join(absent[:3])))
    target = os.path.join(assets_dir, pak)
    before = os.path.getsize(target) if os.path.isfile(target) else 0
    if not missing:
        return {"added": 0, "bytes_before": before, "bytes_after": before, "output": ""}
    lst = os.path.join(work_dir, "_pak_textures_list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        f.write("\n".join(missing) + "\n")
    try:
        r = subprocess.run([sevenza, "a", "-tzip", "-mx0", pak, "@" + lst], cwd=assets_dir, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=1800)
    finally:
        if os.path.isfile(lst):
            os.remove(lst)
    if r.returncode != 0 or "Everything is Ok" not in r.stdout:
        raise RuntimeError("7za failed (%d): %s" % (r.returncode, (r.stdout + r.stderr)[-400:]))
    return {"added": len(missing), "bytes_before": before, "bytes_after": os.path.getsize(target), "output": r.stdout[-300:]}


def main():
    sys.path.insert(0, HERE)
    import final_packages as F
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    config = args[0] if args else "release"
    dest = F.BUILDS[config]
    if not os.path.isdir(dest):
        raise SystemExit("no package at %s: run the package job once first (it makes the folder and then lists what is missing)" % dest)
    missing = F.unpackaged_textures(dest)
    print("%d texture file(s) in Assets that the %s package does not contain" % (len(missing), config))
    for m in missing[:12]:
        print("   ", m)
    if "--list" in sys.argv or not missing:
        return 0
    r = add(missing)
    print("added %d files to Assets/%s: %.1f -> %.1f MB. Now run the package job again." % (r["added"], PAK, r["bytes_before"] / 1e6, r["bytes_after"] / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
