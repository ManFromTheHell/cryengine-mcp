# Quickstart

A walk through the first tool calls, about ten minutes, with a checkpoint after each step. Each checkpoint says what a good result looks like. Statements marked **from the code** can be read in `server.py` or the plugin source. Statements marked **example** show a plausible shape, with values that depend on your project.

Before you start:

- The plugin is installed and the MCP client is configured ([installation.md](installation.md), [mcp-clients.md](mcp-clients.md)).
- Sandbox is running on your project with a level loaded. Steps 2 to 5 and 9 also work without the editor, with the differences noted; steps 1, 6, 7 and 8 need it.
- The tool calls below are written as `tool(argument=value)`. Ask your client to call the tool with those arguments; the client shows the result.

## 1. `ping` - is the bridge answering?

```
ping()
```

Good (**from the code**): a dictionary with the name, the current value and the help text of the console variable `r_DisplayInfo`:

```json
{"name": "r_DisplayInfo", "value": "1", "help": "..."}
```

`value` is whatever your editor has (**example**). Anything in this shape means: the MCP server reached the plugin, the plugin ran a command on the editor's main thread and returned the answer.

If it fails:

| Message | Meaning |
|---|---|
| "could not reach the CryEngine bridge at 127.0.0.1:37778: connection refused ..." | The editor is not running, or the plugin did not load. See [Confirm that it loaded](installation.md#confirm-that-it-loaded). |
| a timeout message that mentions dialogs | A modal dialog is open or the editor is busy. Use `list_dialogs`. |
| the tool does not exist | The client is not connected to the server. See [Check the connection](mcp-clients.md#check-the-connection). |

(The tool's own description says the call needs "an active Play session". That text is from an older design: the plugin answers in edit mode.)

## 2. `project_info` - which project?

```
project_info()
```

Good (**from the code**): a dictionary with these keys:

| Key | Content |
|---|---|
| `active_project` | The folder the file-side tools use. Should be your project. |
| `name` | The folder's name. |
| `chosen_by` | `environment variable CRYENGINE_PROJECT`, `active_project.txt` or `the folder of this server (default)`. |
| `has_cryproject` | `true` if the folder has a `.cryproject` file. |
| `engine_knows` | The projects in the engine's `projects.json` (`name`, `path`, `editor_startup`). Empty if the file cannot be read. |
| `editor` | See the note below. |

If `chosen_by` is the default, or `has_cryproject` is `false`, set `CRYENGINE_PROJECT` in the client configuration and restart the server before going on. See [Project selection](installation.md#project-selection).

Note on `editor`: with no editor running it contains `{"editor": "not reachable: ..."}`. With an editor running, the plugin's verb that this call uses needs an output-file argument that the call does not pass, so the field can hold an error reply even though the editor is up (see [Known gaps](installation.md#known-gaps)). Do not treat that as a connection failure; step 1 is the connection test.

## 2a. Capture the catalogs (once, with the editor open)

A fresh clone has none of the dumps that the lookup tools read, so `cvar_search`, `editor_api`, `object_types(text=...)`, `editor_commands`, `particle_search` and `schematyc_search` fail until you capture them. In a console, from the repository folder, with the Python of your server and the editor on a loaded level (the object catalog creates and deletes about 36 scratch objects, so use a throw-away level):

```
python bridge/editor_api.py --capture
python catalogs/cvar_catalog.py --capture --build
python bridge/editor_commands.py --capture
python catalogs/object_catalog.py --capture
python catalogs/particle_catalog.py --capture
python catalogs/schematyc_catalog.py --capture
```

Each command prints a count or a file name when it is done. The table of what each writes and which tools need it is in [First run: capture the catalogs](installation.md#first-run-capture-the-catalogs). Afterwards, for example, `cvar_search(text="shadow")` returns rows instead of an error naming the command to run.

## 3. `health_check` - can the state be trusted?

```
health_check()
```

Good (**from the code**): a dictionary with `ok`, `failures`, `warnings`, `summary`, a list `items` and `next`. Every item has `status` (`ok`, `info`, `warn` or `fail`), `name`, `detail` and `fix`. `ok` is `false` only when an item has status `fail`. The call reads only; it needs no editor, but probes for one.

Items you can expect:

| Item | With the editor up and a level loaded | Otherwise |
|---|---|---|
| `editor` | `ok`: "Sandbox.exe running, bridge answers, level loaded (terrain max elevation ...)" | `info` if `Sandbox.exe` is not running; `warn` "bridge answers but no level is loaded" if the level has no terrain or none is open; `fail` if `Sandbox.exe` runs but the bridge does not answer |
| `watchdog` | | `info` "scheduled task ClaudeBridgeWatchdog not found or unreadable (optional ...)" on a machine where it was never installed |
| `package release`, `package profile` | | `info` "... (not built yet; optional until you package)" until you have packaged a game |
| `jobs`, `disk` | `ok` | `warn` below 20 GB free |
| `mcp server` | `ok`: started, no module edited since | `warn` if you edited a module after the server started |
| `catalogs` | | `info` while a dump in `engine_data/` is missing; `warn` if a dump is older than the installed build. Capture them: step 2a above |

**Example** of the shape on a fresh setup (the exact list and texts depend on your machine; `...` stands for the remaining items, so the block is not valid JSON):

```json
{"ok": true, "failures": 0, "warnings": 0, "summary": "0 failure(s), 0 warning(s)",
 "items": [{"status": "ok", "name": "editor", "detail": "Sandbox.exe running, bridge answers, level loaded (terrain max elevation 180)", "fix": ""}, ...]}
```

Items about the watchdog and packages are normal here: the watchdog is a separate scheduled task for unattended recovery (see [safety.md](safety.md) before installing it), and packages exist only after you package a game.

## 4. `tool_guide` - the map of the tools

```
tool_guide(family="start")
```

Good (**from the code**): `{"tools": <number>, "families": {...}}`. `tools` is the number of registered tools (154 in this checkout). With `family` set, only the families whose name contains that text are listed; each tool comes with the first sentence of its description. The family `start here` currently holds `tool_guide`, `query_knowledge`, `health_check`, `project_audit`, `wait_for_editor` and `project_overview`. Call `tool_guide()` with no argument for the whole map. A tool no family claims is listed under `unclassified`.

## 5. `query_knowledge` - what is known about a trap?

```
query_knowledge(query="overwriting a texture file while the editor is running", top=2)
```

Good (**from the code**): a list of up to `top` chunks from the knowledge base in `rag/cryengine_project_rag.jsonl`. Each has `id`, `title`, `category`, `score`, `status`, `verified_in`, `date`, `superseded_by`, `verified_note` and `content`. Read `status` and `verified_in` before trusting a chunk: they say how far it was checked. This tool needs no editor. The titles and contents in your result are not shown here, because they come from the corpus (**example**: a trap chunk about the engine caching files it has already opened).

An empty list means no chunk matched; rephrase with the engine's own terms.

## 6. Read-only look at the level

```
list_objects(limit=5)
```

Needs the editor and a loaded level. Good (**from the code**; the block shows the shape: `x, y, z`, `<...>` and `...` are placeholders, so it is not valid JSON):

```json
{"items": [{"name": "...", "type": "...", "pos": [x, y, z], "rotDeg": [...], "scale": [...], "material": "...", "hidden": false, "frozen": false}],
 "shown": 5, "offset": 0, "total": <objects in the level>, "total_is_lower_bound": false, "truncated": false, "next_offset": 5, "note": "..."}
```

`total` is the level's real object count when no filter is given. An empty level gives `items: []`. Use `name_filter` (a case-insensitive substring) on big levels.

Optionally, `level_stats(top=3)` gives the engine's resource report for the level: counts and sizes of geometry and textures, the heaviest textures and meshes. It does not change the level, but it asks the editor to run `level.save_stats`, which writes two report files under `user/testresults/` in the project. Its description mentions numbers measured on one particular game; read your own.

## 7. Create, list, undo and delete an object

This step changes the open level in memory. Nothing is saved to disk unless you save the level or the editor is closed gracefully (which saves it). The steps below remove what they create.

**a. Find a position.** If the level has terrain, ask for the ground height at a point:

```
terrain_height(points=[[512, 512]])
```

Good (**from the code**): `{"heights": [{"x": 512.0, "y": 512.0, "z": <ground height in metres>, "inside": true}]}`. `inside` is `false` when the point is outside the terrain (the height is then clamped to the edge). Use `z` as the third position value below. Without terrain, use the `pos` of an object from step 6.

**b. Create.** Use a name that starts with a letter and has only letters, digits and `_`:

```
place_object(kind="brush", asset="%ENGINE%/EngineAssets/Objects/primitive_cube.cgf", name="mcp_quickstart_cube", position=[512, 512, <z from a>])
```

Good (**from the code**): `{"ok": true, "object": {...as the editor reports it...}, "problems": [], "replaced": false, "saved": false, "note": "..."}`. The call reads the object back after creating it and lists any difference from what you asked in `problems`. If `ok` is `false`, read `problems`. The mesh path is the example in the tool's description; if it is refused as not existing, use a `.cgf` that exists in your project (`asset` must end in `.cgf`).

**c. List it.**

```
list_objects(name_filter="mcp_quickstart")
```

Good: `total` is 1 and the item is named `mcp_quickstart_cube`.

**d. Undo it.** The editor's undo stack also holds your own edits, so the tool undoes a step only if its name contains the text you pass in `expect`.

```
editor_undo(action="info")
editor_undo(action="undo", steps=1, expect="mcp_quickstart_cube")
```

Good (**from the code**): `info` returns `mode`, `undo_steps`, `redo_steps` and `latest` (the newest step names first). The tool's description says `place_object` records a step named `Create new object (<name>)`, so the first entry of `latest` should contain your object's name (**example**). The `undo` call returns the plugin's reply with `took: 1`. If the newest step does not contain `expect`, nothing is undone and the message lists the newest steps.

Repeat the call from **c**: `total` should now be 0.

**e. Delete.** Create the object again (**b**), then:

```
delete_objects(names=["mcp_quickstart_cube"])
delete_objects(names=["mcp_quickstart_cube"], really=1)
```

The first call is a dry run (**from the code**): `{"ok": true, "dry_run": true, "would_delete": 1, "names": ["mcp_quickstart_cube"], "more": 0, "next": "call again with really=1 to delete exactly these"}`. Only a second call with `really` equal to the number of matches deletes. The second call returns `{"ok": true, "deleted": 1, "still_there": [], "saved": false, ...}`. Check with `list_objects(name_filter="mcp_quickstart")` that the level is back to its previous state.

## 8. A screenshot

```
take_screenshot()
```

Good (**from the code**): `{"ok": true}`. The engine's renderer saves the picture; with no `filename` the engine chooses the folder and the name, and the tool does not report them. Look for a new image in the engine's or project's screenshot folder (which one is not verified here).

For a picture of the editor window at a path you choose, call the plugin's window-capture verb through the console tool:

```
run_console_command(command="bridge.viewport.screenshot C:/Temp/mcp_quickstart.bmp")
```

Use a `.bmp` path in a folder that exists (whether the plugin creates the folder was not checked). The reply is a JSON text that includes a `checksum` and a `saturation` value for the captured image. The editor draws lazily; if a picture looks stale or half-drawn, take it again.

## 9. A snapshot

The project has no other undo. Before any bulk change, take a snapshot:

```
snapshot_project(label="quickstart")
```

Good (**from the code**): `{"id": "<timestamp>_quickstart", "files": N, "gigabytes": G, "new_files_stored": N, "new_megabytes_stored": M, "seconds": S, "store": "<folder>", "skipped": [], "unstable": [], "note": "..."}`. The first snapshot stores every file in the project folder except build output, logs, backups and generated folders, so its size is about the size of your project (**example**: gigabytes for a project with many textures). Later snapshots store only files that changed. Check `skipped` (files the editor holds locked) and `unstable` (files that changed while read).

The store is `_snapshots` next to the project folder, on the same disk. Set `CRYENGINE_SNAPSHOT_DIR` to put it on another drive. A same-disk store protects against mistakes, not against a failed drive. `list_snapshots` lists what exists; `diff_snapshot` compares a snapshot with the live files; `restore_files` restores into a side folder by default.

## Where next

- `tool_guide()` for the whole tool map; [tools.md](tools.md) for flags (needs editor, writes files, closes editor) per tool.
- `project_audit` runs the validators in one call and reports what differs from a saved baseline.
- Before using a tool you have not used, run `query_knowledge` on its subject; the knowledge base records traps that make a tool report success without effect.
- [safety.md](safety.md) before enabling auto-recovery, running jobs that close the editor, or installing the watchdog.
