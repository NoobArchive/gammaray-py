#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gamma 模型工具: .ams -> GLB。

格式来源: libShellClient.so
  * CAniGroup = "ams" 资源 (GetFileType 返回 "ams")
  * CStaticPiece::ReadMainData / WriteMainData
  * CTinyVertex<10,11,1> / TTinyNormal<12> / TBitSet<64>::GetBit

ams 主数据(单 piece, 与文件长度精确吻合):
  u16   ?(通常 0)
  u16   vertexCount
  u8    vertices[vertexCount * stride]   (stride=16 时为 8B 打包顶点 + 8B 蒙皮)
  u32   indexCount
  u16/u32 indices[]                     (vertexCount>255 ? u16 : u8)

打包顶点(64 bit):
  pos.x = bits[0:10]  * 0.25
  pos.y = bits[10:20] * 0.25
  pos.z = bits[20:30] * 0.25
  normal = TTinyNormal<12>(bits[30:42])
  uv.u  = bits[42:53] * 0.0005
  uv.v  = bits[53:64] * 0.0005

用法:
  python gamma_models.py ams2glb <in.ams|dir> <out.glb|outdir>
"""
import glob
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gamma_formats as g


def _getbit(buf, pos, n):
    byte = pos >> 3
    shift = pos & 7
    need = (shift + n + 7) // 8
    v = int.from_bytes(buf[byte:byte + need], "little")
    return (v >> shift) & ((1 << n) - 1)


def tiny_normal12(n12):
    v2 = n12 & 0x3FF
    s1 = n12 & 0x400
    s2 = n12 & 0x800
    if v2 == 0:
        return (0.0, -1.0 if s1 else 1.0, 0.0)
    m = int(math.sqrt(v2))
    a = m * 0.050671
    b = (v2 - m * m) * 3.1416 / (2 * m) if m else 0.0
    sa, ca = math.sin(a), math.cos(a)
    sb, cb = math.sin(b), math.cos(b)
    x = sa * sb
    y = ca
    z = sa * cb
    if s2:
        x = -x
    if s1:
        y = -y
    return (x, y, z)


VARIANTS = {
    # packed bytes -> (pos_start, bits_x, bits_y, normal_bit, uv_u_bit, uv_u_bits, uv_v_bit, uv_v_bits)
    8:  (0, 10, 10, 30, 42, 11, 53, 11),
    9:  (0, 12, 12, 36, 48, 12, 60, 12),
    10: (0, 12, 12, 36, 48, 16, 64, 16),
    11: (0, 14, 14, 42, 54, 16, 70, 16),
    12: (0, 16, 16, 48, 60, 16, 76, 16),
}


def _decode_vertices(main, vcount, packed):
    st, bx, by, nbit, ub, unb, vb, vnb = VARIANTS[packed]
    pos, nor, uv = [], [], []
    for i in range(vcount):
        b = main[4 + i * packed:4 + i * packed + packed]
        pos.append((_getbit(b, st, bx) * 0.25,
                    _getbit(b, st + bx, by) * 0.25,
                    _getbit(b, st + bx + by, bx) * 0.25))
        nor.append(tiny_normal12(_getbit(b, nbit, 12)))
        uv.append((_getbit(b, ub, unb) * 0.0005,
                   _getbit(b, vb, vnb) * 0.0005))
    return pos, nor, uv


def _piece_candidates(main, pos, expect=None):
    """在 pos 处尝试解析一个 piece,返回所有合法候选。

    `expect` 是该 piece 的包围盒信息(来自 .ams extra 的 size):
    有些特效模型 UV 大量超出 [0,1](平铺),仅靠 UV 校验会误杀,
    此时改用「解出的位置范围 ≈ 0..size」作为判据。
    """
    if pos + 4 > len(main) or main[pos:pos + 2] != b"\x00\x00":
        return []
    vcount = struct.unpack_from("<H", main, pos + 2)[0]
    if vcount == 0:
        return []
    isz = 2 if vcount > 0xFF else 1
    out = []
    for packed in VARIANTS:
        for skin in (8, 0):
            vp = pos + 4 + vcount * packed + vcount * skin
            if vp + 4 > len(main):
                continue
            ic = struct.unpack_from("<I", main, vp)[0]
            if ic == 0:
                continue
            end = vp + 4 + ic * isz
            if end > len(main):
                continue
            try:
                p, n, uv = _decode_vertices(main[pos:], vcount, packed)
            except Exception:
                continue
            # piece 的 bits 字段 = 顶点缩放指数:pos /= 2^bits(实测 bits=4 时 953/16≈59.6)
            bits = (expect or {}).get("bits", 0) or 0
            if bits > 0:
                f = 1.0 / float(1 << min(bits, 20))
                p = [(x * f, y * f, z * f) for (x, y, z) in p]
            uv_ok = sum(1 for u, v in uv if 0 <= u <= 1.02 and 0 <= v <= 1.02) / vcount
            no_ok = sum(1 for x in n
                        if abs(math.sqrt(x[0]**2 + x[1]**2 + x[2]**2) - 1) < 0.02) / vcount
            bbox_ok = False
            if expect:
                size = expect.get("size") or (0, 0, 0)
                tol = [max(1.0, 0.02 * abs(s)) for s in size]
                bbox_ok = True
                for k in range(3):
                    lo = min(q[k] for q in p)
                    hi = max(q[k] for q in p)
                    if abs(lo) > tol[k] or abs(hi - size[k]) > tol[k]:
                        bbox_ok = False
                        break
            if not (bbox_ok or (uv_ok >= 0.9 and no_ok >= 0.9)):
                continue
            score = uv_ok + no_ok
            if bbox_ok:
                score = max(score, 2.0)
            out.append((score, packed, skin, vcount, ic, vp + 4, end, p, n, uv))
    return out


def tiny_normal32(n):
    v = n & 0x3FFFFFFF
    s1 = n & 0x40000000
    s2 = n & 0x80000000
    if v == 0:
        return (0.0, -1.0 if s1 else 1.0, 0.0)
    m = int(math.sqrt(v))
    a = m * 0.000047938
    b = (v - m * m) * 3.1416 / (2 * m) if m else 0.0
    sa, ca = math.sin(a), math.cos(a)
    sb, cb = math.sin(b), math.cos(b)
    x = sa * sb
    y = ca
    z = sa * cb
    if s2:
        x = -x
    if s1:
        y = -y
    return (x, y, z)


def _read_ustr(b, p):
    n = struct.unpack_from("<I", b, p)[0]
    p += 4
    return b[p:p + 2 * n].decode("utf-16-le", "replace"), p + 2 * n


def parse_ans_skeleton(path):
    """解析 .ans 的骨架名与父子关系。"""
    data = open(path, "rb").read()
    head, off = g.CommonFileHead.parse(data, 0)
    extra = data[off:off + head.extra_size]
    n = struct.unpack_from("<H", extra, 0)[0]
    p = 2
    recs = []
    for _ in range(n):
        name, p = _read_ustr(extra, p)
        b1, b2, b3 = extra[p], extra[p + 1], extra[p + 2]
        p += 3
        kids = list(extra[p:p + b3])
        p += b3
        for _ in range(b2):
            _, p = _read_ustr(extra, p)
            p += 48
        recs.append({"name": name, "kids": kids})
    parent = [-1] * n
    for i, r in enumerate(recs):
        for k in r["kids"]:
            if 0 <= k < n:
                parent[k] = i
    return recs, parent


def anf_quat(axis32, ang):
    ax = tiny_normal32(axis32)
    a = ang * math.pi / 65535.0
    sa = math.sin(a)
    return (sa * ax[0], sa * ax[1], sa * ax[2], math.cos(a))


def parse_ans_anf_table(path):
    """解析 `.ans` 尾部的动画表:每个 clip 的 (a,b,kc,events)。

    条目布局:``u32 a, u16 b, u16 kc, kc × 8B``。`kc` 是事件数;
    本作 93 个 `.ans`、568 个 clip 全部 `kc=0`,事件表为空。
    """
    data = open(path, "rb").read()
    head, off = g.CommonFileHead.parse(data, 0)
    extra = data[off:off + head.extra_size]
    deps = {t.rstrip(b"\x00").decode("latin1"): list(ns) for t, ns in head.deps}
    n = struct.unpack_from("<H", extra, 0)[0]
    p = 2
    for _ in range(n):
        _, p = _read_ustr(extra, p)
        b1, b2, b3 = extra[p], extra[p + 1], extra[p + 2]
        p += 3 + b3
        for _ in range(b2):
            _, p = _read_ustr(extra, p)
            p += 48
    out = []
    for name in deps.get("anf", []):
        a, b = struct.unpack_from("<IH", extra, p)
        p += 6
        kc = struct.unpack_from("<H", extra, p)[0]
        p += 2
        events = [extra[p + 8 * i:p + 8 * (i + 1)].hex() for i in range(kc)]
        p += 8 * kc
        out.append({"clip": name, "a": a, "b": b, "kc": kc, "events": events})
    return out


def parse_ans_bind(path):
    """解析 .ans 尾部每骨 52 字节的绑定数据。

    CAniGroup::_OnReadMainData 读取顺序::

        f32 x3  size        骨骼包围盒(不参与矩阵)
        f32 x3  trans       局部平移(子骨相对父骨)
        f32 x4  quat        世界绑定旋转(引擎里取共轭才是 glTF 世界旋转)
        f32 x3  inv_trans   世界逆绑定平移 D

    引擎按 ``CQuaternion→CMatrix`` 生成 R,再用 R^T 拼 inverseBind,平移直接取 D。
    实测 ``t_world = -R^T·D`` 且 ``t_i = t_parent + R_parent^T·B_i`` 在 97 根骨上
    误差 < 1e-4,即::

        world_rot_i  = R_i^T
        inv_bind_i   = [R_i | D_i]
        local_rot_i  = R_parent · R_i^T
    """
    data = open(path, "rb").read()
    head, off = g.CommonFileHead.parse(data, 0)
    extra = data[off:off + head.extra_size]
    deps = {t.rstrip(b"\x00").decode("latin1"): list(ns) for t, ns in head.deps}
    n = struct.unpack_from("<H", extra, 0)[0]
    p = 2
    for _ in range(n):
        _, p = _read_ustr(extra, p)
        b1, b2, b3 = extra[p], extra[p + 1], extra[p + 2]
        p += 3
        p += b3
        for _ in range(b2):
            _, p = _read_ustr(extra, p)
            p += 48
    for _ in range(len(deps.get("anf", []))):
        p += 4
        p += 2
        kc = struct.unpack_from("<H", extra, p)[0]
        p += 2 + kc * 8
    fig = struct.unpack_from("<H", extra, p)[0]
    p += 2 + 2 * fig
    bones = []
    for _ in range(n):
        b = extra[p:p + 52]
        p += 52
        size = struct.unpack_from("<3f", b, 0)
        trans = struct.unpack_from("<3f", b, 12)
        quat = struct.unpack_from("<4f", b, 24)
        inv_trans = struct.unpack_from("<3f", b, 40)
        bones.append({"size": size, "trans": trans, "quat": quat,
                      "inv_trans": inv_trans, "scale": (1.0, 1.0, 1.0)})
    return bones


# ---- 4x4 矩阵工具(行主序;写 glTF 时转置) ----
def _qmat(q):
    x, y, z, w = q
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return [[1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy), 0.0],
            [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx), 0.0],
            [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy), 0.0],
            [0.0, 0.0, 0.0, 1.0]]


def _trs(t, q, s):
    m = _qmat(q)
    for i in range(3):
        for j in range(3):
            m[i][j] *= s[j]
    m[0][3], m[1][3], m[2][3] = t[0], t[1], t[2]
    return m


def _mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)]
            for i in range(4)]


def _mat_inv(m):
    a = [list(row) + [1.0 if i == j else 0.0 for j in range(4)]
         for i, row in enumerate(m)]
    for i in range(4):
        p = max(range(i, 4), key=lambda r: abs(a[r][i]))
        if abs(a[p][i]) < 1e-12:
            return None
        a[i], a[p] = a[p], a[i]
        d = a[i][i]
        a[i] = [v / d for v in a[i]]
        for r in range(4):
            if r != i:
                f = a[r][i]
                a[r] = [a[r][c] - f * a[i][c] for c in range(8)]
    return [row[4:] for row in a]


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def _qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def _qnorm(q):
    """归一化并夹到 [-1,1],避免 glTF 校验器 VALUE_NOT_IN_RANGE。"""
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n < 1e-9:
        return (0.0, 0.0, 0.0, 1.0)
    x, y, z, w = x / n, y / n, z / n, w / n
    c = lambda v: -1.0 if v < -1.0 else (1.0 if v > 1.0 else v)
    return (c(x), c(y), c(z), c(w))


def bind_node_trs(bind, parents, njoints):
    """把 .ans 绑定数据转成 glTF 关节的局部 TRS。

    世界旋转 = conj(C),故局部旋转 = C_parent · conj(C_child);平移直接用 B。
    """
    out = []
    for i in range(njoints):
        pa = parents[i]
        cp = bind[pa]["quat"] if 0 <= pa < njoints else (0.0, 0.0, 0.0, 1.0)
        out.append({
            "translation": list(bind[i]["trans"]),
            "rotation": list(_qnorm(_qmul(cp, _qconj(bind[i]["quat"])))),
            "scale": [1.0, 1.0, 1.0],
        })
    return out


def bind_inverse_matrices(bind, njoints):
    """引擎里 inverseBind 就存在 .ans 里:[R | D](R 来自 quat,D 为世界逆平移)。

    返回列主序 16 浮点数组,可直接写 glTF MAT4。
    """
    mats = []
    for i in range(njoints):
        R = _qmat(bind[i]["quat"])
        D = bind[i]["inv_trans"]
        mats.append([R[0][0], R[1][0], R[2][0], 0.0,
                     R[0][1], R[1][1], R[2][1], 0.0,
                     R[0][2], R[1][2], R[2][2], 0.0,
                     D[0], D[1], D[2], 1.0])
    return mats


def parse_anf(path, bones):
    """解析 .anf;返回 (transScale, scaleScale, axisCount, tracks)。

    `.anf` 的骨骼表长度**不一定等于骨架骨数**:有些 clip 只动画前 N 根骨
    (例如 `p_001_attack_1` 只有 28 项,而 `p_001.ans` 是 29 根)。
    关键帧时间戳也会变宽:帧数 > 255 的 clip 用 u16(如 `l_p_003b_stand_rest1` 282 帧)。
    这里先用骨架骨数试,失败则按「恰好读完 main」反推 (boneCount, axis, timeWidth)。
    """
    data = open(path, "rb").read()
    head, off = g.CommonFileHead.parse(data, 0)
    main = data[off + head.extra_size:]
    if len(main) < 8:
        return None
    tscale = struct.unpack_from("<f", main, 0)[0]
    sscale = struct.unpack_from("<f", main, 4)[0]
    total = len(main)

    def attempt(nb, axis, tw):
        if nb <= 0 or 8 + 6 * nb > total:
            return None
        try:
            headers = [struct.unpack_from("<3H", main, 8 + 6 * i) for i in range(nb)]
        except Exception:
            return None
        # 先用长度方程快速排除,再真正解析关键帧
        need = 8 + 6 * nb
        for (nS, nR, nT) in headers:
            if nS > 65535 or nR > 65535 or nT > 65535:
                return None
            need += nS * (tw + 2 * axis) + nR * (tw + 6) + nT * (tw + 6)
        if need != total:
            return None
        p = 8 + 6 * nb
        out = []
        try:
            for (nS, nR, nT) in headers:
                kS = []
                for _ in range(nS):
                    if tw == 1:
                        t = main[p]
                        p += 1
                    else:
                        t = struct.unpack_from("<H", main, p)[0]
                        p += 2
                    vals = [struct.unpack_from("<H", main, p + 2 * k)[0] for k in range(axis)]
                    p += 2 * axis
                    kS.append((t, vals))
                kR = []
                for _ in range(nR):
                    if tw == 1:
                        t = main[p]
                        p += 1
                    else:
                        t = struct.unpack_from("<H", main, p)[0]
                        p += 2
                    kR.append((t, struct.unpack_from("<I", main, p)[0],
                               struct.unpack_from("<H", main, p + 4)[0]))
                    p += 6
                kT = []
                for _ in range(nT):
                    if tw == 1:
                        t = main[p]
                        p += 1
                    else:
                        t = struct.unpack_from("<H", main, p)[0]
                        p += 2
                    kT.append((t, struct.unpack_from("<I", main, p)[0],
                               struct.unpack_from("<H", main, p + 4)[0]))
                    p += 6
                out.append((nS, nR, nT, kS, kR, kT))
        except Exception:
            return None
        if p != total:
            return None
        # 补齐到骨架骨数:超出部分引擎回退(单位旋转 + 绑定平移 + 缩放 1)
        empty = (0, 0, 0, [], [], [])
        out = out[:bones] + [empty] * max(0, bones - len(out))
        return out

    for tw in (1, 2):
        for axis in (3, 1):
            out = attempt(bones, axis, tw)
            if out is not None:
                return tscale, sscale, axis, out

    limit = max(1, min(2048, total // 7))
    for tw in (1, 2):
        for axis in (3, 1):
            for nb in range(1, limit + 1):
                if nb == bones:
                    continue
                out = attempt(nb, axis, tw)
                if out is not None:
                    return tscale, sscale, axis, out
    return None


def parse_ams_extra(extra, version):
    """解析 .ams 的 extra(index data),返回每个 piece 的包围盒与材质表。

    ``CPieceGroup::OnReadIndexData`` + ``CPiece::ReadIndexData`` 的顺序::

        u16 pieceClassCount
        per class:  u32 nameLen, utf16 name, u16 pieceCount
            per piece:  u8 type
                        [ver > 0x2715] f32 f16, f32 err, u8 bits, u8 profile
                        f32x3 v44, f32x3 v56      // = size(extent), center
                        [ver > 0x2711] f32x3, f32x3   // 与上二者相同
                        [ver > 0x2712] u8 matCount, per mat: u16 tag, u8 subs, per sub: u8 n, u16[n]

    顶点量化范围就是这块盒:``pos = q*0.25 + (center - size*0.5)``。
    """
    p = 0
    out = []

    def u8():
        nonlocal p
        v = extra[p]
        p += 1
        return v

    def u16():
        nonlocal p
        v = struct.unpack_from("<H", extra, p)[0]
        p += 2
        return v

    def u32():
        nonlocal p
        v = struct.unpack_from("<I", extra, p)[0]
        p += 4
        return v

    def f32():
        nonlocal p
        v = struct.unpack_from("<f", extra, p)[0]
        p += 4
        return v

    def v3():
        return (f32(), f32(), f32())

    ncls = u16()
    for _ in range(ncls):
        nl = u32()
        p += 2 * nl
        npiece = u16()
        for _ in range(npiece):
            rec = {}
            if version > 0x2715:
                rec["type"] = u8()
                rec["f16"] = f32()
                rec["err"] = f32()
                rec["bits"] = u8()
                if version != 10006:
                    rec["profile"] = u8()
            size = v3()
            center = v3()
            if version > 0x2711:
                v3()
                v3()
            mats = []
            if version > 0x2712:
                for _ in range(u8()):
                    tag = u16()
                    for _ in range(u8()):
                        cnt = u8() if version > 0x2713 else 1
                        mats.append((tag, [u16() for _ in range(cnt)]))
            rec["size"] = size
            rec["center"] = center
            rec["offset"] = tuple(center[i] - size[i] * 0.5 for i in range(3))
            rec["mats"] = mats
            out.append(rec)
    return out


def parse_ams(path, ani_root=None, skin_variant=0):
    data = open(path, "rb").read()
    head, off = g.CommonFileHead.parse(data, 0)
    main = data[off + head.extra_size:]
    if len(main) < 8:
        return None

    from functools import lru_cache

    try:
        _extra = data[off:off + head.extra_size]
        _metas = parse_ams_extra(_extra, head.version)
    except Exception:
        _metas = []

    @lru_cache(maxsize=None)
    def best(pos, depth=0):
        if pos == len(main):
            return (0.0, ())
        expect = _metas[depth] if depth < len(_metas) else None
        cands = _piece_candidates(main, pos, expect)
        bestres = None
        for c in sorted(cands, key=lambda x: x[0], reverse=True):
            rest = best(c[6], depth + 1)
            if rest is None:
                continue
            total = c[0] + rest[0]
            if bestres is None or total > bestres[0]:
                bestres = (total, (c,) + rest[1])
        return bestres

    res = best(0)
    if res is None or res[0] < 1.5:
        return None
    pieces = res[1]
    name = os.path.splitext(os.path.basename(path))[0]
    skeleton = None
    ans_path = None
    if ani_root:
        ap = os.path.join(ani_root, name + ".ans")
        if not os.path.exists(ap):
            # 共享骨架回退:
            #   p_*_a 变体 -> bind.ans(28 骨人形)
            #   box02/03 -> box01.ans(箱子共享 9 骨)
            cands = []
            if name.endswith("_a"):
                cands.append("bind.ans")
            if name.startswith("box"):
                cands.append("box01.ans")
            for cn in cands:
                cp = os.path.join(ani_root, cn)
                if os.path.exists(cp):
                    ap = cp
                    break
        if os.path.exists(ap):
            ans_path = ap
            recs, parent = parse_ans_skeleton(ap)
            try:
                bind = parse_ans_bind(ap)
            except Exception:
                bind = None
            skeleton = {"names": [r["name"] for r in recs], "parents": parent,
                        "bind": bind, "path": ap}

    positions, normals, uvs, indices = [], [], [], []
    piece_list = []
    base = 0
    try:
        extra_bytes = data[off:off + head.extra_size]
        piece_meta = parse_ams_extra(extra_bytes, head.version)
        if len(piece_meta) != len(pieces):
            piece_meta = None
    except Exception:
        piece_meta = None
    for pi_piece, (_sc, packed, skin, vcount, ic, ip, end, p, n, uv) in enumerate(pieces):
        isz = 2 if vcount > 0xFF else 1
        fmt = "<H" if isz == 2 else "<B"
        idx = [struct.unpack_from(fmt, main, ip + i * isz)[0] for i in range(ic)]
        off_v = (0.0, 0.0, 0.0)
        tex_variants = [0]
        if piece_meta:
            off_v = piece_meta[pi_piece]["offset"]
            p = [(x + off_v[0], y + off_v[1], z + off_v[2]) for (x, y, z) in p]
            tv = [m[1][0] for m in piece_meta[pi_piece]["mats"] if m[1]]
            if tv:
                tex_variants = tv
        tex_index = tex_variants[skin_variant] if skin_variant < len(tex_variants) else tex_variants[0]
        rec = {"packed": packed, "skin": skin, "vcount": vcount,
               "positions": p, "normals": n, "uvs": uv, "indices": idx,
               "offset": off_v, "tex_index": tex_index,
               "tex_variants": tex_variants}
        if skin and skeleton:
            sbase = ip - 4 - vcount * skin
            jj, jw = [], []
            for i in range(vcount):
                w = struct.unpack_from("<I", main, sbase + 8 * i)[0]
                ix = struct.unpack_from("<I", main, sbase + 8 * i + 4)[0]
                jw.append([(w >> (8 * k)) & 0xFF for k in range(4)])
                jj.append([(ix >> (8 * k)) & 0xFF for k in range(4)])
            rec["joints"] = jj
            rec["weights"] = jw
        piece_list.append(rec)
        positions += p
        normals += n
        uvs += uv
        indices += [base + x for x in idx]
        base += vcount

    deps = {t.rstrip(b"\x00").decode("latin1"): list(ns) for t, ns in head.deps}
    first = pieces[0]
    return {"name": name, "deps": deps, "packed": first[1], "skin": first[2],
            "pieces": piece_list, "positions": positions, "normals": normals,
            "uvs": uvs, "indices": indices, "version": head.version,
            "skeleton": skeleton, "ans_path": ans_path}


def write_glb(path, mesh, textures=None, flip_v=False, anims=None, fps=30.0,
              mat_opts=None):
    """textures: [(name, png_bytes), ...]; 第 i 个 piece 用第 i 张贴图。

    mat_opts 来自 `.mtr`:`{"alphaMode": "OPAQUE"/"BLEND"/"ADDITIVE",
    "doubleSided": bool}`。glTF 没有加色混合, ADDITIVE 会写成 BLEND + extras。
    """
    textures = textures or []
    mat_opts = mat_opts or {}
    alpha_mode = mat_opts.get("alphaMode", "OPAQUE")
    extras = {}
    if alpha_mode == "ADDITIVE":
        extras["blendMode"] = "ADDITIVE"
        alpha_mode = "BLEND"
    double_sided = bool(mat_opts.get("doubleSided", True))
    bin_ = bytearray()
    views, accessors = [], []
    images, texs, mats, prims = [], [], [], []

    def add_view(data, target=None):
        while len(bin_) % 4:
            bin_.append(0)
        off = len(bin_)
        bin_.extend(data)
        v = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target:
            v["target"] = target
        views.append(v)
        return len(views) - 1

    def add_acc(view, ctype, count, atype, mm=None):
        a = {"bufferView": view, "componentType": ctype, "count": count, "type": atype}
        if mm:
            a.update(mm)
        accessors.append(a)
        return len(accessors) - 1

    # 只嵌入真正用到的贴图(每个 piece 用自己材质表里选中的那张;
    # 同一套 atlas 的其它编号是配色变体,默认不导出)
    used_tex = []
    for pc in mesh["pieces"]:
        ti = pc.get("tex_index", 0)
        if 0 <= ti < len(textures) and ti not in used_tex:
            used_tex.append(ti)
    tex_mat = {}
    samplers = []
    if used_tex:
        samplers = [{"magFilter": 9729, "minFilter": 9987,
                     "wrapS": 10497, "wrapT": 10497}]
        for ti in used_tex:
            tname, tdata = textures[ti]
            vi = add_view(tdata)
            images.append({"bufferView": vi, "mimeType": "image/png", "name": tname})
            texs.append({"source": len(images) - 1, "sampler": 0})
            mats.append({"name": tname, "pbrMetallicRoughness": {
                "baseColorTexture": {"index": len(texs) - 1},
                "metallicFactor": 0.0, "roughnessFactor": 1.0},
                "alphaMode": alpha_mode, "doubleSided": double_sided,
                **({"extras": extras} if extras else {})})
            tex_mat[ti] = len(mats) - 1
    else:
        mats.append({"name": "default", "pbrMetallicRoughness": {
            "baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.0,
            "roughnessFactor": 1.0},
            "alphaMode": alpha_mode, "doubleSided": double_sided,
            **({"extras": extras} if extras else {})})

    skel = mesh.get("skeleton")
    njoints = len(skel["names"]) if skel else 0
    inv_acc = None
    bind = skel.get("bind") if skel else None
    if skel:
        if bind and len(bind) == njoints:
            inv_mats = bind_inverse_matrices(bind, njoints)
            data = b"".join(struct.pack("<16f", *m) for m in inv_mats)
        else:
            data = struct.pack("<16f", 1, 0, 0, 0, 0, 1, 0, 0,
                               0, 0, 1, 0, 0, 0, 0, 1) * njoints
        inv_acc = add_acc(add_view(data), 5126, njoints, "MAT4")

    for pi, piece in enumerate(mesh["pieces"]):
        pos, nor, uv, idx = (piece["positions"], piece["normals"],
                             piece["uvs"], piece["indices"])
        if not pos or not idx:
            continue
        vcount = len(pos)
        vpos = add_view(b"".join(struct.pack("<3f", *p) for p in pos), 34962)
        vnor = add_view(b"".join(struct.pack("<3f", *n) for n in nor), 34962)
        mat = tex_mat.get(piece.get("tex_index", 0), 0)
        has_tex = mat in tex_mat.values()
        use32 = vcount > 0xFFFF
        idata = b"".join(struct.pack("<I" if use32 else "<H", i) for i in idx)
        vidx = add_view(idata, 34963)
        mn = [min(p[k] for p in pos) for k in range(3)]
        mx = [max(p[k] for p in pos) for k in range(3)]
        apos = add_acc(vpos, 5126, vcount, "VEC3", {"min": mn, "max": mx})
        anor = add_acc(vnor, 5126, vcount, "VEC3")
        aidx = add_acc(vidx, 5125 if use32 else 5123, len(idx), "SCALAR")
        attrs = {"POSITION": apos, "NORMAL": anor}
        if has_tex:
            if flip_v:
                uvdata = b"".join(struct.pack("<2f", u, 1.0 - v) for u, v in uv)
            else:
                uvdata = b"".join(struct.pack("<2f", u, v) for u, v in uv)
            attrs["TEXCOORD_0"] = add_acc(add_view(uvdata, 34962), 5126, vcount, "VEC2")
        if skel and "joints" in piece:
            jj, ww = piece["joints"], piece["weights"]
            jfmt = "<4H" if njoints > 255 else "<4B"
            jctype = 5123 if njoints > 255 else 5121
            jdata = b"".join(struct.pack(jfmt, *j) for j in jj)
            wdata = bytearray()
            for ws in ww:
                s = float(sum(ws))
                vals = [w / s for w in ws] if s else [1.0, 0.0, 0.0, 0.0]
                wdata += struct.pack("<4f", *vals)
            aj = add_acc(add_view(jdata, 34962), jctype, vcount, "VEC4")
            aw = add_acc(add_view(bytes(wdata), 34962), 5126, vcount, "VEC4")
            attrs["JOINTS_0"] = aj
            attrs["WEIGHTS_0"] = aw
        prims.append({"attributes": attrs, "indices": aidx,
                      "material": mat, "mode": 4})

    while len(bin_) % 4:
        bin_.append(0)
    gltf = {
        "asset": {"version": "2.0", "generator": "gamma_models.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": mesh["name"]}],
        "meshes": [{"name": mesh["name"], "primitives": prims}],
        "materials": mats,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(bin_)}],
    }
    if skel:
        children = [[] for _ in range(njoints)]
        roots = []
        for i, pa in enumerate(skel["parents"]):
            if 0 <= pa < njoints:
                children[pa].append(i + 1)
            else:
                roots.append(i + 1)
        nodes = [{"mesh": 0, "name": mesh["name"], "skin": 0}]
        trs = bind_node_trs(bind, skel["parents"], njoints) if bind else None
        for i in range(njoints):
            nd = {"name": skel["names"][i] or f"joint{i}"}
            if trs and i < len(trs):
                nd.update(trs[i])
            if children[i]:
                nd["children"] = children[i]
            nodes.append(nd)
        gltf["nodes"] = nodes
        gltf["scenes"] = [{"nodes": [0] + roots}]
        gltf["skins"] = [{"joints": list(range(1, njoints + 1)),
                          "inverseBindMatrices": inv_acc}]
    if texs:
        gltf["images"] = images
        gltf["textures"] = texs
        gltf["samplers"] = samplers

    if anims and skel:
        anim_list = []
        for a in anims:
            if isinstance(a, (tuple, list)):
                aname, apath = a[0], a[1]
            else:
                apath = a
                aname = os.path.splitext(os.path.basename(a))[0]
            parsed = parse_anf(apath, njoints)
            if not parsed:
                continue
            ts, ss, axis, tracks = parsed
            a_samplers, a_channels = [], []

            def _add_anim(bone, path, times, vals, vtype, comp):
                ti = add_acc(add_view(b"".join(struct.pack("<f", t) for t in times)),
                             5126, len(times), "SCALAR",
                             {"min": [min(times)], "max": [max(times)]})
                if comp == 4:
                    vo = b"".join(struct.pack("<4f", *v) for v in vals)
                else:
                    vo = b"".join(struct.pack("<3f", *v) for v in vals)
                to = add_acc(add_view(vo), 5126, len(vals), vtype)
                a_samplers.append({"input": ti, "output": to,
                                   "interpolation": "LINEAR"})
                a_channels.append({"sampler": len(a_samplers) - 1,
                                   "target": {"node": 1 + bone, "path": path}})

            for i, tr in enumerate(tracks):
                nS, nR, nT, kS, kR, kT = tr
                if nR:
                    times = [k[0] / fps for k in kR]
                    qs = [_qnorm(anf_quat(axis32, ang)) for (_, axis32, ang) in kR]
                    _add_anim(i, "rotation", times, qs, "VEC4", 4)
                else:
                    # TAnimation::GetFrameData: 无 rotation 通道时回退单位四元数
                    _add_anim(i, "rotation", [0.0], [(0.0, 0.0, 0.0, 1.0)],
                              "VEC4", 4)
                if nT:
                    times = [k[0] / fps for k in kT]
                    vs = []
                    for (_, axis32, ln) in kT:
                        ax = tiny_normal32(axis32)
                        L = ln * ts / 65535.0
                        vs.append((ax[0] * L, ax[1] * L, ax[2] * L))
                    _add_anim(i, "translation", times, vs, "VEC3", 3)
                if nS:
                    times = [k[0] / fps for k in kS]
                    vs = []
                    for (_, vals) in kS:
                        if axis == 1:
                            v = vals[0] * ss / 65535.0
                            vs.append((v, v, v))
                        else:
                            vs.append(tuple(x * ss / 65535.0 for x in vals))
                    _add_anim(i, "scale", times, vs, "VEC3", 3)
            if a_samplers:
                anim_list.append({
                    "name": aname,
                    "samplers": a_samplers, "channels": a_channels})
        if anim_list:
            gltf["animations"] = anim_list

    while len(bin_) % 4:
        bin_.append(0)
    gltf["buffers"] = [{"byteLength": len(bin_)}]
    j = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    while len(j) % 4:
        j += b" "
    total = 12 + 8 + len(j) + 8 + len(bin_)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(j), 0x4E4F534A))
        f.write(j)
        f.write(struct.pack("<II", len(bin_), 0x004E4942))
        f.write(bin_)


def load_textures(mesh, tex_root):
    out = []
    if not tex_root:
        return out
    for name in mesh["deps"].get("tex", []):
        rel = os.path.splitext(name)[0] + ".png"
        p = os.path.join(tex_root, rel.replace("/", os.sep))
        if os.path.exists(p):
            with open(p, "rb") as f:
                out.append((os.path.basename(name), f.read()))
    return out


def ans_clip_paths(ans_path, include_orphans=False):
    """从 `.ans` 的 `anf` 依赖里取出该模型的全部 clip,返回 [(名字, 路径)]。

    `.ans` 里记的是 `./run_w.anf` 这种“相对”名字,而解包出来的文件叫
    `<模型>_run_w.anf`,所以按三种候选依次找。

    `include_orphans` 会补上同模型目录下**未登记在 .ans** 的 `<stem>_*.anf`
    (如 `p_001_attack_1.anf`);但会跳过属于另一个更长骨架名的文件
    (如 `p_022_a_*` 属于 `p_022_a.ans`,不补进 `p_022`)。
    """
    data = open(ans_path, "rb").read()
    head, off = g.CommonFileHead.parse(data, 0)
    stem = os.path.splitext(os.path.basename(ans_path))[0]
    base = os.path.dirname(ans_path)
    out = []
    seen = set()
    for tag, names in head.deps:
        if tag.rstrip(b"\x00") == b"anf":
            for n in names:
                bn = os.path.basename(n.replace("\\", "/"))
                cands = [os.path.normpath(os.path.join(base, n.replace("/", os.sep))),
                         os.path.join(base, f"{stem}_{bn}"),
                         os.path.join(base, bn)]
                for c in cands:
                    if os.path.exists(c):
                        out.append((os.path.splitext(bn)[0], c))
                        seen.add(os.path.abspath(c))
                        break
    if include_orphans:
        ans_stems = {os.path.splitext(f)[0] for f in os.listdir(base)
                     if f.lower().endswith(".ans")}
        longer = [s for s in ans_stems if len(s) > len(stem)]
        for c in sorted(glob.glob(os.path.join(base, stem + "_*.anf"))):
            if os.path.abspath(c) in seen:
                continue
            fn = os.path.basename(c)
            if any(fn.startswith(s + "_") for s in longer):
                continue
            name = fn[len(stem) + 1:-4]
            out.append((name, c))
    return out


def _find_dep_file(ams_path, dep_name, mtr_root=None):
    """按 AMS 的依赖名找 `.mtr`;先试 --mtr-root,再沿 AMS 目录向上找 `mat/`。"""
    rel = dep_name.replace("\\", "/")
    cands = []
    if mtr_root:
        cands.append(os.path.join(mtr_root, rel))
    base = os.path.dirname(os.path.abspath(ams_path))
    for _ in range(5):
        cands.append(os.path.join(base, rel))
        cands.append(os.path.join(base, "..", rel))
        base = os.path.dirname(base)
    for c in cands:
        if os.path.exists(c):
            return os.path.abspath(c)
    return None


def _winding_consistent(mesh):
    """三角形绕序是否与顶点法线一致(多数为正面)。

    `.mtr` 的 cull 直接照搬会让 `l_p_004a`/`p_045` 这类反向绕序模型消失,
    这类模型导出时强制 doubleSided。
    """
    pos = neg = 0
    pos_arr, nor_arr = mesh["positions"], mesh["normals"]
    idx = mesh["indices"]
    for i in range(0, len(idx) - 2, 3):
        a, b, c = idx[i], idx[i + 1], idx[i + 2]
        p0, p1, p2 = pos_arr[a], pos_arr[b], pos_arr[c]
        u = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        v = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0])
        nv = (nor_arr[a][0] + nor_arr[b][0] + nor_arr[c][0],
              nor_arr[a][1] + nor_arr[b][1] + nor_arr[c][1],
              nor_arr[a][2] + nor_arr[b][2] + nor_arr[c][2])
        d = n[0] * nv[0] + n[1] * nv[1] + n[2] * nv[2]
        if d > 0:
            pos += 1
        elif d < 0:
            neg += 1
    return neg <= pos


def _write_anim_manifest(mesh, out_path, anims, fps, explicit=False):
    ans = mesh.get("ans_path")
    declared = []
    if ans and os.path.exists(ans):
        f = g.CommonFileHead.parse(open(ans, "rb").read(), 0)[0]
        for tag, names in f.deps:
            if tag.rstrip(b"\x00") == b"anf":
                for n in names:
                    declared.append(os.path.splitext(os.path.basename(n))[0])
    clips = []
    for a in (anims or []):
        if isinstance(a, (tuple, list)):
            name, path = a[0], a[1]
        else:
            path = a
            name = os.path.splitext(os.path.basename(a))[0]
        clips.append({"name": name, "path": path, "declared": name in declared})
    incl = {os.path.abspath(c["path"]) for c in clips}
    orphans = []
    if ans and os.path.exists(ans):
        for name, path in ans_clip_paths(ans, include_orphans=True):
            if name in declared:
                continue
            orphans.append({"name": name, "path": path,
                            "included": os.path.abspath(path) in incl})
    manifest = {
        "model": mesh.get("name"),
        "ans": ans,
        "fps": fps,
        "explicit": explicit,
        "declared": declared,
        "clips": clips,
        "orphans": orphans,
    }
    p = os.path.splitext(out_path)[0] + ".anims.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return p


def cmd_ams2glb(args):
    src, dst = args.src, args.dst
    files = []
    if os.path.isdir(src):
        for r, _, fs in os.walk(src):
            files += [os.path.join(r, f) for f in fs if f.lower().endswith(".ams")]
    else:
        files = [src]
    os.makedirs(dst, exist_ok=True) if os.path.isdir(dst) or len(files) > 1 else None
    ok = bad = 0
    for p in files:
        try:
            mesh = parse_ams(p, args.ani_root, args.skin)
            if not mesh:
                print(f"  [skip] {p} (无法匹配 piece 布局)")
                bad += 1
                continue
            if os.path.isdir(dst) or len(files) > 1:
                out = os.path.join(dst, os.path.splitext(os.path.basename(p))[0] + ".glb")
            else:
                out = dst
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            textures = load_textures(mesh, args.tex_root)
            mat_opts = None
            mtr_name = None
            mtr_deps = mesh.get("deps", {}).get("mtr") or []
            if mtr_deps:
                mp = _find_dep_file(p, mtr_deps[0], args.mtr_root)
                if mp:
                    try:
                        mi = g.parse_mtr_file(mp)
                        mat_opts = {"alphaMode": mi["alphaMode"],
                                    "doubleSided": mi["doubleSided"]}
                        mtr_name = os.path.basename(mp)
                        if not mat_opts["doubleSided"] and not _winding_consistent(mesh):
                            mat_opts["doubleSided"] = True
                            mtr_name += ",winding->doubleSided"
                    except Exception as e:
                        print(f"  [warn] mtr {mp}: {e}")
            anims = []
            if args.anim:
                anims = [args.anim]
            elif args.clips:
                stem = os.path.splitext(os.path.basename(p))[0]
                ap = mesh.get("ans_path")
                if not ap and args.ani_root:
                    ap = os.path.join(args.ani_root, stem + ".ans")
                if ap and os.path.exists(ap):
                    anims = ans_clip_paths(ap, include_orphans=args.orphan_clips)
                elif args.ani_root:
                    anims = sorted(glob.glob(os.path.join(args.ani_root, stem + "_*.anf")))
            write_glb(out, mesh, textures, args.flip_v, anims, args.fps,
                      mat_opts=mat_opts)
            if anims and not args.no_anim_manifest:
                _write_anim_manifest(mesh, out, anims, args.fps,
                                     explicit=bool(args.anim))
            print(f"  [ok] {os.path.basename(p)} -> {out} "
                  f"({len(mesh['positions'])}v/{len(mesh['indices'])//3}tri, "
                  f"{len(mesh['pieces'])}piece, {len(textures)}tex"
                  + (f", {len(mesh['skeleton']['names'])}bones"
                     if mesh.get("skeleton") else "")
                  + (f", {len(anims)}clips" if anims else "")
                  + (f", mtr={mtr_name}({mat_opts['alphaMode']})" if mat_opts else "") + ")")
            ok += 1
        except Exception as e:
            print(f"  [err] {p}: {e}")
            bad += 1
    print(f"done: ok={ok} failed={bad}")


def cmd_anf_info(args):
    bones = args.bones
    if args.ans:
        recs, _ = parse_ans_skeleton(args.ans)
        bones = len(recs)
    if not bones:
        print("need --bones or --ans")
        return
    r = parse_anf(args.anf, bones)
    if not r:
        print("parse failed")
        return
    ts, ss, axis, tracks = r
    print(f"{args.anf}: bones={bones} transScale={ts:.3f} "
          f"scaleScale={ss:.3f} scaleAxis={axis}")
    for i, t in enumerate(tracks):
        nS, nR, nT, kS, kR, kT = t
        print(f"  bone{i:02d}: scale={nS} rot={nR} trans={nT} "
              f"rotTimes={[k[0] for k in kR[:8]]}")


def cmd_anf_table(args):
    import json
    rows = parse_ans_anf_table(args.ans)
    nz = [r for r in rows if r["kc"]]
    print(f"{args.ans}: clips={len(rows)} kc!=0={len(nz)}")
    for r in rows[:args.limit]:
        print(f"  {r['clip']}: a=0x{r['a']:08X} b={r['b']} kc={r['kc']} "
              f"events={r['events'][:4]}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print(f"-> {args.json}")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Gamma 模型 / 骨架 / 动画工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ams2glb")
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--tex-root", default=None,
                   help="贴图 PNG 根目录(如 _agent-sandbox/png_all)")
    p.add_argument("--ani-root", default=None,
                   help="role/ani 目录(用于加载同名 .ans 骨架并导出蒙皮)")
    p.add_argument("--mtr-root", default=None,
                   help="材质根目录(含 mat/*.mtr;默认从 AMS 目录向上查找)")
    p.add_argument("--flip-v", action="store_true",
                   help="导出时把 V 翻转为 1-v")
    p.add_argument("--anim", default=None,
                   help="要嵌入的 .anf 动画 clip(单文件模式)")
    p.add_argument("--fps", type=float, default=30.0,
                   help="动画时间轴帧率(默认 30)")
    p.add_argument("--skin", type=int, default=0,
                   help="配色/皮肤变体序号(取 piece 材质表的第 N 项,默认 0)")
    p.add_argument("--clips", action="store_true",
                   help="把该模型 .ans 里声明的全部 clip 写进同一个 GLB(多 animation)")
    p.add_argument("--orphan-clips", action="store_true",
                   help="同时包含同模型未登记在 .ans 的 <model>_*.anf(如废案 attack_1)")
    p.add_argument("--no-anim-manifest", action="store_true",
                   help="不写 <model>.anims.json(默认写)")
    p.set_defaults(func=cmd_ams2glb)

    p = sub.add_parser("anf-info")
    p.add_argument("anf")
    p.add_argument("--ans", default=None)
    p.add_argument("--bones", type=int, default=None)
    p.set_defaults(func=cmd_anf_info)

    p = sub.add_parser("anf-table")
    p.add_argument("ans")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", default=None)
    p.set_defaults(func=cmd_anf_table)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
