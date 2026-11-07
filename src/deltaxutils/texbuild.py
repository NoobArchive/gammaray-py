#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 PNG 等常见图片写成 Gamma `.tex`。

当前只实现 **raw BGRA**(format=3, storage=0, 1 级 mip):
主数据 = `u32 payloadSize + BGRA`。ETC2/ATF/DXT 的容器细节还没有
足够样本确认,所以 `--tex-format` 暂时只接受 `bgra`/`raw`。
"""

import os
import struct
import sys

try:
    from . import gamma_formats as g
except ImportError:                       # 直接以脚本方式运行时
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gamma_formats as g


def _rgba_to_bgra(buf):
    out = bytearray(buf)
    for i in range(0, len(out), 4):
        out[i], out[i + 2] = out[i + 2], out[i]
    return bytes(out)


def png_to_tex(src, dst, tex_format="bgra"):
    """读取 PNG/JPEG(BGRA 内存序),写 0x2717 `.tex`。"""
    if tex_format not in ("bgra", "raw", "rgba32"):
        raise ValueError(
            f"暂不支持的 --tex-format: {tex_format};当前只实现 bgra/raw")
    try:
        from PIL import Image
    except ImportError as e:
        raise RuntimeError("需要 Pillow: uv run --with pillow ...") from e
    im = Image.open(src).convert("RGBA")
    w, h = im.size
    payload = _rgba_to_bgra(im.tobytes())
    t = g.TexFile()
    t.head.type = g.MAGIC_TEX
    t.head.version = 0x2717
    t.index = [3, 0, w, h, 0, 1, w, h, 0]   # RGBA32 / raw / 1 mip
    t.main = struct.pack("<I", len(payload)) + payload
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(t.build())
    return w, h


def prepare_tree(src, outdir, tex_format="bgra", verbose=True):
    """镜像目录;`.png` 写成同名 `.tex`,其它文件原样复制。"""
    import shutil
    src = os.path.abspath(src)
    outdir = os.path.abspath(outdir)
    stats = {"tex": 0, "copied": 0, "skipped": 0}
    for root, dirs, files in os.walk(src):
        dirs.sort(key=str.lower)
        files.sort(key=str.lower)
        rel = os.path.relpath(root, src)
        rel = "" if rel == "." else rel
        dst_dir = os.path.join(outdir, rel)
        os.makedirs(dst_dir, exist_ok=True)
        for name in files:
            sp = os.path.join(root, name)
            if os.path.abspath(sp).startswith(outdir + os.sep):
                continue
            if name.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                dst = os.path.join(dst_dir, os.path.splitext(name)[0] + ".tex")
                try:
                    png_to_tex(sp, dst, tex_format)
                    stats["tex"] += 1
                    if verbose and stats["tex"] % 50 == 0:
                        print(f"  [tex] {stats['tex']} ... last: "
                              f"{os.path.relpath(sp, src)}")
                except Exception as e:
                    stats["skipped"] += 1
                    print(f"  [skip] {sp}: {e}")
            else:
                shutil.copy2(sp, os.path.join(dst_dir, name))
                stats["copied"] += 1
    return stats
