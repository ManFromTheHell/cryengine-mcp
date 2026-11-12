#!/usr/bin/env python3
"""Run the manual's shader-cache step for this project, with the corrections the 5.7.1 source requires:

  1. close Sandbox/launchers (a second engine instance uses USER(n), not USER) -- watchdog held off;
  2. back up <project>/user/shaders, then empty (not delete) its cache folders;
  3. copy the Remote Shader Compiler's Cache/Assets/ShaderList_PC.txt to <project>/user/shaders/shaderlist.txt
     (ShaderCache.cpp:242 reads %USER%/shaders/shaderlist.txt, not the Releasing page's USER/ShaderList.txt);
  4. run the engine's own bin/win_x64/ShaderCacheGen.exe with -project (the manual assumes the project sits in
     the engine root) and r_ShaderTarget=D3D11 (the manual's "DX11" is a fatal error, RendererCVars.cpp:777-786);
     r_ShadersUserFolder / r_ShadersUseInstanceLookUpTable are left out: they do not exist in 5.7.1.

    shader_cache_gen.py <ShaderList_PC.txt> [--seed-stock] [--timeout 1800]

The Remote Shader Compiler must be running and Game.cryproject must still point at it (the ShaderCache
Generation page: the process "will not work if you don't use the Remote Shader Compiler").

--seed-stock: before generating, fill the emptied user cache with the engine's own D3D11
cache (shadercache.pak, then shadersbin.pak over it). The engine opens an existing user cache file
read-write and ADDS the new combinations to it (D3DHWShaderCompiling.cpp:2063-2098), so every container
comes out as stock + ours. Without it each container holds only this list's combinations, and
PakShaders' "accumulative" 7za merge then replaces the stock container with the smaller one -- which is
how a merge without it can lose containers' worth of stock shaders.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile

ENGINE = os.environ.get("CRYENGINE_ENGINE_DIR") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1")
PROJECT = __import__("project_root").PROJECT
# CRYENGINE_ENGINE_DIR / CRYENGINE_DEPLOY_LOCK override the engine folder and the watchdog hold-off file (same names as final_packages.py).
LOCK = os.environ.get("CRYENGINE_DEPLOY_LOCK") or _paths.workspace("claude_deploy_in_progress")
LIST = None                                            # sys.argv[1], read in _main (not at import)
TIMEOUT = int(sys.argv[sys.argv.index("--timeout") + 1]) if "--timeout" in sys.argv else 1800
stop = threading.Event()


def keep_lock():
    while not stop.is_set():
        try:
            os.makedirs(os.path.dirname(LOCK), exist_ok=True)
            with open(LOCK, "w") as f:
                f.write("shader_cache_gen\n")
        except OSError:                                    # the folder cannot be created here: no hold-off file then
            pass
        stop.wait(60)


def main():
    threading.Thread(target=keep_lock, daemon=True).start()
    try:
        _main()
    finally:                                               # also when the editor refuses to close or the backup fails: never leave the lock behind
        stop.set()
        try:
            os.remove(LOCK)
        except OSError:
            pass


def _main():
    global LIST
    if len(sys.argv) < 2:
        raise SystemExit("usage: shader_cache_gen.py <ShaderList_PC.txt> [--seed-stock] [--timeout 1800]")
    LIST = sys.argv[1]
    import safe_close                                     # the editor is saved and closed, or this refuses: never taskkill /F Sandbox
    res = safe_close.close_editor("shader_cache_gen", hold_minutes=180)
    if not res["ok"]:
        raise SystemExit("the editor was not closed, nothing was changed: " + "; ".join(res["refused"] or ["it did not close"]))
    for exe in ("GameLauncher.exe", "ShaderCacheGen.exe"):
        subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True)
    time.sleep(4)
    shaders = os.path.join(PROJECT, "user", "shaders")
    backup = os.path.join(PROJECT, "user_shaders_backup_%s" % time.strftime("%Y%m%d_%H%M%S"))
    if os.path.isdir(shaders):
        shutil.copytree(shaders, backup)
        print("backed up", shaders, "->", backup)
    cache = os.path.join(shaders, "cache")
    os.makedirs(cache, exist_ok=True)
    for root, dirs, files in os.walk(cache):
        for f in files:
            os.remove(os.path.join(root, f))
    if "--seed-stock" in sys.argv:
        seeded = 0
        for pak in ("shadercache.pak", "shadersbin.pak"):
            with zipfile.ZipFile(os.path.join(ENGINE, "Engine", pak)) as z:
                for info in z.infolist():
                    name = info.filename.replace("\\", "/").lower()
                    if info.is_dir() or not name.startswith("shaders/cache/d3d11/"):
                        continue
                    dst = os.path.join(cache, *name[len("shaders/cache/"):].split("/"))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with z.open(info) as src, open(dst, "wb") as out:
                        shutil.copyfileobj(src, out)
                    seeded += 1
        print("seeded the user cache with %d stock D3D11 files" % seeded)
    shutil.copyfile(LIST, os.path.join(shaders, "shaderlist.txt"))
    print("shaderlist.txt: %d lines" % sum(1 for _ in open(os.path.join(shaders, "shaderlist.txt"), encoding="latin-1")))
    # Crytek's own pattern (Code/Tools/ShaderCacheGen/GenerateShaderCacheTemplate.cryproject): a separate
    # project file for cache generation -- r_multithreaded 0 (also in the manual's Step 3 system.cfg), remote
    # compiling on, and NO plugins (Game.dll is not needed to compile shaders). The first run with
    # Game.cryproject compiled 0 of 71 combinations and exited with 0xC0000005.
    cmd = [os.path.join(ENGINE, "bin", "win_x64", "ShaderCacheGen.exe"),
           "-project", os.path.join(PROJECT, "GenerateShaderCache.cryproject"), "-noprompt", "-anygpu"]
    print("running:", " ".join(cmd))
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=PROJECT, timeout=TIMEOUT)
        print("ShaderCacheGen exit code %s after %.0f s" % (p.returncode, time.time() - t0))
    except subprocess.TimeoutExpired:
        print("ShaderCacheGen still running after %d s -- killed" % TIMEOUT)
        subprocess.run(["taskkill", "/F", "/IM", "ShaderCacheGen.exe"], capture_output=True)
    finally:
        stop.set()
        try:
            os.remove(LOCK)
        except OSError:
            pass
        if res["was_running"]:
            safe_close.release()
    n = sum(len(f) for _, _, f in os.walk(cache))
    big = sum(1 for r, _, fs in os.walk(cache) for f in fs if os.path.getsize(os.path.join(r, f)) > 16)
    print("user cache now: %d files, %d larger than 16 bytes" % (n, big))
    for name in ("ashillum.cfxb", "ashflat.cfxb", "ashzpass.cfib", "lookupdata.bin"):
        path = os.path.join(cache, "d3d11", name)
        print("  %-16s %s" % (name, os.path.getsize(path) if os.path.exists(path) else "MISSING"))


if __name__ == "__main__":
    main()
