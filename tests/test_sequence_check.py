#!/usr/bin/env python3
"""Self-test of sequence_check.py: the comparison logic on synthetic documents (offline), then, with the editor open, the game's intro sequence through the engine's own loader and
five deliberately broken copies (positive controls). The level's own sequences are not touched. About 30 s live.

    test_sequence_check.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import sequence_check as Q

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-112s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:50]))


def main():
    check("numbers compare as numbers: 512.0000 = 512, '0.000000,0.000000' = '0,0', 1.333333 = 1.3333333, 1 != 2", Q.same_value("512.0000", "512") and Q.same_value("0.000000,0.000000", "0,0") and Q.same_value("1.333333", "1.3333333") and not Q.same_value("1", "2"), "")
    check("a GUID half is hex without leading zeros (07A6... = 7A6...) and only for GUIDLo/GUIDHi", Q.same_value("07A6966744E3D748", "7A6966744E3D748", "GUIDLo") and not Q.same_value("07A6966744E3D748", "7A6966744E3D748"), "")
    a = ET.fromstring('<S a="1"><N x="1" y="2"><K v="1"/><K v="2"/></N></S>')
    same = Q.diff_documents(a, ET.fromstring('<S a="1"><N x="1" y="2"><K v="1"/><K v="2"/></N></S>'))
    check("a document compared with itself differs in nothing (the fixed point)", not (same["dropped_elements"] or same["dropped_attributes"] or same["changed"]), same)
    d = Q.diff_documents(a, ET.fromstring('<S a="1"><N x="9"><K v="1"/></N><E/></S>'))
    check("a dropped attribute, a changed one, a dropped element and an added element are told apart", [x[1] for x in d["dropped_attributes"]] == ["y"] and [c[1] for c in d["changed"]] == ["x"] and d["dropped_elements"] == ["/S/N#1/K#2"] and d["added_elements"] == ["/S/E#1"], d)
    late = Q.late_keys(ET.fromstring('<Sequence endTimeTicks="6000"><Key timeTicks="3000"/><Key timeTicks="12000"/></Sequence>'))
    check("a key after the sequence's end is reported (the engine keeps it, it never plays)", len(late) == 1 and "2.0 s" in late[0] and "1.0 s" in late[0], late)

    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                           # noqa: BLE001
        live = False
        print("  --   the editor is not open: the live checks are skipped")
    seq_file = os.path.join(__import__("project_root").ASSETS, "libs", "ashvale", "sequences", "ashvale_intro.xml")
    if live and not os.path.isfile(seq_file):
        print("  --   %s not found (the live checks use Ashvale's hand-written intro sequence): the live checks are skipped" % seq_file)
        live = False
    if live:
        text = open(seq_file, encoding="utf-8").read()
        r = Q.check_text(text, "t_intro")
        check("the game's hand-written intro loads through the engine and EVERYTHING is kept (1 node, Position and Rotation, 15 keys each, 13 s)",
              r["ok"] and r["loaded"] and len(r["nodes"]) == 1 and {t["param"]: t["keys"] for t in r["nodes"][0]["tracks"]} == {"Position": 15, "Rotation": 15} and abs(r["end"] - 13.0) < 0.01, (r["ok"], r["end"]))
        r1 = Q.check_text(text.replace('<Key value="512.0000"', '<Key Bogus="1" value="512.0000"', 1), "t_bogus")
        check("positive control: an attribute the engine does not know on a Key is reported as dropped", not r1["ok"] and [a[1] for a in r1["dropped_attributes"]] == ["Bogus"], r1["dropped_attributes"])
        r2 = Q.check_text(text.replace('Type="Entity"', 'Type="NoSuchNodeType"', 1), "t_node")
        check("positive control: a node of an unknown type disappears and is reported (0 nodes, its tracks dropped)", not r2["ok"] and r2["nodes"] == [] and len(r2["dropped_elements"]) >= 3, r2["dropped_elements"][:2])
        r3 = Q.check_text(text.replace('paramType="Position"', 'paramType="NoSuchParam"', 1), "t_param")
        check("positive control: an unknown track parameter becomes 'Unknown Entity Parameter' and the file differs", not r3["ok"] and any(t["param"].startswith("Unknown") for t in r3["nodes"][0]["tracks"]), [t["param"] for t in r3["nodes"][0]["tracks"]])
        r4 = Q.check_text(text.replace('value="118.0000"', 'value="abc"', 1), "t_value")
        check("positive control: a key value that is not a number is changed by the engine and reported", not r4["ok"] and len(r4["changed"]) >= 1, r4["changed"][:1])
        r5 = Q.check_text(text.replace('<Key timeTicks="78000" value="172.0000"', '<Key timeTicks="900000" value="172.0000"', 1), "t_late")
        check("a key 150 s into a 13 s sequence is kept by the engine (ok) and reported as a warning by this tool", r5["ok"] and len(r5["warnings"]) == 1, r5["warnings"])
        r6 = Q.check_text(text, "t_again")
        check("the level's own sequences are untouched: loading the same name again later still gives the same answer (the throw-away copy was removed)", r6["ok"] and r6["nodes"] == r["nodes"], "")
        for f in os.listdir(Q.SCRATCH):
            if f.startswith("t_") and "seq_" in f:
                os.remove(os.path.join(Q.SCRATCH, f))
        try:
            Q.check_text("<Sequence><oops", "t_bad")
            bad_x = False
        except Q.SequenceError:
            bad_x = True
        check("a file that is not well-formed XML is refused before anything is sent", bad_x, "")

    bad = results.count(False)
    print("sequence check test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
