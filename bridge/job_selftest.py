#!/usr/bin/env python3
"""A harmless job for proving the job machinery (jobs.py kind `selftest`): prints a line a second, then exits with the code it was given.

    job_selftest.py <seconds> <exit code>
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import sys
import time

seconds, code = int(sys.argv[1]), int(sys.argv[2])
for i in range(seconds):
    print("tick %d of %d" % (i + 1, seconds), flush=True)
    time.sleep(1)
print("RESULT selftest: %d of %d" % (1 if code == 0 else 0, 1), flush=True)
sys.exit(code)
