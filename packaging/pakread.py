"""Read one file out of a CryEngine .pak (a ZIP whose local headers use backslashes, which
Python's zipfile refuses). Reads the local header by hand. Read-only."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import struct
import sys
import zipfile
import zlib


def read(pak, name):
    z = zipfile.ZipFile(pak)
    info = next(i for i in z.infolist() if i.filename.lower() == name.lower())
    with open(pak, "rb") as f:
        f.seek(info.header_offset)
        hdr = f.read(30)
        n, m = struct.unpack("<HH", hdr[26:30])
        f.seek(info.header_offset + 30 + n + m)
        data = f.read(info.compress_size)
    if info.compress_type == 8:
        data = zlib.decompress(data, -15)
    return data


if __name__ == "__main__":
    data = read(sys.argv[1], sys.argv[2])
    if len(sys.argv) > 3:
        open(sys.argv[3], "wb").write(data)
        print("wrote", len(data))
    else:
        sys.stdout.write(data.decode("utf-8", "replace"))
