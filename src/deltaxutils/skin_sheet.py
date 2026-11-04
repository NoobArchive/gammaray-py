#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""male.eqp 皮肤表 -> 3D 缩略图卡片 / 皮肤选择页模拟图。

依赖: numpy + pillow

  uv run --with numpy --with pillow python skin_sheet.py ^
      <male.eqp> <glb目录> <输出目录> [--page <已渲染的皮肤页 PNG>]

产物:
  <输出目录>/thumbs/*.png        每个皮肤的 3D 缩略图
  <输出目录>/skin_sheet.png      29 个皮肤的联络表
  <输出目录>/skin_page_1.png     叠在真实 .gui 渲染图上的第 1 页(需 --page)

皮肤与模型是 **1:1 按顺序配对**(已验证:p_001=粉刷匠, p_001_02=粉刷匠_黄色,
jingyanhezi=经验盒子, obj_001=金币)。
"""
import argparse
import glob
import io
import json
import math
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gamma_formats as gf


# ---------------------------------------------------------------- GLB 读取
def load_glb(path):
    d = open(path, "rb").read()
    jl, _ = struct.unpack_from("<II", d, 12)
    j = json.loads(d[20:20 + jl])
    boff = 20 + jl
    bl, _ = struct.unpack_from("<II", d, boff)
    return j, d[boff + 8:boff + 8 + bl]


def read_acc(j, bin_, idx):
    a = j["accessors"][idx]
    bv = j["bufferViews"][a["bufferView"]]
    off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    n, t, ct = a["count"], a["type"], a["componentType"]
    ncomp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[t]
    dt = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}[ct]
    return np.frombuffer(bin_, dtype=dt, count=n * ncomp, offset=off).reshape(n, ncomp)


def prims_of(j, bin_):
    imgs = {}
    for mi, m in enumerate(j.get("materials") or []):
        bct = (m.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
        if not bct:
            continue
        src = j["textures"][bct["index"]].get("source")
        if src is None:
            continue
        bv = j["bufferViews"][j["images"][src]["bufferView"]]
        off = bv.get("byteOffset", 0)
        try:
            imgs[mi] = Image.open(io.BytesIO(bin_[off:off + bv["byteLength"]])).convert("RGBA")
        except Exception:
            pass
    out = []
    for mesh in j["meshes"]:
        for prim in mesh["primitives"]:
            at = prim["attributes"]
            pos = read_acc(j, bin_, at["POSITION"]).astype(np.float64)
            uv = read_acc(j, bin_, at["TEXCOORD_0"]).astype(np.float64) if "TEXCOORD_0" in at else None
            nor = read_acc(j, bin_, at["NORMAL"]).astype(np.float64) if "NORMAL" in at else None
            idx = (read_acc(j, bin_, prim["indices"]).astype(np.int64).reshape(-1)
                   if "indices" in prim else np.arange(len(pos)))
            out.append((pos, uv, nor, idx, imgs.get(prim.get("material", -1))))
    return out


def joint_world_bounds(j):
    nodes = j.get("nodes") or []
    if not j.get("skins"):
        return None
    joints = set(j["skins"][0]["joints"])
    parent = [-1] * len(nodes)
    for i, nd in enumerate(nodes):
        for c in nd.get("children", []):
            parent[c] = i
    memo = {}

    def W(i):
        if i in memo:
            return memo[i]
        p = np.array(nodes[i].get("translation", [0, 0, 0]), dtype=np.float64)
        if parent[i] >= 0:
            p = W(parent[i]) + p
        memo[i] = p
        return p

    pts = np.array([W(i) for i in joints])
    if len(pts) < 4:
        return None
    return np.percentile(pts, 5, axis=0), np.percentile(pts, 95, axis=0)


def render_thumb(glb, size=(360, 460), yaw=0.28, pad=0.92, cull=False):
    """GLB -> 正面贴图缩略图(正交投影 + z-buffer)。

    cull=True 时按顶点法线剔除背面(用于检查绕序与 `.mtr` 单面是否一致)。
    """
    j, bin_ = load_glb(glb)
    prims = prims_of(j, bin_)
    W, H = size
    cy, sy_ = math.cos(yaw), math.sin(yaw)
    rot = np.array([[cy, 0, sy_], [0, 1, 0], [-sy_, 0, cy]])

    allp = np.vstack([p[0] for p in prims])
    mlo = np.percentile(allp, 2, axis=0)
    mhi = np.percentile(allp, 98, axis=0)
    mxr = max(mhi[0] - mlo[0], 1e-6)
    myr = max(mhi[1] - mlo[1], 1e-6)
    jb = joint_world_bounds(j)
    if jb is not None:
        jlo, jhi = jb
        ctr = (jlo + jhi) / 2
        xr = max(mxr, jhi[0] - jlo[0])
        yr = max(myr, jhi[1] - jlo[1])
        ext = max(xr, min(yr, 2.2 * xr)) * 1.3
    else:
        ext = max(mxr, min(myr, 2.2 * mxr)) * 1.1
        ctr = (mlo + mhi) / 2
    scale = size[0] * pad / ext

    img = np.zeros((H, W, 4), np.float32)
    zbuf = np.full((H, W), -1e18)
    for pos, uv, nor, idx, tex in prims:
        p = (pos - ctr) @ rot.T
        sx = p[:, 0] * scale + W / 2
        sy = -p[:, 1] * scale + H / 2
        sz = p[:, 2]
        if uv is None:
            uv = np.zeros((len(pos), 2))
        tarr = None
        if tex is not None:
            tw, th = tex.size
            tarr = np.asarray(tex, dtype=np.float32) / 255.0
        for t in idx.reshape(-1, 3):
            if cull:
                p0, p1, p2 = pos[t[0]], pos[t[1]], pos[t[2]]
                u = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
                v = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
                n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                     u[0] * v[1] - u[1] * v[0])
                nv = (nor[t[0]][0] + nor[t[1]][0] + nor[t[2]][0],
                      nor[t[0]][1] + nor[t[1]][1] + nor[t[2]][1],
                      nor[t[0]][2] + nor[t[1]][2] + nor[t[2]][2])
                if n[0] * nv[0] + n[1] * nv[1] + n[2] * nv[2] <= 0:
                    continue
            x0, x1, x2 = sx[t]
            y0, y1, y2 = sy[t]
            z0, z1, z2 = sz[t]
            u0, u1, u2 = uv[t][:, 0]
            v0, v1, v2 = uv[t][:, 1]
            minx = max(int(np.floor(min(x0, x1, x2))), 0)
            maxx = min(int(np.ceil(max(x0, x1, x2))), W - 1)
            miny = max(int(np.floor(min(y0, y1, y2))), 0)
            maxy = min(int(np.ceil(max(y0, y1, y2))), H - 1)
            if minx > maxx or miny > maxy:
                continue
            den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            if abs(den) < 1e-9:
                continue
            xs = np.arange(minx, maxx + 1) + 0.5
            ys = np.arange(miny, maxy + 1) + 0.5
            gx, gy = np.meshgrid(xs, ys)
            w0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / den
            w1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / den
            w2 = 1 - w0 - w1
            m = (w0 >= -0.003) & (w1 >= -0.003) & (w2 >= -0.003)
            if not m.any():
                continue
            z = w0 * z0 + w1 * z1 + w2 * z2
            sub = zbuf[miny:maxy + 1, minx:maxx + 1]
            upd = m & (z > sub)
            if not upd.any():
                continue
            if tarr is not None:
                uu = np.clip((w0 * u0 + w1 * u1 + w2 * u2) % 1.0, 0.0, 0.999999)
                vv = np.clip((w0 * v0 + w1 * v1 + w2 * v2) % 1.0, 0.0, 0.999999)
                px = np.clip((uu * tw).astype(int), 0, tw - 1)
                py = np.clip((vv * th).astype(int), 0, th - 1)
                col = tarr[py, px]
            else:
                col = np.ones(upd.shape + (4,), np.float32)
                col[..., :3] = 0.8
            region = img[miny:maxy + 1, minx:maxx + 1]
            a = col[..., 3:4]
            region[upd, :3] = col[upd, :3] * a[upd] + region[upd, :3] * (1 - a[upd])
            region[upd, 3] = np.maximum(region[upd, 3], a[upd, 0])
            sub[upd] = z[upd]
    return Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8), "RGBA")


# ---------------------------------------------------------------- 卡片 / 表
def find_font():
    for c in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
              r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(c):
            return c
    return None


def make_card(path, name, sub, w, h, fonts):
    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=16, fill=(27, 39, 54, 255),
                        outline=(96, 136, 176, 255), width=2)
    d.rounded_rectangle([10, 10, w - 11, h - 71], radius=12, fill=(17, 26, 37, 255))
    if path:
        im = Image.open(path).convert("RGBA")
        im.thumbnail((w - 40, h - 105), Image.LANCZOS)
        card.alpha_composite(im, ((w - im.width) // 2, 10 + (h - 81 - im.height) // 2))
    d.text((w / 2, h - 46), name, font=fonts[0], fill=(240, 240, 240, 255), anchor="mm")
    d.text((w / 2, h - 22), sub, font=fonts[1], fill=(140, 165, 190, 255), anchor="mm")
    return card


def main():
    ap = argparse.ArgumentParser(description="male.eqp 皮肤表 -> 缩略图卡片")
    ap.add_argument("eqp")
    ap.add_argument("glb_dir")
    ap.add_argument("outdir")
    ap.add_argument("--page", default=None, help="已渲染的皮肤页 PNG(可选)")
    ap.add_argument("--page-rect", default="20,154,1240,470",
                    help="卡片区域 x,y,w,h(默认皮肤页内容区)")
    ap.add_argument("--cols", type=int, default=5, help="联络表列数")
    ap.add_argument("--cards", type=int, default=4, help="单页卡片数")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    thumbs = os.path.join(args.outdir, "thumbs")
    os.makedirs(thumbs, exist_ok=True)

    data = open(args.eqp, "rb").read()
    parts = gf.parse_eqp_names(data)
    skins = gf.parse_eqp_skins(data)
    print(f"部件 {len(parts)} 个,皮肤 {len(skins)} 个")

    pairs = []
    for i, s in enumerate(skins):
        part = parts[i] if i < len(parts) else None
        stem = os.path.splitext(os.path.basename(part))[0] if part else None
        glb = os.path.join(args.glb_dir, (stem or "") + ".glb")
        tp = os.path.join(thumbs, (stem or f"skin{i}") + ".png")
        if os.path.exists(glb) and not os.path.exists(tp):
            try:
                render_thumb(glb).save(tp)
            except Exception as e:
                print("  缩略图失败", s["name"], e)
                tp = None
        elif not os.path.exists(glb):
            tp = None
        pairs.append({"i": i, "name": s["name"], "part": part or "",
                      "thumb": tp if os.path.exists(tp or "") else None})

    font_path = find_font()
    f_name = ImageFont.truetype(font_path, 26) if font_path else ImageFont.load_default()
    f_small = ImageFont.truetype(font_path, 18) if font_path else ImageFont.load_default()
    f_big = ImageFont.truetype(font_path, 34) if font_path else ImageFont.load_default()
    fonts = (f_name, f_small)

    cw, ch, pad = 300, 430, 14
    cols = max(1, args.cols)
    rows = (len(pairs) + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * cw + (cols + 1) * pad,
                               rows * ch + (rows + 1) * pad + 70), (18, 26, 36, 255))
    ds = ImageDraw.Draw(sheet)
    ds.text((pad + 6, 26), f"皮肤总表 — {len(pairs)} 个", font=f_big, fill=(255, 255, 255, 255))
    for k, p in enumerate(pairs):
        r, c = divmod(k, cols)
        sheet.alpha_composite(make_card(p["thumb"], p["name"], p["part"], cw, ch, fonts),
                              (pad + c * (cw + pad), 70 + pad + r * (ch + pad)))
    out_sheet = os.path.join(args.outdir, "skin_sheet.png")
    sheet.save(out_sheet)
    print("->", out_sheet, sheet.size)

    if args.page and os.path.exists(args.page):
        ax, ay, aw, ah = [int(v) for v in args.page_rect.split(",")]
        page = Image.open(args.page).convert("RGBA")
        n = min(args.cards, len(pairs))
        for k in range(n):
            card = make_card(pairs[k]["thumb"], pairs[k]["name"], pairs[k]["part"], cw, ch, fonts)
            page.alpha_composite(card, (ax + 8 + k * (cw + 8), ay + 6))
        pages = (len(pairs) + args.cards - 1) // args.cards
        ImageDraw.Draw(page).text((ax + aw - 380, ay + ah - 10),
                                  f"第 1 / {pages} 页  ({len(pairs)} 皮肤)",
                                  font=f_small, fill=(255, 255, 255, 230))
        p1 = os.path.join(args.outdir, "skin_page_1.png")
        page.save(p1)
        print("->", p1, page.size)


if __name__ == "__main__":
    main()
