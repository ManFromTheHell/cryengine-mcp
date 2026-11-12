#!/usr/bin/env python3
"""Read and write binary FBX files without the Autodesk SDK. Standard library only.

A binary FBX is a tree of node records: EndOffset, NumProperties, PropertyListLen (u32, or u64 from
version 7500), a name, typed properties, then child records closed by an all-zero record.
Property type codes: Y i16, C bool, I i32, F f32, D f64, L i64, S string, R raw bytes, and arrays
f d l i b (length, encoding 0=raw/1=zlib, compressed length, data).

    fbx_binary.py dump <file.fbx> [--depth N]    print the node tree (arrays summarised)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import struct
import sys
import zlib

MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"
FOOTER_ID = bytes([0xfa, 0xbc, 0xab, 0x09, 0xd0, 0xc8, 0xd4, 0x66, 0xb1, 0x76, 0xfb, 0x83, 0x1c, 0xf7, 0x26, 0x7e])
FOOTER_MAGIC = bytes([0xf8, 0x5a, 0x8c, 0x6a, 0xde, 0xf5, 0xd9, 0x7e, 0xec, 0xe9, 0x0c, 0xe3, 0x75, 0x8f, 0x29, 0x0b])
ARRAY_FMT = {"f": "f", "d": "d", "l": "q", "i": "i", "b": "b"}
SCALAR_FMT = {"Y": "h", "C": "?", "I": "i", "F": "f", "D": "d", "L": "q"}


class Node:
    def __init__(self, name, props=None, children=None, sentinel=None):
        self.name = name
        self.props = props if props is not None else []   # list of (typecode, value)
        self.children = children if children is not None else []
        # Whether the child list is closed by a null record. Read files keep
        # what they had; new nodes get one when they have children or no props.
        self.sentinel = sentinel

    def find(self, name):
        return next((c for c in self.children if c.name == name), None)

    def find_all(self, name):
        return [c for c in self.children if c.name == name]


def _read_prop(buf, pos):
    code = chr(buf[pos]); pos += 1
    if code in SCALAR_FMT:
        fmt = "<" + SCALAR_FMT[code]
        (value,) = struct.unpack_from(fmt, buf, pos)
        return (code, value), pos + struct.calcsize(fmt)
    if code in ARRAY_FMT:
        length, encoding, clen = struct.unpack_from("<III", buf, pos); pos += 12
        data = buf[pos:pos + clen]; pos += clen
        if encoding == 1:
            data = zlib.decompress(data)
        values = list(struct.unpack("<%d%s" % (length, ARRAY_FMT[code]), data))
        return (code, values), pos
    if code in "SR":
        (length,) = struct.unpack_from("<I", buf, pos); pos += 4
        return (code, bytes(buf[pos:pos + length])), pos + length
    raise ValueError("unknown property type %r at %d" % (code, pos - 1))


def _read_node(buf, pos, wide):
    head = "<QQQ" if wide else "<III"
    end, nprops, _plen = struct.unpack_from(head, buf, pos); pos += struct.calcsize(head)
    name_len = buf[pos]; pos += 1
    if end == 0:
        return None, pos + name_len
    name = bytes(buf[pos:pos + name_len]).decode("ascii", "replace"); pos += name_len
    props = []
    for _ in range(nprops):
        prop, pos = _read_prop(buf, pos)
        props.append(prop)
    children = []
    sentinel = pos < end
    while pos < end:
        child, pos = _read_node(buf, pos, wide)
        if child is None:
            break
        children.append(child)
    return Node(name, props, children, sentinel), end


def read(path):
    with open(path, "rb") as f:
        buf = f.read()
    if not buf.startswith(MAGIC):
        raise ValueError("not a binary FBX: %s" % path)
    (version,) = struct.unpack_from("<I", buf, 23)
    wide = version >= 7500
    pos = 27
    nodes = []
    while True:
        node, pos = _read_node(buf, pos, wide)
        if node is None:
            break
        nodes.append(node)
    # The 16-byte footer id is not a constant: it is tied to the file's FileId /
    # CreationTime, and the SDK rejects a file whose id does not match them
    # ("File is corrupted (or invalid)"). Keep the original.
    footer_id = bytes(buf[pos:pos + 16])
    return version, nodes, footer_id


def _write_prop(out, code, value):
    out += code.encode("ascii")
    if code in SCALAR_FMT:
        out += struct.pack("<" + SCALAR_FMT[code], value)
    elif code in ARRAY_FMT:
        raw = struct.pack("<%d%s" % (len(value), ARRAY_FMT[code]), *value)
        packed = zlib.compress(raw)
        out += struct.pack("<III", len(value), 1, len(packed)) + packed
    else:
        data = value.encode("utf-8") if isinstance(value, str) else value
        out += struct.pack("<I", len(data)) + data


def _write_node(out, node, wide):
    head = "<QQQ" if wide else "<III"
    start = len(out)
    out += b"\0" * struct.calcsize(head)
    name = node.name.encode("ascii")
    out += bytes([len(name)]) + name
    props_start = len(out)
    for code, value in node.props:
        _write_prop(out, code, value)
    props_len = len(out) - props_start
    sentinel = node.sentinel if node.sentinel is not None else (bool(node.children) or not node.props)
    if sentinel or node.children:
        for child in node.children:
            _write_node(out, child, wide)
        out += b"\0" * (struct.calcsize(head) + 1)
    struct.pack_into(head, out, start, len(out), len(node.props), props_len)


def write(path, version, nodes, footer_id=FOOTER_ID):
    wide = version >= 7500
    out = bytearray(MAGIC + struct.pack("<I", version))
    for node in nodes:
        _write_node(out, node, wide)
    out += b"\0" * ((25 if wide else 13))
    out += footer_id
    # Layout measured on a Crytek FBX 7400 file: pad to a 16-byte boundary
    # (a full 16 when already aligned), then 4 zero bytes, then the version.
    pad = (16 - (len(out) % 16)) % 16
    if pad == 0:
        pad = 16
    out += b"\0" * (pad + 4)
    out += struct.pack("<I", version) + b"\0" * 120 + FOOTER_MAGIC
    with open(path, "wb") as f:
        f.write(out)


def _fmt(code, value):
    if code in ARRAY_FMT:
        return "%s[%d]" % (code, len(value))
    if code in "SR":
        text = value.replace(b"\x00\x01", b"::")
        return repr(text[:60].decode("utf-8", "replace"))
    return repr(value)


def dump(nodes, depth, indent=0):
    for n in nodes:
        print("  " * indent + n.name + ": " + ", ".join(_fmt(c, v) for c, v in n.props[:6]))
        if indent + 1 < depth:
            dump(n.children, depth, indent + 1)


def _first(it, what):
    """First item of an iterator, or a ValueError naming what the FBX lacks (instead of a bare StopIteration)."""
    found = next(it, None)
    if found is None:
        raise ValueError("FBX has no %s" % what)
    return found


def add_proxy_skin(nodes, bone_name="Bip01 Pelvis"):
    """CRYENGINE's FBX importer (CryFbxScene PrepareBones) refuses to export a skeleton or animation
    from a file with no skinned mesh, and animation-only FBX files have none. Bind the first mesh to one
    bone with a Skin deformer + Cluster so RC accepts the file; the animation curves are untouched.
    Returns the geometry name that was skinned."""
    objects = _first((n for n in nodes if n.name == "Objects"), "Objects node")
    connections = _first((n for n in nodes if n.name == "Connections"), "Connections node")
    definitions = _first((n for n in nodes if n.name == "Definitions"), "Definitions node")

    geometry = _first((o for o in objects.children if o.name == "Geometry"), "Geometry (mesh) object")
    geometry_id = geometry.props[0][1]
    vertex_count = len(geometry.find("Vertices").props[0][1]) // 3
    bone = _first((o for o in objects.children
                   if o.name == "Model" and o.props[1][1].split(b"\x00\x01")[0].decode() == bone_name), "bone model named %r" % bone_name)
    bone_id = bone.props[0][1]

    used = {o.props[0][1] for o in objects.children if o.props and o.props[0][0] == "L"}
    skin_id = max(used) + 1
    cluster_id = skin_id + 1
    identity = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]

    objects.children.append(Node("Deformer", [("L", skin_id), ("S", b"ProxySkin\x00\x01Deformer"), ("S", b"Skin")], [
        Node("Version", [("I", 101)]),
        Node("Link_DeformAcuracy", [("D", 50.0)]),
    ]))
    objects.children.append(Node("Deformer", [("L", cluster_id), ("S", b"ProxyCluster\x00\x01SubDeformer"), ("S", b"Cluster")], [
        Node("Version", [("I", 100)]),
        Node("UserData", [("S", b""), ("S", b"")]),
        Node("Indexes", [("i", list(range(vertex_count)))]),
        Node("Weights", [("d", [1.0] * vertex_count)]),
        Node("Transform", [("d", identity)]),
        Node("TransformLink", [("d", identity)]),
    ]))
    for child, parent in ((skin_id, geometry_id), (cluster_id, skin_id), (bone_id, cluster_id)):
        connections.children.append(Node("C", [("S", b"OO"), ("L", child), ("L", parent)]))

    definitions.children.append(Node("ObjectType", [("S", b"Deformer")], [Node("Count", [("I", 2)])]))
    count = definitions.find("Count")
    count.props[0] = (count.props[0][0], count.props[0][1] + 2)
    return geometry.props[1][1].split(b"\x00\x01")[0].decode() or "(unnamed)"


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "add-skin":
        version, nodes, footer_id = read(sys.argv[2])
        skinned = add_proxy_skin(nodes)
        write(sys.argv[3], version, nodes, footer_id)
        print("bound geometry %s to Bip01 Pelvis -> %s" % (skinned, sys.argv[3]))
    elif len(sys.argv) >= 3 and sys.argv[1] == "dump":
        depth = int(sys.argv[sys.argv.index("--depth") + 1]) if "--depth" in sys.argv else 3
        version, nodes, _footer = read(sys.argv[2])
        print("FBX version", version)
        dump(nodes, depth)
    else:
        print(__doc__)
