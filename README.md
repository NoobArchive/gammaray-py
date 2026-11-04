# GammaRay Resource Workshop (Python)

Gamma 游戏资源工具的统一入口。当前是**可运行的最小版本**:

```
unpack   assets -> out/original
convert  out/original -> out/converted
pack     files -> 单个 .r(记录级)
```

后端在 `src/deltaxutils/`(原 `gamma_formats.py` / `gamma_models.py`),
GammaRay 只负责前端工作流;模块继续拆分计划见
[`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)。

## 快速开始

```powershell
cd gammaray-py

# 1) 解包
python gammaray.py unpack <apk>/assets -o ..\out\original `
    --filelist filelist_1.3.3.4.7.r

# 2) 批量转换(先 tex,再 models/ui)
uv run --with pillow --with texture2ddecoder --with numpy `
    python gammaray.py convert ..\out\original -o ..\out\converted `
    --only tex,models,ui,mat
# 需要动画: 追加 --clips;未登记废案再加 --orphan-clips

# 3) 打包一个文件
python gammaray.py pack ..\out\packed\one.r <file>
```

`convert` 输出:

```
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
| `unpack` | 按 filelist 分组/拼接 `.r` 分片并解包 |
| `convert` | 批量转换 `.tex` / `.ams` / `.gui` / `.mtr` / `.eft` |
| `pack` | 记录级打包;不更新 filelist/不分片/不签名 |
| `info` | 显示版本与 legacy 目录 |

## 现阶段的边界

- `pack` 不是完整 APK 回包器;见 `docs/formats/package.md`;
- `convert` 目前串行,失败文件会打印并继续;
- 3D/UI/特效依赖 Pillow;ETC 贴图依赖 `texture2ddecoder`;
- 详细工作流见 [`../docs/GETTING-STARTED.md`](../docs/GETTING-STARTED.md)。

## 目标布局

```
src/gammaray/
  cli.py legacy.py
  common.py package.py tex.py eqp.py ui.py fx.py
  models/{ams,ans,anf,glb}.py
  convert.py pack.py
```

迁移顺序:`common/package/tex` → `ams/ans/anf/glb` → `gui/eft/mtr`。
旧 `gamma_formats.py` / `gamma_models.py` 保留同名命令作为兼容壳。
