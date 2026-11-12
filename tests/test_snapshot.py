#!/usr/bin/env python3
"""Self-test of snapshot.py on a fixture project: every claim the module makes has a planted case. A backup tool that has never been shown to bring a
file back is not a backup, so most of this is about the way back.

    test_snapshot.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import hashlib
import os
import shutil
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import snapshot as S

FIX = os.path.join(HERE, "job_runs", "_snap_test")
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-68s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:80]))


def put(path, data, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(data)
    if mtime:
        os.utime(path, (mtime, mtime))


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def refuses(name, fn, *a, **k):
    try:
        fn(*a, **k)
    except S.SnapshotError as exc:
        check(name, True, str(exc)[:66])
        return
    check(name, False, "was accepted")


def tree(root):
    return {os.path.relpath(os.path.join(d, f), root).replace("\\", "/"): sha(os.path.join(d, f)) for d, _, fs in os.walk(root) for f in fs}


def main():
    shutil.rmtree(FIX, ignore_errors=True)
    proj, docs, store = (os.path.join(FIX, n) for n in ("proj", "docs", "snaps"))
    rng = __import__("random").Random(5)
    big = bytes(rng.getrandbits(8) for _ in range(300000))
    # what belongs in a snapshot
    put(os.path.join(proj, "Code", "a.cpp"), b"int main() { return 0; }\n", 1.7e9)
    put(os.path.join(proj, "Code", "b.h"), b"#pragma once\n", 1.7e9)
    put(os.path.join(proj, "Assets", "levels", "x", "x.level"), big, 1.7e9)
    put(os.path.join(proj, "Assets", "textures", "t.dds"), b"DDS " + big[:5000], 1.7e9)
    put(os.path.join(proj, "Assets", "dup1.bin"), big[:20000], 1.7e9)
    put(os.path.join(proj, "Assets", "dup2.bin"), big[:20000], 1.7e9)                       # identical content: stored once
    put(os.path.join(proj, "mcp_server", "s.py"), b"print(1)\n", 1.7e9)
    put(os.path.join(docs, "HANDOFF.md"), b"# handoff\n", 1.7e9)
    # what must NOT be in one (regenerable): each exists on disk, so its absence is the tool's doing
    skip = ["mcp_server/ci_runs/run1.txt", "mcp_server/job_runs/j.log", "mcp_server/__pycache__/m.pyc", "Assets/levels/x/x.level.bak", "editor.log", "bin/win_x64/Game.dll",
            "user_shaders_backup_1/s.cfx", "rc_out_PC_pure/o.caf", "solutions/win64/g.sln", "logbackups/old.log", "Assets/GameSDKTextures.pak.tmp", "Assets/t.thmb.png"]
    for rel in skip:
        put(os.path.join(proj, *rel.split("/")), b"regenerable", 1.7e9)
    S.configure(root=store, sources=[("project", proj, S.PROJECT_EXCLUDES), ("docs", docs, [])], with_git=False)
    try:
        s1 = S.snapshot("first")
        m1 = S.load(s1["id"])
        keep = {"project/Code/a.cpp", "project/Code/b.h", "project/Assets/levels/x/x.level", "project/Assets/textures/t.dds", "project/Assets/dup1.bin", "project/Assets/dup2.bin",
                "project/mcp_server/s.py", "docs/HANDOFF.md"}
        check("the first snapshot holds exactly the authored files (8) and none of the 12 regenerable ones", set(m1["files"]) == keep, sorted(set(m1["files"]) ^ keep)[:4])
        blobs = sum(len(fs) for _, _, fs in os.walk(os.path.join(store, "blobs")))
        check("identical contents are stored once (8 files, 7 blobs)", blobs == 7 and s1["new_files_stored"] == 7, "%d blobs" % blobs)
        check("it says it is the same disk as the project", "same disk" in s1["note"])
        # --- nothing changed: the second costs nothing
        s2 = S.snapshot("second")
        check("a second snapshot of an unchanged project stores nothing and rehashes nothing", s2["new_files_stored"] == 0 and S.load(s2["id"])["stats"]["hashed_or_copied"] == 0, S.load(s2["id"])["stats"])
        # --- change three things
        put(os.path.join(proj, "Code", "a.cpp"), b"int main() { return 1; }\n")                  # modified
        put(os.path.join(proj, "Code", "new.cpp"), b"// new\n")                                  # added
        os.remove(os.path.join(proj, "Code", "b.h"))                                             # removed
        d = S.diff(s2["id"], "current")
        check("diff against the live files: one added, one removed, one modified, the rest unchanged", (d["added"], d["removed"], d["modified"], d["unchanged"]) == (1, 1, 1, 6)
              and d["modified_files"] == ["project/Code/a.cpp"] and d["added_files"] == ["project/Code/new.cpp"] and d["removed_files"] == ["project/Code/b.h"], (d["added"], d["removed"], d["modified"], d["unchanged"]))
        s3 = S.snapshot("third")
        check("the third snapshot stores only the two new contents (a.cpp's, new.cpp's)", s3["new_files_stored"] == 2, s3["new_files_stored"])
        d2 = S.diff(s2["id"], s3["id"])
        check("diff snapshot against snapshot agrees with the live diff", (d2["added"], d2["removed"], d2["modified"]) == (1, 1, 1))
        # --- the documented limit of the fast path, and its remedy
        lvl = os.path.join(proj, "Assets", "levels", "x", "x.level")
        st = os.stat(lvl)
        put(lvl, bytes([big[0] ^ 1]) + big[1:])                                                  # same size ...
        os.utime(lvl, ns=(st.st_atime_ns, st.st_mtime_ns))                                       # ... and the same time
        fast = S.snapshot("fast")
        check("LIMIT: a change that keeps size and time is NOT noticed by the fast path", fast["new_files_stored"] == 0)
        full = S.snapshot("full", full=True)
        check("--full re-hashes everything and finds it", full["new_files_stored"] == 1, full["new_files_stored"])
        # --- the way back
        out = os.path.join(FIX, "restored")
        r = S.restore(s1["id"], ["**"], to=out)
        want = {k.split("/", 1)[1] if False else k: v["sha"] for k, v in S.load(s1["id"])["files"].items()}
        got = tree(out)
        check("restoring snapshot 1 to a side folder brings back all 8 files byte for byte", r["restored"] == 8 and {k: got[k] for k in got} == want, (r["restored"], len(got)))
        check("the side folder is not the project (live files untouched by a side restore)", not os.path.exists(os.path.join(out, "project", "Code", "new.cpp")) and open(os.path.join(proj, "Code", "a.cpp"), "rb").read().endswith(b"1; }\n"))
        sub = S.restore(s1["id"], ["project/Code/*"], to=os.path.join(FIX, "sub"))
        typo = S.restore(s1["id"], ["project/Code/a.cpp", "project/Code/aa.cpp"], to=os.path.join(FIX, "typo"))
        check("a pattern list with one typo restores the rest and WARNS about the typo", typo["restored"] == 1 and "project/Code/aa.cpp" in typo.get("warning", ""), typo.get("warning"))
        check("a pattern restores only what it names", sub["restored"] == 2 and sorted(tree(os.path.join(FIX, "sub"))) == ["project/Code/a.cpp", "project/Code/b.h"], sub["restored"])
        # --- in place, with its own undo
        S.restore(s1["id"], ["project/Code/a.cpp"], overwrite=True)
        back = open(os.path.join(proj, "Code", "a.cpp"), "rb").read()
        check("restore with overwrite=true puts the file back where it was", back == b"int main() { return 0; }\n", back)
        ids = [x["id"] for x in S.list_snapshots()["snapshots"]]
        before = next(i for i in ids if "before_restore" in i)
        undo = S.restore(before, ["project/Code/a.cpp"], to=os.path.join(FIX, "undo"))
        check("...and took a snapshot first, from which the overwritten version can be had again", open(os.path.join(FIX, "undo", "project", "Code", "a.cpp"), "rb").read().endswith(b"1; }\n"), undo["restored"])
        # --- refusals
        refuses("a '..' pattern is refused", S.restore, s1["id"], ["../outside"], to=os.path.join(FIX, "x"))
        refuses("an absolute pattern is refused", S.restore, s1["id"], ["C:/Windows/System32/*"], to=os.path.join(FIX, "x"))
        refuses("a pattern that matches nothing is refused", S.restore, s1["id"], ["project/nothing/*"], to=os.path.join(FIX, "x"))
        refuses("overwrite together with a destination is refused", S.restore, s1["id"], ["**"], to=os.path.join(FIX, "x"), overwrite=True)
        refuses("a destination inside the snapshotted project is refused", S.restore, s1["id"], ["**"], to=os.path.join(proj, "restored_here"))
        refuses("an unknown snapshot is refused", S.restore, "20200101_000000", ["**"], to=os.path.join(FIX, "x"))
        refuses("a snapshot id that is a path is refused", S.load, "../../etc")
        # --- integrity
        ok = S.verify(s1["id"])
        check("verify: every blob of a snapshot re-hashes to its name", ok["ok"] and ok["checked"] == 8, ok)
        victim = S.load(s1["id"])["files"]["project/Code/a.cpp"]["sha"]
        bp = S._blob_path(victim)
        data = bytearray(open(bp, "rb").read())
        data[0] ^= 0xFF
        open(bp, "wb").write(bytes(data))
        bad = S.verify(s1["id"])
        check("a corrupted blob is found by verify", not bad["ok"] and bad["corrupt"] == ["project/Code/a.cpp"], bad)
        refuses("...and a restore refuses to hand it back as good", S.restore, s1["id"], ["project/Code/a.cpp"], to=os.path.join(FIX, "corrupt"))
        data[0] ^= 0xFF
        open(bp, "wb").write(bytes(data))
        # --- an interrupted snapshot leaves nothing half-made
        n_before = len(S._manifests())
        real_store, calls = S._store, [0]
        def exploding(path):
            calls[0] += 1
            if calls[0] == 2:
                raise RuntimeError("power cut")
            return real_store(path)
        S._store = exploding
        put(os.path.join(proj, "Code", "c.cpp"), b"// c\n")
        put(os.path.join(proj, "Code", "d.cpp"), b"// d\n")
        try:
            S.snapshot("interrupted", full=True)
            check("an interrupted snapshot raises", False)
        except RuntimeError:
            check("an interrupted snapshot raises, leaves no manifest, and releases the lock", len(S._manifests()) == n_before and not os.path.exists(os.path.join(store, ".lock")), len(S._manifests()))
        finally:
            S._store = real_store
        S.snapshot("after_interrupt")
        check("the next snapshot works", len(S._manifests()) == n_before + 1)
        # --- a locked file is reported, not hidden
        try:
            import msvcrt
            lockfile = os.path.join(proj, "Code", "locked.cpp")
            put(lockfile, b"x" * 100)
            fh = open(lockfile, "r+b")
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 50)
            try:
                try:
                    open(lockfile, "rb").read()
                    readable = True
                except OSError:
                    readable = False
                if readable:
                    print("  --   this system lets a locked region be read: the locked-file case is skipped")
                else:
                    sk = S.snapshot("locked")
                    check("a file that cannot be read is listed as skipped, with a warning, and the snapshot still completes", [x["path"] for x in sk["skipped"]] == ["project/Code/locked.cpp"] and "warning" in sk, sk["skipped"])
            finally:
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 50)
                fh.close()
        except ImportError:
            print("  --   no msvcrt: the locked-file case is skipped")
        # --- the store may not sit inside what it stores
        S.configure(root=os.path.join(proj, "snaps_inside"))
        refuses("a store inside the snapshotted project is refused", S.snapshot, "x")
        S.configure(root=store)
        # --- it never deletes
        names = [n for n in dir(S) if any(w in n.lower() for w in ("delete", "prune", "remove", "purge", "clean", "rmtree"))]
        check("the module has no function that deletes or prunes a snapshot", not names, names)
        listing = S.list_snapshots()
        check("every snapshot taken is still listed (%d)" % listing["count"], listing["count"] == len(S._manifests()) >= 8, listing["count"])
    finally:
        shutil.rmtree(FIX, ignore_errors=True)
    check("cleanup: the fixture is gone", not os.path.exists(FIX))
    bad = results.count(False)
    print("snapshot test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
