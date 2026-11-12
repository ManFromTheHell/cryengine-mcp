#!/usr/bin/env python3
"""Snapshots of the work. A project may have no version control and no backup, and this tool set deletes and overwrites files.

    snapshot.py snapshot [label]            store what the project is now (only what changed since the last snapshot costs disk or time)
    snapshot.py list [--verify <id>]        the snapshots, newest first; --verify re-hashes every blob one snapshot refers to
    snapshot.py diff <a> [<b>|current]      added / removed / modified files between two snapshots, or a snapshot and the live files
    snapshot.py restore <id> <pattern>... [--to <dir>] [--overwrite]
                                            copy files back: into a side folder by default; --overwrite puts them where they were, after taking a snapshot
                                            of the current files so the restore can itself be undone

How it stores: content-addressed. Every file is copied ONCE to blobs/<aa>/<sha256>; a snapshot is a manifest (manifests/<id>.json) mapping each path to its hash, size
and time. Two snapshots of a project that changed in three files cost three files. The manifest is written LAST and atomically: an interrupted run leaves blobs nobody
refers to and no half snapshot. Nothing in this module deletes or prunes a snapshot, a blob or a manifest.

What is in a snapshot (SOURCES): the whole project folder except what can be regenerated (EXCLUDES: build output, CI and job runs, generated solutions, RC output, log backups,
editor `.bak` files and logs, shader-backup folders, texture sources). Opt in with CRYENGINE_SNAPSHOT_INCLUDE_EXTRAS=1 to also store a notes folder (CRYENGINE_SNAPSHOT_DOCS, e.g. handoff notes, corpus and manual)
and the ClaudeBridge plugin's source from the engine checkout (CRYENGINE_SOURCE), an assistant memory folder (CRYENGINE_SNAPSHOT_MEMORY; private notes: do not share such a snapshot), and
`engine/local_changes.patch`: `git diff` of the engine repository, for local edits to Crytek's own files that are uncommitted. A folder whose variable is unset is left out.

Where: next to the project, `CRYENGINE Projects/_snapshots` (CRYENGINE_SNAPSHOT_DIR overrides). That is the SAME DISK: it protects against a mistake (a wrong delete,
an overwrite, a bad rebuild), not against a failed drive. A copy elsewhere is the owner's decision: `restore <id> "**" --to <folder on another drive>` writes a plain tree to copy.

Limits, stated: a file whose content changed WITHOUT its size or modification time changing is not noticed by the default fast path (`--full` re-hashes everything); a file the editor
holds locked is listed as skipped, never silently left out; a file being written while it is read is flagged `unstable`; the live editor can be mid-save when this runs.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
PROJECTS = os.path.dirname(PROJECT)
ENGINE_REPO = _paths.ENGINE_SOURCE                                             # the engine git checkout (CRYENGINE_SOURCE); "" when unset; only read when CRYENGINE_SNAPSHOT_INCLUDE_EXTRAS=1
DOCS_DIR = os.environ.get("CRYENGINE_SNAPSHOT_DOCS", "")                       # optional notes/corpus folder to store as "docs" (env only, no default)
MEMORY_DIR = os.environ.get("CRYENGINE_SNAPSHOT_MEMORY", "")                   # optional assistant memory folder to store as "memory" (private notes; env only, no default)
EXTRAS = os.environ.get("CRYENGINE_SNAPSHOT_INCLUDE_EXTRAS", "").lower() in ("1", "true", "yes")
ROOT = os.environ.get("CRYENGINE_SNAPSHOT_DIR") or os.path.join(PROJECTS, "_snapshots")

PROJECT_EXCLUDES = [
    "bin/*", "solutions/*", ".vs/*", "logbackups/*", "rc_out_PC_pure/*", "user_shaders_backup_*/*", "mcp_server/ci_runs/*", "mcp_server/job_runs/*",
    "mcp_server/.cache/*", "mcp_server/texture_src/*", "*/__pycache__/*", "__pycache__/*",
    "*.bak", "*.bak2", "*.bak_*", "*.pak.bak*", "*.tmp", "*.pak.tmp", "*.log", "*.thmb.png",
]
SOURCES = [   # (prefix inside the snapshot, folder, exclude patterns relative to it)
    ("project", PROJECT, PROJECT_EXCLUDES),
]
if EXTRAS:                                                         # opt-in: the docs, the plugin source, Claude's memory folder (private notes) and the engine git diff
    if DOCS_DIR:
        SOURCES.append(("docs", DOCS_DIR, ["*/__pycache__/*", "*.tmp"]))
    if ENGINE_REPO:
        SOURCES.append(("plugin", os.path.join(ENGINE_REPO, "Code", "Sandbox", "Plugins", "ClaudeBridgePlugin"), []))
    if MEMORY_DIR:
        SOURCES.append(("memory", MEMORY_DIR, []))
if os.environ.get("CRYENGINE_SNAPSHOT_SOURCES"):                   # tests: a JSON list of [prefix, folder, excludes]
    SOURCES = [tuple(s) for s in json.loads(os.environ["CRYENGINE_SNAPSHOT_SOURCES"])]
WITH_GIT = EXTRAS and not os.environ.get("CRYENGINE_SNAPSHOT_NO_GIT")
CHUNK = 1 << 20
LOCK_MAX_AGE = 3600


class SnapshotError(ValueError):
    pass


def configure(root=None, sources=None, with_git=None):
    """Point the module at another store / other folders (the self-test uses a fixture)."""
    global ROOT, SOURCES, WITH_GIT
    if root:
        ROOT = root
    if sources is not None:
        SOURCES = [tuple(s) for s in sources]
    if with_git is not None:
        WITH_GIT = with_git


def _dirs():
    return os.path.join(ROOT, "blobs"), os.path.join(ROOT, "manifests")


def _blob_path(sha):
    return os.path.join(_dirs()[0], sha[:2], sha)


def _excluded(rel, patterns):
    low = rel.lower()
    return any(fnmatch.fnmatch(low, p.lower()) or fnmatch.fnmatch(os.path.basename(low), p.lower()) for p in patterns)


def walk_sources():
    """(snapshot path, absolute path) for every file that belongs in a snapshot."""
    for prefix, base, excludes in SOURCES:
        if not os.path.isdir(base):
            continue
        for d, dirs, files in os.walk(base):
            rel_dir = os.path.relpath(d, base).replace("\\", "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            dirs[:] = [x for x in dirs if not _excluded((rel_dir + "/" + x if rel_dir else x) + "/x", excludes)]       # prune a folder when a file inside it would be excluded
            for n in files:
                rel = (rel_dir + "/" + n) if rel_dir else n
                if _excluded(rel, excludes):
                    continue
                yield prefix + "/" + rel, os.path.join(d, n)


def _check_root():
    real = os.path.realpath(ROOT).lower()
    for _, base, _ in SOURCES:
        b = os.path.realpath(base).lower()
        if real == b or real.startswith(b + os.sep):
            raise SnapshotError("the snapshot store %s is inside a folder it snapshots (%s): that would store itself" % (ROOT, base))
    os.makedirs(_dirs()[0], exist_ok=True)
    os.makedirs(_dirs()[1], exist_ok=True)
    readme = os.path.join(ROOT, "README.txt")
    if not os.path.isfile(readme):
        open(readme, "w", encoding="utf-8").write(
            "Snapshots of the Ashvale work, made by snapshot.py.\n\n"
            "manifests/<id>.json  one per snapshot: every path with its sha256, size and time\n"
            "blobs/<aa>/<sha256>  each distinct file content, stored once, uncompressed\n\n"
            "To get files back: python snapshot.py restore <id> \"<pattern>\" [--to <folder>]\n"
            "By hand: open a manifest, find the path, copy blobs/<first two hex digits>/<sha256> to wherever and name it.\n"
            "Nothing here is ever deleted by the tools. This is the same disk as the project: copy this folder (or a restore of '**')\n"
            "to another drive for protection against a failed one.\n")


def _store(src_path):
    """Copy a file into the blob store in one pass, hashing what was actually read. Returns (sha, size, mtime_ns, unstable)."""
    before = os.stat(src_path)
    tmp_dir = os.path.join(ROOT, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp = os.path.join(tmp_dir, "t%d_%d" % (os.getpid(), time.time_ns()))
    h = hashlib.sha256()
    size = 0
    try:
        with open(src_path, "rb") as f, open(tmp, "wb") as out:
            while True:
                b = f.read(CHUNK)
                if not b:
                    break
                h.update(b)
                out.write(b)
                size += len(b)
        sha = h.hexdigest()
        dest = _blob_path(sha)
        if os.path.exists(dest) and os.path.getsize(dest) == size:
            os.remove(tmp)
        else:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    after = os.stat(src_path)
    unstable = after.st_mtime_ns != before.st_mtime_ns or after.st_size != before.st_size
    return sha, size, before.st_mtime_ns, unstable


def _store_bytes(data):
    h = hashlib.sha256(data).hexdigest()
    dest = _blob_path(h)
    if not (os.path.exists(dest) and os.path.getsize(dest) == len(data)):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".tmp"
        open(tmp, "wb").write(data)
        os.replace(tmp, dest)
    return h


def _engine_state():
    """engine/local_changes.patch and engine/git_state.txt: the edits to Crytek's own files, which live only in the working tree of the engine repository."""
    if not WITH_GIT or not ENGINE_REPO or not os.path.isdir(os.path.join(ENGINE_REPO, ".git")):
        return {}
    def git(*a):
        try:
            r = subprocess.run(["git", "-C", ENGINE_REPO] + list(a), capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
        except (OSError, subprocess.TimeoutExpired):               # git missing or too slow: the snapshot goes on without the engine diff
            return b""
        return r.stdout
    patch = git("diff", "--no-color", "--binary")
    state = ("HEAD %s\nbranch %s\n\n" % (git("rev-parse", "HEAD").decode().strip(), git("rev-parse", "--abbrev-ref", "HEAD").decode().strip())).encode() + git("status", "--short")
    return {"engine/local_changes.patch": patch, "engine/git_state.txt": state}


def _manifests():
    d = _dirs()[1]
    if not os.path.isdir(d):
        return []
    return sorted(n[:-5] for n in os.listdir(d) if n.endswith(".json"))


def load(snapshot_id):
    if snapshot_id == "latest":
        ids = _manifests()
        if not ids:
            raise SnapshotError("there is no snapshot yet")
        snapshot_id = ids[-1]
    if not re.match(r"^[0-9]{8}_[0-9]{6}[A-Za-z0-9_\-]*$", snapshot_id):
        raise SnapshotError("not a snapshot id: %r (list_snapshots shows them)" % snapshot_id)
    path = os.path.join(_dirs()[1], snapshot_id + ".json")
    if not os.path.isfile(path):
        raise SnapshotError("no such snapshot %r (list_snapshots shows them)" % snapshot_id)
    return json.load(open(path, encoding="utf-8"))


class _Lock:
    def __enter__(self):
        os.makedirs(ROOT, exist_ok=True)
        self.path = os.path.join(ROOT, ".lock")
        if os.path.exists(self.path) and time.time() - os.path.getmtime(self.path) < LOCK_MAX_AGE:
            raise SnapshotError("another snapshot or restore is running (%s is %d s old)" % (self.path, time.time() - os.path.getmtime(self.path)))
        open(self.path, "w").write(str(os.getpid()))
        return self

    def __exit__(self, *a):
        try:
            os.remove(self.path)
        except OSError:
            pass


def snapshot(label="", note="", full=False):
    label = re.sub(r"[^A-Za-z0-9_\-]", "_", label or "")[:40]
    t0 = time.time()
    with _Lock():
        _check_root()
        ids = _manifests()
        prev = load(ids[-1])["files"] if ids else {}
        files, skipped, unstable = {}, [], []
        hashed = 0
        for key, path in walk_sources():
            try:
                st = os.stat(path)
                old = prev.get(key)
                if old and not full and old["size"] == st.st_size and old["mtime_ns"] == st.st_mtime_ns and os.path.exists(_blob_path(old["sha"])):
                    files[key] = old
                    continue
                sha, size, mtime_ns, unst = _store(path)
                hashed += 1
                files[key] = {"sha": sha, "size": size, "mtime_ns": mtime_ns}
                if unst:
                    unstable.append(key)
            except OSError as exc:
                skipped.append({"path": key, "why": str(exc)[:120]})
        for key, data in _engine_state().items():
            sha = _store_bytes(data)
            files[key] = {"sha": sha, "size": len(data), "mtime_ns": 0}
        # the blobs this run ADDED: those no earlier snapshot referred to (counted from the manifests, not guessed)
        referenced = {v["sha"] for v in files.values()}
        before_ids = set()
        for m in ids:
            before_ids |= {v["sha"] for v in load(m)["files"].values()}
        fresh = referenced - before_ids
        new_blobs = len(fresh)
        new_bytes = sum(os.path.getsize(_blob_path(s)) for s in fresh)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        sid = stamp + ("_" + label if label else "")
        n = 1
        while os.path.exists(os.path.join(_dirs()[1], sid + ".json")):
            n += 1
            sid = "%s_%s%d" % (stamp, label or "snap", n)
        manifest = {"format": 1, "id": sid, "label": label, "note": note, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "files": files,
                    "skipped": skipped, "unstable": unstable, "sources": [[p, b] for p, b, _ in SOURCES],
                    "stats": {"files": len(files), "bytes": sum(v["size"] for v in files.values()), "new_blobs": new_blobs, "new_bytes": new_bytes,
                              "hashed_or_copied": hashed, "seconds": round(time.time() - t0, 1)}}
        tmp = os.path.join(_dirs()[1], sid + ".json.tmp")
        json.dump(manifest, open(tmp, "w", encoding="utf-8"), indent=0)
        os.replace(tmp, os.path.join(_dirs()[1], sid + ".json"))              # the manifest LAST: a snapshot exists only once it is complete
    out = {"id": sid, "files": len(files), "gigabytes": round(manifest["stats"]["bytes"] / 1e9, 3), "new_files_stored": new_blobs, "new_megabytes_stored": round(new_bytes / 1e6, 1),
           "seconds": manifest["stats"]["seconds"], "store": ROOT, "skipped": skipped[:20], "unstable": unstable[:20],
           "note": "same disk as the project: protects against mistakes, not a failed drive" if not skipped and not unstable else ""}
    if skipped:
        out["warning"] = "%d file(s) could not be read (locked by the editor?) and are NOT in this snapshot" % len(skipped)
    if unstable:
        out["warning_unstable"] = "%d file(s) changed while they were read: take the snapshot again with the editor idle" % len(unstable)
    return out


def verify(snapshot_id):
    m = load(snapshot_id)
    missing, corrupt = [], []
    for key, v in m["files"].items():
        p = _blob_path(v["sha"])
        if not os.path.isfile(p) or os.path.getsize(p) != v["size"]:
            missing.append(key)
            continue
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(CHUNK), b""):
                h.update(b)
        if h.hexdigest() != v["sha"]:
            corrupt.append(key)
    return {"id": m["id"], "checked": len(m["files"]), "missing": missing[:20], "corrupt": corrupt[:20], "ok": not missing and not corrupt}


def list_snapshots(limit=20, verify_id=None):
    out = []
    for sid in reversed(_manifests()):
        m = load(sid)
        out.append({"id": sid, "created": m["created"], "label": m["label"], "files": m["stats"]["files"], "gigabytes": round(m["stats"]["bytes"] / 1e9, 3),
                    "new_megabytes_stored": round(m["stats"]["new_bytes"] / 1e6, 1), "skipped": len(m.get("skipped", []))})
    res = {"store": ROOT, "count": len(out), "snapshots": out[:limit]}
    if os.path.isdir(_dirs()[0]):
        total = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(_dirs()[0]) for f in fs)
        res["store_gigabytes"] = round(total / 1e9, 2)
    if verify_id:
        res["verify"] = verify(verify_id)
    return res


def _live(prev_files):
    """The live files as {key: sha}, hashing only those whose size or time differs from `prev_files`."""
    live = {}
    for key, path in walk_sources():
        try:
            st = os.stat(path)
            old = prev_files.get(key)
            if old and old["size"] == st.st_size and old["mtime_ns"] == st.st_mtime_ns:
                live[key] = old["sha"]
                continue
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for b in iter(lambda: f.read(CHUNK), b""):
                    h.update(b)
            live[key] = h.hexdigest()
        except OSError:
            live[key] = "?unreadable"
    for key, data in _engine_state().items():
        live[key] = hashlib.sha256(data).hexdigest()
    return live


def diff(a, b="current", limit=60):
    ma = load(a)
    fa = {k: v["sha"] for k, v in ma["files"].items()}
    fb = _live(ma["files"]) if b in ("current", "", None) else {k: v["sha"] for k, v in load(b)["files"].items()}
    added = sorted(k for k in fb if k not in fa)
    removed = sorted(k for k in fa if k not in fb)
    modified = sorted(k for k in fa if k in fb and fa[k] != fb[k])
    return {"a": ma["id"], "b": b if b else "current", "added": len(added), "removed": len(removed), "modified": len(modified), "unchanged": len(fa) - len(removed) - len(modified),
            "added_files": added[:limit], "removed_files": removed[:limit], "modified_files": modified[:limit],
            "more": max(0, len(added) - limit) + max(0, len(removed) - limit) + max(0, len(modified) - limit)}


def restore(snapshot_id, patterns, to=None, overwrite=False, limit=20000):
    m = load(snapshot_id)
    if not patterns:
        raise SnapshotError("name what to restore: exact paths as list_snapshots/diff print them ('project/Code/Npc.cpp'), or patterns ('project/Assets/levels/ashvale/**', '**' for everything)")
    for p in patterns:
        if os.path.isabs(p) or ".." in p.replace("\\", "/").split("/") or ":" in p:
            raise SnapshotError("pattern %r: a relative snapshot path or glob, no '..', no drive letters" % p)
    norm = {p: p.replace("\\", "/").lower().replace("**", "*") for p in patterns}
    keys = sorted(k for k in m["files"] if any(fnmatch.fnmatch(k.lower(), n) for n in norm.values()))
    unmatched = [p for p, n in norm.items() if not any(fnmatch.fnmatch(k.lower(), n) for k in m["files"])]     # a typo in one pattern must not pass silently
    if not keys:
        raise SnapshotError("nothing in snapshot %s matches %s" % (m["id"], patterns))
    if len(keys) > limit:
        raise SnapshotError("%d files match: narrow the patterns (the limit is %d)" % (len(keys), limit))
    if overwrite and to:
        raise SnapshotError("overwrite=true puts files back where they were; do not give a destination as well")
    bases = {p: b for p, b, _ in [tuple(s) for s in SOURCES]}
    safety = None
    if overwrite:
        # undo point: what is there NOW, before it is replaced
        safety = snapshot("before_restore", "taken automatically before restoring %d file(s) from %s" % (len(keys), m["id"]))
        dest_root = None
    else:
        dest_root = os.path.abspath(to) if to else os.path.join(ROOT, "restore", m["id"] + "_" + time.strftime("%H%M%S"))
        for _, base, _ in SOURCES:
            if (os.path.realpath(dest_root).lower() + os.sep).startswith(os.path.realpath(base).lower() + os.sep):
                raise SnapshotError("restore destination %s is inside %s, which is snapshotted: choose a side folder (it would mix restored files into the live tree)" % (dest_root, base))
    done, skipped = 0, []
    with _Lock():
        for key in keys:
            v = m["files"][key]
            prefix, _, rel = key.partition("/")
            if overwrite:
                if prefix not in bases:
                    skipped.append({"path": key, "why": "not a project/docs/plugin/memory file (the engine patch is restored to a side folder only)"})
                    continue
                target = os.path.join(bases[prefix], *rel.split("/"))
            else:
                target = os.path.join(dest_root, *key.split("/"))
            src = _blob_path(v["sha"])
            if not os.path.isfile(src):
                raise SnapshotError("the stored copy of %s is missing (blob %s): nothing more was restored" % (key, v["sha"][:12]))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = target + ".restoring"
            h = hashlib.sha256()
            with open(src, "rb") as f, open(tmp, "wb") as out:
                for b in iter(lambda: f.read(CHUNK), b""):
                    h.update(b)
                    out.write(b)
            if h.hexdigest() != v["sha"]:
                os.remove(tmp)
                raise SnapshotError("the stored copy of %s is corrupt (its hash is %s, the manifest says %s): nothing more was restored" % (key, h.hexdigest()[:12], v["sha"][:12]))
            os.replace(tmp, target)
            done += 1
    res = {"snapshot": m["id"], "restored": done, "skipped": skipped, "into": dest_root or "(in place)", "overwrote": bool(overwrite)}
    if unmatched:
        res["warning"] = "these patterns matched NOTHING in the snapshot (typo?): %s" % unmatched
    if safety:
        res["undo"] = "the files as they were before are in snapshot %s: restore_files(%r, <same patterns>, overwrite=true) puts them back" % (safety["id"], safety["id"])
    return res


def main():
    a = sys.argv[1:]
    try:
        if a[:1] == ["snapshot"]:
            print(json.dumps(snapshot(a[1] if len(a) > 1 and not a[1].startswith("--") else "", full="--full" in a), indent=1))
        elif a[:1] == ["list"]:
            print(json.dumps(list_snapshots(verify_id=a[a.index("--verify") + 1] if "--verify" in a else None), indent=1))
        elif a[:1] == ["diff"]:
            print(json.dumps(diff(a[1], a[2] if len(a) > 2 else "current"), indent=1))
        elif a[:1] == ["restore"]:
            pats = [x for x in a[2:] if not x.startswith("--") and x != (a[a.index("--to") + 1] if "--to" in a else None)]
            print(json.dumps(restore(a[1], pats, a[a.index("--to") + 1] if "--to" in a else None, "--overwrite" in a), indent=1))
        else:
            print(__doc__)
    except SnapshotError as exc:
        print("refused: %s" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
