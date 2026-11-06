import struct, lzma, os, sys, json, argparse

HEADER = struct.Struct('<4sIIIIII')  # magic, version, dirs, files, usize, csize, propsize
MAGIC = b'gmz\0'
VERSION = 0x2711


def _lzma_props(data):
    if len(data) < 5:
        raise ValueError('LZMA props too short')
    prop = data[0]
    lc = prop % 9
    lp = (prop // 9) % 5
    pb = prop // 45
    dict_size = int.from_bytes(data[1:5], 'little')
    return {'id': lzma.FILTER_LZMA1, 'dict_size': dict_size, 'lc': lc, 'lp': lp, 'pb': pb}


def decompress(path):
    """Return (header_dict, decompressed_manifest_bytes, raw_file_bytes)."""
    b = open(path, 'rb').read()
    if len(b) < HEADER.size:
        raise ValueError('file too short')
    magic, ver, dirs, files, usize, csize, psize = HEADER.unpack_from(b, 0)
    if magic != MAGIC:
        raise ValueError(f'bad magic {magic!r}')
    if ver != VERSION:
        raise ValueError(f'unsupported gmz version {ver:#x}')
    pos = HEADER.size
    props = b[pos:pos + psize]
    pos += psize
    stream = b[pos:pos + csize]
    pos += csize
    if pos != len(b):
        raise ValueError(f'trailing bytes: {len(b) - pos}')
    filt = _lzma_props(props)
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[filt])
    out = dec.decompress(stream, max_length=usize)
    if len(out) != usize:
        raise ValueError(f'decompressed {len(out)} != {usize}')
    hdr = dict(version=ver, dir_count=dirs, file_count=files,
               uncompressed_size=usize, compressed_size=csize, props_size=psize,
               props=props.hex(' '))
    return hdr, out, b


def parse_manifest(out, dir_count, file_count):
    pos = 0
    dirs = []
    for _ in range(dir_count):
        if pos + 4 > len(out):
            raise ValueError('manifest truncated (dir length)')
        n = struct.unpack_from('<I', out, pos)[0]
        pos += 4
        if pos + 2 * n > len(out):
            raise ValueError('manifest truncated (dir data)')
        dirs.append(out[pos:pos + 2 * n].decode('utf-16le'))
        pos += 2 * n

    files = []
    for _ in range(file_count):
        if pos + 12 > len(out):
            raise ValueError('manifest truncated (file header)')
        plen, blocks, newsize = struct.unpack_from('<III', out, pos)
        pos += 12
        if pos + 2 * plen > len(out):
            raise ValueError('manifest truncated (file path)')
        path = out[pos:pos + 2 * plen].decode('utf-16le')
        pos += 2 * plen
        bl = []
        for _ in range(blocks):
            if pos + 8 > len(out):
                raise ValueError('manifest truncated (block header)')
            size, offset = struct.unpack_from('<II', out, pos)
            pos += 8
            if pos + size > len(out):
                raise ValueError('manifest truncated (block data)')
            bl.append((size, offset, out[pos:pos + size]))
            pos += size
        files.append(dict(path=path, new_size=newsize, block_count=blocks, blocks=bl))
    if pos != len(out):
        raise ValueError(f'manifest has {len(out) - pos} trailing bytes')
    return dirs, files


def parse(path):
    hdr, out, raw = decompress(path)
    dirs, files = parse_manifest(out, hdr['dir_count'], hdr['file_count'])
    return hdr, dirs, files, out


def info(path):
    hdr, dirs, files, out = parse(path)
    print(os.path.basename(path))
    print(f"  version={hdr['version']:#x} dirs={hdr['dir_count']} files={hdr['file_count']} "
          f"usize={hdr['uncompressed_size']} csize={hdr['compressed_size']} "
          f"props={hdr['props']}")
    print(f"  dirs: {', '.join(dirs)}")
    for f in files:
        print(f"  {f['path']!r} new_size={f['new_size']} blocks={f['block_count']} "
              f"patch_bytes={sum(len(d) for _, _, d in f['blocks'])}")


def dump(path, outdir, dump_blocks=False):
    hdr, dirs, files, out = parse(path)
    os.makedirs(outdir, exist_ok=True)
    manifest = {'header': hdr, 'dirs': dirs, 'files': []}
    base = os.path.splitext(os.path.basename(path))[0]
    for fi, f in enumerate(files):
        manifest['files'].append({
            'path': f['path'], 'new_size': f['new_size'], 'block_count': f['block_count'],
            'patch_bytes': sum(len(d) for _, _, d in f['blocks']),
        })
        if dump_blocks:
            bdir = os.path.join(outdir, f'blocks_{fi:04d}')
            os.makedirs(bdir, exist_ok=True)
            for bi, (size, offset, data) in enumerate(f['blocks']):
                with open(os.path.join(bdir, f'{bi:05d}_{offset:08x}.bin'), 'wb') as fp:
                    fp.write(data)
    with open(os.path.join(outdir, base + '.manifest.json'), 'w', encoding='utf-8') as fp:
        json.dump(manifest, fp, ensure_ascii=False, indent=1)
    with open(os.path.join(outdir, base + '.manifest.bin'), 'wb') as fp:
        fp.write(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Guyu2 .gmz patch parser')
    ap.add_argument('command', choices=['info', 'dump'])
    ap.add_argument('gmz', nargs='+')
    ap.add_argument('-o', '--out', help='output directory for dump')
    ap.add_argument('--blocks', action='store_true', help='dump each patch block separately')
    args = ap.parse_args(argv)
    if args.command == 'info':
        for p in args.gmz:
            info(p)
    else:
        if not args.out:
            ap.error('dump requires -o/--out')
        for p in args.gmz:
            dump(p, args.out, args.blocks)


if __name__ == '__main__':
    main()
