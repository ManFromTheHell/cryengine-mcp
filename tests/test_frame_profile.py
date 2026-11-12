#!/usr/bin/env python3
"""Self-test of frame_profile.py: the parser, the findings and the comparison on synthetic profiles with a clean control first (offline), then, with the editor open, a real capture of the viewport with a
positive control (the engine's own e_MergedMeshes switch removes the grass animation's cost from the profile) and a repeat control (the same state captured twice shows the noise).

    test_frame_profile.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys
import tempfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import frame_profile as F

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def blk(name, ms, kids=""):
    return '<block name="%s" totalTimeMS="%.4f" startTime="0" stopTime="0" args="">%s</block>' % (name, ms, kids)


def profile_xml(frames=10, spike_at=None, logs=8, pvrn=4.0, extra=None):
    """a Main thread of `frames` frames (an optional one with `logs` log writes in its entity update), a RenderThread and a worker"""
    main = ""
    for i in range(frames):
        ents = blk("EntitySystem::UpdateEntityComponents", 0.15 + (logs * 0.25 if i == spike_at else 0.0), blk("CLog::LogV", 0.25) * logs if i == spike_at else "")
        main += blk("CSystem::Update", 0.3 + (logs * 0.25 if i == spike_at else 0.0), blk("EntitySystem::Update", 0.2 + (logs * 0.25 if i == spike_at else 0.0), ents))
        main += blk("CViewManager::IdleUpdate", 2.0, blk("C3DEngine::RenderScene", 1.8))
    total = frames * 2.3
    xml = '<root><thread name="Main" totalTimeMS="%.3f" startTime="0" stopTime="0" totalBlocks="1">%s</thread>' % (total, main)
    xml += '<thread name="RenderThread" totalTimeMS="%.3f" startTime="0" stopTime="0" totalBlocks="1">%s</thread>' % (total, blk("SRenderThread::WaitFlushCond", total * 0.1) + blk("Present", 0.5))
    work = blk("PVRNUpdateSpines", pvrn) + (extra or "")
    xml += '<thread name="JobSystem_Worker_0" totalTimeMS="%.3f" startTime="0" stopTime="0" totalBlocks="1">%s</thread></root>' % (total, work)
    return xml


def write(folder, name, xml):
    p = os.path.join(folder, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(xml)
    return p


def main():
    tmp = tempfile.mkdtemp(prefix="zz_mcp_fp_test_")
    try:
        clean = F.parse(write(tmp, "clean.xml", profile_xml()))
        check("parse: 10 frames (one CSystem::Update block each), 3 threads, the main thread's total is the sum of its top-level blocks", clean["frames"] == 10 and len(clean["threads"]) == 3 and abs(clean["main_ms"] - 10 * 2.3) < 1e-6, (clean["frames"], clean["main_ms"]))
        f = clean["functions"]
        check("parse: self time is total minus children (RenderScene 1.8 x 10; IdleUpdate 0.2 x 10 of its own), calls counted, threads recorded", abs(f["C3DEngine::RenderScene"]["self"] - 18.0) < 1e-6 and abs(f["CViewManager::IdleUpdate"]["self"] - 2.0) < 1e-6
              and f["C3DEngine::RenderScene"]["calls"] == 10 and f["PVRNUpdateSpines"]["threads"] == {"JobSystem_Worker_0"}, (f["C3DEngine::RenderScene"]["self"], f["CViewManager::IdleUpdate"]["self"]))
        rep = F.check(clean)
        codes = sorted(x["code"] for x in rep["findings"])
        check("control: a steady profile (no log writes, no long frame) gives P001 and P004 only", codes == ["P001", "P004"] and rep["hitches"] == [], codes)
        check("summary: the frame time is 2.3 ms with a median of 2.3, and the top self-time function is C3DEngine::RenderScene (18 ms over 10 frames)",
              rep["summary"]["main_ms_per_frame"] == 2.3 and rep["summary"]["median_frame_ms"] == 2.3 and rep["summary"]["top_self"][0]["name"] == "C3DEngine::RenderScene", rep["summary"]["top_self"][:2])

        spike = F.parse(write(tmp, "spike.xml", profile_xml(spike_at=4)))
        rep = F.check(spike)
        codes = sorted(x["code"] for x in rep["findings"])
        check("a frame with 8 log writes in the entity update: P002 (8 writes) and P003 (frame 4) are raised", "P002" in codes and "P003" in codes and [h["frame"] for h in rep["hitches"]] == [4], codes)
        h = rep["hitches"][0]
        check("the hitch names its cause: CLog::LogV carries the extra 2 ms", h["causes"][0][0] == "CLog::LogV" and abs(h["causes"][0][1] - 2.0) < 0.01 and h["ms"] > h["median_ms"] * 1.8, h)
        check("P002 says how many writes and what they cost", "8 log writes" in [x for x in rep["findings"] if x["code"] == "P002"][0]["message"], "")
        few = F.parse(write(tmp, "few.xml", profile_xml(frames=3, spike_at=1)))
        check("control: under 5 frames there is no hitch verdict (no median to speak of)", F.hitches(few) == [], "")
        two = F.parse(write(tmp, "two.xml", profile_xml(spike_at=4, logs=2)))
        check("control: 2 log writes in a capture stay under the P002 threshold of 3 (and are no hitch)", "P002" not in [x["code"] for x in F.check(two)["findings"]], "")

        a = F.parse(write(tmp, "a.xml", profile_xml(pvrn=4.0)))
        b = F.parse(write(tmp, "b.xml", profile_xml(pvrn=0.0)))
        a2 = F.parse(write(tmp, "a2.xml", profile_xml(pvrn=4.2)))
        cmp = F.compare(a, b, a2)
        top = cmp["changed"][0]
        check("compare: switching the worker's job off shows as PVRNUpdateSpines -0.4 ms per frame with the repeat's noise (0.02) beside it", top["name"] == "PVRNUpdateSpines" and abs(top["delta"] + 0.4) < 1e-6 and abs(top["noise"] - 0.02) < 1e-6, top)
        x1 = F.parse(write(tmp, "x1.xml", profile_xml(extra=blk("Wobbly", 10.0))))
        x2 = F.parse(write(tmp, "x2.xml", profile_xml(extra=blk("Wobbly", 13.0))))
        x3 = F.parse(write(tmp, "x3.xml", profile_xml(extra=blk("Wobbly", 12.0))))
        check("compare control: a function that moves 0.3 ms/frame between A and B but 0.2 between A and its repeat is NOT reported (3 x the noise)", "Wobbly" not in [r["name"] for r in F.compare(x1, x2, x3)["changed"]], "")
        check("compare without a repeat reports every difference above the floor (noise 0)", "Wobbly" in [r["name"] for r in F.compare(x1, x2)["changed"]], "")

        raised = []
        for label, bad in (("missing file", os.path.join(tmp, "nope.xml")), ("not XML", write(tmp, "bad.xml", "this is not xml"))):
            try:
                F.parse(bad)
                raised.append(False)
            except F.ProfileError:
                raised.append(True)
        check("a missing file and a file that is not XML raise ProfileError", raised == [True, True], raised)
        empty = F.parse(write(tmp, "empty.xml", "<root><thread name=\"Main\" totalTimeMS=\"1\" totalBlocks=\"0\"></thread></root>"))
        check("a profile without frames is an error finding (P000), not a crash", [x["code"] for x in F.check(empty)["findings"]] == ["P000"], "")
        try:
            F.capture(frames=2)
            ok = False
        except F.ProfileError:
            ok = True
        check("capture refuses fewer than 5 frames before it touches the editor", ok, "")

        live = False
        try:
            import debug_views
            debug_views._cvar_get("sys_bp_enabled")
            live = True
        except Exception as exc:                                              # noqa: BLE001
            print("  (the editor does not answer: %s)" % str(exc)[:80])
        if live:
            run_live()
        else:
            print("  (editor not answering: the live checks are skipped)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


def run_live():
    before = {n: F._cvar_get(n) for n in ("sys_bp_enabled", "ed_keepEditorActive", "e_MergedMeshes")}
    a = F.capture(240)
    b = F.capture(240, cvars={"e_MergedMeshes": "0"})
    a2 = F.capture(240)
    check("live: 240 frames recorded (the engine counts exactly the frames asked, +-5)", all(235 <= p["frames"] <= 245 for p in (a, b, a2)), (a["frames"], b["frames"], a2["frames"]))
    names = {t["name"] for t in a["threads"]}
    check("live: the profile holds the Main thread, the RenderThread and job workers; the viewport's render call is in it", {"Main", "RenderThread"} <= names and any(n.startswith("JobSystem_Worker") for n in names) and "CViewManager::IdleUpdate" in a["functions"], sorted(names)[:4])
    per = lambda p, n: p["functions"].get(n, {"self": 0.0})["self"] / max(p["frames"], 1)
    pa, pb, p2 = per(a, "PVRNUpdateSpines"), per(b, "PVRNUpdateSpines"), per(a2, "PVRNUpdateSpines")
    if pa > 0.5:
        check("live positive control: e_MergedMeshes 0 removes the grass animation (PVRNUpdateSpines) from the profile: %.2f -> %.2f ms per frame" % (pa, pb), pb < 0.05 * pa, (round(pa, 3), round(pb, 3)))
        check("live repeat control: the same state captured twice differs by under 25%% on that function (%.2f vs %.2f)" % (pa, p2), abs(pa - p2) < 0.25 * pa, (round(pa, 3), round(p2, 3)))
        cmp = F.compare(a, b, a2)
        check("live: compare puts the grass animation first in the changes, with a delta of the size it was", cmp["changed"] and cmp["changed"][0]["name"] == "PVRNUpdateSpines" and cmp["changed"][0]["delta"] < -0.5 * pa, cmp["changed"][:1])
    else:
        print("  (no merged meshes in view of the viewport: the e_MergedMeshes controls are skipped)")
    after = {n: F._cvar_get(n) for n in before}
    check("live: the capture put everything back (sys_bp_enabled, ed_keepEditorActive, e_MergedMeshes as they were)", after == before, (before, after))
    rep = F.check(a)
    check("live: the check answers with a P001 line for the real capture", rep["findings"][0]["code"] == "P001" and rep["summary"]["main_ms_per_frame"] > 0, rep["findings"][0]["message"][:60])


if __name__ == "__main__":
    main()
