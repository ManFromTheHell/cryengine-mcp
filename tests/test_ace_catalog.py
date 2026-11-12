#!/usr/bin/env python3
"""Self-test of ace_catalog.py: a fixture project with a library, samples and code (offline) with every problem planted once, then the real project. Editor-free, about 1 s.

    test_ace_catalog.py       # exit 1 on any failure
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
import ace_catalog as A

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-112s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:50]))


LIB = """<AudioSystemData name="fx" version="5">
 <Triggers>
  <Trigger name="fx_door"><Event name="door.ogg" type="start" volume="-6" loop_count="1"/></Trigger>
  <Trigger name="fx_door_stop"><Event name="door.ogg" type="stop"/></Trigger>
  <Trigger name="fx_vox_a_0"><Event name="a0.ogg" type="start"/></Trigger>
  <Trigger name="fx_vox_a_1"><Event name="gone.ogg" type="start"/></Trigger>
  <Trigger name="fx_orphan"><Event name="door.ogg" type="start"/></Trigger>
 </Triggers>
 <Parameters><Parameter name="fx_gain"/></Parameters>
</AudioSystemData>"""
LIB2 = '<AudioSystemData name="two"><Triggers><Trigger name="fx_door"><Event name="door.ogg" type="start"/></Trigger></Triggers></AudioSystemData>'
CODE = """#include "x.h"
constexpr auto kDoor = CryAudio::StringToId("fx_door");
constexpr auto kTypo = CryAudio::StringToId("fx_dor");       // a typo: no error at run time, only silence
constexpr auto kGain = CryAudio::StringToId("fx_gain");
// CryAudio::StringToId("fx_commented_out")
std::string voice = "fx_vox_" + id + "_" + std::to_string(j);
"""


def main():
    tmp = tempfile.mkdtemp(prefix="acetest_")
    try:
        assets, code = os.path.join(tmp, "Assets"), os.path.join(tmp, "Code")
        os.makedirs(os.path.join(assets, "audio", "mw", "ace"))
        os.makedirs(os.path.join(assets, "audio", "mw", "assets"))
        os.makedirs(code)
        open(os.path.join(assets, "audio", "mw", "ace", "fx.xml"), "w").write(LIB)
        open(os.path.join(assets, "audio", "mw", "ace", "two.xml"), "w").write(LIB2)
        for f in ("door.ogg", "a0.ogg"):
            open(os.path.join(assets, "audio", "mw", "assets", f), "wb").write(b"OggS")
        open(os.path.join(code, "game.cpp"), "w").write(CODE)
        p = A.problems(assets, code)
        check("a name the code uses that no library defines is found (fx_dor), the commented-out one is not", list(p["undefined_in_code"]) == ["fx_dor"], list(p["undefined_in_code"]))
        check("a sample an event names that does not exist is found (gone.ogg in fx_vox_a_1)", p["missing_samples"] == [("fx_vox_a_1", "gone.ogg")], p["missing_samples"])
        check("a trigger defined in two libraries is found (fx_door)", p["duplicates"] == [("fx_door", "fx", "two")], p["duplicates"])
        check("triggers built at run time from a literal prefix in the code ('fx_vox_') are not called unreferenced", p["built_at_run_time"] == ["fx_vox_a_0", "fx_vox_a_1"], p["built_at_run_time"])
        check("a trigger nothing refers to is listed (fx_orphan, fx_door_stop)", p["unreferenced_triggers"] == ["fx_door_stop", "fx_orphan"], p["unreferenced_triggers"])
        check("a parameter the code uses counts as defined (fx_gain)", "fx_gain" not in p["undefined_in_code"], "")
        rows = A.search("door", assets)
        check("search finds triggers by name with their events (volume, type)", {r["name"] for r in rows} == {"fx_door", "fx_door_stop"} and rows[0]["events"][0]["type"] in ("start", "stop"), [r["name"] for r in rows])
        open(os.path.join(assets, "audio", "mw", "ace", "bad.xml"), "w").write("<AudioSystemData><oops")
        check("a library that does not parse is reported, not fatal", A.problems(assets, code)["broken_libraries"][0]["library"] == "bad", "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    real = A.problems()
    if real["code_names"] == 0 and not A.libraries():
        print("  (real-project checks skipped: the active project has no audio libraries and its code names no triggers)")
        bad = results.count(False)
        print("ace catalog test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
        return 1 if bad else 0
    check("the real project: every name the game code uses is defined (%d names), every event's sample exists, no trigger is defined twice" % real["code_names"],
          real["code_names"] > 0 and not real["undefined_in_code"] and not real["missing_samples"] and not real["duplicates"] and not real["broken_libraries"], list(real["undefined_in_code"]))
    check("the real project has at least one audio library", len(A.libraries()) >= 1, (len(A.libraries()), real["triggers"]))
    bad = results.count(False)
    print("ace catalog test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
