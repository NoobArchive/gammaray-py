import struct, os, sys
from gammahash import hash_path

KEY = bytes([0xE7,0x9B,0x9B,0x9C,0x4E,0xD6,0x44,0x50,0xB3,0x53,0x8D,0xF1,0x38,0xCC,0xCE,0xA9])

def build(members, version=0, dead=()):
    # members: list of (name, data, header_size)
    # dead: list of (size, end_offset) appended after index
    header = 20
    off = header
    placed = []
    for name, data, hs in members:
        placed.append((name, data, hs, off))
        off += len(data)
    idxoff = off
    idx = b''.join(struct.pack('<IIIII', hash_path(name), hs, o, len(d), 0)
                   for name, d, hs, o in placed)
    body = bytearray()
    pos = header
    for name, d, hs, o in placed:
        assert pos == o
        blob = bytes(x ^ KEY[(o + i) & 15] for i, x in enumerate(d)) if version == 1 else d
        body += blob
        pos += len(d)
    out = bytearray(struct.pack('<4sIIII', b'pckg', version, len(members), idxoff, len(dead)))
    out += body
    out += idx
    for size, end in dead:
        out += struct.pack('<II', size, end)
    assert len(out) == idxoff + len(idx) + len(dead) * 8
    return bytes(out)

if __name__ == '__main__':
    # smoke test: round-trip a few misc Lua chunks
    import pkg_unpack as pu
    src = r'D:\game\guyu2\data\misc.pkg'
    b = open(src, 'rb').read()
    magic, ver, count, idxoff = struct.unpack_from('<4sIII', b, 0)
    members = []
    for i in range(5):
        h, hs, o, sz, z = struct.unpack_from('<IIIII', b, idxoff + i * 20)
        d = b[o:o + sz]
        d = bytes(x ^ KEY[(o + j) & 15] for j, x in enumerate(d))
        members.append((f'ShellClient/test_{i}.lua', d, hs))
    p = build(members, 1)
    open('roundtrip.pkg', 'wb').write(p)
    names = {f'roundtrip/{i}': members[i][0] for i in range(len(members))}
    os.makedirs('out_roundtrip', exist_ok=True)
    print(pu.unpack('roundtrip.pkg', 'out_roundtrip', names))
    for name, d, hs in members:
        got = open(os.path.join('out_roundtrip', name.replace('/', os.sep)), 'rb').read()
        assert got == d, name
    print('roundtrip OK')
