# Installation

This page installs the three parts of the system and confirms that they work together.

| Part | What it is | Where it runs |
|---|---|---|
| `ClaudeBridgePlugin` | A C++ editor plugin (`plugin/ClaudeBridgePlugin/`). It listens on `127.0.0.1:37778` and executes requests on the editor's main thread. | Inside `Sandbox.exe` |
| `server.py` | The MCP server (Python, stdio). It registers the tools and talks to the plugin over TCP. It also reads project and engine files directly. | A child process of your MCP client |
| An MCP client | Claude Desktop, Claude Code or any stdio MCP client. See [mcp-clients.md](mcp-clients.md). | Your machine |

Many tools do not need the plugin or a running editor (catalogs, validators, `query_knowledge`, snapshots). If you only want those, you can skip the plugin sections. See [Running with and without an editor](mcp-clients.md#running-with-and-without-an-editor).

## Prerequisites

| Requirement | Needed for | Notes |
|---|---|---|
| Windows 10 or 11, 64-bit | everything | The plugin is a Windows Sandbox plugin. Many modules use Windows paths, `tasklist`, `taskkill` and PowerShell. |
| CRYENGINE 5.7.1 LTS, installed with the CRYENGINE Launcher | everything | You need your own CRYENGINE licence. The tools assume the Launcher's default install folder (see [Environment variables](#environment-variables)). |
| Python 3.10 or newer | the MCP server | `server.py` uses `list[float] \| None` annotations in function signatures, which need 3.10 at import time. |
| The `mcp` Python package | the MCP server | `requirements.txt` asks for `mcp>=1.2.0`. The minimum version is not tested. |
| `numpy` | the MCP server | `server.py` imports `checks/audio_report.py` and `level/world_build.py` at start, and both do `import numpy` at module level. See [Python packages](#python-packages). |
| CRYENGINE 5.7.1 source tree, Visual Studio with the C++ desktop workload and MFC, CMake | only to build the plugin | See [Build the plugin](#build-the-plugin). |

## Get the plugin

This repository contains the plugin source only. No compiled `ClaudeBridgePlugin.dll` is included. The plugin is built inside the CRYENGINE source tree because it links against the `Sandbox` target (for `CTerrainManager`, `CHeightmap`, `CVegetationMap`, `CGameExporter` and `CLensFlareManager`, which Sandbox exports) and uses the editor's headers.

### Build the plugin

1. **Get the engine source.** Check out the CRYENGINE 5.7.1 source (the `release` branch / `5.7.1` tag of the public CRYENGINE repository). Follow the engine's own `README.md` for the build prerequisites (Visual Studio with *Desktop development with C++* and *C++ MFC for build tools*; the SDKs zip; the Autodesk FBX SDK 2016.1 if you want a complete Sandbox build).
2. **Apply the mandatory engine patch.** From the root of the engine tree:

   ```
   git apply "<path to this repository>\engine-patches\0001-build-claude-bridge-plugin.patch"
   ```

   See [Engine patches](#engine-patches) for what it does and what the second patch is for.
3. **Copy the plugin source** so the folder is `Code/Sandbox/Plugins/ClaudeBridgePlugin/` in the engine tree (the patch adds `add_subdirectory("Code/Sandbox/Plugins/ClaudeBridgePlugin")`, so the folder name and location matter):

   ```
   xcopy /E /I "<path to this repository>\plugin\ClaudeBridgePlugin" "<engine tree>\Code\Sandbox\Plugins\ClaudeBridgePlugin"
   ```
4. **Generate the solution with the option on.** Run the engine's CMake step as its README describes (`cry_cmake.exe` in the repository root if you have it, or `Tools\CMake\cmake_create_win64_solution.bat`, which starts `cmake-gui` after the first configure). In the CMake options set:
   - `OPTION_SANDBOX` = ON (the plugin links the `Sandbox` target);
   - `PLUGIN_CLAUDE_BRIDGE` = ON (added by patch 0001; the default is OFF).

   Click *Configure*, then *Generate*. The solution is written to `solutions_cmake/win64/CRYENGINE_Win64.sln` (`.slnx` with the newest Visual Studio generator). The first configure downloads the SDKs zip unless `CRYENGINE_v5.7.0_SDKs.zip` already sits in the engine root.
5. **Build** in Visual Studio, configuration `Profile`, platform `x64`: build the `Sandbox` project first (this builds the editor and its libraries, which produces the `Sandbox.lib` import library the plugin links), then `ClaudeBridgePlugin`. The project file is `solutions_cmake\win64\Code\Sandbox\Plugins\ClaudeBridgePlugin\ClaudeBridgePlugin.vcxproj`.

   Once the dependencies are built, the plugin alone can be rebuilt from a command line:

   ```
   msbuild "solutions_cmake\win64\Code\Sandbox\Plugins\ClaudeBridgePlugin\ClaudeBridgePlugin.vcxproj" /p:Configuration=Profile /p:Platform=x64 /p:BuildProjectReferences=false
   ```
6. **Find the DLL** at `<engine tree>\bin\win_x64\EditorPlugins\ClaudeBridgePlugin.dll`.

`Profile` is the configuration the plugin was built in during development. Which configuration matches your Launcher engine is not verified (see below).

### Engine patches

The files in `engine-patches/` are `git apply` patches for the engine tree.

| Patch | Changes | Needed? |
|---|---|---|
| `0001-build-claude-bridge-plugin.patch` | `Tools/CMake/BuildSandbox.cmake`: adds `option(PLUGIN_CLAUDE_BRIDGE ... OFF)` and `add_subdirectory("Code/Sandbox/Plugins/ClaudeBridgePlugin")` when it is ON. | **Mandatory.** Without it CMake does not know the plugin. Alternative: add the same `add_subdirectory` line to your own CMake files. |
| `0002-optional-cmake4-and-missing-fbx-sdk.patch` | `Tools/CMake/InitialSetup.cmake`: replaces `cmake_policy(SET CMP0040 OLD)` with a guarded `cmake_policy(SET CMP0040 NEW)`. `Tools/CMake/modules/FbxSdk.cmake` and `Code/Sandbox/EditorQt/CMakeLists.txt`: only reference the FBX SDK if `${SDK_DIR}/FbxSdk/include` exists, and print a warning if it does not. | **Optional.** The CMP0040 hunk is only needed with CMake 4.x, where setting the policy to `OLD` is an error; it changes the policy from `OLD` to `NEW`. The FBX hunks make CMake generate, and the `EditorQt` link step skip the FBX SDK, when `${SDK_DIR}/FbxSdk/include` is absent (they also skip imported-library locations that do not exist). Whether Sandbox then compiles and links without the FBX SDK is not verified; `FbxPlugin` and `MeshImporter` still reference it unconditionally. If you install the FBX SDK as the engine README says, you do not need these hunks. |

Neither patch changes engine behaviour at run time.

### What is not verified

- **Whether a plugin built against a locally built Sandbox loads in the stock Launcher `Sandbox.exe`.** The plugin links `Sandbox.lib`, the import library of the `Sandbox.exe` you built, together with the editor libraries built from the same tree. The configuration the plugin was developed against used editor binaries built from the same source. Copying only the DLL into an unmodified Launcher install has not been verified; a mismatch in exports or class layouts could prevent the plugin from loading or could misbehave once loaded. The plugin does not need a patched `GetIEditorImpl` export (it reaches the editor implementation with a `static_cast`), but that is a narrower statement than "it loads in stock Sandbox".
- **The exact Visual Studio, CMake and Qt versions** required. The engine README lists Visual Studio 2017, 2019 or 2022. `cmake_create_solution.bat` in the engine tree only detects installations up to VS 2019 in the registry (from reading the script); with VS 2022 use `cry_cmake.exe` or `cmake-gui` and choose the generator yourself.
- **Which build configuration** (`Profile` or another) matches the Launcher's `Sandbox.exe`.

If you want to test the stock-Sandbox case, copy the DLL as described below, start the editor, and run the check in [Confirm that it loaded](#confirm-that-it-loaded). To undo it, close the editor and delete the DLL.

## Install the plugin DLL

Close Sandbox. Copy the DLL to the editor-plugin folder of the engine you will run:

```
<engine>\bin\win_x64\EditorPlugins\ClaudeBridgePlugin.dll
```

For a Launcher install, `<engine>` is normally:

```
%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1
```

If you replace an existing `ClaudeBridgePlugin.dll`, keep the old one (for example as `ClaudeBridgePlugin.dll.prev`). Sandbox scans `EditorPlugins` at start; there is nothing to register.

If you run an editor you built yourself, the plugin is already in `<engine tree>\bin\win_x64\EditorPlugins\` after the build.

### Which editor to start

The plugin is built against the CRYENGINE **5.7.1** source tree (the same version as the Launcher's `cryengine-57-lts\5.7.1` engine). No `ClaudeBridgePlugin.dll` ships with the Launcher or with this repository: you always have to build one.

| Editor | Start it with | Plugin | Point the tools at it |
|---|---|---|---|
| The one you built | `<engine tree>\bin\win_x64\Sandbox.exe` (a fresh start shows the Project Browser, see [First start](#first-start-and-the-project-browser)) | Already in its `EditorPlugins` folder after the build. | Set `CRYENGINE_ENGINE` and `CRYENGINE_ENGINE_DIR` to `<engine tree>`: the relaunch code (`bridge/bridge_watchdog.py`, `bridge/editor_open.py`) starts `<that folder>\bin\win_x64\Sandbox.exe`. |
| The Launcher's | The Launcher's "open editor" button, or `<Launcher engine>\bin\win_x64\Sandbox.exe` | You copy the DLL in. Whether a DLL built in your tree loads there is not verified, see [What is not verified](#what-is-not-verified). | The default of both variables. |

Sandbox scans the `EditorPlugins` folder at start (not verified: that it is always the one next to the executable that was started).

### Confirm that it loaded

Start Sandbox (see [First start](#first-start-and-the-project-browser)). The plugin writes this line to the editor log when its listener is up:

```
[ClaudeBridgePlugin] Listening on 127.0.0.1:37778 (idle tick + WM_TIMER pump; works outside Play mode, while minimised, and inside modal dialogs)
```

If the port cannot be opened the log contains `[ClaudeBridgePlugin] Failed to start TCP listener on 127.0.0.1:37778`. The location of the editor log depends on your setup and is not covered here; the editor's console/log pane shows the same lines.

A check that does not need the log, in PowerShell:

```powershell
Test-NetConnection 127.0.0.1 -Port 37778
```

`TcpTestSucceeded : True` means something is listening. The definitive check is the `ping` tool from your MCP client (see [quickstart.md](quickstart.md)).

The port is the constant `kBridgePort` in `plugin/ClaudeBridgePlugin/ClaudeBridgePlugin.h`. Changing it needs a rebuild of the plugin, and the Python side must be told with `CRYENGINE_BRIDGE_PORT`. The plugin binds `127.0.0.1` only.

### Security note

The bridge has no authentication. It can run arbitrary Python and console commands inside the editor and read and write arbitrary files as the user who runs the editor, so any program that can connect to the port controls the editor and that user account. The plugin accepts connections on the loopback address only and closes a connection whose first non-whitespace byte is not `{` (this rejects an HTTP request sent by a web page). Run the editor with the bridge only on a machine and account you trust. Details: [safety.md](safety.md).

## Python packages

```
python -m pip install -r requirements.txt
python -m pip install numpy
```

`requirements.txt` lists `mcp>=1.2.0` only. `numpy` is also needed to start the server (see Prerequisites); if `requirements.txt` in your checkout does not list it, install it as above.

Further packages are needed only by parts you do not need for the first steps:

| Package / program | Used by | Notes |
|---|---|---|
| `torch`, `motionbricks` | the `animation/kimodo_*.py` and `animation/motionbricks_*.py` scripts (and `animation/anim_oneshot.py`) | Optional animation pipeline around third-party models. Not needed by the MCP server. |
| `PySide2`, `SandboxBridge` | `bridge/editor_panel.py` | Provided by the editor's own embedded Python (the `editor_panel` tool). Do not pip-install them. |
| `ffmpeg` | picture scaling in the screenshot tools (they return the unscaled PNG without it); required by `audio_report`, `compare_shots`/`visual_check` and the audio makers (`make_music`, `render_voices`) | Must be on `PATH`. Without it those tools raise an error that says so (checked for `audio_report`; not read in detail for the others). |
| `rc.exe` | texture, mesh and `.cryasset` tools | Ships with the engine in `<engine>\Tools\rc\`. |

Tools that need something else (a saved copy of the CRYENGINE manual, the GameSDK sample project, a text-to-speech engine) say so in their description or error text.

## Environment variables

These are all the names read by `os.environ` / `os.getenv` in this repository's Python files. Set the ones you need in the `env` block of your MCP client configuration (see [mcp-clients.md](mcp-clients.md)); scripts you run from a console use the console's environment. The table was produced by scanning the code; to check it against your copy:

```powershell
Get-ChildItem -Recurse -Filter *.py | Select-String -Pattern 'environ|getenv' | Select-Object -ExpandProperty Line
```

No default points at a folder of the original author's machine. Shared state files go to the workspace folder (`state/` in the repository, or `CRYENGINE_WORKSPACE`); the CRYENGINE source tree and the MotionBricks install are located only by their variables (the source tree has no default, MotionBricks defaults to a folder inside the repository). What remains as defaults are standard Windows locations, such as the Launcher's engine folder, the Launcher's `projects.json` in `%PROGRAMDATA%` and a Visual Studio MSBuild path; each has a variable that overrides it.

### Server, connection and recovery

| Name | Meaning | Default |
|---|---|---|
| `CRYENGINE_PROJECT` | The project the file-side tools work on: a project folder, or its `.cryproject` file. See [Project selection](#project-selection). | unset |
| `CRYENGINE_BRIDGE_HOST` | Host the Python side connects to. The plugin itself always binds `127.0.0.1`. | `127.0.0.1` |
| `CRYENGINE_BRIDGE_PORT` | Port the Python side connects to. Must equal `kBridgePort` in the plugin. | `37778` |
| `CLAUDE_AUTO_RECOVER` | `1`: a tool that finds the editor gone (connection refused) calls `bridge_watchdog.recover` and retries once. `recover` asks the scheduled watchdog task to start `Sandbox.exe` (or, if that task cannot be run, launches it itself); a crashed editor may be ended with `taskkill /F`. **Every start through the watchdog code (`launch()`) first rewrites the Launcher's project list:** `restore_startup_project` calls `project_root.write_projects_json`, which replaces `%PROGRAMDATA%\Crytek\CRYENGINE\projects.json` with a single entry, the active project, marked `startupProject`. The first original is copied once to `projects.json.mcp-backup` in the same folder; a later rewrite does not refresh that backup. Read [safety.md](safety.md) before enabling it. Any other value: tools report the error and do nothing. `server.py` sets the default with `os.environ.setdefault`, so a value from your configuration wins. | `0` |
| `CRYENGINE_WORKSPACE` | The workspace folder every tool shares for small state files: the deploy lock `claude_deploy_in_progress`, the "closed on purpose" marker, the watchdog's pause, last-level and log files, the game's trace log `claude_npc.log`, the devloop build log and deploy/rollback batch files, and the shader staging folder `ShaderPak`. Read through `_paths.WORKSPACE`. The folder is created by the tools that write into it. | `<repository>\state`. The older names below are used when this one is unset |
| `CRYENGINE_STATE_DIR`, `CLAUDE_STATE_DIR` | Older names of the same folder, read in this order after `CRYENGINE_WORKSPACE`. Set only one of the three; they no longer mean different folders. `examples/ashvale/door_crossing_test.py` and `examples/ashvale/transition_frames.py` read `CRYENGINE_STATE_DIR` and `examples/ashvale/release_playthrough.py` / `undercroft_playthrough.py` read `CLAUDE_STATE_DIR` directly, falling back to the workspace. | the workspace folder |
| `CLAUDE_EDITOR_CLOSED_MARKER` | Full path of the marker file that means "the editor was closed on purpose; do not relaunch it". Read by `bridge/bridge_py.py` and `bridge/safe_close.py`; both use the same default now. | `<workspace>\claude_editor_closed` |
| `CRYENGINE_DEPLOY_LOCK` | Full path of the deploy lock file, for `packaging/final_packages.py`, `packaging/shader_cache_gen.py`, `examples/ashvale/night_test.py`, `examples/ashvale/ui_screens.py`, `examples/ashvale/package_features_test.py`, `examples/ashvale/shader_tour.py`. `bridge/jobs.py`, `packaging/shader_cache_refresh.py`, `screens/perf_tour.py`, `examples/ashvale/shader_pixel_test.py` and the two playthrough scripts always use `<workspace>\claude_deploy_in_progress`, so if you set this variable the lock files differ for those modules; leave it unset unless you need it. | `<workspace>\claude_deploy_in_progress` |
| `CRYENGINE_FALLBACK_LEVEL` | Level asset (`levels/<name>/<name>.level`) that `bridge/bridge_watchdog.py` reopens after relaunching the editor when no level is remembered. | empty (reopen nothing) |
| `CRYENGINE_ENV_AFTER_RELOAD` | Bridge commands, separated by `;`, that the watchdog re-applies after reopening a level (for example `bridge.env.time 15.0`). | empty |
| `CRYENGINE_PROJECTS_TEMPLATE` | Path of a `projects.json`-format file from which `bridge/project_root.py editor` copies the engine fields when the Launcher's own `projects.json` has no entry. | empty |
| `CRYENGINE_RESTART_WAIT` | Seconds `bridge/editor_suite.py` waits for the editor to come back after a restart. | `600` |

### Engine, source and sample-project locations

| Name | Meaning | Default |
|---|---|---|
| `CRYENGINE_ENGINE` | Installed engine folder (the one containing `bin\win_x64\Sandbox.exe`). | `%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1` |
| `CRYENGINE_ENGINE_DIR` | The same folder under a second name. Different modules read different names: some read only `CRYENGINE_ENGINE` (`bridge/bridge_watchdog.py`, `catalogs/cvar_catalog.py`, `bridge/editor_open.py`, `animation/fbx_to_caf.py`, `packaging/shader_cache_refresh.py`, `catalogs/shader_catalog.py`, `bridge/symbolize.py`), some only `CRYENGINE_ENGINE_DIR` (`bridge/bridge_devloop.py`, `packaging/package_project.py`, `catalogs/rc_catalog.py`, `packaging/shader_cache_gen.py`), the rest either (`CRYENGINE_ENGINE_DIR` first, except `screens/debug_views.py` and `checks/engine_tests.py`). **Set both to the same value if your engine is not in the default folder.** A few modules ignore both variables and use the Launcher 5.7.1 path directly (`bridge/editor_panel.py`, `packaging/package_symbols.py`, `packaging/pak_textures.py`, the RC log default of `packaging/final_packages.py`). | same as above |
| `CRYENGINE_ENGINE_ASSETS` | The engine's `engine` asset folder (read by `level/world_build.py`). | `<CRYENGINE_ENGINE_DIR>\engine` if that is set, else the Launcher default |
| `CRYENGINE_ENGINE_PAK` | Path of `engine.pak` (`screens/grading_chart.py`). | `<engine>\engine\engine.pak` |
| `CRYENGINE_SOURCE` | A CRYENGINE source checkout, read through `_paths.ENGINE_SOURCE` as a reference by the catalog builders (`catalogs/cvar_catalog.py`, `catalogs/particle_catalog.py`, `catalogs/schematyc_catalog.py`, `catalogs/shader_catalog.py`, `checks/bt_check.py`, `catalogs/catalog_stamps.py`), by `bridge/symbolize.py` and `packaging/package_symbols.py` (symbol search), by `bridge/bridge_devloop.py` (build and deploy of the plugin) and by `packaging/snapshot.py` with extras on. When it is unset the source-reading steps are skipped and the catalogs rely on the editor alone; `checks/env_check.py` raises an error asking for it and `bridge/bridge_devloop.py` stops with a message. | unset (empty) |
| `CRYENGINE_GAMESDK_DIR` | `gamesdk` asset folder of the GameSDK sample project (`animation/anim_inventory.py`, `checks/asset_lint.py`, `level/import_gamesdk_asset.py`). | `%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Assets\crytek\cryengine-gamesdk-sample-project\5.7.1\gamesdk` |
| `GAMESDK_ASSETS` | The same folder, read by `server.py` and `level/world_build.py`. | newest `...\cryengine-gamesdk-sample-project\*\gamesdk` under the Launcher's `Assets` folder (`level/world_build.py`: the 5.7.1 path above) |
| `GAMESDK_AUDIO_PAK` | The sample's `audio.pak` (`checks/gamesdk_audio.py`). | `<gamesdk folder above>\audio.pak` |
| `CRYENGINE_DOCS` | Folder with the saved CRYENGINE manual as markdown, for `search_manual`, `read_manual_page`, `manual_toc`, and the output folder of `catalogs/scrape_cryengine_docs.py`. The manual is not part of this repository (that script can build a copy). | `<repository>\cryengine_manual` |
| `CRYENGINE_SHADER_PAKS` | Folder holding a generated `ShaderCache.pak`; only the lint rule L011 uses it and it warns if it is missing. | `<repository>\ShaderPak\Engine` |
| `CRYENGINE_MSBUILD` | `MSBuild.exe` for `bridge/bridge_devloop.py` (a maintainer tool for rebuilding and redeploying the plugin; not needed to use the tools). | a `Visual Studio\18\Community` path |
| `CRYENGINE_SOURCE_DIR` | Older name of `CRYENGINE_SOURCE`, used when that one is unset (read through `_paths.ENGINE_SOURCE`, so it applies to every module above). | unset (empty) |
| `CMAKE_EXE` | `cmake.exe` for `bridge/game_build_locked.py`. | `cmake` from `PATH`, else `C:\Program Files\CMake\bin\cmake.exe` |

### Snapshots

| Name | Meaning | Default |
|---|---|---|
| `CRYENGINE_SNAPSHOT_DIR` | Where `snapshot_project` keeps its store. | `<parent of the project folder>\_snapshots` |
| `CRYENGINE_SNAPSHOT_INCLUDE_EXTRAS` | `1`, `true` or `yes`: also snapshot, when the matching variable is set, the folder `CRYENGINE_SNAPSHOT_DOCS`, the plugin source `Code\Sandbox\Plugins\ClaudeBridgePlugin` under `CRYENGINE_SOURCE`, the folder `CRYENGINE_SNAPSHOT_MEMORY` (an assistant's memory folder: private notes, do not share such a snapshot), and the `git diff` of the `CRYENGINE_SOURCE` repository. With none of those variables set, extras add nothing. Leave it unset unless you want that. | off |
| `CRYENGINE_SNAPSHOT_DOCS` | A notes or corpus folder stored as `docs` in the snapshot when extras are on. No default. | unset |
| `CRYENGINE_SNAPSHOT_MEMORY` | An assistant memory folder stored as `memory` in the snapshot when extras are on. No default. | unset |
| `CRYENGINE_SNAPSHOT_NO_GIT` | Skip the `git diff` part when extras are on. | unset |
| `CRYENGINE_SNAPSHOT_SOURCES` | Test hook: a JSON list of `[prefix, folder, excludes]` that replaces the sources. | unset |

### Packaging, jobs and project-specific defaults

These belong to the packaging/test jobs and to modules that were written for one game. They have no effect on the first-steps tools.

| Name | Meaning | Default |
|---|---|---|
| `CRYENGINE_PACKAGE_DIR` | Folder in which packaged builds are written (`packaging/final_packages.py`, `packaging/release_manifest.py`). | parent of the project folder |
| `CRYENGINE_PACKAGE_PREFIX` | Name prefix of the packaged-build folders (`<prefix>_final_profile`, `<prefix>_final_release`) in `bridge/jobs.py`. | `Ashvale` |
| `CRYENGINE_RC_LOG` | Path of the resource compiler log read by `packaging/final_packages.py`. | `<Launcher engine>\Tools\rc\rc_log.log` |
| `CRYENGINE_SHADER_PAK_DIR` | Shader pak folder used by `packaging/final_packages.py`. | `<CRYENGINE_SHADER_STAGE, else workspace\ShaderPak>\Engine` |
| `CRYENGINE_SHADER_STAGE` | Staging folder of `packaging/shader_cache_refresh.py` (and, through the default above, of `final_packages.py`). An older setup that used `C:\ShaderPak` must set this variable now. | `<workspace>\ShaderPak` |
| `CRYENGINE_RSC_DIR` | Remote Shader Compiler folder for `packaging/shader_cache_refresh.py`. An older setup that used `C:\ShaderCompiler\RemoteShaderCompiler` must set this variable now. | `<engine>\Tools\RemoteShaderCompiler` |
| `CRYENGINE_RECORD_BUILD` | Record-build folder of `packaging/shader_cache_refresh.py`. | `<parent of the project>\Ashvale_shader_record` |
| `GAME_BUILD_LOCK` | Lock file used by `bridge/game_build_locked.py`. | `<project>\user\game_build.lock` |
| `CRYENGINE_MESH_PREFIX` | Mesh path prefix that `level_stats` reports as the project's own meshes. | `objects/ashvale/` |
| `CRYENGINE_PRESET_PREFIX` | Prefix of the environment-preset names `level_check` expects the game to look up. | `ashvale` |
| `CRYENGINE_DIALOGUE` | Path of the dialogue XML (`assets/make_voices.py`, `examples/ashvale/story_check.py`). | `<project>\Assets\libs\ashvale\dialogue.xml` |
| `CRYENGINE_FIRST_LEVEL` | First level name used by `examples/ashvale/story_check.py`. | `ashvale` |
| `CRYENGINE_DEFAULT_LEVEL` | Level opened by `bridge/editor_open.py` / `level/open_level.py` when none is given. | `bridge/editor_open.py`: `ashvale`; `level/open_level.py`: empty |
| `NAV_ORIGIN_OBJECT` | Origin object of `nav_map` when none is given. | `AshvaleStart` |
| `MANNEQUIN_ADB`, `MANNEQUIN_CONTROLLER`, `MANNEQUIN_CHARACTER` | Defaults of `mannequin_check` (asset-relative paths). | `Animations/Mannequin/ADB/Villager.adb`, `Animations/Mannequin/ADB/VillagerControllerDefinition.xml`, `Objects/Characters/Human/sdk_player/sdk_player.cdf` |
| `CRYENGINE_NPC_LOG`, `CRYENGINE_GAME_LOG` | Log files that game test scripts read (`examples/ashvale/ashvale_ci.py`, `examples/ashvale/door_crossing_test.py`, `examples/ashvale/shader_pixel_test.py`, `examples/ashvale/transition_frames.py`, `examples/ashvale/night_test.py`, `examples/ashvale/package_features_test.py`, `examples/ashvale/ui_screens.py`). | `claude_npc.log` in the project folder (`ashvale_ci.py`, `shader_pixel_test.py`) or in the workspace folder (all the others; `CRYENGINE_NPC_LOG` is read by `ashvale_playtest.py`, `shader_tour.py` and the two scripts using `CRYENGINE_STATE_DIR` too) |
| `CRYENGINE_GAME_PORT` | Port of a game-side bridge used by `examples/ashvale/transition_frames.py`. | `37777` |
| `CRYENGINE_TEST_OUT` | Output folder of `examples/ashvale/night_test.py`. | `<parent of the project>\night_test` |
| `ASH_EXTRA_ARGS` | Extra game command-line arguments for `examples/ashvale/loading_screen_test.py` (for example `+gfx_uiaction_log 1`). | empty |
| `ASHVALE_SCRATCH` | Scratch folder of `examples/ashvale/build_ashvale.py` (heightmap). | `<repository>\engine_data` |
| `MOTIONBRICKS_ROOT` | Install and recordings folder of the optional animation pipeline: `clips/`, `work/`, `repo/motionbricks`, venv (`_paths.MOTIONBRICKS_ROOT`, used by every `animation/motionbricks_*.py` and `kimodo_retarget.py`). Not part of this repository and never downloaded. | `<repository>\anim_work\motionbricks` |
| `MOTIONBRICKS_WORK` | Work folder of `animation/kimodo_clips.py` and `animation/motionbricks_validate.py`. | `<MOTIONBRICKS_ROOT>\work` |
| `KIMODO_OUT` | Folder with candidate Kimodo `.npz` files (`animation/kimodo_clips.py`). | `<repository>\anim_work\kimodo_out` |
| `CRYENGINE_CORPUS_MIRROR` | Path of a second copy of the knowledge corpus that `rag/corpus_lib.py sync` also writes (a file inside a docs folder; `rag_query.py` is copied next to it). | empty (no mirror) |

### Tests

| Name | Meaning | Default |
|---|---|---|
| `MCP_TEST_NO_LEVEL_CHANGES` | Any non-empty value: `tests/test_mcp_tools.py` skips the checks that create and delete scratch objects in the open level. | unset |
| `MCP_TEST_LIVE` | `1`: `tests/test_world_build.py` runs its live part (it saves the open level and switches the editor to a scratch level). | unset |
| `CRYENGINE_TEST_SCRATCH_Z` | Height of the scratch boxes in `tests/test_visarea_check.py`. | `300` |
| `BRIDGE_SELFTEST_SCRATCH` | Scratch folder of `bridge/bridge_selftest.py`. | `%TEMP%\bridge_selftest` |
| `CLAUDE_AUTO_RECOVER` | `tests/test_server_support.py`, `tests/test_watchdog_recover.py` and `bridge/tool_smoke.py` set or pass it on; `0` keeps a test from relaunching the editor. | unset |

### Windows variables read

`LOCALAPPDATA` (Launcher paths, `bridge/editor_panel.py`), `PROGRAMDATA` (`projects.json`, default `C:\ProgramData`), `TEMP` (`bridge/editor_api.py`) and `SystemRoot` (`bridge/symbolize.py`).

## Project selection

The file-side tools (asset checks, lints, jobs, snapshots, catalogs) work on one *active project*, chosen once when the server starts by `bridge/project_root.py`, in this order:

1. The environment variable `CRYENGINE_PROJECT`: a project folder, or its `.cryproject` file. If the value is not a folder with a `.cryproject` file it is ignored without an error.
2. The first line of `active_project.txt` in the server folder, written by `python bridge/project_root.py set <path>` or by the `use_project` tool.
3. The parent of the folder that contains `bridge/project_root.py` (the original layout was `<project>\mcp_server\server.py`).

Because rule 3 is almost never what you want in a stand-alone checkout, **set `CRYENGINE_PROJECT` in your client configuration.**

```
python bridge/project_root.py                   # which project is active, and which rule chose it
python bridge/project_root.py set <path>        # write active_project.txt
python bridge/project_root.py clear             # delete active_project.txt
```

The choice is read once per process. After `set`, `clear` or `use_project`, restart the MCP server (see [mcp-clients.md](mcp-clients.md#restart-after-changes)). The `project_info` tool reports the active project, `chosen_by` (which rule applied) and whether the folder has a `.cryproject`.

The editor is separate: it runs one project at a time, and the editor-side tools describe whatever project the open Sandbox has loaded. If the two differ, the file-side tools describe one project and the editor-side tools another. `project_info` is meant to flag that (`editor_matches_active_project`); see [Known gaps](#known-gaps) below.

## First start and the Project Browser

Start Sandbox on the project you set in `CRYENGINE_PROJECT`.

- **From the Launcher** (open the project's editor), Sandbox starts on that project.
- **From `Sandbox.exe` directly**, a fresh start shows the *Project Browser* and waits for you to pick a project. An unattended start cannot get past it.

The Project Browser is skipped when the engine's project list marks a project as the startup project. That list is `C:\ProgramData\Crytek\CRYENGINE\projects.json`: an entry with `"startupProject": true` makes `CProjectManager::GetStartupProject` return that project's `.cryproject` path, and Sandbox opens it without showing the browser. `python bridge/project_root.py editor` writes such a file for the active project. **It replaces the whole project list with a single entry** (it copies the engine fields from the first existing entry, or from the file named by `CRYENGINE_PROJECTS_TEMPLATE`, and keeps the first original as `projects.json.mcp-backup`). If you use the Launcher for several projects, edit `projects.json` by hand instead, or pick the project in the Project Browser.

After the editor is up with a level loaded, run `ping` and `health_check` (see [quickstart.md](quickstart.md)).

## First run: capture the catalogs

Several tools that work without an editor read a dump of the editor's own data. A fresh clone has none (`engine_data/` holds only `.gitkeep`, and `editor_api.json` is not committed), so those tools fail until you capture the dump once. Do it after the first successful `ping`, with the editor open, from the repository folder, using the same Python as the server:

```
python bridge/editor_api.py --capture
python catalogs/cvar_catalog.py --capture --build
python bridge/editor_commands.py --capture
python catalogs/object_catalog.py --capture
python catalogs/particle_catalog.py --capture
python catalogs/schematyc_catalog.py --capture
```

| Command | Writes | Tools that need it |
|---|---|---|
| `bridge/editor_api.py --capture` | `editor_api.json` (repository root) | `editor_api` |
| `bridge/editor_api.py --sweep` (optional, after `--capture`) | `engine_data/editor_api_samples.json` | the `sample` values in `editor_api` results |
| `catalogs/cvar_catalog.py --capture --build` | raw dumps and `engine_data/cvars.json` | `cvar_search`, `cvar_info`, `spec_group` |
| `bridge/editor_commands.py --capture` | `engine_data/editor_commands.json` | `editor_commands` |
| `catalogs/object_catalog.py --capture` | `engine_data/object_catalog.json` | `object_types` with `text` (without `text` it asks the editor) |
| `catalogs/particle_catalog.py --capture` | `engine_data/particle_features.json`, `engine_data/particle_modifiers.json`, `engine_data/particle_aliases.json` | `particle_search`, `particle_info` |
| `catalogs/schematyc_catalog.py --capture` | `engine_data/schematyc_env.json` | `schematyc_search`, `schematyc_info` |

Notes:

- Each command prints a count when it is done; the missing-dump errors of the tools name the command to run. `bt_nodes` and `uqs_catalog` are captured by calling the tool with `refresh=true` (see [mcp-clients.md](mcp-clients.md#running-with-and-without-an-editor)).
- `object_catalog.py --capture` creates and deletes about 36 scratch objects in the open level (about one minute) and checks the object count at the end. Run it on a throw-away level, because a graceful editor close saves the open level.
- `cvar_catalog.py --build` also scans a CRYENGINE source checkout when `CRYENGINE_SOURCE` points at one; without it the source-derived columns (registration file and line, default value) are skipped.
- `editor_panes` has no capture script: with no saved list, or with `refresh=true`, it asks the editor and saves the list to `engine_data/editor_panes.json`.
- A catalog describes the engine build that was running when you captured it; `python catalogs/catalog_stamps.py` compares each catalog with the installed build. Capture again after switching to another engine build.

Behaviour that surprises first-time users:

- **A restarted editor opens at *Untitled*.** The bridge then answers every command, but terrain reads as empty, which looks like a wiped level. `health_check` reports "bridge answers but no level is loaded".
- **Closing a level with unsaved edits raises a modal "save changes?" dialog**, and the editor then waits for you. `list_dialogs` and `click_dialog_button` can answer it from the MCP client.
- **A graceful editor close saves the open level.** Jobs that close the editor (packaging, tests, shader cache) refuse to run unless called with `allow_editor_close=true`.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| `ping` fails with "could not reach the CryEngine bridge ... connection refused" | The editor is not running, or the plugin did not load. | Start the editor; check the log for the `Listening on` line and `Test-NetConnection` as above. |
| `ping` times out | A modal dialog is open, or the editor is busy loading. | `list_dialogs`, then `click_dialog_button`; wait for the level to load. |
| The plugin DLL is in place but nothing listens | Plugin built against different editor binaries than the one that loaded it (see [What is not verified](#what-is-not-verified)), or another program owns port 37778. | Check the editor log; delete the DLL to go back to a plain editor. |
| `ModuleNotFoundError: numpy` (or `mcp`) when the client starts the server | The client launched a Python that does not have the packages. | Use the full path of the `python.exe` where you ran `pip install`, in the client configuration. |
| Tools report the wrong project | `CRYENGINE_PROJECT` unset or invalid. | Call `project_info` and read `chosen_by`. |
| `cvar_search`, `editor_api`, `particle_search`, `schematyc_search` and similar fail with "no catalog" or "no dump" | The one-time capture was not run. | [First run: capture the catalogs](#first-run-capture-the-catalogs). |

More symptoms and fixes, each tied to a knowledge-base entry, are in [troubleshooting.md](troubleshooting.md).

## Known gaps

- Other limits of this early release are in [limitations.md](limitations.md).
