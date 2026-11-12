# ClaudeBridge: operating the loop

**Who this is for.** Maintainers of the editor plugin (`plugin/ClaudeBridgePlugin`). Most users of the
MCP server never need the build/deploy loop or the scheduled tasks described here. Sections 2 to 6
(batch runner, self-test, failure modes, terrain, vegetation, verbs) are useful to anyone who talks to
the plugin directly. Numbers quoted below are observations on CRYENGINE 5.7.1 on one Windows machine.

Companion to [CRYENGINE_BRIDGE.md](CRYENGINE_BRIDGE.md) (the generated editor API reference, which says
what the editor can do). This file says how to drive the editor without a person in the room and records
the failure modes that were expensive to find. The wire protocol is in
[plugin-protocol.md](plugin-protocol.md).

---

## 1. The build / deploy loop (maintainers)

### Prerequisites

- A CRYENGINE 5.7.1 source tree in which the plugin is built (`CRYENGINE_SOURCE`, or the older name
  `CRYENGINE_SOURCE_DIR`; there is no default, and `build`, `deploy` and `cycle` stop with a message while
  it is unset) and MSBuild (`CRYENGINE_MSBUILD`, default a Visual Studio 18 Community path).
  The installed engine folder is `CRYENGINE_ENGINE_DIR`. See [limitations.md](limitations.md) and
  `engine-patches/`.
- Two helper batch files, `claude_deploy.bat` and `claude_rollback.bat`, in the shared workspace folder
  (`CRYENGINE_WORKSPACE`, `<repo>/state` by default; the build and devloop logs go there too). They are author-side scripts that are **not in this repository**; `register` only points
  scheduled tasks at them. Their job, as `bridge/bridge_devloop.py` uses them: the deploy task waits
  for Sandbox to exit, copies the built DLL over the installed one (keeping the old one as
  `ClaudeBridgePlugin.dll.prev`) and relaunches Sandbox; the rollback task kills Sandbox, restores the
  `.prev` DLL and relaunches. Their exact contents are not verified here; write your own.

### Scheduled tasks

`python bridge/bridge_devloop.py register` creates three Task Scheduler entries, each a one-time task
dated 23:59 that exists only to be started with `schtasks /Run`:

| Task | Runs |
|---|---|
| `ClaudeBridgeDeploy` | `cmd /c <workspace>\claude_deploy.bat` |
| `ClaudeBridgeRollback` | `cmd /c <workspace>\claude_rollback.bat` |
| `ClaudeBridgeCycle` | `python bridge/bridge_devloop.py cycle` (the interpreter that ran `register`) |

Remove them with `schtasks /Delete /TN ClaudeBridgeDeploy /F` (and likewise for the other two).
The watchdog (section 7) registers its own task, `ClaudeBridgeWatchdog`.

### Running the loop

```
schtasks /Run /TN ClaudeBridgeCycle      # build -> deploy -> restart -> self-test
```

Output goes to `<CRYENGINE_SOURCE_DIR>\claude_devloop.log`. A cycle is a compile of a few seconds plus
an editor restart; the restart took from about 15 seconds to several minutes depending on the level
loaded, so do not plan around one number.

`bridge/bridge_devloop.py` can be run piecewise:

| Command | Does |
|---|---|
| `build` | msbuild the plugin only (`/p:BuildProjectReferences=false`, so the editor's own projects are NOT rebuilt). Sandbox may stay open. |
| `deploy` | save, close Sandbox (`bridge.window.close`), swap the DLL keeping the old one as `.prev`, relaunch, reload the level (`--level`); verified by SHA-256 |
| `selftest [--destructive]` | run `bridge/bridge_selftest.py` against the live editor |
| `cycle` | all three |
| `rollback` | a plugin that does not load leaves no bridge: kills Sandbox, puts `ClaudeBridgePlugin.dll.prev` back, relaunches (task `ClaudeBridgeRollback`) |
| `register` | (re)create the three scheduled tasks |

### Why scheduled tasks rather than a child process

**Sandbox.exe runs inside a Windows job object.** Every process it spawns inherits that job and is
killed when Sandbox exits. A deploy has to outlive the editor it is restarting, so it cannot be a child
of it. Task Scheduler runs outside the job. A deploy helper started with
`subprocess.Popen(..., DETACHED_PROCESS)` from Sandbox's own Python died with the editor, silently,
leaving the DLL unswapped.

### Why the build does not need Sandbox closed

The vcxproj writes to `<CRYENGINE_SOURCE_DIR>\bin\win_x64\EditorPlugins\`, not to the installed engine.
Only the final copy into `<engine>\bin\win_x64\EditorPlugins\` needs the editor gone.

### The project browser

A fresh Sandbox opens a Project Browser and waits. `startupProject: true` in
`%PROGRAMDATA%\Crytek\CRYENGINE\projects.json` makes `AskUserToSpecifyProject` return immediately
without showing it (`CProjectManager::GetStartupProject`). The watchdog keeps a copy of that file in its
state folder (`claude_projects_startup.json`) and restores it. This edits a file outside the project;
see [safety.md](safety.md).

### The save prompt

Closing a level with unsaved edits raises a modal "save changes?" box, and an unattended run then waits
forever. `deploy` saves first, closes with `bridge.window.close` (WM_CLOSE posted to the main window:
the title bar's X, needing no focus), and if Sandbox is still alive 8, 20 and 40 seconds later answers
any dialog with `bridge.dialog.click default`. Sending Alt+F4 and Return as synthetic keys is not
used any more, because they go to whatever window is in front.

---

## 2. Running a batch of bridge commands

`bridge/bridge_run.py commands.txt` sends one command per line and prints trimmed results. `# ...` is a
comment; `sleep N` waits. A pass of thirteen commands is one round trip instead of thirteen.

**It must be launched detached when it is started from the editor's Python.** Calling it with a
blocking `subprocess.run(...)` from inside Sandbox's Python deadlocks the editor: the interpreter holds
the main thread, and every command the script sends is queued for that same thread, until the
server-side 20-second timeouts drain the list. The script aborts after two consecutive "main thread is
genuinely blocked" errors rather than freezing the editor for list-length x 20 s. A detached child dies
with the editor (section 1), so use this only for work that finishes while the editor runs.

```python
subprocess.Popen([PY, R, LIST], stdout=open(LOG, "wb"),
                 stderr=subprocess.STDOUT,
                 creationflags=0x8 | 0x200)   # DETACHED | NEW_PROCESS_GROUP
```

---

## 3. The self-test

`bridge/bridge_selftest.py` runs about 110 checks across core, window, dialog, python, files, camera,
terrain, objects, entities, env, vegetation, database and capture. It is non-destructive by default;
`--destructive` adds terrain regeneration, vegetation painting and the dialog group (which triggers a
navigation rebuild if the expected modal does not appear). Some checks need the GameSDK sample's
`sdk_player` character. The exit code is the failure count, so it can gate a deploy.

Checks assert **behaviour, not response codes**. Several verbs have reported success while doing
nothing, so `check()` takes a `verify` callback: the camera test reads the position back, the file test
compares bytes, the flare test confirms the exported library actually contains `<Params>`.

Its first run found a product bug: a mistyped `bridge.*` verb fell through to `IConsole`, which logs
"Unknown command" and reports success, so every typo silently succeeded. Unknown `bridge.` verbs are now
refused.

---

## 4. Failure modes worth knowing

### The editor only runs when it is drawing (fixed)
`eNotify_OnIdleUpdate` stops firing when the window is minimised, a menu is open, or a modal dialog is
up. Every queued command then times out, including whichever one would undo the situation. The plugin
now also drains its queue from a **WM_TIMER on a message-only window**, which every message loop
dispatches, nested modal loops included. Minimising the editor no longer stalls queued commands.

### Engine log output is treated as a printf format string (worked around)
CryEngine funnels Python's stdout back through `CryLog`, which takes the text it is handed **as the
format string**. `%s:%d` came back as `:1`; printing anything containing `%n` **crashed the editor**,
because `%n` writes through a pointer that was never passed. `execute_python` now runs the script with
`sys.stdout`/`sys.stderr` redirected into a StringIO that a runner script writes to a temp file, which
the plugin reads back, so script output never reaches the logger. Engine-side messages still come back,
separately, as `engine_log`.

### `IPythonManager::Execute` truncates around 1000 characters
Silently, mid-string, surfacing as a `SyntaxError` about an unterminated literal. The plugin writes the
script to a file and sends a fixed line of about 120 characters that reads it, so script length no
longer matters.

### Multi-line Python
The command line splits on whitespace, so newlines and indentation cannot survive it. Use
`bridge.execute_python.b64 <base64-of-utf8-source>` through `run_console_command`, or the MCP tool
`execute_python_script`, which sends the script that way. The MCP `execute_python` tool goes through
the whitespace-splitting path and is one line only.

### Overwriting a loose asset does nothing while the editor runs
CryPak caches files it has already opened. Rewriting terrain layer BMPs and recompositing produced a
baked `terraintexture.pak` identical to the previous bake: `LoadTexture` reported success and returned
the old pixels. **Write a new filename** (`terrain_grass_v2.bmp`) or restart the editor.

### Rebuilt plugin DLL "did nothing"
The installed DLL is locked while Sandbox runs: close Sandbox, copy the DLL into
`<engine>\bin\win_x64\EditorPlugins\`, restart. Plugins load once at startup; restarting the MCP
connector does nothing for C++ changes. Before deploying, check that the built DLL contains your change
(search its bytes for the new verb name, for example with PowerShell
`Select-String -Path ClaudeBridgePlugin.dll -Pattern 'bridge.myverb' -Encoding ascii`). A build that
compiled unchanged source is a known trap.

### Screenshots catch stale or half-drawn frames
Sandbox renders lazily. A grab straight after a camera move returns the previous frame, or a torn one
with terrain LOD still resolving. Use `bridge.viewport.screenshot settle=2500 <path>`: it nudges the
cursor a pixel at a time to keep frames coming, then captures.

### `general.get_root_path` does not exist
`dir(general)` holds 112 names and that is not one of them, so the call raises

    AttributeError: module 'sandbox.general' has no attribute 'get_root_path'

which the file-based runner catches and returns as a traceback; the editor survives it. The getters that
do exist are `general.get_current_level_path()` and `general.get_current_level_name()`. The general
lesson: a crash observed in the same minute as a command is not evidence that the command caused it.

## 5. Terrain workflow

```
bridge.terrain.get_heights_raw <path>              # 1024x1024 float32, metres
bridge.terrain.set_heights_raw <path> [maxHeight]  # install, with clamp counts
```

The engine's own `GenerateTerrain` cannot produce a specific landscape: it always calls `Normalize()`
and resets max height to `terrainSize/4 + waterLevel`, and its `iCover`/`iSharpness` arguments are
accepted and never used. Design the heightmap elsewhere and install it.

`level/make_heightmap.py` generates a heightmap (warped ridge backbone, ridged multifractal crests,
droplet erosion; its main flow uses 650,000 droplets). The valley pattern is the trace of simulated
water running downhill, not a noise function.

Then texture it:

```
bridge.terrain.select_layer <n>
bridge.terrain.set_layer_texture textures/<file>.bmp
bridge.terrain.set_layer_params <n> <altStart> <altEnd> <slopeMin> <slopeMax>
bridge.terrain.generate_surface_texture [repeat] [altFeather] [slopeFeather]
```

`generate_surface_texture` reports **measured** coverage per layer, not just which layers it
considered. That matters: a layer configured as "slope 26 to 90 degrees" has a band of 0.49 to 573 in
slope-ratio terms, and a feather of 15% of the band ramped it to full weight only at 89 degrees, so it
contributed about 0.0001 everywhere and the terrain baked 100% base layer while reporting the layer as
blended. The feather is capped in natural units and tunable per call.

Choose layer colours against measurements, not by eye. Lighting multiplies the baked colour by a factor
that depends on the level (about 1.75 for the Ashvale example), and darkening all layers uniformly
preserves the average and destroys the contrast. Measure your own level.

Terrain changes are invisible until `general.save_level()` then a reload.

A layer's baked texture is only its DISTANT look. Close up the player sees the layer's **material**,
and a script-created layer starts on `%ENGINE%/EngineAssets/Materials/material_terrain_default`, the
green checkerboard. Nothing in Python or ICommandManager assigns one:

```
bridge.terrain.set_layer_material <index-or-name> <material>
```

## 5b. Vegetation

```
bridge.vegetation.list                                   brushes and their instance counts
bridge.vegetation.instances [objectIndex] [max]          positions, scale, render node present
bridge.vegetation.add <cgf> [density] [size] [sizeVar] [minAlt] [maxAlt] [minSlopeDeg] [maxSlopeDeg]
bridge.vegetation.paint  <index> <x1> <y1> <x2> <y2>     scatter by the brush's rules
bridge.vegetation.clear  <index> <x1> <y1> <x2> <y2>
bridge.vegetation.place  <index> <x> <y> <z>             one instance, exactly there
bridge.vegetation.params <index> <key> <value> ...       size sizevar density bending airadius
                                                         hideable castshadow hidden elev* slope*
```

An instance count is not evidence that anything is visible: each instance keeps the scale it was placed
with, so a brush whose `size` was 0 leaves instances with scale 0 and no render node at all (60 bushes,
none rendered). `bridge.vegetation.instances` is the check; fixing the brush afterwards does not revive
the old instances, so `clear` and `paint` again. Vegetation lives in the level, so `general.save_level`
afterwards, and after planting anything with collision, regenerate the navmesh and **export** it or the
game keeps running on older navigation data.

---

## 6. Verb families

`bridge.camera.*` `bridge.db.*` `bridge.dialog.*` `bridge.entities.*` `bridge.env.*`
`bridge.file.*` `bridge.input.*` `bridge.object.*` `bridge.terrain.*`
`bridge.vegetation.*` `bridge.viewport.*` `bridge.window.*`
`bridge.execute_command` `bridge.execute_python[.b64|.raw]`
`bridge.cvar_get` `bridge.cvar_set`

Out-of-band (run on the TCP thread, work even when the editor is wedged):
`bridge.viewport.screenshot`, `bridge.input.*`, `bridge.window.*`.

`bridge.window.list` enumerates every visible top-level window of the process (titles read with
`InternalGetWindowText`, which sends no message, so it cannot hang on a busy UI thread). It also reports
`activeModal` and a `foreground` object for the REAL foreground window, whoever owns it. A foreign
prompt in front of Sandbox (the Firewall's) is what makes synthetic input go astray.
`bridge.window.close` posts WM_CLOSE.

### Dialogs: answer them by name

- `bridge.dialog.list`: every open dialog.
  - Qt: every visible top-level `QDialog`, or any modal widget.
  - Win32: the `#32770` dialogs, i.e. message boxes and the few MFC prompts left.
  - Each entry gives the title, class, whether it is modal or active, its label texts (the question),
    and its buttons `{text, standard, enabled, default}`. `standard` is locale-free: `yes`, `no`, `ok`,
    `cancel`, `save`, `discard`, and so on.
- `bridge.dialog.click <button> [title substring]`
  - `<button>` is a standard name, `default`, or the button's text with spaces and `&` removed.
  - The target is the active modal. If there is none, the only Win32 dialog; if there is none of those,
    the only dialog. Add part of the title to choose between several.
  - It refuses when the button is missing, ambiguous or disabled, and lists the buttons.
  - The click is `QMetaObject::invokeMethod(button, "click", QueuedConnection)`, exactly what a mouse
    click does, so `CQuestionDialog::Execute()` returns the right StandardButton. `accept()` or `done()`
    would make it return NoButton or Cancel (QuestionDialog.cpp:142-150).
  - The click is queued: a button that opens another modal cannot stall the verb. Confirm with
    `dialog.list`.
- These run on the GUI thread, from their own queue. The WM_TIMER pump drains that queue even while the
  ordinary queue is held by the command that opened the dialog. That command sits inside the dialog's
  nested loop until the dialog closes, and without the separate queue every queued verb timed out
  behind it.
- Test with a real modal: a queued `ai.regenerate_agent_type_all` opens "MNM Navigation Rebuilding
  request" (Yes/No; No does nothing). It is listed with both buttons; `click maybe` is refused with the
  button list; `click no` closes it; the queued command returns.
- Only this process's dialogs are reachable. The Windows Firewall prompt belongs to another process and
  is not something the bridge should answer.

### Synthetic input: target-aware and self-checking

`bridge.input.click|doubleclick|move|drag|scroll [hwnd=<n>|modal|screen] <x> <y> ...`
- **Coordinates.** They default to the main window's top-left, the screenshot's space. `modal` means
  inside the active modal, `hwnd=` means inside that window of ours, and `screen` means physical screen
  pixels.
- **Before a click.** The target is raised without activation. Then the verb works out, from z-order and
  rectangles only, which top-level window the point reaches.
  - It **refuses** when that window is another program's, and names it.
  - It also refuses when the window is one of ours that a modal blocks.
- **Pointer.** The pointer position is read back. A click that would land elsewhere is refused, not sent.
- **DPI.** Everything runs in per-monitor-v2 physical pixels for the thread. Coordinates aim at the
  pixel centre, so they survive either rounding.
- **Replies.** Each reply carries `screen`, `cursor`, `hit` and `foregroundBefore`.

`bridge.input.key|text` sends only when the foreground window is ours: the modal if one is up, else the
main window. Otherwise it answers "keys NOT sent: the foreground window is ..." instead of typing into
another program. Keys carry scan codes, plus the extended flag for arrows, Home/End, PgUp/PgDn, Ins and
Del.

The refusal for a click aimed at another program's window was observed with Notepad over the point
(`hit` = Notepad, `ours: false`). The key-refusal branch has not been exercised: a process started
from the editor did not get the foreground (the foreground lock), so there was no foreign foreground
window to test against.

### Databases
`material`, `particle`, `archetype`, `gametoken`, `flare`. For **flares use
`export_library`/`import_library`, not `export`/`import`**: `CLensFlareItem`'s per-item serializer
writes only Name/Id/Library and omits the optics tree entirely, so a per-item export is an empty shell.
`CLensFlareLibrary::Serialize` does write it, and `IDataBaseLibrary::Serialize` is pure virtual, so it is
reachable without linking Sandbox.exe. (`CEditorImpl::GetDBItemManager` also has no `EDB_TYPE_FLARE`
case at all and returns null; the plugin routes flares through `EditorImpl()->GetLensFlareManager()`.
`EditorImpl()` is `static_cast<CEditorImpl*>(GetIEditor())`. The plugin does not import
`GetIEditorImpl`, which only a locally built Sandbox.exe exports.)

Importing a library **replaces** it: `CBaseLibrary::Serialize(loading)` calls `RemoveAllItems()` first.

## 7. Crash recovery: the watchdog

`bridge/bridge_watchdog.py`, from Task Scheduler every 5 minutes (task `ClaudeBridgeWatchdog`). Install
with `python bridge/bridge_watchdog.py --install`, remove with `--uninstall`, run one cycle by hand with
no arguments. It is opt-in and it starts and kills Sandbox.exe; read [safety.md](safety.md) first.

Why it has to live outside Sandbox: the bridge lives inside Sandbox.exe, so a crash removes the means of
recovery along with the editor.

State: the watchdog keeps `claude_watchdog_state.json`, `claude_watchdog.log`, the deploy lock
(`claude_deploy_in_progress`), `claude_projects_startup.json` and the marker, pause and last-level files
in the shared workspace folder, `_paths.WORKSPACE`: `CRYENGINE_WORKSPACE`, else the older names
`CRYENGINE_STATE_DIR` / `CLAUDE_STATE_DIR`, else `<repo>/state`. `bridge/jobs.py` and the other tools
write the same deploy lock into that same folder, so the watchdog sees a job's lock without further
setup. Only `CRYENGINE_DEPLOY_LOCK` (a full path) and `CLAUDE_EDITOR_CLOSED_MARKER` can point those two
files elsewhere; set them only if you want that.

Settings (environment variables): `CRYENGINE_ENGINE` (engine folder), `CRYENGINE_FALLBACK_LEVEL` (level
asset to reopen when none is remembered; empty by default), `CRYENGINE_ENV_AFTER_RELOAD` (bridge commands
to re-apply after a reopen, separated by `;`; empty by default).

What each cycle checks, in order:

1. The deploy lock (`claude_deploy_in_progress`, under 300 s old) makes it stand down, so the watchdog
   and a deploy never fight over the DLL. A stale lock is ignored rather than disabling the watchdog
   forever.
2. `tasklist` for Sandbox.exe. Absent means launch.
3. `bridge.cvar_get r_DisplayInfo`, a QUEUED command, deliberately. An out-of-band verb answers from the
   TCP thread even while the main thread is wedged, which is exactly the state worth catching. Two
   consecutive failures are needed before `taskkill /F`, so a long level load is not mistaken for a
   hang.

Recovery is three steps, not one: detect and launch (up to one 5-minute tick of latency), wait for the
bridge to answer, then reopen the level and re-apply the environment settings. The log lines for these
are `Sandbox not running -- starting it`, `reopened <level>: ok` and
`re-applied N of M environment settings`.

Restoring the PROCESS is only half the job, and the other half is the half that lies. A relaunched
Sandbox comes up at `Untitled`, where every command answers normally and `bridge.terrain.info` reports
zeros, which is indistinguishable from a wiped level unless you check. The shading preset can also
revert on startup (fog density came back at its default instead of the value that had been set). The
level reopen and `CRYENGINE_ENV_AFTER_RELOAD` put both back.

Auditing it. The healthy path logs nothing, so the heartbeat is the mtime of
`claude_watchdog_state.json`, rewritten every cycle; `claude_watchdog.log` only records events.
`schtasks /Query /TN ClaudeBridgeWatchdog /FO LIST /V` gives `Last Result` (0 is good) and
`Task To Run`. Check that `Task To Run` names a real `python.exe`: `--install` registers the interpreter
that runs it, and installing from inside Sandbox's own interpreter would write `Sandbox.exe` there
instead, because `sys.executable` is Sandbox.exe. That would launch a second editor every five minutes
rather than checking on the first.
