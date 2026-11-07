# 如何进行某特定操作？

> 所有命令默认在 `gammaray-py/` 下执行。`GammaRay` 是统一入口;
> 需要单步排查时再直接调用 `deltaxutils/gamma_formats.py`。

## 解包目录

```powershell
python gammaray.py unpack <含 filelist 的目录> -o out/original
```

- 自动选择最新的 `filelist_*.r`;需要时加 `--filelist filelist_1.3.3.4.7.r`;
- 只解某一类:`--only config|tex|role|...`;
- 路径过滤:`--include "role/mod/*"` / `--exclude "map/*"`。

## 解包 APK

```powershell
python gammaray.py unpack <x.apk> -o out/original
```

工具会把 APK 的 `assets/` 解到临时目录,寻找其中的 `filelist_*.r`,
解包完成后清理临时目录。APK 只解包,不重打包/签名。

## 解包并顺带转换

```powershell
python gammaray.py unpack <assets 或 apk> -o out/original -c
python gammaray.py unpack <assets 或 apk> -o out/original -c out/converted
```

`-c` 不带路径时输出到 `out/converted`;带路径时输出到指定目录。

## 只转换

```powershell
# 默认 src=out/original, out=out/converted
uv run --with pillow --with texture2ddecoder python gammaray.py convert

# 只转贴图/模型/UI/材质
python gammaray.py convert out/original -o out/converted --only tex,ui
```

`models` / `ui` 依赖 `tex` 生成的 PNG:先转 `tex` 再转它们。
模型需要动画时加 `--clips`;需要未登记废案时加 `--orphan-clips`。

## PNG 预处理成 `.tex`

```powershell
uv run --with pillow python gammaray.py prepare <目录> -o out/datapre
```

- `.png/.jpg/.bmp` → 同名 `.tex`;其它文件原样复制;
- 当前只实现 **raw BGRA**(`format=3`,storage=0,1 级 mip);
- `--tex-format` 暂时只接受 `bgra` / `raw`;
  ATF/ETC2/DXT 的容器和 mip 规则还没确定,不要假装支持。

## 目录封包 + 生成 filelist

```powershell
python gammaray.py pack <目录> -o out/datarel --version 1.3.3.4.7
```

规则:

- 根目录 `etc/`、`version.inf`、`filelist_*.r` 不打包、不写 filelist;
- 同一目录下按文件名排序,累计到 200 KiB 为一个逻辑包;
- 单个文件超过 200 KiB 时独占一个包,整条 `.r` 流按 200 KiB 分片;
- 输出 `<dir>/<md5>.r` 和 `filelist_<version>.r`。

`--limit` 可改分组/分片阈值;`-q` 减少输出。

## 只做一个记录级 `.r`

```powershell
python gammaray.py pack-records out/one.r a.bin b.bin c.bin
```

不写 filelist、不分片;多成员顺序必须自己保证。

## 替换游戏里的单个小文件(不更新 filelist)

直接把文件按原成员顺序打成 `.r`,覆盖同名 `.r` 即可。
客户端只会在日志里报 `CheckLocalBuffer: read error size`,仍会加载;
filelist 的 `size` 是旧 `.r` 长度,详见
[`../docs/formats/rpack.md`](../docs/formats/rpack.md)。

## 校验 filelist / 包

```powershell
python gamma_formats.py fl-verify <assets> --filelist filelist_1.3.3.4.7.r
python gamma_formats.py fl-list <assets>/filelist_1.3.3.4.7.r -n 20
```

## 单个格式

```powershell
python gamma_formats.py tex-info x.tex
python gamma_formats.py tex-batch <目录> <输出目录>
python gamma_formats.py gui-render x.gui out.png --tex-root <png根> --no-widget-bg
python gamma_formats.py gui-batch <gui目录> <输出目录> --tex-root <png根>
python gamma_formats.py eft-info x.eft --units --json x.json
python gamma_formats.py mtr-info x.mtr --json x.json
python gamma_formats.py eqp role/dress/male.eqp --json male.json
python gamma_formats.py skins config/resource/equip.txt --json skins.json
python gamma_models.py ams2glb x.ams out.glb --tex-root <png根> --ani-root <ani目录>
```

## UI 透明背景

- 画布默认完全透明;
- `--bg 181A20` 恢复旧蓝黑底,`--bg RRGGBBAA` 指定 alpha;
- `--no-widget-bg` 去掉控件自己的 `(60,80,110,40)` 占位底色。

## 常见问题

| 现象 | 处理 |
|---|---|
| `unpack` 说找不到 filelist | 用 `--filelist` 指定,或确认目录根/APK 的 `assets/` 下有 `filelist_*.r` |
| `prepare` 说需要 Pillow | 用 `uv run --with pillow python gammaray.py prepare ...` |
| 模型没有贴图 | 先 `convert --only tex`,或加 `--tex-root` |
| UI 没有文字 | `convert` 的 src 下要有 `config/dictionary` |
| `pack` 后游戏报 size 警告 | 说明覆盖了旧 `.r` 但没同步 filelist;需要时用新生成的 filelist 替换 |
| `pack` 的压缩尺寸和官方不同 | `csize` 是 zlib 估算,服务器 `.z` 压缩器尚未实现 |
