#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gamma(GammaKernel)资源格式工具。

结论来自对 libShellClient.so(Gamma 引擎,ARM32)的静态逆向:
  * .tex 贴图容器      -> Gamma::CCommonFileHead / Gamma::CTextureFile
  * .r / .z 资源包     -> Gamma::CPackage / Gamma::CPackageMgr
  * filelist_<ver>.r   -> Gamma::CPackageMgr::OnLoadFileList (TSV)

本文件仅依赖标准库。

示例:
  python gamma_formats.py tex-info    a.tex
  python gamma_formats.py tex-unpack  a.tex outdir
  python gamma_formats.py tex-pack    payload.bin out.tex --format 3 --width 512 --height 512 --mips 10
  python gamma_formats.py fl-list     filelist_1.3.3.4.7.r
  python gamma_formats.py fl-verify   <assets_dir>
  python gamma_formats.py unpack      <assets_dir> <out_dir>
  python gamma_formats.py r-unpack    x.r outdir
  python gamma_formats.py r-pack      out.r file1 file2 ...
"""
import argparse
import json
import math
import os
import random
import re
import struct
import sys
import zlib

# ATF 的平台块是 ETC(Android), DXT 只覆盖旧变体; 优先用 texture2ddecoder。
# 运行: uv run --with texture2ddecoder python gamma_formats.py ...
try:
    import texture2ddecoder as _t2d
except Exception:  # 纯标准库环境下仍可解码 raw/DXT
    _t2d = None

MAGIC_TEX = b"tex\x00"

# ETextureFormat 位深表(取自 Gamma::GetBitPerPixel 的静态表, 单位 bit/pixel)
BPP = [4, 8, 24, 32, 16, 16, 4, 8, 24, 32, 16, 16, 16, 4, 8, 8,
       16, 16, 32, 32, 32, 32, 64, 64, 128, 0, 16, 32, 32]

# IsCompressFormat() 返回 true 的枚举(块压缩格式)
COMPRESSED = {0, 1, 6, 7, 13, 14}

# 旧版 .tex(古域2 0x2711..0x2713/0x2715)的 D3D 格式映射,与上面的枚举无关
LEGACY_FORMAT_GUESS = {
    0: "DXT1", 1: "DXT5", 2: "RGB24", 3: "RGBA32",
    4: "RGB565", 5: "A4R4G4B4",
}

# 依据位深 + 是否压缩 + GL 常见取值给出的“最可能”解释, 供参考
FORMAT_GUESS = {
    0: "compressed 4bpp (DXT1/ETC1/PVRTC4)",
    1: "compressed 8bpp (DXT5/ETC2/PVRTC8)",
    2: "RGB 24bpp",
    3: "RGBA 32bpp",
    4: "16bpp (ARGB4444?)",
    5: "16bpp (RGB565?)",
    6: "compressed 4bpp",
    7: "compressed 8bpp",
    8: "24bpp",
    9: "32bpp",
    10: "16bpp",
    11: "16bpp",
    12: "16bpp",
    13: "compressed 4bpp",
    14: "compressed 8bpp",
    15: "8bpp (A8/L8)",
    16: "16bpp",
    17: "16bpp",
    18: "32bpp",
    19: "32bpp",
    20: "32bpp",
    21: "32bpp",
    22: "64bpp",
    23: "64bpp",
    24: "128bpp",
    25: "invalid",
    26: "16bpp",
    27: "32bpp",
    28: "32bpp",
}

STORAGE_RAW = 0
STORAGE_ATF = 4   # 索引[1]==4 表示主数据是 ATF/压缩容器


# --------------------------------------------------------------------------
# 通用
# --------------------------------------------------------------------------
def decode_text(b: bytes) -> str:
    """Gamma 项目的文本编码很混乱: UTF-8 / UTF-16LE / GBK 都可能出现。"""
    if b.startswith(b"\xef\xbb\xbf"):
        return b[3:].decode("utf-8", "replace")
    if b.startswith(b"\xff\xfe"):
        return b[2:].decode("utf-16-le", "replace")
    if b.startswith(b"\xfe\xff"):
        return b[2:].decode("utf-16-be", "replace")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("gbk", "replace")


def write_png(path: str, w: int, h: int, rgba: bytes) -> None:
    if len(rgba) < w * h * 4:
        rgba = rgba + b"\x00" * (w * h * 4 - len(rgba))
    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


# ---- 块压缩解码(BC1/DXT1, BC3/DXT5) ----
def _rgb565(v):
    r = (v >> 11) & 0x1F
    g = (v >> 5) & 0x3F
    b = v & 0x1F
    return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)


def decode_dxt1(data, w, h):
    out = bytearray(w * h * 4)
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    p = 0
    for by in range(bh):
        for bx in range(bw):
            blk = data[p:p + 8]
            p += 8
            if len(blk) < 8:
                blk = blk + b"\x00" * (8 - len(blk))
            c0, c1 = struct.unpack_from("<HH", blk, 0)
            bits = struct.unpack_from("<I", blk, 4)[0]
            r0, g0, b0 = _rgb565(c0)
            r1, g1, b1 = _rgb565(c1)
            if c0 > c1:
                cp = [(r0, g0, b0, 255), (r1, g1, b1, 255),
                      ((2*r0+r1)//3, (2*g0+g1)//3, (2*b0+b1)//3, 255),
                      ((r0+2*r1)//3, (g0+2*g1)//3, (b0+2*b1)//3, 255)]
            else:
                cp = [(r0, g0, b0, 255), (r1, g1, b1, 255),
                      ((r0+r1)//2, (g0+g1)//2, (b0+b1)//2, 255), (0, 0, 0, 0)]
            for py in range(4):
                for px in range(4):
                    x, y = bx*4 + px, by*4 + py
                    if x >= w or y >= h:
                        continue
                    ci = (bits >> (2 * (4*py + px))) & 3
                    o = (y*w + x) * 4
                    out[o:o+4] = bytes(cp[ci])
    return bytes(out)


def decode_dxt5(data, w, h):
    out = bytearray(w * h * 4)
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    p = 0
    for by in range(bh):
        for bx in range(bw):
            blk = data[p:p + 16]
            p += 16
            if len(blk) < 16:
                blk = blk + b"\x00" * (16 - len(blk))
            a0, a1 = blk[0], blk[1]
            abits = int.from_bytes(blk[2:8], "little")
            c0, c1 = struct.unpack_from("<HH", blk, 8)
            cbits = struct.unpack_from("<I", blk, 12)[0]
            ap = [a0, a1, 0, 0, 0, 0, 0, 0]
            if a0 > a1:
                for i in range(1, 7):
                    ap[1 + i] = ((7 - i) * a0 + i * a1) // 7
            else:
                for i in range(1, 5):
                    ap[1 + i] = ((5 - i) * a0 + i * a1) // 5
                ap[6], ap[7] = 0, 255
            r0, g0, b0 = _rgb565(c0)
            r1, g1, b1 = _rgb565(c1)
            if c0 > c1:
                cp = [(r0, g0, b0), (r1, g1, b1),
                      ((2*r0+r1)//3, (2*g0+g1)//3, (2*b0+b1)//3),
                      ((r0+2*r1)//3, (g0+2*g1)//3, (b0+2*b1)//3)]
            else:
                cp = [(r0, g0, b0), (r1, g1, b1),
                      ((r0+r1)//2, (g0+g1)//2, (b0+b1)//2), (0, 0, 0)]
            for py in range(4):
                for px in range(4):
                    x, y = bx*4 + px, by*4 + py
                    if x >= w or y >= h:
                        continue
                    ai = (abits >> (3 * (4*py + px))) & 7
                    ci = (cbits >> (2 * (4*py + px))) & 3
                    r, g, b = cp[ci]
                    o = (y*w + x) * 4
                    out[o:o+4] = bytes((r, g, b, ap[ai]))
    return bytes(out)


def extract_jxr(data, outdir, base):
    """把内嵌的 JPEG-XR(容器以 II BC 开头) 逐段抽出为 .jxr。"""
    idxs = []
    i = data.find(b"II\xbc")
    while i >= 0:
        idxs.append(i)
        i = data.find(b"II\xbc", i + 3)
    out = []
    for k, st in enumerate(idxs):
        en = idxs[k + 1] if k + 1 < len(idxs) else len(data)
        p = os.path.join(outdir, f"{base}_{k}.jxr")
        with open(p, "wb") as f:
            f.write(data[st:en])
        out.append(p)
    return out


# ---- ATF(Gamma 变体: "ATF" + u24BE(bodyLen) + format + log2w + log2h + levels) ----
ATF_FORMAT = {0: "RGB888(RGBA)", 1: "RGBA8888", 2: "compressed(DXT1+JXR)",
              3: "raw compressed(DXT1)", 4: "compressed alpha(DXT5+JXR)",
              5: "raw compressed alpha(DXT5)", 0x0c: "lossy(DXT1+JXR)",
              0x0d: "lossy alpha(DXT5+JXR)"}


def parse_atf(main):
    if main[:3] != b"ATF":
        return None
    return {"body_len": int.from_bytes(main[3:6], "big"),
            "format": main[6], "width": 1 << main[7], "height": 1 << main[8],
            "levels": main[9], "main": main, "body": main[10:]}


def _u24(b, o):
    return (b[o] << 16) | (b[o + 1] << 8) | b[o + 2]


def atf_block_and_codec(atf):
    """返回 (codec, data)。两种头部版本:
       +16 处 u24 == 期望长度 -> 平台块(Android = ETC2/ETC1), 数据在 +19;
       +10 处 u24 == 期望长度 -> 旧变体(DXT), 数据在 +13。
    期望长度: ATF5 = w*h(8bpp), ATF3 = w*h/2(4bpp)。"""
    f, w, h, main = atf["format"], atf["width"], atf["height"], atf["main"]
    want = (w * h) if f == 5 else ((w * h) // 2 if f == 3 else 0)
    if want <= 0 or len(main) < 19:
        return None
    if _u24(main, 16) == want:
        return ("etc_dual" if f == 5 else "etc_rgb"), main[19:19 + want]
    if _u24(main, 10) == want:
        return ("dxt5" if f == 5 else "dxt1"), main[13:13 + want]
    return None


def _bgra_to_rgba(b):
    out = bytearray(b)
    for i in range(0, len(out), 4):
        out[i], out[i + 2] = out[i + 2], out[i]
    return bytes(out)


def _decode_etc_dual(data, w, h):
    """fmt5: 块是 (w × 2h) 的 ETC1/ETC2 —— 上半为颜色, 下半为 alpha。"""
    full = _t2d.decode_etc2(data, w, h * 2)      # 输出 BGRA
    out = bytearray(w * h * 4)
    for y in range(h):
        row_t = y * w
        row_b = (h + y) * w
        for x in range(w):
            ci = (row_t + x) * 4
            ai = (row_b + x) * 4
            o = (row_t + x) * 4
            out[o] = full[ci + 2]        # R
            out[o + 1] = full[ci + 1]    # G
            out[o + 2] = full[ci]        # B
            out[o + 3] = full[ai + 2]    # alpha 取自下半
    return bytes(out)


def atf_jxr_blocks(main):
    idxs = []
    i = main.find(b"II\xbc")
    while i >= 0:
        idxs.append(i)
        i = main.find(b"II\xbc", i + 3)
    return [main[st:(idxs[k + 1] if k + 1 < len(idxs) else len(main))]
            for k, st in enumerate(idxs)]


def atf_to_rgba(atf):
    """返回 (rgba, codec) 或 (None, 原因)。"""
    r = atf_block_and_codec(atf)
    if r is None:
        return None, f"atf-format-{atf['format']}(no block)"
    codec, data = r
    w, h = atf["width"], atf["height"]
    try:
        if codec == "dxt5":
            return decode_dxt5(data, w, h), "dxt5"
        if codec == "dxt1":
            return decode_dxt1(data, w, h), "dxt1"
        if _t2d is None:
            return None, "etc2 needs texture2ddecoder (uv run --with texture2ddecoder)"
        if codec == "etc_dual":
            return _decode_etc_dual(data, w, h), "etc-dual"
        if codec == "etc_rgb":
            return _bgra_to_rgba(_t2d.decode_etc2(data, w, h)), "etc2"
    except Exception as e:
        return None, f"decode error: {e}"


# --------------------------------------------------------------------------
# CCommonFileHead  (Gamma::CCommonFileHead::ReadHead)
#   u32 type            4CC, 如 "tex\\0"
#   u32 version         例如 0x2717
#   u32 depCount
#   dep[depCount]: u32 tag; u32 nameLen; u16 nameLen[](UTF-16LE)
#   u32 extraDataSize   紧随其后的“索引/额外数据”长度
# --------------------------------------------------------------------------
class CommonFileHead:
    def __init__(self):
        self.type = b""
        self.version = 0
        self.deps = []           # [(tag, name)]
        self.extra_size = 0

    @classmethod
    def parse(cls, data: bytes, off: int = 0):
        self = cls()
        self.type = data[off:off + 4]
        off += 4
        (self.version,) = struct.unpack_from("<I", data, off)
        off += 4
        (dep_count,) = struct.unpack_from("<I", data, off)
        off += 4
        for _ in range(dep_count):
            tag = data[off:off + 4]
            off += 4
            (name_count,) = struct.unpack_from("<I", data, off)
            off += 4
            names = []
            for _ in range(name_count):
                (cc,) = struct.unpack_from("<I", data, off)
                off += 4
                names.append(data[off:off + cc * 2].decode("utf-16-le", "replace"))
                off += cc * 2
            self.deps.append((tag, names))
        (self.extra_size,) = struct.unpack_from("<I", data, off)
        off += 4
        return self, off

    def build(self) -> bytes:
        out = bytearray()
        out += self.type
        out += struct.pack("<I", self.version)
        out += struct.pack("<I", len(self.deps))
        for tag, names in self.deps:
            out += tag
            out += struct.pack("<I", len(names))
            for name in names:
                b = name.encode("utf-16-le")
                out += struct.pack("<I", len(b) // 2)
                out += b
        out += struct.pack("<I", self.extra_size)
        return bytes(out)


# --------------------------------------------------------------------------
# .tex
# 布局: CommonFileHead + extra(9*u32 索引) + main data
# 索引: [0]=ETextureFormat [1]=storage(0 原始 / 4 ATF容器)
#       [2]=widthPow2 [3]=heightPow2 [4]=depth/cube [5]=mipCount
#       [6]=width [7]=height [8]=reserved
# --------------------------------------------------------------------------
class TexFile:
    def __init__(self):
        self.head = CommonFileHead()
        self.index = [0] * 9
        self.legacy_info = None      # 古域2 等旧版 0x2711..0x2713 的 6*u32 头
        self.extra = b""
        self.main = b""

    # ---- 解析 ----
    @classmethod
    def parse(cls, data: bytes):
        self = cls()
        self.head, off = CommonFileHead.parse(data, 0)
        self.extra = data[off:off + self.head.extra_size]
        off += self.head.extra_size
        self.main = data[off:]
        if 0x2710 <= self.head.version <= 0x2713 and len(self.main) >= 24:
            # 旧纹理头: format, width, height, mipCount, width2, height2
            self.legacy_info = list(struct.unpack_from("<6I", self.main, 0))
            self.main = self.main[24:]
        elif self.head.version == 0x2715 and len(self.main) >= 40:
            # 仅见的 0x2715 地图纹理: 8B 前缀 + [w,h,format,mip,usedW,usedH] + 8B 尾部
            # (样本: map/_hash/00477af3.tex 1024x512 DXT1)
            w, h, f, mip = struct.unpack_from("<4I", self.main, 8)
            self.legacy_info = [f, w, h, mip, 0, 0]
            self.legacy_tail = 8
            self.main = self.main[40:]
        elif len(self.extra) >= 36:
            self.index = list(struct.unpack_from("<9I", self.extra, 0))
        return self

    @property
    def format(self):
        return self.legacy_info[0] if self.legacy_info else self.index[0]

    @property
    def storage(self):
        return 0 if self.legacy_info else self.index[1]

    @property
    def mips(self):
        return self.legacy_info[3] if self.legacy_info else self.index[5]

    @property
    def width(self):
        return self.legacy_info[1] if self.legacy_info else self.index[6]

    @property
    def height(self):
        return self.legacy_info[2] if self.legacy_info else self.index[7]

    def is_tex_magic(self):
        return self.head.type[:3] == b"tex" or self.head.type == MAGIC_TEX

    # ---- 常见图片魔数(用于非 .tex 载荷) ----
    @staticmethod
    def sniff(payload: bytes):
        if payload.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if payload.startswith(b"\x89PNG"):
            return "png"
        if payload.startswith(b"BM"):
            return "bmp"
        if payload.startswith(b"ATF"):
            return "atf"
        if payload.startswith(b"WMPHOTO"):
            return "jxr"
        if payload.startswith(b"RIFF"):
            return "webp/riff"
        return None

    # ---- 原始像素 -> RGBA ----
    def first_level_rgba(self):
        """仅对未压缩常见格式返回 RGBA 字节, 否则 None。"""
        f, w, h = self.format, self.width, self.height
        if w == 0 or h == 0 or f in COMPRESSED:
            return None
        n = w * h
        bpp = BPP[f] if f < len(BPP) else 0
        need = n * bpp // 8
        src = self.main
        if self.head.version > 0x2711 and len(src) >= 4:
            src = src[4:]           # 每级 mip 前有 u32 长度前缀
        src = src[:need]
        if len(src) < need:
            return None
        if f == 3 and bpp == 32:            # RGBA/BGRA 32bpp
            return self._swap32(src)
        if f == 9 and bpp == 32:
            return self._swap32(src)
        if f == 2 and bpp == 24:            # RGB/BGR 24bpp
            out = bytearray(n * 4)
            for i in range(n):
                r, g, b = src[i * 3], src[i * 3 + 1], src[i * 3 + 2]
                out[i * 4:i * 4 + 4] = bytes((r, g, b, 255))
            return bytes(out)
        if f == 8 and bpp == 24:
            out = bytearray(n * 4)
            for i in range(n):
                b, g, r = src[i * 3], src[i * 3 + 1], src[i * 3 + 2]
                out[i * 4:i * 4 + 4] = bytes((r, g, b, 255))
            return bytes(out)
        if bpp == 16:                        # 4444 / 565
            out = bytearray(n * 4)
            for i in range(n):
                v = src[i * 2] | (src[i * 2 + 1] << 8)
                if f in (4, 10, 16, 26):
                    a = (v >> 12) & 0xF
                    r = (v >> 8) & 0xF
                    g = (v >> 4) & 0xF
                    b = v & 0xF
                    out[i * 4:i * 4 + 4] = bytes((r * 17, g * 17, b * 17, a * 17))
                else:                        # 565
                    r = (v >> 11) & 0x1F
                    g = (v >> 5) & 0x3F
                    b = v & 0x1F
                    out[i * 4:i * 4 + 4] = bytes((r * 255 // 31, g * 255 // 63,
                                                  b * 255 // 31, 255))
            return bytes(out)
        if f == 15 and bpp == 8:             # A8 / L8
            out = bytearray(n * 4)
            for i in range(n):
                v = src[i]
                out[i * 4:i * 4 + 4] = bytes((v, v, v, v))
            return bytes(out)
        return None

    @staticmethod
    def _swap32(src, bgra=True):
        """Gamma/Android 常把 32bpp 存成 BGRA, 这里统一转成 RGBA。
        若发现颜色通道顺序不对, 把 bgra 改成 False。"""
        out = bytearray(len(src))
        for i in range(0, len(src), 4):
            x, y, z, w = src[i], src[i + 1], src[i + 2], src[i + 3]
            out[i:i + 4] = bytes((z, y, x, w)) if bgra else bytes((x, y, z, w))
        return bytes(out)

    # ---- 旧版(0x2711..0x2713)纹理 ----
    # 格式:0=DXT1 1=DXT5 2=RGB24 3=RGBA32 4=RGB565 5=A4R4G4B4
    # mip: >=0x2712 每级前有 u32 压缩长度; 小于理论大小时是 16-bit 命令 RLE。
    LEGACY_BPP = {0: 4, 1: 8, 2: 24, 3: 32, 4: 16, 5: 16}
    LEGACY_BLOCK = {0: 8, 1: 16, 2: 3, 3: 4, 4: 2, 5: 2}

    @staticmethod
    def _rle_blocks(src, block, expect):
        """CStaticTexture::ReadMipData 的块 RLE。

        命令 u16:n = cmd & 0x7FFF;
          bit15=0 -> 读一个 block, 重复 n 次;
          bit15=1 -> 连续读 n 个 block。
        """
        out = bytearray()
        p = 0
        while p + 2 <= len(src) and len(out) < expect:
            cmd = src[p] | (src[p + 1] << 8)
            p += 2
            n = cmd & 0x7FFF
            if n == 0:
                continue
            if cmd & 0x8000:
                need = n * block
                out += src[p:p + need]
                p += need
            else:
                blk = src[p:p + block]
                p += block
                out += blk * n
        return bytes(out[:expect])

    def legacy_levels(self):
        """逐级返回 (w, h, data); 不完整时抛 ValueError。"""
        w, h = self.width, self.height
        bpp = self.LEGACY_BPP.get(self.format)
        block = self.LEGACY_BLOCK.get(self.format)
        if not bpp or w <= 0 or h <= 0:
            raise ValueError(f"legacy unsupported format {self.format} {w}x{h}")
        nlev = self.mips or 1
        pos = 0
        for _ in range(max(1, nlev)):
            expect = max(block or 1, (w * h * bpp) // 8)
            if self.head.version == 0x2715:
                # 0x2715 样本没有逐级 u32 长度; 整块可能是 RLE
                chunk = self._rle_blocks(self.main[pos:], block, expect)
                pos = len(self.main)
            elif self.head.version >= 0x2712:
                if pos + 4 > len(self.main):
                    raise ValueError("truncated mip size")
                size = struct.unpack_from("<I", self.main, pos)[0]
                pos += 4
                if size >= expect:
                    chunk = self.main[pos:pos + expect]
                    pos += expect
                else:
                    chunk = self._rle_blocks(self.main[pos:pos + size], block, expect)
                    pos += size
            else:
                chunk = self.main[pos:pos + expect]
                pos += expect
            if len(chunk) < expect:
                raise ValueError(f"truncated mip {w}x{h}: {len(chunk)}/{expect}")
            yield w, h, chunk
            w = max(1, w // 2)
            h = max(1, h // 2)

    def legacy_first_level_rgba(self):
        w, h, src = next(self.legacy_levels())
        f = self.format
        n = w * h
        if f == 0:
            return decode_dxt1(src, w, h)
        if f == 1:
            return decode_dxt5(src, w, h)
        out = bytearray(n * 4)
        if f == 2:                                   # D3DFMT_R8G8B8 -> BGR
            for i in range(n):
                b, g, r = src[i * 3], src[i * 3 + 1], src[i * 3 + 2]
                out[i * 4:i * 4 + 4] = bytes((r, g, b, 255))
            return bytes(out)
        if f == 3:                                   # A8R8G8B8 -> BGRA
            return self._swap32(src[:n * 4])
        if f == 4:                                   # R5G6B5
            for i in range(n):
                v = src[i * 2] | (src[i * 2 + 1] << 8)
                r = (v >> 11) & 0x1F
                g = (v >> 5) & 0x3F
                b = v & 0x1F
                out[i * 4:i * 4 + 4] = bytes((r * 255 // 31, g * 255 // 63,
                                              b * 255 // 31, 255))
            return bytes(out)
        if f == 5:                                   # A4R4G4B4
            for i in range(n):
                v = src[i * 2] | (src[i * 2 + 1] << 8)
                a = (v >> 12) & 0xF
                r = (v >> 8) & 0xF
                g = (v >> 4) & 0xF
                b = v & 0xF
                out[i * 4:i * 4 + 4] = bytes((r * 17, g * 17, b * 17, a * 17))
            return bytes(out)
        return None

    # ---- 写回 ----
    def build(self) -> bytes:
        self.extra = struct.pack("<9I", *self.index)
        self.head.extra_size = len(self.extra)
        if not self.head.type:
            self.head.type = MAGIC_TEX
        if self.head.version == 0:
            self.head.version = 0x2717
        return self.head.build() + self.extra + self.main


# --------------------------------------------------------------------------
# .r 包 (Gamma::CPackage)
#   连续记录: [u32 length][length 字节原始数据]
#   截断(大资源只随包内前 0xC8000=204800 字节)时最后一条会超出文件尾。
# --------------------------------------------------------------------------
def parse_package(data: bytes):
    entries = []
    problems = []
    off = 0
    while off + 4 <= len(data):
        (n,) = struct.unpack_from("<I", data, off)
        if off + 4 + n > len(data):
            problems.append({"offset": off, "declared": n,
                             "remaining": len(data) - off - 4})
            entries.append({"offset": off, "length": None,
                            "data": data[off + 4:]})
            off = len(data)
            break
        entries.append({"offset": off, "length": n,
                        "data": data[off + 4:off + 4 + n]})
        off += 4 + n
    return entries, off, problems


def build_package(blobs):
    out = bytearray()
    for b in blobs:
        out += struct.pack("<I", len(b))
        out += b
    return bytes(out)


# --------------------------------------------------------------------------
# filelist_<ver>.r  (Gamma::CPackageMgr::OnLoadFileList)
#   filelist 本身也是一个 .r(单记录), 内容是 TSV:
#       <name>\t<col1>\t<col2>\r\n
#   col1 >= 0  -> 包(通常是 "<dir>/<md5>.z"), 之后若干“空列”行是该包的成员;
#                 col1=本地文件字节数, col2=完整压缩(网络)字节数
#   成员本地文件名 = 包名把结尾 ".z" 换成 ".r"
# --------------------------------------------------------------------------
def read_filelist_records(path):
    data = open(path, "rb").read()
    entries, _, problems = parse_package(data)
    text = b"".join(e["data"] for e in entries if e["data"] is not None)
    return text, problems


def parse_filelist(path):
    text, problems = read_filelist_records(path)
    s = decode_text(text)
    rows = []
    for line in s.split("\r\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        name = parts[0]
        c1 = parts[1].strip() if len(parts) > 1 else ""
        c2 = parts[2].strip() if len(parts) > 2 else ""
        rows.append((name, int(c1) if c1 not in ("", "-") else None,
                     int(c2) if c2 not in ("", "-") else None))

    # 分组规则(逆向 CPackageMgr::OnLoadFileList + CPackage::OnLoaded 得出):
    #   连续多个“包行”(col1>=0) = 同一逻辑包的多个分片(segment),
    #   其后的“空列行”(成员名)才是该包内的文件。
    #   分片在磁盘上是 <hash>.r,首片带 [u32 总长] 头,后续片无头,需按顺序拼接。
    packages, standalone = [], []
    pending, cur = [], None
    for name, c1, c2 in rows:
        if c1 is not None and c1 >= 0:
            if cur is not None:
                pending = [{"name": name, "size": c1, "csize": c2}]
                cur = None
            else:
                pending.append({"name": name, "size": c1, "csize": c2})
        else:
            if pending:
                cur = {"parts": pending, "members": [name]}
                packages.append(cur)
                pending = []
            elif cur is not None:
                cur["members"].append(name)
            else:
                standalone.append(name)
    for p in pending:
        packages.append({"parts": [p], "members": []})
    return {"rows": rows, "packages": packages, "plain": standalone,
            "problems": problems}


def r_path_from_z(package_name: str, assets_dir: str) -> str:
    """filelist 里的 <...>.z -> 本地 <...>.r。"""
    rel = package_name[:-2] + ".r" if package_name.endswith(".z") else package_name
    return os.path.join(assets_dir, rel.replace("/", os.sep))


def member_path(package_name: str, member: str) -> str:
    if "/" in member or "\\" in member:
        return member
    d = os.path.dirname(package_name)
    return f"{d}/{member}" if d else member


def load_package_bytes(assets_dir, pkg):
    """按顺序拼接一个逻辑包的所有分片 .r,返回 (bytes, error)。"""
    buf = bytearray()
    for part in pkg["parts"]:
        rp = r_path_from_z(part["name"], assets_dir)
        if not os.path.exists(rp):
            return None, f"missing: {part['name']} -> {rp}"
        with open(rp, "rb") as f:
            buf += f.read()
    return bytes(buf), None


# ---- 解包路径过滤(任务: 仅 config / 仅贴图 等) ----
CATEGORY_PREFIXES = {
    "config": ("config/",),
    "tex": ("gui/tex/", "role/tex/", "fx/tex/", "map/"),
    "sound": ("sound/",),
    "music": ("music/",),
    "video": ("video/",),
    "role": ("role/",),
    "lua": ("misc/ShellClientLua/", "misc/ShellCommonLua/"),
    "mat": ("mat/",),
}


def path_selected(rel, args):
    import fnmatch
    if getattr(args, "only", None):
        prefixes = CATEGORY_PREFIXES.get(args.only, ())
        low = rel.lower()
        if not any(low.startswith(px) for px in prefixes):
            return False
    for pat in getattr(args, "include", []) or []:
        if not fnmatch.fnmatch(rel.lower(), pat.lower()):
            return False
    for pat in getattr(args, "exclude", []) or []:
        if fnmatch.fnmatch(rel.lower(), pat.lower()):
            return False
    return True


# --------------------------------------------------------------------------
# 命令
# --------------------------------------------------------------------------
def convert_tex(data, outdir, base):
    """把一个 .tex 转成 PNG / JXR / 原始文件。返回 (kind, [输出路径...])。"""
    os.makedirs(outdir, exist_ok=True)
    t = TexFile.parse(data)
    if t.head.type[:3] != b"tex" and data[:4] != MAGIC_TEX:
        p = os.path.join(outdir, base + ".bin")
        with open(p, "wb") as f:
            f.write(data)
        return "non-tex", [p]
    sniff = t.sniff(t.main)
    if t.legacy_info:
        try:
            rgba = t.legacy_first_level_rgba()
        except Exception as e:
            p = os.path.join(outdir, base + ".bin")
            with open(p, "wb") as f:
                f.write(t.main)
            return f"legacy-error-{e.__class__.__name__}", [p]
        if rgba is None:
            p = os.path.join(outdir, base + ".bin")
            with open(p, "wb") as f:
                f.write(t.main)
            return f"legacy-format-{t.format}", [p]
        p = os.path.join(outdir, base + ".png")
        write_png(p, t.width, t.height, rgba)
        return {0: "dxt1", 1: "dxt5", 2: "rgb24", 3: "rgba32",
                4: "rgb565", 5: "a4r4g4b4"}.get(t.format, f"legacy-{t.format}"), [p]
    if t.storage == STORAGE_ATF or sniff == "atf":
        atf = parse_atf(t.main)
        if atf:
            rgba, codec = atf_to_rgba(atf)
            if rgba is not None:
                p = os.path.join(outdir, base + ".png")
                write_png(p, atf["width"], atf["height"], rgba)
                return codec, [p]
            blocks = atf_jxr_blocks(atf["main"])
            if blocks:
                paths = []
                for k, b in enumerate(blocks):
                    p = os.path.join(outdir, f"{base}_{k}.jxr")
                    with open(p, "wb") as f:
                        f.write(b)
                    paths.append(p)
                return "jxr", paths
        p = os.path.join(outdir, base + ".atf")
        with open(p, "wb") as f:
            f.write(t.main)
        return "atf", [p]
    if sniff in ("jpg", "png", "bmp", "jxr", "webp/riff"):
        ext = sniff.split("/")[0]
        p = os.path.join(outdir, base + "." + ext)
        with open(p, "wb") as f:
            f.write(t.main)
        return ext, [p]
    rgba = t.first_level_rgba()
    if rgba is not None:
        p = os.path.join(outdir, base + ".png")
        write_png(p, t.width, t.height, rgba)
        return "raw", [p]
    p = os.path.join(outdir, base + ".bin")
    with open(p, "wb") as f:
        f.write(t.main)
    return f"raw-format-{t.format}", [p]


def cmd_tex_batch(args):
    import collections
    stats = collections.Counter()
    total = 0
    for root, _, files in os.walk(args.srcdir):
        for fn in files:
            if not fn.lower().endswith(".tex"):
                continue
            src = os.path.join(root, fn)
            try:
                with open(src, "rb") as f:
                    data = f.read()
                if data[:4] != MAGIC_TEX:
                    continue
                rel = os.path.relpath(src, args.srcdir)
                outdir = os.path.join(args.outdir, os.path.dirname(rel))
                kind, paths = convert_tex(data, outdir, os.path.splitext(fn)[0])
                stats[kind] += 1
                total += 1
            except Exception as e:
                stats["ERROR"] += 1
                print(f"  !! {src}: {e}")
    print(f"共处理 {total} 个 .tex -> {args.outdir}")
    for k, v in stats.most_common():
        print(f"   {k:24} {v}")


def parse_eqp_skins(data):
    """解析 `.eqp` 后半段的**皮肤表**(名字 + 部件类名)。

    结构(实测 male.eqp): `.ams` 列表、`.ans` 列表之后是

        u32 count
        重复: u32 nameLen + UTF-16 皮肤名
              u32 classLen + UTF-16 部件类名("body"/"obj")
              ~60 字节元数据(首字段为 0,1,2… 的序号,含 1.0f 缩放等)

    条目长度随名字长度变化(实测 79/80 字节),这里按「名字+类名」的模式扫描,
    不依赖固定 stride。
    """
    out = []
    n = len(data)
    p = 0
    while p + 8 < n:
        ln = struct.unpack_from("<I", data, p)[0]
        if not (1 <= ln <= 32) or p + 4 + 2 * ln > n:
            p += 1
            continue
        try:
            nm = data[p + 4:p + 4 + 2 * ln].decode("utf-16-le")
        except Exception:
            p += 1
            continue
        if not nm or not all(ord(c) >= 0x20 and ord(c) != 0xFFFF for c in nm):
            p += 1
            continue
        q = p + 4 + 2 * ln
        if q + 4 > n:
            break
        cl = struct.unpack_from("<I", data, q)[0]
        if not (1 <= cl <= 16) or q + 4 + 2 * cl > n:
            p += 1
            continue
        try:
            cls = data[q + 4:q + 4 + 2 * cl].decode("utf-16-le")
        except Exception:
            p += 1
            continue
        if not cls or not cls.isascii() or not cls.replace("_", "").isalnum():
            p += 1
            continue
        if nm.isascii():
            p += 1
            continue
        out.append({"index": len(out), "name": nm, "class": cls, "offset": p})
        p = q + 4 + 2 * cl
    return out


def parse_eqp_names(data):
    """`.eqp` = (12 字节头) + 'ams\\0' + u32 count + count×[u32 charCount + UTF16LE]。"""
    p = 12
    if data[p:p + 4] == b"ams\x00":
        p += 4
    names = []
    if p + 4 > len(data):
        return names
    (count,) = struct.unpack_from("<I", data, p)
    p += 4
    for _ in range(count):
        if p + 4 > len(data):
            break
        (cc,) = struct.unpack_from("<I", data, p)
        p += 4
        names.append(data[p:p + cc * 2].decode("utf-16-le", "replace"))
        p += cc * 2
    return names


def cmd_deps(args):
    """解析 .eqp/.ams 的依赖(模型/材质)名称,输出关系图。"""
    import json
    out = []

    def head_deps(path, label, skip=0):
        data = open(path, "rb").read()[skip:]
        if len(data) < 16:
            return None
        try:
            h, _ = CommonFileHead.parse(data, 0)
        except Exception:
            return None
        t = h.type.rstrip(b"\x00").decode("latin1")
        return {"file": label, "type": t, "version": hex(h.version),
                "deps": [{"tag": tg.rstrip(b"\x00").decode("latin1"),
                          "names": list(nms)} for tg, nms in h.deps]}

    for root, _, files in os.walk(args.dir):
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, args.dir)
            low = fn.lower()
            if low.endswith(".eqp"):
                names = parse_eqp_names(open(p, "rb").read())
                out.append({"file": rel, "type": "eqp",
                            "deps": [{"tag": "ams", "names": names}]})
            elif low.endswith(".ams"):
                info = head_deps(p, rel)
                if info:
                    out.append(info)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"解析 {len(out)} 个模型/装备文件 -> {args.out}")
    for e in out[:12]:
        deps = ", ".join(f"{d['tag']}=[{','.join(d['names'])}]" for d in e["deps"])
        print(f"  {e['file']}  ({e['type']})  [{deps}]")


def cmd_tex_info(args):
    t = TexFile.parse(open(args.tex, "rb").read())
    info = {
        "file": args.tex,
        "size": os.path.getsize(args.tex),
        "head": {
            "type": t.head.type.rstrip(b"\x00").decode("latin1"),
            "version": hex(t.head.version),
            "dep_count": len(t.head.deps),
            "extra_size": t.head.extra_size,
        },
        "index": t.index,
        "legacy_info": t.legacy_info,
        "legacy_format": (LEGACY_FORMAT_GUESS.get(t.format)
                          if t.legacy_info else None),
        "format": t.format,
        "format_guess": FORMAT_GUESS.get(t.format),
        "bpp": BPP[t.format] if t.format < len(BPP) else None,
        "compressed": t.format in COMPRESSED,
        "storage": t.storage,
        "storage_guess": "ATF/compressed container" if t.storage == 4 else "raw",
        "width": t.width,
        "height": t.height,
        "mip_count": t.mips,
        "main_size": len(t.main),
        "main_magic": t.sniff(t.main),
    }
    print(json.dumps(info, indent=2, ensure_ascii=False))


def cmd_tex_unpack(args):
    data = open(args.tex, "rb").read()
    t = TexFile.parse(data)
    os.makedirs(args.outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(args.tex))[0]
    meta = {
        "type": t.head.type.rstrip(b"\x00").decode("latin1"),
        "version": hex(t.head.version),
        "index": t.index,
        "format": t.format,
        "format_guess": FORMAT_GUESS.get(t.format),
        "storage": t.storage,
        "width": t.width,
        "height": t.height,
        "mip_count": t.mips,
    }
    with open(os.path.join(args.outdir, base + ".meta.json"), "w",
              encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    sniff = t.sniff(t.main)
    if t.storage == STORAGE_ATF or sniff == "atf":
        with open(os.path.join(args.outdir, base + ".atf"), "wb") as f:
            f.write(t.main)
        atf = parse_atf(t.main)
        kind = "ATF"
        if atf:
            kind = f"ATF fmt={atf['format']}({ATF_FORMAT.get(atf['format'], '?')}) " \
                   f"{atf['width']}x{atf['height']} lv={atf['levels']}"
            rgba, codec = atf_to_rgba(atf)
            if rgba is not None:
                out = os.path.join(args.outdir, base + ".png")
                write_png(out, atf["width"], atf["height"], rgba)
                print(f"[{kind} -> {codec}] {out}")
                return
        # 非 DXT 路径: 按 ATF 块抽取 JPEG-XR
        jxrs = []
        if atf:
            for k, b in enumerate(atf_jxr_blocks(atf["main"])):
                p = os.path.join(args.outdir, f"{base}_{k}.jxr")
                with open(p, "wb") as f:
                    f.write(b)
                jxrs.append(p)
        if not jxrs:
            jxrs = extract_jxr(t.main, args.outdir, base)
        if jxrs:
            print(f"[{kind}] 已抽取 {len(jxrs)} 个 JPEG-XR: " + ", ".join(
                os.path.basename(p) for p in jxrs))
        else:
            print(f"[{kind}] 原始 ATF 已写出: {base}.atf  ({len(t.main)} bytes)")
    elif sniff in ("jpg", "png", "bmp", "jxr", "webp/riff"):
        out = os.path.join(args.outdir, base + "." + sniff.split("/")[0])
        open(out, "wb").write(t.main)
        print(f"[图片 {sniff}] {out}  ({len(t.main)} bytes)")
    elif t.format in COMPRESSED:
        out = os.path.join(args.outdir, base + ".bin")
        open(out, "wb").write(t.main)
        print(f"[块压缩 format={t.format}] 原始数据已写出: {out}  ({len(t.main)} bytes)")
    else:
        rgba = t.first_level_rgba()
        if rgba:
            out = os.path.join(args.outdir, base + ".png")
            write_png(out, t.width, t.height, rgba)
            print(f"[原始像素 format={t.format} {t.width}x{t.height}] {out}")
        else:
            out = os.path.join(args.outdir, base + ".bin")
            open(out, "wb").write(t.main)
            print(f"[未知 format={t.format}] 原始数据已写出: {out}")


def cmd_tex_pack(args):
    payload = open(args.payload, "rb").read()
    t = TexFile()
    t.head.type = MAGIC_TEX
    t.head.version = 0x2717
    t.index = [args.format, args.storage, args.width, args.height, 0,
               args.mips, args.width, args.height, 0]
    t.main = payload
    open(args.out, "wb").write(t.build())
    print(f"已写出 {args.out}  ({os.path.getsize(args.out)} bytes)")


def cmd_fl_list(args):
    fl = parse_filelist(args.filelist)
    print(f"name={args.filelist}")
    print(f"rows={len(fl['rows'])}  logical_packages={len(fl['packages'])}  "
          f"plain={len(fl['plain'])}")
    for p in fl["packages"][: args.limit]:
        names = ",".join(pt["name"].split("/")[-1] for pt in p["parts"][:3])
        extra = "" if len(p["parts"]) <= 3 else f" (+{len(p['parts']) - 3})"
        print(f"  pkg parts={len(p['parts'])} [{names}{extra}]  "
              f"members={len(p['members'])}")
        for m in p["members"][:6]:
            print(f"      - {m}")
        if len(p["members"]) > 6:
            print(f"      ... (+{len(p['members']) - 6})")
    if fl["plain"]:
        print("  standalone:", ", ".join(fl["plain"][:10]))


def cmd_fl_verify(args):
    assets = args.assets
    fl_path = os.path.join(assets, args.filelist) if not os.path.isabs(args.filelist) \
        else args.filelist
    fl = parse_filelist(fl_path)
    ok = bad = missing = 0
    for p in fl["packages"]:
        data, err = load_package_bytes(assets, p)
        if err:
            missing += 1
            print(f"  [缺失] {err}")
            continue
        entries, end, problems = parse_package(data)
        nrec, nmem = len(entries), len(p["members"])
        flag = "OK" if (nrec == nmem and not problems) else "MISMATCH"
        if flag == "OK":
            ok += 1
        else:
            bad += 1
        if flag != "OK" or args.verbose:
            label = p["parts"][0]["name"].split("/")[-1]
            print(f"  [{flag}] {label} parts={len(p['parts'])} joined={len(data)} "
                  f"records={nrec} members={nmem} problems={problems}")
    print(f"ok={ok} mismatch={bad} missing={missing}")


def cmd_unpack(args):
    assets, outdir = args.assets, args.outdir
    fl_path = args.filelist if os.path.isabs(args.filelist) \
        else os.path.join(assets, args.filelist)
    fl = parse_filelist(fl_path)
    os.makedirs(outdir, exist_ok=True)
    unsupported_dir = os.path.join(outdir, "_unsupported")
    count = skipped = 0
    warnings = []
    for p in fl["packages"]:
        data, err = load_package_bytes(assets, p)
        if err:
            skipped += 1
            warnings.append(f"{p['parts'][0]['name']}\tmissing\t{err}")
            continue
        entries, end, problems = parse_package(data)
        if problems and not args.keep_partial:
            skipped += 1
            os.makedirs(unsupported_dir, exist_ok=True)
            dst = os.path.join(unsupported_dir,
                               p["parts"][0]["name"][:-2] + ".r")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            open(dst, "wb").write(data)
            warnings.append(f"{p['parts'][0]['name']}\tparse_problem\t"
                            f"joined={len(data)}\t{problems[:1]}")
            continue
        base = p["parts"][0]["name"]
        for i, e in enumerate(entries):
            if i < len(p["members"]):
                rel = member_path(base, p["members"][i])
            else:
                rel = os.path.splitext(base)[0] + f"_{i}.bin"
            if not path_selected(rel, args):
                continue
            dst = os.path.join(outdir, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            open(dst, "wb").write(e["data"])
            count += 1
    for name in fl["plain"]:
        if not path_selected(name, args):
            continue
        src = os.path.join(assets, name.replace("/", os.sep))
        if os.path.exists(src):
            dst = os.path.join(outdir, name.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            open(dst, "wb").write(open(src, "rb").read())
            count += 1
    if warnings:
        with open(os.path.join(outdir, "_unsupported.txt"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(warnings))
        print(f"注意: {skipped} 个包无法解析, 清单见 _unsupported.txt")
    print(f"已解包 {count} 个文件 -> {outdir}")


def cmd_r_unpack(args):
    data = b"".join(open(f, "rb").read() for f in args.rfile)
    entries, end, problems = parse_package(data)
    os.makedirs(args.outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(args.rfile[0]))[0]
    for i, e in enumerate(entries):
        name = args.names[i] if args.names and i < len(args.names) else f"{base}_{i}.bin"
        open(os.path.join(args.outdir, name), "wb").write(e["data"])
        print(f"  [{i}] len={e['length']} -> {name}")
    if problems:
        print("  警告: 存在无法完整解析的记录(需更多分片拼接):", problems)


def cmd_r_pack(args):
    blobs = [open(f, "rb").read() for f in args.files]
    open(args.out, "wb").write(build_package(blobs))
    print(f"已写出 {args.out}  ({os.path.getsize(args.out)} bytes, {len(blobs)} 条记录)")


def cmd_r_types(args):
    data = b"".join(open(f, "rb").read() for f in args.rfile)
    entries, end, problems = parse_package(data)
    for i, e in enumerate(entries):
        d = e["data"] or b""
        fourcc = d[:4]
        disp = "".join(chr(b) if 32 <= b < 127 else "." for b in fourcc)
        ver = struct.unpack_from("<I", d, 4)[0] if len(d) >= 8 else 0
        dep = struct.unpack_from("<I", d, 8)[0] if len(d) >= 12 else 0
        extra = struct.unpack_from("<I", d, 12)[0] if len(d) >= 16 else 0
        note = ""
        if fourcc[:3] in (b"tex", b"anf", b"ans", b"ams", b"mtr"):
            note = "(CCommonFileHead: type/version/depCount/extraDataSize)"
        print(f'[{i}] size={len(d):>7} type={fourcc!r} [{disp}] '
              f'ver={ver:#x} depCount={dep} extra={extra} {note}')
    if problems:
        print("  警告:", problems)


GUI_CLASSES = ["CGWnd", "CGButton", "CGCheckButton", "CGComboBox", "CGEdit",
               "CGListCtrl", "CGProgress", "CGRichWnd", "CGScrollBar",
               "CGScrollPane", "CGTreeCtrl", "CItemWnd"]


class _GuiReader:
    """`.gui` = `CWindowResource::LoadItem` 的读取顺序(版本 0x271d 实测 213/213 精确)。"""

    def __init__(self, buf, ver):
        self.b, self.p, self.ver = buf, 0, ver

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.p)[0]
        self.p += 4
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.b, self.p)[0]
        self.p += 4
        return v

    def f32(self):
        v = struct.unpack_from("<f", self.b, self.p)[0]
        self.p += 4
        return v

    def u16(self):
        v = struct.unpack_from("<H", self.b, self.p)[0]
        self.p += 2
        return v

    def u8(self):
        v = self.b[self.p]
        self.p += 1
        return v

    def s(self):
        n = self.u32()
        if n > 4096:
            raise ValueError(f"bad string length {n} @{self.p - 4}")
        if self.ver <= 0x2718:                       # 古域2 0x2714 等旧版: UTF-16LE
            if self.p + 2 * n > len(self.b):
                raise ValueError(f"bad string length {n} @{self.p - 4}")
            v = self.b[self.p:self.p + 2 * n].decode("utf-16-le", "replace")
            self.p += 2 * n
            return v
        if self.p + n > len(self.b):
            raise ValueError(f"bad string length {n} @{self.p - 4}")
        v = self.b[self.p:self.p + n].decode("utf-8", "replace")
        self.p += n
        return v


def parse_gui_tree(extra, version):
    """解析 `.gui` 的窗口树;返回 (tree, 是否恰好读完, 已用字节)。"""
    r = _GuiReader(extra, version)

    def item(depth):
        it = {"class": r.s(), "name": r.s()}
        # f[0] = style(u32);f[1..4] = x,y,w,h **有符号 int32**
        # 旧版/运行时窗口常用负 x/y 表示左侧/屏幕外锚点,按 u32 会跑到左上角。
        it["f"] = [r.u32()] + [r.i32() for _ in range(4)]
        # <=0x2711 没有 g[0..1];0x271B 仍会走第二个分支读取
        it["g"] = [r.u32() for _ in range(2)] if version > 0x2711 else [0, 0]
        it["h"] = r.u32()
        it["skin"] = r.s()
        it["i"] = [r.u32() for _ in range(4)]
        it["s16"] = r.s()
        # s17/s26 是 0x271D 才有的两个附加字符串;0x271B/0x271C 没有
        it["s17"] = r.s() if version > 0x271C else ""
        it["s24"] = r.s()
        it["s25"] = r.s()
        it["s26"] = r.s() if version > 0x271C else ""
        more_count = 3 if version <= 0x2719 else (6 if version > 0x271D else 4)
        it["more"] = [r.s() for _ in range(more_count)]
        it["f30"] = r.u32()
        it["fade"] = r.s() if version > 0x2717 else ""
        n = r.u32()
        it["children"] = []
        for i in range(1, n + 1):
            ch = {}
            if i != 1:                               # 第 1 个 sub 用窗口自身的矩形
                ch["rect"] = [r.i32() for _ in range(4)]
            ch["lists"] = []
            for _ in range(r.u32()):
                lst = {"a": r.u32(), "b": r.u32(), "c": r.u32()}
                lst["states"] = []
                for _ in range(r.u32()):
                    st = {"tex": r.u32(),
                          "uv": [r.f32() for _ in range(4)],     # 源矩形 (l,t,r,b) texel
                          "dst": [r.f32() for _ in range(4)],    # 目标矩形 (l,t,r,b)
                          "k": r.u32()}
                    if version <= 0x2714:
                        # 旧版(古域2 0x2714)状态只有 44B:
                        # tex + uv + dst + k + u32(u[0])
                        tail = r.u32()
                        st.update({"alpha": 255, "f": 0,
                                   "u": [tail & 0xFFFF, (tail >> 16) & 0xFFFF,
                                         0xFFFF, 0xFFFF],
                                   "u5": 0})
                    else:
                        st.update({"alpha": r.u8(),
                                   "f": struct.unpack("<b", bytes([r.u8()]))[0],
                                   "u": [r.u16() for _ in range(4)], "u5": r.u32()})
                    lst["states"].append(st)
                ng = r.u32() if version > 0x2715 else 0
                lst["groups"] = [r.b[r.p + 20 * j:r.p + 20 * (j + 1)] for j in range(ng)]
                r.p += 20 * ng
                ch["lists"].append(lst)
            it["children"].append(ch)
        it["windows"] = []
        if depth == 0 or version > 0x271A:
            for _ in range(r.u32()):
                nm = r.s()
                it["windows"].append([nm, item(depth + 1)])
        return it

    tree = item(0)
    return tree, r.p == len(extra), r.p


def parse_gui(path):
    data = open(path, "rb").read()
    head, off = CommonFileHead.parse(data, 0)
    deps = {t.rstrip(b"\x00").decode("latin1"): list(ns) for t, ns in head.deps}
    tree, exact, used = parse_gui_tree(data[off:off + head.extra_size], head.version)
    return {"version": head.version, "deps": deps, "tree": tree,
            "exact": exact, "used": used,
            "extra_size": head.extra_size}


def cmd_gui_info(args):
    import json
    g = parse_gui(args.gui)
    texs = g["deps"].get("tex", [])
    print(f"{args.gui}: version={hex(g['version'])} extra={g['extra_size']} "
          f"exact={g['exact']} texDeps={len(texs)}")
    for t in texs:
        print(f"  tex: {t}")

    def walk(it, depth=0):
        x, y, w, h = it["f"][1], it["f"][2], it["f"][3], it["f"][4]
        print("  " * depth + f"[{it['class']}] {it['name']!r} rect=({x},{y},{w},{h}) "
              f"skin={it['skin']!r}" +
              (f" more={it['more']}" if any(it["more"]) else "") +
              (f" s24={it['s24']!r}" if it["s24"] else "") +
              (f" s25={it['s25']!r}" if it["s25"] else ""))
        for ch in it["children"]:
            print("  " * (depth + 1) + f"sub rect={ch.get('rect')}")
            for lst in ch["lists"]:
                for st in lst["states"]:
                    ti = st["tex"]
                    tn = texs[ti] if ti < len(texs) else f"<{ti}>"
                    print("  " * (depth + 2) +
                          f"state tex={os.path.basename(tn)} src={[round(v, 1) for v in st['uv']]} "
                          f"dst={[round(v, 1) for v in st['dst']]} a={st['alpha']}")
        for nm, sub in it["windows"]:
            print("  " * (depth + 1) + f"window {nm!r}")
            walk(sub, depth + 2)

    walk(g["tree"])
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(g, f, ensure_ascii=False, indent=1)
        print(f"-> {args.json}")


def _find_tex(tex_root, name, cache):
    base = os.path.splitext(os.path.basename(name))[0]
    key = name.replace("\\", "/").lower() or base
    if key in cache:
        return cache[key]
    hit = None
    if tex_root:
        # 先按 .gui 依赖里的相对路径找:main01.tex 在 gui/tex/ 与 fx/tex/
        # 可能同名,只按 basename 会拿错 atlas(U→V 越界、平铺判断失真)。
        rel = os.path.splitext(name.replace("\\", "/"))[0] + ".png"
        if rel and not rel.startswith("/") and ".." not in rel:
            cand = os.path.join(tex_root, rel.replace("/", os.sep))
            if os.path.isfile(cand):
                hit = cand
        if hit is None:
            for r, _, fs in os.walk(tex_root):
                if base + ".png" in fs:
                    hit = os.path.join(r, base + ".png")
                    break
    cache[key] = hit
    return hit


def load_gui_dictionary(config_dir):
    """加载 `config/dictionary/*.txt`: UTF-16/UTF-8 的 `id<TAB>文本` 表。

    `.gui` 里窗口的 `name`(去掉首字符标记)就是这里的 key,例如 `̖90000804` → `90000804`。
    """
    out = {}
    d = os.path.join(config_dir, "dictionary")
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.lower().endswith(".txt"):
            continue
        raw = open(os.path.join(d, f), "rb").read()
        if raw[:2] == b"\xff\xfe":
            txt = raw.decode("utf-16-le", "ignore")
        elif raw[:3] == b"\xef\xbb\xbf":
            txt = raw[3:].decode("utf-8", "ignore")
        else:
            try:
                txt = raw.decode("utf-8")
            except Exception:
                txt = raw.decode("gbk", "ignore")
        if txt.startswith("\ufeff"):
            txt = txt[1:]
        for line in txt.splitlines():
            if "\t" not in line:
                continue
            k, v = line.split("\t", 1)
            k = k.strip()
            if k:
                out[k] = v.rstrip("\r\n")
    return out


_RICH_TAG = re.compile(r"#[a-zA-Z][0-9a-fA-F]*")


def rich_to_plain(text):
    """去掉 `#cRRGGBBAA` 这类富文本标记,返回 (纯文本, 首个颜色)。"""
    color = None
    for m in re.finditer(r"#c([0-9a-fA-F]{6,8})", text):
        h = m.group(1)
        try:
            if len(h) >= 6:
                color = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            pass
    return _RICH_TAG.sub("", text), color


def _gui_argb(c):
    """0xAARRGGBB -> (r,g,b,a)。"""
    return ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF, (c >> 24) & 0xFF)


def _tile_source_piece(img, sx0, sy0, sx1, sy1, out_w, out_h):
    """按引擎的 `SRectInfoEx` 语义取一个源图块。

    `sx0..sx1` / `sy0..sy1` 是带方向的有符号纹理坐标:
      - 终点小于起点 = 反向采样(翻转);
      - 越界部分按整张纹理循环(GL_REPEAT)。
    """
    from PIL import Image
    iw, ih = img.size
    if iw <= 0 or ih <= 0 or out_w <= 0 or out_h <= 0:
        return None
    xmin, xmax = sorted((sx0, sx1))
    ymin, ymax = sorted((sy0, sy1))
    # 把负坐标搬到 >=0,同时保留方向的符号
    ox = math.floor(xmin / iw) * iw
    oy = math.floor(ymin / ih) * ih
    nx0, nx1 = xmin - ox, xmax - ox
    ny0, ny1 = ymin - oy, ymax - oy
    cw = max(1, int(math.ceil(nx1)) + 1)
    ch = max(1, int(math.ceil(ny1)) + 1)
    if cw > 16384 or ch > 16384:
        return None
    tiled = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    # 平铺足够覆盖采样范围;源图块通常不超过一两个图集宽度
    for yy in range(0, ch, ih):
        for xx in range(0, cw, iw):
            tiled.paste(img, (xx, yy))
    box = (int(math.floor(nx0)), int(math.floor(ny0)),
           int(math.ceil(nx1)), int(math.ceil(ny1)))
    box = (max(0, box[0]), max(0, box[1]),
           min(cw, max(box[0] + 1, box[2])),
           min(ch, max(box[1] + 1, box[3])))
    piece = tiled.crop(box)
    if piece.width <= 0 or piece.height <= 0:
        return None
    if sx1 < sx0:
        piece = piece.transpose(Image.FLIP_LEFT_RIGHT)
    if sy1 < sy0:
        piece = piece.transpose(Image.FLIP_TOP_BOTTOM)
    if piece.size != (out_w, out_h):
        piece = piece.resize((out_w, out_h), Image.BILINEAR)
    return piece


def render_gui(g, tex_root, out_png, bg=(24, 26, 32, 255), texts=None,
               font_path=None, draw_all_lists=False, flip_uv=True,
               first_sibling_only=True, apply_rotation=False, hide_windows=None,
               tile_wrap=True):
    """把 `.gui` 合成为 PNG(需要 Pillow)。rect0=源 texel 矩形,rect1=目标矩形。"""
    from PIL import Image, ImageDraw, ImageFont
    texs = g["deps"].get("tex", [])
    tree = g["tree"]
    texts = texts or {}
    W, H = int(tree["f"][3]), int(tree["f"][4])
    if not (0 < W <= 4096 and 0 < H <= 4096):
        W, H = 1280, 720
    canvas = Image.new("RGBA", (W, H), bg)
    cache = {}
    stats = {"states": 0, "drawn": 0, "skipped": 0, "wrapped": 0, "texts": 0}

    if font_path is None:
        for cand in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
                     r"C:\Windows\Fonts\simhei.ttf"):
            if os.path.exists(cand):
                font_path = cand
                break

    def win_score(it):
        """窗口“内容量”:状态数 + 子窗口递归;用于同矩形备选里挑代表性窗口。"""
        n = sum(len(lst["states"]) for ch in it["children"] for lst in ch["lists"])
        for _, sub in it.get("windows", []):
            n += 1 + win_score(sub)
        return n

    def draw_text(it, x, y, w, h, text_lst=None):
        key = (it.get("name") or "").strip()
        if not key:
            return
        txt = texts.get(key) or texts.get(key[1:])
        if not txt:
            return
        plain, color = rich_to_plain(txt)
        plain = plain.strip()
        if not plain:
            return
        # `.gui` 的文本颜色在 SChildImageList.b(ARGB),不是固定黑色:
        # 例如登录按钮 0xFFFFFBE8(奶白)、普通说明 0xFFA9793C(棕)。
        state_col = None
        bc_col = None
        if text_lst:
            b = int(text_lst.get("b") or 0)
            if b:
                state_col = _gui_argb(b)
            c = int(text_lst.get("c") or 0)
            if c and ((c >> 24) & 0xFF) > 0:
                bc_col = _gui_argb(c)
        if color:
            col = (color[0], color[1], color[2], 255)
        elif state_col:
            col = state_col
        else:
            col = (169, 121, 60, 255)  # 引擎最常见默认棕
        # `item.f[0]` = style: 0x03000000 = 左/中/右, 0x0C000000 = 上/中/下
        # (来自 CGWnd::DrawText 对 GetStyle() 的位测试)
        try:
            style = int(it.get("f", [0])[0])
        except Exception:
            style = 0
        h_align = 1 if (style & 0x01000000) else (2 if (style & 0x02000000) else 0)
        v_align = 1 if (style & 0x04000000) else (2 if (style & 0x08000000) else 0)
        # i[0] = 字号(px), i[1] = 字间距, i[2] = 行间距(加在字号上)
        try:
            fsz = int(it.get("i", [0])[0])
        except Exception:
            fsz = 0
        if not (6 <= fsz <= 200):
            fsz = max(9, min(int(h * 0.55), 40))
        fsz = max(6, min(fsz, int(max(h, 1) * 1.4)))
        try:
            row_dist = float(it.get("i", [0, 0, 0])[2])
        except Exception:
            row_dist = 0.0
        if not (0 < row_dist < 400):
            row_dist = 0.0
        lh = fsz + row_dist if row_dist else fsz * 1.2
        try:
            font = ImageFont.truetype(font_path, fsz) if font_path else ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()
        # 文本单独画到透明层,才能正确表现 c 的半透明描边/阴影
        # 注意不要覆盖 lh(行高),否则垂直居中会按整窗高度偏移。
        layer_w, layer_h = max(1, int(round(w))), max(1, int(round(h)))
        layer = Image.new("RGBA", (layer_w, layer_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)

        def measure(s):
            try:
                return d.textlength(s, font=font)
            except Exception:
                return len(s) * fsz * 0.6

        def wrap_line(s, maxw):
            if not s:
                return [""]
            toks = re.findall(r"[A-Za-z0-9_\-\.'/]+|.", s)
            out, cur = [], ""
            for tok in toks:
                if cur and measure(cur + tok) > maxw:
                    out.append(cur)
                    cur = ""
                if not cur and measure(tok) > maxw:
                    for ch in tok:
                        if cur and measure(cur + ch) > maxw:
                            out.append(cur)
                            cur = ""
                        cur += ch
                else:
                    cur += tok
            if cur:
                out.append(cur)
            return out or [""]

        lines = plain.split("\n")
        if (it.get("class") == "CGRichWnd" or "\n" in plain) and w > 8:
            wrapped = []
            for ln in lines:
                wrapped += wrap_line(ln, w - 4)
            lines = wrapped
        else:
            # 普通单行控件: 超宽时按比例缩小字号(引擎是裁剪, 这里保证可读)
            try:
                widest = max(measure(s) for s in lines)
                if w > 4 and widest > w * 1.1 and widest > 0:
                    fsz = max(8, int(fsz * (w * 1.1) / widest))
                    font = ImageFont.truetype(font_path, fsz) if font_path else font
                    lh = fsz * 1.2
            except Exception:
                pass
        th = lh * len(lines)
        if v_align == 1:
            ty = y + max(0.0, (h - th) / 2)
        elif v_align == 2:
            ty = y + max(0.0, h - th)
        else:
            ty = y + 2
        # c = WndTextBcColor: 引擎的字体顶点同时带主色/底色两色。
        # 预览用 1px 八向描边近似,alpha 保留 c 的高字节。
        outline = None
        if bc_col and bc_col != col:
            outline = [(-1, -1), (0, -1), (1, -1), (-1, 0),
                       (1, 0), (-1, 1), (0, 1), (1, 1)]
        for i, ln in enumerate(lines):
            tw = measure(ln)
            if h_align == 1:
                tx = max(0.0, (w - tw) / 2)
            elif h_align == 2:
                tx = max(0.0, w - tw - 2)
            else:
                tx = 2.0
            ty_line = ty - y + i * lh
            if outline:
                for dx, dy in outline:
                    d.text((tx + dx, ty_line + dy), ln, font=font, fill=bc_col)
            d.text((tx, ty_line), ln, font=font, fill=col)
        canvas.alpha_composite(layer, (int(round(x)), int(round(y))))
        stats["texts"] += 1

    def draw(it, ox, oy):
        x, y = ox + it["f"][1], oy + it["f"][2]
        w, h = it["f"][3], it["f"][4]
        if not all(abs(v) < 8192 for v in (x, y, w, h)) or w <= 0 or h <= 0:
            x, y, w, h = ox, oy, 0, 0
        if w and h:
            canvas.alpha_composite(
                Image.new("RGBA", (int(w), int(h)), (60, 80, 110, 40)), (int(x), int(y)))
        text_lst = None
        for ch in it["children"]:
            ch_lists = ch["lists"]
            if text_lst is None:
                for lst in ch_lists:
                    if lst["a"] == 0x00020001:
                        text_lst = lst
                        break
                if text_lst is None:
                    text_lst = next(iter(ch_lists), None)
            if not draw_all_lists:
                # 引擎每个控件只画一个状态列表(常态/悬停/按下/禁用之一),
                # a = (listIndex<<16)|1,实测 INDEX 2 = 常态
                chosen = None
                for want in (0x00020001, 0x00010001, 0x00030001):
                    for lst in ch_lists:
                        if lst["a"] == want and lst["states"]:
                            chosen = lst
                            break
                    if chosen:
                        break
                if chosen is None:
                    for lst in ch_lists:
                        if lst["states"]:
                            chosen = lst
                            break
                ch_lists = [chosen] if chosen else []
            for lst in ch_lists:
                for st in lst["states"]:
                    stats["states"] += 1
                    ti = st["tex"]
                    tn = texs[ti] if ti < len(texs) else ""
                    dl, dt, dr, db = st["dst"]
                    if not all(abs(v) < 20000 for v in (dl, dt, dr, db)):
                        stats["skipped"] += 1
                        continue
                    dw, dh = int(round(abs(dr - dl))), int(round(abs(db - dt)))
                    if dw <= 0 or dh <= 0:
                        stats["skipped"] += 1
                        continue
                    px, py = int(round(x + min(dl, dr))), int(round(y + min(dt, db)))
                    kcol = st.get("k", 0xFFFFFFFF)
                    ka = ((kcol >> 24) & 0xFF) / 255.0
                    a = st["alpha"] / 255.0 * ka
                    # `tex=-1`/空纹理名: 预览器只把 u1 bit3(0x08) 当纯色块
                    # (登录页米黄底、全屏压暗层);其余是动态控件占位,不画。
                    if not tn:
                        flags = st["u"][1] if len(st.get("u", [])) > 1 else 0
                        if not (flags & 0x08) or a <= 0:
                            stats["skipped"] += 1
                            continue
                        col = _gui_argb(kcol)
                        solid = Image.new("RGBA", (dw, dh), (col[0], col[1], col[2], int(255 * a)))
                        lf, tp = max(0, px), max(0, py)
                        rt, bt = min(W, px + dw), min(H, py + dh)
                        if rt > lf and bt > tp:
                            canvas.alpha_composite(
                                solid.crop((lf - px, tp - py, rt - px, bt - py)), (lf, tp))
                            stats["solid"] = stats.get("solid", 0) + 1
                        else:
                            stats["skipped"] += 1
                        continue
                    path = _find_tex(tex_root, tn, cache)
                    if not path:
                        stats["skipped"] += 1
                        continue
                    img = cache.get("_img_" + path)
                    if img is None:
                        img = Image.open(path).convert("RGBA")
                        cache["_img_" + path] = img
                    sl, stp, sr, sb = st["uv"]
                    if not all(abs(v) < 20000 for v in (sl, stp, sr, sb)):
                        stats["skipped"] += 1
                        continue
                    src_w = abs(sr - sl)
                    src_h = abs(sb - stp)
                    if src_w <= 0 or src_h <= 0:
                        stats["skipped"] += 1
                        continue
                    flags = st["u"][1] if len(st.get("u", [])) > 1 else 0
                    wper = st["u"][2] if len(st.get("u", [])) > 2 else 65535
                    hper = st["u"][3] if len(st.get("u", [])) > 3 else 65535
                    # u1 bit4/bit5 = 反向平铺; u2/u3 = 平铺周期(纹理像素)
                    eff_w = -wper if (flags & 0x10) else wper
                    eff_h = -hper if (flags & 0x20) else hper
                    if tile_wrap and abs(eff_w) < src_w:
                        ratio_w = max(abs(eff_w) / src_w, 1.0 / 16.0)
                    else:
                        ratio_w = 1.0
                    if tile_wrap and abs(eff_h) < src_h:
                        ratio_h = max(abs(eff_h) / src_h, 1.0 / 16.0)
                    else:
                        ratio_h = 1.0
                    if eff_w < 0:
                        sx0 = sr
                        sx1 = sr - (sr - sl) * ratio_w
                    else:
                        sx0 = sl
                        sx1 = sl + (sr - sl) * ratio_w
                    if eff_h < 0:
                        sy0 = sb
                        sy1 = sb - (sb - stp) * ratio_h
                    else:
                        sy0 = stp
                        sy1 = stp + (sb - stp) * ratio_h
                    if not flip_uv and ratio_w >= 1.0 and ratio_h >= 1.0:
                        # 仅用于对照旧预览:关闭负宽/负高翻转
                        sx0, sx1 = min(sl, sr), max(sl, sr)
                        sy0, sy1 = min(stp, sb), max(stp, sb)
                    if (sr < sl) or (sb < stp):
                        stats["flipped"] = stats.get("flipped", 0) + 1
                    if sx0 < 0 or sy0 < 0 or max(sx0, sx1) > img.width or max(sy0, sy1) > img.height:
                        stats["wrapped"] += 1
                    tile_dw = max(1, int(round(dw * ratio_w)))
                    tile_dh = max(1, int(round(dh * ratio_h)))
                    piece = _tile_source_piece(img, sx0, sy0, sx1, sy1,
                                               tile_dw, tile_dh)
                    if piece is None:
                        stats["skipped"] += 1
                        continue
                    if ratio_w < 1.0 or ratio_h < 1.0:
                        tiled = Image.new("RGBA", (dw, dh), (0, 0, 0, 0))
                        start_x = dw if eff_w < 0 else 0
                        step_x = -tile_dw if eff_w < 0 else tile_dw
                        start_y = dh if eff_h < 0 else 0
                        step_y = -tile_dh if eff_h < 0 else tile_dh
                        nx = max(1, int(math.ceil(dw / tile_dw)) + 1)
                        ny = max(1, int(math.ceil(dh / tile_dh)) + 1)
                        for yy in range(ny):
                            ty = int(round(start_y + yy * step_y))
                            top = ty - tile_dh if eff_h < 0 else ty
                            for xx in range(nx):
                                tx = int(round(start_x + xx * step_x))
                                left = tx - tile_dw if eff_w < 0 else tx
                                tiled.alpha_composite(piece, (left, top))
                        piece = tiled
                        stats["tiled"] = stats.get("tiled", 0) + 1
                    # f(signed byte) = 旋转角, 单位“半圈”: 角度 = f/128 * 180°
                    rot = int(st.get("f", 0))
                    if apply_rotation and rot:
                        deg = rot * 180.0 / 128.0
                        piece = piece.rotate(-deg, resample=Image.BICUBIC, expand=True)
                        px = int(round(x + dl + (dw - piece.width) / 2))
                        py = int(round(y + dt + (dh - piece.height) / 2))
                        stats["rotated"] = stats.get("rotated", 0) + 1
                    # state.k = ARGB 染色/透明度(0xFFFFFFFF=白色不变, 0xFFC0C0C0=灰化禁用)
                    if a < 0.996:
                        piece.putalpha(piece.getchannel("A").point(lambda v: int(v * a)))
                    if kcol != 0xFFFFFFFF:
                        tr = (kcol >> 16) & 0xFF
                        tg = (kcol >> 8) & 0xFF
                        tb = kcol & 0xFF
                        rch, gch, bch, ach = piece.split()
                        piece = Image.merge("RGBA", (
                            rch.point(lambda v: v * tr // 255),
                            gch.point(lambda v: v * tg // 255),
                            bch.point(lambda v: v * tb // 255),
                            ach))
                        stats["tinted"] = stats.get("tinted", 0) + 1
                    lf, tp = max(0, px), max(0, py)
                    rt, bt = min(W, px + piece.width), min(H, py + piece.height)
                    if rt > lf and bt > tp:
                        canvas.alpha_composite(
                            piece.crop((lf - px, tp - py, rt - px, bt - py)), (lf, tp))
                        stats["drawn"] += 1
                    else:
                        stats["skipped"] += 1
        if w and h:
            draw_text(it, x, y, w, h, text_lst)
        wins = it["windows"]
        chosen = set()
        groups = {}
        if first_sibling_only:
            def is_overlay(sub):
                if sub.get("class") != "CGButton":
                    return False
                for ch in sub["children"]:
                    for lst in ch["lists"]:
                        for st in lst["states"]:
                            if st["tex"] < len(texs) and st["alpha"] > 0:
                                return False
                return True

            for idx, (nm, sub) in enumerate(wins):
                key = tuple(round(v) for v in sub["f"][1:5])
                groups.setdefault(key, []).append(idx)
            for key, idxs in groups.items():
                if len(idxs) > 1:
                    # 透明点击层(如全屏“点击继续”)始终保留;
                    # 其余同矩形兄弟是模式/状态备选,选内容最多的一个作代表。
                    cand = [i for i in idxs if not is_overlay(wins[i][1])]
                    if cand:
                        best = max(cand, key=lambda i: (win_score(wins[i][1]), -i))
                        chosen.add(best)
                    for i in idxs:
                        if is_overlay(wins[i][1]):
                            chosen.add(i)
        for idx, (nm, sub) in enumerate(wins):
            if hide_windows and nm in hide_windows:
                stats["hidden_windows"] = stats.get("hidden_windows", 0) + 1
                continue
            key = tuple(round(v) for v in sub["f"][1:5])
            if first_sibling_only and len(groups.get(key, [])) > 1 and idx not in chosen:
                stats["hidden_windows"] = stats.get("hidden_windows", 0) + 1
                continue
            draw(sub, x, y)

    draw(tree, -tree["f"][1], -tree["f"][2])
    canvas.save(out_png)
    return W, H, stats


def cmd_gui_render(args):
    g = parse_gui(args.gui)
    texts = load_gui_dictionary(args.config) if args.config else {}
    try:
        W, H, st = render_gui(g, args.tex_root, args.out, texts=texts,
                              flip_uv=not args.no_uv_flip,
                              first_sibling_only=not args.all_windows,
                              apply_rotation=args.rotation,
                              hide_windows=set(args.hide_window),
                              tile_wrap=args.wrap_tile)
    except ImportError:
        print("需要 Pillow: uv run --with pillow python gamma_formats.py gui-render ...")
        return
    print(f"{args.gui} -> {args.out}  {W}x{H}  exact={g['exact']}  "
          f"states={st['states']} drawn={st['drawn']} solid={st.get('solid', 0)} "
          f"wrapped={st['wrapped']} "
          f"flipped={st.get('flipped', 0)} rotated={st.get('rotated', 0)} "
          f"tiled={st.get('tiled', 0)} hidden={st.get('hidden_windows', 0)} "
          f"skipped={st['skipped']} texts={st['texts']}")


def cmd_gui_batch(args):
    os.makedirs(args.outdir, exist_ok=True)
    texts = load_gui_dictionary(args.config) if args.config else {}
    if texts:
        print(f"字典 {len(texts)} 条 ({args.config})")
    ok = bad = 0
    drawn = total = wrapped = tcount = flipped = rotated = hidden = tiled = solid = 0
    for r, _, fs in os.walk(args.dir):
        for f in sorted(fs):
            if not f.lower().endswith(".gui"):
                continue
            src = os.path.join(r, f)
            rel = os.path.relpath(src, args.dir)
            out = os.path.join(args.outdir, os.path.splitext(rel)[0] + ".png")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            try:
                g = parse_gui(src)
                W, H, st = render_gui(g, args.tex_root, out, texts=texts)
                drawn += st["drawn"]
                total += st["states"]
                wrapped += st["wrapped"]
                tcount += st["texts"]
                flipped += st.get("flipped", 0)
                rotated += st.get("rotated", 0)
                hidden += st.get("hidden_windows", 0)
                tiled += st.get("tiled", 0)
                solid += st.get("solid", 0)
                if not args.quiet:
                    print(f"  [ok] {rel} {W}x{H} exact={g['exact']} "
                          f"drawn={st['drawn']}/{st['states']} text={st['texts']}")
                ok += 1
            except ImportError:
                print("需要 Pillow: uv run --with pillow ...")
                return
            except Exception as e:
                print(f"  [err] {rel}: {e}")
                bad += 1
    print(f"done: ok={ok} failed={bad}  states={total} drawn={drawn} "
          f"solid={solid} wrapped={wrapped} flipped={flipped} rotated={rotated} "
          f"tiled={tiled} hidden={hidden} texts={tcount}")


def _eft_prop_marks(ex):
    """扫描 `u32 len + UTF-16 'fx/...'`,得到 prop 名字的 (offset, name) 列表。

    prop 名字统一以 `fx/` 开头(如 `fx/loop`、`fx/attack_1`),而内部的资源路径用
    反斜杠(`fx\\ani\\...`),所以这个标记是可靠的切分点。
    """
    out = []
    i, n = 0, len(ex)
    while i + 6 <= n:
        ln = struct.unpack_from("<I", ex, i)[0]
        if 3 <= ln <= 200 and i + 4 + 2 * ln <= n:
            try:
                s = ex[i + 4:i + 4 + 2 * ln].decode("utf-16-le")
            except Exception:
                s = ""
            if s.startswith("fx/") and all(32 <= ord(c) < 127 for c in s):
                out.append((i, s))
                i += 4 + 2 * ln
                continue
        i += 1
    return out


def _eft_utf16_strings(buf, start, end):
    out = []
    i = start
    while i + 4 <= end:
        ln = struct.unpack_from("<I", buf, i)[0]
        if 2 <= ln <= 120 and i + 4 + 2 * ln <= end:
            try:
                s = buf[i + 4:i + 4 + 2 * ln].decode("utf-16-le")
            except Exception:
                s = ""
            if s and all(0x20 <= ord(c) < 0xD800 or 0xE000 <= ord(c) < 0xFFFD for c in s):
                out.append(s)
                i += 4 + 2 * ln
                continue
        i += 1
    return out


def _safe(s):
    """控制台是 GBK,把非常用字符替换掉,避免 UnicodeEncodeError。"""
    if not isinstance(s, str):
        return s
    return "".join(c if (0x20 <= ord(c) < 0x2E80 or 0x4E00 <= ord(c) <= 0x9FFF) else "."
                   for c in s)


_EFT_UNIT_TYPES = {
    0: "CParticleSystem", 1: "CSprite", 2: "CPolyTrail", 3: "CCameraShake",
    4: "CCameraFilter", 5: "CModelConsole", 6: "CDynamicLight", 7: "CNull",
    8: "C3DAudio", 9: "CModelMaterial", 10: "CPolyChain",
    11: "CModelAnimation", 12: "CGeometry",
}

# 字段语义来自各 `*Prop::GetBaseProp` / `GetAdvancedProp` 里的 UTF-32 属性名
_EFT_PROP_KEYS = {
    0: {
        "a184": "挂接点偏移和缩放", "a188": "绕速度方向旋转",
        "f192": "旋转同步", "f196": "最大发射间隔",
        "v200": "最小发射间隔", "v212": "每次发射粒子数",
        "v224": "每次发射粒子数(2)", "v236": "最小尺寸",
        "a248": "最大尺寸", "a252": "发射平面法线",
        "a256": "最小速度", "a260": "最大速度", "a264": "阻尼系数",
        "f268": "最小角速度", "f272": "最大角速度",
        "a276": "最小生命周期", "a280": "最大生命周期",
        "a284": "发射器最小半径", "a288": "发射器最大半径",
        "a296": "矩形长宽比", "a300": "长宽密度比",
        "a304": "角点平分", "a308": "跟随方式", "a324": "混合模式",
    },
    1: {
        "v180": "调节类型", "v192": "曲线参数", "a204": "动作名",
        "a208": "朝向", "f212": "旋转同步", "f216": "最大发射间隔",
        "a324": "圆周起始", "v236": "法线模式0", "v244": "法线模式1",
    },
    2: {
        "f196": "改变链节大小", "f200": "法线类型",
        "a224": "轨迹单元生命周期", "a204": "圆周起始",
    },
    3: {
        "a180": "震动等级1", "a184": "震动等级2",
        "f188": "震动等级3", "f192": "泛光范围",
    },
    4: {
        "f196": "Z测试", "f200": "过滤类型1", "f204": "过滤类型2",
        "a192": "影响范围高度Z",
    },
    5: {
        "s240": "动作名", "a192": "动作名索引", "a196": "同步模式",
        "a200": "同步模型动作", "u244": "动画文件索引1",
        "u246": "动画文件索引2",
    },
    8: {"s192": "音乐音效文件名"},
    9: {"applyClass": "应用类名", "shaderType": "Shader类型"},
    12: {
        "a200": "UV模式", "a204": "UV方式", "a208": "贴图拉伸",
        "a212": "UV包围方式", "f228": "几何体类型", "f232": "打光开关",
        "f236": "打光", "f240": "轴向", "f244": "轴向上方最小",
        "f248": "轴向上方最大", "a256": "轴向下方最大",
        "a260": "轴向下方最大(2)", "a264": "圆周最大分段",
        "a268": "圆周最大分段(2)", "a276": "圆周起始",
    },
}


def _eft_props(u, t):
    """把偏移字段映射成编辑器里的中文属性名(值保持文件原始类型)。"""
    float_keys = {
        0: {"a248", "a252", "a256", "a276", "a280", "a284", "a288"},
        1: {"a204", "a208", "a324"},
        2: {"a224"},
        4: {"a192"},
        5: {"a192", "a196", "a200"},
        12: {"a256", "a260"},
    }.get(t, set())
    out = {}
    for key, name in _EFT_PROP_KEYS.get(t, {}).items():
        if key not in u:
            continue
        v = u[key]
        if key in float_keys and isinstance(v, int):
            v = struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0]
        out[name] = v
    return out


class _EftReader:
    """`.eft` 的小端读取器; 越界/异常长度立刻报错, 便于做“恰好读完”校验。"""

    def __init__(self, buf, version):
        self.b, self.version, self.p = buf, version, 0

    def need(self, n):
        if n < 0 or self.p + n > len(self.b):
            raise ValueError(f"eof: need {n} at {self.p}/{len(self.b)}")

    def u8(self):
        self.need(1); v = self.b[self.p]; self.p += 1; return v

    def u16(self):
        self.need(2)
        v = struct.unpack_from("<H", self.b, self.p)[0]; self.p += 2; return v

    def u32(self):
        self.need(4)
        v = struct.unpack_from("<I", self.b, self.p)[0]; self.p += 4; return v

    def f32(self):
        self.need(4)
        v = struct.unpack_from("<f", self.b, self.p)[0]; self.p += 4; return v

    def skip(self, n):
        self.need(n); self.p += n

    def raw(self, n):
        self.need(n); v = self.b[self.p:self.p + n]; self.p += n; return v.hex()

    def count(self, limit=1000000):
        n = self.u32()
        if n > limit:
            raise ValueError(f"count {n} at {self.p - 4}")
        return n

    def string(self):
        n = self.count(4096)
        self.need(2 * n)
        v = self.b[self.p:self.p + 2 * n].decode("utf-16-le", "replace")
        self.p += 2 * n
        return v

    def rawstr(self):
        """std::string: u32 字节数 + 单字节文本(不是 UTF-16)。"""
        n = self.count(4096)
        self.need(n)
        v = self.b[self.p:self.p + n].decode("latin1", "replace")
        self.p += n
        return v

    def f32s(self):
        return [self.f32() for _ in range(self.count())]

    def u32s(self):
        return [self.u32() for _ in range(self.count())]

    def multvec(self):
        n = self.count()
        return [self.f32s() for _ in range(n)]


def _eft_vec3(r):
    return [r.f32(), r.f32(), r.f32()]


def _eft_unit_base(u, r, has_tex=True):
    """CEffectUnitProp::ReadIndexData (基类, @0x2c9140)。

    贴图索引只在存在 `tex` 依赖时才会被读取(无依赖时引擎跳过整个循环)。
    """
    if r.version > 0x2712:
        u["id"] = r.u32()
    n = r.count(4096)
    if has_tex:
        u["textures"] = [r.u32() for _ in range(n)]
    else:
        u["textures"] = []
    n = r.count()
    u["values"] = [r.f32() for _ in range(n)]
    n = r.count()
    r.skip(4 * n)
    u["mv1"] = r.multvec()
    u["mv2"] = r.multvec()
    n = r.count()
    u["vec3s"] = [_eft_vec3(r) for _ in range(n)]
    n = r.count(4096)
    if r.version <= 0x2717:
        u["colors"] = [r.u32() for _ in range(n)]
    else:
        u["colors"] = [[r.u32() for _ in range(r.count())] for _ in range(n)]
    n = r.count(4096)
    if r.version <= 0x2715:
        u["bytes"] = r.raw(n)
    else:
        u["bytes"] = [r.raw(r.count()) for _ in range(n)]
    u["a"], u["b"], u["c"], u["d"] = r.u32(), r.u32(), r.u32(), r.u32()
    u["u88"], u["f90"], u["f91"] = r.u16(), r.u8(), r.u8()
    if r.version > 0x2716:
        u["u92"] = r.u16()
    if r.version > 0x2718:
        u["quat"] = [r.f32() for _ in range(4)]
    if r.version > 0x271A:
        u["e"] = r.u32()
    u["s1"], u["s2"], u["s3"] = r.string(), r.string(), r.string()
    n = r.count(10000)
    u["names"] = [r.string() for _ in range(n)]


def _eft_unit(u, r, t, has_tex=True):
    """按 type 翻译各 `*Prop::ReadIndexData` 的专属前缀, 然后读基类。"""
    if t == 0:      # CParticleSystemProp
        v = r.u32()
        u["version"] = v
        u["a180"], u["a188"] = r.u32(), r.u32()
        u["f192"], u["f196"] = r.f32(), r.f32()
        u["v200"] = _eft_vec3(r)
        u["v212"], u["v224"], u["v236"] = _eft_vec3(r), _eft_vec3(r), _eft_vec3(r)
        u["a252"], u["a256"], u["a260"], u["a264"] = r.u32(), r.u32(), r.u32(), r.u32()
        u["f268"], u["f272"] = r.f32(), r.f32()
        for name in ("a276", "a284", "a288", "a296", "a300", "a304",
                     "a312", "a292", "a316", "a280", "a324", "a328"):
            u[name] = r.u32()
        u["f336"] = r.u8()
        if v != 0:
            u["a184"], u["a332"] = r.u32(), r.u32()
        if v > 1:
            u["a308"] = r.u32()
        if v > 2:
            u["a320"] = r.u32()
        if v > 3:
            u["a248"] = r.u32()
    elif t == 1:    # CSpriteProp
        v = r.u32()
        u["version"] = v
        u["a204"], u["a208"] = r.u32(), r.u32()
        u["f252"] = r.f32()
        u["v180"], u["v192"] = _eft_vec3(r), _eft_vec3(r)
        u["f212"], u["f216"] = r.f32(), r.f32()
        u["a220"], u["a224"], u["a228"] = r.u32(), r.u32(), r.u32()
        u["f232"] = r.u8()
        if v != 0:
            u["f233"] = r.u8()
        if v > 1:
            u["f234"] = r.u8()
        if v > 2:
            u["f235"] = r.u8()
        if v > 3:
            u["a324"] = r.u32()
        if v > 4:
            u["r236"], u["r244"] = r.raw(8), r.raw(8)
    elif t == 2:    # CPolyTrailProp
        d = r.u32()
        u["version"] = d
        u["f177"], u["f178"], u["f179"], u["f180"], u["f181"] = (
            r.u8(), r.u8(), r.u8(), r.u8(), r.u8())
        u["r184"] = r.raw(12)
        u["f196"], u["f200"] = r.f32(), r.f32()
        u["a208"], u["a212"], u["a216"] = r.u32(), r.u32(), r.u32()
        u["f220"] = r.u8()
        if d != 0:
            u["a224"] = r.u32()
        if d > 1:
            u["a228"] = r.u32()
        if d > 2:
            u["a232"] = r.u32()
        if d > 3:
            u["a204"] = r.u32()
    elif t == 3:    # CCameraShakeProp
        v = r.u32()
        u["version"] = v
        u["a180"], u["a184"] = r.u32(), r.u32()
        u["f188"], u["f192"] = r.f32(), r.f32()
        if v != 0:
            u["a196"] = r.u32()
    elif t == 4:    # CCameraFilterProp
        v = r.u32()
        u["version"] = v
        u["a180"], u["a188"], u["a184"] = r.u32(), r.u32(), r.u32()
        u["f196"], u["f200"], u["f204"] = r.f32(), r.f32(), r.f32()
        if v != 0:
            u["f208"], u["a192"], u["f209"], u["f210"] = r.u8(), r.u32(), r.u8(), r.u8()
    elif t == 5:    # CModelConsoleProp
        v = r.u32()
        u["version"] = v
        u["s240"] = r.string()
        u["u232"] = r.u16()
        u["u244"], u["u246"] = r.u16(), r.u16()
        u["a248"], u["a252"] = r.u32(), r.u32()
        u["a192"], u["a196"], u["a200"] = r.u32(), r.u32(), r.u32()
        u["s208"], u["s236"] = r.string(), r.string()
        u["r180"] = r.raw(12)
        u["pairs"] = [[r.u16(), r.u8()] for _ in range(7)]
        if v > 1:
            u["f288"] = r.u8()
    elif t == 7:    # CNullProp
        d = r.u32()
        u["version"] = d
        u["r180"] = r.raw(12)
        u["f196"] = r.u8()
        if d != 0:
            u["a192"] = r.u32()
            u["f197"] = r.u8()
        else:
            u["f197_skip"] = r.raw(2)
            u["f198"] = r.u8()
        if d > 1:
            u["f198b"] = r.u8()
    elif t == 8:    # C3DAudioProp
        d = r.u32()
        u["version"] = d
        u["a196"], u["f200"], u["f204"] = r.u32(), r.f32(), r.f32()
        if d != 0:
            u["f208"] = r.u8()
        u["s192"] = r.string()
    elif t == 9:    # CModelMaterialProp
        d = r.u32()
        u["version"] = d
        u["applyClass"] = r.string()
        u["shaderType"] = r.u8()
        if d > 5:
            u["v178"] = r.raw(12)
        elif d != 0:
            u["v178"] = r.raw(8)
        if 2 <= d <= 5:
            u["s"] = r.string()
        if 3 <= d <= 5:
            u["v29"] = r.raw(8)
    elif t == 12:   # CGeometryProp
        d = r.u32()
        u["version"] = d
        u["f199"], u["f195"], u["f196"] = r.u8(), r.u8(), r.u8()
        u["a208"], u["a212"], u["a200"], u["a204"] = r.u32(), r.u32(), r.u32(), r.u32()
        u["f197"], u["f198"] = r.u8(), r.u8()
        u["f272"], u["f273"], u["f274"], u["f275"] = r.u8(), r.u8(), r.u8(), r.u8()
        u["a180"] = r.u32()
        u["f184"] = r.u8()
        u["a188"] = r.u32()
        u["f192"], u["f193"], u["f194"] = r.u8(), r.u8(), r.u8()
        u["r216"] = r.raw(12)
        u["f228"], u["f232"], u["f236"] = r.f32(), r.f32(), r.f32()
        u["f240"], u["f244"], u["f248"] = r.f32(), r.f32(), r.f32()
        u["u252"], u["u254"] = r.u16(), r.u16()
        u["a256"], u["a260"], u["a264"], u["a268"] = r.u32(), r.u32(), r.u32(), r.u32()
        if d != 0:
            u["a276"] = r.u32()
    else:
        raise ValueError(f"unknown unit type {t}")
    _eft_unit_base(u, r, has_tex)


def parse_eft(extra, version, has_tex=True):
    """解析 `.eft`(CEffectGroup + CEffectProp):返回 props 列表。

    `CEffectGroup::OnReadIndexData`: u16 propCount + 每个 prop(ReadString 名字 +
    `CEffectProp::OnReadIndexData`)。`CEffectProp` 的头部结构:

        [ver>0x2711] u32 dur(毫秒), u32 nStr + nStr × ReadString, ReadString(拼接名)
        f32 ×3 pos, f32 ×3 rot
        [ver>0x2713] u8 b92
        [ver>0x2719] u8 b93, u8 b94
        u16 unitCount
        unitCount × (u16 type + 该类型的 unit 数据)

    unit 的 type → 类映射来自 `CRenderer::CreateEffectUnitProp` 的 switch:
    0 CParticleSystem / 1 CSprite / 2 CPolyTrail / 3 CCameraShake /
    4 CCameraFilter / 5 CModelConsole / 6 CDynamicLight / 7 CNull /
    8 C3DAudio / 9 CModelMaterial / 10 CPolyChain / 11 CModelAnimation /
    12 CGeometry。每个子类先读专属字段, 再调基类 `ReadIndexData`。

    返回的每个 prop 带 `unit_details`;`unit_exact` 表示 unit 段是否恰好读完
    (与下一个 prop 的起点对齐)。
    """
    if len(extra) < 6:
        return []
    marks = _eft_prop_marks(extra)
    props = []
    for k, (p, name) in enumerate(marks):
        q = p + 4 + 2 * len(name)
        rec = {"name": name}
        if version > 0x2711:
            rec["dur"] = struct.unpack_from("<I", extra, q)[0]
            q += 4
            ns = struct.unpack_from("<I", extra, q)[0]
            q += 4
            strs = []
            for _ in range(ns):
                if q + 4 > len(extra):
                    break
                sl = struct.unpack_from("<I", extra, q)[0]
                q += 4
                strs.append(extra[q:q + 2 * sl].decode("utf-16-le", "replace"))
                q += 2 * sl
            rec["strs"] = strs
            if q + 4 <= len(extra):
                sl = struct.unpack_from("<I", extra, q)[0]
                q += 4
                rec["s32"] = extra[q:q + 2 * sl].decode("utf-16-le", "replace")
                q += 2 * sl
        if q + 24 > len(extra):
            break
        rec["pos"] = list(struct.unpack_from("<3f", extra, q))
        q += 12
        rec["rot"] = list(struct.unpack_from("<3f", extra, q))
        q += 12
        if version > 0x2713 and q < len(extra):
            rec["b92"] = extra[q]
            q += 1
        if version > 0x2719 and q + 2 <= len(extra):
            rec["b93"] = extra[q]
            rec["b94"] = extra[q + 1]
            q += 2
        if q + 2 > len(extra):
            break
        rec["units"] = struct.unpack_from("<H", extra, q)[0]
        q += 2
        end = marks[k + 1][0] if k + 1 < len(marks) else len(extra)
        rec["unit_bytes"] = max(0, end - q)
        rec["strings"] = _eft_utf16_strings(extra, q, end)
        r = _EftReader(extra, version)
        r.p = q
        units = []
        try:
            for _ in range(rec["units"]):
                t = r.u16()
                u = {"type": t, "typeName": _EFT_UNIT_TYPES.get(t, f"type{t}")}
                _eft_unit(u, r, t, has_tex)
                u["props"] = _eft_props(u, t)
                units.append(u)
            rec["unit_exact"] = (r.p == end)
            if r.p != end:
                rec["unit_delta"] = r.p - end
        except Exception as e:
            rec["unit_exact"] = False
            rec["unit_error"] = str(e)
        rec["unit_details"] = units
        rec["unit_types"] = [u["type"] for u in units]
        props.append(rec)
    return props


def parse_eft_file(path):
    data = open(path, "rb").read()
    head, off = CommonFileHead.parse(data, 0)
    deps = {t.rstrip(b"\x00").decode("latin1"): list(ns) for t, ns in head.deps}
    extra = data[off:off + head.extra_size]
    return {"version": head.version, "deps": deps,
            "props": parse_eft(extra, head.version, bool(deps.get("tex")))}


def _mtr_blend_name(v):
    # D3D9 枚举: 1=ZERO 2=ONE 3=SRCCOLOR 4=INVSRCCOLOR
    #             5=SRCALPHA 6=INVSRCALPHA 7=DESTALPHA 8=INVDESTALPHA ...
    return {0: "Zero0", 1: "Zero", 2: "One", 3: "SrcColor", 4: "InvSrcColor",
            5: "SrcAlpha", 6: "InvSrcAlpha", 7: "DestAlpha", 8: "InvDestAlpha",
            9: "DestColor", 10: "InvDestColor"}.get(v, f"?{v}")


def parse_mtr_extra(extra, version):
    """`CMaterialTemplate::_OnReadMainData` 的 extra。

    0x2716 = 24+slen, 0x2717 = 26+slen;
    0x2712 = 1 + 11 + 24/28, 0x2713 = len+slen + 11 + 28。
    旧版把 ambient/diffuse/specular/emissive/power 直接存在模板里,
    0x2716+ 只保留 diffuse + specularSharp + factor。
    """
    r = _EftReader(extra, version)
    info = {"version": version}
    if version <= 0x2713:
        # 旧版: 0x2712 只有一个空的着色器占位字节;0x2713 是 std::string
        if version <= 0x2712:
            r.skip(1)
            info["shaderOverride"] = ""
        else:
            info["shaderOverride"] = r.rawstr()
        flags = [r.u8() for _ in range(11)]
        (info["alphaBlendEnable"], info["srcBlend"], info["destBlend"],
         info["f157"], info["f158"], info["alpharef"],
         info["zTestEnable"], info["zWrite"], info["zTestFunc"],
         info["cull"], info["f160"]) = flags
        info["legacyZWrite"] = info["f157"]
        info["legacy158"] = info["f158"]
        info["legacy160"] = info["f160"]
        vals = []
        while r.p + 4 <= len(extra):
            vals.append(r.u32())
        info["legacy_vals"] = vals
        info["legacy_color_flags"] = vals[0] if vals else 0
        info["ambient"] = vals[1] if len(vals) > 1 else 0xFFFFFFFF
        info["diffuse"] = vals[2] if len(vals) > 2 else 0xFFFFFFFF
        info["specular"] = vals[3] if len(vals) > 3 else 0xFFFFFFFF
        info["emissive"] = vals[4] if len(vals) > 4 else 0
        info["specularPower"] = (struct.unpack("<f", struct.pack("<I", vals[5]))[0]
                                 if len(vals) > 5 else 1.0)
        info["factor_bytes"] = list(
            vals[6].to_bytes(4, "little")) if len(vals) > 6 else [255] * 4
        fb = info["factor_bytes"]
        info["factor"] = [fb[2] / 255.0, fb[1] / 255.0, fb[0] / 255.0, fb[3] / 255.0]
        info["specularSharp"] = 0
        info["f126"] = 1 if info["specular"] not in (0xFF000000,) else 0
        info["f127"] = 1 if info["emissive"] not in (0xFF000000, 0) else 0
    else:
        info["shaderOverride"] = r.rawstr() if version > 0x2712 else ""
        info["alphaBlendEnable"] = r.u8()
        info["srcBlend"] = r.u8()
        info["destBlend"] = r.u8()
        info["f157"] = r.u8()
        info["f158"] = r.u8()
        info["alpharef"] = r.u8()
        info["zTestEnable"] = r.u8()
        info["zWrite"] = r.u8()
        info["zTestFunc"] = r.u8()
        info["cull"] = r.u8()
        info["f160"] = r.u8()
        # 这三个在 SMaterial(+76..+148) 之外,是 CMaterialTemplate 的旧版/编辑器字段:
        # f157 在全部样本里与 zWrite 同值;f158/f160 为固定 5/3。
        info["legacyZWrite"] = info["f157"]
        info["legacy158"] = info["f158"]
        info["legacy160"] = info["f160"]
        diff = r.u32()
        info["diffuse"] = diff
        info["diffuse_rgba"] = [diff & 0xFF, (diff >> 8) & 0xFF,
                                (diff >> 16) & 0xFF, (diff >> 24) & 0xFF]
        if version > 0x2714:
            info["specularSharp"] = r.u8()
        else:
            info["specularSharp"] = 0
            r.skip(12)
        if version > 0x2716:
            info["f126"], info["f127"] = r.u8(), r.u8()
        else:
            info["f126"] = info["f127"] = 0
        # 这两个字节位于 SMaterial(+76..+148) 内,即 SMaterial+50/+51。
        # 12 个样本里只在 specular / emissive / octopus 上出现 255,按材质用途命名。
        info["specularEnabled"] = info["f126"] != 0
        info["emissiveEnabled"] = info["f127"] != 0
        if version > 0x2715:
            b0, b1, b2, b3 = r.u8(), r.u8(), r.u8(), r.u8()
            # this+33..36 = (b2,b1,b0,b3)/255
            info["factor_bytes"] = [b0, b1, b2, b3]
            info["factor"] = [b2 / 255.0, b1 / 255.0, b0 / 255.0, b3 / 255.0]
        else:
            info["factor"] = [1.0, 1.0, 1.0, 1.0]
        if version <= 0x2714 and r.p + 4 <= len(extra):
            r.skip(4)
    info["diffuse_rgba"] = [info["diffuse"] & 0xFF, (info["diffuse"] >> 8) & 0xFF,
                            (info["diffuse"] >> 16) & 0xFF, (info["diffuse"] >> 24) & 0xFF]
    info["specularEnabled"] = info.get("specularEnabled", info["f126"] != 0)
    info["emissiveEnabled"] = info.get("emissiveEnabled", info["f127"] != 0)
    info["extra_size"] = len(extra)
    info["exact"] = (r.p == len(extra))
    info["srcBlendName"] = _mtr_blend_name(info["srcBlend"])
    info["destBlendName"] = _mtr_blend_name(info["destBlend"])
    info["alphaCutoff"] = info["alpharef"] / 255.0
    info["doubleSided"] = (info["cull"] == 1)   # D3D: 1=NONE, 2=CW, 3=CCW
    if not info["alphaBlendEnable"]:
        info["alphaMode"] = "OPAQUE"
    elif info["srcBlend"] == 5 and info["destBlend"] == 6:
        info["alphaMode"] = "BLEND"
    elif info["srcBlend"] == 5 and info["destBlend"] == 2:
        info["alphaMode"] = "ADDITIVE"          # glTF 无加色, 导出时按 BLEND 处理
    elif info["srcBlend"] == 0 and info["destBlend"] == 0:
        info["alphaMode"] = "OPAQUE"            # 着色器自发光/默认
    else:
        info["alphaMode"] = "BLEND"
    return info


def parse_mtr_file(path):
    data = open(path, "rb").read()
    head, off = CommonFileHead.parse(data, 0)
    extra = data[off:off + head.extra_size]
    info = parse_mtr_extra(extra, head.version)
    info["deps"] = {t.rstrip(b"\x00").decode("latin1"): list(ns)
                    for t, ns in head.deps}
    info["path"] = path
    return info


def cmd_mtr_info(args):
    import json
    info = parse_mtr_file(args.mtr)
    print(f"{args.mtr}: version={hex(info['version'])} extra={info['extra_size']} "
          f"exact={info['exact']}")
    for tag, names in info["deps"].items():
        print(f"  dep {tag}: {names}")
    if info["shaderOverride"]:
        print(f"  shaderOverride={info['shaderOverride']!r}")
    print(f"  alphaBlend={info['alphaBlendEnable']} src={info['srcBlendName']} "
          f"dest={info['destBlendName']} alpharef={info['alpharef']} "
          f"alphaCutoff={info['alphaCutoff']:.3f} "
          f"zTest={info['zTestEnable']} zWrite={info['zWrite']} "
          f"zFunc={info['zTestFunc']} cull={info['cull']} "
          f"doubleSided={info['doubleSided']} alphaMode={info['alphaMode']}")
    d = info["diffuse_rgba"]
    print(f"  diffuse=#{d[0]:02X}{d[1]:02X}{d[2]:02X}{d[3]:02X} "
          f"factor={[round(v, 3) for v in info['factor']]} "
          f"sharp={info['specularSharp']} "
          f"specular={info['specularEnabled']} emissive={info['emissiveEnabled']}")
    print(f"  legacy: zWrite={info['legacyZWrite']} "
          f"b158={info['legacy158']} b160={info['legacy160']} "
          f"(SMaterial 之外,不进入 GLB)")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        print(f"-> {args.json}")


def cmd_eft_info(args):
    import json
    import collections
    info = parse_eft_file(args.eft)
    print(f"{args.eft}: version={hex(info['version'])}")
    for tag, names in info["deps"].items():
        print(f"  dep {tag}: {len(names)} 个")
        for n in names:
            if n:
                print(f"     {n}")
    print(f"  props {len(info['props'])} 个")
    for p in info["props"]:
        cnt = collections.Counter(u.get("typeName") for u in p.get("unit_details", []))
        tdesc = ", ".join(f"{n}×{c}" for n, c in cnt.items())
        print(f"  [{p['name']}] dur={p.get('dur', 0)}ms units={p.get('units', 0)} "
              f"pos={[round(v, 2) for v in p.get('pos', [])]} "
              f"rot={[round(v, 2) for v in p.get('rot', [])]} "
              f"unitBlob={p.get('unit_bytes', 0)}B exact={p.get('unit_exact')}"
              + (f" types=[{tdesc}]" if tdesc else "")
              + (f" err={p['unit_error']}" if p.get("unit_error") else ""))
        if p.get("strs"):
            print(f"      strs={[_safe(s) for s in p['strs']]}")
        if args.units:
            for u in p.get("unit_details", []):
                props = u.get("props") or {}
                txt = ", ".join(f"{k}={v}" for k, v in list(props.items())[:10])
                print(f"      - {u['typeName']}: {txt}")
        if p.get("strings"):
            print(f"      refs={[_safe(s) for s in p['strings'][:8]]}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        print(f"-> {args.json}")


def cmd_eft_preview(args):
    """把 `.eft` 的每个 prop/unit 画成结构预览卡(粗略粒子云 + 贴图 + 语义属性)。"""
    from PIL import Image, ImageDraw, ImageFont
    info = parse_eft_file(args.eft)
    tex_names = info["deps"].get("tex", [])
    font_path = None
    for cand in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(cand):
            font_path = cand
            break
    try:
        font = ImageFont.truetype(font_path, 15) if font_path else ImageFont.load_default()
        small = ImageFont.truetype(font_path, 12) if font_path else ImageFont.load_default()
    except Exception:
        font = small = ImageFont.load_default()

    W = 1120
    row_h = 230
    H = max(row_h, len(info["props"]) * row_h)
    canvas = Image.new("RGBA", (W, H), (22, 26, 34, 255))
    d = ImageDraw.Draw(canvas)

    def tex_img(idx):
        if idx is None or idx < 0 or idx >= len(tex_names):
            return None
        p = _find_tex(args.tex_root, tex_names[idx], {})
        if not p:
            return None
        try:
            return Image.open(p).convert("RGBA")
        except Exception:
            return None

    for row, prop in enumerate(info["props"]):
        y0 = row * row_h
        d.rectangle([8, y0 + 6, W - 8, y0 + row_h - 6], fill=(30, 36, 48, 255),
                    outline=(70, 84, 104, 255))
        d.text((18, y0 + 12), f"{prop['name']}   {prop.get('dur', 0)}ms   "
               f"units={prop.get('units', 0)}", font=font, fill=(230, 236, 245, 255))
        x = 18
        for u in prop.get("unit_details", [])[:4]:
            cw, ch = 260, 180
            cy = y0 + 40
            d.rectangle([x, cy, x + cw - 8, cy + ch], fill=(24, 30, 40, 255),
                        outline=(58, 70, 88, 255))
            d.text((x + 8, cy + 6), u["typeName"], font=small, fill=(255, 220, 130, 255))
            # 第一个贴图
            tex_idx = None
            for ti in u.get("textures", []):
                if 0 <= ti < len(tex_names):
                    tex_idx = ti
                    break
            img = tex_img(tex_idx)
            box = 96
            if img is not None:
                im = img.copy()
                im.thumbnail((box, box), Image.LANCZOS)
                canvas.alpha_composite(im, (x + 8, cy + 26 + (box - im.height) // 2))
            else:
                d.rectangle([x + 8, cy + 26, x + 8 + box, cy + 26 + box],
                            fill=(52, 64, 82, 255))
            # 粗略粒子云:用语义字段的尺寸/速度/数量
            props = u.get("props") or {}
            if u["typeName"] == "CParticleSystem" and img is not None:
                rng = random.Random(zlib.crc32(
                    (prop["name"] + str(u.get("id", 0))).encode("utf-8")))
                cnt_v = props.get("每次发射粒子数") or [0, 20, 0]
                cnt = max(4, min(60, int(max(abs(v) for v in cnt_v) or 20)))
                size_v = props.get("最小尺寸") or [8, 8, 8]
                base = max(6, min(26, int(max(abs(v) for v in size_v) or 12)))
                cx, cyy = x + 8 + box // 2, cy + 26 + box // 2
                for _ in range(cnt):
                    ang = rng.uniform(0, math.tau)
                    dist = rng.uniform(0, 42)
                    sz = rng.randint(max(4, base // 2), base + 6)
                    px = int(cx + math.cos(ang) * dist) - sz // 2
                    py = int(cyy + math.sin(ang) * dist) - sz // 2
                    pim = img.copy()
                    pim.thumbnail((sz, sz), Image.LANCZOS)
                    a = rng.randint(70, 200)
                    pim.putalpha(pim.getchannel("A").point(lambda v: v * a // 255))
                    canvas.alpha_composite(pim, (px, py))
            # 语义属性(最多 5 行)
            tx = x + 112
            ty = cy + 28
            lines = 0
            for k, v in props.items():
                if lines >= 5:
                    break
                if isinstance(v, list):
                    vs = "[" + ",".join(f"{z:.1f}" if isinstance(z, float) else str(z)
                                        for z in v[:3]) + "]"
                elif isinstance(v, float):
                    vs = f"{v:.2f}"
                else:
                    vs = str(v)
                s = f"{k}: {vs}"
                if len(s) > 26:
                    s = s[:25] + "…"
                d.text((tx, ty), s, font=small, fill=(190, 202, 218, 255))
                ty += 18
                lines += 1
            x += cw
    canvas.save(args.out)
    print(f"{args.eft} -> {args.out}  props={len(info['props'])}")


def cmd_eft_tex(args):
    """把一个 `.eft` 用到的贴图拼成联络表(需要 Pillow)。"""
    from PIL import Image, ImageDraw
    info = parse_eft_file(args.eft)
    names = [n for n in info["deps"].get("tex", []) if n]
    imgs = []
    for n in names:
        base = os.path.splitext(os.path.basename(n))[0]
        p = _find_tex(args.tex_root, n, {})
        if p:
            imgs.append((base, p))
    if not imgs:
        print("没有找到贴图")
        return
    cell, pad, cols = 160, 10, 6
    rows = (len(imgs) + cols - 1) // cols
    W = cols * cell + (cols + 1) * pad
    H = rows * (cell + 20) + (rows + 1) * pad
    sheet = Image.new("RGBA", (W, H), (20, 24, 30, 255))
    d = ImageDraw.Draw(sheet)
    for k, (nm, p) in enumerate(imgs):
        r, c = divmod(k, cols)
        im = Image.open(p).convert("RGBA")
        im.thumbnail((cell, cell), Image.LANCZOS)
        x = pad + c * (cell + pad)
        y = pad + r * (cell + 20 + pad)
        sheet.alpha_composite(im, (x + (cell - im.width) // 2, y))
        d.text((x + 2, y + cell + 3), nm[:24], fill=(200, 210, 220, 255))
    sheet.save(args.out)
    print(f"{args.eft} -> {args.out}  贴图 {len(imgs)} 张")


def read_text_any(path):
    raw = open(path, "rb").read()
    # 先看 BOM;UTF-8 文件不能先按 UTF-16LE 解(会“成功”但全是乱码)
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", "replace").lstrip("\ufeff")
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace").lstrip("\ufeff")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    # 无 BOM:含大量 0 字节才按 UTF-16LE,否则 UTF-8 → GBK
    sample = raw[:400]
    if sample.count(0) > max(8, len(sample) // 8):
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                return raw.decode(enc).lstrip("\ufeff")
            except Exception:
                pass
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return None


def _read_tab(path):
    t = read_text_any(path)
    if not t:
        return [], []
    lines = [ln for ln in t.splitlines() if ln.strip()]
    if not lines:
        return [], []
    hdr = [c.strip() for c in lines[0].split("\t")]
    rows = []
    for ln in lines[1:]:
        c = ln.split("\t")
        if len(c) < len(hdr):
            c += [""] * (len(hdr) - len(c))
        rows.append({hdr[i]: c[i].strip() for i in range(len(hdr))})
    return hdr, rows


def _to_int(v, default=None):
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _to_float(v, default=None):
    try:
        return float(str(v).strip())
    except Exception:
        return default


def _pick(src, *keys, default=""):
    """取第一个非空字段;兼容旧版 equip.txt 的 ID/szName 命名。"""
    for k in keys:
        v = src.get(k)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return default


def _find_models_root(equip_path):
    """从 equip.txt 向上找包含 role/mod 的目录。"""
    d = os.path.dirname(os.path.abspath(equip_path))
    for _ in range(5):
        cand = os.path.join(d, "role", "mod")
        if os.path.isdir(cand):
            return cand
        d = os.path.dirname(d)
    return None


def _model_parts(stem):
    """p_046 / p_004_a / p_028b / l_p_001a -> (base, variant, number)。"""
    m = re.match(r"^(l_)?(p_(\d+))([a-z]*)$", stem)
    if not m:
        return stem, "", None
    base = (m.group(1) or "") + m.group(2)
    return base, m.group(4) or "", int(m.group(3))


def _skin_row(src, nid, model, ani, dic, source, fusion=False):
    stem = os.path.splitext(os.path.basename(model))[0]
    base, variant, num = _model_parts(stem)
    # 1.3.3: dName;1.1.x: szName(仍是字典键)
    key = _pick(src, "dName", "szName")
    mark = _pick(src, "szMark")
    name = dic.get(key, "") or mark or stem
    frag = []
    if "nFragmentID" in src:
        if _to_int(src.get("nFragmentID")) not in (None, 0):
            frag.append({"id": _to_int(src.get("nFragmentID")),
                         "count": _to_int(src.get("nFragmentCount"))})
    else:
        for i in range(1, 6):
            fid = _to_int(src.get(f"nFragment{i}"))
            if fid:
                frag.append({"id": fid, "count": _to_int(src.get(f"nCount{i}"))})
    fx = [src.get(f"szFx{i}", "").replace("\\", "/")
          for i in range(10) if src.get(f"szFx{i}", "")]
    mats = [_to_int(src.get(f"nMatrlIndex{i}")) for i in range(10)]
    show_default = "1" if source != "model" else "0"
    return {
        "uid": f"{'fusion' if fusion else source}:{nid}",
        "id": stem,
        "legacy_id": _to_int(nid),
        "source": source,
        "registered": source != "model",
        "fusion": fusion,
        "model_id": num,
        "base_model": base,
        "variant": variant,
        "name": name,
        "dictionary_name": dic.get(key, ""),
        "mark": mark,
        "key": key or None,
        "description_key": src.get("dDescription") or None,
        "description": dic.get(src.get("dDescription", ""), ""),
        "icon": _to_int(src.get("nIconID")),
        "show": _pick(src, "nShow", default=show_default) == "1",
        "sort": _to_int(_pick(src, "nSort", default=nid)),
        "price": _to_int(src.get("nPrice")),
        "acquire": _to_int(_pick(src, "nAcquire", "nSign")),
        "propvalue": _to_int(src.get("nPropvalue")),
        "type": _to_int(src.get("nType")),
        "fragment": frag,
        "pay": {"type": _to_int(src.get("nPayType")),
                "index": _to_int(src.get("nPayIndex")),
                "count": _to_int(src.get("nPayCount"))},
        "model": model,
        "ani": ani,
        "scale": _to_float(src.get("nScale"), 0.36 if source != "model" else None),
        "materials": mats,
        "fx": fx,
    }


def cmd_skins(args):
    """导出皮肤表:equip.txt + fusion.txt + 未实装的 p_* 模型。

    新 schema 同时给三套标识:
      id        = 模型文件名 stem(如 p_001 / p_004_a / l_p_001a)
      legacy_id = 原表 nID(equip 1..35 / fusion 1001..1053)
      key       = 本地化字典键(0b04xxxx / 0b06xxxx)
    """
    import json
    _hdr, eq = _read_tab(args.equip)
    if not eq:
        print("读取失败")
        return
    eqdir = os.path.dirname(os.path.abspath(args.equip))
    if args.config:
        cfg = args.config
    elif os.path.isdir(os.path.join(eqdir, "dictionary")):
        cfg = eqdir
    elif os.path.isdir(os.path.join(os.path.dirname(eqdir), "dictionary")):
        cfg = os.path.dirname(eqdir)
    else:
        cfg = os.path.dirname(eqdir)
    dic = load_gui_dictionary(cfg) if os.path.isdir(cfg) else {}
    out = []
    for r in eq:
        out.append(_skin_row(r, _pick(r, "nID", "ID"), r.get("szModel", ""),
                             r.get("szAni", ""), dic, "equip"))

    fpath = args.fusion or os.path.normpath(
        os.path.join(os.path.dirname(args.equip), "fusion.txt"))
    if os.path.exists(fpath):
        _h2, fus = _read_tab(fpath)
        for r in fus:
            out.append(_skin_row(r, r.get("nID"), r.get("szModel", ""),
                                 r.get("szAni", ""), dic, "fusion", True))

    if not args.registered_only:
        mroot = args.models_root or _find_models_root(args.equip)
        used = {os.path.splitext(os.path.basename(r["model"]))[0]
                for r in out if r.get("model")}
        if mroot and os.path.isdir(mroot):
            for f in sorted(os.listdir(mroot)):
                if not f.lower().endswith(".ams"):
                    continue
                stem = os.path.splitext(f)[0]
                if not stem.startswith("p_") or stem in used:
                    continue
                ani = os.path.join(os.path.dirname(mroot), "ani", stem + ".ans")
                if not os.path.exists(ani) and stem.endswith("_a"):
                    alt = os.path.join(os.path.dirname(mroot), "ani", "bind.ans")
                    ani = alt if os.path.exists(alt) else ani
                out.append(_skin_row(
                    {}, stem, f"role/mod/{f}",
                    ("role/ani/" + os.path.basename(ani)) if os.path.exists(ani) else "",
                    dic, "model"))

    for i, r in enumerate(out):
        r["order"] = i
    counts = {}
    for r in out:
        counts[r["source"]] = counts.get(r["source"], 0) + 1
    print(f"{args.equip}: {len(out)} 个皮肤 " +
          " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"{'order':>5} {'id':14s} {'legacy':>6} {'src':6s} {'name':16s} {'model':28s}")
    for r in out:
        print(f"{r['order']:>5} {r['id']:14s} {str(r['legacy_id']):>6} "
              f"{r['source']:6s} {r['name'][:16]:16s} {os.path.basename(r['model']):28s}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"-> {args.json}")


def cmd_eqp(args):
    """导出 .eqp(皮肤/装备清单)里的 UTF-16 名字:部件模型 + 皮肤名。"""
    import json
    data = open(args.eqp, "rb").read()
    names = parse_eqp_names(data)
    skins = parse_eqp_skins(data)
    rows = []
    for i, n in enumerate(names):
        exists = None
        if args.root:
            p = os.path.join(args.root, n.replace("/", os.sep))
            exists = os.path.exists(p)
        rows.append({"index": i, "name": n, "exists": exists})
    print(f"{args.eqp}: {len(names)} 个部件模型, {len(skins)} 个皮肤")
    for r in rows:
        mark = "" if r["exists"] is None else ("  [ok]" if r["exists"] else "  [missing]")
        print(f"  {r['index']:2d}  {r['name']}{mark}")
    if skins:
        print("皮肤(名字 + 部件类名):")
        for s in skins:
            print(f"  {s['index']:2d}  {s['name']}  [{s['class']}]  @0x{s['offset']:04x}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"file": args.eqp,
                       "parts": [r["name"] for r in rows],
                       "skins": [{"index": s["index"], "name": s["name"],
                                  "class": s["class"]} for s in skins]},
                      f, ensure_ascii=False, indent=2)
        print(f"-> {args.json}")
    if args.txt:
        with open(args.txt, "w", encoding="utf-8") as f:
            f.write("\n".join(r["name"] for r in rows) + "\n")
            f.write("\n# skins\n")
            f.write("\n".join(s["name"] for s in skins) + "\n")
        print(f"-> {args.txt}")


def main():
    # 控制台常是 GBK;遇到字典/资源里的特殊字符不要直接抛 UnicodeEncodeError
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Gamma 资源工具: tex / r / eqp / gui / eft / mtr")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tex-info"); p.add_argument("tex"); p.set_defaults(func=cmd_tex_info)
    p = sub.add_parser("tex-unpack"); p.add_argument("tex"); p.add_argument("outdir"); p.set_defaults(func=cmd_tex_unpack)
    p = sub.add_parser("tex-pack")
    p.add_argument("payload"); p.add_argument("out")
    p.add_argument("--format", type=int, default=3)
    p.add_argument("--storage", type=int, default=0)
    p.add_argument("--width", type=int, required=True)
    p.add_argument("--height", type=int, required=True)
    p.add_argument("--mips", type=int, default=1)
    p.set_defaults(func=cmd_tex_pack)

    p = sub.add_parser("tex-batch")
    p.add_argument("srcdir"); p.add_argument("outdir")
    p.set_defaults(func=cmd_tex_batch)

    p = sub.add_parser("deps")
    p.add_argument("dir"); p.add_argument("out")
    p.set_defaults(func=cmd_deps)

    p = sub.add_parser("eqp")
    p.add_argument("eqp")
    p.add_argument("--json", default=None, help="同时写出 JSON")
    p.add_argument("--txt", default=None, help="同时写出纯文本名单")
    p.add_argument("--root", default=None, help="用该目录检查名字对应的文件是否存在")
    p.set_defaults(func=cmd_eqp)

    p = sub.add_parser("skins")
    p.add_argument("equip", help="config/resource/equip.txt")
    p.add_argument("--config", default=None, help="config 目录(用于本地化名字)")
    p.add_argument("--fusion", default=None, help="fusion.txt(默认同目录)")
    p.add_argument("--models-root", default=None, help="role/mod 目录(默认从 equip 推导)")
    p.add_argument("--registered-only", action="store_true",
                   help="只导出 equip/fusion,不补未实装 p_* 模型")
    p.add_argument("--json", default=None)
    p.set_defaults(func=cmd_skins)

    p = sub.add_parser("gui-info")
    p.add_argument("gui")
    p.add_argument("--json", default=None)
    p.set_defaults(func=cmd_gui_info)

    p = sub.add_parser("eft-info")
    p.add_argument("eft")
    p.add_argument("--json", default=None)
    p.add_argument("--units", action="store_true", help="打印每个 unit 的语义属性")
    p.set_defaults(func=cmd_eft_info)

    p = sub.add_parser("eft-tex")
    p.add_argument("eft")
    p.add_argument("out")
    p.add_argument("--tex-root", default=None)
    p.set_defaults(func=cmd_eft_tex)

    p = sub.add_parser("eft-preview")
    p.add_argument("eft")
    p.add_argument("out")
    p.add_argument("--tex-root", default=None)
    p.set_defaults(func=cmd_eft_preview)

    p = sub.add_parser("mtr-info")
    p.add_argument("mtr")
    p.add_argument("--json", default=None)
    p.set_defaults(func=cmd_mtr_info)

    p = sub.add_parser("gui-render")
    p.add_argument("gui")
    p.add_argument("out")
    p.add_argument("--tex-root", default=None)
    p.add_argument("--config", default=None, help="config 目录(用于本地化字典)")
    p.add_argument("--no-uv-flip", action="store_true",
                   help="关闭 src 矩形负宽/负高触发的翻转(对照旧行为)")
    p.add_argument("--all-windows", action="store_true",
                   help="同矩形兄弟窗口全部绘制(默认只画内容最多的状态代表)")
    p.add_argument("--rotation", action="store_true",
                   help="实验性:按 state.f 旋转;多数 UI 的 f 并非屏幕角度")
    p.add_argument("--no-rotation", action="store_true",
                   help="兼容旧参数;现在默认就不旋转")
    p.add_argument("--wrap-tile", dest="wrap_tile", action="store_true",
                   default=True, help="按 u[2]/u[3] 平铺(默认开启)")
    p.add_argument("--no-wrap-tile", dest="wrap_tile", action="store_false",
                   help="关闭 u[2]/u[3] 平铺,只缩放整块源矩形")
    p.add_argument("--hide-window", action="append", default=[],
                   help="按名字隐藏窗口(可多次,如 jishazhe)")
    p.set_defaults(func=cmd_gui_render)

    p = sub.add_parser("gui-batch")
    p.add_argument("dir")
    p.add_argument("outdir")
    p.add_argument("--tex-root", default=None)
    p.add_argument("--config", default=None)
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_gui_batch)

    p = sub.add_parser("fl-list"); p.add_argument("filelist")
    p.add_argument("-n", "--limit", type=int, default=20); p.set_defaults(func=cmd_fl_list)

    p = sub.add_parser("fl-verify")
    p.add_argument("assets")
    p.add_argument("--filelist", default="filelist_1.3.3.4.7.r")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_fl_verify)

    p = sub.add_parser("unpack")
    p.add_argument("assets"); p.add_argument("outdir")
    p.add_argument("--filelist", default="filelist_1.3.3.4.7.r")
    p.add_argument("--only", choices=sorted(CATEGORY_PREFIXES.keys()),
                   help="只解某一类资源")
    p.add_argument("--include", action="append", default=[],
                   help="仅解包匹配的成员路径(fnmatch, 可多次)")
    p.add_argument("--exclude", action="append", default=[],
                   help="排除匹配的成员路径(fnmatch, 可多次)")
    p.add_argument("--keep-partial", action="store_true",
                   help="解析异常的包也按已有记录尽量导出")
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("r-unpack")
    p.add_argument("rfile", nargs="+")
    p.add_argument("outdir")
    p.add_argument("--names", nargs="*")
    p.set_defaults(func=cmd_r_unpack)

    p = sub.add_parser("r-pack")
    p.add_argument("out"); p.add_argument("files", nargs="+"); p.set_defaults(func=cmd_r_pack)

    p = sub.add_parser("r-types")
    p.add_argument("rfile", nargs="+")
    p.set_defaults(func=cmd_r_types)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
