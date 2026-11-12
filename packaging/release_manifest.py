#!/usr/bin/env python3
"""Versioned packages, the version stamp, and the release verdict.

`package()` keeps the previous good package until the new one is proven; each package records which build it is and that it passed the gate (a stale gate forces a rerun).

    ../Ashvale_staging_<config>/                  the packager's output while it runs (the previous good package is untouched)
    ../Ashvale_<yyyymmdd>_<hash8>/                a finished Release package; the hash is of its SHIPPED files (Assets/, bin/, engine/, config.pak, system.cfg)
    ../Ashvale_profile_<yyyymmdd>_<hash8>/        a Profile package
    ../Ashvale_<id>_symbols/                      Game.dll, the matching Game.pdb and modules.json (package_symbols.archive)
    ../Ashvale_final_release  (junction)          the PROMOTED build: every script that runs a package by that name keeps working; same for Ashvale_final_profile
    <package>/ashvale_version.txt                 date, config, package hash, Game.dll sha, shader pak shas, group digests, overrides used
    <package>/RELEASE_OK.json                     written ONLY by a run of `final_packages.py --all` that passed every group on exactly this package hash, with the project unchanged during
                                                  the run and no preflight override. A package without it has not passed the gate.

The first promotion finds a real folder under the old name: it is RENAMED to Ashvale_legacy_<config>_<time> (never deleted by this code) and the junction takes its place.
prune() deletes only folders whose name matches the strict pattern above AND that hold an ashvale_version.txt (i.e. that this code made), and never the promoted one; it keeps the newest
`keep_good` packages that hold a RELEASE_OK.json and the newest `keep_unjudged` that do not.

    release_manifest.py list                    the versioned packages, which is promoted, which are good
    release_manifest.py hash <package-dir>      the package hash
    release_manifest.py verify <package-dir>    does RELEASE_OK.json match what is on disk now
    release_manifest.py prune [--keep-good N] [--keep-unjudged N] [--really]     dry run unless --really
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
# Where packages and the promoted junctions live. Default: the folder that holds the project.
# Set CRYENGINE_PACKAGE_DIR to keep them somewhere else (final_packages.py reads the same variable).
ROOT = os.environ.get("CRYENGINE_PACKAGE_DIR") or os.path.dirname(PROJECT)
SHIPPED_DIRS = ("assets", "bin", "engine")
SHIPPED_FILES = ("config.pak", "system.cfg")
VERSION_FILE = "ashvale_version.txt"
OK_FILE = "RELEASE_OK.json"
NAME = re.compile(r"^Ashvale_(?:(profile)_)?(\d{8})_([0-9a-f]{8})$")
LINKS = {"release": "Ashvale_final_release", "profile": "Ashvale_final_profile"}
KEEP_GOOD, KEEP_UNJUDGED = 3, 2


def staging_dir(config, root=ROOT):
    return os.path.join(root, "Ashvale_staging_" + config)


def versioned_name(config, date, hash8):
    return "Ashvale_%s%s_%s" % ("profile_" if config == "profile" else "", date, hash8)


def shipped_files(dest):
    """[(relative path with forward slashes, absolute path)] of the files that make the package what it is, in a fixed order"""
    out = []
    for top in sorted(os.listdir(dest)):
        p = os.path.join(dest, top)
        if os.path.isdir(p) and top.lower() in SHIPPED_DIRS:
            for d, dirs, files in os.walk(p):
                dirs.sort()
                for f in sorted(files):
                    full = os.path.join(d, f)
                    out.append((os.path.relpath(full, dest).replace("\\", "/"), full))
        elif os.path.isfile(p) and top.lower() in SHIPPED_FILES:
            out.append((top, p))
    return sorted(out, key=lambda x: x[0].lower())


def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def package_hash(dest):
    """sha256 over (path, size, content hash) of every shipped file -> hex. Files a test run writes into a package (screenshots, logs, user data) are not shipped files and do not count."""
    h = hashlib.sha256()
    for rel, full in shipped_files(dest):
        h.update(("%s\0%d\0%s\n" % (rel.lower(), os.path.getsize(full), file_sha(full))).encode())
    return h.hexdigest()


def write_version_file(dest, info):
    lines = ["%s: %s" % (k, info[k]) for k in sorted(info) if not isinstance(info[k], (dict, list))]
    for k in sorted(info):
        if isinstance(info[k], dict):
            lines += ["%s.%s: %s" % (k, kk, info[k][kk]) for kk in sorted(info[k])]
        elif isinstance(info[k], list):
            lines += ["%s: %s" % (k, "; ".join(map(str, info[k])) or "none")]
    with open(os.path.join(dest, VERSION_FILE), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def read_version_file(dest):
    out = {}
    try:
        with open(os.path.join(dest, VERSION_FILE), encoding="utf-8") as f:
            for line in f:
                k, _, v = line.rstrip("\n").partition(": ")
                out[k] = v
    except OSError:
        pass
    return out


def build_info(dest, config, game_dll_sha, shader_paks, digests, overrides, date=None):
    """The content of ashvale_version.txt (the package hash is added by finalize)"""
    return {"product": "Ashvale", "config": config, "date": date or time.strftime("%Y-%m-%d %H:%M:%S"), "game_dll_sha256_16": game_dll_sha,
            "shader_paks": dict(shader_paks), "group_digests": dict(digests), "preflight_overrides": [("%s=%s" % kv) for kv in sorted((overrides or {}).items())]}


def finalize(staging, config, info, root=ROOT, today=None):
    """Hash the finished package in `staging`, write ashvale_version.txt into it, rename the folder to its versioned name -> (path, hash). If a package of that name exists (same date, same content) the
    staging copy is dropped and the existing one is returned."""
    full = package_hash(staging)
    info = dict(info, package_hash=full)
    write_version_file(staging, info)
    name = versioned_name(config, today or time.strftime("%Y%m%d"), full[:8])
    target = os.path.join(root, name)
    if os.path.isdir(target):
        if package_hash(target) == full:
            shutil.rmtree(staging, ignore_errors=True)
            return target, full
        raise RuntimeError("%s exists with different content (hash collision on 8 hex digits, or it was changed): not overwriting it" % target)
    os.rename(staging, target)
    return target, full


def is_junction(path):
    import ctypes
    a = ctypes.windll.kernel32.GetFileAttributesW(path)
    return a != 0xFFFFFFFF and bool(a & 0x400)


def promoted(config, root=ROOT):
    """the versioned folder the junction currently points at, or None"""
    link = os.path.join(root, LINKS[config])
    if os.path.exists(link) and is_junction(link):
        return os.path.realpath(link)
    return None


def promote(target, config, root=ROOT):
    """Point Ashvale_final_<config> at `target` (a junction). A real folder under that name is renamed to Ashvale_legacy_<config>_<time>, not deleted. -> a text saying what happened"""
    link = os.path.join(root, LINKS[config])
    note = ""
    if os.path.lexists(link):
        if is_junction(link):
            os.rmdir(link)                                         # removes the link only, never what it points to
        else:
            legacy = os.path.join(root, "Ashvale_legacy_%s_%s" % (config, time.strftime("%Y%m%d_%H%M%S")))
            os.rename(link, legacy)
            note = "the real folder %s was renamed to %s; " % (LINKS[config], os.path.basename(legacy))
    r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0 or not os.path.isdir(link):
        raise RuntimeError("could not create the junction %s -> %s: %s" % (link, target, (r.stdout + r.stderr)[:200]))
    return note + "%s -> %s" % (LINKS[config], os.path.basename(target))


def versioned_packages(root=ROOT):
    """[{name, path, config, date, hash8, good, has_version_file}] newest first (by date then folder time)"""
    out = []
    for n in os.listdir(root):
        m = NAME.match(n)
        p = os.path.join(root, n)
        if m and os.path.isdir(p) and not is_junction(p):
            out.append({"name": n, "path": p, "config": "profile" if m.group(1) else "release", "date": m.group(2), "hash8": m.group(3),
                        "good": os.path.isfile(os.path.join(p, OK_FILE)), "has_version_file": os.path.isfile(os.path.join(p, VERSION_FILE)), "mtime": os.path.getmtime(p)})
    return sorted(out, key=lambda x: (x["date"], x["mtime"]), reverse=True)


def prune(config, keep_good=KEEP_GOOD, keep_unjudged=KEEP_UNJUDGED, really=False, root=ROOT):
    """-> {"keep": [...], "delete": [...]}; deletes only with really=True, only versioned folders with a version file, never the promoted one; symbols folders go with their package"""
    pk = [p for p in versioned_packages(root) if p["config"] == config and p["has_version_file"]]
    prom = promoted(config, root)
    keep, delete = [], []
    n_good = n_unj = 0
    for p in pk:
        if prom and os.path.normcase(p["path"]) == os.path.normcase(prom):
            keep.append(p["name"])
            continue
        if p["good"] and n_good < keep_good:
            n_good += 1
            keep.append(p["name"])
        elif not p["good"] and n_unj < keep_unjudged:
            n_unj += 1
            keep.append(p["name"])
        else:
            delete.append(p["name"])
    errors = []
    if really:
        def _onerror(_func, path, exc_info):                      # a failed delete is reported, not hidden
            errors.append("%s: %s" % (path, exc_info[1]))
        for n in delete:
            shutil.rmtree(os.path.join(root, n), onerror=_onerror)
            sym = os.path.join(root, n + "_symbols")
            if os.path.isdir(sym):
                shutil.rmtree(sym, onerror=_onerror)
    return {"keep": keep, "delete": delete, "deleted": really, "errors": errors}


def write_release_ok(dest, config, all_groups, groups_run, results, digests_before, digests_after, overrides, started, root=ROOT):
    """Write RELEASE_OK.json into `dest` when (and only when) the gate really passed on exactly this package -> (written, [reasons it was not])"""
    why = []
    if config != "release":
        why.append("only a Release package is released (this one is %s)" % config)
    if set(groups_run) != set(all_groups):
        why.append("not every group ran (%s missing): use --all" % ", ".join(sorted(set(all_groups) - set(groups_run))))
    bad = [r[0] for r in results if r[1] or r[2]]
    if bad:
        why.append("failed tests: " + ", ".join(bad))
    if not results:
        why.append("no test results")
    if overrides:
        why.append("preflight overrides were used (%s): a package made with one is not a release" % ", ".join(sorted(overrides)))
    if digests_before != digests_after:
        why.append("the project changed during the run (group digests differ): the verdict is about a different project")
    ver = read_version_file(dest)
    now = package_hash(dest)
    if not ver.get("package_hash"):
        why.append("the package has no %s: it was not made by this packaging path" % VERSION_FILE)
    elif ver["package_hash"] != now:
        why.append("the package on disk (%s) is not the package that was made (%s): it changed after packaging" % (now[:12], ver["package_hash"][:12]))
    if why:
        return False, why
    rec = {"package": os.path.basename(os.path.realpath(dest)), "package_hash": now, "config": config, "passed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "seconds": round(time.time() - started), "game_dll_sha256_16": ver.get("game_dll_sha256_16"), "groups": sorted(groups_run), "group_digests": digests_after,
           "tests": [{"test": r[0], "returncode": r[1], "failed": r[2], "seconds": round(r[3], 1)} for r in results]}
    with open(os.path.join(dest, OK_FILE), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    return True, []


def verify(dest):
    """-> (ok, text): does the package's RELEASE_OK.json describe the package that is on disk now"""
    p = os.path.join(dest, OK_FILE)
    if not os.path.isfile(p):
        return False, "no %s: this package has not passed the gate" % OK_FILE
    with open(p, encoding="utf-8") as f:
        rec = json.load(f)
    now = package_hash(dest)
    if rec.get("package_hash") != now:
        return False, "%s is about a different package (%s) than the one on disk (%s)" % (OK_FILE, str(rec.get("package_hash"))[:12], now[:12])
    return True, "RELEASE_OK for %s, passed %s" % (rec.get("package"), rec.get("passed_at"))


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 2
    if a[0] == "list":
        for p in versioned_packages():
            print("%-40s %-8s %s%s" % (p["name"], p["config"], "GOOD" if p["good"] else "unjudged", "  (promoted)" if promoted(p["config"]) and os.path.normcase(promoted(p["config"])) == os.path.normcase(p["path"]) else ""))
        return 0
    if a[0] == "hash":
        print(package_hash(a[1]))
        return 0
    if a[0] == "verify":
        ok, text = verify(a[1])
        print(text)
        return 0 if ok else 1
    if a[0] == "prune":
        kg = int(a[a.index("--keep-good") + 1]) if "--keep-good" in a else KEEP_GOOD
        ku = int(a[a.index("--keep-unjudged") + 1]) if "--keep-unjudged" in a else KEEP_UNJUDGED
        for cfg in ("release", "profile"):
            print(cfg, json.dumps(prune(cfg, kg, ku, "--really" in a)))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
