# MCP clients

`server.py` is an MCP server that speaks the stdio transport. The client starts it as a child process and talks to it over its standard input and output. The server's name is `cryengine`, so a client that namespaces tools by server shows them as, for example, `mcp__cryengine__ping`.

Before you configure a client, install the Python packages and set up the plugin: [installation.md](installation.md).

## What every configuration needs

| Field | Value |
|---|---|
| command | The Python 3.10+ interpreter that has `mcp` and `numpy` installed. Use the full path to `python.exe` if more than one Python is installed (`where python` lists them). |
| args | One entry: the absolute path to `server.py`. |
| env | At least `CRYENGINE_PROJECT` (your project folder). Add `CRYENGINE_ENGINE` and `CRYENGINE_ENGINE_DIR` (both, same value) if the engine is not in the Launcher's default folder. The full list is in [installation.md](installation.md#environment-variables). |

Use either doubled backslashes (`C:\\tools\\...`) or forward slashes (`C:/tools/...`) in JSON.

## Claude Desktop

Open the configuration file from *Settings > Developer > Edit Config*. It is `claude_desktop_config.json`, normally in `%APPDATA%\Claude\`. Add the server under `mcpServers`:

```json
{
  "mcpServers": {
    "cryengine": {
      "command": "C:\\Python311\\python.exe",
      "args": ["C:\\tools\\cryengine-mcp\\server.py"],
      "env": {
        "CRYENGINE_PROJECT": "C:\\Users\\you\\Documents\\CRYENGINE Projects\\MyGame",
        "CRYENGINE_ENGINE": "C:\\Users\\you\\AppData\\Local\\Programs\\CRYENGINE Launcher\\Engines\\crytek\\cryengine-57-lts\\5.7.1",
        "CRYENGINE_ENGINE_DIR": "C:\\Users\\you\\AppData\\Local\\Programs\\CRYENGINE Launcher\\Engines\\crytek\\cryengine-57-lts\\5.7.1"
      }
    }
  }
}
```

The paths are examples; replace them with yours. Save the file and quit Claude Desktop completely (not just close the window), then start it again. The configuration is read at start.

## Claude Code

Add the server from a terminal. The CLI's own help puts the name first and the options after it, then `--` and the command:

```
claude mcp add cryengine --env CRYENGINE_PROJECT="C:\Users\you\Documents\CRYENGINE Projects\MyGame" -- python "C:\tools\cryengine-mcp\server.py"
```

Everything after `--` is the command and its arguments; `cryengine` is the name you give the server. `--env` takes several values, so keep the name before it (or end the `--env` list with `--`); otherwise the name can be read as another variable. Add one more `--env NAME=value` for each variable.

Quoting on Windows: the paths contain spaces, so each path with a space needs double quotes.

- **PowerShell**: put the whole `NAME=value` in quotes, or the quotes inside the value can be lost when PowerShell passes the argument on:

  ```powershell
  claude mcp add cryengine --env 'CRYENGINE_PROJECT=C:\Users\you\Documents\CRYENGINE Projects\MyGame' -- python 'C:\tools\cryengine-mcp\server.py'
  ```

  Single quotes are literal in PowerShell; the program receives the path with its spaces as one argument.
- **cmd.exe**: single quotes are not quote characters, use double quotes as in the command above.
- Either shell: after adding, run `claude mcp list` (or `claude mcp get cryengine`) and check that the `CRYENGINE_PROJECT` value and the script path show with their spaces intact. How each Claude Code version stores the arguments was not verified here; the `.mcp.json` form below avoids shell quoting altogether. Options such as `--scope` (who sees the server: only you in this project, everyone in this project, or all your projects) differ between Claude Code versions; run `claude mcp add --help` to see yours. `claude mcp list` shows the configured servers and `/mcp` inside a session shows their state.

The same server can be written into a project-level `.mcp.json`:

```json
{
  "mcpServers": {
    "cryengine": {
      "command": "python",
      "args": ["C:/tools/cryengine-mcp/server.py"],
      "env": { "CRYENGINE_PROJECT": "C:/Users/you/Documents/CRYENGINE Projects/MyGame" }
    }
  }
}
```

If `python` on your `PATH` is not the interpreter with `mcp` and `numpy`, use its full path.

## Any other stdio MCP client

The generic form is:

```json
{
  "command": "<path to python.exe>",
  "args": ["<absolute path to server.py>"],
  "env": { "CRYENGINE_PROJECT": "<project folder>" }
}
```

How the three fields are written depends on the client (some use a `servers` key, some a TOML or YAML file). The server needs no command-line arguments and starts with `mcp.run(transport="stdio")`. It resolves its own files relative to where it is installed; setting the working directory to the repository folder is harmless if your client has that option (a working directory is not required by the code, not verified for every module).

You can run `python server.py` by hand to see whether it starts. It then waits silently for MCP messages on standard input; stop it with Ctrl+C. An import error (for example `No module named 'numpy'`) is printed to the console.

## Check the connection

1. In the client, confirm that the `cryengine` server is listed and connected, and that it exposes tools (154 in this checkout; `tool_guide` returns the count as `tools`).
2. Call `server_info`. It does not need the editor. It reports `pid`, `python`, `file` (the `server.py` it runs), `auto_recover`, `started`, `uptime_s`, `stale`, `catalogs` and more. If `file` is not the path you configured, the client started another copy.
3. Call `ping`. This one needs the editor and the plugin: it reads the console variable `r_DisplayInfo` through the bridge and returns `{"name": "r_DisplayInfo", "value": "...", "help": "..."}`. If it fails, the message says what happened:
   - "connection refused": the editor is not running or the plugin did not load;
   - a timeout: the editor is busy or a modal dialog is open (`list_dialogs`).

Then continue with [quickstart.md](quickstart.md).

## Restart after changes

The server imports its modules once, at start. Editing a `.py` file in the repository, or changing an environment variable in the client configuration, has no effect on a running server.

- `server_info` lists `stale`: the modules edited after the server started, and a `restart_note` ("restart the MCP connector to load: ..."). `health_check` reports the same under the item `mcp server`.
- To restart: in Claude Desktop, quit and start it again. In Claude Code, reconnect the server from `/mcp` if your version offers it, or start a new session.
- After `use_project` or `python bridge/project_root.py set`, restart the server too: the active project is read once per process.

Only the MCP server is affected. The editor, and the plugin loaded in it, keep running; a new plugin DLL takes effect when the editor is restarted.

## Running with and without an editor

The server starts and answers without Sandbox. Tools fall into groups according to what they need. The per-tool table, generated from `server.py`, is [tools.md](tools.md) (column *Needs editor*). The groups below are copied from it.

When a tool needs the editor and none is running, it fails with the "connection refused" message above. With `CLAUDE_AUTO_RECOVER=1` in the server's environment, the server instead tries to start `Sandbox.exe` and retries once; this is off by default (see [installation.md](installation.md#server-connection-and-recovery)). It is not harmless: before every start through the watchdog code it replaces the Launcher's `projects.json` with a single entry for the active project (the first original is kept once as `projects.json.mcp-backup`), and it may end a crashed editor with `taskkill /F`. Leave `CLAUDE_AUTO_RECOVER` unset (default `0`) while you first try the tools, and read [safety.md](safety.md) before setting it to `1`.

### Editor-free tools (64)

These work from project files, engine files, the saved manual and the knowledge base. Some write files (see tools.md).

`use_project`, `list_archetypes`, `study_gamedata`, `entity_class_properties`, `project_overview`, `outline_asset_xml`, `plan_character_collider`, `read_asset_file`, `query_knowledge`, `lint_assets`, `job_kinds`, `test_plan`, `start_job`, `job_status`, `read_job_log`, `list_jobs`, `stop_job`, `shoot_game`, `get_shots`, `texture_recipes`, `make_texture`, `write_material`, `build_mesh`, `preview_voice`, `render_voices`, `make_music`, `health_check`, `compare_shots`, `audio_report`, `debug_views`, `gametoken_check`, `snapshot_project`, `list_snapshots`, `diff_snapshot`, `restore_files`, `visual_check`, `visual_status`, `search_manual`, `read_manual_page`, `manual_toc`, `cvar_search`, `cvar_info`, `spec_group`, `editor_api`, `schematyc_search`, `schematyc_info`, `shader_info`, `shader_search`, `asset_info`, `asset_report`, `asset_metadata`, `audio_triggers`, `rc_presets`, `rc_options`, `pbs_check`, `decal_materials`, `gamesdk_audio`, `engine_tests`, `env_variables`, `env_check`, `env_diff`, `server_info`, `read_output`, `tool_guide`

Caveats:

- `shoot_game`, `visual_check` and `start_job` (for the kinds `tests`, `package`, `shader_cache`, `shoot`, `visual`, `cvar_probe`) run or package the game and close an open editor. They refuse unless called with `allow_editor_close=true`.
- Catalog tools read dumps in `engine_data/` (and `editor_api.json` in the repository root), which a fresh clone does not have. They need a one-time capture from a running editor, listed with the tool for each in [First run: capture the catalogs](installation.md#first-run-capture-the-catalogs): `python catalogs/cvar_catalog.py --capture --build` (for `cvar_search`, `cvar_info`, `spec_group`), `python catalogs/schematyc_catalog.py --capture` (for `schematyc_search`, `schematyc_info`), `python bridge/editor_api.py --capture` (for `editor_api`; `--sweep` adds sample values), `python catalogs/object_catalog.py --capture` (for `object_types` with `text`). Until then these tools fail with an error that names the command to run (`editor_api`'s missing-dump message was not checked).
- `editor_panes` lists the saved pane list; with none saved, it asks the editor.
- The manual tools (`search_manual`, `read_manual_page`, `manual_toc`) need a saved copy of the CRYENGINE manual (`CRYENGINE_DOCS`). None is included.
- `shader_info` and `shader_search` read the engine's own shader files (and, for the global flag table, a `globals.txt` the engine writes into a packaged build's `user/shaders/cache/`; whether every answer works without one is not verified). `asset_metadata`, the texture and mesh makers and `rc_options` with `refresh=true` run the engine's `rc.exe`.
- Several tools were written around one game's data and default to its names. Check a tool's arguments before relying on a default; see [limitations.md](limitations.md).

### Tools that need the editor and the plugin (75)

`run_console_command`, `execute_editor_command`, `execute_python`, `load_level`, `goto_position`, `ping`, `take_screenshot`, `get_cvar`, `set_cvar`, `list_entities`, `teleport_entity`, `generate_terrain_height`, `set_terrain_layer_texture`, `list_terrain_layers`, `select_terrain_layer`, `execute_python_script`, `list_objects`, `spawn_archetype`, `create_navigation_area`, `create_ai_path`, `create_nav_seed_point`, `regenerate_navigation`, `set_ai_debug`, `list_windows`, `list_dialogs`, `click_dialog_button`, `export_level`, `create_collision_wall`, `get_entity_physics`, `component_properties`, `sample_entity_physics`, `wait_for_editor`, `place_object`, `delete_objects`, `debug_view`, `create_object`, `object_properties`, `spline_points`, `add_component`, `link_area_target`, `flow_graph_add`, `terrain_hole`, `pfx_check`, `pfx_write`, `pfx_preview`, `render_tour`, `sequence_check`, `nav_reach`, `nav_map`, `mannequin_check`, `bt_check`, `uqs_check`, `level_stats`, `level_validate`, `character_check`, `material_check`, `flow_check`, `anim_check`, `anim_sample`, `cryproject_check`, `drs_check`, `ui_check`, `editor_notify`, `editor_undo`, `editor_panel`, `frame_profile`, `object_check`, `entity_check`, `new_level`, `terrain_shape`, `terrain_height`, `terrain_layers`, `vegetation`, `save_level`, `decal_preview`

Editor-side tools change the **open level in memory**. Nothing is saved until you save the level, but a graceful editor close (and the jobs that close the editor) saves it. Several tools need a level to be loaded; with *Untitled* open they answer with empty data.

### Tools that use the editor only for part of their job (8)

| Tool | Without the editor | With the editor |
|---|---|---|
| `project_info` | Reports the active project and the engine's project list. The `editor` field says the editor is not reachable. | Also asks the editor (see the known gap in [installation.md](installation.md#known-gaps)). |
| `inspect_level`, `study_level` | Read the level's files. | Not needed for the file-side answer. |
| `object_types` | With `text`, searches the saved catalog. | Without `text`, lists the editor's object types. |
| `editor_panes` | Lists the saved pane list. | `refresh=true` asks the editor. |
| `level_check` | Checks the level's saved settings and the exported pak. | Adds live data. |
| `visarea_check` | Checks the level files. | The probes need the editor. |
| `project_audit` | Runs the validators that work offline. | Runs all of them. |

### Tools that read a saved dump (7)

`particle_search`, `particle_info`, `editor_commands`, `mannequin_clips`, `bt_nodes`, `uqs_catalog`, `flow_nodes`

They read a dump under `engine_data/`, which does not exist in a fresh checkout. Produce it once with the editor open ([First run: capture the catalogs](installation.md#first-run-capture-the-catalogs)): `python catalogs/particle_catalog.py --capture`, `python bridge/editor_commands.py --capture`; for `bt_nodes` and `uqs_catalog`, call the tool with `refresh=true`. Afterwards the tools work without the editor. (`mannequin_clips`, `flow_nodes`: the first run needs the editor; the exact trigger was not checked.)

## Output size

A tool result larger than about 20,000 characters is not returned in full. The tool answers with `output_truncated`, an `output_id` and the path of the saved result (in `job_runs/out/` in the repository). Page through it with `read_output(id, key, offset, limit)`.

Work that takes minutes (packaging, tests) is started as a background job: `start_job`, then `job_status` and `read_job_log`. A job runs as a detached process, so it is not tied to one tool call.
