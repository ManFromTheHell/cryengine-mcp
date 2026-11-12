#!/usr/bin/env python3
"""The ONE list of checks that must pass before anything is packaged.

These checks live in one module so that every packaging path runs them; previously some paths packaged with none of them, and a check that
"could not run" (an import error, a missing log) printed one line and let the package go on. Now every path that makes a package calls the same functions:

    final_packages.package()          pre-package checks, the packager, post-package checks
    shader_cache_refresh.record()     the same, for the Profile record build
    jobs `package` / `tests`          through final_packages

    preflight.py [--config release|profile] [--phase pre|post] [--dest <package>] [--skip-check name=reason ...] [--json]

STATUS of a check: pass | fail (it ran and found a fault) | error (it could not run: counted as a FAILURE) | skipped (an explicit, recorded override).
A report is `ok` only when every check is pass or skipped. A skip needs `--skip-check <name>=<reason of at least 8 characters>`; it is written into the report,
into the package's ashvale_version.txt and into RELEASE_OK.json, so a package made with one never looks like a clean one.

Checks, by name (a skip can name `level_check:example` for one level, or `level_check` for all):
  pre   scratch_in_levels   zz_mcp_*/scratch* objects saved in a level layer (a graceful editor close saves the level)
        assets_xml          text assets that are not well-formed XML (RC cannot convert them and the packager then ships the last good converted copy)
        audio               wav/mp3/flac under Assets/audio (the packager's copy whitelist has no *.wav)
        textures_presplit   loose GameSDK pre-split .dds sets that are in no Assets/*.pak (RC refuses a set that is already split)
        level_check:<name>  level_check.check for EVERY level under Assets/levels (the presets the game looks up, the exported level.pak)
        stale_sources:<cfg> a source file newer than the configuration's Game.dll (the packager compiles nothing)
  post  rc                  'Failed to convert' / 'Cannot read file' in Resource Compiler's log; a log not written by THIS packaging is an error
        textures_packaged   every loose .dds under Assets that the finished package does not contain
        package_bin         package_clean's allowlist test of both bin folders (and the Game.dll's configuration)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import glob
import hashlib
import json
import os
import re
import sys
import time
import zipfile

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
# Engine install folder: env CRYENGINE_ENGINE_DIR or CRYENGINE_ENGINE (both names are used by other modules), default the Launcher's 5.7.1 LTS folder.
ENGINE = os.environ.get("CRYENGINE_ENGINE_DIR") or os.environ.get("CRYENGINE_ENGINE") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1")
RC_LOG = os.path.join(ENGINE, "Tools", "rc", "rc_log.log")
XML_SUFFIXES = (".xml", ".env", ".adb", ".mtl", ".cdf", ".tod", ".lyr")
AUDIO_LOST = (".wav", ".mp3", ".flac", ".aif", ".aiff")
MIN_REASON = 8

sys.path.insert(0, HERE)


def game_dll(config, project=PROJECT):
    d = os.path.join(project, "bin", "win_x64_release" if config == "release" else "win_x64")
    default = os.path.join(d, "Game.dll")
    if os.path.isfile(default):
        return default
    # a project whose code module is not called Game: the newest non-engine DLL in the folder
    import glob
    cands = [f for f in glob.glob(os.path.join(d, "*.dll")) if not os.path.basename(f).lower().startswith(("cry", "d3d", "api-ms", "vcruntime", "msvcp"))]
    return max(cands, key=os.path.getmtime) if cands else default


def sha(path, n=16):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:n]


# ----------------------------------------------------------------------------------------------- the checks (each returns a list of faults; empty = pass)
def malformed_assets(project=PROJECT):
    """Text assets that are not well-formed XML. The Resource Compiler cannot convert them, says so only in its own log, and the packager then ships the LAST GOOD converted copy from
    rc_out_PC_pure -- silently. Example: a '--' inside an XML comment keeps every package on an old audio library."""
    import xml.parsers.expat
    bad = []
    assets = os.path.join(project, "Assets")
    for d, _, files in os.walk(assets):
        for f in files:
            if f.lower().endswith(XML_SUFFIXES):
                p = os.path.join(d, f)
                try:
                    with open(p, "rb") as fh:
                        data = fh.read()
                    if data[:3] == b"\xef\xbb\xbf":
                        data = data[3:]
                    if data.lstrip()[:1] == b"<":
                        xml.parsers.expat.ParserCreate().Parse(data, True)
                except Exception as exc:                          # noqa: BLE001 - the parser's message is the finding
                    bad.append("%s: %s" % (os.path.relpath(p, assets).replace("\\", "/"), exc))
    return bad


def unshippable_audio(project=PROJECT):
    """Audio files Crytek's packager leaves out. Its copy whitelist (rc_jobs/game_assets.xml AllExtensions) has *.ogg and *.wem/*.fsb/*.bank but no *.wav, so a WAV under Assets/audio is silently
    absent from Audio.pak: the rain, the crickets and the wind are silent in every build."""
    audio = os.path.join(project, "Assets", "audio")
    return [os.path.relpath(os.path.join(d, f), audio).replace("\\", "/") for d, _, files in os.walk(audio) for f in files if f.lower().endswith(AUDIO_LOST)]


def _pak_names(paks):
    have = set()
    for p in paks:
        try:
            with zipfile.ZipFile(p) as z:
                have.update(n.lower().replace("\\", "/") for n in z.namelist())
        except Exception:                                         # noqa: BLE001 - an unreadable pak is simply not a source of names; the caller's count shows it
            pass
    return have


_SPLIT = re.compile(r"\.dds\.(\d+a?|a)$", re.I)


def presplit_textures_outside_paks(project=PROJECT):
    """Loose pre-split .dds sets (x.dds beside x.dds.1 ...) that are in no Assets/*.pak. RC compiles a plain .dds but refuses a set that is already split, which is how GameSDK ships them: those
    must be in Assets/GameSDKTextures.pak. Left out of every pak, a fire sprite renders as a white square."""
    assets = os.path.join(project, "Assets")
    have = _pak_names(glob.glob(os.path.join(assets, "*.pak")))
    loose = {}
    for d, _, files in os.walk(assets):
        for f in files:
            lf = f.lower()
            if ".dds" in lf and not lf.endswith((".cryasset", ".png")):
                loose.setdefault(os.path.join(d, f), None)
    split_bases = set()
    for p in loose:
        m = _SPLIT.search(p)
        if m:
            split_bases.add(p[:m.start()] + ".dds")
    split_lower = {b.lower() for b in split_bases}
    out = []
    for p in loose:
        isplit = bool(_SPLIT.search(p)) or p.lower() in split_lower
        rel = os.path.relpath(p, assets).replace("\\", "/")
        if isplit and rel.lower() not in have:
            out.append(rel)
    return sorted(out)


def unpackaged_textures(dest, project=PROJECT):
    """Loose .dds files under Assets that the finished package does not contain (every pak of the package's assets/ folder is read)."""
    assets = os.path.join(project, "Assets")
    have = _pak_names(glob.glob(os.path.join(dest, "assets", "*.pak")) + glob.glob(os.path.join(dest, "Assets", "*.pak")))
    return [rel for d, _, files in os.walk(assets) for f in files
            if ".dds" in f.lower() and not f.lower().endswith((".cryasset", ".png"))
            for rel in [os.path.relpath(os.path.join(d, f), assets).replace("\\", "/")] if rel.lower() not in have]


def stale_sources(config, project=PROJECT):
    """Source files newer than the configuration's Game.dll. The packager copies bin/ as it stands and compiles nothing, so a package made after a code edit without a build tests yesterday's game
    (visible only by, for example, grepping the DLL for a new CVar name)."""
    code = os.path.join(project, "Code")
    if not os.path.isdir(code):
        return []                                                 # a content-only project has nothing to compile
    dll = game_dll(config, project)
    if not os.path.isfile(dll):
        return ["(no %s)" % dll]
    built = os.path.getmtime(dll)
    return [os.path.relpath(os.path.join(d, f), code).replace("\\", "/") for d, _, files in os.walk(code) for f in files
            if f.endswith((".cpp", ".h", ".txt", ".cmake")) and os.path.getmtime(os.path.join(d, f)) > built]


def levels(project=PROJECT):
    """Every level folder under Assets/levels (a folder with leveldata or a .level file)."""
    root = os.path.join(project, "Assets", "levels")
    if not os.path.isdir(root):
        return []
    return sorted(n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n))
                  and (os.path.isdir(os.path.join(root, n, "leveldata")) or os.path.isfile(os.path.join(root, n, n + ".level"))))


def level_errors(level, project=PROJECT):
    """level_check.check's error findings for one level -> ["V001 message", ...]. The level_check module is imported from THIS folder; a failure to run it raises."""
    import level_check
    r = level_check.check(level, with_engine=False, assets=os.path.join(project, "Assets"), code_dir=os.path.join(project, "Code"))
    return ["%s %s" % (f["code"], f["message"][:200]) for f in r["findings"] if f["severity"] == "error"]


def scratch_saved_in_levels(project=PROJECT):
    """Objects named zz_mcp_* / scratch* saved in a level's layer files would ship (a graceful editor close saves the level, scratch objects included)"""
    import safe_close
    return ["%s: %s" % (lv, ", ".join(names[:5])) for lv, names in sorted(safe_close.scratch_on_disk(project).items())]


def rc_failures(since, rc_log=None):
    """'Failed to convert' lines in the Resource Compiler's log. RC starts the log afresh on each run, so a log written after `since` belongs to the packaging just done. Raises when the log is
    missing or older than `since` (the check could not tell anything: that is an ERROR, not a pass)."""
    rc_log = rc_log or RC_LOG
    if not os.path.isfile(rc_log):
        raise RuntimeError("no RC log at %s" % rc_log)
    if os.path.getmtime(rc_log) < since:
        raise RuntimeError("the RC log was not written by this packaging (%s is older than the run): the conversion result is unknown" % rc_log)
    with open(rc_log, "rb") as f:
        text = f.read().decode("utf-8", "replace")
    return [l.strip() for l in text.splitlines() if "Failed to convert" in l or "Cannot read file" in l]


# ----------------------------------------------------------------------------------------------- the runner
def parse_skips(values):
    """['name=reason', ...] -> {name: reason}; a skip without a reason of MIN_REASON characters is refused"""
    out = {}
    for v in values or []:
        name, _, reason = v.partition("=")
        name, reason = name.strip(), reason.strip()
        if not name or len(reason) < MIN_REASON:
            raise ValueError("--skip-check needs name=reason with a reason of at least %d characters, got %r" % (MIN_REASON, v))
        out[name] = reason
    return out


def standing_exemptions():
    """{check name: reason} from preflight_exempt.json next to this file: checks that never apply to this project (each with its reason; recorded like any override)."""
    path = os.path.join(_paths.ROOT, "preflight_exempt.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str) and len(v) >= MIN_REASON}


def _skipped(name, skips):
    """the reason when `name` (or its family before ':') is skipped, else None"""
    if name in skips:
        return skips[name]
    return skips.get(name.split(":")[0])


def _one(name, fn, skips, cap=8):
    reason = _skipped(name, skips)
    if reason is not None:
        return {"name": name, "status": "skipped", "detail": "SKIPPED by override: " + reason, "items": []}
    try:
        faults = fn()
    except Exception as exc:                                      # noqa: BLE001 - a check that cannot run is a failure, never a pass
        return {"name": name, "status": "error", "detail": "could not run: %s: %s" % (type(exc).__name__, str(exc)[:300]), "items": []}
    if faults:
        return {"name": name, "status": "fail", "detail": "%d fault(s): %s" % (len(faults), "; ".join(str(x) for x in faults[:cap])), "items": list(faults)}
    return {"name": name, "status": "pass", "detail": "", "items": []}


def run(config, phase="pre", dest=None, skips=None, project=PROJECT, rc_since=None, rc_log=None, level_list=None):
    """Run the checks of one phase -> {"ok", "config", "phase", "checks": [...], "skips": {...}}.
    phase 'pre': before the packager runs. phase 'post': after it (needs dest, and rc_since = time.time() taken just before the packager started)."""
    if config not in ("release", "profile"):
        raise ValueError("config is 'release' or 'profile'")
    skips = dict(standing_exemptions(), **dict(skips or {}))       # standing exemptions first: a skip given on the command line wins
    checks = []
    if phase == "pre":
        checks.append(_one("assets_xml", lambda: malformed_assets(project), skips))
        checks.append(_one("audio", lambda: unshippable_audio(project), skips))
        checks.append(_one("textures_presplit", lambda: presplit_textures_outside_paks(project), skips))
        try:
            names = level_list if level_list is not None else levels(project)
            if not names:
                raise RuntimeError("no level folder under %s: nothing to ship" % os.path.join(project, "Assets", "levels"))
        except Exception as exc:                                  # noqa: BLE001
            checks.append({"name": "level_check", "status": "error", "detail": "could not list the levels: %s" % exc, "items": []})
            names = []
        for lv in names:
            checks.append(_one("level_check:" + lv, lambda lv=lv: level_errors(lv, project), skips))
        checks.append(_one("scratch_in_levels", lambda: scratch_saved_in_levels(project), skips))
        checks.append(_one("stale_sources:" + config, lambda: stale_sources(config, project), skips))
    elif phase == "post":
        if dest is None or rc_since is None:
            raise ValueError("phase 'post' needs dest and rc_since")
        checks.append(_one("rc", lambda: rc_failures(rc_since, rc_log), skips))
        checks.append(_one("textures_packaged", lambda: unpackaged_textures(dest, project), skips))

        def bin_faults():
            import package_clean
            return package_clean.audit(dest, config, project)
        checks.append(_one("package_bin", bin_faults, skips))
    else:
        raise ValueError("phase is 'pre' or 'post'")
    used = {c["name"] for c in checks if c["status"] == "skipped"}
    unused = sorted(k for k in skips if not any(c["name"] == k or c["name"].split(":")[0] == k for c in checks))
    ok = all(c["status"] in ("pass", "skipped") for c in checks)
    return {"ok": ok, "config": config, "phase": phase, "checks": checks, "skips": {k: v for k, v in skips.items() if k not in unused},
            "unused_skips": unused, "skipped_checks": sorted(used)}


def format_report(rep):
    lines = ["preflight %s (%s): %s" % (rep["phase"], rep["config"], "OK" if rep["ok"] else "BLOCKED")]
    for c in rep["checks"]:
        lines.append("   %-34s %-8s %s" % (c["name"], c["status"].upper(), c["detail"][:300]))
    if rep.get("unused_skips"):
        lines.append("   (skip names that matched no check of this phase: %s)" % ", ".join(rep["unused_skips"]))
    return "\n".join(lines)


def require(rep, out=print):
    """Print the report; SystemExit(1) when it is not ok. final_packages / shader_cache_refresh call this."""
    out(format_report(rep))
    if not rep["ok"]:
        raise SystemExit(1)
    return rep


def main():
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="release")
    ap.add_argument("--phase", default="pre")
    ap.add_argument("--dest")
    ap.add_argument("--skip-check", action="append", default=[])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    since = time.time() - 3600
    rep = run(a.config, a.phase, a.dest, parse_skips(a.skip_check), rc_since=since if a.phase == "post" else None)
    print(json.dumps(rep, indent=1) if a.json else format_report(rep))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
