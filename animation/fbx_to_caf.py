#!/usr/bin/env python3
"""Convert an animation-only FBX (e.g. the Launcher's crysis-animation-assets, Bip01 rig) into a CRYENGINE .caf
with the Resource Compiler, without Sandbox's importer dialog.

    fbx_to_caf.py <clip.FBX> <output folder> [--motion-node Locator_Locomotion] [--take NAME]

Why each step exists (all measured on relaxed_tac_run_nw_fwd_fast_3p_01.FBX, RC 1.1.8.7):
  * rc.exe refuses a raw .fbx ("FbxConverter expected .json or .cgf"): it takes an import-request JSON and
    /overwriteextension=fbx. The JSON is FLAT (source_filename, output_ext, animation{...}) -- a {"metadata": ...}
    or {"request": ...} wrapper is silently ignored and RC writes a static .cgf instead.
  * The FBX SDK inside RC refuses files with no skinned mesh ("FBX Scene does not have any skinned mesh",
    CryFbxScene.cpp PrepareBones). These clips have none, so fbx_binary.add_proxy_skin binds one HUD quad to
    Bip01 Pelvis first. The curves are untouched.
  * Under Git Bash, /options are rewritten into paths -- call rc.exe from Python (as here) or set MSYS_NO_PATHCONV=1.
  * motionNodePath Locator_Locomotion gives the clip its root motion (the Crysis locomotion rig's locator).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import fbx_binary  # noqa: E402

# CRYENGINE_ENGINE: the engine folder (the one holding Tools/rc/rc.exe); the default is the Launcher 5.7.1 LTS install
_ENGINE = os.environ.get("CRYENGINE_ENGINE") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1")
RC = os.path.join(_ENGINE, "Tools", "rc", "rc.exe")


def convert(source, out_dir, motion_node="Locator_Locomotion", take="", rc=RC, start_frame=-1, end_frame=-1):
    name = os.path.splitext(os.path.basename(source))[0]
    work = tempfile.mkdtemp(prefix="fbx2caf_")
    try:
        version, nodes, footer = fbx_binary.read(source)
        has_skin = any(o.name == "Deformer" for o in next(n for n in nodes if n.name == "Objects").children)
        fbx_path = os.path.join(work, name + ".fbx")
        if not has_skin:
            fbx_binary.add_proxy_skin(nodes)
            fbx_binary.write(fbx_path, version, nodes, footer)
        else:
            shutil.copyfile(source, fbx_path)
        request = {
            "source_filename": fbx_path.replace("\\", "/"),
            "output_ext": "caf",
            "unit_size": "file",
            "scale": 1.0,
            "animation": {"name": take, "motionNodePath": [motion_node] if motion_node else [],
                          "startFrame": start_frame, "endFrame": end_frame},
        }
        json_path = os.path.join(work, name + ".json")
        with open(json_path, "w") as f:
            json.dump(request, f)
        proc = subprocess.run([rc, json_path, "/overwriteextension=fbx", "/refresh"],
                              capture_output=True, text=True, errors="replace", timeout=1800)
        errors = [l for l in proc.stdout.splitlines() if l.startswith("[E]")]
        caf = os.path.join(work, name + ".caf")
        if proc.returncode != 0 or not os.path.isfile(caf):
            raise RuntimeError("rc.exe failed (exit %d): %s" % (proc.returncode, " | ".join(errors) or proc.stdout[-800:]))
        os.makedirs(out_dir, exist_ok=True)
        target = os.path.join(out_dir, name + ".caf")
        shutil.copyfile(caf, target)
        return target, os.path.getsize(target), not has_skin
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("source")
    p.add_argument("out_dir")
    p.add_argument("--motion-node", default="Locator_Locomotion")
    p.add_argument("--take", default="")
    p.add_argument("--start-frame", type=int, default=-1, help="first frame of the clip (-1: the take's own start); a take whose time span is longer than its keys is otherwise compiled with the padding")
    p.add_argument("--end-frame", type=int, default=-1)
    a = p.parse_args()
    target, size, proxied = convert(a.source, a.out_dir, a.motion_node, a.take, start_frame=a.start_frame, end_frame=a.end_frame)
    print("wrote %s (%d bytes)%s" % (target, size, ", proxy skin added" if proxied else ""))


if __name__ == "__main__":
    main()
