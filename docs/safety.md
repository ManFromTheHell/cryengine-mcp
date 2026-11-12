# Safety and threat model

This project lets an AI assistant drive the CRYENGINE Sandbox editor. That is powerful by design, and the design has no security boundary inside the machine. Read this before installing the plugin.

## 1. The threat model, stated plainly

The `ClaudeBridgePlugin` editor plugin opens a TCP listener on `127.0.0.1:37778`. **Whoever can open a connection to that port controls the editor, and through it, your Windows account.** The protocol (see `plugin-protocol.md`) includes, on purpose:

| Capability | Verb |
|---|---|
| Run arbitrary Python inside the editor | `bridge.execute_python`, `.b64`, `.raw` |
| Run any console command or editor command | any non-`bridge.` text, `bridge.execute_command` |
| Read any file the editor process can read | `bridge.file.read_b64` |
| Write or overwrite any file the editor process can write | `bridge.file.write_b64`, the dump verbs, `bridge.viewport.screenshot`, `bridge.db.export*` and `bridge.terrain.get_heights_raw`, which write to a path you give |
| Close the editor, answer its dialogs, move the mouse, type keys | `bridge.window.close`, `bridge.dialog.click`, `bridge.input.*` |
| Capture the screen contents of a window | `bridge.viewport.screenshot` |

Python in the editor can start programs, so "arbitrary Python" means "run anything as you". Writing a file into your Startup folder is one example of what is reachable. There is no sandbox, no permission prompt and no audit log inside the plugin.

The listener is bound to the loopback address only, so other computers cannot reach it. Nothing else limits who can.

## 2. What the plugin does about it

One protection exists: **the first non-whitespace byte of every connection must be `{`.** Otherwise the connection is answered with an error and closed before anything is parsed.

Why: the protocol parser used to look only for a `"cmd"` string anywhere in a line. A web page could make your browser send an HTTP POST to `http://127.0.0.1:37778/` whose body line was a valid request. The HTTP request line and headers were ignored as unparseable, and the body ran. Browsers write the HTTP request line first, which starts with a letter, so the rule rejects every browser-originated request, including WebSocket handshakes.

## 3. What it does not do

| Gap | Detail |
|---|---|
| No authentication | Any process that connects and sends a line starting with `{` is obeyed. There is no token, password or handshake. |
| Other local users and processes can connect | On a machine with several Windows users (a shared PC, a terminal server) the loopback port is shared by all of them. The listener does not set `SO_EXCLUSIVEADDRUSE`, so a process that sets `SO_REUSEADDR` could also bind the same port and intercept the traffic. |
| No path confinement | Every verb that takes a path accepts any absolute path, including UNC paths. Opening a UNC path makes Windows try to authenticate to that server, which can leak an NTLM hash. `bridge.file.read_b64` loads the whole file into memory with no size limit. |
| Screenshots of other windows | `bridge.viewport.screenshot hwnd=N` only checks that `N` is a window. It does not check that the window belongs to the editor, so it can capture another application's window. Without `hwnd=`, the fallback copies the screen rectangle of the editor window, including any window that overlaps it. It also restores a minimised window. |
| Synthetic input uses the real pointer | `bridge.input.*` uses global `SendInput`. Click, doubleclick, drag and scroll refuse to act on another program's window, and key and text are sent only when the foreground window is the editor's. `bridge.input.move` is not restricted and can move your pointer anywhere. All of them move your real mouse, and so does `bridge.viewport.screenshot` with `settle=` (it jitters the pointer by one pixel). |
| No connection limit | One thread per connection, no receive timeout. A client that connects and stays silent holds a thread. |

The protocol is not an HTTP service, so cross-site tricks other than the HTTP one above do not apply to it. Any program that can open a socket can still send `{`.

## 4. Recommendations

- Use it on a **single-user machine** that you control. Do not install the plugin on a shared or remote-desktop host.
- The Windows firewall is not relevant: the traffic never leaves the machine. Do not "expose" the port by forwarding it, proxying it or binding it to another address, and do not edit the source to listen on `0.0.0.0`.
- Do not run an MCP client that you do not trust against this server. Anything that can call the MCP tools can call `execute_python`.
- Be aware of prompt injection. If the assistant reads untrusted text (a downloaded asset's description, a web page, a file from a stranger) it may be steered into calling these tools. The guards in section 5 do not stop a determined injection; supervise what the assistant is doing, and review file writes.
- Close Sandbox, or unload the plugin (remove `ClaudeBridgePlugin.dll` from `EditorPlugins`), when you are not using the bridge. The port exists only while the editor with the plugin runs.
- Keep your project under a backup or version control. The `snapshot_project` tool is a safety net against mistakes, not a backup (section 5).

## 5. Guards on the server side

These live in the Python MCP server, not in the plugin. They reduce accidents. They do not protect against someone who talks to port 37778 directly, and they are not a sandbox.

| Guard | What it does | Limits |
|---|---|---|
| Danger deny-list (`really=true`) | `execute_python`, `execute_python_script`, `execute_editor_command` and `run_console_command` refuse calls that name editor exit, plugin unload, Lua or file-run commands, modal dialog functions, or (for Python) `os.system`, `subprocess`, `shutil.rmtree`, `os.remove` and similar, unless `really=true` is passed. `run_console_command` also refuses `quit`, `exit` and `bridge.window.close`. | It is a text match. For example `__import__('os').system(...)` is not matched. In `run_console_command` only the `bridge.execute_command`, `bridge.execute_python` and `bridge.execute_python.b64` passthroughs are inspected. `bridge.execute_python.raw`, `bridge.file.*`, `bridge.input.*` and all other verbs are not. Treat it as a speed bump. |
| `overwrite=true` | `make_texture`, `write_material`, `build_mesh`, `pfx_write`, `place_object` and `restore_files` refuse to replace an existing file or object unless it is set. | Covers those tools only. |
| Counted delete | `delete_objects` is a dry run until you pass back the number of matches. It does not save the level. | |
| `allow_editor_close` | Job kinds that close the editor (`tests`, `package`, `shader_cache`, `shoot`, `visual`, `cvar_probe`; all except `package` also launch the game) refuse while `Sandbox.exe` runs unless it is true. The close is a graceful save-and-close that raises `CloseRefused` and does not close when it cannot save and verify the level. | The flag is a parameter the assistant sets. It is meant to make the assistant tell you first. |
| Job whitelist | `start_job` can only start one of a fixed list of kinds, each mapped to a script file of the repository, with arguments validated against fixed names, numbers and character sets. No shell is used and no command string is accepted. See `architecture.md`. | The scripts themselves are trusted code. |
| Snapshots | `snapshot_project` stores a content-addressed copy of the project before bulk changes; `restore_files` brings files back (taking a `before_restore` snapshot first). Nothing deletes a snapshot. | The store is on the same disk by default. It protects against mistakes, not drive failure. |
| No downloads | No tool downloads anything. A tool that would need a download refuses and says what to ask the user. The only code in the folder that uses `urllib` is a standalone script, `catalogs/scrape_cryengine_docs.py`, which no tool calls. | The editor and the engine themselves are not restricted. |
| Output bounding | Tool results over about 20,000 characters are saved to `job_runs/out/` and summarised, so a large reply cannot flood the assistant's context. | Convenience only. |

## 6. What touches files or settings outside the project folder

Verified by searching the code. Most of this is in the maintainer tools (`bridge/bridge_devloop.py`, `bridge/bridge_watchdog.py`, `bridge/safe_close.py`) and in the test and packaging scripts. Read these before running them.

| Action | Where | Notes |
|---|---|---|
| Scheduled tasks created | `bridge/bridge_devloop.py register` creates `ClaudeBridgeDeploy`, `ClaudeBridgeRollback` and `ClaudeBridgeCycle` with `schtasks /Create ... /SC ONCE /ST 23:59 /F`. `bridge/bridge_watchdog.py --install` creates `ClaudeBridgeWatchdog`, run every 5 minutes. `--uninstall` deletes it. | `/F` overwrites a task of the same name. `bridge/bridge_devloop.py` also runs `schtasks /Run` on these tasks. The deploy and rollback tasks run `.bat` files from the engine source folder; those scripts are not part of this repository, so what they do is **not verified**. Nothing creates a task unless you run those commands. |
| Editor launched and killed | `bridge/bridge_watchdog.py` starts `Sandbox.exe`. When the editor stops answering after two probes, it ends it with `taskkill /F /PID` (the PID it launched) or `/IM Sandbox.exe` (every editor) without saving. `bridge/safe_close.py` closes the editor gracefully and only falls back to `taskkill /F` on the PID listening on port 37778, or on `Sandbox.exe` when exactly one is running. `bridge/jobs.py stop` kills a job's process tree and `GameLauncher.exe` by image name. Several test and packaging scripts also kill `Sandbox.exe`, `GameLauncher.exe` or `ShaderCacheGen.exe` by image name. In `examples/ashvale/`, `gameplay_test.py`, `soundscape_test.py` and `ui_menu_test.py` run `taskkill /F /IM Sandbox.exe` and `/IM GameLauncher.exe`, and `night_test.py`, `shader_pixel_test.py` and the three above kill `GameLauncher.exe` the same way, without saving. | These affect any such process on the machine, including ones you started yourself. |
| Automatic editor restart | Off by default: the MCP server sets `CLAUDE_AUTO_RECOVER=0` (with `os.environ.setdefault`, so a value you set wins). If you set `CLAUDE_AUTO_RECOVER=1` in the server's environment, a tool that finds the connection refused (or `wait_for_editor`) runs the watchdog's recovery: it runs the scheduled task `ClaudeBridgeWatchdog` (`schtasks /Run /TN ClaudeBridgeWatchdog`) if that task can be run, otherwise it starts `Sandbox.exe` directly, and it kills a crashed editor (`taskkill /F`) without saving. It stands down while the "closed on purpose" marker exists. | Leave it at `0` unless you want that. |
| `projects.json` rewritten | `project_root.write_projects_json` replaces `%PROGRAMDATA%\Crytek\CRYENGINE\projects.json` (the Launcher's project list) with a single entry that makes the active project the startup project, so Sandbox opens it without the Project Browser. It copies the original once to `projects.json.mcp-backup`. Called by `bridge/editor_open.py`, by the watchdog before a relaunch, by auto-recovery, and by `bridge/project_root.py editor`. | The Launcher's other project entries are not in the file afterwards. Restore from the `.mcp-backup` copy if you want them back. |
| Files written next to the project | Snapshots go to `_snapshots/` in the folder that contains the project folder (`CRYENGINE_SNAPSHOT_DIR` overrides). Packages are built in sibling folders of the project (`bridge/jobs.py` `BUILDS`). | |
| State files | The watchdog, `bridge/safe_close.py`, `bridge/bridge_py.py`, `bridge/jobs.py` and the packaging and example scripts keep their lock, marker, pause, last-level and log files in one workspace folder: `CRYENGINE_WORKSPACE`, else the older `CRYENGINE_STATE_DIR` / `CLAUDE_STATE_DIR`, else `state/` in the repository. `CRYENGINE_DEPLOY_LOCK` and `CLAUDE_EDITOR_CLOSED_MARKER` can point the lock and the marker at other paths. The shader staging folder (`ShaderPak`) and the deploy/rollback batch files are also created under it. | By default every tool agrees on the folder. If you set the single-file variables, set them for every process (the MCP server, the watchdog task, the scripts you run), or the lock and marker are not seen by every tool. |
| Temp files | For every `bridge.execute_python` and `bridge.execute_python.b64` call the plugin writes `claude_bridge_python_in.py`, `claude_bridge_python_run.py` and `claude_bridge_python_out.txt` in `%TEMP%` (the last is deleted after it is read). The names are fixed, so another process of the same user could replace them. | |
| Engine install | `bridge/bridge_devloop.py deploy` saves the open level, then starts the scheduled task `ClaudeBridgeDeploy`, which runs `claude_deploy.bat` from the workspace folder (`state/` by default). `rollback` does the same with `ClaudeBridgeRollback` and `claude_rollback.bat`. | Those `.bat` files are not part of this repository, so what they copy and close is not verified here. Read them before registering the tasks. |
| Not done | No registry edits, no `setx`, no power-plan changes and no display keep-awake request were found in the code. | Searched with `grep` over every `.py` file in this repository. The `.bat` files used by `bridge/bridge_devloop.py` are not in the repository and were not read. |

### Seeing and removing the scheduled tasks

The tasks are created only by the commands in the first row of the table above. To see which exist on this machine (names from `bridge/bridge_devloop.py` and `bridge/bridge_watchdog.py`):

```
schtasks /Query /TN ClaudeBridgeWatchdog
schtasks /Query /TN ClaudeBridgeDeploy
schtasks /Query /TN ClaudeBridgeRollback
schtasks /Query /TN ClaudeBridgeCycle
```

A task that does not exist makes the query report an error. To remove one, `schtasks /Delete /TN <name> /F`; for the watchdog, `python bridge/bridge_watchdog.py --uninstall` does the same. Removing the watchdog task also disables the first step of auto-recovery (it then launches Sandbox directly, if `CLAUDE_AUTO_RECOVER=1`). Check what a task runs before keeping it: `schtasks /Query /TN <name> /V /FO LIST` shows the command line (the "Task To Run" field).

## 7. Known unfixed defects in the plugin

These come from a read-through of the plugin source and were re-checked against it where noted. They are not fixed. The IDs are labels from an internal audit that is not published. D1 (any web page could run code) is the one the first-byte rule fixed, so it is not listed.

| ID | In plain language |
|---|---|
| D2 | Other local users or processes can connect, or bind the same port (section 3). |
| D3 | No path confinement; UNC paths can leak an NTLM hash; reading a file has no size limit. |
| D4 | `viewport.screenshot hwnd=` can capture any window, not just the editor's. |
| D5 | If the plugin is unloaded while a client is connected, that client's thread keeps running with pointers to freed objects. This can crash the editor on shutdown or reload. Sockets are never closed on unload. |
| D6 | Partly addressed: a request line over 256 MiB is dropped. There is still no receive timeout and no connection limit, and if `accept()` keeps failing (for example out of buffers) the accept thread loops without pausing and uses a CPU core. |
| D7 | `\uXXXX` in a request is not decoded (`\u00e9` arrives as `u00e9`), and engine text that is not UTF-8 can make a reply invalid UTF-8. Send raw UTF-8 (`ensure_ascii=False`). |
| D8 | "Out-of-band verbs work when the editor is hung" is only partly true. Screenshot, `window.restore`, `focus` and `minimise` call Win32 functions that wait for the editor's UI thread. If it is truly hung they block forever. There is no timeout on that path. |
| D9 | The guard that stops two drains of the command queue from nesting covers the timer path but not the idle-tick path. A command that opens a modal loop can let the timer run further queued commands inside it. |
| D10 | A command that times out for the client can still run later (or finish running). A client that retries after "timed out" can perform a delete, create or paint twice. |
| D11 | A C++ exception that is not a `std::exception` escapes a client thread and terminates the editor. There is no structured-exception guard around engine calls either. |
| D12 | Some failures look like success: `bridge.file.read_b64` on a missing file returns `ok: true, bytes: 0`; `bridge.execute_python*` returns `ok: true` when the script raised (the traceback is in `log`). |
| D13 | Fixed temp file names for the Python runner (section 6). Two editors, or another process of the same user, can race or tamper with them. |
| D14 | Synthetic input moves the real mouse, and input from two concurrent clients can interleave because there is no lock. (From the audit; not re-checked.) |
| D15 | The log-capture object that collects engine output appends to an unlocked buffer while engine worker threads may log. A data race is possible. Whether the engine's log locking prevents it is not verified. |
| D16 | `bridge.terrain.export_texture` fills a raw byte buffer that it treats as the engine's `SGameExporterSettings` structure because the constructor is not exported. A change to that structure in another engine version would break it silently. |
| D17 | A command with leading whitespace followed by a misspelt `bridge.` verb is passed to the console and reports success (section 3 of `plugin-protocol.md`). |
| D18 | `bridge.input.text` sends each UTF-8 byte as a separate Latin-1 character, so non-ASCII text is typed wrongly. |

Also not verified: whether a plugin built against a locally built `Sandbox` import library loads in the stock Launcher `Sandbox.exe` (ABI and export match).
