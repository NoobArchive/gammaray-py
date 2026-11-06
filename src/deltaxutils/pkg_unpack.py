import struct, os, sys, json, argparse, hashlib, glob
try:
    from .gammahash import hash_path
except ImportError:
    from gammahash import hash_path

KEY = bytes([0xE7,0x9B,0x9B,0x9C,0x4E,0xD6,0x44,0x50,0xB3,0x53,0x8D,0xF1,0x38,0xCC,0xCE,0xA9])
COMMON_EXT = {b'tex\0':'tex', b'ams\0':'ams', b'ans\0':'ans', b'anf\0':'anf',
              b'mtr\0':'mtr', b'gfx\0':'gfx', b'gui\0':'gui', b'eft\0':'eft',
              b'map\0':'map'}

def decode_text(data):
    if data[:3] == b'\xef\xbb\xbf':
        return data[3:].decode('utf-8', 'replace')
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return data.decode('utf-16', 'replace')
    try:
        return data.decode('utf-8')
    except Exception:
        return data.decode('gbk', 'replace')

def sniff_ext(d, hs):
    if len(d) >= 4 and d[:4] in COMMON_EXT:
        return COMMON_EXT[d[:4]], '4cc'
    if d.startswith(b'\x1bLuaQ'):
        return 'luac', 'magic'
    if d.startswith(b'DDS '):
        return 'dds', 'magic'
    if d.startswith(b'ID3') or d[:2] in (b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'):
        return 'mp3', 'magic'
    if d.startswith(b'OggS'):
        return 'ogg', 'magic'
    if d.startswith(b'RIFF'):
        return 'wav', 'magic'
    if d.startswith(b'\x89PNG'):
        return 'png', 'magic'
    if d.startswith(b'\xff\xd8\xff'):
        return 'jpg', 'magic'
    if d.startswith(b'BM'):
        return 'bmp', 'magic'
    if d[:4] in (b'\x00\x01\x00\x00', b'OTTO', b'true', b'ttcf'):
        return 'ttf', 'magic'
    if d.startswith(b'\xef\xbb\xbf'):
        return 'xml', 'bom'
    if d[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return 'txt', 'bom'
    if hs > 0:
        return 'bin', 'head'
    # text-ish
    sample = d[:4096]
    if sample and all(32 <= x < 127 or x in (9, 10, 13) for x in sample):
        return 'txt', 'text'
    return 'bin', 'raw'

def common_head_size(d):
    """Return CCommonFileHead total size (head + extra), or None."""
    try:
        off = 12
        dep_count = struct.unpack_from('<I', d, 8)[0]
        for _ in range(dep_count):
            off += 4  # tag
            name_count = struct.unpack_from('<I', d, off)[0]
            off += 4
            for _ in range(name_count):
                cc = struct.unpack_from('<I', d, off)[0]
                off += 4 + cc * 2
        extra = struct.unpack_from('<I', d, off)[0]
        return off + 4 + extra
    except Exception:
        return None

def safe_rel(name):
    name = name.replace('\\', '/').lstrip('/')
    parts = [p for p in name.split('/') if p not in ('', '.', '..')]
    return '/'.join(parts)

def unpack(path, outdir, names=None, verify=False):
    b = open(path, 'rb').read()
    if len(b) < 20 or b[:4] != b'pckg':
        raise ValueError('not a pckg file')
    magic, ver, count, idxoff = struct.unpack_from('<4sIII', b, 0)
    free_count = struct.unpack_from('<I', b, 16)[0]
    if ver not in (0, 1):
        print(f'warning: unknown version {ver}; treating as plaintext', file=sys.stderr)
    if idxoff + count * 20 > len(b):
        raise ValueError('index out of bounds')
    pkgname = os.path.splitext(os.path.basename(path))[0]
    manifest = []
    stats = {'resolved': 0, 'hash_named': 0, 'bytes': 0, 'bad_header_size': 0}
    for i in range(count):
        h, hs, off, sz, reserved = struct.unpack_from('<IIIII', b, idxoff + i * 20)
        if off + sz > len(b):
            print(f'warning: entry {i} out of bounds', file=sys.stderr)
            sz = max(0, len(b) - off)
        raw = b[off:off + sz]
        data = bytes(x ^ KEY[(off + j) & 15] for j, x in enumerate(raw)) if ver == 1 else raw
        if verify and hs > 0 and len(data) >= 4 and data[:4] in COMMON_EXT:
            exp = common_head_size(data)
            if exp is not None and exp != hs:
                stats['bad_header_size'] += 1
                print(f'warning: {pkgname}[{i}] headerSize {hs} != parsed {exp}', file=sys.stderr)
        name = (names or {}).get(f'{pkgname}/{i}')
        if name:
            rel = safe_rel(name)
            stats['resolved'] += 1
        else:
            ext, _ = sniff_ext(data, hs)
            rel = f'_hash/{h:08x}.{ext}'
            stats['hash_named'] += 1
        # avoid collisions
        dest = os.path.join(outdir, rel)
        if os.path.exists(dest):
            base, ext = os.path.splitext(dest)
            dest = f'{base}_{h:08x}{ext}'
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'wb') as f:
            f.write(data)
        manifest.append({
            'index': i, 'hash': f'{h:08x}', 'name': name or '', 'path': os.path.relpath(dest, outdir).replace('\\', '/'),
            'offset': off, 'size': sz, 'header_size': hs, 'reserved': reserved,
        })
        stats['bytes'] += len(data)
    with open(os.path.join(outdir, '_manifest.json'), 'w', encoding='utf-8') as f:
        json.dump({'package': pkgname, 'version': ver, 'count': count, 'index_offset': idxoff,
                   'free_count': free_count, 'stats': stats, 'entries': manifest}, f,
                  ensure_ascii=False, indent=1)
    return stats

def main():
    ap = argparse.ArgumentParser(description='Guyu2 .pkg unpacker (Gamma pckg)')
    ap.add_argument('pkg', nargs='+')
    ap.add_argument('-o', '--out', required=True)
    ap.add_argument('--names', default=os.path.join(os.path.dirname(__file__), 'resolved_names.json'))
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args()
    names = {}
    if args.names and os.path.exists(args.names):
        names = json.load(open(args.names, encoding='utf-8'))
    pkgs = []
    for pattern in args.pkg:
        hits = glob.glob(pattern)
        pkgs.extend(hits if hits else [pattern])
    for p in pkgs:
        out = os.path.join(args.out, os.path.splitext(os.path.basename(p))[0])
        st = unpack(p, out, names, args.verify)
        print(f'{os.path.basename(p)}: {st}')

if __name__ == '__main__':
    main()
