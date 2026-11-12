#!/usr/bin/env python3
"""The level's resource report as the ENGINE writes it (ENGINE_LEARNING_PLAN A5), from the open editor, without running the game.

The editor command `level.save_stats` runs the engine's console command SaveLevelStats (CrySystem/Statistics.cpp): two Excel-format XML workbooks in %USER%/TestResults, `<level>.xml` (17 sheets: Summary,
Modules Memory Info, Memory Stats, Static Geometry, Characters, Meshes, Brushes, Textures, Materials, Materials Unused Textures, Profiler, Peaks, Budget, Physical Entities, Entities, AnimationKeys Info,
Streaming Info) and `depends_<level>.xml` (every asset, how many instances and dependencies, its size). The files are not well-formed XML (the `ss:` and `x:` prefixes are never declared: Excel does not care);
this module declares them, reads the worksheets into tables, and answers the questions a person asks Excel: what is the heaviest, what is unused, how many of each.

    python level/level_stats.py            runs level.save_stats (needs the editor) and prints the summary
    python level/level_stats.py top Textures 15

The editor has only the resources it has loaded (the level's brushes, entities, materials, textures, characters), and no frame statistics (load time and fps read 0): it is a resource census, not a profile.
`level.validate` (the editor's own "Check Level for Errors") is wrapped too: `validate()` returns its warnings grouped by kind.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
RESULTS = os.path.join(PROJECT, "user", "testresults")
SS = "{urn:schemas-microsoft-com:office:spreadsheet}"
NS = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}

sys.path.insert(0, HERE)


class StatsError(RuntimeError):
    pass


# Game-specific mesh folder reported as `project_meshes` (lower case, with slashes); set CRYENGINE_MESH_PREFIX for another game, e.g. "objects/mygame/".
MESH_PREFIX = (os.environ.get("CRYENGINE_MESH_PREFIX") or "objects/ashvale/").lower()


def level_name():
    """The level the open editor holds (the engine names its stats files after it); StatsError when the editor holds no level."""
    import open_level
    name = open_level.name()
    if not name:
        raise StatsError("no level is open in the editor (set CRYENGINE_DEFAULT_LEVEL or open one)")
    return name


def read_workbook(path):
    """{sheet name: [[cell text, ...], ...]}: a SpreadsheetML file as the engine writes it (undeclared ss:/x: prefixes are declared first)."""
    text = open(path, encoding="utf-8", errors="replace").read()
    text = re.sub(r"<Workbook\b[^>]*>", '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet" xmlns:x="urn:schemas-microsoft-com:office:excel">', text, count=1)
    text = text.replace('<ExcelWorkbook xmlns="urn:schemas-microsoft-com:office:excel"/>', "")
    root = ET.fromstring(text.encode("utf-8"))
    out = {}
    for ws in root.findall("ss:Worksheet", NS):
        rows = []
        for row in ws.iter(SS + "Row"):
            cells, idx = [], 0
            for c in row.findall("ss:Cell", NS):
                i = c.get(SS + "Index")
                if i:
                    while idx < int(i) - 1:
                        cells.append("")
                        idx += 1
                d = c.find("ss:Data", NS)
                cells.append((d.text or "") if d is not None else "")
                idx += 1
            rows.append(cells)
        out[ws.get(SS + "Name")] = rows
    return out


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def save_stats(timeout=120):
    """Runs level.save_stats in the open editor and returns (stats workbook path, dependency workbook path, the engine's log lines)."""
    import bridge_py
    before = {}
    lvl = level_name()
    for f in ("%s.xml" % lvl, "depends_%s.xml" % lvl):
        p = os.path.join(RESULTS, f)
        before[p] = os.path.getmtime(p) if os.path.isfile(p) else 0
    r = bridge_py.send("bridge.execute_command level.save_stats", timeout)
    if not r.get("ok"):
        raise StatsError("the editor refused: %s" % r.get("error"))
    log = json.loads(r["output"]).get("log", "")
    paths = []
    for p, was in before.items():
        if not os.path.isfile(p) or os.path.getmtime(p) <= was:
            raise StatsError("the engine did not write %s (log: %s)" % (p, log[-300:]))
        paths.append(p)
    return paths[0], paths[1], [l for l in dict.fromkeys(x.strip() for x in log.splitlines()) if l]


def tables(stats_path):
    """The engine's workbook as tables: {sheet: {"header": [...], "rows": [[...]]}}. The header is the first row whose cells are all text and has at least 3 cells (the sheets start with a title)."""
    sheets = read_workbook(stats_path)
    out = {}
    for name, rows in sheets.items():
        header_at = 0
        for i, r in enumerate(rows[:8]):
            if len(r) >= 3 and all(c and _num(c) is None for c in r[:3]):
                header_at = i
                break
        out[name] = {"header": rows[header_at] if rows else [], "rows": rows[header_at + 1:], "all_rows": rows}
    return out


def _cell(r, i):
    return r[i] if i < len(r) else ""


def summary(stats_path):
    """{label: [numbers or text]}: every labelled row of the Summary sheet ('Count' under Textures and under Meshes are told apart as 'Textures: Count' and 'Meshes: Count'; the resource table's rows keep their
    names). Sizes are in MB as the engine states them."""
    rows = read_workbook(stats_path)["Summary"]
    out, section = {}, ""
    for r in rows:
        if not r or not r[0]:
            continue
        if len(r) == 1:
            section = r[0]
            out.setdefault(r[0], [])
            continue
        label = r[0] if r[0] not in ("Count", "Total Size", "System Memory", "Video Memory") else "%s: %s" % (section, r[0])
        out[label] = [(_num(x) if _num(x) is not None else x) for x in r[1:]]
    return out


def top(stats_path, sheet, n=15, column=None):
    """The n rows with the largest number in `column` (default: the first column whose header names a size: 'size', 'mem', 'kb', 'mb') of a sheet, as dicts keyed by the header."""
    t = tables(stats_path)
    if sheet not in t:
        raise StatsError("no sheet %r (sheets: %s)" % (sheet, ", ".join(t)))
    header, rows = t[sheet]["header"], t[sheet]["rows"]
    if column is None:
        for i, h in enumerate(header):
            if re.search(r"size|mem|kb|mb", h, re.I) and any(_num(r[i]) is not None for r in rows if i < len(r)):
                column = h
                break
    if column is None or column not in header:
        raise StatsError("sheet %r has no size column (header: %s)" % (sheet, header))
    ci = header.index(column)
    scored = [(_num(r[ci]) if ci < len(r) and _num(r[ci]) is not None else -1, r) for r in rows]
    scored.sort(key=lambda x: -x[0])
    return {"sheet": sheet, "column": column, "rows": [dict(zip(header, r)) for _, r in scored[:n] if _ >= 0]}


PHYS_PROXY_LIMIT = 5000        # e_PhysProxyTriLimit: 5000 in the source, 10000 from sys_spec_Physics; a mesh whose physics proxy is over it has NO COLLISIONS ("No Collisions, Proxy Too Big!")


def physics_over_limit(rows, limit=PHYS_PROXY_LIMIT):
    """[(file, physics triangles)] of the Static Geometry rows (column 9 = Physics Tris) whose proxy is over the limit, the biggest first."""
    out = [(_cell(r, 0), _num(_cell(r, 9))) for r in rows if (_num(_cell(r, 9)) or 0) > limit]
    return sorted(out, key=lambda x: -x[1])


def report(stats_path, n=10):
    """The questions people ask the workbook: -> {summary, heaviest_textures (not engine render targets), heaviest_render_targets, heaviest_geometry (by texture + mesh KB), project_meshes (objects/ashvale/*: LODs and
    triangles per LOD as the ENGINE loaded them, physics triangles), no_lod_meshes (1,000+ triangles and no LOD), characters, entities, notes}."""
    t = tables(stats_path)
    tex = t["Textures"]["rows"]
    user_tex = [r for r in tex if not _cell(r, 0).startswith("$")]
    rts = [r for r in tex if _cell(r, 0).startswith("$")]
    key = lambda r: -(_num(_cell(r, 2)) or 0)
    tx = lambda r: {"file": _cell(r, 0), "refs": _num(_cell(r, 1)), "kb": _num(_cell(r, 2)), "resolution": _cell(r, 3), "format": _cell(r, 6), "usage": _cell(r, 7)}
    sg = t["Static Geometry"]["rows"]
    geo = lambda r: {"file": _cell(r, 0), "refs": _num(_cell(r, 1)), "mesh_kb": _num(_cell(r, 2)), "texture_kb": _num(_cell(r, 3)), "draw_calls": _num(_cell(r, 4)), "lods": _num(_cell(r, 5)), "tris": _num(_cell(r, 8)),
                     "physics_tris": _num(_cell(r, 9)), "lod_tris": _cell(r, 11)}
    mine = [geo(r) for r in sg if MESH_PREFIX in _cell(r, 0).lower()]
    mine.sort(key=lambda g: -(g["tris"] or 0))
    nolod = [geo(r) for r in sg if (_num(_cell(r, 8)) or 0) >= 1000 and not (_num(_cell(r, 5)) or 0)]
    nolod.sort(key=lambda g: -(g["tris"] or 0))
    over = [{"file": f, "physics_tris": count} for f, count in physics_over_limit(sg)]
    most = max([(_num(_cell(r, 9)) or 0) for r in sg] or [0])
    return {"summary": summary(stats_path),
            "heaviest_textures": [tx(r) for r in sorted(user_tex, key=key)[:n]],
            "heaviest_render_targets": [tx(r) for r in sorted(rts, key=key)[:max(3, n // 2)]],
            "heaviest_geometry": [geo(r) for r in sorted(sg, key=lambda r: -((_num(_cell(r, 2)) or 0) + (_num(_cell(r, 3)) or 0)))[:n]],
            "project_meshes": mine, "no_lod_meshes": nolod[:n], "physics_over_limit": over[:n], "max_physics_tris": most,
            "characters": [{"file": _cell(r, 0), "instances": _num(_cell(r, 1)), "lods": _num(_cell(r, 5))} for r in t["Characters"]["rows"] if _cell(r, 0)],
            "entities": len(t["Entities"]["rows"]),
            "notes": ["the editor holds what it has loaded: a resource census, no frame statistics (load time and fps read 0)",
                      "$-named textures are render targets and pools (shadow maps, terrain pool, probes), not project assets",
                      "physics_over_limit lists meshes whose physics proxy has more than %d triangles (e_PhysProxyTriLimit: such a mesh has no collisions)" % PHYS_PROXY_LIMIT]}


def group_warnings(log):
    """{kind: [message]}: the engine's validation lines (the log of the command), de-duplicated (the engine writes each line twice), the crylink and the names removed from the kind."""
    lines = list(dict.fromkeys(re.sub(r"\$\d", "", l).strip() for l in log.splitlines() if l.strip()))
    kinds = {}
    for l in lines:
        if not l.startswith("[Warning]") and not l.startswith("[Error]"):
            continue
        text = re.sub(r"crylink://\S+", "", l).strip()
        kind = re.sub(r"Entity \S+", "Entity <name>", text)
        kind = re.sub(r"'[^']*'", "'<name>'", kind)
        kind = re.sub(r"\(([^)]*)\)\s*$", "(<value>)", kind)
        kind = re.sub(r"Slot \d+", "Slot N", kind)
        kinds.setdefault(kind, []).append(text)
    return kinds


def validate():
    """The editor's own 'Check Level for Errors' (level.validate): every object validates itself, every object's used resources are checked, duplicates are looked for. -> {total, kinds: {kind: [message]}}."""
    import bridge_py
    r = bridge_py.send("bridge.execute_command level.validate", 120)
    if not r.get("ok"):
        raise StatsError("the editor refused: %s" % r.get("error"))
    kinds = group_warnings(json.loads(r["output"]).get("log", ""))
    return {"total": sum(len(v) for v in kinds.values()), "kinds": kinds}


def main():
    a = sys.argv[1:]
    stats, deps, log = save_stats()
    if a and a[0] == "top":
        r = top(stats, a[1] if len(a) > 1 else "Textures", int(a[2]) if len(a) > 2 else 15)
        print("%s by %s" % (r["sheet"], r["column"]))
        for row in r["rows"]:
            print("  ", {k: v[:40] for k, v in list(row.items())[:7]})
        return
    r = report(stats)
    for k, v in r["summary"].items():
        print("%-48s %s" % (k, v))
    print("project meshes (LODs as loaded by the engine):")
    for m in r["project_meshes"][:12]:
        print("  %-36s lods %s  tris %s  physics %s  %s" % (m["file"], m["lods"], m["tris"], m["physics_tris"], m["lod_tris"]))


if __name__ == "__main__":
    main()
