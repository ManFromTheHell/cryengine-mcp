#!/usr/bin/env python3
"""The game's surface -> impact table as the engine's MaterialEffects data.

    materialfx.py write [--overwrite]   (--overwrite is required when the files already exist) write Assets/libs/materialeffects/materialeffects.xml and fxlibs/ashvale_impact.xml from the tables below
    materialfx.py check      read both back by the rules of CMaterialEffects::LoadSpreadSheet / LoadFXLibrary (offline) and report what would not resolve

What the engine does (CryAction/MaterialEffects/MaterialEffects.cpp):
  * `libs/materialeffects/materialeffects.xml` is an Excel SpreadsheetML workbook, read with IXmlTableReader. Row 0: the surface types as columns (a name the surface-type
    manager does not know is skipped with a warning "MFX WARNING: Material not found"). Rows 1..n (n = the number of columns) are the surface-vs-surface block; its first cell names
    the row (kept for the Excel reader, unused here). Rows after that are CUSTOM rows: the first cell is a name ("ash_blade") and each column's cell is "<library>:<effect>".
    IMaterialEffects::GetEffectId("ash_blade", surfaceIndex) answers the effect id of that cell, ExecuteEffect(id, params) plays it.
  * `libs/materialeffects/fxlibs/<library>.xml` (<FXLib><Effect name delay>...) defines an effect as any of <Audio trigger>, <Particle><Name>, <RandEffect><Decal><Material>, <FlowGraph>.
  * The engine calls CompleteInit() itself (CCryAction::CompleteInit and after a level's static world), so a project only ships the two files and asks for ids.
The writer emits the workbook the way Excel's XML export does (the sample's header, one <Row> per row, `ss:Index` for the first cell of the header row), without its styling.
Nothing here is a second engine: the check only predicts what the real loader will do, and the game's `claude_mfx_info` command reads the engine's real answer back.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
MFX_DIR = os.path.join(ASSETS, "libs", "materialeffects")
SHEET = os.path.join(MFX_DIR, "materialeffects.xml")
LIB_NAME = "ashvale_impact"
LIB = os.path.join(MFX_DIR, "fxlibs", LIB_NAME + ".xml")
SURFACE_TYPES = os.path.join(MFX_DIR, "SurfaceTypes.xml")
SFX_TRIGGERS = os.path.join(ASSETS, "audio", "sdlmixer", "ace", "claude_sfx.xml")
PARTICLE_LIBS = os.path.join(ASSETS, "libs", "particles")

# ------------------------------------------------------------------ the data (edit here, then `write`)
# effect name -> {audio: ACE trigger, particle: library effect, decal: (material, min, max, lifetime) or None, delay: seconds}
EFFECTS = {
    "stone": dict(audio="sfx_impact_stone", particle="ashfx.hit_concrete.hit"),
    "wood":  dict(audio="sfx_impact_wood", particle="ashfx.hit_wood.hit"),
    "metal": dict(audio="sfx_impact_metal", particle="ashfx.hit_metal.hit"),
    "soil":  dict(audio="sfx_impact_dirt", particle="ashfx.hit_soil.hit"),
    "leaf":  dict(audio="sfx_impact_veg"),
    "glass": dict(audio="sfx_impact_glass"),
    "flesh": dict(audio="sfx_hit_flesh_b", particle="ashfx.hit_flesh.b", decal=("materials/decals/blood/blood_splat1", 0.35, 0.55, 30.0)),
    # an arrow that lands: the sound only (no sparks or chips: the arrow is small and the dust belongs to a blade)
    "arrow_wood":  dict(audio="sfx_impact_wood"),
    "arrow_stone": dict(audio="sfx_impact_stone", particle="ashfx.hit_concrete.hit"),
    "arrow_soil":  dict(audio="sfx_impact_dirt"),
}

# row (what hit) -> {surface type: effect name}. A surface with no entry has NO effect: the caller keeps its own response.
ROWS = {
    "ash_blade": {
        "mat_concrete": "stone", "mat_rock": "stone", "mat_wood": "wood", "mat_wood_breakable": "wood", "mat_metal": "metal",
        "mat_soil": "soil", "mat_grass": "soil", "mat_snow": "soil", "mat_vegetation": "leaf", "mat_canopy": "leaf",
        "mat_glass": "glass", "mat_glass_unbreakable": "glass", "mat_flesh": "flesh",
    },
    "ash_arrow": {
        "mat_concrete": "arrow_stone", "mat_rock": "arrow_stone", "mat_wood": "arrow_wood", "mat_wood_breakable": "arrow_wood",
        "mat_soil": "arrow_soil", "mat_grass": "arrow_soil", "mat_metal": "metal", "mat_flesh": "flesh",
    },
}


def surface_names():
    """The project's surface types in their file order (the columns)."""
    root = ET.parse(SURFACE_TYPES).getroot()
    return [e.get("name") for e in root.iter("SurfaceType") if e.get("name")]


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


HEADER = """<?xml version="1.0"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:html="http://www.w3.org/TR/REC-html40">
 <Styles>
  <Style ss:ID="Default" ss:Name="Normal">
   <Alignment ss:Vertical="Bottom"/>
   <Borders/>
   <Font ss:FontName="Arial"/>
   <Interior/>
   <NumberFormat/>
   <Protection/>
  </Style>
 </Styles>
 <Worksheet ss:Name="Sheet1">
  <Table ss:ExpandedColumnCount="%d" ss:ExpandedRowCount="%d" x:FullColumns="1" x:FullRows="1" ss:DefaultRowHeight="15">
"""
FOOTER = """  </Table>
 </Worksheet>
</Workbook>
"""


def write_workbook(path, surfaces, rows):
    """rows: {row name: {surface: 'library:effect'}} -> the workbook text (also written to `path` when given)."""
    n = len(surfaces)
    out = [HEADER % (n + 1, 1 + n + len(rows))]
    out.append('   <Row>\n    <Cell ss:Index="2"><Data ss:Type="String">%s</Data></Cell>\n' % esc(surfaces[0]))
    for s in surfaces[1:]:
        out.append('    <Cell><Data ss:Type="String">%s</Data></Cell>\n' % esc(s))
    out.append("   </Row>\n")
    for s in surfaces:                               # the surface-vs-surface block: names only (n rows), so the custom rows start after row n
        out.append('   <Row>\n    <Cell><Data ss:Type="String">%s</Data></Cell>\n   </Row>\n' % esc(s))
    for name, cells in rows.items():
        out.append('   <Row>\n    <Cell><Data ss:Type="String">%s</Data></Cell>\n' % esc(name))
        col = 0
        for i, s in enumerate(surfaces):
            if s in cells:
                if col != i:                         # a gap: say which column the next cell is (ss:Index is 1-based)
                    out.append('    <Cell ss:Index="%d"><Data ss:Type="String">%s</Data></Cell>\n' % (i + 2, esc(cells[s])))
                else:
                    out.append('    <Cell><Data ss:Type="String">%s</Data></Cell>\n' % esc(cells[s]))
                col = i + 1
        out.append("   </Row>\n")
    text = "".join(out) + FOOTER
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return text


def write_library(path, effects):
    out = ["<FXLib>\n\n"]
    for name, e in effects.items():
        out.append('\t<Effect name="%s" delay="%s">\n' % (name, e.get("delay", "0.0")))
        if e.get("audio"):
            out.append('\t\t<Audio trigger="%s"/>\n' % e["audio"])
        if e.get("particle"):
            out.append("\t\t<Particle>\n\t\t\t<Name>%s</Name>\n\t\t</Particle>\n" % e["particle"])
        if e.get("decal"):
            mat, lo, hi, life = e["decal"]
            out.append('\t\t<RandEffect>\n\t\t\t<Decal minscale="%g" maxscale="%g" lifetime="%g">\n\t\t\t\t<Material>%s</Material>\n\t\t\t</Decal>\n\t\t</RandEffect>\n' % (lo, hi, life, mat))
        out.append("\t</Effect>\n\n")
    out.append("</FXLib>\n")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(out))


def read_workbook(path):
    """(columns {index: surface name}, surface rows [names], custom rows {name: {col index: cell text}}) the way LoadSpreadSheet sees them."""
    ns = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}
    key = "{urn:schemas-microsoft-com:office:spreadsheet}Index"
    root = ET.parse(path).getroot()
    rows = []
    for row in root.iter("{urn:schemas-microsoft-com:office:spreadsheet}Row"):
        cells, col = {}, 0
        for cell in row.findall("ss:Cell", ns):
            if cell.get(key):
                col = int(cell.get(key)) - 1
            data = cell.find("ss:Data", ns)
            if data is not None and (data.text or "").strip():
                cells[col] = data.text.strip()
            col += 1
        rows.append(cells)
    columns = {i: t for i, t in rows[0].items() if i > 0}
    max_col = max(columns) if columns else 0
    surface_rows = [r.get(0, "") for r in rows[1:max_col + 1]]
    custom = {r[0]: {i: t for i, t in r.items() if i > 0} for r in rows[max_col + 1:] if 0 in r}
    return columns, surface_rows, custom


def lib_effects(path):
    root = ET.parse(path).getroot()
    out = {}
    for e in root.iter("Effect"):
        a = e.find("Audio")
        p = e.find("Particle/Name")
        d = e.find("RandEffect/Decal/Material")
        out[e.get("name")] = dict(audio=a.get("trigger") if a is not None else None, particle=(p.text or "").strip() if p is not None else None,
                                  decal=(d.text or "").strip() if d is not None else None)
    return out


def particle_effects():
    names = set()
    if not os.path.isdir(PARTICLE_LIBS):
        return names
    for f in os.listdir(PARTICLE_LIBS):
        if f.lower().endswith(".xml"):
            lib = f[:-4]
            for m in re.finditer(r'^ <Particles Name="([^"]+)"', open(os.path.join(PARTICLE_LIBS, f), encoding="utf-8", errors="replace").read(), re.M):
                names.add(lib + "." + m.group(1))
    return names


def check():
    """Findings as (code, text): M001 a column that is not a surface type, M002 fewer surface rows than columns, M003 a cell whose library or effect is missing,
    M004 an audio trigger that is not in claude_sfx.xml, M005 a particle effect that no library holds, M006 a decal material file missing, M007 an effect no cell uses, M008 a custom row the table defines is read as a surface row."""
    findings = []
    surfaces = set(surface_names())
    columns, surface_rows, custom = read_workbook(SHEET)
    for i, name in columns.items():
        if name not in surfaces:
            findings.append(("M001", "column %d '%s' is not a surface type of SurfaceTypes.xml (the engine skips it with a warning)" % (i, name)))
    if len(columns) > len(surface_rows):
        findings.append(("M002", "%d columns but %d surface rows (the engine warns 'not enough rows' and the custom rows start too early)" % (len(columns), len(surface_rows))))
    for name in ROWS:
        if name not in custom:
            findings.append(("M008", "the custom row '%s' is not seen as one: the surface-row block above it is shorter than the column count, so the engine reads it as a surface row and never as a name" % name))
    effects = lib_effects(LIB) if os.path.isfile(LIB) else {}
    triggers = set(re.findall(r'<Trigger name="([^"]+)"', open(SFX_TRIGGERS, encoding="utf-8", errors="replace").read())) if os.path.isfile(SFX_TRIGGERS) else set()
    pfx = particle_effects()
    used = set()
    for row, cells in custom.items():
        for col, text in cells.items():
            lib, _, eff = text.partition(":")
            if lib != LIB_NAME or eff not in effects:
                findings.append(("M003", "row '%s' column %d (%s): '%s' is not an effect of library %s" % (row, col, columns.get(col, "?"), text, LIB_NAME)))
            else:
                used.add(eff)
    for name, e in effects.items():
        if e["audio"] and e["audio"] not in triggers:
            findings.append(("M004", "effect '%s': audio trigger '%s' is not defined in claude_sfx.xml" % (name, e["audio"])))
        if e["particle"] and e["particle"] not in pfx:
            findings.append(("M005", "effect '%s': particle effect '%s' is in no libs/particles library" % (name, e["particle"])))
        if e["decal"] and not os.path.isfile(os.path.join(ASSETS, e["decal"].replace("/", os.sep) + ".mtl")):
            findings.append(("M006", "effect '%s': decal material '%s' has no .mtl" % (name, e["decal"])))
        if name not in used:
            findings.append(("M007", "effect '%s' is used by no cell" % name))
    return findings, {"columns": len(columns), "surface_rows": len(surface_rows), "custom_rows": {r: len(c) for r, c in custom.items()}, "effects": len(effects)}


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "write":
        surfaces = surface_names()
        rows = {r: {s: "%s:%s" % (LIB_NAME, e) for s, e in cells.items()} for r, cells in ROWS.items()}
        for r, cells in ROWS.items():
            bad = [s for s in cells if s not in surfaces]
            if bad:
                raise SystemExit("row %s names surface types the project does not define: %s" % (r, bad))
        existing = [p for p in (SHEET, LIB) if os.path.exists(p)]
        if existing and "--overwrite" not in argv:
            raise SystemExit("%s already exist(s): `write` replaces them; run snapshot_project first, then pass --overwrite" % ", ".join(existing))
        write_workbook(SHEET, surfaces, rows)
        write_library(LIB, EFFECTS)
        print("wrote", SHEET, "and", LIB)
    findings, summary = check()
    print(summary)
    for code, text in findings:
        print(code, text)
    print("%d finding(s)" % len(findings))
    return 1 if any(c in ("M001", "M002", "M003", "M004", "M005", "M006", "M008") for c, _ in findings) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
