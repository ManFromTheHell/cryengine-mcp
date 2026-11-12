#!/usr/bin/env python3
"""Start a long script detached from this shell (a Claude background command is killed after 10 minutes; a test suite takes an hour).

    run_detached.py <log file> <script> [args...]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import subprocess
import sys

log = open(sys.argv[1], "wb")
p = subprocess.Popen([sys.executable, "-u"] + sys.argv[2:], stdout=log, stderr=subprocess.STDOUT, creationflags=0x8 | 0x200)
print("started pid", p.pid, "log", sys.argv[1])
