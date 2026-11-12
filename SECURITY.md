# Security

## Trust model

- The editor plugin (`ClaudeBridgePlugin`) has **no authentication**. Anything that can connect to its port can use it.
- It binds `127.0.0.1` only (default port 37778) and accepts connections from the loopback address only. It is not meant to be exposed to a network; do not forward or proxy the port.
- Every request is one JSON object per line. The plugin drops a connection whose first non-whitespace byte is not `{`, after sending one JSON error line. This rejects HTTP requests that a web page makes the browser send to the port.
- A client that gets through can run Python inside the editor, run console commands, and read and write files through the editor's process. Treat every local process and every MCP client you register as able to do the same.
- The `really` and `overwrite` flags and the `allow_editor_close` flag of the jobs guard against accidents; they are not a security boundary.
- Editor auto-recovery (which can start or terminate `Sandbox.exe`) is off unless `CLAUDE_AUTO_RECOVER=1` is set.

Details: [docs/safety.md](docs/safety.md) and [docs/plugin-protocol.md](docs/plugin-protocol.md).

## Reporting a vulnerability

Open an issue on the GitHub repository for ordinary problems. For a report that should not be public (for example a way to make the bridge execute commands from a web page or from another user's session), use GitHub's private vulnerability reporting on the repository (the "Security" tab, "Report a vulnerability") instead of a public issue.

## Supported version

Only the latest `main` is supported.
