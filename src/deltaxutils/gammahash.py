import struct

M = 0xFFFFFFFF

def rotl(x, n):
    return ((x << n) | (x >> (32 - n))) & M

def hash_bytes(data: bytes) -> int:
    length = len(data)
    a = 0x9E3779B9
    b = 0
    c = 0x9E3779B9
    i = 0
    n = length // 12
    for _ in range(n):
        k = data[i:i+12]
        w0 = int.from_bytes(k[0:4], 'little')
        w1 = int.from_bytes(k[4:8], 'little')
        w2 = int.from_bytes(k[8:12], 'little')
        v7 = (c + w1) & M
        v8 = (b + w2) & M
        v9 = ((v8 >> 13) ^ ((a + w0 - v8 - v7) & M)) & M
        v10 = ((v9 << 8) ^ ((v7 - v8 - v9) & M)) & M
        v11 = ((v10 >> 13) ^ ((v8 - v10 - v9) & M)) & M
        v12 = ((v11 >> 12) ^ ((v9 - v11 - v10) & M)) & M
        v13 = ((v12 << 16) ^ ((v10 - v11 - v12) & M)) & M
        v14 = ((v13 >> 5) ^ ((v11 - v13 - v12) & M)) & M
        a = ((v14 >> 3) ^ ((v12 - v14 - v13) & M)) & M
        c = ((a << 10) ^ ((v13 - v14 - a) & M)) & M
        b = ((c >> 15) ^ ((v14 - c - a) & M)) & M
        i += 12
    rem = length - i
    tail = data[i:]
    v15 = (length + b) & M
    # switch fallthrough semantics
    if rem >= 11: v15 = (v15 + (tail[10] << 24)) & M
    if rem >= 10: v15 = (v15 + (tail[9] << 16)) & M
    if rem >= 9:  v15 = (v15 + (tail[8] << 8)) & M
    if rem >= 8:  c = (c + (tail[7] << 24)) & M
    if rem >= 7:  c = (c + (tail[6] << 16)) & M
    if rem >= 6:  c = (c + (tail[5] << 8)) & M
    if rem >= 5:  c = (c + tail[4]) & M
    if rem >= 4:  a = (a + (tail[3] << 24)) & M
    if rem >= 3:  a = (a + (tail[2] << 16)) & M
    if rem >= 2:  a = (a + (tail[1] << 8)) & M
    if rem >= 1:  a = (a + tail[0]) & M
    v16 = ((v15 >> 13) ^ ((a - v15 - c) & M)) & M
    v17 = ((v16 << 8) ^ ((c - v15 - v16) & M)) & M
    v18 = ((v17 >> 13) ^ ((v15 - v17 - v16) & M)) & M
    v19 = ((v18 >> 12) ^ ((v16 - v18 - v17) & M)) & M
    v20 = ((v19 << 16) ^ ((v17 - v18 - v19) & M)) & M
    v21 = ((v20 >> 5) ^ ((v18 - v20 - v19) & M)) & M
    v22 = ((v21 >> 3) ^ ((v19 - v21 - v20) & M)) & M
    x = ((v22 << 10) ^ ((v20 - v21 - v22) & M)) & M
    return (((x >> 15) ^ ((v21 - x - v22) & M)) & M)

def hash_path(s: str) -> int:
    # lowercase ASCII, backslash -> slash, then UTF-16LE bytes
    out = []
    for ch in s:
        o = ord(ch)
        if 65 <= o <= 90: o += 32
        elif o == 92: o = 47
        out.append(o)
    b = b''.join(struct.pack('<H', o) for o in out)
    return hash_bytes(b)

if __name__ == '__main__':
    import sys
    for s in sys.argv[1:]:
        print(f"{hash_path(s):08x}  {s}")
