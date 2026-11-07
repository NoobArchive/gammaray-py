#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一个目录封成 Gamma `.r` 包,并生成 `filelist_<version>.r`。

规则(与游戏客户端的本地读取一致):

* `.r` = 连续 `[u32 length][payload]`,没有压缩/加密;
* 同一目录下的文件按名字排序,累计到 200 KiB 为一个逻辑包;
* 单个文件超过 200 KiB 时独占一个逻辑包,并把整条 `.r` 流按 200 KiB 切片;
* filelist 的 part 行写分片 `.r` 的实际长度,成员行只写一次;
* 根目录的 `etc/`、`version.inf`、`filelist_*.r` 不打包、不写入 filelist。

filelist 的 `csize` 是给服务器下载用的压缩尺寸;本地包不校验它,
这里用 zlib 估计,精确的服务器 `.z` 压缩器尚未实现。
"""

import hashlib
import os
import struct
import zlib

DEFAULT_LIMIT = 204800          # 200 KiB
DEFAULT_VERSION = "0.0.0"


def _norm_rel(path):
    return path.replace("\\", "/").lstrip("/")


def _is_root_excluded(rel_parts):
    if not rel_parts:
        return False
    if rel_parts[0] == "etc":
        return True
    if len(rel_parts) == 1:
        name = rel_parts[0]
        if name == "version.inf":
            return True
        if name.lower().startswith("filelist_") and name.lower().endswith(".r"):
            return True
    return False


def collect_files(src, skip_dirs=()):
    """返回 {相对目录: [(文件名, 绝对路径, 字节数), ...]}。"""
    src = os.path.abspath(src)
    skip = {os.path.abspath(p) for p in skip_dirs}
    out = {}
    for root, dirs, files in os.walk(src):
        dirs.sort(key=str.lower)
        files.sort(key=str.lower)
        if os.path.abspath(root) in skip:
            dirs[:] = []
            continue
        rel_dir = _norm_rel(os.path.relpath(root, src))
        if rel_dir == ".":
            rel_dir = ""
        if _is_root_excluded(rel_dir.split("/") if rel_dir else []):
            dirs[:] = []
            continue
        # 根目录下的 etc 整个剪掉;其它子目录里的 etc 不受影响
        if not rel_dir:
            dirs[:] = [d for d in dirs if d != "etc"]
        for name in files:
            full = os.path.join(root, name)
            rel_parts = (rel_dir.split("/") if rel_dir else []) + [name]
            if _is_root_excluded(rel_parts):
                continue
            if os.path.abspath(full) in skip:
                continue
            out.setdefault(rel_dir, []).append((name, full, os.path.getsize(full)))
    return out


def group_files(files, limit=DEFAULT_LIMIT):
    """按文件名排序,把 (name, path, size) 分成 <= limit 的组。"""
    groups = []
    cur = []
    cur_size = 0
    for item in sorted(files, key=lambda x: x[0].lower()):
        need = 4 + item[2]
        if need > limit:
            if cur:
                groups.append(cur)
                cur, cur_size = [], 0
            groups.append([item])
            continue
        if cur and cur_size + need > limit:
            groups.append(cur)
            cur, cur_size = [], 0
        cur.append(item)
        cur_size += need
    if cur:
        groups.append(cur)
    return groups


def _build_stream(items):
    buf = bytearray()
    for _name, path, _size in items:
        with open(path, "rb") as f:
            data = f.read()
        buf += struct.pack("<I", len(data))
        buf += data
    return bytes(buf)


def _part_name(rel_dir, members, idx, data):
    h = hashlib.md5()
    h.update(rel_dir.encode("utf-8"))
    h.update(b"\x00")
    h.update("\n".join(members).encode("utf-8"))
    h.update(b"\x00")
    h.update(str(idx).encode("ascii"))
    h.update(b"\x00")
    h.update(data)
    return h.hexdigest()


def _csize_estimate(data):
    # 原始服务器 .z 的压缩器尚未确认;zlib9 的容器比原始 csize 小约 6B,
    # 这里补 6B 作为“下载尺寸”的近似值。
    return len(zlib.compress(data, 9)) + 6


def pack_tree(src, outdir, version=DEFAULT_VERSION, limit=DEFAULT_LIMIT,
              verbose=True):
    """执行目录封包。返回统计 dict。"""
    src = os.path.abspath(src)
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)
    by_dir = collect_files(src, skip_dirs=(outdir,))
    rows = []
    stats = {"files": 0, "packages": 0, "parts": 0, "bytes": 0}
    for rel_dir in sorted(by_dir, key=str.lower):
        files = by_dir[rel_dir]
        stats["files"] += len(files)
        groups = group_files(files, limit)
        for items in groups:
            members = [it[0] for it in items]
            stream = _build_stream(items)
            stats["packages"] += 1
            stats["bytes"] += len(stream)
            parts = [stream[i:i + limit] for i in range(0, len(stream), limit)]
            for idx, part in enumerate(parts):
                digest = _part_name(rel_dir, members, idx, part)
                name = digest + ".z"
                rname = digest + ".r"
                pkg_dir = os.path.join(outdir, rel_dir.replace("/", os.sep))
                os.makedirs(pkg_dir, exist_ok=True)
                with open(os.path.join(pkg_dir, rname), "wb") as f:
                    f.write(part)
                rows.append(f"{rel_dir + '/' if rel_dir else ''}{name}\t"
                            f"{len(part)}\t{_csize_estimate(part)}\r\n")
                stats["parts"] += 1
            for m in members:
                rows.append(f"{m}\t\t\r\n")
            if verbose and stats["packages"] % 100 == 0:
                print(f"  ... {stats['packages']} packages / {stats['parts']} parts")
    text = "".join(rows).encode("utf-8")
    fl = struct.pack("<I", len(text)) + text
    fl_name = f"filelist_{version}.r"
    with open(os.path.join(outdir, fl_name), "wb") as f:
        f.write(fl)
    stats["filelist"] = fl_name
    stats["filelist_bytes"] = len(fl)
    stats["outdir"] = outdir
    return stats


def pack_summary(stats):
    return (f"files={stats['files']} packages={stats['packages']} "
            f"parts={stats['parts']} data={stats['bytes']}B "
            f"filelist={stats['filelist']} ({stats['filelist_bytes']}B)")
