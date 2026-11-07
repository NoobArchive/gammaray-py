# GammaRay Resource Workshop (Python)

Gamma 游戏资源工具的统一入口。后端在 `src/deltaxutils/`
(`gamma_formats` / `gamma_models` / `rpack` / `texbuild`),
GammaRay 负责工作流。

```text
unpack   目录 / .apk            -> out/original(可 -c 顺带转换)
convert  out/original           -> out/converted
prepare  PNG                    -> raw BGRA .tex(可再封包)
pack     目录                   -> .r + filelist_<version>.r
```

## 快速开始

```powershell
cd gammaray-py

# 1) 解包目录或 APK(自动找 filelist_*.r;也可 --filelist 指定)
python gammaray.py unpack ..\V3-1.3.3\assets -o ..\out\original

# 解包 APK 并顺带转换(默认到 out/converted)
python gammaray.py unpack ..\粉刷匠大作战_1.3.3.apk -o ..\out\original -c

# 2) 只转换(默认 src=out/original, out=out/converted)
uv run --with pillow --with texture2ddecoder `
    python gammaray.py convert --only tex,models,ui,mat

# 3) 预处理:PNG -> raw BGRA .tex
uv run --with pillow python gammaray.py prepare ..\out\original `
    -o ..\out\datapre

# 4) 目录封包 + filelist(200KiB 分组/分片)
python gammaray.py pack ..\out\datapre -o ..\out\datarel `
    --version 1.3.3.4.7
```

`convert` 输出:

```text
out/converted/
  tex/     保持原相对路径
  models/  role/mod/*.glb, fx/mod/*.glb
  ui/      gui/**/*.png
  mat/     mat/*.json
  fx/      cfg/*.json (--only fx)
```

## 命令

| 命令 | 说明 |
|---|---|
| `unpack` | 解包含 filelist 的目录或 `.apk`;`-c` 顺带转换 |
| `convert` | 批量转换 `.tex` / `.ams` / `.gui` / `.mtr` / `.eft` |
| `prepare` | PNG 转 raw BGRA `.tex`(目前仅此编码) |
| `pack` | 目录 → `.r` + `filelist_<version>.r`,200 KiB 分组/分片 |
| `pack-records` | 旧版记录级打包(单 `.r`,不写 filelist) |
| `info` | 显示版本与 legacy 目录 |

## 默认路径

```text
unpack  -> out/original
convert -> out/converted
prepare -> out/datapre-YYYYMMDDHHMMSS
pack    -> out/datarel-YYYYMMDDHHMMSS
```

## 边界

- `pack` 不生成服务器用的 `.z` 压缩包,`csize` 只是 zlib 估算;
- `prepare` 只实现 raw BGRA(`format=3`),ATF/ETC/DXT 未确定;
- GLB → `.ams` 尚未实现(动画/蒙皮/材质字段仍有缺口);
- APK 只解包、不重打包/签名;
- 详见 [`../docs/formats/rpack.md`](../docs/formats/rpack.md)、
  [`../docs/GETTING-STARTED.md`](../docs/GETTING-STARTED.md)。
