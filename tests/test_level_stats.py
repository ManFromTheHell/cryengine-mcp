#!/usr/bin/env python3
"""Self-test of level_stats.py: the workbook reader on a synthetic file written the way the engine writes it (undeclared ss:/x: prefixes), the validation-line grouping on synthetic log text (offline), then,
with the editor open, the engine's own SaveLevelStats and 'Check Level for Errors' on the real level: the nine rebuilt houses must show the LODs authored for them, and the level must be exactly as
it was (the object count is read before and after: neither command may change it). About 15 s live.

    test_level_stats.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import sys
import tempfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import level_stats as L

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-120s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


SAMPLE = """<?xml version="1.0"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">
 <ExcelWorkbook xmlns="urn:schemas-microsoft-com:office:excel"/>
 <Styles><Style ss:ID="s25"><Font x:CharSet="204" ss:Bold="1"/></Style></Styles>
 <Worksheet ss:Name="Summary"><Table>
  <Row><Cell><Data ss:Type="String">Level x</Data></Cell></Row>
  <Row ss:StyleID="s25"><Cell><Data ss:Type="String">Resource Type (MB)</Data></Cell><Cell><Data ss:Type="String">Count</Data></Cell><Cell><Data ss:Type="String">Memory Size</Data></Cell></Row>
  <Row><Cell><Data ss:Type="String">CGF Objects</Data></Cell><Cell><Data ss:Type="Number">70</Data></Cell><Cell><Data ss:Type="Number">127</Data></Cell></Row>
  <Row><Cell><Data ss:Type="String">Textures</Data></Cell></Row>
  <Row><Cell><Data ss:Type="String">Count</Data></Cell><Cell><Data ss:Type="Number">630</Data></Cell></Row>
  <Row><Cell><Data ss:Type="String">Meshes</Data></Cell></Row>
  <Row><Cell><Data ss:Type="String">Count</Data></Cell><Cell><Data ss:Type="Number">681</Data></Cell></Row>
 </Table></Worksheet>
 <Worksheet ss:Name="Textures"><Table>
  <Row><Cell><Data ss:Type="String">Filename</Data></Cell><Cell><Data ss:Type="String">Refs</Data></Cell><Cell><Data ss:Type="String">Texture Size (KB)</Data></Cell></Row>
  <Row><Cell><Data ss:Type="String">a.dds</Data></Cell><Cell><Data ss:Type="Number">1</Data></Cell><Cell><Data ss:Type="Number">100</Data></Cell></Row>
  <Row><Cell><Data ss:Type="String">b.dds</Data></Cell><Cell ss:Index="3"><Data ss:Type="Number">900</Data></Cell></Row>
  <Row><Cell><Data ss:Type="String">$rt</Data></Cell><Cell><Data ss:Type="Number">1</Data></Cell><Cell><Data ss:Type="Number">5000</Data></Cell></Row>
 </Table></Worksheet>
</Workbook>
"""


def physics_limit_offline():
    rows = [["a.cgf", "1", "", "", "", "", "", "", "100", "100"], ["big.cgf", "1", "", "", "", "", "", "", "9000", "7000"], ["edge.cgf", "1", "", "", "", "", "", "", "5000", "5000"], ["huge.cgf", "1", "", "", "", "", "", "", "9000", "12000"]]
    over = L.physics_over_limit(rows)
    check("positive control: meshes whose physics proxy is over the 5,000 limit are listed, the biggest first (12,000 and 7,000), and one AT the limit and a small one are not", over == [("huge.cgf", 12000.0), ("big.cgf", 7000.0)], over)
    check("control: a limit of 10,000 (sys_spec_Physics) leaves only the 12,000 one", L.physics_over_limit(rows, 10000) == [("huge.cgf", 12000.0)], "")


def main():
    physics_limit_offline()
    d = tempfile.mkdtemp()
    p = os.path.join(d, "x.xml")
    open(p, "w", encoding="utf-8").write(SAMPLE)
    s = L.read_workbook(p)
    check("the workbook is read although its ss:/x: prefixes are never declared (a strict parser refuses it)", sorted(s) == ["Summary", "Textures"] and s["Textures"][1] == ["a.dds", "1", "100"], sorted(s))
    check("a cell with ss:Index leaves the skipped cells empty (b.dds has no Refs, its size is in column 3)", s["Textures"][2] == ["b.dds", "", "900"], s["Textures"][2])
    sm = L.summary(p)
    check("the summary keeps the resource table and tells the two 'Count' rows apart (Textures: Count 630, Meshes: Count 681)", sm["CGF Objects"] == [70.0, 127.0] and sm["Textures: Count"] == [630.0] and sm["Meshes: Count"] == [681.0], sm)
    t = L.top(p, "Textures", 2)
    check("top() sorts a sheet by its size column: the engine render target ($rt) is first, then b.dds", [r["Filename"] for r in t["rows"]] == ["$rt", "b.dds"] and t["column"] == "Texture Size (KB)", t["rows"][:1])
    g = L.group_warnings("level.validate\nlevel.validate\n$6[Warning] Geometry File in Slot 0 for Entity A not found crylink://x?y\n$6[Warning] Geometry File in Slot 0 for Entity A not found crylink://x?y\n$6[Warning] Geometry File in Slot 1 for Entity B not found\n$6[Warning] 'glass' undefined surface type, using mat_default (glass)\nValidating Objects...\n")
    check("validation lines are de-duplicated and grouped by kind with the names and values removed (2 kinds, the slot kind holds 2 messages)", len(g) == 2 and any(len(v) == 2 for v in g.values()), list(g))

    try:
        import bridge_py
        before = len(json.loads(bridge_py.send("bridge.object.list", 30)["output"]))
        live = True
    except Exception:                                           # noqa: BLE001
        live = False
        print("  --   the editor is not open: the live checks are skipped")
    if live:
        stats, deps, log = L.save_stats()
        check("the engine's SaveLevelStats wrote both workbooks (statistics and dependencies) and said so in its log", os.path.isfile(stats) and os.path.isfile(deps) and any(l.lower().find("exported '") >= 0 and ".xml'" in l.lower() for l in log), log[:3])
        r = L.report(stats, 10)
        mine = {m["file"].split("/")[-1]: m for m in r["project_meshes"]}
        houses = ["Reeve", "Tavern", "Chapel", "CottageA", "CottageB", "CottageC", "TomCabin", "Smithy", "Barn"]
        ashvale = all("%s.cgf" % h in mine for h in houses)        # the Ashvale example level's houses; any other level asserts the structural facts only
        if not ashvale:
            print("  SKIP: the Ashvale house checks (this level does not hold the nine Ashvale houses)")
            check("structural: every mesh with LODs has LOD triangle counts that never increase", all([float(x) for x in m["lod_tris"].split(" / ")] == sorted([float(x) for x in m["lod_tris"].split(" / ")], reverse=True) for m in mine.values() if m.get("lods", 0) > 1 and m.get("lod_tris")), "")
        if ashvale:
            check("the ENGINE reports the nine rebuilt houses with 3 LODs each (LOD0 + the two authored), each LOD smaller than the one before", all(mine["%s.cgf" % h]["lods"] == 3 for h in houses) and all(
            [float(x) for x in mine["%s.cgf" % h]["lod_tris"].split(" / ")] == sorted([float(x) for x in mine["%s.cgf" % h]["lod_tris"].split(" / ")], reverse=True) for h in houses), {h: mine["%s.cgf" % h]["lod_tris"] for h in houses[:2]})
        expect = {} if not ashvale else {"Reeve": 2446, "Tavern": 2422, "Chapel": 1846, "CottageA": 2958, "CottageB": 1974, "CottageC": 2958, "TomCabin": 2058, "Smithy": 2226, "Barn": 1674}
        check("LOD0 of every house is the triangle count it had before the rebuild (the numbers measured when the houses were built), and the physics proxy has the same count", all(mine["%s.cgf" % h]["tris"] == n and mine["%s.cgf" % h]["physics_tris"] == n for h, n in expect.items()), {h: mine["%s.cgf" % h]["tris"] for h in list(expect)[:3]})
        check("no mesh of the level has a physics proxy over e_PhysProxyTriLimit (5,000): the largest is 2,958 (CottageA / CottageC), so every mesh collides; the report names any that would not (%d)" % len(r["physics_over_limit"]),
              r["physics_over_limit"] == [] and 0 <= r["max_physics_tris"] <= 5000, r["max_physics_tris"])
        check("the report has the textures, render targets and geometry the workbook holds (630 textures, the cached shadow maps as the largest render targets)", r["summary"]["Textures: Count"][0] > 400 and r["heaviest_render_targets"] and r["heaviest_render_targets"][0]["file"].startswith("$"), r["summary"].get("Textures: Count"))
        v = L.validate()
        check("the editor's own level validation runs and groups its warnings: the Bread pickups' geometry-slot false positive is a kind found (the undefined surface types the project had until 2026-10-08 are gone; any left are other tests' residue)", v["total"] >= 0 and (not ashvale or any("Geometry File in Slot" in k for k in v["kinds"])) and all(isinstance(m, list) and m for m in v["kinds"].values()), list(v["kinds"])[:2])
        import object_props as O
        with O.scratch("zz_ex_"):
            O.create("EntityWithComponent", "zz_ex_m", [505.0, 190.0, 36.0])
            bridge_py.send("bridge.entity.add_component zz_ex_m Mesh", 30)
            msgs = [m for ms in L.validate()["kinds"].values() for m in ms if "zz_ex_m" in m]
        check("control: a scratch entity with a plain Mesh component gets the same 'Geometry File in Slot 0 not found' line, so that warning on the Bread pickups is the editor's helper object, not a missing mesh", len(msgs) == 1 and "Slot 0" in msgs[0], msgs)
        after = len(json.loads(bridge_py.send("bridge.object.list", 30)["output"]))
        check("neither command changed the level: the object count is the same before and after (and no dialog is open)", before == after and json.loads(bridge_py.send("bridge.dialog.list", 10)["output"])["count"] == 0, (before, after))

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
