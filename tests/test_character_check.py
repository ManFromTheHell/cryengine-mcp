#!/usr/bin/env python3
"""Self-test of character_check.py: the .cdf reader and the code scanner on synthetic input (offline), then, with the editor open, every character of the project through the engine's own character
manager and deliberately broken copies (positive controls) next to an unchanged copy (control). Copies get unique names (CryPak caches a loose file by name) and are deleted again. About 20 s live.

    test_character_check.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys
import tempfile
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import character_check as C

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-120s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(r, text=""):
    return sorted({f["code"] for f in r["findings"] if text in f["message"] or text in f["where"]})


def main():
    d = tempfile.mkdtemp()
    try:
        open(os.path.join(d, "a.cpp"), "w").write('static const char* k = "Objects/Characters/Human/x/y.cdf"; auto s = "skins/a.skin"; auto n = "not_a_character.txt";\n')
        found = C.code_characters(d)
        check("the code scanner finds .cdf / .chr / .skin string literals and nothing else", sorted(found) == ["Objects/Characters/Human/x/y.cdf", "skins/a.skin"], sorted(found))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    # The checks below pin THIS author's project (sdk_player.cdf, 3 character files, 29 attachments); on any other project they are skipped, not failed.
    ashvale = os.path.isfile(os.path.join(C.ASSETS, "objects", "characters", "human", "sdk_player", "sdk_player.cdf"))
    if ashvale:
        real = C.code_characters()
        check("the game's own code names one character: sdk_player.cdf", sorted(real) == ["Objects/Characters/Human/sdk_player/sdk_player.cdf"], sorted(real))
        chars = C.project_characters()
        check("the project has 3 character definitions (variants whose meshes were never imported are not kept) and the base one's file lists 29 attachments with their bindings", len(chars) == 3 and len(C.file_attachments("objects/characters/human/sdk_player/sdk_player.cdf")[1]) == 29, len(chars))
        check("a character nothing mentions is told from the one the game uses (sdk_player_red.cdf is inert, sdk_player.cdf is referenced by the C++)", C.referenced_by_game("objects/characters/human/sdk_player/sdk_player.cdf") and not C.referenced_by_game("objects/characters/human/sdk_player/sdk_player_red.cdf"), "")
    else:
        print("  --   SKIPPED the Ashvale-specific character checks: Assets/objects/characters/human/sdk_player/sdk_player.cdf is not in this project")

    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                           # noqa: BLE001
        live = False
        print("  --   the editor is not open: the live checks are skipped")
    if live and not ashvale:
        live = False
        print("  --   SKIPPED the live character checks: they pin the Ashvale project's sdk_player.cdf (4 characters, 163 joints, 29 attachments)")
    if live:
        r = C.check()
        base = [c for c in r["characters"] if c["file"].lower() == "objects/characters/human/sdk_player/sdk_player.cdf"]
        loose = 0                                  # the animation set is the library's 1,692 names plus the project's loose clips (MotionBricks, Kimodo): derived, not pinned
        for sub in ("motionbricks", "kimodo"):
            folder = os.path.join(os.path.dirname(HERE), "Assets", "Animations", "human", "male", sub)
            loose += len([f for f in os.listdir(folder) if f.endswith(".caf")]) if os.path.isdir(folder) else 0
        check("the engine creates all 4 (3 files + the C++'s spelling of the base one): 163 joints, 29 attachments of which 5 hold an object, the library's 1,692 animations + %d loose clips" % loose, len(r["characters"]) == 4 and all(c["created"] for c in r["characters"]) and base[0]["joints"] == 163 and base[0]["attachments"] == 29 and base[0]["bound"] == 5 and base[0]["animations"] == 1692 + loose, base[:1])
        check("control: the base character the game uses has no finding at all", not [f for f in r["findings"] if f["where"].lower().endswith("sdk_player.cdf")], [f["message"][:40] for f in r["findings"] if f["where"].lower().endswith("sdk_player.cdf")][:2])
        check("the project is clean now: no C003 (the variants that bound missing meshes are gone) and the check passes", not [f for f in r["findings"] if f["code"] == "C003"] and r["ok"], codes(r))

        stamp = "%d_%d" % (os.getpid(), int(time.time()))
        folder = os.path.join(C.ASSETS, "objects", "characters", "human", "sdk_player")
        made = []

        def variant(tag, text):
            rel = "objects/characters/human/sdk_player/zz_mcp_%s_%s.cdf" % (tag, stamp)
            p = os.path.join(C.ASSETS, rel.replace("/", os.sep))
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            made.append(p)
            return C.check([rel])
        try:
            base_xml = open(os.path.join(folder, "sdk_player.cdf"), encoding="utf-8").read()
            r1 = variant("copy", base_xml)
            check("control: an unchanged copy of the base character is created with the same numbers and no finding", r1["ok"] and not [f for f in r1["findings"] if "zz_mcp_copy" in f["where"]] and r1["characters"][0]["joints"] == 163 and r1["characters"][0]["attachments"] == 29, [f["message"][:50] for f in r1["findings"]][:2])
            bad = base_xml.replace('Binding="objects/characters/human/sdk_player/shoes.skin"', 'Binding="objects/characters/human/sdk_player/no_such_shoes.skin"')
            assert bad != base_xml
            r2 = variant("noskin", bad)
            check("positive control: a skin that does not exist is reported (C003; a warning here because nothing refers to the copy) with the engine's own line", any(f["code"] == "C003" and "no_such_shoes" in f["message"] for f in r2["findings"]), codes(r2))
            badbone = base_xml.replace('BoneName="weapon_bone"', 'BoneName="no_such_bone"', 1)
            assert badbone != base_xml
            r3 = variant("nobone", badbone)
            check("positive control: an attachment on a bone the skeleton lacks is found (C004, or C002 when the engine drops the attachment)", bool({"C004", "C002"} & set(codes(r3))), codes(r3))
            r4 = variant("notchar", "<Foo/>")
            check("positive control: a file that is not a character definition cannot be created (C001) and the engine's reason is kept", "C001" in codes(r4) and not r4["characters"][0]["created"], codes(r4))
        finally:
            for p in made:
                if os.path.exists(p):
                    os.remove(p)
        left = [f for f in os.listdir(folder) if f.startswith("zz_mcp_")]
        check("the project is as it was: no test character is left", not left, left)

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
