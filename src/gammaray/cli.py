"""gammaray 统一 CLI。

第一版只做三件事:
  unpack  : assets -> out/original
  convert : out/original -> out/converted (tex/models/ui/mat/fx)
  pack    : files -> 单个 .r

内部复用 gamma_formats.py / gamma_models.py;后续按 ARCHITECTURE.md 搬模块。
"""

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

from . import __version__
from .legacy import gf, gm, LEGACY_DIR


def _out_path(path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def cmd_unpack(args):
    ns = argparse.Namespace(
        assets=args.assets,
        outdir=args.out,
        filelist=args.filelist,
        only=args.only,
        include=args.include or [],
        exclude=args.exclude or [],
        keep_partial=args.keep_partial,
    )
    gf.cmd_unpack(ns)


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


def _convert_models(src, out, tex_root, counts, clips=False, fps=30.0):
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
                gm.write_glb(str(dst), mesh, textures, anims=(
                    gm.ans_clip_paths(mesh["ans_path"]) if clips and mesh.get("ans_path") else None
                ), fps=fps, mat_opts=mat_opts)
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
    out = Path(args.out)
    if not src.exists():
        raise SystemExit(f"源目录不存在: {src}")
    only = {x.strip() for x in (args.only or "tex,models,ui,mat").split(",") if x.strip()}
    valid = {"tex", "models", "ui", "mat", "fx"}
    unknown = only - valid
    if unknown:
        raise SystemExit(f"未知的 --only: {', '.join(sorted(unknown))}; 可选: {', '.join(sorted(valid))}")
    tex_root = out / "tex"
    counts = {}
    if "tex" in only:
        print("[1/4] tex -> PNG")
        _convert_tex(src, tex_root, counts)
    if "models" in only:
        print("[2/4] models -> GLB")
        _convert_models(src, out, tex_root, counts, args.clips, args.fps)
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


def cmd_pack(args):
    blobs = [Path(f).read_bytes() for f in args.files]
    data = gf.build_package(blobs)
    out = _out_path(args.out)
    out.write_bytes(data)
    print(f"packed {len(blobs)} record(s) -> {out} ({len(data)} bytes)")
    print("注意: 这不更新 filelist、不做 200KiB 分片、不处理 APK 签名。")


def cmd_info(args):
    print(f"gammaray {__version__}")
    print(f"legacy: {LEGACY_DIR}")
    print("commands: unpack / convert / pack / info")
    print("docs: docs/GETTING-STARTED.md, docs/ARCHITECTURE.md")


def build_parser():
    ap = argparse.ArgumentParser(prog="gammaray",
                                 description="Gamma 资源工具统一入口")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("unpack", help="解包 assets -> out/original")
    p.add_argument("assets")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--filelist", default="filelist_1.3.3.4.7.r")
    p.add_argument("--only", default=None,
                   choices=sorted(gf.CATEGORY_PREFIXES.keys()))
    p.add_argument("--include", action="append", default=[])
    p.add_argument("--exclude", action="append", default=[])
    p.add_argument("--keep-partial", action="store_true")
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("convert", help="out/original -> out/converted")
    p.add_argument("src")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--only", default="tex,models,ui,mat",
                   help="逗号分隔: tex,models,ui,mat,fx")
    p.add_argument("--clips", action="store_true",
                   help="models 同时写入 .ans 的全部动画 clip")
    p.add_argument("--fps", type=float, default=30.0,
                   help="动画时间轴帧率(默认 30)")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("pack", help="文件 -> 单个 .r(记录级)")
    p.add_argument("out")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_pack)

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
    except Exception:
        traceback.print_exc()
        return 1
