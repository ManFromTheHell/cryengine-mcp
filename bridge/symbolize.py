"""Resolve Module+0xOFFSET frames against a local PDB (dbghelp, no debugger).

    python bridge/symbolize.py <dll> 0x19ee55 [0x179b69 ...]                 offsets of ONE module (as before)
    python bridge/symbolize.py --log <error.log> [--dir <package|symbols folder> ...] [--all-threads]
                                                                       every frame of the crashed thread (all threads with --all-threads) of an engine error.log

--log reads the stacks the engine's crash handler wrote (`[Crashed] Call Stack: ["JobSystem_Worker_7" [21368]]` then `  8) function=CryRenderD3D11.dll+0xb6a6b` ...), finds each module's binary
(--dir folders first: a package, its `_symbols` archive from package_symbols.py; then the project's bin folders for Game.dll, the installed engine, the engine source tree, the system folder) and
its PDB by GUID and age (package_symbols.find_pdb: a same-named PDB of another build is NOT used), and prints function and source line where a PDB resolves it, the nearest export where only the
binary is known, and `?` otherwise. A frame is never presented as resolved when the PDB did not match: the status column says `pdb matched`, `NO MATCHING PDB`, or `export only`.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import re
import sys

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
sys.path.insert(0, HERE)
# CRYENGINE_ENGINE: the installed engine folder (default: the Launcher 5.7.1 LTS install); CRYENGINE_SOURCE: an engine source tree to search for binaries (optional;
# a missing folder is skipped by module_dirs).
ENGINE = os.environ.get("CRYENGINE_ENGINE") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1")
ENGINE_SOURCE = _paths.ENGINE_SOURCE            # "" when CRYENGINE_SOURCE is unset: the source tree is then simply not searched
BASE = 0x180000000
STEP = 0x100000000

FRAME = re.compile(r"^\s*(\d+)\)\s+function=(?P<mod>[^+\s]+)\+0x(?P<off>[0-9a-fA-F]+)\s*$")
STACK = re.compile(r'^(?P<crashed>\[Crashed\]\s+)?Call Stack:\s*\["(?P<name>[^"]*)"\s*\[(?P<tid>\d+)\]\]')


def parse_log(text):
    """-> [{"thread", "tid", "crashed", "frames": [(index, module, offset)]}] in the order of the file (frame 1 is the outermost call, the highest number the innermost)"""
    stacks, cur = [], None
    for line in text.splitlines():
        m = STACK.match(line.strip())
        if m:
            cur = {"thread": m.group("name"), "tid": int(m.group("tid")), "crashed": bool(m.group("crashed")), "frames": []}
            stacks.append(cur)
            continue
        f = FRAME.match(line)
        if f and cur is not None:
            cur["frames"].append((int(f.group(1)), f.group("mod"), int(f.group("off"), 16)))
    return stacks


def module_dirs(dirs=()):
    """folders searched for a module's binary, in order"""
    out = []
    for d in dirs:
        d = os.path.abspath(d)
        out.append(d)
        for sub in ("bin/win_x64_release", "bin/win_x64", "bin"):
            out.append(os.path.join(d, *sub.split("/")))
    out += [os.path.join(PROJECT, "bin", "win_x64_release"), os.path.join(PROJECT, "bin", "win_x64"),
            os.path.join(ENGINE, "bin", "win_x64_release"), os.path.join(ENGINE, "bin", "win_x64"),
            ]
    if ENGINE_SOURCE:
        out += [os.path.join(ENGINE_SOURCE, "bin", "win_x64"), os.path.join(ENGINE_SOURCE, "bin", "win_x64_release")]
    out.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"))
    return [d for d in out if os.path.isdir(d)]


def find_module(name, dirs):
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


class Resolver:
    """dbghelp, one fake process handle, one module per base address"""
    def __init__(self, search_dirs=()):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.dbghelp = ctypes.WinDLL("dbghelp.dll")
        outer = self

        class SYMBOL_INFO(ctypes.Structure):
            _fields_ = [("SizeOfStruct", wintypes.ULONG), ("TypeIndex", wintypes.ULONG), ("Reserved", ctypes.c_ulonglong * 2),
                        ("Index", wintypes.ULONG), ("Size", wintypes.ULONG), ("ModBase", ctypes.c_ulonglong),
                        ("Flags", wintypes.ULONG), ("Value", ctypes.c_ulonglong), ("Address", ctypes.c_ulonglong),
                        ("Register", wintypes.ULONG), ("Scope", wintypes.ULONG), ("Tag", wintypes.ULONG),
                        ("NameLen", wintypes.ULONG), ("MaxNameLen", wintypes.ULONG), ("Name", ctypes.c_char * 1024)]

        class IMAGEHLP_LINE64(ctypes.Structure):
            _fields_ = [("SizeOfStruct", wintypes.DWORD), ("Key", ctypes.c_void_p), ("LineNumber", wintypes.DWORD),
                        ("FileName", ctypes.c_char_p), ("Address", ctypes.c_ulonglong)]
        self.SYMBOL_INFO, self.IMAGEHLP_LINE64 = SYMBOL_INFO, IMAGEHLP_LINE64
        d = self.dbghelp
        d.SymLoadModuleEx.restype = ctypes.c_ulonglong
        d.SymLoadModuleEx.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulonglong, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        d.SymFromAddr.argtypes = [wintypes.HANDLE, ctypes.c_ulonglong, ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(SYMBOL_INFO)]
        d.SymGetLineFromAddr64.argtypes = [wintypes.HANDLE, ctypes.c_ulonglong, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(IMAGEHLP_LINE64)]
        self.proc = wintypes.HANDLE(0x1234)
        d.SymSetOptions(0x2 | 0x10)                                # UNDNAME | LOAD_LINES (no symbol server, no deferred surprises)
        paths = ";".join(search_dirs).encode()
        d.SymInitialize(self.proc, paths or None, False)
        self.bases = {}                                            # lower module name -> (base, status, binary)
        self.dirs = list(search_dirs)

    def load(self, name, binary, pdb_status=None):
        key = name.lower()
        if key in self.bases:
            return self.bases[key]
        base = BASE + STEP * len(self.bases)
        if binary and self.dbghelp.SymLoadModuleEx(self.proc, None, binary.encode(), None, base, 0, None, 0):
            self.bases[key] = (base, pdb_status or "?", binary)
        else:
            self.bases[key] = (None, "binary not found", binary)
        return self.bases[key]

    def at(self, name, offset):
        """-> (function text, source text, status)"""
        base, status, _binary = self.bases[name.lower()]
        if base is None:
            return "?", "", status
        addr = base + offset
        sym = self.SYMBOL_INFO()
        sym.SizeOfStruct = self.ct.sizeof(self.SYMBOL_INFO) - 1024
        sym.MaxNameLen = 1024
        disp = self.ct.c_ulonglong(0)
        found = self.dbghelp.SymFromAddr(self.proc, addr, self.ct.byref(disp), self.ct.byref(sym))
        fn = "%s+0x%x" % (sym.Name.decode("latin-1"), disp.value) if found else "?"
        if found and status != "pdb matched" and disp.value > 0x2000:      # without a PDB the nearest EXPORT can be megabytes away: that is not the function
            fn = "? (nearest export %s is +0x%x away: not the function)" % (sym.Name.decode("latin-1"), disp.value)
        line = self.IMAGEHLP_LINE64()
        line.SizeOfStruct = self.ct.sizeof(self.IMAGEHLP_LINE64)
        d2 = self.wt.DWORD(0)
        where = ("%s:%d" % (line.FileName.decode("latin-1"), line.LineNumber)) if self.dbghelp.SymGetLineFromAddr64(self.proc, addr, self.ct.byref(d2), self.ct.byref(line)) else ""
        return fn, where, status


def resolve_stacks(stacks, search_dirs=(), all_threads=False, resolver=None):
    """-> list of printable lines for the crashed thread (or all). `resolver` is a test hook."""
    import package_symbols
    dirs = module_dirs(search_dirs)
    res = resolver or Resolver([d for d in dirs if os.path.isdir(d)][:6])
    lines = []
    for s in stacks:
        if not (s["crashed"] or all_threads):
            continue
        lines.append("%s thread %s [%d]" % ("CRASHED" if s["crashed"] else "      ", s["thread"], s["tid"]))
        for idx, mod, off in sorted(s["frames"], key=lambda f: -f[0]):
            if mod.lower() not in res.bases:
                binary = find_module(mod, dirs)
                status = "binary not found"
                if binary:
                    pdb, how = package_symbols.find_pdb(binary, dirs)
                    status = {"matched": "pdb matched", "name-only": "NO MATCHING PDB (a same-named one has another GUID/age)", "none": "export only"}[how]
                res.load(mod, binary, status)
            fn, where, status = res.at(mod, off)
            if fn == "?" and status in ("pdb matched",):
                status = "pdb matched, no symbol at this address"
            lines.append("  %2d) %s+0x%x  %s  %s  [%s]" % (idx, mod, off, fn, where, status))
    if not lines:
        lines.append("no stack in the log" if not stacks else "no crashed thread in the log (use --all-threads)")
    return lines


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    if a[0] == "--log":
        log = a[1]
        dirs = [a[i + 1] for i, x in enumerate(a) if x == "--dir"]
        with open(log, "rb") as f:
            text = f.read().decode("utf-8", "replace")
        exe = re.search(r"^Executable:\s*(.+)$", text, re.M)                # the build that crashed: its folder is searched first
        if exe and os.path.isdir(os.path.dirname(exe.group(1).strip())):
            dirs.insert(0, os.path.dirname(exe.group(1).strip()))
        print("(the log carries no module GUIDs: binaries are chosen by folder; pass --dir <package or symbols folder> of the build that crashed)")
        for line in resolve_stacks(parse_log(text), dirs, "--all-threads" in a):
            print(line)
        return 0
    dll = os.path.abspath(a[0])
    res = Resolver([os.path.dirname(dll)])
    if not res.load(os.path.basename(dll), dll, "?")[0]:
        sys.exit("could not load " + dll)
    for off in a[1:]:
        fn, where, _ = res.at(os.path.basename(dll), int(off, 16))
        print("%s  %s  %s" % (off, fn, where))
    return 0


if __name__ == "__main__":
    sys.exit(main())
