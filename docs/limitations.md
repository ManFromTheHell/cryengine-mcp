# Limitations

What this project does not do, what it needs from outside, and what has not been verified. Where a
statement could not be checked against the code or a review of it, it says "not verified".

## Scope

| limit | detail |
|---|---|
| Engine version | CRYENGINE 5.7.1 LTS only. The editor API catalogs, the plugin and every measurement in the [knowledge base](knowledge-base.md) are from 5.7.1. Other versions are not verified |
| Operating system | Windows only. The code uses `taskkill`, `tasklist`, `schtasks`, PowerShell, Win32 window capture and `.exe` tools |
| One editor, one project | The plugin listens on one fixed TCP port, 37778 on 127.0.0.1, which is a compile-time constant in the plugin. The Python side can be pointed elsewhere with `CRYENGINE_BRIDGE_HOST` and `CRYENGINE_BRIDGE_PORT`, but the plugin does not read them. The editor opens one project at a time; the server picks its project from `CRYENGINE_PROJECT`, then `active_project.txt`, then the folder the server lives in (`bridge/project_root.py`). `active_project.txt` is written by the `use_project` tool; the folder must hold a `.cryproject` file, and the choice is cached until the MCP server restarts |
| Python | The server needs Python 3.10 or newer (its code uses PEP 604 union annotations, a type and `None` joined by a vertical bar, which need 3.10). The editor's embedded interpreter is Python 3.7, so code sent to it must be 3.7 compatible |
| Editor required | Most tools talk to a running Sandbox through the plugin. Tools that read project files, run the engine's own command-line tools or search the knowledge base work with the editor closed. A tool's description says which it needs |
| Hardware | Developed and run on one machine. Other GPUs, drivers and display setups are not verified. Several tools capture the screen, so a display that is off or asleep can produce blank or stale pictures |

## Getting the plugin into the editor

The editor plugin (`plugin/ClaudeBridgePlugin`, C++) links against the editor's own `Sandbox`
target and uses editor internals (`CEditorImpl`, `CTerrainManager`, `CHeightmap`,
`CVegetationMap`, `CGameExporter`, `CLensFlareManager`). So:

- Building it needs a full CRYENGINE 5.7.1 source build with CMake and Visual Studio. The exact
  Visual Studio, CMake and Qt versions required are not verified; `bridge/bridge_devloop.py`
  defaults to one Visual Studio MSBuild path and takes `CRYENGINE_MSBUILD` to override it.
- Whether a plugin built this way loads in the stock Sandbox that the CRYENGINE Launcher installs
  is **not verified**. The author ran locally built `Sandbox`, `EditorCommon`, `CryQt` and
  `MFCToolsPlugin` libraries, and a matching export is unproven for the stock ones.
- The engine build-file changes the author used are in `engine-patches/`: only `0001` is needed to
  build the plugin; `0002` helps with CMake 4 and a missing FBX SDK. A `SANDBOX_API` export on
  `GetIEditorImpl` was also in the author's tree, is in neither patch, and is not needed: the plugin
  does not reference it.
- No prebuilt plugin DLL is shipped.

## Things that need outside programs

None of these programs is bundled and the server never downloads anything. When one is missing, the
tool that needs it refuses and says what to install; installing it is your decision. (The one script
in the repository that uses the network is `catalogs/scrape_cryengine_docs.py`, which you run by hand to
save Crytek's manual.)

| program or package | needed by | notes |
|---|---|---|
| CRYENGINE Launcher with engine 5.7.1 LTS | the whole project | engine folder, editor, `rc.exe` and the packager come from it |
| CRYENGINE 5.7.1 source tree | building the plugin; `env_check`, `cvar_catalog` and `bt_check` read it; the C++ game build | the audit notes `bt_check` caches an empty registry when the source is absent and `env_check` needs the source tree for its variable table |
| Resource compiler `rc.exe` (from the engine) | `build_mesh`, `make_texture`, `asset_metadata`, `rc_presets` / `rc_options`, `start_job('package')`, the animation clip builders | several modules locate it through the default Launcher path (see "Remaining fixed locations" below) |
| `ffmpeg` and `ffprobe` on PATH | `make_music`, `render_voices`, `audio_report`, `compare_shots`, `visual_check`, sound-loop encoding, and image scaling in `shoot_game` results | `shoot_game` returns the raw PNG when `ffmpeg` is missing; the others fail with a message |
| Piper (`tts_piper/piper/piper.exe` plus the `.onnx` voice files in `tts_piper/`, under the repository root) | `render_voices`, `preview_voice` | Not bundled. Windows SAPI voices (`System.Speech`, part of Windows) are the fallback with `sapi=true` / `--sapi`. Voice quality was judged by listening only |
| Kimodo (NVIDIA text-to-motion, run separately) | generating new motion for `animation/kimodo_clips.py` | the repository converts previously generated `.npz` motion files; those files (`anim_sources/kimodo/`) are not in this repository, and generation needs a separate install. See [animation-pipeline.md](animation-pipeline.md) and [NOTICE.md](../NOTICE.md) |
| MotionBricks (NVIDIA, separate Python environment with PyTorch) | `animation/motionbricks_record.py`, `animation/motionbricks_scenario.py` | the other `motionbricks_*` modules use numpy and process recorded clips. A ROCm build of PyTorch on Windows lacks `torch.distributed` (`trap-rocm-windows-pytorch-no-distributed`) |
| `numpy` | dozens of modules (heightmaps, textures, audio, animation, picture comparison) | `requirements.txt` currently lists only `mcp` |
| `torch` | the two MotionBricks scripts above, in their own environment | not needed by the server |
| MSBuild, CMake, Visual Studio | `bridge/bridge_devloop.py`, `packaging/build_game.py`, `packaging/rebuild_game.py`, `bridge/game_build_locked.py` | the game DLL and plugin builds |
| Windows Task Scheduler (`schtasks`) | `bridge/bridge_devloop.py` (deploy and self-test tasks), `bridge/bridge_watchdog.py --install` | creates persistent tasks on your machine; see [safety.md](safety.md) |
| An offline copy of Crytek's manual (markdown) | `search_manual`, `read_manual_page`, `manual_toc` | not shipped. Save it with `catalogs/scrape_cryengine_docs.py` or point `CRYENGINE_DOCS` at your copy |
| A packaged game and `GameLauncher.exe` | `shoot_game`, `visual_check`, `start_job` kinds that play the game, the playthrough and picture tests | these jobs close the editor and launch the game; they require `allow_editor_close=true` |

## What is unverified

- **Most of the knowledge base.** A large share of its chunks carry `status: unclassified` and
  `verified_in: unclassified`, and some carry `verified_in: none` (built, never seen working). See
  [knowledge-base.md](knowledge-base.md).
- **Game-side features were mostly proven in the editor, not in a packaged game.** Where a tool
  reports success it reports what the engine read back, but a feature "working in the editor" is
  not the same as working in the packaged build.
- **Anything on another machine.** The tools were written and tested on one machine; defaults that
  assume that machine's folders are the most common failure on others.
- **The test suite is partly machine-specific.** Many tests assert counts or names from the
  author's game, some skip their live checks and exit 0 when the editor is closed (a pass with
  nothing checked), and the full editor-only suite takes several minutes with an editor and a
  loaded level. A green run on a clean machine does not show that the tools work.
- **The plugin's behaviour with other levels, large levels, and other editor layouts.** Picture
  tests crop the editor window by known window sizes; other layouts are not verified.

## Modules specific to the original game

The repository grew around one medieval-village game ("Ashvale") and carries its builders and tests.
Treat these as examples, not as general tools:

- level and content builders: `examples/ashvale/build_ashvale.py`, `examples/ashvale/undercroft_kit.py`, `examples/ashvale/uc_geom.py`, `examples/ashvale/weapon_kit.py`,
  `assets/texture_lab_crypt.py`, `examples/ashvale/villager_anim.py` and the other `*_kit.py` files
- playthrough and regression harnesses: `examples/ashvale/ashvale_ci.py`, `examples/ashvale/ashvale_playtest.py`,
  `examples/ashvale/undercroft_playthrough.py`, `examples/ashvale/release_playthrough.py`, `examples/ashvale/door_crossing_test.py`, `examples/ashvale/pickup_test.py`,
  `examples/ashvale/night_test.py`, `examples/ashvale/loading_screen_test.py`, `examples/ashvale/ui_menu_test.py`, `examples/ashvale/soundscape_test.py`
- content checks that assume its data layout: `examples/ashvale/story_check.py`, parts of `level/level_stats.py`,
  `checks/level_check.py`, `screens/visual_check.py` (fixed view set and one global baseline), `bridge/tool_smoke.py`
  (its table of sample calls)
- audio and voice tools tied to its dialogue files and cast: `assets/make_voices.py` (`render_voices`)
- the animation pipeline for its villager rig (`kimodo_*`, `motionbricks_*`, `animation/anim_oneshot.py`)

Several modules run their logic at import time or read `sys.argv` at import time (this was found
in `examples/ashvale/build_ashvale.py`, `catalogs/cvar_probe.py`, `examples/ashvale/night_test.py`, `examples/ashvale/ui_screens.py`,
`examples/ashvale/undercroft_playthrough.py`, `packaging/shader_cache_gen.py`), so importing them from another script has
side effects. Some tool signatures default to the original level name; pass your own level
explicitly and read the tool's description. The reviewed list is longer than this page; which
items have since been fixed is not verified here.

## Known issues from the code review

An internal review before release found a large number of defects (the list is not published, and
which were fixed is not verified here). By category, the kinds of problem that can remain are:

| category | what it means for you |
|---|---|
| Remaining fixed locations | The state files, the engine source tree and the MotionBricks folder no longer have machine-specific defaults: the shared files go to `<repo>/state` (`CRYENGINE_WORKSPACE`), the source tree is unset until `CRYENGINE_SOURCE` is given (the tools that need it say so), and MotionBricks defaults to `<repo>/anim_work/motionbricks` (`MOTIONBRICKS_ROOT`). What is left are standard Windows locations used when no variable is set (the Launcher's 5.7.1 engine and GameSDK folders, `%PROGRAMDATA%\Crytek\CRYENGINE\projects.json`, a Visual Studio 18 MSBuild path, `C:\Program Files\CMake`), which suit a default install and are overridden by the variables in [installation.md](installation.md); a few modules ignore the engine variables and use the Launcher path directly (`bridge/editor_panel.py`, `packaging/package_symbols.py`, `packaging/pak_textures.py`); and Ashvale-specific defaults in the example and packaging modules (`Ashvale` package prefix, `ashvale` preset prefix and level name, `AshvaleStart`, `Ashvale_shader_record`). The shader staging folder and the Remote Shader Compiler default to `<workspace>/ShaderPak` and `<engine>\Tools\RemoteShaderCompiler`; an existing `C:\ShaderPak` or `C:\ShaderCompiler` setup needs `CRYENGINE_SHADER_STAGE` / `CRYENGINE_RSC_DIR` |
| Dated and first-person comments | The comments of the bridge, packaging, catalog and Ashvale example modules were rewritten as neutral technical statements. Some modules still carry dated or first-person comments and a few runtime note strings in that voice (for example in `checks/audio_report.py`, `assets/pipeline.py`, `screens/visual_check.py`, `screens/debug_views.py`, `bridge/editor_api.py`, `bridge/tool_smoke.py`). They are cosmetic; read them as the author's field notes |
| Force-kill by image name | The watchdog, `safe_close`, the job runner and several test scripts end processes with `taskkill /IM Sandbox.exe` or `GameLauncher.exe`, which stops every instance on the machine, not only the one they started. Do not run them while you have unsaved work in another editor |
| Destructive defaults | Some scripts overwrite or delete files without a guard (all `.png` in an output folder, saved games of a build, backup pruning by name, a `clear` command that accepts an empty prefix). Snapshot first (`snapshot_project`) and read the tool's description |
| Silent passes | Some tests and checks report success when the editor is absent or the instrument returned zeros. A pass is not evidence unless the check can fail; see [engine-lifetimes.md](engine-lifetimes.md) |
| Stale caches in a long-running server | Some catalogs cache their first answer for the life of the server process (for example a character text cache and the behaviour-tree registry). Restart the MCP connector after changing engine data they read |
| Input validation | Archive extraction and asset-file reading take caller-supplied paths with limited checking (a review flagged path traversal in the pak reader), and some tools write to a caller-supplied output path without validating it. Do not point them at untrusted input |
| Machine-wide state | Some helpers rewrite global files such as the engine's per-user `projects.json`, with limited backup, or create scheduled tasks that persist after the session |
| Editor relaunch | Off by default (`CLAUDE_AUTO_RECOVER` is 0). With `CLAUDE_AUTO_RECOVER=1` in the server's environment, a tool that finds the editor gone starts Sandbox.exe, and `wait_for_editor` may replace a crashed one with `taskkill /F`. The optional watchdog (`bridge/bridge_watchdog.py --install`) does the same every 5 minutes from Task Scheduler. Enable either only if you want that |
| State folders | The shared state (deploy lock, "closed on purpose" marker, pause, last-level and log files, the game's trace log) lives in one workspace folder, `_paths.WORKSPACE`: `CRYENGINE_WORKSPACE`, else the older `CRYENGINE_STATE_DIR` / `CLAUDE_STATE_DIR`, else `<repo>/state`. Set only one of them. `CRYENGINE_DEPLOY_LOCK`, `CLAUDE_EDITOR_CLOSED_MARKER` and `CRYENGINE_NPC_LOG` / `CRYENGINE_GAME_LOG` can move single files; if you set them, the watchdog and the tools only agree when they all see the same value |

## Safety limits

The plugin is a local remote-control channel into the editor. In short:

- It listens only on 127.0.0.1 and has **no authentication, no encryption
  and no connection limit**. A request line over 256 MiB is dropped, and a connection whose first
  byte is not `{` (an HTTP request from a browser, for example) is rejected. Any process on the
  machine, under any account that can reach loopback, can send commands.
- The command channel can run arbitrary Python in the editor (`bridge.execute_python`), read and
  write arbitrary files (`bridge.file.*`), run any engine console command (any text that does not
  start with `bridge.` is passed to the console), send synthetic mouse and keyboard input, and close
  the editor.
- The MCP server guards the dangerous tools with `really=true`, `allow_editor_close=true`,
  `overwrite=true` and a deny-list of known-dangerous commands. A review found the deny-list can be
  bypassed, so it is a safety net against mistakes by the model, not a security boundary.
- The plugin's request parser is hand-written, not a general JSON parser; it handles a few escape
  sequences only and turns `\uXXXX` into the bare characters.
- A graceful editor close saves the open level, including scratch objects left in it.

The full model, what is touched outside the project folder, and how to run read-only are in
[safety.md](safety.md).
