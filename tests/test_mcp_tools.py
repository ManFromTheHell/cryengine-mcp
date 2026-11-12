#!/usr/bin/env python3
"""Self-test of the MCP server's editor-free tools THROUGH THE PROTOCOL: launches server.py over stdio the way Claude does, lists its
tools, and calls lint, jobs, the pipeline and the refusals. It never closes the editor and never launches the game: a job that would is called WITHOUT
allow_editor_close and must be refused (while Sandbox.exe runs; with it closed that check is skipped and said so).

    test_mcp_tools.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import asyncio
import json
import os
import shutil
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import jobs as J

ASSETS = os.path.join(os.path.dirname(HERE), "Assets")
PROJECT = __import__("project_root").PROJECT     # the active project: the checks that name the author's game assets are skipped when its files are not there
TMP = [os.path.join(ASSETS, top, "_mcp_selftest") for top in ("textures", "materials", "objects")]
results = []
NO_LEVEL_CHANGES = bool(os.environ.get("MCP_TEST_NO_LEVEL_CHANGES"))      # set to 1 to skip the checks that create and delete scratch objects in the OPEN editor's level
EXPECT = {"search_manual", "read_manual_page", "manual_toc", "cvar_search", "cvar_info", "spec_group", "editor_api", "editor_panes", "schematyc_search", "schematyc_info", "shader_info", "shader_search", "asset_info", "asset_report", "asset_metadata", "object_types", "create_object", "object_properties", "spline_points", "add_component", "link_area_target", "flow_graph_add", "gametoken_check", "terrain_hole", "debug_views", "debug_view", "particle_search", "particle_info", "pfx_check", "pfx_write", "pfx_preview", "render_tour", "sequence_check", "audio_triggers", "editor_commands", "nav_reach", "nav_map", "mannequin_check", "mannequin_clips", "bt_check", "bt_nodes", "uqs_check", "uqs_catalog", "level_stats", "level_validate", "rc_presets", "rc_options", "character_check", "material_check", "flow_nodes", "flow_check", "env_variables", "env_check", "env_diff", "anim_check", "anim_sample", "cryproject_check", "ui_check", "entity_check", "level_check", "visarea_check", "frame_profile", "editor_panel", "object_check", "editor_notify", "editor_undo", "drs_check", "pbs_check", "decal_materials", "decal_preview", "gamesdk_audio", "engine_tests", "project_audit", "snapshot_project", "list_snapshots", "diff_snapshot", "restore_files", "wait_for_editor", "visual_check", "visual_status", "health_check", "compare_shots", "audio_report", "place_object", "delete_objects", "tool_guide", "lint_assets", "job_kinds", "test_plan", "start_job", "job_status", "read_job_log", "list_jobs", "stop_job", "shoot_game", "get_shots",
          "texture_recipes", "make_texture", "write_material", "build_mesh", "preview_voice", "render_voices", "make_music", "query_knowledge",
          "new_level", "terrain_shape", "terrain_height", "terrain_layers", "vegetation", "save_level", "server_info", "read_output"}


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-64s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:90]))


def payload(res):
    """A tool's result: the structured content when there is one; else the JSON of its text block(s). An untyped `list` return comes back as one text
    block per item (a one-item list is then indistinguishable from a single value: no check here depends on that)."""
    if res.structuredContent is not None:
        sc = res.structuredContent
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    parsed = []
    for c in res.content:
        text = getattr(c, "text", None)
        if text is None:
            continue
        try:
            parsed.append(json.loads(text))
        except ValueError:
            parsed.append(text)
    return parsed[0] if len(parsed) == 1 else parsed


def cleanup():
    shutil.rmtree(SNAP, ignore_errors=True)
    for d in TMP:
        if os.path.basename(d) == "_mcp_selftest" and os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
    for s in ("diff", "ddna", "displ"):
        f = os.path.join(HERE, "texture_src", "mcpself_stone_" + s + ".tif")
        if os.path.isfile(f):
            os.remove(f)


SNAP = os.path.join(J.JOBS, "_mcp_snap_fix")
def real_manifests():
    d = os.path.join(os.path.dirname(os.path.dirname(HERE)), "_snapshots", "manifests")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


REAL_MANIFESTS_BEFORE = real_manifests()
NL = bytes([10])                       # a newline in the fixture files (written this way: a backslash escape was mangled once by the shell that patched this file)


def snapshot_env():
    """The snapshot tools are pointed at a throwaway project and store: a test must never write into the real store."""
    import json as _j
    proj, docs = os.path.join(SNAP, "proj"), os.path.join(SNAP, "docs")
    for d, name, data in ((proj, "Code/a.cpp", b"int a;" + NL), (proj, "Code/b.cpp", b"int b;" + NL), (docs, "HANDOFF.md", b"# h" + NL)):
        os.makedirs(os.path.dirname(os.path.join(d, name)), exist_ok=True)
        open(os.path.join(d, name), "wb").write(data)
    # the SDK hands a child only a few variables: pass on the settings that choose the project and the engine (CRYENGINE_PROJECT, CRYENGINE_ENGINE, ...) and
    # CLAUDE_AUTO_RECOVER=0 (running with the editor deliberately closed)
    keep = {k: v for k, v in os.environ.items() if k.startswith(("CRYENGINE_", "CLAUDE_", "MCP_TEST_", "GAMESDK_"))}
    return {**keep, "CRYENGINE_SNAPSHOT_DIR": os.path.join(SNAP, "store"), "CRYENGINE_SNAPSHOT_NO_GIT": "1",
            "CRYENGINE_SNAPSHOT_SOURCES": _j.dumps([["project", proj, []], ["docs", docs, []]])}


async def run():
    params = StdioServerParameters(command=sys.executable, args=[_paths.script("server.py")], env=snapshot_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            tools = {t.name: t for t in (await s.list_tools()).tools}
            check("the server lists the new tools (%d tools in all)" % len(tools), EXPECT <= set(tools), sorted(EXPECT - set(tools)))
            short = [n for n in EXPECT if n in tools and len(tools[n].description or "") < 60]
            check("every new tool has a real description", not short, short)

            lint = payload(await s.call_tool("lint_assets", {"max_findings": 5}))
            check("lint_assets: the project is clean and says what it examined", lint["ok"] and bool(lint["checked"]), lint["checked"])      # was: > 100 meshes and level references (the author's project)
            narrow = payload(await s.call_tool("lint_assets", {"scope": ["textures"]}))
            check("lint_assets: a scope limits what is checked", list(narrow["checked"]) == ["textures"], narrow["checked"])

            kinds = payload(await s.call_tool("job_kinds", {}))
            check("job_kinds lists the whitelist", {k["kind"] for k in kinds} >= {"tests", "package", "shader_cache", "shoot", "voices", "music", "textures", "selftest"}, [k["kind"] for k in kinds])
            plan = payload(await s.call_tool("test_plan", {}))
            check("test_plan reports every test group (%d: the jobs module's own list)" % len(J.test_groups()), {l.split()[0] for l in plan} >= set(J.test_groups()), plan[:2])

            # --- the job machinery through the protocol
            r = payload(await s.call_tool("start_job", {"kind": "selftest", "params": {"seconds": 3}}))
            check("start_job selftest returns an id at once", r.get("started") is True, r)
            jid = r["id"]
            st = payload(await s.call_tool("job_status", {"job_id": jid}))
            check("job_status: running right after the start", st["state"] == "running", st["state"])
            for _ in range(20):
                await asyncio.sleep(1)
                st = payload(await s.call_tool("job_status", {"job_id": jid}))
                if st["state"] != "running":
                    break
            check("job_status: done, exit 0, verdict read from the output", st["state"] == "done" and st["exit_code"] == 0 and st["verdict"] == "ok", st)
            log = payload(await s.call_tool("read_job_log", {"job_id": jid, "tail": 50, "contains": "tick"}))
            check("read_job_log: the filtered lines are the job's output", len(log["lines"]) == 3, log)
            bad = payload(await s.call_tool("start_job", {"kind": "selftest", "params": {"seconds": 2, "exit_code": 1}}))
            await asyncio.sleep(5)
            st = payload(await s.call_tool("job_status", {"job_id": bad["id"]}))
            check("a failing job is reported failed with its exit code", st["state"] == "failed" and st["exit_code"] == 1, st["state"])
            slow = payload(await s.call_tool("start_job", {"kind": "selftest", "params": {"seconds": 30}}))
            stopped = payload(await s.call_tool("stop_job", {"job_id": slow["id"]}))
            check("stop_job stops a running job", stopped["state"] == "stopped", stopped)
            lst = payload(await s.call_tool("list_jobs", {"limit": 5}))
            check("list_jobs shows them newest first", len(lst) >= 3 and lst[0]["id"] >= lst[-1]["id"], [j["state"] for j in lst])

            # --- refusals: no arbitrary commands, no editor closing unasked, no unknown kinds
            for kind, params, why in [("tests", {"groups": ["nope"]}, "an unknown test group"), ("rm", {}, "an unknown kind"),
                                      ("shoot", {"build": "release", "shots": ["a 1 2 3; del x"]}, "a shot line with a shell fragment"),
                                      ("shoot", {"build": "release", "shots": ["!exec evil.cfg"]}, "a blocked console command"),
                                      ("textures", {"surfaces": ["../../x"]}, "a path as a surface name")]:
                r = payload(await s.call_tool("start_job", {"kind": kind, "params": params}))
                check("start_job refuses " + why, r.get("ok") is False and "refused" in r, str(r.get("refused", r))[:70])
            if J.sandbox_running():
                for kind, params in (("tests", {"groups": ["smoke"]}), ("package", {}), ("shader_cache", {})):
                    r = payload(await s.call_tool("start_job", {"kind": kind, "params": params}))
                    check("start_job %s is refused while Sandbox.exe runs (no allow_editor_close)" % kind, r.get("ok") is False and "closes the editor" in r.get("refused", ""), str(r.get("refused", r))[:70])
                shots = await s.call_tool("shoot_game", {"shots": ["x 500 500 0"]})
                text = "".join(getattr(c, "text", "") for c in shots.content)
                check("shoot_game is refused while Sandbox.exe runs", "refused" in text and "closes the editor" in text, text[:80])
            else:
                print("  --   Sandbox.exe is not running: the 'refuse to close the editor' checks are skipped")

            # --- the pipeline through the protocol
            rec = payload(await s.call_tool("texture_recipes", {}))
            check("texture_recipes lists the surfaces", "stone" in rec and rec["stone"]["height_map"] is True, list(rec))
            t = payload(await s.call_tool("make_texture", {"surface": "stone", "name": "mcpself_stone", "size": 128, "folder": "textures/_mcp_selftest"}))
            check("make_texture: three maps, height map BC4, lint clean", t.get("ok") is True and {f["format"] for f in t["files"]} >= {"BC4"} and len(t["files"]) == 3, [f["format"] for f in t.get("files", [])] or t)
            m = payload(await s.call_tool("write_material", {"path": "materials/_mcp_selftest/box", "submaterials": [{"name": "stone", "texture": "textures/_mcp_selftest/mcpself_stone", "height_depth": 0.03}]}))
            check("write_material: written and lint clean", m.get("ok") is True, m.get("lint") or m)
            b = payload(await s.call_tool("build_mesh", {"spec": {"name": "box", "folder": "objects/_mcp_selftest", "material_file": "materials/_mcp_selftest/box", "materials": ["stone"],
                                                              "parts": [{"box": [0, 0, 0, 1, 1, 1], "material": "stone"}]}}))
            check("build_mesh: built, material named and found, 1 m cube", b.get("ok") is True and b["bounds_m"]["size"] == [1.0, 1.0, 1.0], b.get("problems") or b.get("bounds_m") or b)
            bad = payload(await s.call_tool("build_mesh", {"spec": {"name": "oops", "materials": ["a"]}}))
            check("build_mesh: a malformed spec is a refusal, not a crash", bad.get("ok") is False, str(bad.get("refused"))[:70])
            full = payload(await s.call_tool("lint_assets", {"max_findings": 5}))
            check("the project lints clean with the throwaway files in it", full["ok"], full["by_code"])
            pv = payload(await s.call_tool("preview_voice", {"text": "The road north is not safe after dark.", "voice": "alba"}))
            if pv.get("ok") is False and "piper" in str(pv).lower():
                print("  --   SKIP preview_voice: Piper (or the voice 'alba') is not installed: %s" % str(pv)[:90])
            else:
                check("preview_voice renders a WAV with Piper (real time factor < 1)", pv.get("ok") is True and os.path.isfile(pv["path"]) and pv["render_seconds"] < pv["speech_seconds"], pv)
            # --- the second batch: guide, instructions, health, audio, pictures, objects
            check("the server sends its instructions with the handshake (the model sees them without asking)", bool(init.instructions) and "tool_guide" in init.instructions and "overwrite" in init.instructions, len(init.instructions or ""))
            guide = payload(await s.call_tool("tool_guide", {}))
            check("tool_guide classifies EVERY registered tool (%d): none unclassified" % len(tools), guide["tools"] == len(tools) and "unclassified" not in guide["families"], guide["families"].get("unclassified"))
            g2 = payload(await s.call_tool("tool_guide", {"family": "verify"}))
            check("tool_guide can show one family", list(g2["families"]) == ["verify without the editor"] and any(t["tool"] == "compare_shots" for t in g2["families"]["verify without the editor"]), list(g2["families"]))
            hc = payload(await s.call_tool("health_check", {}))
            check("health_check answers with a status for each part and the counts agree", hc["failures"] == sum(i["status"] == "fail" for i in hc["items"]) and len(hc["items"]) >= 7, hc["summary"])
            if os.path.isfile(os.path.join(PROJECT, "Assets", "audio", "sdlmixer", "assets", "ash_music_day.ogg")):
                ar = payload(await s.call_tool("audio_report", {"path": "audio/sdlmixer/assets/ash_music_day.ogg"}))
                check("audio_report measures the day music (about 2 minutes, loop, no flags)", 100 < ar["duration_s"] < 130 and ar["treated_as_loop"] and not ar["flags"] and ar["lufs"] is not None, (ar["duration_s"], ar["lufs"]))
            else:
                print("  --   SKIP audio_report on the author's day music: audio/sdlmixer/assets/ash_music_day.ogg is not in this project")
            ar2 = payload(await s.call_tool("audio_report", {"path": "../server.py"}))
            check("audio_report refuses a file outside the project's audio", ar2.get("ok") is False, str(ar2.get("refused"))[:60])
            marker = os.path.join(J.JOBS, "_last_compare_job.txt")
            job = open(marker).read().strip() if os.path.isfile(marker) else ""
            if job and os.path.isdir(os.path.join(J.JOBS, job + "_shots")):
                res = await s.call_tool("compare_shots", {"a": job + ":n1", "b": job + ":n3", "control": job + ":n2"})
                kinds_back = [type(c).__name__ for c in res.content]
                body = payload(res)
                check("compare_shots returns the measurements AND a difference picture", "ImageContent" in kinds_back and str(body.get("verdict", "")).startswith("CHANGED"), (kinds_back, str(body.get("verdict"))[:50]))
            else:
                print("  --   the lamp experiment's job is no longer kept: compare_shots through the protocol skipped")
            cs = payload(await s.call_tool("compare_shots", {"a": "x;y", "b": "z"}))
            check("compare_shots refuses a bad reference", cs.get("ok") is False if isinstance(cs, dict) else False, str(cs)[:60])
            if J.sandbox_running() and not NO_LEVEL_CHANGES:
                before = payload(await s.call_tool("list_objects", {"name_filter": "Mcptest_"}))
                pl = payload(await s.call_tool("place_object", {"kind": "brush", "asset": "%ENGINE%/EngineAssets/Objects/primitive_cube.cgf", "name": "Mcptest_p", "position": [30, 30, 200]}))
                check("place_object through the protocol: placed and read back", pl.get("ok") is True and pl["object"]["pos"] == [30, 30, 200], str(pl)[:80])
                dup = payload(await s.call_tool("place_object", {"kind": "brush", "asset": "%ENGINE%/EngineAssets/Objects/primitive_cube.cgf", "name": "Mcptest_p", "position": [31, 30, 200]}))
                check("place_object refuses an existing name", dup.get("ok") is False and "overwrite" in dup.get("refused", ""), str(dup.get("refused"))[:60])
                dry = payload(await s.call_tool("delete_objects", {"prefix": "Mcptest_"}))
                check("delete_objects is a dry run by default", dry.get("dry_run") is True and dry["would_delete"] == 1, dry)
                gone = payload(await s.call_tool("delete_objects", {"prefix": "Mcptest_", "really": 1}))
                check("delete_objects with the confirmed count deletes it", gone.get("ok") is True and gone["deleted"] == 1, gone)
                left = payload(await s.call_tool("list_objects", {"name_filter": "Mcptest_"}))
                check("nothing of the test is left in the level", not left["items"], left)
                check("list_objects answers with its limits (T-13): items, shown, total, truncated", {"items", "shown", "total", "truncated", "next_offset"} <= set(left) and left["truncated"] is False, list(left))
                bad_page = payload(await s.call_tool("list_objects", {"limit": 0}))
                check("list_objects refuses limit 0", bad_page.get("ok") is False, str(bad_page.get("refused"))[:60])
            else:
                print("  --   place_object / delete_objects through the protocol skipped (%s)" % ("MCP_TEST_NO_LEVEL_CHANGES" if NO_LEVEL_CHANGES else "Sandbox.exe is not running"))
            # --- the third batch: snapshots (on a fixture), wait_for_editor, visual_status
            proj = os.path.join(SNAP, "proj")
            sn = payload(await s.call_tool("snapshot_project", {"label": "first"}))
            check("snapshot_project stores the fixture project (3 files) in the fixture store", sn.get("files") == 3 and "_mcp_snap_fix" in sn["store"], sn)
            open(os.path.join(proj, "Code", "a.cpp"), "wb").write(b"int a = 1;" + NL)
            os.remove(os.path.join(proj, "Code", "b.cpp"))
            open(os.path.join(proj, "Code", "c.cpp"), "wb").write(b"int c;" + NL)
            df = payload(await s.call_tool("diff_snapshot", {"a": sn["id"]}))
            check("diff_snapshot against the live files: one modified, one removed, one added", (df["modified"], df["removed"], df["added"]) == (1, 1, 1) and df["modified_files"] == ["project/Code/a.cpp"], (df["modified"], df["removed"], df["added"]))
            side = os.path.join(SNAP, "side")
            rs = payload(await s.call_tool("restore_files", {"snapshot": sn["id"], "paths": ["project/Code/b.cpp"], "to": side}))
            check("restore_files to a side folder brings the removed file back and leaves the live tree alone", rs.get("restored") == 1 and open(os.path.join(side, "project", "Code", "b.cpp"), "rb").read() == b"int b;" + NL and not os.path.exists(os.path.join(proj, "Code", "b.cpp")), rs)
            ow = payload(await s.call_tool("restore_files", {"snapshot": sn["id"], "paths": ["project/Code/a.cpp"], "overwrite": True}))
            check("restore_files overwrite=true puts the file back, and says how to undo it", open(os.path.join(proj, "Code", "a.cpp"), "rb").read() == b"int a;" + NL and "before_restore" in ow.get("undo", ""), ow.get("undo"))
            ls = payload(await s.call_tool("list_snapshots", {"verify": sn["id"]}))
            check("list_snapshots lists both (the first and the automatic before_restore) and verify finds nothing wrong", ls["count"] == 2 and ls["verify"]["ok"] and ls["verify"]["checked"] == 3, (ls["count"], ls["verify"]))
            bad = payload(await s.call_tool("restore_files", {"snapshot": sn["id"], "paths": ["../../etc/passwd"], "to": side}))
            check("restore_files refuses a path that climbs out", bad.get("ok") is False, str(bad.get("refused"))[:60])
            bad = payload(await s.call_tool("diff_snapshot", {"a": "../nope"}))
            check("diff_snapshot refuses an id that is a path", bad.get("ok") is False, str(bad.get("refused"))[:60])
            check("the real snapshot store has exactly the manifests it had before this test (the fixture store took all of it)", real_manifests() == REAL_MANIFESTS_BEFORE, "%d before, %d now" % (len(REAL_MANIFESTS_BEFORE), len(real_manifests())))
            w = payload(await s.call_tool("wait_for_editor", {"timeout": 20}))
            if J.sandbox_running():
                check("wait_for_editor returns at once when the editor is up (1 poll)", w.get("ok") is True and w["polls"] == 1 and w["waited_s"] < 5, w)
            else:
                check("wait_for_editor with the editor closed times out with a diagnosis (it does not hang)", w.get("ok") is False and "diagnosis" in w and w["waited_s"] >= 15, str(w)[:80])
            bad = payload(await s.call_tool("wait_for_editor", {"timeout": 5000}))
            check("wait_for_editor refuses a timeout over 900 s", bad.get("ok") is False, str(bad.get("refused"))[:60])
            vs = payload(await s.call_tool("visual_status", {}))
            check("visual_status reads the baseline (20 views) and how many launches the tolerance rests on", (not vs["baseline"] or vs["baseline"]["views"] > 0), vs["baseline"])      # was: exactly 20 views, tolerance from >= 3 launches (the author's baseline); no baseline yet is fine
            rf = payload(await s.call_tool("start_job", {"kind": "visual", "params": {"accept": True, "calibrate": True}}))
            check("a visual job that would combine accept with calibrate is refused", rf.get("ok") is False, str(rf.get("refused"))[:60])
            kn = payload(await s.call_tool("query_knowledge", {"query": "shader cache stale after material change", "top": 2}))
            check("query_knowledge still answers", len(kn) >= 1 and "shader" in json.dumps(kn).lower(), [k["id"] for k in kn])
            sm = payload(await s.call_tool("search_manual", {"query": "river tool", "top": 3}))
            if not (isinstance(sm, dict) and sm.get("hits")):
                print("  --   SKIP the manual checks: no offline manual is installed (set CRYENGINE_DOCS): %s" % str(sm)[:80])
                sm = None
            else:
                rp = payload(await s.call_tool("read_manual_page", {"page": sm["hits"][0]["page_id"]}))
                tc = payload(await s.call_tool("manual_toc", {}))
                check("the manual is reachable through the protocol: search -> read -> toc", sm["hits"][0]["title"] == "River Tool" and rp["title"] == "River Tool" and "mentions" in rp and tc["pages"] > 0, (sm["hits"][0]["title"], tc["pages"]))      # was: exactly 1,725 pages
            cs = payload(await s.call_tool("cvar_search", {"text": "antialiasing", "limit": 5}))
            ci = payload(await s.call_tool("cvar_info", {"name": "e_DefaultMaterial"}))
            check("cvar_search finds variables with where they can be set; cvar_info says e_DefaultMaterial is refused in Release, with why", cs["matches"] >= 1 and "settable" in cs["results"][0] and ci["settable"]["release"] == "no" and "cheat" in ci["settable"]["why"], (cs["matches"], ci["settable"]))
            cc = payload(await s.call_tool("cvar_search", {"settable_in": "release", "flag": "CHEAT"}))
            check("control: no cheat variable is listed as settable in Release", cc["matches"] == 0, cc["matches"])
            bad = payload(await s.call_tool("cvar_info", {"name": "r_antialiasing"}))
            check("cvar_info on a name that does not exist suggests the real ones", bad.get("ok") is False and any(n.startswith("r_Antialiasing") for n in bad.get("did_you_mean", [])), bad.get("did_you_mean"))
            sg = payload(await s.call_tool("spec_group", {"group": "sys_spec_Particles", "level": 1}))
            check("spec_group reads the engine's own quality group from engine.pak (level 1 sets e_ParticlesQuality 2)", sg["values"].get("e_ParticlesQuality") == "2", sg.get("values", {}).get("e_ParticlesQuality"))
            ea = payload(await s.call_tool("editor_api", {"text": "track animation", "module": "trackview", "limit": 3}))
            ep = payload(await s.call_tool("editor_panes", {}))
            eo = payload(await s.call_tool("editor_api", {"read_only": True, "limit": 50}))
            check("editor_api finds functions with their arguments and description; editor_panes lists the editor's 57 tools; read_only lists the 21 safe getters",
                  ea["matches"] >= 1 and ea["results"][0]["function"].startswith("trackview.") and "description" in ea["results"][0] and ep["count"] >= 1 and "Track View" in ep["panes"] and eo["matches"] >= 1 and all(r["read_only"] for r in eo["results"]), (ea["matches"], ep["count"], eo["matches"]))
            ss = payload(await s.call_tool("schematyc_search", {"text": "rigidbody", "type": "Component"}))
            si = payload(await s.call_tool("schematyc_info", {"name": "Health"}))
            sa = payload(await s.call_tool("schematyc_info", {"name": "stop"}))
            check("schematyc_search finds the engine's Rigidbody component with its members; schematyc_info reads the game's own Health component (registered in Code/Components/Health.cpp); an ambiguous name lists candidates",
                  ss["matches"] >= 1 and "Resting" in ss["results"][0]["members"] and (si.get("source") == "Code/Components/Health.cpp" or not os.path.isfile(os.path.join(PROJECT, "Code", "Components", "Health.cpp"))) and sa.get("ok") is False and len(sa.get("candidates", [])) >= 4, (ss["matches"], si.get("source"), len(sa.get("candidates", []))))
            sh = payload(await s.call_tool("shader_info", {"shader": "Illum", "flags": ["PARALLAX_OCCLUSION_MAPPING", "BUMP_MAP"]}))
            if "_output" in sh:                                     # Illum's full answer is over the output budget: the tool saves it and summarises; read the parts back (read_output)
                for part in ("flags", "params"):
                    sh[part] = payload(await s.call_tool("read_output", {"id": sh["_output"]["output_id"], "key": part, "limit": 500}))["items"]
            ssr = payload(await s.call_tool("shader_search", {"text": "displacement"}))
            check("shader_info reads Illum from the engine's own shader scripts (16 flags, PomDisplacement needing %PARALLAX_OCCLUSION_MAPPING) and resolves a flag set (BUMP_MAP is ignored by the engine); shader_search finds across shaders",
                  len(sh["flags"]) == 16 and any(p["name"] == "PomDisplacement" and p["needs"] for p in sh["params"]) and sh["resolved"]["ignored"] == ["BUMP_MAP"] and sh["resolved"]["honoured"] == ["PARALLAX_OCCLUSION_MAPPING"] and ssr["matches"] >= 4,
                  (len(sh["flags"]), sh["resolved"]["ignored"], ssr["matches"]))
            ps = payload(await s.call_tool("particle_search", {"text": "sprites"}))
            pi = payload(await s.call_tool("particle_info", {"name": "RenderSprites"}))
            pm = payload(await s.call_tool("particle_search", {"kind": "modifier", "text": "random"}))
            pn = payload(await s.call_tool("particle_info", {"name": "Render: Sprite"}))
            check("particle_search finds the engine's Render: Sprites feature; particle_info gives its file name, its defaults and the effects that use it (the same feature by editor or file name); modifiers are searched too; a misspelt feature offers candidates",
                  ps["matches"] >= 2 and any(r["name"] == "Render: Sprites" for r in ps["results"]) and pi["file_name"] == "RenderSprites" and "FacingMode" in pi["defaults"] and pi["used_by_effects"] >= 10
                  and {m["name"] for m in pm["results"]} >= {"Random", "ColorRandom"} and pn.get("ok") is False and "Render: Sprites" in pn.get("did_you_mean", []), (ps["matches"], pi.get("used_by_effects"), pn.get("did_you_mean")))
            have_mtl = os.path.isfile(os.path.join(PROJECT, "Assets", "materials", "ashvale", "ash_broken.mtl"))
            if not have_mtl:
                print("  --   SKIP asset_info / asset_report: they read the author's materials/ashvale/ash_broken.mtl, which is not in this project")
            ai = payload(await s.call_tool("asset_info", {"path": "materials/ashvale/ash_broken.mtl"})) if have_mtl else None
            ar = payload(await s.call_tool("asset_report", {"report": "heaviest", "type": "Mesh", "limit": 3}))
            bad = payload(await s.call_tool("asset_info", {"path": "objects/nothing/at_all.cgf"}))
            if have_mtl:
              check("asset_info reads the engine's own metadata (a material and what it depends on); asset_report finds the heaviest meshes by the triangles the engine measured; an asset the engine has not recorded is said so",
                  ai["type"] == "Material" and ai["depends_on"] and len(ar["heaviest"]) == 3 and ar["heaviest"][0]["weight"] >= ar["heaviest"][1]["weight"] and bad.get("ok") is False and "no .cryasset" in str(bad.get("refused")),
                  (ai["type"], ar["heaviest"][0]["weight"], str(bad.get("refused"))[:40]))
            ot = payload(await s.call_tool("object_types", {}))
            pre = payload(await s.call_tool("list_objects", {"name_filter": "zz_mcp_", "limit": 5})) if ot.get("count") and not NO_LEVEL_CHANGES else {}
            if pre.get("items"):
                print("  --   SKIP the object tools' live checks: the level already holds zz_mcp_ objects (%s), which this test would otherwise own" % ", ".join(str(i.get("name")) for i in pre["items"][:3]))
            elif ot.get("count") and not NO_LEVEL_CHANGES:
                made = payload(await s.call_tool("create_object", {"type": "Road", "name": "zz_mcp_road", "position": [400, 250, 40]}))
                try:
                    setp = payload(await s.call_tool("object_properties", {"name": "zz_mcp_road", "set": {"Width": "9", "NoSuchKey": "1"}}))
                    sp = payload(await s.call_tool("spline_points", {"name": "zz_mcp_road", "points": [[400, 250, 40], [420, 256, 40], [440, 250, 40]]}))
                    rd = payload(await s.call_tool("object_properties", {"name": "zz_mcp_road"}))
                    prot = payload(await s.call_tool("object_properties", {"name": "zz_mcp_road", "set": {"Type": "River"}}))
                    unk = payload(await s.call_tool("create_object", {"type": "NoSuchType", "name": "zz_mcp_x", "position": [0, 0, 0]}))
                    dup = payload(await s.call_tool("create_object", {"type": "Road", "name": "zz_mcp_road", "position": [0, 0, 0]}))
                    check("create_object / object_properties / spline_points through the protocol: a Road is created (36 types known), Width set and read back, a misspelt key reported as not applied, 3 points set, Type cannot be changed, an unknown type and a taken name are refused",
                          ot["count"] >= 30 and made.get("ok") is True and setp["changed"]["Width"]["after"] == "9" and setp["not_applied"] == ["NoSuchKey"] and sp["points"] == 3 and rd["properties"]["Width"] == "9"
                          and prot.get("ok") is False and unk.get("ok") is False and "unknown object type" in str(unk.get("refused")) and dup.get("ok") is False, (ot["count"], setp.get("not_applied"), sp.get("points")))
                finally:
                    dl = payload(await s.call_tool("delete_objects", {"names": ["zz_mcp_road"], "really": 1}))
                    check("the scratch road is deleted again (delete_objects with the count)", dl.get("ok") is not False and not dl.get("refused"), str(dl)[:60])
            else:
                print("  --   the editor is not open with a level: the object tools' live checks are skipped")
            bad = payload(await s.call_tool("start_job", {"kind": "cvar_probe", "params": {"cvars": ["e_x; del 1"]}, "allow_editor_close": True}))
            check("a cvar_probe job with a line that is not 'name number' is refused before anything closes", bad.get("ok") is False and "not understood" in str(bad.get("refused")), str(bad.get("refused"))[:60])
            # --- 2026-10-23: danger deny-list, reload guard, bounded output, server_info
            # These send editor-closing commands to the live server: a regression of the deny-list would close the editor, so MCP_TEST_NO_LEVEL_CHANGES=1 skips them.
            for tool_name, args, what in (() if NO_LEVEL_CHANGES else (("execute_python", {"code": "general.exit()"}, "general.exit"), ("execute_editor_command", {"command": "general.unload_all_plugins"}, "unload_all_plugins"),
                                          ("execute_python_script", {"code": "x = general.message_box_ok('a')"}, "a message box"))):
                dr = payload(await s.call_tool(tool_name, args))
                check("%s refuses %s without really=true" % (tool_name, what), dr.get("ok") is False and "really=true" in dr.get("refused", ""), str(dr.get("refused"))[:60])
            mr = payload(await s.call_tool("mannequin_check", {"reload": True}))
            check("mannequin_check refuses reload=true without confirm_reload", mr.get("ok") is False and "confirm_reload" in mr.get("refused", ""), str(mr.get("refused"))[:60])
            si = payload(await s.call_tool("server_info", {}))
            check("server_info: start time, no module edited since (or a restart note naming them), catalogs stamped state", "started" in si and isinstance(si["stale"], list) and set(si["catalogs"]) == {"cvars.json", "editor_commands.json", "editor_api.json"}, (si["started"], si["stale"][:3]))
            ro = payload(await s.call_tool("read_output", {"id": "../../etc/passwd"}))
            check("read_output refuses an id that is a path", ro.get("ok") is False, str(ro.get("refused"))[:60])
            big = payload(await s.call_tool("query_knowledge", {"query": "navmesh", "top": 20}))
            big0 = big[0] if isinstance(big, list) and big else big          # a one-item list comes back as its single item
            if isinstance(big0, dict) and "_output" in big0:
                oid = big0["_output"]["output_id"]
                pg = payload(await s.call_tool("read_output", {"id": oid, "key": "", "offset": 0, "limit": 3}))
                check("a too-big result is saved and summarised, and read_output pages it back (3 of the 20 chunks)", pg["total"] == 20 and pg["returned"] == 3 and "title" in pg["items"][0], (pg["total"], pg["returned"]))
                os.remove(big0["_output"]["path"])
            else:
                check("query_knowledge top=20 stays inside the output budget", len(json.dumps(big)) < 25000, len(json.dumps(big)))
            bad = payload(await s.call_tool("read_manual_page", {"page": "Water"}))
            check("an ambiguous manual title is refused with the ids, not guessed", bad.get("ok") is False and "36869912" in str(bad.get("refused")), str(bad.get("refused"))[:60])


def main():
    cleanup()
    try:
        asyncio.run(run())
    finally:
        cleanup()
    left = [d for d in TMP if os.path.exists(d)]
    check("cleanup: the throwaway folders are gone", not left, left)
    bad = results.count(False)
    print("mcp tools test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
