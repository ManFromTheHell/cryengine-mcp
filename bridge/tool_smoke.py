#!/usr/bin/env python3
"""Does every tool of the MCP server WORK for an AI that has never used it? (goal: an AI can build a game without issues, using only the tools and knowledge)

Starts server.py over stdio exactly as Claude does, lists the tools, and calls every READ-ONLY tool once with plain arguments, the way a first-time caller would (names taken from the live project, not
from a script's memory). A tool is

  ok         answered, with no error flag and no `refused` / `error` key
  expected   refused for a reason that is the CALLER's (no editor open, a name that does not exist ...) - listed, not counted against the tool
  BROKEN     raised, timed out, returned an error flag, or refused something a first call should have been able to do
  skipped    changes the level, the project or the machine (creates, deletes, sets, starts jobs or the game ...): covered by its own test or by a scratch-object test, named here with the reason
  negative   a MUTATING tool called with arguments that are invalid on purpose must REFUSE (a `refused` answer, ok false, or an error flag). `negative-ok` = refused; `NEGATIVE-FAIL` = it
             accepted garbage. The arguments are chosen to be refused before anything is touched, so this is safe with the editor open.
  budget     a result over BUDGET_CHARS characters is BROKEN: a tool must bound its output (server_support) instead of flooding the caller's context.

A tool that is in none of the three tables is reported as UNCLASSIFIED: adding a tool to server.py makes this fail until it is classified, so the audit cannot rot.

    python bridge/tool_smoke.py              # needs the editor open for the editor tools; exit 1 on a BROKEN or UNCLASSIFIED tool
    python bridge/tool_smoke.py --json       # the table as JSON
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import asyncio
import json
import os
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
sys.path.insert(0, HERE)

BUDGET_CHARS = 25000                              # the server's own budget is ~20,000; this is the "something is wrong" line
BUDGET_EXEMPT = {"tool_guide": 45000}             # the map of every tool is meant to be seen whole

# Mutating tools with invalid arguments. Each must be REFUSED before it touches anything (checked against the tool's code, not guessed).
NEGATIVE = {
    "execute_python": {"code": "general.exit()"}, "execute_python_script": {"code": "general.run_lua('x')"}, "execute_editor_command": {"command": "general.exit"},
    "run_console_command": {"command": "quit"}, "mannequin_check": {"reload": True}, "start_job": {"kind": "nonsense_kind"}, "stop_job": {"job_id": "no_such_job"},
    "place_object": {"kind": "nonsense", "asset": "x", "name": "1 bad name", "position": [0, 0]}, "delete_objects": {"prefix": "ab"},
    "create_object": {"type": "NoSuchType", "name": "bad name", "position": [0, 0, 0]}, "object_properties": {"name": "zz_no_such_object", "set": {"A": "1"}},
    "spline_points": {"name": "zz_no_such_object", "points": [[0, 0, 0]]}, "write_material": {"path": "../escape", "submaterials": []},
    "make_texture": {"surface": "nonsense", "name": "bad name"}, "build_mesh": {"spec": {}}, "restore_files": {"snapshot": "../nope", "paths": ["x"]},
    "load_level": {"level_name": "../escape"}, "new_level": {"name": "1 bad"}, "terrain_shape": {"style": "nonsense", "preview": True}, "terrain_layers": {"action": "nonsense"},
    "vegetation": {"action": "nonsense"}, "regenerate_navigation": {"agent_type_index": 99}, "create_navigation_area": {"points": [[0, 0]], "floor_z": 0},
    "create_ai_path": {"points": [[0, 0, 0]]}, "export_level": {"wait_seconds": 1}, "component_properties": {"entity": "a b", "component": "C", "values": {"x": 1}}, "add_component": {"entity": "a b", "component": "C"}, "link_area_target": {"area": "zz_no_such_area", "entities": ["zz_no_such_entity"]}, "flow_graph_add": {"entity": "zz_no_such_entity", "nodes": [{"id": "1", "class": "Debug:Log"}], "edges": []},
    "read_output": {"id": "../x"}, "list_objects": {"limit": 0}, "list_entities": {"offset": -1},
}

# tools that change the level, the project, jobs, the game or the machine: not called here
SKIP = {
    "build_mesh": "writes a mesh (test_pipeline)", "click_dialog_button": "clicks an editor dialog", "create_ai_path": "creates a level object", "create_collision_wall": "creates a level object",
    "create_nav_seed_point": "creates a level object", "create_navigation_area": "creates a level object", "create_object": "creates a level object (test_object_props)", "decal_preview": "spawns a decal (test_decal_lab)",
    "delete_objects": "deletes level objects (test_objects)", "debug_view": "switches rendering CVars (test_debug_views)", "editor_notify": "shows a notification in the editor", "execute_editor_command": "arbitrary editor command",
    "execute_python": "arbitrary editor Python", "execute_python_script": "arbitrary editor Python", "export_level": "exports the level", "generate_terrain_height": "rewrites the terrain", "goto_position": "moves the viewport camera",
    "load_level": "loads a level", "make_music": "writes audio files", "make_texture": "writes textures", "pfx_preview": "spawns an effect in the viewport (test_pfx_author)", "pfx_write": "writes a particle file",
    "place_object": "creates a level object (test_objects)", "preview_voice": "synthesises speech", "regenerate_navigation": "rebuilds the navmesh", "render_tour": "switches rendering CVars (test_render_tour)",
    "render_voices": "synthesises speech", "restore_files": "restores project files", "run_console_command": "arbitrary console command", "select_terrain_layer": "changes the editor's terrain layer",
    "set_ai_debug": "switches AI debug drawing", "set_cvar": "sets a CVar", "set_terrain_layer_texture": "changes a terrain layer", "shoot_game": "launches the game (never)", "snapshot_project": "copies the project (1.7 GB store)",
    "use_project": "changes which project the tools work on (writes active_project.txt)", "spawn_archetype": "creates an entity", "start_job": "starts a job (some close the editor or run the game)", "stop_job": "stops a job", "take_screenshot": "writes a screenshot file", "teleport_entity": "moves an entity",
    "visual_check": "launches the game (never)", "add_component": "adds a component to a level entity (negative case in NEGATIVE)", "link_area_target": "changes an area object (negative case in NEGATIVE)", "terrain_hole": "changes the terrain (PLAYGROUND.md)", "flow_graph_add": "stores a flow graph in the level (PLAYGROUND.md, test_flow_graph; negative case in NEGATIVE)", "write_material": "writes a material (test_pipeline)", "compare_shots": "needs two pictures (test_shot_compare)", "spline_points": "sets the points of a level object (test_object_props)", "get_shots": "needs a finished shoot job",
    "read_job_log": "needs a job id (called with a real one below when a job exists)", "job_status": "needs a job id (called with a real one below when a job exists)", "audio_report": "called with a real file below",
    "diff_snapshot": "called with a real snapshot below", "plan_character_collider": "pure arithmetic, called below",
    "new_level": "creates a level and switches the editor to it (test_world_build)", "terrain_shape": "rewrites the open level's terrain (test_world_build)", "terrain_layers": "adds layers / bakes the terrain texture (test_world_build)",
    "vegetation": "adds brushes / paints instances (test_world_build)", "save_level": "saves the open level",
    "read_output": "needs a saved output id (called with a real one below when one exists; refusal covered by NEGATIVE)",
}


def find_first(folder, exts, contains=""):
    for root, _dirs, files in os.walk(folder):
        for f in sorted(files):
            if f.lower().endswith(exts) and contains in f.lower():
                return os.path.relpath(os.path.join(root, f), ASSETS).replace(os.sep, "/")
    return None


def calls(ctx):
    """tool -> arguments of one plain first call (the project's own names). ctx: facts looked up from the project."""
    c = {
        "anim_check": {}, "anim_sample": {"clip": "relaxed_tac_run_nw_fwd_fast_3p_01", "times": [0.0, 0.5]}, "asset_info": {"path": ctx["mtl"]}, "asset_metadata": {}, "asset_report": {"limit": 3}, "audio_triggers": {}, "bt_check": {}, "bt_nodes": {"text": "wait"}, "character_check": {},
        "cryproject_check": {}, "cvar_info": {"name": "e_Vegetation"}, "cvar_search": {"text": "e_vegetation", "limit": 3}, "debug_views": {}, "decal_materials": {"limit": 3}, "drs_check": {},
        "editor_api": {"text": "object", "limit": 3}, "editor_commands": {"text": "save", "limit": 3}, "editor_panel": {"action": "status"}, "editor_panes": {}, "editor_undo": {"action": "info"},
        "engine_tests": {}, "entity_check": {}, "entity_class_properties": {"classes": ["Door"]}, "component_properties": {"entity": ctx["entity"], "component": "CNightLampComponent"}, "env_check": {}, "env_diff": {}, "env_variables": {"limit": 3}, "flow_check": {},
        "flow_nodes": {"limit": 3}, "frame_profile": {"frames": 60}, "gamesdk_audio": {"limit": 3}, "get_cvar": {"name": "e_Vegetation"}, "health_check": {}, "inspect_level": {}, "job_kinds": {},
        "level_check": {}, "gametoken_check": {}, "terrain_height": {"points": [[100, 100]]}, "level_stats": {"top": 3}, "level_validate": {}, "lint_assets": {"max_findings": 5}, "list_archetypes": {}, "list_dialogs": {}, "list_entities": {"name_filter": "Lamp"},
        "list_jobs": {"limit": 3}, "list_objects": {"name_filter": "Valley"}, "list_snapshots": {"limit": 3}, "list_terrain_layers": {}, "list_windows": {}, "mannequin_check": {}, "mannequin_clips": {"text": "idle"},
        "manual_toc": {"query": "water"}, "material_check": {}, "nav_map": {"x0": 470, "y0": 290, "x1": 500, "y1": 320, "step": 10, "z": 36}, "nav_reach": {"origin": [482, 302, 36], "targets": [[490, 310, 36]]},
        "object_check": {}, "object_properties": {"name": "ValleyProbe"}, "object_types": {"text": "vis"}, "outline_asset_xml": {"path": ctx["xml"], "depth": 1, "names_only": True},
        "particle_info": {"name": ctx["pfx_feature"]}, "particle_search": {"text": "fire", "limit": 3}, "pbs_check": {}, "pfx_check": {"path": ctx["pfx"]}, "ping": {}, "project_audit": {},
        "project_info": {}, "project_overview": {"max_chars": 3000}, "query_knowledge": {"query": "navmesh", "top": 2}, "rc_options": {"text": "jpg"}, "rc_presets": {"text": "texture"}, "read_asset_file": {"path": ctx["xml"], "max_chars": 400},
        "read_manual_page": {"page": "36869859"}, "sample_entity_physics": {"name": ctx["entity"], "samples": 2, "interval_s": 0.1}, "get_entity_physics": {"name": ctx["entity"]},
        "schematyc_info": {"name": ctx["schematyc"]}, "schematyc_search": {"text": "light", "limit": 3}, "search_manual": {"query": "terrain", "top": 3}, "sequence_check": {"path": ctx["sequence"]},
        "shader_info": {"shader": "Illum"}, "shader_search": {"text": "illum", "limit": 3}, "spec_group": {}, "study_gamedata": {"max_chars": 3000}, "study_level": {"max_chars": 3000}, "test_plan": {},
        "texture_recipes": {}, "tool_guide": {}, "ui_check": {}, "uqs_catalog": {"text": "item"}, "uqs_check": {}, "visarea_check": {}, "visual_status": {}, "wait_for_editor": {"timeout": 5},
        "plan_character_collider": {"total_height": 1.8, "radius": 0.3}, "audio_report": {"path": ctx["audio"]}, "server_info": {},
    }
    if ctx.get("output"):
        c["read_output"] = {"id": ctx["output"], "limit": 3}
    if ctx.get("job"):
        c["job_status"] = {"job_id": ctx["job"]}
        c["read_job_log"] = {"job_id": ctx["job"], "tail": 5}
    if ctx.get("snap"):
        c["diff_snapshot"] = {"a": ctx["snap"]}
    return c


def lookup():
    """facts about the project a first-time caller would take from the other tools' answers"""
    import jobs
    ctx = {"mtl": find_first(os.path.join(ASSETS, "materials", "ashvale"), (".mtl",)) or "materials/ashvale/world.mtl",
           "xml": "libs/ui/UIElements/MainMenu.xml", "sequence": "libs/ashvale/sequences/ashvale_intro.xml",
           "pfx": find_first(ASSETS, (".pfx",)) or "Particles/examples/omni/dust.pfx",
           "audio": find_first(os.path.join(ASSETS, "audio"), (".ogg", ".wav")) or "audio/ashvale/ambience/crickets.ogg",
           "pfx_feature": "RenderSprites", "schematyc": "Health", "entity": "Lamp_Well", "job": None, "snap": None}
    recs = jobs.list_jobs(1)
    if recs:
        ctx["job"] = recs[0]["id"]
    snaps = os.path.join(os.path.dirname(PROJECT), "_snapshots")
    ctx["snap"] = "latest" if os.path.isdir(snaps) else None
    out_dir = os.path.join(HERE, "job_runs", "out")
    saved = sorted(f[:-5] for f in os.listdir(out_dir) if f.endswith(".json")) if os.path.isdir(out_dir) else []
    ctx["output"] = saved[-1] if saved else None
    return ctx


# a refusal that is the CALLER's: the thing it needs is not there (not a fault of the tool)
CALLER_REASONS = ("editor", "sandbox", "bridge", "not open", "does not answer", "no such job", "no such")


def chars_of(res):
    """The text a caller receives (the structured copy repeats the same data, so it is not counted twice)."""
    return sum(len(getattr(c, "text", "") or "") for c in res.content)


def negative_verdict(res):
    """-> (kind, detail) for a call that SHOULD have been refused"""
    if res.isError:
        return "negative-ok", " ".join(getattr(c, "text", "") or "" for c in res.content)[:120]
    sc = res.structuredContent
    data = sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    if data is None:
        texts = [getattr(c, "text", None) for c in res.content]
        try:
            data = json.loads(texts[0]) if len(texts) == 1 and texts[0] else texts
        except ValueError:
            data = texts
    if isinstance(data, dict) and (data.get("refused") or data.get("ok") is False or data.get("error")):
        return "negative-ok", str(data.get("refused") or data.get("error") or data)[:120]
    return "NEGATIVE-FAIL", "accepted invalid arguments: " + json.dumps(data, default=str)[:160]


def verdict(res):
    """-> (kind, detail) for one MCP call result"""
    if res.isError:
        text = " ".join(getattr(c, "text", "") or "" for c in res.content)[:200]
        return "BROKEN", "error flag: " + text
    sc = res.structuredContent
    data = sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    if data is None:
        texts = [getattr(c, "text", None) for c in res.content]
        try:
            data = json.loads(texts[0]) if len(texts) == 1 and texts[0] else texts
        except ValueError:
            data = texts
    if isinstance(data, dict):
        for key in ("refused", "error"):
            if data.get(key):
                msg = str(data[key])[:200]
                if any(r in msg.lower() for r in CALLER_REASONS):
                    return "expected", msg
                return "BROKEN", "%s: %s" % (key, msg)
        if "findings" in data or "summary" in data or "dropped" in data:
            return "ok", ""                                       # a REPORT: ok false only means it found errors in the project, which is its job
        if data.get("ok") is False and not data.get("refused"):
            return "BROKEN", "ok false: " + json.dumps(data)[:200]
    if data in (None, "", [], {}):
        return "BROKEN", "empty answer"
    return "ok", ""


async def run(only=None):
    ctx = lookup()
    table = calls(ctx)
    env = None
    passed = {k: v for k, v in os.environ.items() if k.startswith(("CRYENGINE_", "CLAUDE_", "GAMESDK_"))}
    if passed:         # the SDK hands a child only a few variables: pass on the ones that choose the project and the engine, and CLAUDE_AUTO_RECOVER (0 = never relaunch the editor)
        env = dict(get_default_environment(), **passed)
    params = StdioServerParameters(command=sys.executable, args=[_paths.script("server.py")], cwd=HERE, env=env)
    rows = []
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = sorted(t.name for t in (await s.list_tools()).tools)
            for n in names:
                if only and n not in only:
                    continue
                if n in NEGATIVE:
                    t0 = time.time()
                    try:
                        res = await asyncio.wait_for(s.call_tool(n, NEGATIVE[n]), timeout=120)
                        kind, detail = negative_verdict(res)
                    except asyncio.TimeoutError:
                        kind, detail = "NEGATIVE-FAIL", "timed out after 120 s on invalid arguments"
                    except Exception as exc:                              # noqa: BLE001 - a raised error IS a refusal
                        kind, detail = "negative-ok", "%s: %s" % (type(exc).__name__, str(exc)[:100])
                    rows.append({"tool": n + " (invalid args)", "kind": kind, "detail": detail, "seconds": round(time.time() - t0, 1)})
                if n in SKIP and n not in table:
                    rows.append({"tool": n, "kind": "skipped", "detail": SKIP[n], "seconds": 0.0})
                    continue
                if n not in table:
                    rows.append({"tool": n, "kind": "UNCLASSIFIED", "detail": "in neither SKIP nor calls(): classify it in tool_smoke.py", "seconds": 0.0})
                    continue
                t0 = time.time()
                size = 0
                try:
                    res = await asyncio.wait_for(s.call_tool(n, table[n]), timeout=240)
                    kind, detail = verdict(res)
                    size = chars_of(res)
                    if size > BUDGET_EXEMPT.get(n, BUDGET_CHARS):
                        kind, detail = "BROKEN", "output of %d characters is over the budget of %d: bound it (server_support.bound)" % (size, BUDGET_EXEMPT.get(n, BUDGET_CHARS))
                except asyncio.TimeoutError:
                    kind, detail = "BROKEN", "timed out after 240 s"
                except Exception as exc:                                  # noqa: BLE001
                    kind, detail = "BROKEN", "%s: %s" % (type(exc).__name__, str(exc)[:160])
                rows.append({"tool": n, "kind": kind, "detail": detail, "seconds": round(time.time() - t0, 1), "chars": size})
    return rows


def main():
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    rows = asyncio.run(run(only or None))
    if "--json" in sys.argv:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            if r["kind"] != "ok":
                print("  %-12s %-26s %5.1fs  %s" % (r["kind"], r["tool"], r["seconds"], r["detail"][:150]))
        count = {}
        for r in rows:
            count[r["kind"]] = count.get(r["kind"], 0) + 1
        print("tools: %d  %s  (slowest: %s)" % (len(rows), count, ", ".join("%s %.0fs" % (r["tool"], r["seconds"]) for r in sorted(rows, key=lambda r: -r["seconds"])[:3])))
    return 1 if any(r["kind"] in ("BROKEN", "UNCLASSIFIED", "NEGATIVE-FAIL") for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
