"""gammaray 统一 CLI。

  unpack  : 目录 / .apk -> out/original(可 -c 顺带转换)
  convert : out/original -> out/converted(tex/models/ui/mat/fx)
  prepare : 把 PNG 预处理成 raw BGRA .tex,输出可再封包的目录
  pack    : 目录 -> .r 包 + filelist_<version>.r(200KiB 分组/分片)

内部复用 DeltaXUtils 的 gamma_formats / gamma_models / rpack / texbuild。
"""

import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

from . import __version__
from .legacy import gf, gm, LEGACY_DIR

DEFAULT_OUT = Path("out")


def _ts():
    return datetime.datetime.now().strftime("%Y%m%d%H%M%S")


def _target_out_root(target):
    """目标文件/目录旁边的 out 目录(不再依赖当前工作目录)。

    如果目标本身已经在名为 out 的目录下,直接复用这个 out。
    """
    t = Path(target).resolve()
    parent = t.parent
    if parent.name.lower() == "out":
        return parent
    return parent / "out"


def _default_convert_dir(out_dir):
    """转换输出默认放在 original 的旁边(同一 out 根)。"""
    return Path(out_dir).parent / "converted"


def _out_path(path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# unpack
# ---------------------------------------------------------------------------
def _pick_filelist(assets: Path, requested=None) -> Path:
    if requested:
        p = Path(requested)
        if p.is_file():
            return p.resolve()
        q = assets / requested
        if q.is_file():
            return q.resolve()
        raise SystemExit(f"找不到 filelist: {requested}")
    cands = list(assets.glob("filelist_*.r"))
    if not cands:
        raise SystemExit(f"{assets} 下没有 filelist_*.r;请用 --filelist 指定")
    if len(cands) > 1:
        cands.sort(key=lambda p: p.stat().st_mtime)
        print(f"找到多个 filelist,使用最新的: {cands[-1].name}")
    return cands[-1].resolve()


def _extract_apk_assets(apk: Path, tmp: Path) -> Path:
    with zipfile.ZipFile(apk) as z:
        names = [n for n in z.namelist()
                 if n.startswith("assets/") and not n.endswith("/")]
        if not names:
            raise SystemExit(f"{apk} 里没有 assets/")
        z.extractall(tmp, names)
    return tmp / "assets"


def cmd_unpack(args):
    from deltaxutils import hotcache
    src = Path(args.assets)
    if not src.exists():
        raise SystemExit(f"输入不存在: {src}")
    out = Path(args.out) if args.out else _target_out_root(src) / "original"
    tmp = None
    try:
        if src.is_file() and src.suffix.lower() == ".apk":
            tmp = Path(tempfile.mkdtemp(prefix="gammaray-apk-"))
            assets = _extract_apk_assets(src, tmp)
        elif src.is_dir() and (getattr(args, "cache", False)
                               or hotcache.is_cache_dir(src)):
            tmp = Path(tempfile.mkdtemp(prefix="gammaray-cache-"))
            assets = tmp / "assets"
            stats = hotcache.materialize(
                src, assets, base=getattr(args, "base", None))
            print(f"热更新缓存展开: files={stats['files']} "
                  f"decoded={stats['decoded']} skipped={stats['skipped']}"
                  + (f" base={stats['from_base']}" if stats["from_base"] else ""))
        else:
            assets = src
        fl = _pick_filelist(assets, args.filelist)
        ns = argparse.Namespace(
            assets=str(assets),
            outdir=str(out),
            filelist=str(fl),
            only=args.only,
            include=args.include or [],
            exclude=args.exclude or [],
            keep_partial=args.keep_partial,
        )
        gf.cmd_unpack(ns)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)

    if args.convert is not None:
        conv = Path(args.convert) if args.convert else _default_convert_dir(out)
        cns = argparse.Namespace(
            src=str(out), out=str(conv), only="tex,models,ui,mat",
            tex_root=None, clips=False, orphan_clips=False, fps=30.0,
        )
        cmd_convert(cns)


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------
def _convert_tex(src, tex_out, counts):
    for f in src.rglob("*.tex"):
        rel = f.relative_to(src)
        try:
            data = f.read_bytes()
            kind, paths = gf.convert_tex(data, str((tex_out / rel.parent)), f.stem)
            counts[kind] = counts.get(kind, 0) + 1
        except Exception as e:
            counts["ERROR"] = counts.get("ERROR", 0) + 1
            print(f"  [tex err] {rel}: {e}")


def _convert_models(src, out, tex_root, counts, clips=False, fps=30.0,
                    orphan_clips=False):
    for group in ("role", "fx"):
        mod = src / group / "mod"
        ani = src / group / "ani"
        if not mod.exists():
            continue
        for f in sorted(mod.glob("*.ams")):
            rel = f.relative_to(src).with_suffix(".glb")
            dst = _out_path(out / "models" / rel)
            try:
                mesh = gm.parse_ams(str(f), str(ani) if ani.exists() else None, 0)
                if not mesh:
                    counts["models_skip"] = counts.get("models_skip", 0) + 1
                    continue
                textures = gm.load_textures(mesh, str(tex_root))
                mat_opts = None
                mtrs = mesh.get("deps", {}).get("mtr") or []
                if mtrs:
                    mp = gm._find_dep_file(str(f), mtrs[0])
                    if mp:
                        mi = gf.parse_mtr_file(mp)
                        mat_opts = {"alphaMode": mi["alphaMode"],
                                    "doubleSided": mi["doubleSided"]}
                        if not mat_opts["doubleSided"] and not gm._winding_consistent(mesh):
                            mat_opts["doubleSided"] = True
                anims = (gm.ans_clip_paths(mesh["ans_path"],
                                           include_orphans=orphan_clips)
                         if clips and mesh.get("ans_path") else None)
                gm.write_glb(str(dst), mesh, textures, anims=anims, fps=fps,
                             mat_opts=mat_opts)
                if anims:
                    gm._write_anim_manifest(mesh, str(dst), anims, fps)
                counts["models"] = counts.get("models", 0) + 1
            except Exception as e:
                counts["models_error"] = counts.get("models_error", 0) + 1
                print(f"  [model err] {rel}: {e}")


def _convert_ui(src, out, tex_root, counts):
    config = src / "config"
    texts = gf.load_gui_dictionary(str(config)) if config.exists() else {}
    gui_dir = src / "gui"
    if not gui_dir.exists():
        return
    for f in sorted(gui_dir.rglob("*.gui")):
        rel = f.relative_to(src).with_suffix(".png")
        dst = _out_path(out / "ui" / rel)
        try:
            g = gf.parse_gui(str(f))
            gf.render_gui(g, str(tex_root), str(dst), texts=texts)
            counts["ui"] = counts.get("ui", 0) + 1
        except Exception as e:
            counts["ui_error"] = counts.get("ui_error", 0) + 1
            print(f"  [ui err] {rel}: {e}")


def _convert_mat(src, out, counts):
    mat = src / "mat"
    if not mat.exists():
        return
    for f in sorted(mat.rglob("*.mtr")):
        rel = f.relative_to(src)
        if rel.parts and rel.parts[0] == "mat":
            rel = Path(*rel.parts[1:])
        rel = rel.with_suffix(".json")
        dst = _out_path(out / "mat" / rel)
        try:
            info = gf.parse_mtr_file(str(f))
            dst.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
            counts["mat"] = counts.get("mat", 0) + 1
        except Exception as e:
            counts["mat_error"] = counts.get("mat_error", 0) + 1
            print(f"  [mtr err] {rel}: {e}")


def _convert_fx(src, out, tex_root, counts):
    fx = src / "fx"
    if not fx.exists():
        return
    for f in sorted(fx.rglob("*.eft")):
        rel = f.relative_to(src)
        if rel.parts and rel.parts[0] == "fx":
            rel = Path(*rel.parts[1:])
        rel = rel.with_suffix(".json")
        dst = _out_path(out / "fx" / rel)
        try:
            info = gf.parse_eft_file(str(f))
            dst.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
            counts["fx"] = counts.get("fx", 0) + 1
        except Exception as e:
            counts["fx_error"] = counts.get("fx_error", 0) + 1
            print(f"  [eft err] {rel}: {e}")


def cmd_convert(args):
    src = Path(args.src)
    out = Path(args.out) if args.out else _default_convert_dir(src)
    if not src.exists():
        raise SystemExit(f"源目录不存在: {src}")
    only = {x.strip() for x in (args.only or "tex,models,ui,mat").split(",") if x.strip()}
    valid = {"tex", "models", "ui", "mat", "fx"}
    unknown = only - valid
    if unknown:
        raise SystemExit(f"未知的 --only: {', '.join(sorted(unknown))}; 可选: {', '.join(sorted(valid))}")
    tex_root = Path(args.tex_root) if args.tex_root else out / "tex"
    counts = {}
    if "tex" in only:
        print("[1/4] tex -> PNG")
        _convert_tex(src, tex_root, counts)
    if "models" in only:
        print("[2/4] models -> GLB")
        _convert_models(src, out, tex_root, counts, args.clips, args.fps,
                        args.orphan_clips)
    if "ui" in only:
        print("[3/4] gui -> PNG")
        _convert_ui(src, out, tex_root, counts)
    if "mat" in only:
        print("[4/4] mtr -> JSON")
        _convert_mat(src, out, counts)
    if "fx" in only:
        print("[+] eft -> JSON")
        _convert_fx(src, out, tex_root, counts)
    print("convert done:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"-> {out}")


# ---------------------------------------------------------------------------
# prepare / pack
# ---------------------------------------------------------------------------
def cmd_prepare(args):
    from deltaxutils import texbuild
    src = Path(args.src)
    out = Path(args.out) if args.out else _target_out_root(src) / f"datapre-{_ts()}"
    if not src.exists():
        raise SystemExit(f"源目录不存在: {src}")
    stats = texbuild.prepare_tree(str(src), str(out),
                                  tex_format=args.tex_format, verbose=True)
    print(f"prepare done: tex={stats['tex']} copied={stats['copied']} "
          f"skipped={stats['skipped']} -> {out}")


def cmd_pack(args):
    from deltaxutils import rpack
    src = Path(args.src)
    if not src.is_dir():
        raise SystemExit(f"需要目录: {src}")
    out = Path(args.out) if args.out else _target_out_root(src) / f"datarel-{_ts()}"
    version = args.version or rpack.detect_version(str(src))
    if not version:
        raise SystemExit(
            "无法从源目录推断版本(根目录需要 version.inf 或 filelist_*.r);"
            "请用 --version 指定,例如 --version 1.3.3.4.7")
    vi_ver = rpack.read_version_inf(str(src))
    if args.version and vi_ver and args.version != vi_ver:
        raise SystemExit(
            f"源 version.inf 是 {vi_ver},与 --version {args.version} 不一致;"
            "游戏会按 version.inf 找 filelist_<version>.r")
    if args.limit < 16:
        raise SystemExit("--limit 至少为 16 字节")
    stats = rpack.pack_tree(str(src), str(out), version=version,
                            limit=args.limit, verbose=not args.quiet)
    print(rpack.pack_summary(stats))


def cmd_pack_records(args):
    blobs = [Path(f).read_bytes() for f in args.files]
    data = gf.build_package(blobs)
    out = _out_path(args.out)
    out.write_bytes(data)
    print(f"packed {len(blobs)} record(s) -> {out} ({len(data)} bytes)")
    print("提示: 目录封包请用 `gammaray pack <dir>`。")


def cmd_info(args):
    print(f"gammaray {__version__}")
    print(f"legacy: {LEGACY_DIR}")
    print("commands: unpack / convert / prepare / pack / pack-records / info")
    print("docs: README.md, docs/howto.md, docs/ARCHITECTURE.md")


# ---------------------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser(prog="gammaray",
                                 description="Gamma 资源工具统一入口")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("unpack", help="解包目录 / .apk / 热更新缓存")
    p.add_argument("assets", help="含 filelist 的目录、.apk,或热更新 cache 目录")
    p.add_argument("-o", "--out", default=None,
                   help="输出目录(默认目标旁边的 out/original)")
    p.add_argument("--filelist", default=None,
                   help="手动指定 filelist(默认自动找最新的 filelist_*.r)")
    p.add_argument("-c", "--convert", nargs="?", const="", default=None,
                   help="解包后顺带转换;可选转换输出目录(默认目标 out/converted)")
    p.add_argument("--cache", action="store_true",
                   help="强制按热更新缓存解析(percent-encoded 文件名)")
    p.add_argument("--base", default=None,
                   help="热更新缓存叠加的基础 assets 目录")
    p.add_argument("--only", default=None,
                   choices=sorted(gf.CATEGORY_PREFIXES.keys()),
                   help="解包时只取某一类资源")
    p.add_argument("--include", action="append", default=[])
    p.add_argument("--exclude", action="append", default=[])
    p.add_argument("--keep-partial", action="store_true")
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("convert", help="original -> converted")
    p.add_argument("src", nargs="?", default=str(DEFAULT_OUT / "original"))
    p.add_argument("-o", "--out", default=None,
                   help="输出目录(默认 src 旁边的 converted)")
    p.add_argument("--only", default="tex,models,ui,mat",
                   help="逗号分隔: tex,models,ui,mat,fx")
    p.add_argument("--tex-root", default=None,
                   help="贴图 PNG 根目录(默认 <out>/tex)")
    p.add_argument("--clips", action="store_true",
                   help="models 同时写入 .ans 的全部动画 clip")
    p.add_argument("--orphan-clips", action="store_true",
                   help="同时包含同模型未登记的 <model>_*.anf(如 attack_1)")
    p.add_argument("--fps", type=float, default=30.0,
                   help="动画时间轴帧率(默认 30)")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("prepare", help="PNG -> raw BGRA .tex(可再封包)")
    p.add_argument("src")
    p.add_argument("-o", "--out", default=None,
                   help="输出目录(默认目标旁边的 out/datapre-时间戳)")
    p.add_argument("--tex-format", default="bgra",
                   help="当前只支持 bgra/raw;ATF/ETC/DXT 尚未实现")
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("pack", help="目录 -> .r + filelist_<version>.r")
    p.add_argument("src")
    p.add_argument("-o", "--out", default=None,
                   help="输出目录(默认目标旁边的 out/datarel-时间戳)")
    p.add_argument("--version", default=None,
                   help="filelist 版本号(默认读根 version.inf / 已有 filelist)")
    p.add_argument("--limit", type=int, default=204800,
                   help="逻辑包/分片大小(默认 204800 = 200KiB)")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("pack-records", help="文件 -> 单个 .r(旧版记录级)")
    p.add_argument("out")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_pack_records)

    p = sub.add_parser("info", help="显示路径/版本")
    p.set_defaults(func=cmd_info)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        return 1
