#!/usr/bin/env python3
"""Self-test of object_check.py: the value comparison and every finding on synthetic objects with a clean control first (offline), then, with the editor open, the project's own brushes, navigation areas and
probe against the editor, and the same real objects altered in memory (a property the engine does not have, a changed value, a missing object) to show that the comparison can fire.

    test_object_check.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import copy
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import object_check as C

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(findings, text=""):
    return sorted({f["code"] for f in findings if text in f["where"] or text in f["message"]})


BRUSH = '<Object Type="Brush" Name="Wall1" Pos="10,20,30" Scale="1,1,1" ViewDistRatio="100" CastShadowMaps="1" Prefab="objects/w.cgf"><CollisionFiltering collisionClass="0" colliderGroup="1"/></Object>'


def el(xml, **file_attrs):
    e = ET.fromstring(xml)
    e.set("_file", "main.lyr")
    return e


def main():
    check("same_value: equal text, numbers within 1e-3, comma lists member by member", C.same_value("a", "a") and C.same_value("10.0004,20,30", "10,20,30") and C.same_value("1e-1", "0.1"), "")
    check("same_value control: 1.01 vs 1, a different word, lists of different length, one different member", not C.same_value("1.01", "1") and not C.same_value("true", "false") and not C.same_value("1,2", "1,2,3") and not C.same_value("1,2,3", "1,2,4"), "")

    live_ok = ET.fromstring(BRUSH)
    rep = C.check(objects=[el(BRUSH)], total=1, live=lambda n: live_ok, live_count=1)
    check("control: an object identical to the live one gives no finding", rep["findings"] == [] and rep["compared"] == 1, rep["findings"])

    f = ET.fromstring(BRUSH.replace('CastShadowMaps="1"', 'CastShadowMapsX="1" CastShadowMaps="1"'))
    f.set("_file", "main.lyr")
    rep = C.check(objects=[f], total=1, live=lambda n: live_ok, live_count=1)
    check("O002: a property the live object does not have is found, with its value", codes(rep["findings"]) == ["O002"] and "CastShadowMapsX" in rep["findings"][0]["message"], codes(rep["findings"]))

    l = ET.fromstring(BRUSH.replace('ViewDistRatio="100"', 'ViewDistRatio="255"'))
    rep = C.check(objects=[el(BRUSH.replace('ViewDistRatio="100"', 'ViewDistRatio="300"'))], total=1, live=lambda n: l, live_count=1)
    check("O003: a value the live object holds differently (300 clamped to 255) is found, both values shown", codes(rep["findings"]) == ["O003"] and "'300'" in rep["findings"][0]["message"] and "'255'" in rep["findings"][0]["message"], rep["findings"][0]["message"][:60])
    near = ET.fromstring(BRUSH.replace('Pos="10,20,30"', 'Pos="10.0003,20,29.9996"'))
    check("control: a position that differs by 0.0004 (float formatting) is NOT a difference", C.check(objects=[el(BRUSH)], total=1, live=lambda n: near, live_count=1)["findings"] == [], "")

    # the level defaults to the one the editor has open (a fixed 'ashvale' read every object of the first level as missing while the Undercroft was open: 318 false errors in project_audit)
    if os.path.isdir(os.path.join(C.LEVELS, "ashvale_undercroft", "layers")):
        import open_level
        was = open_level.name
        try:
            open_level.name = lambda default="ashvale": "ashvale_undercroft"
            rep = C.check(live=lambda n: None, live_count=1)
            check("with no level named, the check reads the level the editor has open (here the Undercroft's own 20 brushes and area), not Ashvale's", rep["level"] == "ashvale_undercroft" and 15 <= rep["objects"] <= 40, (rep["level"], rep["objects"]))
        finally:
            open_level.name = was
    else:
        print("  --   SKIPPED: the level ashvale_undercroft is not in this project (Ashvale-specific check)")

    newer = ET.fromstring(BRUSH.replace('Pos=', 'NewProp="5" Pos='))
    rep = C.check(objects=[el(BRUSH)], total=1, live=lambda n: newer, live_count=1)
    check("O004 (information): an attribute only the live object has - the file predates it", codes(rep["findings"]) == ["O004"] and rep["counts"]["error"] == 0 and rep["counts"]["warning"] == 0, codes(rep["findings"]))

    ch = ET.fromstring(BRUSH.replace('colliderGroup="1"', 'colliderGroup="2"'))
    rep = C.check(objects=[el(BRUSH)], total=1, live=lambda n: ch, live_count=1)
    check("a changed attribute of a CHILD node is found with the node in its path (O003 .../CollisionFiltering)", codes(rep["findings"]) == ["O003"] and "CollisionFiltering" in rep["findings"][0]["where"], rep["findings"][0]["where"])
    nochild = ET.fromstring('<Object Type="Brush" Name="Wall1" Pos="10,20,30" Scale="1,1,1" ViewDistRatio="100" CastShadowMaps="1" Prefab="objects/w.cgf"/>')
    rep = C.check(objects=[el(BRUSH)], total=1, live=lambda n: nochild, live_count=1)
    check("O005: a child node the file has and the live object lacks", codes(rep["findings"]) == ["O005"], codes(rep["findings"]))

    comp_file = el('<Object Type="EnvironmentProbe" Name="P"><Components><Component typeId="t" guid="AAA" Active="true"/></Components></Object>')
    comp_live = ET.fromstring('<Object Type="EnvironmentProbe" Name="P"><Components><Component typeId="t" guid="BBB" Active="true"/></Components></Object>')
    check("control: a component's instance guid (new at every level load) is not a difference", C.check(objects=[comp_file], total=1, live=lambda n: comp_live, live_count=1)["findings"] == [], "")
    comp_live2 = ET.fromstring('<Object Type="EnvironmentProbe" Name="P"><Components><Component typeId="t" guid="BBB" Active="false"/></Components></Object>')
    check("but a changed property of that component is (Active true vs false)", codes(C.check(objects=[comp_file], total=1, live=lambda n: comp_live2, live_count=1)["findings"]) == ["O003"], "")

    rep = C.check(objects=[el(BRUSH)], total=1, live=lambda n: None, live_count=1)
    check("O001: an object the editor does not hold is an error", codes(rep["findings"]) == ["O001"] and rep["counts"]["error"] == 1 and rep["compared"] == 0, codes(rep["findings"]))
    rep = C.check(objects=[el(BRUSH), el(BRUSH)], total=2, live=lambda n: live_ok, live_count=2)
    check("O007 (information): a name that occurs twice in the layer files is reported once", [x["code"] for x in rep["findings"]] == ["O007"], codes(rep["findings"]))
    rep = C.check(objects=[el(BRUSH)], total=5, live=lambda n: live_ok, live_count=7)
    check("O006 (information): the editor's object count differs from the layer files'", codes(rep["findings"]) == ["O006"] and rep["counts"]["warning"] == 0, codes(rep["findings"]))

    tmp = tempfile.mkdtemp(prefix="zz_mcp_objcheck_")
    try:
        lay = os.path.join(tmp, "lv", "layers")
        os.makedirs(lay)
        with open(os.path.join(lay, "main.lyr"), "w", encoding="utf-8") as fh:
            fh.write('<ObjectLayer><Layer Name="Main"><LayerObjects>%s<Object Type="Entity" Name="E1" EntityClass="X"/></LayerObjects></Layer></ObjectLayer>' % BRUSH)
        objs, total = C.read_objects("lv", tmp)
        check("read_objects: entities are left to entity_check (1 brush returned, 2 objects counted) and the file name is kept", len(objs) == 1 and total == 2 and objs[0].get("_file") == "main.lyr", (len(objs), total))
        with open(os.path.join(lay, "bad.lyr"), "w", encoding="utf-8") as fh:
            fh.write("<not closed")
        try:
            C.read_objects("lv", tmp)
            ok = False
        except C.ObjectCheckError:
            ok = True
        check("a layer file that is not XML raises ObjectCheckError", ok, "")
        try:
            C.read_objects("nowhere", tmp)
            ok = False
        except C.ObjectCheckError:
            ok = True
        check("a level without a layers folder raises ObjectCheckError", ok, "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    live = False
    try:
        import object_props
        object_props.object_count()
        live = True
    except Exception as exc:                                                  # noqa: BLE001
        print("  (the editor does not answer: %s)" % str(exc)[:80])
    if live and not os.path.isdir(os.path.join(C.LEVELS, "ashvale", "layers")):
        print("  --   SKIPPED: the live checks read the level 'ashvale', which is not in this project")
    elif live:
        run_live()
    else:
        print("  (editor not answering: the live checks are skipped)")
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


def run_live():
    rep = C.check("ashvale")
    check("live: every brush, navigation area and probe of the level is held by the editor (%d objects %s)" % (rep["objects"], rep["types"]), rep["objects"] > 100 and rep["compared"] == rep["objects"] and rep["counts"]["error"] == 0, (rep["objects"], rep["compared"]))
    check("live: and each one matches its file: no ignored property, no changed value, no child difference", rep["counts"]["warning"] == 0, [f["message"][:60] for f in rep["findings"] if f["severity"] == "warning"][:2])
    objs, total = C.read_objects("ashvale")
    brush = copy.deepcopy([o for o in objs if o.get("Type") == "Brush"][0])
    swap = [k for k in brush.attrib if k not in ("Name", "Type", "Id", "Layer", "LayerGUID", "_file") and brush.get(k).replace(".", "").replace("-", "").replace(",", "").isdigit()]
    bad = copy.deepcopy(brush)
    bad.set("ZzNoSuchProperty", "1")
    victim = swap[0]
    bad.set(victim, "987654")
    r2 = C.check(objects=[bad], total=None, live_count=None)
    check("live positive control: the same real brush with a made-up property and %s changed: O002 and O003 are raised" % victim, codes(r2["findings"]) == ["O002", "O003"], codes(r2["findings"]))
    r3 = C.check(objects=[copy.deepcopy(brush)], total=None, live_count=None)
    check("live control: the unaltered copy of that brush gives nothing", r3["findings"] == [] and r3["compared"] == 1, r3["findings"])
    ghost = copy.deepcopy(brush)
    ghost.set("Name", "ZzNoSuchBrush")
    r4 = C.check(objects=[ghost], total=None, live_count=None)
    check("live positive control: a brush the editor does not hold is O001", codes(r4["findings"]) == ["O001"], codes(r4["findings"]))


if __name__ == "__main__":
    main()
