#!/usr/bin/env python3
"""Where a mesh actually is: the bounding box of every mesh chunk in a .cgf, and its nodes.

    cgf_bounds.py <file.cgf> [...]

Reads the CrCh (0x746) chunk table: ChunkType_Mesh 0x1000 (MESH_CHUNK_DESC_0801 -- 7 ints, nStreamChunkID[16],
nPhysicsDataChunkId[4], then bboxMin/bboxMax; CryHeaders.h:571) and ChunkType_Node 0x100B (name[64], object and
parent ids). Sizes are metres in the mesh's own space.

Why: GameSDK props are not modelled around their origin, and guessing cost a round. torch.cgf is
the torch as it stands in its wall bracket torch_base.cgf -- y 0.29..0.64, z 0.10..1.08, so its fire is ~1 m up and
~0.5 m out, not the "0.6 m head height" once claimed from the viewport; its grip helper is node
'grab_onehanded_01'. The engine primitives' sizes were read the same way (primitive_box is a 1 x 1 x 0.25 slab).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import struct
import sys


def chunks(data):
    sig, ver, count, table = struct.unpack_from("<4sIII", data, 0)
    if sig != b"CrCh" or ver != 0x746:
        raise ValueError("not a CrCh 0x746 chunk file (%r %#x)" % (sig, ver))
    for i in range(count):
        ctype, cver, cid, size, offset = struct.unpack_from("<HHIII", data, table + i * 16)
        yield ctype, cver, cid, size, offset


def describe(path):
    data = open(path, "rb").read()
    print(path)
    for ctype, cver, cid, size, offset in chunks(data):
        if ctype == 0x1000:
            verts = struct.unpack_from("<i", data, offset + 8)[0]
            p = offset + 7 * 4 + 16 * 4 + 4 * 4
            lo = struct.unpack_from("<3f", data, p)
            hi = struct.unpack_from("<3f", data, p + 12)
            print("  mesh %-4d %5d verts  min (%7.3f %7.3f %7.3f)  max (%7.3f %7.3f %7.3f)  size (%.3f %.3f %.3f)"
                  % ((cid, verts) + lo + hi + tuple(b - a for a, b in zip(lo, hi))))
        elif ctype == 0x100B:
            name = data[offset:offset + 64].split(b"\0")[0].decode("latin1")
            obj, parent = struct.unpack_from("<ii", data, offset + 64)
            print("  node '%s' object %d parent %d" % (name, obj, parent))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for arg in sys.argv[1:]:
        describe(arg)
