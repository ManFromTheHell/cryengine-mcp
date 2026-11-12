# cryengine-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI client (for example Claude) drive the **CRYENGINE 5.7.1** Sandbox editor. A small C++ editor plugin (`ClaudeBridgePlugin`) listens on a local TCP port inside Sandbox; the Python server translates MCP tool calls into requests to that plugin and into reads of the engine's own files.

It has 154 tools:

- object, level, terrain and vegetation editing in the open editor;
- validators that compare your project data (materials, levels, flow graphs, behaviour trees, UI, animation, ...) with what the engine itself loads;
- catalogs of what the engine offers (CVars, Flow nodes, Schematyc components, shaders, editor commands, the Python editor API);
- screenshots of the editor viewport and of a packaged game;
- background jobs (package, tests, shader cache) with logs;
- a knowledge base of engine traps and procedures, each chunk labelled with how it was established (`measured`, `read-from-source`, ...; many older chunks are still `unclassified`) (`query_knowledge`).

Tool results are meant to be measurements read back from the engine, not "ok".

## Who it is for

CRYENGINE 5.7.1 LTS developers on Windows who want an AI client to inspect and edit a project through the editor, and who are comfortable building a Sandbox plugin from the engine source.

## What it is not

- **Not affiliated with, endorsed by or supported by Crytek.** CRYENGINE is Crytek's product; you need your own licence for it. See [NOTICE.md](NOTICE.md).
- **Not a game.** No game or game assets are included.
- **Windows only.** The plugin is a Windows Sandbox plugin; many tools call Windows paths and `taskkill`.
- **Local use only.** The bridge has no authentication. See [Safety](#safety).
- **Not a replacement for the engine's documentation.** The offline manual tools read a copy of the manual that you supply; none is included.

## Status

The plugin was built and run against a locally built Sandbox. Building it against, or loading it into, an unmodified Launcher install of Sandbox is not verified (see [docs/limitations.md](docs/limitations.md)).

The code was written for one author's game project. The path defaults are portable now: shared state goes to `state/` in the repository (`CRYENGINE_WORKSPACE`), the CRYENGINE source tree is used only when `CRYENGINE_SOURCE` is set, and the optional MotionBricks install defaults to a folder inside the repository (`MOTIONBRICKS_ROOT`); see the environment-variable tables in [docs/installation.md](docs/installation.md). Parts are still project-specific: some modules assume that game's level names, asset layout or build, and some tests assert its data. [docs/limitations.md](docs/limitations.md) lists what is known to be non-portable. Treat the project as an early release: read a tool's description before relying on it.

## Architecture

```
AI client (MCP, stdio)
        |
   server.py  ----  helper modules (validators, catalogs, jobs, snapshots, asset makers)
        |                         \
        | JSON, one object per line  \__ reads the engine, project and manual files directly
        | TCP 127.0.0.1:37778
        v
  ClaudeBridgePlugin  (C++ plugin loaded by Sandbox.exe, runs on the editor's idle tick)
        |
   Sandbox editor: objects, terrain, Python, console, screenshots
```

- `server.py` registers the tools (FastMCP, stdio transport). Blocking work runs in worker threads; large results are saved and paged (`read_output`).
- The plugin only accepts a connection from the loopback address, and drops any connection whose first non-whitespace byte is not `{` (after sending one JSON error line); this rejects HTTP requests sent by a web page to the port.
- Host and port can be changed with `CRYENGINE_BRIDGE_HOST` and `CRYENGINE_BRIDGE_PORT` (the port is `kBridgePort` in `plugin/ClaudeBridgePlugin/ClaudeBridgePlugin.h`).
- The project being served is chosen by `bridge/project_root.py`: `CRYENGINE_PROJECT`, else `active_project.txt`, else the parent folder of this repository (the original layout was `<project>/mcp_server/server.py`). In a stand-alone clone that last fallback is almost never what you want: **set `CRYENGINE_PROJECT` in your client configuration**. Restart the server after changing it. The editor itself runs one project at a time.

## Quick start

1. Install CRYENGINE 5.7.1 LTS with your own licence and get its source. Build and install the plugin: [docs/installation.md](docs/installation.md). The plugin needs the small engine build patches in [engine-patches/](engine-patches/).
2. Install the Python side: Python 3.10 or newer, then `pip install -r requirements.txt`. This installs `mcp` and `numpy`, both required to start the server. Optional extras are listed in docs/installation.md.
3. Register the server in your MCP client: [docs/mcp-clients.md](docs/mcp-clients.md). The command is the full path of the `python` that has `mcp` and `numpy` installed, the argument is the full path to `server.py`, and the `env` block must set `CRYENGINE_PROJECT` to your project folder (without it the file-side tools run against the parent folder of this repository).
4. Start Sandbox with your project. The plugin logs that it is listening on `127.0.0.1:37778`.
5. Ask the client to call `ping`, then `health_check` and `tool_guide`. A first walk-through is in [docs/quickstart.md](docs/quickstart.md).

Many tools (the validators, `query_knowledge`, snapshots) work without the editor. The catalog tools read dumps that must be captured once from a running editor (see [Tests](#tests)). The tool reference says which need it: [docs/tools.md](docs/tools.md).

## Tool families

Counts come from the `FAMILIES` table in `server.py`, which `tool_guide` returns at run time (it also reports any tool that no family claims).

| Family | Tools |
|---|---|
| start here (`tool_guide`, `query_knowledge`, `health_check`, `project_audit`, ...) | 6 |
| learn the engine: manual, CVars, editor API, Schematyc, shaders, assets, validators and catalogs | 63 |
| verify without the editor (lint, packaged-game screenshots, shot comparison, audio report) | 7 |
| protect the work (project snapshots, diff, restore) | 4 |
| long jobs (start, status, log, stop) | 7 |
| make assets (textures, materials, meshes, voices, music) | 7 |
| level objects (editor open) | 18 |
| build a world (editor open): new level, terrain, vegetation, save | 6 |
| terrain (editor open) | 4 |
| navigation and AI (editor open) | 6 |
| editor and engine control (console, Python, CVars, dialogs, screenshots, load/export level) | 15 |
| study and inspect (levels, game data, entity physics) | 9 |
| project selection (`project_info`, `use_project`) | 2 |

The full list with flags (needs editor, writes files, closes editor) is in [docs/tools.md](docs/tools.md).

## Safety

- The bridge listens on `127.0.0.1` only and has **no authentication**. Any local process can send it commands.
- `execute_python` and `execute_python_script` run arbitrary Python inside the editor. The `really` flag only guards against accidents; it is not a sandbox.
- Some jobs close the editor or start the game; they refuse unless `allow_editor_close=true` is passed.
- The editor auto-recovery (which can start `Sandbox.exe` or terminate a crashed one) is off by default and enabled with `CLAUDE_AUTO_RECOVER=1`.
- Tools that replace existing files require `overwrite=true`. Take a snapshot (`snapshot_project`) before bulk changes: a project without version control has no other undo.

Details: [docs/safety.md](docs/safety.md). How to report a vulnerability: [SECURITY.md](SECURITY.md).

## Repository layout

The MCP server is `server.py` at the root. Everything it calls lives in folders by purpose. Modules import each other by plain name (`import bridge_py`); `_paths.py` puts all the folders on `sys.path`, and every module starts with a three-line header that imports it, so any module also runs on its own: `python checks/material_check.py`.

| Path | Contents |
|---|---|
| `server.py` | The MCP server and all 154 tool definitions. |
| `_paths.py` | Makes the tool folders importable by module name; `_paths.ROOT` is the repository root. |
| `bridge/` (29) | The editor connection and its upkeep: the TCP client (`bridge_py.py`), editor start/close/recovery (`editor_open.py`, `safe_close.py`, `bridge_watchdog.py`), background jobs (`jobs.py`), health checks, project selection (`project_root.py`), editor Python/command wrappers. |
| `checks/` (23) | Validators that compare your project data with what the engine itself loads: materials, flow graphs, behaviour trees, UI, DRS, navigation, audio, asset lint, project audit. |
| `catalogs/` (17) | Catalogs of what the engine offers (CVars, Schematyc, shaders, particles, objects, audio triggers, RC options) plus the offline manual search. |
| `level/` (14) | Levels and objects: placing, reading and checking objects, terrain, world building, level statistics. |
| `assets/` (13) | Asset makers: textures, materials, meshes and FBX, particles, music, sounds and voices, image converters. |
| `animation/` (22) | Animation checks and tools, plus the optional MotionBricks / Kimodo pipeline (third-party models, see [NOTICE.md](NOTICE.md)). |
| `screens/` (12) | Pictures and rendering: screenshot comparison, visual checks, debug views, render tours, lighting and frame profiling. |
| `packaging/` (14) | Snapshots, packaging, pre-flight checks, shader-cache refresh, release manifests. |
| `examples/ashvale/` (23) | Modules from the original game project (the "Ashvale" sample). Examples of project-specific code, not general tools. |
| `tests/` (68) | The test scripts. See [Tests](#tests). |
| `plugin/ClaudeBridgePlugin/` | The C++ Sandbox editor plugin (CMake, sources). |
| `engine-patches/` | Patches to the CRYENGINE build files; `0001` is required for CMake to know the plugin, `0002` is optional (CMake 4.x, no FBX SDK). Whether a plugin built this way loads in the stock Launcher Sandbox is not verified. |
| `rag/` | The knowledge base (`cryengine_project_rag.jsonl`), its query program and maintenance scripts. See `rag/README.md`. |
| `engine_data/` | Created by the tools when they dump catalogs from a running editor (not committed; empty apart from `.gitkeep` in a fresh clone). |
| `tools/` | The generator for `docs/tools.md` and `docs/modules.md`, and the metadata it reads. |
| `docs/` | Documentation: [docs/README.md](docs/README.md). |
| `knowledge_holdout.json`, `knowledge_dev.json`, `package_allowlist.json`, `preflight_exempt.json` | Data files read by the knowledge evaluation and the packaging and pre-flight modules. |
| `requirements.txt`, `LICENSE`, `NOTICE.md`, `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md` | Project files. |

The full module list with purposes is in [docs/modules.md](docs/modules.md).

## Tests

Every `test_*.py` is a plain script that prints its checks and exits non-zero on failure (run it with `python tests/test_x.py` from the repository root; no pytest needed). `CLAUDE_AUTO_RECOVER` is off by default, so a test never starts an editor.

All 68 test files were run once on a machine with no editor running, no CRYENGINE project and no captured catalogs: 53 exited 0, 15 failed or timed out. "Exit 0" includes tests whose live part is skipped when the editor is absent (for example `test_lods_live`, `test_level_stats`, `test_editor_panel`), so it does not mean the live behaviour was checked. There is no single command that runs all tests; run each file. The reasons below for the 15 failures were not verified test by test:

- Need a **catalog captured from a running editor** (`python <folder>/<module>.py --capture`, for example `python catalogs/cvar_catalog.py --capture --build` with Sandbox open; output goes to `engine_data/`, except `python bridge/editor_api.py --capture`, which writes `editor_api.json` in the repository root): `tests/test_debug_views.py`, `tests/test_editor_api.py`, `tests/test_editor_commands.py`, `tests/test_mcp_tools.py`, `tests/test_object_catalog.py`, `tests/test_particle_catalog.py`, `tests/test_pfx_author.py`, `tests/test_render_tour.py`, `tests/test_server_support.py`.
- Need the **editor itself** (the bridge on 127.0.0.1:37778): `tests/test_anim_sample.py`, `tests/test_object_props.py`, `tests/test_tool_smoke.py`.
- Need the **original game project's data**: `tests/test_health.py`, `tests/test_kimodo_clips.py`, `tests/test_project_audit.py`. Their expectations are specific to that project's files, shader cache or animation sources; treat them as examples of the style of check.

Exiting 0 without an editor (files in `tests/`; some skip their live part): `test_ace_catalog`, `test_anim_check`, `test_asset_catalog`, `test_asset_lint`, `test_asset_meta`, `test_audio_report`, `test_bt_check`, `test_capture_safety`, `test_character_check`, `test_cryproject_check`, `test_cvar_catalog`, `test_decal_lab`, `test_drs_check`, `test_editor_panel`, `test_editor_suite`, `test_editor_ux`, `test_editor_window`, `test_engine_tests`, `test_entity_check`, `test_env_check`, `test_flow_check`, `test_flow_graph`, `test_frame_profile`, `test_gamesdk_audio`, `test_gametoken_check`, `test_knowledge_eval`, `test_level_check`, `test_level_stats`, `test_lods_live`, `test_mannequin_check`, `test_manual`, `test_material_check`, `test_materialfx`, `test_nav_check`, `test_object_check`, `test_objects`, `test_pak_textures`, `test_pbs_check`, `test_pipeline`, `test_prune_imports`, `test_rc_catalog`, `test_schematyc_catalog`, `test_sequence_check`, `test_shader_catalog`, `test_shot_compare`, `test_snapshot`, `test_ui_check`, `test_uqs_check`, `test_visarea_check`, `test_visual_check`, `test_watchdog_crash`, `test_watchdog_recover`, `test_world_build`.

Some tests create scratch folders (`job_runs/`, `state/`, `engine_data/*_scratch/`, `texture_src/`) in the repository; they are git-ignored and safe to delete.

## Licence

MIT. See [LICENSE](LICENSE). Third-party notices, and what is not covered by this licence, are in [NOTICE.md](NOTICE.md).
