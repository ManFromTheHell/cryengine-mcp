#!/usr/bin/env python3
"""Where is a live process's main thread? Samples its call stack without a debugger: suspend the
thread, read its CONTEXT, walk the stack with dbghelp's StackWalk64 (x64 unwind data comes from each
module's .pdata, so no PDB is needed to WALK), resume. Frames print as module+0xOFFSET, resolved to
a symbol when dbghelp can (PDB beside the module, or an export) -- symbolize.py takes the same
offsets. Built for Release builds, whose log is compiled out.

    stack_sample.py <pid | exe name> [samples=40] [interval_s=0.25] [--depth 24] [--all-threads]

Output: the most frequent stacks (by their top frames), then time per module at the leaf and
"deepest non-system module" per sample -- usually the answer on its own.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import ctypes
import os
import sys
import time
from collections import Counter
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
dbghelp = ctypes.WinDLL("dbghelp", use_last_error=True)

PROCESS_ALL = 0x0400 | 0x0010 | 0x0008 | 0x0020  # QUERY_INFORMATION | VM_READ | VM_OPERATION | VM_WRITE
THREAD_ALL = 0x0002 | 0x0008 | 0x0010 | 0x0040  # SUSPEND_RESUME | GET_CONTEXT | SET_CONTEXT | QUERY_INFORMATION
TH32CS_SNAPTHREAD, TH32CS_SNAPPROCESS = 0x4, 0x2
CONTEXT_FULL = 0x10000B   # AMD64 | CONTROL | INTEGER | FLOATING_POINT
AMD64 = 0x8664
SYSTEM_MODULES = {"ntdll.dll", "kernel32.dll", "kernelbase.dll", "win32u.dll", "user32.dll", "ucrtbase.dll",
                  "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "combase.dll", "rpcrt4.dll", "gdi32.dll"}


class THREADENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD), ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260)]


class ADDRESS64(ctypes.Structure):
    _fields_ = [("Offset", ctypes.c_ulonglong), ("Segment", wintypes.WORD), ("Mode", wintypes.DWORD)]


class KDHELP64(ctypes.Structure):
    _fields_ = [("Thread", ctypes.c_ulonglong), ("ThCallbackStack", wintypes.DWORD), ("ThCallbackBStore", wintypes.DWORD),
                ("NextCallback", wintypes.DWORD), ("FramePointer", wintypes.DWORD), ("KiCallUserMode", ctypes.c_ulonglong),
                ("KeUserCallbackDispatcher", ctypes.c_ulonglong), ("SystemRangeStart", ctypes.c_ulonglong),
                ("KiUserExceptionDispatcher", ctypes.c_ulonglong), ("StackBase", ctypes.c_ulonglong),
                ("StackLimit", ctypes.c_ulonglong), ("BuildVersion", wintypes.DWORD), ("RetpolineStubFunctionTableSize", wintypes.DWORD),
                ("RetpolineStubFunctionTable", ctypes.c_ulonglong), ("RetpolineStubOffset", wintypes.DWORD),
                ("RetpolineStubSize", wintypes.DWORD), ("Reserved0", ctypes.c_ulonglong * 2)]


class STACKFRAME64(ctypes.Structure):
    _fields_ = [("AddrPC", ADDRESS64), ("AddrReturn", ADDRESS64), ("AddrFrame", ADDRESS64), ("AddrStack", ADDRESS64),
                ("AddrBStore", ADDRESS64), ("FuncTableEntry", ctypes.c_void_p), ("Params", ctypes.c_ulonglong * 4),
                ("Far", wintypes.BOOL), ("Virtual", wintypes.BOOL), ("Reserved", ctypes.c_ulonglong * 3), ("KdHelp", KDHELP64)]


class SYMBOL_INFO(ctypes.Structure):
    _fields_ = [("SizeOfStruct", wintypes.ULONG), ("TypeIndex", wintypes.ULONG), ("Reserved", ctypes.c_ulonglong * 2),
                ("Index", wintypes.ULONG), ("Size", wintypes.ULONG), ("ModBase", ctypes.c_ulonglong),
                ("Flags", wintypes.ULONG), ("Value", ctypes.c_ulonglong), ("Address", ctypes.c_ulonglong),
                ("Register", wintypes.ULONG), ("Scope", wintypes.ULONG), ("Tag", wintypes.ULONG),
                ("NameLen", wintypes.ULONG), ("MaxNameLen", wintypes.ULONG), ("Name", ctypes.c_char * 512)]


k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenThread.restype = wintypes.HANDLE
k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
dbghelp.StackWalk64.argtypes = [wintypes.DWORD, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(STACKFRAME64),
                                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
dbghelp.SymFromAddr.argtypes = [wintypes.HANDLE, ctypes.c_ulonglong, ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(SYMBOL_INFO)]
dbghelp.SymGetModuleBase64.restype = ctypes.c_ulonglong
dbghelp.SymGetModuleBase64.argtypes = [wintypes.HANDLE, ctypes.c_ulonglong]
k32.GetThreadTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD]
psapi.GetModuleInformation.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
k32.SuspendThread.argtypes = k32.ResumeThread.argtypes = k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.SuspendThread.restype = k32.ResumeThread.restype = wintypes.DWORD
k32.GetThreadContext.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.GetThreadDescription.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
k32.GetThreadDescription.restype = ctypes.c_long
k32.LocalFree.argtypes = [ctypes.c_void_p]
dbghelp.SymInitialize.argtypes = [wintypes.HANDLE, ctypes.c_char_p, wintypes.BOOL]


def thread_name(tid):
    """GetThreadDescription (Windows 10 1607+): CRYENGINE names its threads (e.g. RenderLoadingThread)."""
    h = k32.OpenThread(0x1000, False, tid)   # THREAD_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    p = ctypes.c_wchar_p()
    try:
        if k32.GetThreadDescription(wintypes.HANDLE(h), ctypes.byref(p)) >= 0 and p.value:
            name = p.value
            k32.LocalFree(p)
            return name
    finally:
        k32.CloseHandle(h)
    return ""


def find_pid(arg):
    if arg.isdigit():
        return int(arg)
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    e = PROCESSENTRY32W()
    e.dwSize = ctypes.sizeof(e)
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        if e.szExeFile.lower() == arg.lower():
            k32.CloseHandle(snap)
            return e.th32ProcessID
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    sys.exit("no process named " + arg)


def threads_of(pid):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    e = THREADENTRY32()
    e.dwSize = ctypes.sizeof(e)
    out = []
    ok = k32.Thread32First(snap, ctypes.byref(e))
    while ok:
        if e.th32OwnerProcessID == pid:
            out.append(e.th32ThreadID)
        ok = k32.Thread32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


def modules(hproc):
    arr = (ctypes.c_void_p * 1024)()
    need = wintypes.DWORD()
    psapi.EnumProcessModulesEx(hproc, arr, ctypes.sizeof(arr), ctypes.byref(need), 3)
    info = []
    for h in arr[: need.value // ctypes.sizeof(ctypes.c_void_p)]:
        name = ctypes.create_unicode_buffer(520)
        psapi.GetModuleFileNameExW(hproc, ctypes.c_void_p(h), name, 520)

        class MODINFO(ctypes.Structure):
            _fields_ = [("base", ctypes.c_void_p), ("size", wintypes.DWORD), ("entry", ctypes.c_void_p)]
        mi = MODINFO()
        psapi.GetModuleInformation(hproc, ctypes.c_void_p(h), ctypes.byref(mi), ctypes.sizeof(mi))
        info.append((mi.base or 0, mi.size, os.path.basename(name.value)))
    return sorted(info)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pid = find_pid(args[0])
    samples = int(args[1]) if len(args) > 1 else 40
    interval = float(args[2]) if len(args) > 2 else 0.25
    depth = int(sys.argv[sys.argv.index("--depth") + 1]) if "--depth" in sys.argv else 24
    hproc = k32.OpenProcess(PROCESS_ALL, False, pid)
    if not hproc:
        sys.exit("OpenProcess failed: %d" % ctypes.get_last_error())
    dbghelp.SymSetOptions(0x2 | 0x10 | 0x4)   # UNDNAME | LOAD_LINES | DEFERRED_LOADS
    dbghelp.SymInitialize(hproc, None, True)
    mods = modules(hproc)

    def where(addr):
        for base, size, name in mods:
            if base <= addr < base + size:
                return name, addr - base
        return "?", addr

    def sym(addr):
        s = SYMBOL_INFO()
        s.SizeOfStruct = ctypes.sizeof(SYMBOL_INFO) - 512
        s.MaxNameLen = 511
        disp = ctypes.c_ulonglong()
        if dbghelp.SymFromAddr(hproc, addr, ctypes.byref(disp), ctypes.byref(s)) and disp.value < 0x4000:
            return "%s+0x%x" % (s.Name.decode("latin-1"), disp.value)
        return ""

    # The main thread is the one created first.
    tids = threads_of(pid)
    if "--all-threads" not in sys.argv:
        def created(tid):
            h = k32.OpenThread(0x0040, False, tid)
            c, e, kt, ut = (ctypes.c_ulonglong() for _ in range(4))
            k32.GetThreadTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut))
            k32.CloseHandle(h)
            return c.value or 1 << 62
        tids = [min(tids, key=created)]
    ctx_buf = ctypes.create_string_buffer(1232 + 16)
    ctx_addr = (ctypes.addressof(ctx_buf) + 15) & ~15
    fta = ctypes.cast(dbghelp.SymFunctionTableAccess64, ctypes.c_void_p)
    gmb = ctypes.cast(dbghelp.SymGetModuleBase64, ctypes.c_void_p)
    stacks, leaf_mod, deep_mod = Counter(), Counter(), Counter()
    per_thread = {}   # tid -> Counter of stacks (for --all-threads)
    frames_seen = {}
    for i in range(samples):
        for tid in tids:
            ht = k32.OpenThread(THREAD_ALL, False, tid)
            if not ht:
                continue
            if k32.SuspendThread(ht) == 0xFFFFFFFF:
                k32.CloseHandle(ht)
                continue
            try:
                ctypes.memset(ctx_addr, 0, 1232)
                ctypes.c_uint32.from_address(ctx_addr + 0x30).value = CONTEXT_FULL
                if not k32.GetThreadContext(ht, ctypes.c_void_p(ctx_addr)):
                    continue
                rip = ctypes.c_uint64.from_address(ctx_addr + 0xF8).value
                rsp = ctypes.c_uint64.from_address(ctx_addr + 0x98).value
                rbp = ctypes.c_uint64.from_address(ctx_addr + 0xA0).value
                sf = STACKFRAME64()
                sf.AddrPC.Offset, sf.AddrPC.Mode = rip, 3
                sf.AddrFrame.Offset, sf.AddrFrame.Mode = rbp, 3
                sf.AddrStack.Offset, sf.AddrStack.Mode = rsp, 3
                frames = []
                for _ in range(depth):
                    if not dbghelp.StackWalk64(AMD64, hproc, ht, ctypes.byref(sf), ctypes.c_void_p(ctx_addr), None, fta, gmb, None):
                        break
                    if sf.AddrPC.Offset == 0:
                        break
                    frames.append(sf.AddrPC.Offset)
            finally:
                k32.ResumeThread(ht)
                k32.CloseHandle(ht)
            names = []
            for a in frames:
                m, off = where(a)
                names.append("%s+0x%x" % (m, off))
                frames_seen.setdefault(names[-1], sym(a))
            if names:
                leaf_mod[names[0].split("+")[0]] += 1
                deep = next((n.split("+")[0] for n in names if n.split("+")[0].lower() not in SYSTEM_MODULES), "(system only)")
                deep_mod[deep] += 1
                stacks[tuple(names[:depth])] += 1
                per_thread.setdefault(tid, Counter())[tuple(names[:depth])] += 1
        time.sleep(interval)
    total = sum(leaf_mod.values())
    print("pid %d, %d samples of %s" % (pid, total, "the main thread" if len(tids) == 1 else "%d threads" % len(tids)))
    print("\nleaf module:", ", ".join("%s %d%%" % (m, 100 * n // total) for m, n in leaf_mod.most_common(8)))
    print("first non-system module:", ", ".join("%s %d%%" % (m, 100 * n // total) for m, n in deep_mod.most_common(8)))
    if len(tids) > 1:
        # Busy threads first: a thread whose leaf is not a kernel wait is doing work.
        def busy(tid):
            return sum(n for st, n in per_thread[tid].items() if not st[0].lower().startswith(("ntdll", "win32u")))
        print("\nper thread (busiest first; busy = samples whose leaf is outside ntdll/win32u):")
        if "--match" in sys.argv:
            # Full stacks of every thread whose samples touch one of the named modules.
            wanted = [m.lower() for m in sys.argv[sys.argv.index("--match") + 1].split(",")]
            for tid, c in per_thread.items():
                hits = [(st, n) for st, n in c.items() if any(f.lower().startswith(tuple(wanted)) for f in st)]
                if not hits:
                    continue
                st, n = max(hits, key=lambda h: h[1])
                print("\n  tid %d: %d/%d samples in this stack" % (tid, n, sum(c.values())))
                for f in st:
                    print("     %-40s %s" % (f, frames_seen.get(f, "")))
            return
        engine = [t for t in per_thread if any(f.startswith(("Cry", "Game")) for st in per_thread[t] for f in st)]
        for tid in sorted(engine, key=busy, reverse=True):
            st, n = per_thread[tid].most_common(1)[0]
            first = next((f for f in st if f.startswith(("Cry", "Game"))), st[-1])
            print("  tid %6d %-24s busy %3d/%-3d  first engine frame %-30s (%d x) %s"
                  % (tid, "'%s'" % thread_name(tid)[:22], busy(tid), sum(per_thread[tid].values()), first, n,
                     " <- ".join(f for f in st[:8] if not f.startswith(("ntdll", "KERNEL")))))
        return
    print("\nmost frequent stacks:")
    for st, n in stacks.most_common(4):
        print("  -- %d samples" % n)
        for f in st:
            print("     %-40s %s" % (f, frames_seen.get(f, "")))


if __name__ == "__main__":
    main()
