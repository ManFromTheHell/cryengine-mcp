# Architecture

How the pieces fit together, for CRYENGINE 5.7.1. Statements below were checked against the code at the time of the last edit; if the code has moved on, the code wins. Anything not checked says "not verified".

Related: [`plugin-protocol.md`](plugin-protocol.md) (the wire protocol and verb list), [`safety.md`](safety.md) (threat model), [`CRYENGINE_BRIDGE.md`](CRYENGINE_BRIDGE.md) (the editor's Python API reference), [`CLAUDE_BRIDGE_OPS.md`](CLAUDE_BRIDGE_OPS.md) (operating notes and failure modes).

## 1. Components

| Component | Where | Role |
|---|---|---|
| MCP server | `server.py` | A [FastMCP](https://github.com/modelcontextprotocol/python-sdk) stdio server named `cryengine`. It registers 154 `@mcp.tool()` functions. It is the only thing an MCP client talks to. |
| Server support | `bridge/server_support.py` | Wraps every tool at start-up: bounded output, worker threads, relaunch report. Holds the danger deny-list and the level-loading and export logic (so they can be tested without an editor). |
| Bridge client | `bridge/bridge_py.py` | The Python side of the TCP protocol used by helper modules and scripts: `send`, `run_source` (multi-line Python), auto-recovery, timeout and refusal messages. `server.py` has its own small `_exchange`/`_send_command` pair with the same behaviour. |
| Helper modules | the tool folders (`bridge/`, `checks/`, `catalogs/`, `level/`, `assets/`, `animation/`, `screens/`, `packaging/`) | One module per job, imported by the tools: checkers and catalogs (`checks/flow_check.py`, `checks/material_check.py`, `checks/nav_check.py`, `catalogs/cvar_catalog.py`, ...), authoring (`level/world_build.py`, `level/objects.py`, `assets/texture_lab.py`, `assets/pfx_author.py`, ...), file-only readers (`packaging/crypak.py`, `level/level_inventory.py`, ...). Many need the editor, many do not. |
| Jobs | `bridge/jobs.py` | Starts long-running whitelisted work as a detached process and tracks it (section 7). |
| Watchdog | `bridge/bridge_watchdog.py`, `bridge/safe_close.py` | Keeps the editor alive from outside it; closes it gracefully before jobs (section 7). |
| Snapshots | `packaging/snapshot.py` | Content-addressed backups of the project (section 8). |
| Project selection | `bridge/project_root.py` | Decides which CRYENGINE project the file-based tools work on (section 9). |
| Editor plugin | `plugin/ClaudeBridgePlugin/` | C++ Sandbox editor plugin. Listens on `127.0.0.1:37778` and executes commands on the editor's main thread. |
| Maintainer loop | `bridge/bridge_devloop.py`, `bridge/bridge_selftest.py`, `bridge/bridge_run.py` | Build, deploy and self-test the plugin. Maintainer-only; not needed to use the tools. |
| Knowledge corpus | `rag/` | `cryengine_project_rag.jsonl` (traps, procedures, engine facts) plus `rag_query.py`, a stdlib BM25 search over it. The `query_knowledge` tool uses it. Each chunk records how it was verified (`status`, `verified_in`). |
| Offline docs search | `catalogs/manual.py`, `catalogs/docs_search.py`, `search_manual`, `read_manual_page`, `manual_toc` | Search of a local copy of the CRYENGINE manual. The copy itself is not part of the repository: save the CRYENGINE manual as markdown into a folder and point `CRYENGINE_DOCS` at it (or use `catalogs/scrape_cryengine_docs.py`, which is the only code in the repository that downloads anything; no tool calls it). |

## 2. Data flow

```
 MCP client (Claude Desktop, Claude Code, ...)
        |  stdio, MCP protocol
        v
 +--------------------------------------------------------------+
 | server.py  (154 tools)          server_support.install(mcp)  |
 |   * plain-def tools run in worker threads (asyncio.to_thread)|
 |   * results > ~20,000 chars saved to job_runs/out/, summarised|
 |   * danger deny-list (really=true) on python/console/command |
 +----+---------------------+----------------------+------------+
      |                     |                      |
      | file-only tools     | editor tools         | start_job / snapshot
      v                     v                      v
 helper modules      _send_command / bridge_py   bridge/jobs.py  --spawns--> detached
 (read project       one TCP connection per         |               "bridge/jobs.py _run"
  files, paks,       command, JSON line each        |               -> script file from a
  catalogs)          way                            |               tool folder (whitelist)
                            |                       v
                            |               job_runs/<id>.json + .log
                            v
              127.0.0.1:37778  (loopback, no auth)
 +--------------------------------------------------------------+
 | Sandbox.exe  --  ClaudeBridgePlugin.dll                      |
 |  accept thread -> one thread per connection                  |
 |     out-of-band verbs (window, input, screenshot) run HERE   |
 |     everything else -> queue + wait on a future (20 s)       |
 |                              |                               |
 |  main (UI) thread  <---------+   drained by                 |
 |     eNotify_OnIdleUpdate  and  25 ms WM_TIMER (any msg loop)|
 |     dialog queue (10 s) drained by the same timer            |
 |     -> engine: IConsole / ICommandManager / Python / C++ API |
 +--------------------------------------------------------------+
        |
        v
  CRYENGINE editor state (level, terrain, objects, ...)

 bridge/bridge_watchdog.py (Task Scheduler, outside the editor) probes the bridge,
 relaunches Sandbox, reopens the level.
```

The server opens a **new TCP connection for every command** (`_exchange`), sends one line and reads one line.

## 3. The three command registries, and the plugin's own verbs

Sandbox has three separate command registries. A command that exists in one returns "Unknown command" in the others. This is the most common way to waste time with the editor.

| Registry | What it holds | Reached by | MCP tools that use it |
|---|---|---|---|
| `IConsole` | CVars and true console commands (`r_DisplayInfo 1`, `goto x y z`, `e_TimeOfDay 12`) | any command text that does not start with `bridge.` | `run_console_command`, `goto_position` |
| `ICommandManager` | `module.command` names registered with `REGISTER_EDITOR_AND_SCRIPT_COMMAND` / `REGISTER_PYTHON_COMMAND` (`asset.edit`, `general.save_level`, `ai.*`, ...). Fire-and-forget: the log says whether it did anything. | `bridge.execute_command <cmd>` | `execute_editor_command`, `regenerate_navigation`; internally `load_level` (`asset.edit`), `export_level` (`exporter.export_to_engine`, `ai.regenerate_ignored`), `level/level_stats.py` (`level.save_stats`, `level.validate`), `bridge/editor_state.py` |
| Python interpreter | Sandbox's embedded Python with the `sandbox` modules pre-imported (`general`, `object`, `level`, `terrain`, ...). It also holds Python-only commands, such as `general.new_object` and `general.create_object`, that never reach `ICommandManager`. | `bridge.execute_python`, `.b64`, `.raw` | `execute_python`, `execute_python_script`; internally `bridge_py.run_source` in `level/world_build.py`, `level/object_props.py`, `bridge/editor_api.py`, `bridge/editor_ux.py`, `bridge/editor_window.py` |

The fourth path is the plugin's own typed **verbs** (`bridge.terrain.*`, `bridge.object.*`, `bridge.env.*`, `bridge.db.*`, ...). They call C++ editor classes directly, for things none of the three registries expose, and return structured JSON. Most of the tools (terrain, vegetation, objects, entities, dialogs, environment, and the `*_check` validators that dump engine state) use these. `get_cvar` and `set_cvar` use `bridge.cvar_get`/`bridge.cvar_set`, which go through `ICVar` directly instead of console text.

Two cross-over facts:

- `run_console_command` accepts `bridge.*` text, because the plugin matches the raw command before the console fallback. Any verb is reachable through it, with no dedicated tool.
- `CRYENGINE_BRIDGE.md` lists the Python surface (586 functions in 24 modules on the editor it was captured from; `python bridge/editor_api.py --capture` regenerates the dump) and flags the functions that open modal dialogs. A modal dialog blocks the editor until someone answers it; see `list_dialogs` / `click_dialog_button` and section 5.

Why not just use Python for everything? It is the widest surface, but a script that leaves the editor in a modal state or depends on printed output is fragile. The verbs return checked, structured results, and can be used while Python-level output is unreliable (printing `%` inside the editor's Python reaches a printf-style logger; the plugin works around it by capturing stdout, see `CLAUDE_BRIDGE_OPS.md`).

## 4. Out-of-band and queued verbs

| Class | Verbs | Executes on | Why |
|---|---|---|---|
| Out-of-band | `bridge.viewport.screenshot`, `bridge.window.*`, `bridge.input.*` | The client's TCP thread. Win32 calls only, no engine state. | They must work when the main thread is not servicing the queue, for example behind a modal dialog, so a stuck editor can still be looked at and unblocked. |
| Dialog queue | `bridge.dialog.list`, `bridge.dialog.click` | Main thread, own queue, drained by the 25 ms timer even inside a modal loop. | A command that opened a modal dialog holds the main queue until the dialog closes; the verb that closes it cannot wait in the same queue. The click itself is queued (`QueuedConnection`), so it cannot stall. |
| Main queue | all other verbs, and raw console commands | Main thread. | The engine and editor are not thread-safe. Only the main thread touches `gEnv` or editor objects. |

Out-of-band is not the same as "works when the editor is hung". Some of these verbs wait for the editor's UI thread inside Win32 calls (`safety.md`, D8).

## 5. The main-thread model and timeouts

Threads in the plugin: the editor's UI thread, one accept thread, and one detached thread per client connection. Client threads never touch the engine. They push a pending command and block on a `std::promise` future.

Draining the queue happens in two places on the UI thread:

1. the editor's `eNotify_OnIdleUpdate` event. It stops firing when the window is minimised, a menu is open or a modal dialog is up;
2. a `WM_TIMER` every 25 ms on a hidden message-only window `ClaudeBridgePumpWindow`. Every message loop dispatches it, including nested modal loops.

The bridge's own queue does not depend on window focus: the 25 ms timer drains it either way. The editor's own update (for example the deferred game-mode toggle) runs only while a Sandbox window is active. Putting `ed_keepEditorActive = 1` in `user.cfg` has no effect; changing the CVar from 0 to 1 while the editor is running does (see [`troubleshooting.md`](troubleshooting.md)).

| Timeout | Value | Where | What happens |
|---|---|---|---|
| Plugin main-queue wait | 20 s | `kCommandTimeoutSeconds` | The client gets `timed out waiting for the editor main thread...`. The command stays queued and may still run (`safety.md`, D10). |
| Plugin dialog-queue wait | 10 s | `kDialogTimeoutSeconds` | `timed out: the editor main thread ran no message loop for 10 s`. |
| Timer interval | 25 ms | `kPumpIntervalMs` | |
| Server default socket timeout | 25 s | `TIMEOUT_SECONDS` in `server.py` | Kept above the plugin's 20 s so the plugin answers first. On a timeout the server probes `bridge.dialog.list` (3 s limit) and adds the open dialogs and a next step to the error. |
| `bridge_py.send` default | 120 s | `bridge/bridge_py.py` | Callers override it. `load_level` sends `asset.edit` with 240 s and then polls until the level is open (up to 180 s); it reports an open dialog instead of waiting it out. |
| Level-load style commands | longer than 20 s | | The plugin's 20 s wait expires while the command keeps running; tools that expect this poll for the result rather than trust the reply. |

A drain takes the whole queue at once and runs the commands in order. If a command opens a modal dialog, the commands already taken wait until it returns. The 20 s limit counts waiting plus running.

Do not call the bridge synchronously from the editor's own Python interpreter. The interpreter holds the main thread that would answer, and the editor freezes until the plugin's timeouts drain the queue. `bridge/bridge_py.py` and `bridge/bridge_run.py` are meant to run from a normal shell.

## 6. Server internals

`server_support.install(mcp)` wraps every registered tool:

- **Worker threads.** A plain-`def` tool runs with `asyncio.to_thread`, so a slow bridge call does not freeze other tools. A short list (`tool_guide`, `server_info`, `read_output`, ...) runs inline.
- **Bounded output.** A result over about 20,000 characters is written to `job_runs/out/<id>.json`. The tool returns a summary (small values kept, big ones described by type and count) and the id. `read_output(id, key, offset, limit)` pages through the saved file.
- **Relaunch report.** If the call caused the editor to be restarted (auto-recovery below), the result carries `editor_relaunched: true`.
- **Error explanation.** `_send_command` turns a timeout into "a modal dialog may be open: use `list_dialogs` / `click_dialog_button`" with the open dialogs listed, and a refused connection into "the editor is not running: use `wait_for_editor` / `health_check`".
- **Auto-recovery (opt-in).** `server.py` sets `CLAUDE_AUTO_RECOVER=0` by default, so a refused connection is reported as an error and nothing is started or killed. With `CLAUDE_AUTO_RECOVER=1` in the server's environment, a refused connection calls `bridge_watchdog.recover`. It first runs the scheduled task `ClaudeBridgeWatchdog` (`schtasks /Run /TN ClaudeBridgeWatchdog`); only if that task cannot be run does it launch Sandbox itself, killing a crashed editor first (`taskkill /F`, nothing is saved). It stands down when the "closed on purpose" marker file exists (written by `bridge/safe_close.py` when a job closes the editor deliberately).
- **Danger deny-list.** `really=true` is needed for calls that exit the editor, unload plugins, run Lua or script files, open modal dialogs or (in Python) run programs or delete files (`safety.md`, section 5).
- **Child processes** are started with `stdin=DEVNULL`: under MCP stdio the inherited stdin is the protocol pipe, and a child that reads it blocks.
- **`server_info`** reports when the server started and which modules were edited since. A running server keeps the old code until the MCP connection is restarted.

## 7. Jobs and the watchdog

### Jobs (`bridge/jobs.py`)

A job is for work that outlasts a tool call: packaging, test groups, a shader cache refresh, rendering audio. `start_job(kind, params, allow_editor_close)`:

1. Looks `kind` up in the fixed table `KINDS`. An unknown kind is refused. Each kind names one script file of the repository (found by file name in the tool folders) and states whether it needs the machine to itself, closes the editor, and launches the game.
2. Validates `params` in `build_argv` against fixed lists and patterns: names from a list, numbers in a range, a build name, console variables matching `name number`, shot lines matching a short set of patterns (a console command in a shot line may not be `exec`, `quit`, `bind`, `alias`, `unbind`, `disconnect` or `unload`). Anything else raises `JobError`. No shell is involved and no string from the caller becomes a command line.
3. Refuses an editor-closing kind while `Sandbox.exe` runs unless `allow_editor_close` is true, and refuses to start an exclusive kind while another one runs.
4. Writes `job_runs/<id>.json`, then starts `python -u bridge/jobs.py _run <id>` **detached** (`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`, stdin from `DEVNULL`), so it survives a tool timeout and a server restart. The wrapper closes the editor gracefully if needed (`bridge/safe_close.py`), runs the script, and writes the exit code into the record.
5. `job_status` reads the record, folds in a verdict read from the job's output (a test run exits 0 even when tests fail: the `SUMMARY` line counts), and marks a job whose process vanished as `lost`. `read_job_log` reads the log. `stop_job` kills the process tree. The newest 30 jobs are kept.

Kinds: `tests`, `package`, `build_game`, `shader_cache`, `shoot`, `visual`, `cvar_probe`, `ci`, `editor_suite`, `voices`, `music`, `textures`, `selftest`. Several of them (`tests`, `package`, `shoot`, `visual`, `ci`, `voices`, `music`) were written for the author's own game and its packaging layout; they are not general-purpose. `editor_suite` runs every `test_*.py` for the editor-only tools against an open editor. `job_kinds` lists parameters.

### Watchdog (`bridge/bridge_watchdog.py`)

The bridge lives inside `Sandbox.exe`, so a crashed editor takes the means of recovery with it. The watchdog therefore runs outside the editor, from Task Scheduler (`--install` registers `ClaudeBridgeWatchdog` every 5 minutes). Each cycle:

1. stands down if a deploy lock is fresh, a "closed on purpose" marker exists, or a pause file exists;
2. launches Sandbox if it is not running;
3. probes the bridge with a **queued** command (`bridge.cvar_get r_DisplayInfo`), deliberately: an out-of-band verb would answer from the TCP thread while the main thread is wedged. After two consecutive failures (or a crash report newer than the editor's start) it kills the editor and relaunches it;
4. after a relaunch, rewrites the startup project (`projects.json`), reopens the remembered level (or the level named in `CRYENGINE_FALLBACK_LEVEL` when none is remembered), and re-applies the bridge commands in `CRYENGINE_ENV_AFTER_RELOAD` (separated by `;`, empty by default).

The watchdog exists only after `python bridge/bridge_watchdog.py --install`. Without it nothing restarts the editor, except the opt-in auto-recovery above. The engine folder it launches from comes from `CRYENGINE_ENGINE`.

Tools can call the same logic on demand (`recover`): it runs the scheduled `ClaudeBridgeWatchdog` task if that task can be run and otherwise launches Sandbox itself. The watchdog kills an editor without saving. See `safety.md` section 6.

## 8. Snapshots (`packaging/snapshot.py`)

`snapshot_project`, `list_snapshots`, `diff_snapshot`, `restore_files`. Content-addressed: each distinct file is copied once to `blobs/<xx>/<sha256>`; a snapshot is a manifest (`manifests/<id>.json`) mapping paths to hashes, written last and atomically, so an interrupted run leaves no half snapshot. A snapshot after a small change stores only what changed. It stores the active project folder minus regenerable output (build folders, job runs, logs, `.bak` files, shader backups). `CRYENGINE_SNAPSHOT_INCLUDE_EXTRAS=1` adds more sources (documents, the plugin source, `git diff` of the engine checkout). The store is the `_snapshots` folder beside the project folder (`CRYENGINE_SNAPSHOT_DIR` overrides). It is on the same disk, so it does not protect against disk failure. Nothing in the module deletes a snapshot. `restore_files` restores to a side folder by default; with `overwrite=true` it first takes a `before_restore` snapshot. The default fast path trusts size and modification time; `full=true` re-hashes.

## 9. Choosing the project (`bridge/project_root.py`)

All file-based tools take their project folder from `project_root.PROJECT`, resolved once at import in this order:

1. environment variable `CRYENGINE_PROJECT` (a folder holding a `.cryproject`, or the file itself; `Game.cryproject` is preferred);
2. `active_project.txt` next to the server (written by the `use_project` tool or `python bridge/project_root.py set <path>`);
3. otherwise the parent folder of the folder that holds `server.py`. Set one of the first two when you install this server standalone, or the parent folder is assumed to be the project.

Changing the project means restarting the MCP server. The editor is a separate matter: it runs one project at a time, chosen by `projects.json`'s startup entry. `bridge/project_root.py editor` and the editor launchers write that entry (this replaces the Launcher's project list; `safety.md` section 6). `project_info` reports both the active project and the project the open editor has, and warns when they differ. Editor-side tools describe whatever the open editor holds regardless of the active project.

## 10. Build and deploy of the plugin

The plugin is original code that includes CRYENGINE headers. It is built inside the engine's CMake tree as an editor plugin: `CryEditorPlugin(ClaudeBridgePlugin ...)` with `target_link_libraries(ClaudeBridgePlugin PRIVATE Sandbox)`, because it needs classes exported from the Sandbox executable (`CTerrainManager`, `CHeightmap`, `CVegetationMap`, `CGameExporter`, `CLensFlareManager`). The folder is added to the build by `engine-patches/0001-build-claude-bridge-plugin.patch`, which adds the CMake option `PLUGIN_CLAUDE_BRIDGE` (default OFF) to `Tools/CMake/BuildSandbox.cmake`; step-by-step build instructions are in [`installation.md`](installation.md). The build output is `bin/win_x64/EditorPlugins/ClaudeBridgePlugin.dll`. Sandbox loads it at start-up from `EditorPlugins`. The log line `[ClaudeBridgePlugin] Listening on 127.0.0.1:37778` confirms it.

Not verified: whether a DLL built against a locally built `Sandbox` import library loads in the stock Launcher `Sandbox.exe`; the exact Visual Studio, CMake and Qt versions required. `bridge/bridge_devloop.py` can build, deploy and self-test; its paths come from the environment variables `CRYENGINE_ENGINE_DIR`, `CRYENGINE_SOURCE_DIR` and `CRYENGINE_MSBUILD` (defaults are one machine's layout), and its deploy step depends on helper `.bat` files that are not in this repository. A plugin that does not load leaves no bridge, so keep the previous DLL (`.prev`) next to the new one.
