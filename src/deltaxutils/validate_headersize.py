import argparse
import glob
import os
import struct
import sys

try:
    from . import gamma_formats as gf
except ImportError:
    import gamma_formats as gf

KEY = bytes([0xE7, 0x9B, 0x9B, 0x9C, 0x4E, 0xD6, 0x44, 0x50, 0xB3,
             0x53, 0x8D, 0xF1, 0x38, 0xCC, 0xCE, 0xA9])
MAGICS = {b'tex\0', b'ams\0', b'ans\0', b'anf\0', b'mtr\0', b'gfx\0',
          b'gui\0', b'eft\0', b'map\0'}


def entries(path):
    b = open(path, 'rb').read()
    magic, ver, count, idxoff = struct.unpack_from('<4sIII', b, 0)
    out = []
    for i in range(count):
        h, hs, off, sz, _ = struct.unpack_from('<IIIII', b, idxoff + i * 20)
        data = b[off:off + sz]
        if ver == 1:
            data = bytes(x ^ KEY[(off + j) & 15] for j, x in enumerate(data))
        out.append((h, hs, off, sz, data))
    return ver, out


def validate(root):
    total = ok = raw = unknown = 0
    bad = []
    for path in sorted(glob.glob(os.path.join(root, '*.pkg'))):
        ver, es = entries(path)
        for i, (h, hs, off, sz, data) in enumerate(es):
            total += 1
            if data[:4] in MAGICS:
                try:
                    head, end = gf.CommonFileHead.parse(data, 0)
                    exp = end + head.extra_size
                    if exp == hs:
                        ok += 1
                    else:
                        bad.append((os.path.basename(path), i, f'{h:08x}', hs, exp, data[:4]))
                except Exception as e:
                    bad.append((os.path.basename(path), i, f'{h:08x}', hs, 'ERR', str(e)))
            elif hs == 0:
                raw += 1
            else:
                unknown += 1
    return dict(total=total, ok=ok, bad=bad, raw=raw, unknown=unknown)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Validate .pkg entry headerSize against CCommonFileHead')
    ap.add_argument('root', nargs='?', default=r'D:\game\guyu2\data')
    args = ap.parse_args(argv)
    st = validate(args.root)
    print('total', st['total'], 'ok', st['ok'], 'bad', len(st['bad']),
          'raw(headerSize=0)', st['raw'], 'non4cc with hs>0', st['unknown'])
    for x in st['bad'][:20]:
        print(x)
    return 0 if not st['bad'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
