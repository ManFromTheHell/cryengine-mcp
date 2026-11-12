#!/usr/bin/env python3
"""Minimal ClaudeBridge client. Python 3, standard library only, loopback only.

    python bridge_client.py                          # three harmless read-only demo commands
    python bridge_client.py "bridge.cvar_get r_DisplayInfo"   # whatever you pass is sent as is

With no arguments it sends only reads: bridge.cvar_get sys_spec, bridge.dialog.list and
bridge.window.info. Anything you pass on the command line runs in the editor with your
rights (see docs/safety.md in the repository). The port is 37778; CRYENGINE_BRIDGE_PORT overrides it.
The demo works only while Sandbox is running with ClaudeBridgePlugin loaded; otherwise it prints a message and exits with 1.
"""
import json, os, socket, sys

def send(cmd, req_id=1, port=int(os.environ.get("CRYENGINE_BRIDGE_PORT", "37778")), timeout=25.0):
    with socket.create_connection(("127.0.0.1", port), timeout) as s:        # one connection per command
        s.sendall((json.dumps({"id": req_id, "cmd": cmd}, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):                                         # the reply is exactly one line
            chunk = s.recv(65536)
            if not chunk:
                raise ConnectionError("connection closed before a full reply line")
            buf += chunk
    return json.loads(buf.decode("utf-8", "replace"))                         # layer 1: the envelope

def output_of(reply):
    """Layer 2: for bridge.* verbs the "output" string is itself JSON text; other commands return plain text."""
    try:
        return json.loads(reply["output"])
    except (KeyError, TypeError, ValueError):
        return reply.get("output")

if __name__ == "__main__":
    for n, cmd in enumerate(sys.argv[1:] or ["bridge.cvar_get sys_spec", "bridge.dialog.list", "bridge.window.info"], 1):
        try:
            reply = send(cmd, n)
        except ConnectionRefusedError:
            sys.exit("Connection refused: the editor is not running, or ClaudeBridgePlugin did not load "
                     "(nothing is listening on 127.0.0.1:%s). Start Sandbox with the plugin and try again." % os.environ.get("CRYENGINE_BRIDGE_PORT", "37778"))
        print(">", cmd)
        print(json.dumps(output_of(reply) if reply.get("ok") else {"error": reply.get("error")}, indent=2)[:1500])
