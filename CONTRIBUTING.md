# Contributing

This project is an MCP server for the CRYENGINE 5.7.1 Sandbox editor plus a C++ editor plugin. Read the [README](README.md) first. This file covers how to change the code, what a good change contains and how to report a bug.

## Ground rules

- **Results are measurements, not "ok".** A tool returns what it read back from the engine, the editor or the engine's files (counts, names, values, file paths), so a caller can tell a real answer from an empty one. A check that finds nothing must show that it was able to find something: validators in this repo come with a positive control (a deliberately broken fixture that the check must flag) next to the clean case.
- **A zero needs a control.** If a tool reports 0 errors, 0 matches or a ratio, the test must also prove the instrument works (see the `control:` checks in `tests/test_watchdog_crash.py` or `tests/test_snapshot.py`).
- **Nothing downloads.** New code must not fetch files or use the network. If a feature needs an external file, it says what is needed, from where, and how big, and leaves the download to the user. Existing exception: `catalogs/scrape_cryengine_docs.py` (run by hand, downloads pages of the online manual).
- **No arbitrary commands.** `start_job` runs a fixed set of job kinds. Do not add a tool that runs a caller-supplied command line. (`execute_python` and `execute_python_script` run Python inside the editor; that is a known exception and is documented in [docs/safety.md](docs/safety.md).)
- **Nothing is replaced silently.** A tool that overwrites an existing file or object takes an explicit `overwrite=true` (or `really=true`), and bulk deletes are preceded by `snapshot_project` in the instructions you write.
- **No machine-specific paths or names.** New code takes paths from `_paths.py` (`_paths.WORKSPACE` / `_paths.workspace(name)` for state files, `_paths.ENGINE_SOURCE` for a source checkout, `_paths.MOTIONBRICKS_ROOT`), from `bridge/project_root.py`, or from an environment variable; do not write a user name, a drive layout or a game's level name as a default. Standard Windows locations (the Launcher's engine folder, `%PROGRAMDATA%`, `%LOCALAPPDATA%`) are accepted as a last fallback behind a variable. Existing exceptions are the Ashvale example and packaging defaults (the `Ashvale` package prefix, the `ashvale` level and preset names, `AshvaleStart`) listed in [docs/installation.md](docs/installation.md); do not copy them. Comments state technical facts: no dates, diary entries or first-person remarks.
- **Do not weaken a safety property** of the plugin: it binds `127.0.0.1` only and drops a connection whose first non-whitespace byte is not `{`.

## Setup

- Windows, Python 3.10 or newer (the code uses `dict | None` annotations), `pip install -r requirements.txt` (installs `mcp` and `numpy`, both needed to start the server).
- Running the server's tools against the editor needs the plugin built and Sandbox running: [docs/installation.md](docs/installation.md). Much of the work (validators, catalogs, tests with synthetic data) does not.

## Adding a tool

1. **Write the logic in a helper module**, not in `server.py`. Look at `checks/pbs_check.py`, `catalogs/ace_catalog.py` or `packaging/snapshot.py`. The pattern used throughout:
   - a module docstring that says what it measures, how to run it from the command line, and what it does not prove;
   - plain functions that return a `dict` (or list of `dict`) with the findings and the counts they were computed from;
   - a module-level exception class for refusals (for example `SnapshotError`, `ManualError`);
   - `if __name__ == "__main__":` with a small command line (`argparse` or plain `sys.argv`) so the module also works without the MCP server;
   - paths from `project_root.PROJECT` or an environment variable.
   - the file goes in the folder that fits its purpose (`bridge/`, `checks/`, `catalogs/`, `level/`, `assets/`, `animation/`, `screens/`, `packaging/`, `tests/`), starts with the three-line `_paths` header that every module has (copy it from a neighbour: it makes the other modules importable by name and lets the file run on its own), defines `HERE = _paths.ROOT` when it needs the repository root, and is added to `tools/module_folders.json` and `tools/module_meta.json` so `tools/gen_tools_doc.py` lists it.
2. **Register it in `server.py`** under `@mcp.tool()`. Existing tools import their helper below the tools that need it (`import x as x_mod  # noqa: E402`) and call it from a thread so the event loop is not blocked:

   ```python
   @mcp.tool()
   async def my_check(paths: list[str] | None = None) -> dict:
       """One paragraph: what is measured, against what, how long it takes, whether it needs the editor,
       what each argument does, and how to read the result. Say what it does not prove."""
       return await asyncio.to_thread(my_mod.check, paths or None)
   ```

   The docstring is the tool description the client sees and `docs/tools.md` is generated from it. Return `_refusal(exc)` (`{"ok": False, "refused": "..."}`) for an expected refusal. Tools that talk to the editor use the helpers near the top of `server.py` (`_send_command`, `_send_structured`; helper modules use `bridge_py.send`); do not open your own socket.
3. **Add it to a family** in the `FAMILIES` table (`server.py`, just above `tool_guide`). `tool_guide` lists any tool that no family claims under `unclassified`, and `tests/test_mcp_tools.py` checks that none is.
4. **Add a test** (below).
5. **Regenerate the tool reference:** `python tools/gen_tools_doc.py` writes `docs/tools.md`; `python tools/gen_tools_doc.py --check` exits 1 when it is out of date. The script has no `--help`: any argument other than `--check` is ignored and the files are rewritten. Flags such as "needs editor" and "writes files" come from `tools/tool_meta.json`; add an entry for your tool there.
6. **Add a knowledge chunk** if you learned something that fails silently or costs a build cycle (below).

## Tests

Tests are standalone scripts, not pytest modules. Each `test_*.py` can be run directly (`python tests/test_snapshot.py`), prints one line per check, and exits non-zero if any check failed. The common shape:

```python
def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-68s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:80]))
```

- Prefer synthetic fixtures in a temporary folder over real project data, so the test passes on any machine.
- Start with a control that must pass, then the broken cases that must be flagged.
- A test that needs the editor, the engine source or a game project should say so in its docstring and must not report success when it silently skipped its live part. Several older tests still do this; do not copy it.
- Do not run a test that talks to a live editor against a level you care about. `tests/test_mcp_tools.py` and `tests/test_tool_smoke.py` start the server and call tools; read them first.
- There is no single command that runs every test; run the files one by one. Tests that exit 0 without an editor are listed in the [README](README.md#tests) (some of them skip their live part). Run the ones for the modules you touched, and `python -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" file.py` on every Python file you edit.

## Knowledge base chunks

`rag/cryengine_project_rag.jsonl` is the knowledge base behind `query_knowledge`. Never edit it by hand; use `rag/corpus_lib.py`:

```python
import corpus_lib as L
L.upsert(dict(id="trap-short-name", title="The symptom as the title", category="trap",
              tags=["words", "a newcomer", "would type"], source="where you measured it",
              content="What happens, why, and the fix.", status="measured",
              verified_in="editor", date="YYYY-MM-DD", superseded_by=""))
```

`upsert` validates the chunk (required fields, title length, known category, unique id and title) and writes atomically; nothing is written if validation fails. `status` is `measured`, `read-from-source`, `hypothesis` or `superseded`; `verified_in` is `none`, `editor`, `packaged` or `partial`. Say what you actually observed. See `rag/README.md` for the fields and categories, and `python rag/build_corpus.py --check` to validate the whole corpus.

Note: `corpus_lib.py` keeps a second, byte-identical copy of the corpus at the path in `CRYENGINE_CORPUS_MIRROR` (empty by default). `upsert` refuses and writes nothing unless that variable points to a file in an existing folder outside the repository, so first create the folder, copy `rag/cryengine_project_rag.jsonl` there, set the variable, and run the snippet from the `rag/` folder. `python rag/build_corpus.py --check` also expects a `rag_query.py` in that folder.

## Code style (observed in the repository)

- Python, standard library first. The server needs `mcp`; many modules also import `numpy`. Heavier packages (for example PyTorch for the MotionBricks scripts) are used only by the optional animation scripts.
- Long, informative docstrings are normal: they state the measurement, the limits, and the numbers that justify a rule.
- Names are `snake_case`; modules live in the folder that fits their purpose (see the README layout table); helper modules end in `_check`, `_catalog`, `_lab`, `_kit` by role.
- Errors that a user can fix are raised as a module exception and become `{"ok": False, "refused": ...}`; do not swallow exceptions to return a "pass".
- Read and write text files with an explicit `encoding="utf-8"`.
- Make small, exact edits rather than scripted regex rewrites of whole files; Windows paths contain backslashes that string-patching scripts easily mangle.
- C++ (the plugin): tabs for indentation, braces on their own line, comments that explain why.

## Reporting a bug

Open an issue with:

1. What you ran: the tool name and arguments, or the command line.
2. What you expected, and what you got (paste the full result; large results are saved by the server and `read_output` pages them).
3. Your environment: CRYENGINE version (5.7.1 expected), Windows version, Python version, how the plugin was built, and whether the editor was running with a level open.
4. The editor log lines from the plugin (they start with `[ClaudeBridgePlugin]`) if the tool talks to the editor.
5. Whether the problem reproduces on a blank project. Some modules still assume the layout of the project they came from; if the failure mentions a level or asset name you do not have, that is likely the cause and is worth reporting.

For a **security issue** in the bridge, follow [SECURITY.md](SECURITY.md) instead of posting details in a public issue.
