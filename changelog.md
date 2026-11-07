# Changelog - DeltaXUtils & GammaRay

当前还没有正式的发布版本号规则，暂时以时间安排。

## 2026-11-07

- `gammaray unpack`:直接接受目录或 `.apk`;自动寻找 `filelist_*.r`;
  `-c [目录]` 解包后顺带转换。
- `gammaray convert`:默认 `out/original` → `out/converted`;
  `--only tex,models,ui,mat,fx`。
- `gammaray prepare`:PNG/JPEG/BMP → raw BGRA `.tex`(`format=3`,1 mip);
  `--tex-format` 目前只接受 `bgra`/`raw`。
- `gammaray pack`:目录 → `.r` + `filelist_<version>.r`;
  同目录 ≤200 KiB 合并,大文件 200 KiB 分片;排除根 `etc/`、`version.inf`。
- `gammaray pack-records`:保留旧版记录级 `.r` 打包。
- 新增 `deltaxutils/rpack.py`、`deltaxutils/texbuild.py`。
- `fl-verify` 增加 `size_mismatch`(filelist `size` 与实际 `.r` 长度)。
- `.eqp` 改为按 `CEquipmentGroup` 完整解析。
- UI 预览默认透明画布;新增 `--bg` / `--no-widget-bg`。