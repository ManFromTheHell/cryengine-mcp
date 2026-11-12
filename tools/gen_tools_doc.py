#!/usr/bin/env python3
"""Generate docs/tools.md (from server.py) and docs/modules.md (from tools/module_meta.json).

Run from the repository root:

    python tools/gen_tools_doc.py            # writes docs/tools.md and docs/modules.md
    python tools/gen_tools_doc.py --check    # writes nothing; exit 1 if either file is out of date
    python tools/gen_tools_doc.py --help     # prints this usage and writes nothing

Any other argument is an error (exit 2, nothing is written).

What it reads
  server.py              parsed with ast (never imported, so no editor and no MCP
                         runtime is needed): every function decorated with
                         @mcp.tool(...) gives the name, the signature and the first
                         paragraph of the docstring.
  tools/module_meta.json per-module purpose, category, status, dependencies for
                         every module in the tool folders (see build_modules).
  tools/module_folders.json module file name -> folder; used to print the module
                         paths in docs/modules.md and to find the files on disk.
  tools/tool_meta.json   per-tool family, editor requirement, side effects, helper
                         modules. Reviewed by hand against the source; it is the one
                         part of the document that is not derived from server.py.

A tool that is in server.py but not in tool_meta.json gets the family "other" and
the value "unknown" in every metadata column, and is listed again at the end of the
document. A name in tool_meta.json that is not in server.py is reported on stderr
and left out of the document.

Standard library only.
"""
import ast
import inspect
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server.py"
META = ROOT / "tools" / "tool_meta.json"
OUT = ROOT / "docs" / "tools.md"

# Section order. Families that appear in tool_meta.json but not here are appended
# alphabetically; "other" is always last.
FAMILY_ORDER = [
    "Project and server",
    "Editor and bridge control",
    "Level and objects",
    "Terrain and world building",
    "Assets",
    "Audio",
    "Animation and character",
    "Screenshots and visual checks",
    "Jobs and tests",
    "Snapshots",
    "Validators",
    "Catalogs and manual",
    "Project study and knowledge",
]

FAMILY_BLURB = {
    "Project and server": "Server and project state, the editor probe, the saved-output pager and the tool map.",
    "Editor and bridge control": "Raw access to the running Sandbox editor through the bridge plugin: console, commands, Python, windows, dialogs, undo, notifications.",
    "Level and objects": "Read and change the open level: objects, entities, archetypes, components, navigation areas and AI paths.",
    "Terrain and world building": "Create a level and shape terrain, terrain layers and vegetation.",
    "Assets": "Create and inspect meshes, materials, textures, particles and asset metadata.",
    "Audio": "Voice, music and audio-trigger tools. `preview_voice`, `render_voices` and `make_music` are tied to the Ashvale example project.",
    "Animation and character": "Animation, Mannequin and character-physics checks.",
    "Screenshots and visual checks": "Pictures of the editor viewport or of a packaged game, and picture comparison. `visual_check` and the fixed view lists it uses are tied to the Ashvale example project.",
    "Jobs and tests": "Detached, whitelisted background jobs (package, tests, build) and the engine's own unit tests.",
    "Snapshots": "Project snapshots and restore, for use before bulk changes.",
    "Validators": "Check project data by loading it through the engine's own loaders.",
    "Catalogs and manual": "Searchable reference: the offline manual, console variables, editor API, Schematyc, shaders, behaviour trees, UQS, Flow nodes, particle features, editor commands.",
    "Project study and knowledge": "Read-only inspection of a project's levels, game data and XML, plus the bundled knowledge corpus.",
}

UNKNOWN = "unknown"


def tool_nodes(tree):
    """Functions decorated with @mcp.tool or @mcp.tool(...), in file order."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if (isinstance(target, ast.Attribute) and target.attr == "tool"
                    and isinstance(target.value, ast.Name) and target.value.id == "mcp"):
                found.append(node)
                break
    found.sort(key=lambda n: n.lineno)
    return found


def signature(node):
    """name(params) -> ret, from the AST: annotations and defaults as written."""
    a = node.args
    parts = []
    pos = list(a.posonlyargs) + list(a.args)
    defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)

    def one(arg, default):
        s = arg.arg
        if arg.annotation is not None:
            s += ": " + ast.unparse(arg.annotation)
        if default is not None:
            s += (" = " if arg.annotation is not None else "=") + ast.unparse(default)
        return s

    for i, (arg, d) in enumerate(zip(pos, defaults)):
        parts.append(one(arg, d))
        if a.posonlyargs and i == len(a.posonlyargs) - 1:
            parts.append("/")
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    elif a.kwonlyargs:
        parts.append("*")
    for arg, d in zip(a.kwonlyargs, a.kw_defaults):
        parts.append(one(arg, d))
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    ret = " -> " + ast.unparse(node.returns) if node.returns is not None else ""
    return "%s(%s)%s" % (node.name, ", ".join(parts), ret)


def first_paragraph(node):
    doc = ast.get_docstring(node, clean=True)
    if not doc:
        return ""
    para = re.split(r"\n\s*\n", inspect.cleandoc(doc).strip())[0]
    return " ".join(para.split())


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def build():
    src = SERVER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    nodes = tool_nodes(tree)
    meta = json.loads(META.read_text(encoding="utf-8"))

    names = [n.name for n in nodes]
    dupes = sorted({n for n in names if names.count(n) > 1})
    grep_count = len(re.findall(r"^@mcp\.tool\b", src, re.M))
    problems = []
    if dupes:
        problems.append("duplicate tool names in server.py: " + ", ".join(dupes))
    if grep_count != len(nodes):
        problems.append("ast found %d tools, a line count of '@mcp.tool' found %d" % (len(nodes), grep_count))
    stale = sorted(set(meta) - set(names))
    for n in stale:
        problems.append("tool_meta.json lists '%s' which is not in server.py (ignored)" % n)

    tools = []
    for node in nodes:
        m = meta.get(node.name)
        tools.append({
            "name": node.name,
            "line": node.lineno,
            "sig": signature(node),
            "doc": first_paragraph(node),
            "family": m["family"] if m else "other",
            "editor": m["editor"] if m else UNKNOWN,
            "writes": m["writes"] if m else UNKNOWN,
            "closes": m["closes"] if m else UNKNOWN,
            "launches": m["launches"] if m else UNKNOWN,
            "effects": (m["effects"] + ("; editor: " + m["editor_note"] if m.get("editor_note") else "")) if m else UNKNOWN,
            "helpers": m["helpers"] if m else UNKNOWN,
            "known": m is not None,
        })

    fams = {}
    for t in tools:
        fams.setdefault(t["family"], []).append(t)
    order = [f for f in FAMILY_ORDER if f in fams]
    order += sorted(f for f in fams if f not in FAMILY_ORDER and f != "other")
    if "other" in fams:
        order.append("other")

    L = []
    w = L.append
    w("# Tool reference")
    w("")
    w("Generated by `tools/gen_tools_doc.py` from `server.py`. Do not edit by hand; edit the docstrings in `server.py` or `tools/tool_meta.json` and run the generator again.")
    w("")
    w("This server targets CRYENGINE 5.7.1. Tool names, signatures and the first paragraph of each description are read from `server.py` with `ast`. "
      "The flag columns, the side-effect text and the helper modules come from a source review recorded in `tools/tool_meta.json`; they were not produced by running the tools. "
      "Where the review did not read a helper module, the entry says \"not verified\".")
    w("")
    w("## How to read the tables")
    w("")
    w("Each family section has two parts: a table of flags and side effects, then a list of signatures with the description from the tool's docstring (its first paragraph). "
      "At run time, `tool_guide` returns the same kind of map from the registered tools; its grouping differs from the one used here.")
    w("")
    w("| Column | Meaning |")
    w("|---|---|")
    w("| Needs editor | Whether the tool talks to a running Sandbox editor through the bridge plugin (TCP port 37778). `yes`: it fails or returns an error without one. `no`: it works without an editor. `optional`: it works offline under a condition given in the side-effects text or the description (for example an `asset_dir` argument, a `refresh` flag, or a mode that only probes). `cache`: it answers from a saved dump; only a first run or a refresh asks the editor. |")
    w("| Writes files | `yes`: it writes files under the project, the server's data folders or the engine's output folders (pictures, caches, level files, logs). `conditional`: only with a certain argument (for example `apply=true`, `persist=true`, `accept_baseline`). `cache`: it only writes its own cache file. `no`: it reads, or changes editor state in memory only. `not verified`: the review did not read the code that would decide it. |")
    w("| Closes editor | `yes`: the tool closes Sandbox.exe to run, as a graceful save-and-close (it refuses unless `allow_editor_close=true` is passed). `depends on kind`: `start_job` closes the editor only for some job kinds, see below. `no`: it does not close the editor. |")
    w("| Launches game | `yes`: the tool starts the packaged game executable. `depends on kind`: see `start_job`. `no`: it does not. |")
    w("| Side effects | Condensed from the review. \"Unsaved\" means the change is in the editor's memory until the level is saved. |")
    w("| Helpers | The Python modules (and a few functions) in this repository that do the work. See [modules.md](modules.md). |")
    w("")
    w("`wait_for_editor`, `health_check` and `project_info` are probes: they report the state of the editor or the project and change nothing (`wait_for_editor` only starts Sandbox when `CLAUDE_AUTO_RECOVER=1`, which is off by default).")
    w("")
    w("Editor changes are not file writes: a tool marked `Writes files: no` and `Needs editor: yes` can still change the open level in memory (see its side-effects text), and a graceful editor close can save such changes.")
    w("")
    w("### Prerequisites per tool")
    w("")
    w("Some tools need something that is not part of this repository. The prerequisite is named in the tool's description; the common ones are:")
    w("")
    w("- A saved offline copy of the CRYENGINE manual (`search_manual`, `read_manual_page`, `manual_toc`): not shipped; set `CRYENGINE_DOCS`, see `catalogs/scrape_cryengine_docs.py`.")
    w("- The CRYENGINE engine source tree, via the environment variable `CRYENGINE_SOURCE` (some catalogs and checkers).")
    w("- The GameSDK sample installed with the engine (`gamesdk_audio`, `study_gamedata` and similar).")
    w("- External programs for the audio tools (Piper for voices, ffmpeg for conversion) and, for the animation scripts, a local MotionBricks or Kimodo install.")
    w("")
    w("`docs/installation.md` lists the setup steps. A tool whose family or description names the Ashvale example (`preview_voice`, `render_voices`, `make_music`, `visual_check` and the tools built on the Ashvale shot lists) is tied to that example project and is not a general tool.")
    w("")
    w("### Catalogs captured from the editor")
    w("")
    w("The catalog tools answer from dumps in `engine_data/`. Most dumps are captured once from a running editor and are not all shipped: `schematyc_env.json`, `uqs_catalog.json` and `editor_api_samples.json` are not in a fresh clone. "
      "A tool whose dump is missing has nothing to answer from until the matching capture command (named in its description, for example `python catalogs/schematyc_catalog.py --capture`) has been run once with the editor open. "
      "Such tools are marked `cache` in the Needs editor column.")
    w("")
    w("`start_job` job kinds (from `bridge/jobs.py`; the tool takes the kind as an argument):")
    w("")
    jk = job_kinds()
    if jk:
        w("| Job kind | Closes editor | Launches game |")
        w("|---|---|---|")
        for k, c, g in jk:
            w("| `%s` | %s | %s |" % (k, "yes" if c else "no", "yes" if g else "no"))
        w("")
        w("A job that closes the editor is refused while `Sandbox.exe` runs unless `allow_editor_close=true` is passed.")
    else:
        w("(The job kinds table could not be read from `bridge/jobs.py`; not verified.)")
    w("")
    w("## Families")
    w("")
    w("| Family | Tools |")
    w("|---|---|")
    for f in order:
        w("| [%s](#%s) | %d |" % (f, anchor(f), len(fams[f])))
    w("| **Total** | **%d** |" % len(tools))
    w("")

    for f in order:
        ts = fams[f]
        w("## " + f)
        w("")
        if f in FAMILY_BLURB:
            w(FAMILY_BLURB[f])
            w("")
        w("| Tool | Needs editor | Writes files | Closes editor | Launches game | Side effects | Helpers |")
        w("|---|---|---|---|---|---|---|")
        for t in ts:
            w("| `%s` | %s | %s | %s | %s | %s | %s |" % (
                t["name"], t["editor"], t["writes"], t["closes"], t["launches"],
                cell(t["effects"]), cell(pathify(t["helpers"]))))
        w("")
        for t in ts:
            w("- `%s`" % t["sig"].replace("`", "'"))
            w("  %s" % (pathify(t["doc"]) if t["doc"] else "(no docstring)"))
        w("")

    w("## Tool count")
    w("")
    w("- `@mcp.tool` functions in `server.py`: **%d** (ast), %d (line count of `^@mcp.tool`)." % (len(tools), grep_count))
    w("- Needs editor: yes %d, optional %d, cache %d, no %d." % tuple(sum(1 for t in tools if t["editor"] == v) for v in ("yes", "optional", "cache", "no")))
    w("- Writes files: yes %d, conditional %d, cache %d, not verified %d, no %d." % tuple(sum(1 for t in tools if t["writes"] == v) for v in ("yes", "conditional", "cache", "not verified", "no")))
    w("- Closes the editor: yes %d, depends on kind %d." % tuple(sum(1 for t in tools if t["closes"] == v) for v in ("yes", "depends on kind")))
    w("- Launches the game: yes %d, depends on kind %d." % tuple(sum(1 for t in tools if t["launches"] == v) for v in ("yes", "depends on kind")))
    w("")
    unknown = [t for t in tools if not t["known"]]
    w("### Tools without review metadata")
    w("")
    if unknown:
        w("These tools are in `server.py` but not in `tools/tool_meta.json`; their family is \"other\" and their columns are \"unknown\".")
        w("")
        for t in unknown:
            w("- `%s` (server.py line %d)" % (t["name"], t["line"]))
    else:
        w("None. Every tool in `server.py` has an entry in `tools/tool_meta.json`.")
    w("")
    return "\n".join(L), tools, problems


def anchor(title):
    return re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")


def job_kinds():
    """(kind, closes_editor, launches_game) read from the KINDS literal in jobs.py."""
    p = ROOT / "bridge" / "jobs.py"
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "KINDS" for t in node.targets):
            if not isinstance(node.value, ast.Dict):
                return []
            rows = []
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Tuple) and len(v.elts) >= 4:
                    c, g = v.elts[2], v.elts[3]
                    if isinstance(c, ast.Constant) and isinstance(g, ast.Constant):
                        rows.append((k.value, bool(c.value), bool(g.value)))
            return rows
    return []


MODULE_META = ROOT / "tools" / "module_meta.json"
MODULES_OUT = ROOT / "docs" / "modules.md"
FOLDERS = json.loads((ROOT / "tools" / "module_folders.json").read_text(encoding="utf-8"))   # module file name -> folder
_MODNAME = re.compile(r"(?<![\w/.\-])([A-Za-z0-9_]+\.py)\b")


def pathify(text):
    """`bridge/jobs.py` -> `bridge/jobs.py` for every module of the repository named in a text"""
    return _MODNAME.sub(lambda m: (FOLDERS[m.group(1)] + "/" + m.group(1)) if m.group(1) in FOLDERS else m.group(1), text)


CATEGORY_ORDER = [
    ("core-tool-module", "Core tool modules", "Logic behind the MCP tools and their shared plumbing: assets, jobs, snapshots, screenshots, project resolution."),
    ("bridge-infrastructure", "Bridge and editor infrastructure", "Talk to the Sandbox editor through the bridge plugin, keep it alive, build and deploy the plugin."),
    ("catalog-or-checker", "Catalogs and checkers", "Dump engine registries into searchable catalogs, or load project data through engine loaders and report problems."),
    ("authoring-tool", "Authoring tools", "Generate or convert assets: textures, meshes, sounds, animation, level geometry, packaging."),
    ("test", "Tests", "Unit tests (`test_*.py`) and game or editor test scripts."),
    ("project-specific-ashvale", "Example / project-specific (Ashvale game)", "Scripts that build, drive or test the author's sample game; useful as examples, not as general tools."),
    ("one-off-patch-or-migration", "One-off scripts", "Procedures or migrations written for one job; read before running."),
    ("other", "Other", ""),
]
SERVER_ROW = ("server.py", "MCP server entry point (stdio). Defines every tool listed in [tools.md](tools.md) and the `FAMILIES` map used by `tool_guide`.")


def build_modules(n_tools):
    meta = json.loads(MODULE_META.read_text(encoding="utf-8"))
    on_disk = sorted([f for f, g in FOLDERS.items() if (ROOT / g / f).is_file()] + [p.name for p in ROOT.glob("*.py") if p.name not in FOLDERS and not p.name.startswith("_")])
    problems = ["module_meta.json lists '%s' which is not in the repository (skipped)" % m for m in sorted(set(meta) - set(on_disk))]
    missing = [m for m in on_disk if m not in meta and m != "server.py"]
    groups = {}
    for m in on_disk:
        if m in meta:
            groups.setdefault(meta[m]["category"], []).append(m)
    L = []
    w = L.append
    w("# Python modules")
    w("")
    w("Generated by `tools/gen_tools_doc.py` from the `*.py` files in the tool folders and `tools/module_meta.json` (the folder of each module is in `tools/module_folders.json`). Do not edit by hand.")
    w("")
    w("`tools/module_meta.json` records, per module, a one-sentence purpose, a category, whether it needs a running Sandbox editor, and its non-Python dependencies. "
      "It comes from a read-through of each file; the modules were not executed to produce it. Names of other modules in this folder are left out of the dependency column; "
      "the dependencies shown are external programs, packages, installed engine files or running services.")
    w("")
    w("The modules find each other through `_paths.py`, a bootstrap at the repository root that puts every tool folder on `sys.path`; it is deliberately not listed in the tables below (the generator skips files whose name starts with an underscore). "
      "Because of it a script can be started as `python <folder>/<file>.py` from any directory.")
    w("")
    w("## Columns and status values")
    w("")
    w("| Column | Meaning |")
    w("|---|---|")
    w("| Editor | `yes`: the module needs a running Sandbox editor with the bridge plugin for at least its main function. `no`: it does not. |")
    w("| External dependencies | Programs, packages, files or services outside this repository that the module needs. `editor with ClaudeBridgePlugin` means the bridge plugin on its TCP port. |")
    w("| Status | `general`: written to work on any project (paths and names may still need configuring). `example / project-specific`: built around the sample game \"Ashvale\" (its level, NPCs, console commands or assets); read it as an example, it will not run on another project unchanged. `one-off`: a procedure or migration written for a single job. `limited use`: depends on a specific external setup or duplicates another module; see the purpose. |")
    w("")
    w("The Tests group holds both `test_*.py` files and scripts that drive a packaged game or a live editor; the Editor and Status columns tell them apart. The `test_*.py` files in `tests/` are plain scripts that are started directly (for example `python tests/test_knowledge_eval.py`); a test marked `example / project-specific` assumes the Ashvale example project and fails on another project.")
    w("")
    w("## Entry point")
    w("")
    w("| Module | Purpose |")
    w("|---|---|")
    w("| `%s` | %s |" % SERVER_ROW)
    w("")
    count = 1
    total_by_status = {}
    for key, title, blurb in CATEGORY_ORDER:
        mods = groups.pop(key, [])
        if not mods:
            continue
        w("## %s (%d)" % (title, len(mods)))
        w("")
        if blurb:
            w(blurb)
            w("")
        w("| Module | Purpose | Editor | External dependencies | Status |")
        w("|---|---|---|---|---|")
        for m in mods:
            e = meta[m]
            w("| `%s` | %s | %s | %s | %s |" % (FOLDERS.get(m, ".") + "/" + m if m in FOLDERS else m, cell(pathify(e["purpose"])), "yes" if e["needs_editor"] else "no",
                                                 cell(pathify("; ".join(e["deps"]))) or "none", e["status"]))
            total_by_status[e["status"]] = total_by_status.get(e["status"], 0) + 1
        w("")
        count += len(mods)
    for key, mods in sorted(groups.items()):
        w("## %s (%d)" % (key, len(mods)))
        w("")
        w("| Module | Purpose | Editor | External dependencies | Status |")
        w("|---|---|---|---|---|")
        for m in mods:
            e = meta[m]
            w("| `%s` | %s | %s | %s | %s |" % (FOLDERS.get(m, ".") + "/" + m if m in FOLDERS else m, cell(pathify(e["purpose"])), "yes" if e["needs_editor"] else "no", cell(pathify("; ".join(e["deps"]))) or "none", e["status"]))
            total_by_status[e["status"]] = total_by_status.get(e["status"], 0) + 1
        w("")
        count += len(mods)
    w("## Count")
    w("")
    w("- Modules listed: **%d** (%d in the repository, including `server.py`)." % (count, len(on_disk)))
    w("- By status: " + ", ".join("%s %d" % (k, v) for k, v in sorted(total_by_status.items())) + " (plus `server.py`).")
    w("- Tools defined in `server.py`: %d (see [tools.md](tools.md))." % n_tools)
    w("")
    w("### Modules without metadata")
    w("")
    if missing:
        w("These files are in the tool folders but not in `tools/module_meta.json`:")
        w("")
        for m in missing:
            w("- `%s`" % m)
    else:
        w("None.")
    w("")
    return "\n".join(L), problems, count, len(on_disk)


USAGE = """usage: python tools/gen_tools_doc.py [--check | --help]

  (no argument)  write docs/tools.md and docs/modules.md
  --check        write nothing; exit 1 if either file is out of date
  --help, -h     print this text and write nothing
"""


def main(argv):
    if any(a in ("--help", "-h") for a in argv):
        print(USAGE, end="")
        return 0
    unknown = [a for a in argv if a != "--check"]
    if unknown or len(argv) > 1:
        print("error: unknown argument(s): %s" % " ".join(unknown or argv), file=sys.stderr)
        print(USAGE, file=sys.stderr, end="")
        return 2
    text, tools, problems = build()
    for p in problems:
        print("warning: " + p, file=sys.stderr)
    if any(p.startswith(("ast found", "duplicate")) for p in problems):
        print("error: tool count mismatch; not writing", file=sys.stderr)
        return 2
    mtext, mproblems, mcount, mdisk = build_modules(len(tools))
    for p in mproblems:
        print("warning: " + p, file=sys.stderr)
    text = text.rstrip("\n") + "\n"
    mtext = mtext.rstrip("\n") + "\n"
    if "--check" in argv:
        bad = 0
        for path, new in ((OUT, text), (MODULES_OUT, mtext)):
            cur = path.read_text(encoding="utf-8") if path.exists() else ""
            if cur != new:
                print("%s is out of date; run python tools/gen_tools_doc.py" % path.relative_to(ROOT), file=sys.stderr)
                bad = 1
        return bad
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    MODULES_OUT.write_text(mtext, encoding="utf-8", newline="\n")
    print("wrote %s: %d tools" % (OUT.relative_to(ROOT), len(tools)))
    print("wrote %s: %d modules (%d *.py in the tool folders)" % (MODULES_OUT.relative_to(ROOT), mcount, mdisk))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
