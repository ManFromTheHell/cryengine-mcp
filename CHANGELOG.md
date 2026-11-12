# Changelog

All notable changes are listed here, newest first. Targets CRYENGINE 5.7.1 LTS.

## Unreleased

### Changed

- **Portable defaults.** No module defaults to a folder of the original machine any more. `_paths.py` now provides `WORKSPACE` (`CRYENGINE_WORKSPACE`, else the older `CRYENGINE_STATE_DIR` / `CLAUDE_STATE_DIR`, else `<repository>/state`), `ENGINE_SOURCE` (`CRYENGINE_SOURCE`, else `CRYENGINE_SOURCE_DIR`, else unset) and `MOTIONBRICKS_ROOT` (`MOTIONBRICKS_ROOT`, else `<repository>/anim_work/motionbricks`). The deploy lock, the "closed on purpose" marker, the pause and last-level files, the game trace log and the devloop batch files all live in the workspace folder, so `CLAUDE_STATE_DIR` and `CRYENGINE_STATE_DIR` no longer name different folders.
- **Engine source is optional.** With `CRYENGINE_SOURCE` unset, the catalogs skip their source-reading steps and rely on the editor, symbol search skips the source `bin` folders, and `bridge/bridge_devloop.py build/deploy/cycle` stop with a message. `checks/env_check.py` still needs it and says so.
- **Shader tooling paths.** The shader staging folder defaults to `<workspace>/ShaderPak` (`CRYENGINE_SHADER_STAGE`) and the Remote Shader Compiler to `<engine>/Tools/RemoteShaderCompiler` (`CRYENGINE_RSC_DIR`); an existing `C:\ShaderPak` or `C:\ShaderCompiler` setup must set these variables. `CRYENGINE_SHADER_PAK_DIR` defaults to the `Engine` folder of the stage.
- **Snapshots.** `packaging/snapshot.py` extras read the docs and memory folders only from the new variables `CRYENGINE_SNAPSHOT_DOCS` and `CRYENGINE_SNAPSHOT_MEMORY`; the plugin source and the `git diff` are skipped when `CRYENGINE_SOURCE` is unset.
- **Manual scraper output** defaults to `CRYENGINE_DOCS`, else `<repository>/cryengine_manual`.
- **Example and module comments** were rewritten as neutral technical statements; dates, diary remarks and audit ids were removed from the comments of the bridge, packaging, catalog and Ashvale example modules. Some modules outside that pass still carry dated or first-person comments (see docs/limitations.md).
- Documentation updated to match: installation guide (environment variables), safety, limitations, bridge operations, animation pipeline, contributing guide and the generated module reference.

## 0.1.0 - 2026-11-12 - initial public release

First public version of the MCP server, the editor plugin and the knowledge base.

### Contents

- **MCP server** (`server.py`): 154 tools over stdio, grouped by `tool_guide` into families: start here, learn the engine (manual, CVars, editor API, Schematyc, shaders, validators and catalogs), verify without the editor, protect the work (snapshots), long jobs, make assets, level objects, build a world, terrain, navigation and AI, editor and engine control, study and inspect, and project selection.
- **Helper modules**: validators and catalogs that compare project data with what the engine loads, asset makers, snapshots, screenshot and audio measurement, background jobs, editor health and recovery.
- **ClaudeBridgePlugin** (`plugin/ClaudeBridgePlugin/`): a Sandbox editor plugin that serves a line-based JSON protocol on `127.0.0.1:37778`.
- **Engine build patches** (`engine-patches/`): a patch that adds the `PLUGIN_CLAUDE_BRIDGE` build option, and an optional patch for CMake 4.x and builds without the Autodesk FBX SDK.
- **Knowledge base** (`rag/`): searchable notes on engine traps and procedures, with a query program, maintenance scripts and evaluation questions.
- **Tests**: 68 standalone `test_*.py` scripts; many run without an editor, some of them skipping their live checks (see the README).
- **Project selection**: `project_info` and `use_project` choose the served project (`CRYENGINE_PROJECT`, else `active_project.txt`, else the parent folder of the repository; see `bridge/project_root.py`).
- **Folder layout**: only `server.py` and `_paths.py` are in the repository root; the helper modules live in `bridge/`, `checks/`, `catalogs/`, `level/`, `assets/`, `animation/`, `screens/`, `packaging/`, `examples/ashvale/` and `tests/`.
- **Catalog capture**: `catalogs/cvar_catalog.py`, `catalogs/object_catalog.py`, `catalogs/particle_catalog.py`, `catalogs/schematyc_catalog.py` and `bridge/editor_commands.py` take `--capture` to dump their data from a running editor into `engine_data/`; `bridge/editor_api.py --capture` writes `editor_api.json` in the repository root.
- **Documentation**: README, tool and module references generated from the code, the plugin protocol, safety and installation guides, engine notes, `NOTICE.md`, `SECURITY.md` and `CONTRIBUTING.md`.

### Hardening done before release

- **Plugin accepts only JSON requests.** The first non-blank byte of a connection must be `{`; anything else (for example an HTTP request sent by a web page to the loopback port) is answered with an error and the connection is closed before anything is parsed or run. The plugin still binds `127.0.0.1` only.
- **Editor auto-recovery is off by default.** `CLAUDE_AUTO_RECOVER` defaults to `0`; set it to `1` to let a tool start `Sandbox.exe` or terminate a crashed one.
- **User names removed.** The author's user name and e-mail address were removed from the code, documentation and knowledge base. At the time of this release many modules still defaulted to `C:\CRYENGINE-source` when the matching environment variable was not set (replaced by portable defaults, see Unreleased above), and some engine notes and knowledge chunks still describe the author's machine and decisions. See docs/installation.md (Environment variables) and docs/limitations.md.
- **Requirements.** `requirements.txt` lists `mcp` and `numpy` (both needed to start the server); the installation guide lists the optional packages; the Python 3.10 minimum is documented.
- **Dead and one-off files left out**: migration scripts, scratch output, run logs, local settings and notes derived from Crytek sample projects are not part of the release, and `.gitignore` keeps them out.
- **Engine patches reduced** to the build changes the plugin needs. Changes to engine gameplay code and an obsolete export change were dropped.

### Known limitations

Some modules and tests still assume the layout of the project they were written for. See [docs/limitations.md](docs/limitations.md).
