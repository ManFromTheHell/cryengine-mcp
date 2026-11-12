# ClaudeBridge wire protocol

This is the protocol spoken by `ClaudeBridgePlugin`, the Sandbox editor plugin in `plugin/ClaudeBridgePlugin/`, on `127.0.0.1:37778`. The MCP server uses it, and so can any other client. It is read from `TcpCommandServer.cpp` (transport) and `ClaudeBridgePlugin.cpp` (verbs) for CRYENGINE 5.7.1.

Read `safety.md` first: the protocol has no authentication, and several verbs run arbitrary code or write arbitrary files.

A runnable stdlib-only client is in [`examples/bridge_client.py`](examples/bridge_client.py).

## 1. Transport

| Item | Value |
|---|---|
| Transport | Raw TCP, no TLS, no HTTP |
| Address | `127.0.0.1` only (hard-coded `inet_pton("127.0.0.1")`). Not reachable from other machines. |
| Port | `37778`, a compile-time constant (`kBridgePort` in `ClaudeBridgePlugin.h`). Not configurable at run time. `server.py`, `bridge/bridge_py.py` and `bridge/editor_open.py` read `CRYENGINE_BRIDGE_PORT` (`server.py` and `bridge/bridge_py.py` also read `CRYENGINE_BRIDGE_HOST`) so they can match a plugin you rebuilt with another port. The watchdog, `bridge/safe_close.py` and the maintainer scripts in `bridge/` hard-code 37778, so another port is not supported for those. |
| Framing | One JSON object per line, terminated by `\n`. Empty lines are skipped. |
| Direction | Strict request/response: each request line gets exactly one reply line, in order, on the same connection. Several requests may be sent on one connection; they are handled one after another. |
| Concurrency | One accept thread, one detached thread per connection. Different connections run concurrently, but anything that touches the engine is queued onto the editor's main thread (see section 5). |
| Line limit | A request line larger than 256 MiB without a newline makes the server drop the connection. This is the only size limit. |
| Server-side timeouts | None on the socket. The client must set its own timeout. |
| Encoding | UTF-8 in practice. See section 4 for the escape handling. |

The plugin logs `[ClaudeBridgePlugin] Listening on 127.0.0.1:37778 ...` to the editor log when it starts. If the port is taken, it logs an error and no bridge exists.

## 2. The first-byte rule

A web page can make a browser open a TCP connection to `127.0.0.1:37778` and send text. The browser always starts with an HTTP request line (`POST /...`, `GET /...`, `OPTIONS ...`).

The server therefore requires the first non-whitespace byte of a connection to be `{`. Whitespace skipped here is space, tab, CR and LF. If it is anything else, the server sends this one line and closes the connection without parsing or executing anything:

```
{"id": null, "ok": false, "error": "not a bridge request (expected a JSON object per line)"}
```

The check applies to the first line of each connection only. Later lines on the same connection are not checked this way. This blocks browser-originated requests; it does not authenticate anything (see `safety.md`).

## 3. Request

```
{"id": 7, "cmd": "bridge.cvar_get sys_spec"}\n
```

| Field | Meaning |
|---|---|
| `cmd` | Required string. The command line. |
| `id` | Optional integer. Echoed back verbatim in the reply. |

The server does **not** contain a JSON parser. It finds the substring `"cmd"`, the next `:`, the next `"`, and reads up to the next unescaped `"`. Consequences:

- Send a flat, compact object with only these two keys, `id` first, as `json.dumps({"id": n, "cmd": c})` does.
- Escapes understood in `cmd`: `\n \t \r \" \\`. Any other escape, including `\uXXXX`, is replaced by the bare character after the backslash (`é` arrives as `u00e9`). Python's default `json.dumps` writes non-ASCII as `\uXXXX`, so pass `ensure_ascii=False` when a command contains non-ASCII text. The MCP server's own sender does not set it, so non-ASCII characters in a command typed through a tool are mangled; use `bridge.execute_python.b64` (base64 is ASCII) for non-ASCII code or paths. Raw UTF-8 bytes pass through the parser unchanged. Whether each individual verb then handles non-ASCII paths correctly is not verified.
- `id` is read as the run of digits and `-` after `"id":`. A missing, string or otherwise non-numeric id is echoed as `null`. A float such as `1.5` is echoed as `1`.
- A line with no `"cmd"` string gets the reply `{"id": null, "ok": false, "error": "could not parse request"}`.

### How `cmd` is dispatched

1. The first whitespace-delimited token is the **verb**. Arguments are the following tokens, split on whitespace. Verbs that take a path or free text rejoin the remaining tokens with single spaces, so runs of spaces collapse. Names containing spaces cannot be passed to most verbs.
2. A verb starting with `bridge.` is a plugin verb (catalogue in section 6). An unknown `bridge.` verb is an error: `unknown bridge verb: <verb> (families: ...)`.
3. Anything that does not start with `bridge.` is run as a raw engine console command (`IConsole::ExecuteString`). The reply `output` is the console/log text captured while it ran, as plain text (not JSON). An unknown console command does not fail: it logs "Unknown command" and the reply is `ok: true`.

The `bridge.` prefix test does not skip leading whitespace, although tokenising does. A command with leading whitespace before a misspelt `bridge.` verb therefore falls through to the console and "succeeds" silently. Send commands without leading whitespace.

## 4. Response

Exactly one line.

| Case | Reply |
|---|---|
| Success | `{"id": N, "ok": true, "output": "<string>"}` |
| The handler threw (bad arguments, timeout, engine refusal) | `{"id": N, "ok": false, "error": "<message>"}` |
| Request had no `cmd` | `{"id": null, "ok": false, "error": "could not parse request"}` |
| First byte was not `{` | see section 2; the connection is then closed |

### Double encoding of `output`

`output` is always a JSON **string**. For every `bridge.*` verb, the content of that string is itself JSON text (an object or array). A client needs two `json.loads` calls:

```python
reply = json.loads(line)               # layer 1: the envelope
payload = json.loads(reply["output"])  # layer 2: the verb's own JSON
```

For raw console commands, `output` is plain text and the second `json.loads` must not be applied.

`ok: true` on the envelope only means the command was dispatched without an exception. Many verbs report their own outcome inside the payload (`"ok": false`, a `log` field, counts). `bridge.execute_python*` returns `ok: true` even when the script raised: the traceback is in the payload's `log`.

Escaping on output: `"` `\` newline, CR and tab are escaped, other control characters below 0x20 become `\u00xx`, and all other bytes (including non-ASCII) are copied as they are. If the engine produced non-UTF-8 text, the reply line is not valid UTF-8; decode with `errors="replace"`.

### Typical error messages

| Message | Meaning |
|---|---|
| `timed out waiting for the editor main thread to run this command (both the idle tick and the WM_TIMER pump failed to drain the queue -- the main thread is genuinely blocked, not merely not drawing)` | The 20 s queue wait expired. A modal dialog or a long command is blocking the editor. The command may still run later (see section 5). |
| `timed out: the editor main thread ran no message loop for 10 s -- ...` | Same, for `bridge.dialog.*` (10 s). |
| `unknown bridge verb: ...` | Misspelt verb. |
| `unknown cvar: <name>` | `bridge.cvar_get` / `bridge.cvar_set` on a name the console does not have. |

## 5. Which thread runs a command

| Class | Verbs | Runs on | Wait limit | Works while the editor is blocked? |
|---|---|---|---|---|
| Out-of-band | `bridge.viewport.screenshot`, `bridge.window.*`, `bridge.input.*` | The client's own TCP thread, using Win32 calls only. Never queued. | none | Mostly. They do not wait for the main thread, but several of them call Win32 functions (`ShowWindow`, `PrintWindow`) that send messages to the editor's window and can block if its UI thread is hung. |
| Dialog queue | `bridge.dialog.*` | The editor's main thread, from a separate queue drained by a 25 ms `WM_TIMER`. | 10 s | Yes while a modal dialog is open (its loop runs the timer). No if the main thread is busy in a long command or hung: the verb then times out after 10 s. |
| Main queue | every other verb, and raw console commands | The editor's main thread. | 20 s | No. |

Main queue details:

- The queue is drained from two places: the editor's `eNotify_OnIdleUpdate` and a 25 ms `WM_TIMER` on a hidden message-only window (`ClaudeBridgePumpWindow`). The timer runs inside every nested message loop, so queued commands complete while the editor is minimised or a menu is open.
- A drain takes the whole queue and runs the commands in order. If one command opens a modal dialog, the commands already taken wait until that command returns.
- The 20 s limit covers waiting **and** running. A command that runs longer returns the timeout error to the client while it keeps running in the editor. A command that timed out before it started still runs later. A client that retries after a timeout can therefore run a delete, create or paint twice. Check the editor state first.
- Do not call the bridge from the editor's own Python interpreter with a blocking socket: the interpreter occupies the main thread that would answer.

## 6. Verb catalogue

Verified by grepping `ClaudeBridgePlugin.cpp`: every verb below has a handler. Argument forms come from the comments above each handler. `<angle>` is required, `[square]` is optional. "File arg" means the verb writes or reads a file at a path you give, with no path restriction.

### Raw execution

| Verb | Arguments | Notes |
|---|---|---|
| `bridge.execute_python` | `<one line of Python>` | Runs in the editor's embedded interpreter with Sandbox modules (`general`, `object`, `level`, `terrain`, ...) in scope. Whitespace runs collapse, so only single-line code survives. Stdout is captured. Payload: `{ok, log, engine_log, sent_chars}`. |
| `bridge.execute_python.b64` | `<base64 of UTF-8 source>` | Same, multi-line source intact. |
| `bridge.execute_python.raw` | `<one line>` | Passed to the interpreter without the capture wrapper. Python output goes to the engine log, which treats text as a printf format string; do not print `%`. |
| `bridge.execute_command` | `<module.command args...>` | Sandbox's `ICommandManager` (see `architecture.md`). Payload `{ok, log}`; the command itself does not report success, read `log`. |
| (any text not starting with `bridge.`) | console line | `IConsole`: CVars and true console commands. |
| `bridge.cvar_get` | `<name>` | Payload `{name, value, help}`. Read-only. |
| `bridge.cvar_set` | `<name> <value>` | Payload `{name, value}` read back. |
| `bridge.screenshot` | `[file]` | `IRenderer::ScreenShot`. Returns an error when the renderer returns false (it does in edit mode, per `CRYENGINE_BRIDGE.md`; use `bridge.viewport.screenshot`). |

### Out-of-band (client thread)

| Verb | Arguments | Notes |
|---|---|---|
| `bridge.viewport.screenshot` | `[hwnd=N] [settle=ms] <path.bmp>` | Captures the editor window (or the window `N`, any window; see `safety.md`). Options come before the path. `settle` is clamped to 0..10000 ms; while it waits, the plugin jitters the real mouse pointer by one pixel (`SendInput`) so the editor redraws. The image is written to the path you give, with no path restriction. |
| `bridge.window.info` | none | Main window title, minimised/foreground state, rectangles. |
| `bridge.window.list` | none | Every visible top-level window of the editor process, the active modal, and the real foreground window. |
| `bridge.window.restore`, `.focus` | none | Restore if minimised, bring to front. |
| `bridge.window.minimise` | none | `minimize` is also accepted. |
| `bridge.window.close` | none | Posts `WM_CLOSE` to the main window, like the title bar's X. An unsaved level raises a save prompt; answer it with `bridge.dialog.click`. |
| `bridge.input.click`, `.doubleclick`, `.move`, `.drag`, `.scroll` | `[hwnd=N\|modal\|screen] <x> <y> ...` | Real `SendInput` mouse events, which move the user's pointer. `click`, `doubleclick`, `drag` and `scroll` refuse when the point would land on another program's window or on a window blocked by a modal; `move` is not restricted. |
| `bridge.input.key` | `<name> [ctrl] [shift] [alt]` | Sent only if the foreground window belongs to the editor. |
| `bridge.input.text` | `<text>` | Same rule. Non-ASCII text is mangled. |

### Dialogs (main thread, own queue)

| Verb | Arguments | Notes |
|---|---|---|
| `bridge.dialog.list` | none | Open Qt and Win32 dialogs: title, class, modal/active, label texts, buttons `{text, standard, enabled, default}`. Read-only. |
| `bridge.dialog.click` | `<button> [title substring]` | `<button>` is a standard name (`yes`, `no`, `ok`, `cancel`, `save`, `discard`...), `default`, or the button text with spaces and `&` removed. Refuses if missing, ambiguous or disabled. The click is queued, like a mouse click. |

### Files

| Verb | Arguments | Notes |
|---|---|---|
| `bridge.file.read_b64` | `<path>` | Reads the whole file into memory. A missing or unreadable file gives `ok: true, bytes: 0`. |
| `bridge.file.write_b64` | `<base64> <path>` | Writes, reads back, and reports `{written, verified, match}`. Overwrites without asking. |

### Terrain

`bridge.terrain.` + `info`, `list_layers`, `select_layer <i|name>`, `delete_layer <i|name>`, `set_layer_texture <path>`, `set_layer_material <i|name> <material>`, `set_layer_params <i|name> <altStart> <altEnd> <slopeMin> <slopeMax>`, `generate_height [passes] [freq] [freqStep] [fade] [cover] [seed] [sharp] [smooth]`, `generate_surface_texture [repeat]`, `export_texture [resolution] [colorMultiplier]`, `get_heights_raw <path>`, `set_heights_raw <path> [maxHeight]`, `hole <x> <y> <w> <h> <1|0>`, `is_hole <x> <y>`.

`generate_height` replaces the heightmap. `set_heights_raw` replaces it from a float32 file. Nothing is saved until the level is saved.

### Vegetation

`bridge.vegetation.` + `list`, `add <cgf> [density] [size] [sizeVar] [minAlt] [maxAlt] [minSlopeDeg] [maxSlopeDeg]`, `paint <index> <x1> <y1> <x2> <y2>`, `clear <index> <x1> <y1> <x2> <y2>`, `place <index> <x> <y> <z>`, `instances [index] [max]`, `params <index> <key> <value> ...`.

### Objects, entities, shapes, undo

| Verb | Arguments |
|---|---|
| `bridge.object.list` | `[nameFilter]` |
| `bridge.object.create` | `<typeName> <x> <y> <z> [name] [geometryFile]` |
| `bridge.object.transform` | `<name> <x> <y> <z> [rotX rotY rotZ] [scaleX scaleY scaleZ]` |
| `bridge.object.material` | `<name> <material>` |
| `bridge.object.delete`, `.select` | `<name>` |
| `bridge.object.types` | `[categoryFilter]` |
| `bridge.object.xml` | `<name>`: serialise one object to XML |
| `bridge.object.set` | `<name> <base64 of "key=value" lines>` |
| `bridge.entities.list` | `[filter]` (capped at 200 entries) |
| `bridge.entities.physics` | `<name>` |
| `bridge.entities.teleport` | `<name> <x> <y> <z>` |
| `bridge.entity.add_component` | `<entity> <component GUID, class name or label>` |
| `bridge.component.properties` | `<entity> <component> [json]`. Send the complete property set when writing; omitted string members are reset. |
| `bridge.nav.query` | `<agentType\|-> <base64 of lines "label x1 y1 z1 x2 y2 z2">`: navmesh path queries; the result is in the payload, no file is written |
| `bridge.shape.add_points` | `<object> <agentType\|-> <x> <y> <z> [...]` |
| `bridge.undo.mode` | `[on\|off]`: whether object verbs record editor undo steps (default off) |
| `bridge.undo.info` | none |
| `bridge.undo.step` | `<undo\|redo> [n]` (1..50) |
| `bridge.notify` | `<info\|warning\|critical> <title> \| <message>` |

### Camera and environment

`bridge.camera.get`, `bridge.camera.set <x> <y> <z> [yawDeg] [pitchDeg]`, `bridge.camera.look_at <ex> <ey> <ez> <tx> <ty> <tz>`.

`bridge.env.` + `list [filter]`, `set <name|index> <v0> [v1] [v2]`, `time [hour]`, `presets`, `load <path.env>`, `default <preset name>`, `current <preset name>`, `remove <preset name>`, `save [preset name]`, `new_asset <path.env>`, `getkeys <variable>`, `keys <variable> <h:v[/v/v],...> [smooth|linear|step|zero]`. Time-of-day presets and variables.

### Databases

`bridge.db.types`, then `bridge.db.<action> <type> ...` for the types `material`, `particle`, `archetype`, `gametoken`, `flare` and the actions `libraries`, `items`, `load_library`, `save_library`, `add_library`, `create`, `export`, `import`, `delete`, `export_library`, `import_library`. For flares use the `_library` pair. `import_library` replaces the whole library.

### Engine introspection dumps

Each takes an output path (some also a second argument after `|`), writes JSON there, and replies with the path and counts. The path is not restricted.

`bridge.schematyc.dump`, `bridge.commands.dump`, `bridge.particles.features`, `bridge.particles.roundtrip`, `bridge.sequence.roundtrip`, `bridge.mannequin.clips`, `bridge.mannequin.check`, `bridge.character.check`, `bridge.character.names`, `bridge.character.bindpose`, `bridge.character.samplepose`, `bridge.material.check`, `bridge.drs.dump`, `bridge.ui.dump`, `bridge.visarea.dump`, `bridge.project.info`, `bridge.flow.catalog`, `bridge.flow.check`, `bridge.uqs.catalog`, `bridge.uqs.check`, `bridge.bt.nodes`, `bridge.bt.load`.

Arguments for each are in the comment above its handler in `ClaudeBridgePlugin.cpp`. `bridge.uqs.check` can delete only files whose names start with `zz_mcp_`.

## 7. Transcripts

Values in replies are illustrative: they depend on your editor. Lines starting `>` are what the client sends, `<` what it receives.

**A harmless read** (`examples/bridge_client.py` sends this):

```
> {"id": 1, "cmd": "bridge.cvar_get sys_spec"}
< {"id": 1, "ok": true, "output": "{\"name\": \"sys_spec\", \"value\": \"4\", \"help\": \"...\"}"}
```

After the second decode: `{"name": "sys_spec", "value": "4", "help": "..."}`.

**Unknown CVar:**

```
> {"id": 2, "cmd": "bridge.cvar_get no_such_cvar"}
< {"id": 2, "ok": false, "error": "unknown cvar: no_such_cvar"}
```

**Misspelt verb** (refused, not silently accepted):

```
> {"id": 3, "cmd": "bridge.cvar_gett sys_spec"}
< {"id": 3, "ok": false, "error": "unknown bridge verb: bridge.cvar_gett (families: bridge.camera.*, ...)"}
```

The family list in this message is incomplete; section 6 is the full list.

**Raw console command** (`output` is plain text, not JSON):

```
> {"id": 4, "cmd": "r_DisplayInfo"}
< {"id": 4, "ok": true, "output": "..."}
```

**Not a bridge request** (for example a browser):

```
> POST / HTTP/1.1
< {"id": null, "ok": false, "error": "not a bridge request (expected a JSON object per line)"}
(connection closed)
```

**Two requests on one connection** are answered in order, one line each:

```
> {"id": 5, "cmd": "bridge.dialog.list"}
> {"id": 6, "cmd": "bridge.window.info"}
< {"id": 5, "ok": true, "output": "{\"ok\": true, \"verb\": \"list\", \"count\": 0, ...}"}
< {"id": 6, "ok": true, "output": "{\"ok\": true, \"verb\": \"info\", ...}"}
```

## 8. Not verified

- The exact arguments of the introspection dump verbs beyond the comments quoted in the source.
- Behaviour with non-ASCII paths in each verb.
- That the plugin builds and loads against the stock Launcher `Sandbox.exe` rather than a locally built one. See the plugin build notes in `architecture.md`.
