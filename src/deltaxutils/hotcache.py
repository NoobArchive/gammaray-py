#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Android Gamma 客户端的热更新缓存。

缓存目录里的文件名是 **percent-encoded URL**,例如::

    http%A3%F2%F2res.fsj.mqgamer.com%F2fenshuajiang%F2data%F2config%F2<hash>.r

`%F2` 是 GBK 全角斜杠 `／`(A3F2)被拆开后的残留,实际分隔符。
逻辑路径就是 `/data/` 之后的部分:`config/<hash>.r`、`filelist_1.3.3.2.r`。
"""

import os
import shutil
import urllib.parse

DEFAULT_MARKER = b"/data/"


def decode_cache_name(name):
    """缓存文件名 -> 逻辑相对路径(找不到 /data/ 时返回原名)。"""
    raw = urllib.parse.unquote_to_bytes(name)
    # 0xF2 = GBK 全角斜杠的第二字节;缓存工具把它当分隔符用
    raw = raw.replace(b"\xf2", b"/").replace(b"\\", b"/")
    i = raw.lower().find(b"/data/")
    if i >= 0:
        raw = raw[i + len(DEFAULT_MARKER):]
    raw = raw.lstrip(b"/")
    for enc in ("utf-8", "gbk", "latin1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin1", "replace")


def _safe_rel(rel):
    parts = [p for p in rel.replace("\\", "/").split("/")
             if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def is_cache_dir(path):
    """目录是否像热更新缓存(存在 http%... 或 %F2 编码的文件)。"""
    try:
        for name in os.listdir(path):
            up = name.upper()
            if up.startswith("HTTP%") or "%F2" in up:
                return True
    except OSError:
        return False
    return False


def _link_or_copy(src, dst, hardlink=True):
    try:
        if os.path.exists(dst):
            os.remove(dst)
        if hardlink:
            os.link(src, dst)
        else:
            shutil.copy2(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def materialize(cache_dir, dest, base=None, hardlink=True):
    """把缓存目录展开成普通 assets 目录。

    `base` 可选:先铺一份基础资源(APK 解包目录),再用缓存覆盖。
    返回统计 dict。
    """
    cache_dir = os.path.abspath(cache_dir)
    dest = os.path.abspath(dest)
    os.makedirs(dest, exist_ok=True)
    stats = {"files": 0, "decoded": 0, "skipped": 0, "from_base": 0}

    def put(src, rel):
        rel = _safe_rel(rel)
        if not rel:
            stats["skipped"] += 1
            return
        dp = os.path.join(dest, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dp) or dest, exist_ok=True)
        _link_or_copy(src, dp, hardlink)
        stats["files"] += 1

    if base:
        base = os.path.abspath(base)
        for root, _dirs, files in os.walk(base):
            rel_dir = os.path.relpath(root, base)
            rel_dir = "" if rel_dir == "." else rel_dir
            for fn in files:
                sp = os.path.join(root, fn)
                rel = f"{rel_dir}/{fn}" if rel_dir else fn
                put(sp, rel)
                stats["from_base"] += 1

    for fn in sorted(os.listdir(cache_dir), key=str.lower):
        sp = os.path.join(cache_dir, fn)
        if not os.path.isfile(sp):
            continue
        rel = decode_cache_name(fn)
        if not rel:
            stats["skipped"] += 1
            continue
        if rel == fn and "%" in fn:
            # 没找到 /data/,又带百分号:大概率是没认出的缓存编码,不要乱造路径
            stats["skipped"] += 1
            continue
        put(sp, rel)
        stats["decoded"] += 1
    return stats
