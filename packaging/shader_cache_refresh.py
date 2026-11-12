#!/usr/bin/env python3
"""The whole shader-cache procedure in one command -- the manual's "Releasing a
Project" steps with this project's corrections:

  1. Assets/GameShaders.pak rebuilt from Assets/GameShaders (RC never copies .cfx/.cfi/.ext; a Profile build
     reads project shaders -- including the Water.cfx override -- from it).
  2. A fresh Profile package (Crytek's packager) played through with shader_tour.py --rsc while the Remote
     Shader Compiler records every global-cache miss (its ShaderList_PC.txt is backed up and started empty).
  3. shader_cache_gen.py <list> --seed-stock (the user cache starts as the engine's own D3D11 cache, so every
     container comes out stock + ours).
  4. Tools/PakShaders.bat D3D11 <stage> with <stage>\\Engine seeded from FRESH stock paks (<stage> = CRYENGINE_SHADER_STAGE, default <workspace>\\ShaderPak).
  5. Audit against the stock paks: no non-Water container lost or smaller, Vulkan byte-identical, and every
     project-overridden shader's .cfxb built from the project source (source CRC in its header).

    shader_cache_refresh.py [--skip-record <ShaderList_PC.txt>] [--append] [--only-undercroft] [--skip-check name=reason] [--accept-count-change] [--kill-unresponsive]

--append            keep the recorded list (the Remote Shader Compiler adds only what the route newly asks for) instead of starting it empty
--skip-check        a preflight check overridden on purpose (preflight.py lists them); the record build is checked exactly like a package
--only-undercroft   with --append: the route is only chapter II (the hatch, the dungeon, the vault, the way back), a few minutes instead of a whole tour

Leaves the three paks in <stage>\\Engine; copying them into a build's engine/ folder is the last step
(package_project.py builds do not include them). Sandbox is closed for the run through safe_close (saved first; refused when the level is dirty; watchdog lock held, display
NOT kept awake: no display keep-awake request is made); the watchdog reopens it afterwards.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import zipfile
import zlib

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
# Machine-specific locations, each overridable by an environment variable:
#   CRYENGINE_ENGINE         the engine folder (default: the Launcher 5.7.1 LTS install)
#   CRYENGINE_RSC_DIR        the RemoteShaderCompiler folder (default <engine>\Tools\RemoteShaderCompiler)
#   CRYENGINE_SHADER_STAGE   where the three paks are staged (default <workspace>\ShaderPak; the workspace is state/ unless CRYENGINE_WORKSPACE says otherwise)
#   CRYENGINE_RECORD_BUILD   the package used to record shader requests (default <projects folder>\Ashvale_shader_record)
#   CRYENGINE_WORKSPACE      folder of the shared watchdog lock (CRYENGINE_STATE_DIR / CLAUDE_STATE_DIR are read too; default state/)
ENGINE = os.environ.get("CRYENGINE_ENGINE") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1")
RSC = os.environ.get("CRYENGINE_RSC_DIR") or os.path.join(ENGINE, "Tools", "RemoteShaderCompiler")
STAGE = os.environ.get("CRYENGINE_SHADER_STAGE") or _paths.workspace("ShaderPak")
RECORD_BUILD = os.environ.get("CRYENGINE_RECORD_BUILD") or os.path.join(os.path.dirname(PROJECT), "Ashvale_shader_record")
LOCK = _paths.workspace("claude_deploy_in_progress")
PY = sys.executable
stop = threading.Event()
CLOSED = {}


def keep_lock():
    while not stop.is_set():
        os.makedirs(os.path.dirname(LOCK), exist_ok=True)
        with open(LOCK, "w") as f:
            f.write("shader_cache_refresh\n")
        stop.wait(10)          # shader_cache_gen.py deletes the lock when it finishes; put it back quickly


def step(msg):
    print("\n== " + msg, flush=True)


def run(args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, **kw)
    return r


def rebuild_game_shaders_pak():
    step("1. Assets/GameShaders.pak from Assets/GameShaders")
    pak = os.path.join(PROJECT, "Assets", "GameShaders.pak")
    tmp = pak + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)
    r = run([os.path.join(ENGINE, "Tools", "7za.exe"), "a", "-tzip", "-mx0", tmp, "GameShaders"],
            cwd=os.path.join(PROJECT, "Assets"))
    if r.returncode != 0:
        sys.exit("7za failed: " + r.stdout[-400:])
    shutil.move(tmp, pak)
    with zipfile.ZipFile(pak) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
    print("   %d files: %s" % (len(names), ", ".join(os.path.basename(n) for n in names)))


def record():
    step("2. record: fresh Profile package + shader_tour.py --rsc")
    lst = os.path.join(RSC, "Cache", "Assets", "ShaderList_PC.txt")
    if os.path.exists(lst):
        backup = os.path.join(_paths.workspace("list_backups"), "ShaderList_PC_%s.txt" % time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        if "--append" in sys.argv:
            shutil.copyfile(lst, backup)          # the recorded list stays: the Remote Shader Compiler adds only what this route newly asks for
            print("   --append: the recorded list is KEPT (copy at %s)" % backup)
        else:
            shutil.move(lst, backup)
            print("   previous list kept as", backup)
    if "crysCompileServer.exe".lower() not in run(["tasklist"]).stdout.lower():
        subprocess.Popen([os.path.join(RSC, "CrySCompileServer.exe")], cwd=RSC, creationflags=0x8 | 0x200)
        time.sleep(3)
        print("   Remote Shader Compiler started")
    # the SAME checks as a real package: without them a shader list could be recorded from a build that lacked its textures
    import package_clean
    import preflight
    skips = preflight.parse_skips([sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--skip-check" and i + 1 < len(sys.argv)])
    preflight.require(preflight.run("profile", "pre", skips=skips), print)
    shutil.rmtree(os.path.join(PROJECT, "rc_out_PC_pure"), ignore_errors=True)       # never let a stale converted file ride along
    rc_since = time.time()
    shutil.rmtree(RECORD_BUILD, ignore_errors=True)
    r = run([PY, _paths.script("package_project.py"), os.path.join(PROJECT, "Game.cryproject"),
             "--config", "win_x64:Profile", "--out", RECORD_BUILD], timeout=3600)
    packaged = os.path.exists(os.path.join(RECORD_BUILD, "bin", "win_x64", "Game.dll"))
    print("   packaged:", packaged)
    if not packaged:
        sys.exit("packaging failed: " + (r.stdout + r.stderr)[-800:])
    package_clean.clean(RECORD_BUILD, "profile")
    preflight.require(preflight.run("profile", "post", dest=RECORD_BUILD, skips=skips, rc_since=rc_since), print)
    tour = [PY, "-u", _paths.script("shader_tour.py"), RECORD_BUILD, "--rsc", "--label", "record"] + (["--only-undercroft"] if "--only-undercroft" in sys.argv else [])
    r = run(tour, timeout=2400)
    print("   " + "\n   ".join(l for l in r.stdout.splitlines() if l.startswith(("RESULT", "misses by"))))
    with open(lst, encoding="latin-1") as f:
        n = sum(1 for l in f if l.strip())
    print("   recorded %d lines -> %s" % (n, lst))
    return lst


def generate(lst):
    step("3. ShaderCacheGen, seeded with the stock cache")
    r = run([PY, "-u", _paths.script("shader_cache_gen.py"), lst, "--seed-stock"], timeout=3600)
    print("   " + "\n   ".join(l for l in r.stdout.splitlines()[-8:]))
    log_path = os.path.join(ENGINE, "shadercachegen.log")
    if not os.path.isfile(log_path):
        raise SystemExit("ShaderCacheGen wrote no log at %s: check CRYENGINE_ENGINE and the output above" % log_path)
    log = open(log_path, encoding="latin-1").read()
    summary = [l.strip() for l in log.splitlines() if "Combinations: (" in l or "X4500" in l or "Couldn't compile" in l]
    print("   log: " + "\n   log: ".join(summary[-6:]))


def pack():
    step("4. PakShaders.bat D3D11 %s" % STAGE)
    old = STAGE + "_old_" + time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(old, exist_ok=True)
    for d in ("USER", "USER1", "USER2", "Engine"):
        if os.path.isdir(os.path.join(STAGE, d)):
            shutil.move(os.path.join(STAGE, d), os.path.join(old, d))
    os.makedirs(os.path.join(STAGE, "Engine"))
    for src, dst in (("shadercache.pak", "ShaderCache.pak"), ("shadercachestartup.pak", "ShaderCacheStartup.pak"),
                     ("shadersbin.pak", "ShadersBin.pak")):
        shutil.copyfile(os.path.join(ENGINE, "Engine", src), os.path.join(STAGE, "Engine", dst))
    shutil.copytree(os.path.join(PROJECT, "user", "shaders", "cache", "d3d11"),
                    os.path.join(STAGE, "USER", "shaders", "cache", "D3D11"))
    for tool in ("7za.exe", "7z.dll"):
        if not os.path.exists(os.path.join(STAGE, "Tools", tool)):
            os.makedirs(os.path.join(STAGE, "Tools"), exist_ok=True)
            shutil.copyfile(os.path.join(ENGINE, "Tools", tool), os.path.join(STAGE, "Tools", tool))
    r = subprocess.run(["cmd", "/c", os.path.join(ENGINE, "Tools", "PakShaders.bat"), "D3D11", STAGE],
                       cwd=STAGE, capture_output=True, text=True)
    print("   'Everything is Ok' x%d" % r.stdout.count("Everything is Ok"))


def cfxb_source_crc(data):
    return struct.unpack_from("<I", data, 24)[0]     # SShaderBinHeader: magic, CRC32, version, ..., m_nSourceCRC32


def audit():
    step("5. check against the stock paks")
    overrides = {}
    cryfx = os.path.join(PROJECT, "Assets", "GameShaders", "HWScripts", "CryFX")
    for f in (os.listdir(cryfx) if os.path.isdir(cryfx) else []):      # a project without its own CryFX overrides has none to audit
        if f.lower().endswith(".cfx"):
            overrides[f.lower()[:-4]] = zlib.crc32(open(os.path.join(cryfx, f), "rb").read())
    ok = True
    for pak in ("ShaderCache.pak", "ShaderCacheStartup.pak", "ShadersBin.pak"):
        old = {i.filename.lower().replace("\\", "/"): (i.file_size, i.CRC) for i in
               zipfile.ZipFile(os.path.join(ENGINE, "Engine", pak.lower())).infolist() if not i.is_dir()}
        z = zipfile.ZipFile(os.path.join(STAGE, "Engine", pak))
        new = {i.filename.lower().replace("\\", "/"): (i.file_size, i.CRC) for i in z.infolist() if not i.is_dir()}
        changed_shader = lambda k: any("/%s@" % s in k or k.endswith("/%s.cfxb" % s) for s in overrides) or k.endswith("lookupdata.bin")
        lost = [k for k in old if k not in new]
        smaller = [k for k in old if k in new and new[k][0] < old[k][0] and not changed_shader(k)]
        vulkan = [k for k in old if "/vulkan/" in k and new.get(k) != old[k]]
        grew = sum(1 for k in old if k in new and new[k][0] > old[k][0])
        added = sum(1 for k in new if k not in old)
        print("   %-22s stock %4d new %4d | lost %d, smaller (non-override) %d, vulkan changed %d | grew %d, added %d"
              % (pak, len(old), len(new), len(lost), len(smaller), len(vulkan), grew, added))
        if lost or smaller or vulkan:
            ok = False
            print("      ", lost[:5], smaller[:5], vulkan[:5])
        if pak == "ShadersBin.pak":
            for name, crc in overrides.items():
                entry = next((n for n in z.namelist() if n.lower().replace("\\", "/") == "shaders/cache/d3d11/%s.cfxb" % name), None)
                got = cfxb_source_crc(z.read(entry)) if entry else None
                print("      %s.cfxb source CRC %s, project %s -> %s" % (name, hex(got) if got is not None else "MISSING",
                                                                     hex(crc), "OK" if got == crc else "MISMATCH"))
                ok &= got == crc
    print("\nAUDIT %s" % ("PASS" if ok else "FAIL"))
    return ok


def main():
    threading.Thread(target=keep_lock, daemon=True).start()
    # (no display keep-awake request is made: the display's idle timer is the machine owner's setting)
    try:
        import safe_close                                  # save and close the editor, or refuse (a dirty level, an unknown object count): never taskkill /F an editor
        res = safe_close.close_editor("shader_cache_refresh", hold_minutes=240,
                                      accept_count_change="--accept-count-change" in sys.argv, kill_unresponsive="--kill-unresponsive" in sys.argv)
        CLOSED.update(res)
        if not res["ok"]:
            sys.exit("the editor was not closed, nothing was changed: " + "; ".join(res["refused"] or ["it did not close"]))
        subprocess.run(["taskkill", "/F", "/IM", "GameLauncher.exe"], capture_output=True)       # a stray game window holds the package's files
        time.sleep(5)
        rebuild_game_shaders_pak()
        lst = sys.argv[sys.argv.index("--skip-record") + 1] if "--skip-record" in sys.argv else record()
        generate(lst)
        pack()
        sys.exit(0 if audit() else 1)
    finally:
        stop.set()
        try:
            os.remove(LOCK)
        except OSError:
            pass
        if CLOSED.get("was_running"):                      # only the marker THIS run wrote (final_packages holds its own)
            try:
                import safe_close
                safe_close.release()                       # the watchdog may reopen the editor (and the level that was open)
            except Exception:                              # noqa: BLE001
                pass


if __name__ == "__main__":
    main()
