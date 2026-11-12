#!/usr/bin/env python3
"""Read a Statoscope file log (e_StatoscopeLogDestination 0 -> <user>/statoscope/perflog*.bin) and print a
summary per section. Statoscope is CRYENGINE's own per-frame profiler (manual: "Statoscope", "Logging
Data in Statoscope"); its viewer is Tools/Statoscope/Statoscope.exe (a GUI). This only reads the numbers
back so they can be compared in text -- the format is the engine's writer, CrySystem/Statoscope.cpp
(CStatoscope::Tick, CStatoscopeDataGroup::WriteHeader, CDataWriter::WriteDataStr with the string pool).

    statoscope_read.py <perflog.bin> [--functions N] [--worst N] [--marker-path Ashvale]

Sections come from user markers (e_StatoscopeAddUserMarker <path> <name>, data group 'u'): every
marker with the given path (default "Ashvale", what perf_tour.py writes) starts a section named after it. Groups used when present: 'f' frame length, 'O' overview
(main/render/GPU load), 't' threading, 'i' GPU times by pass, 'g' graphics (draw calls, tris),
'm' memory, 'r' function profile (top self time per section with --functions).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import collections
import struct
import sys

FLOAT, INT, STRING, B64TEXTURE, INT64 = 1, 2, 3, 4, 5
MARKER_PATH = sys.argv[sys.argv.index("--marker-path") + 1] if "--marker-path" in sys.argv else "Ashvale"


class Reader:
    def __init__(self, data):
        self.d = data
        self.p = 0
        self.pool = {}
        self.pooled = True

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def f32(self):
        v = struct.unpack_from("<f", self.d, self.p)[0]
        self.p += 4
        return v

    def i64(self):
        v = struct.unpack_from("<q", self.d, self.p)[0]
        self.p += 8
        return v

    def raw_str(self):
        n = self.i32()
        s = self.d[self.p:self.p + n].decode("latin-1")
        self.p += n
        return s

    def string(self):
        if not self.pooled:
            return self.raw_str()
        crc = self.u32()
        if crc in self.pool:
            return self.pool[crc]
        s = self.raw_str()
        self.pool[crc] = s
        return s

    def value(self, t):
        if t == FLOAT:
            return self.f32()
        if t == INT:
            return self.i32()
        if t == INT64:
            return self.i64()
        if t == STRING:
            return self.string()
        raise ValueError("element type %d" % t)


def read_frames(path):
    r = Reader(open(path, "rb").read())
    endian = r.u8()
    version = r.u32()
    r.pooled = r.u8() != 0
    if endian != 0:
        raise SystemExit("big-endian log")
    groups = []
    frames = []
    while r.p < len(r.d):
        start = r.p
        try:
            if r.u8():
                if r.u8():
                    raise SystemExit("module information ('k' group) is not supported -- leave it out")
                groups = []
                for _ in range(r.i32()):
                    gpath = r.string()
                    elems = [(r.i32(), r.string()) for _ in range(r.i32())]
                    groups.append((gpath, gpath.count("$"), elems))
            frame = {"t": r.f32(), "sets": {}}
            if r.i32() == B64TEXTURE:
                n = r.i32()
                r.p += n
            for gpath, ndollar, elems in groups:
                sets = []
                for _ in range(r.i32()):
                    names = [r.string() for _ in range(ndollar)]
                    vals = {name: r.value(t) for t, name in elems}
                    sets.append((names, vals))
                frame["sets"][gpath] = sets
            events = r.u32()                  # interval events (not "r.p += r.u32()": that adds to the OLD position)
            r.p += events
            if r.u32() != 0xDEADBEEF:
                print("frame at byte %d: no end marker -- stopping" % start)
                break
            frames.append(frame)
        except (struct.error, IndexError):
            break                             # a log cut off mid-frame (the game was killed)
    return version, frames


def pct(values, q):
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def main():
    path = sys.argv[1]
    nfunc = int(sys.argv[sys.argv.index("--functions") + 1]) if "--functions" in sys.argv else 0
    nworst = int(sys.argv[sys.argv.index("--worst") + 1]) if "--worst" in sys.argv else 0
    version, frames = read_frames(path)
    print("%s: version %d, %d frames, %.0f s" % (path, version, len(frames),
                                                 frames[-1]["t"] - frames[0]["t"] if frames else 0))
    sections = collections.OrderedDict()
    current = "start"
    for fr in frames:
        for names, vals in fr["sets"].get("/UserMarkers/$", []):
            # Only the caller's markers start a section: the engine adds its own (e.g. every global shader
            # cache miss is a "Shaders/Global Cache Misses" marker, D3DShaders.cpp:134-138).
            if names and names[0] == MARKER_PATH:
                current = vals.get("name", current)
        sections.setdefault(current, []).append(fr)

    def series(frs, gpath, field):
        out = []
        for fr in frs:
            for _, vals in fr["sets"].get(gpath, []):
                if field in vals:
                    out.append(vals[field])
        return out

    print("%-22s %6s %7s %7s %7s %7s | %6s %6s %6s | %6s %7s %8s | %s" % (
        "section", "frames", "ms p50", "p95", "p99", "max", "main", "render", "gpu", "draws", "shadowD", "tris", "spikes>33ms"))
    for name, frs in sections.items():
        fl = series(frs, "/", "frameLengthInMS") or series(frs, "/Overview/", "frameLengthInMS")
        mt = series(frs, "/Overview/", "MTLoadInMS")
        rt = series(frs, "/Overview/", "RTLoadInMS")
        gpu = series(frs, "/Overview/", "GPULoadInMS") or series(frs, "/Graphics/", "GPUFrameLengthInMS")
        dc = series(frs, "/Graphics/", "numTotalDrawCalls")
        sdc = series(frs, "/Graphics/", "numShadowDrawCalls")
        tris = series(frs, "/Graphics/", "numTris")
        spikes = sum(1 for v in fl if v > 33.3)
        print("%-22s %6d %7.2f %7.2f %7.2f %7.1f | %6.2f %6.2f %6.2f | %6.0f %7.0f %8.0f | %d" % (
            name[:22], len(frs), pct(fl, .5), pct(fl, .95), pct(fl, .99), max(fl) if fl else float("nan"),
            pct(mt, .5), pct(rt, .5), pct(gpu, .5), pct(dc, .5), pct(sdc, .5), pct(tris, .5), spikes))
    gpu_fields = [f for f in ("Frame", "Scene/Overall", "Shadows/Overall", "Lighting/Overall", "VFX/Overall",
                              "Scene/Water", "Scene/Particles&Glass", "VFX/Fog")]
    if any(fr["sets"].get("/GPUTimes/") for fr in frames):
        print("\nGPU time by pass (median ms):")
        print("%-22s " % "section" + " ".join("%9s" % f.split("/")[0][:9] + ("" if "/" not in f else "") for f in gpu_fields))
        for name, frs in sections.items():
            print("%-22s " % name[:22] + " ".join("%9.2f" % pct(series(frs, "/GPUTimes/", f), .5) for f in gpu_fields))
    mem = series(frames, "/Memory/", "mainMemUsageInMB")
    vid = series(frames, "/Memory/", "vidMemUsageInMB")
    if mem:
        print("\nmemory: main %.0f -> %.0f MB (peak %.0f), video %d -> %d MB" % (mem[0], mem[-1], max(mem), vid[0], vid[-1]))
    if nworst:
        # A stutter hides in a section's average: the N longest frames on their own, with what took their time.
        owner = {id(fr): name for name, frs in sections.items() for fr in frs}
        def one(fr, gpath, field):
            vals = [v[field] for _, v in fr["sets"].get(gpath, []) if field in v]
            return vals[0] if vals else float("nan")
        length = lambda fr: (one(fr, "/", "frameLengthInMS") if fr["sets"].get("/") else one(fr, "/Overview/", "frameLengthInMS"))
        print("\n%d longest frames:" % nworst)
        for fr in sorted(frames, key=lambda f: -(length(f) if length(f) == length(f) else 0))[:nworst]:
            print("  %7.1f ms at %.1f s in [%s]  main %.1f  render %.1f  gpu %.1f" % (
                length(fr), fr["t"] - frames[0]["t"], owner.get(id(fr), "?"), one(fr, "/Overview/", "MTLoadInMS"),
                one(fr, "/Overview/", "RTLoadInMS"), one(fr, "/Overview/", "GPULoadInMS")))
            funcs = sorted(((v.get("selfTimeInMS", 0.0), n[0]) for n, v in fr["sets"].get("/Threads/$", [])), reverse=True)
            for t, fn in funcs[:8]:
                print("        %8.2f ms  %s" % (t, fn))
    if nfunc:
        for name, frs in sections.items():
            total = collections.Counter()
            peak = collections.Counter()
            for fr in frs:
                for names, vals in fr["sets"].get("/Threads/$", []):
                    total[names[0]] += vals.get("selfTimeInMS", 0.0)
                    peak[names[0]] = max(peak[names[0]], vals.get("selfTimeInMS", 0.0))
            if not total:
                continue
            print("\n[%s] top self time per frame (ms), main thread unless named:" % name)
            for fn, t in total.most_common(nfunc):
                print("   %7.3f avg  %7.2f peak  %s" % (t / max(1, len(frs)), peak[fn], fn))


if __name__ == "__main__":
    main()
