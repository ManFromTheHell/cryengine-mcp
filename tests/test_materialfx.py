#!/usr/bin/env python3
"""Self-test of materialfx.py (E-21, 2026-10-27): the writer's workbook is read back by the loader's rules, the project's own two files are clean, and each rule fires on a deliberately
broken copy (a positive control per finding) while the unchanged copy stays clean (the control). No editor and no game needed.

    test_materialfx.py        # exit 1 on any failure
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
import materialfx as M

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-120s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:70]))


def codes(findings):
    return sorted({c for c, _ in findings})


def with_copy(mutator):
    """Run check() against copies of the project's files in a temp folder after `mutator(sheet_path, lib_path)`; the module's paths are restored."""
    tmp = tempfile.mkdtemp()
    saved = (M.SHEET, M.LIB)
    try:
        sheet, lib = os.path.join(tmp, "materialeffects.xml"), os.path.join(tmp, "ashvale_impact.xml")
        shutil.copyfile(saved[0], sheet)
        shutil.copyfile(saved[1], lib)
        mutator(sheet, lib)
        M.SHEET, M.LIB = sheet, lib
        return M.check()
    finally:
        M.SHEET, M.LIB = saved
        shutil.rmtree(tmp, ignore_errors=True)


def edit(path, old, new):
    t = open(path, encoding="utf-8").read()
    assert old in t, "test anchor missing: %r" % old
    open(path, "w", encoding="utf-8", newline="\n").write(t.replace(old, new, 1))


def main():
    missing = [p for p in (M.SHEET, M.LIB, M.SURFACE_TYPES, M.SFX_TRIGGERS) if not os.path.isfile(p)]
    if missing:                                               # this test copies and pins the author's Ashvale workbook / impact library (17 surface types, ash_blade / ash_arrow rows)
        print("SKIPPED: the project has no %s (the Ashvale material-effects files this test is written around); no checks were run" % ", ".join(os.path.basename(p) for p in missing))
        return 0
    surfaces = M.surface_names()
    check("SurfaceTypes.xml gives 17 surface types, mat_default among them", len(surfaces) == 17 and "mat_default" in surfaces, len(surfaces))
    findings, summary = M.check()
    check("the project's workbook and library are clean (no finding of M001-M006)", not [f for f in findings if f[0] != "M007"], findings[:2])
    check("the workbook has 17 columns, 17 surface rows and the two custom rows ash_blade / ash_arrow", summary["columns"] == 17 and summary["surface_rows"] == 17 and set(summary["custom_rows"]) == {"ash_blade", "ash_arrow"}, summary)

    rows = {r: {s: "%s:%s" % (M.LIB_NAME, e) for s, e in cells.items()} for r, cells in M.ROWS.items()}
    text = M.write_workbook(None, surfaces, rows)
    tmp = tempfile.mkdtemp()
    try:
        p = os.path.join(tmp, "w.xml")
        open(p, "w", encoding="utf-8").write(text)
        cols, srows, custom = M.read_workbook(p)
        check("the writer's workbook read back: every cell sits in the column of its surface type", all(cols[i] in M.ROWS["ash_blade"] for i in custom["ash_blade"]) and
              {cols[i]: t.split(":")[1] for i, t in custom["ash_blade"].items()} == M.ROWS["ash_blade"], len(custom["ash_blade"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    f, _ = with_copy(lambda s, l: None)
    check("CONTROL: an unchanged copy of the two files gives no error finding", not [x for x in f if x[0] != "M007"], codes(f))
    f, _ = with_copy(lambda s, l: edit(s, "mat_metal</Data>", "mat_metall</Data>"))
    check("a column that is not a surface type is reported (M001)", "M001" in codes(f), codes(f))
    f, _ = with_copy(lambda s, l: edit(s, "ashvale_impact:wood", "ashvale_impact:woodd"))
    check("a cell naming an effect the library does not have is reported (M003)", "M003" in codes(f), codes(f))
    f, _ = with_copy(lambda s, l: edit(s, "ashvale_impact:stone", "other_lib:stone"))
    check("a cell naming another library is reported (M003)", "M003" in codes(f), codes(f))
    f, _ = with_copy(lambda s, l: edit(l, 'trigger="sfx_impact_metal"', 'trigger="sfx_no_such"'))
    check("an audio trigger that claude_sfx.xml does not define is reported (M004)", "M004" in codes(f), codes(f))
    f, _ = with_copy(lambda s, l: edit(l, "ashfx.hit_wood.hit", "ashfx.hit_wood.nope"))
    check("a particle effect that no library holds is reported (M005)", "M005" in codes(f), codes(f))
    f, _ = with_copy(lambda s, l: edit(l, "materials/decals/blood/blood_splat1", "materials/decals/blood/nope"))
    check("a decal material without a .mtl is reported (M006)", "M006" in codes(f), codes(f))
    f, _ = with_copy(lambda s, l: edit(s, '<Row>\n    <Cell><Data ss:Type="String">mat_fabric</Data></Cell>\n   </Row>\n', ""))
    check("a surface row missing: a custom row is silently read as a surface row (M008)", "M008" in codes(f), codes(f))
    f, _ = with_copy(lambda s, l: edit(s, 'mat_wood_breakable</Data></Cell>\n   </Row>', 'mat_wood_breakable</Data></Cell>\n    <Cell><Data ss:Type="String">mat_default</Data></Cell>\n   </Row>'))
    check("a table with more columns than surface rows is reported (M002 and M008)", "M002" in codes(f) or "M008" in codes(f), codes(f))
    f, _ = with_copy(lambda s, l: edit(l, '\t<Effect name="glass"', '\t<Effect name="unused_effect" delay="0.0">\n\t\t<Audio trigger="sfx_impact_glass"/>\n\t</Effect>\n\n\t<Effect name="glass"'))
    check("an effect no cell uses is noted (M007, information)", "M007" in codes(f), codes(f))

    passed = sum(results)
    print("SUMMARY: %d of %d checks passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
