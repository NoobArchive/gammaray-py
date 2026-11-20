# gammaray-py TODO

> 最新条目在最上面。状态记号：`[ ]` 未做 ｜ `[~]` 进行中/部分 ｜ `[x]` 已完成。
> 请登记新条目的时间和状态发生变化的条目相应时间及其更改哈希

> 不执行 Markdown 严格换行：在编辑器下排版易用即可，当前不追求在要求严格的渲染器中完美渲染


## 已完成(第一版)

- [x] `gammaray unpack`(目录 / `.apk` / `-c` / **热更新 cache**) 261120
- [x] 默认输出改为目标旁边的 `out/`(不依赖 pwd) 261120
- [x] `gammaray convert`(tex / models / ui / mat / fx) 261107
- [x] `gammaray prepare`(PNG → raw BGRA `.tex`) 261107
- [x] `gammaray pack`(目录 → `.r` + `filelist_<ver>.r`,200 KiB 分组/分片) 261107
- [x] `gammaray pack-records`(旧记录级 `.r`) 261107
- [x] `gammaray info`
- [x] legacy 定位/导入(`src/gammaray/legacy.py`)

## 回打包/预处理后续

- [ ] `pack` 复刻官方分组(需要服务器打包清单;当前只按 200 KiB 贪心)
- [ ] `pack` 生成服务器 `.z` 并写入准确的 `csize`
- [ ] `prepare --tex-format` 支持 ATF/ETC2/DXT(先确认 shader/格式映射)
- [ ] `prepare` 生成 mip 链
- [ ] GLB → `.ams`(动画/蒙皮/材质字段缺口)
- [ ] APK 重打包/签名(暂不实现)

## 迁移

261104：已经计划将原始工具更名为 deltaxutils 并作为后端工具迁移到该 repo，GammaRay 作为前端，似乎以下迁移意义不大？
deltaxutils 准备不再弃用。

- [ ] 把 `common.py`(CCommonFileHead / decode_text)从 `gamma_formats.py` 搬出
- [ ] 把 `package.py`(.r / filelist / 分片)搬出
- [ ] 把 `tex.py` 搬出
- [ ] 把 `models/{ams,ans,anf,glb}.py` 从 `gamma_models.py` 搬出
- [ ] 把 `ui.py` / `fx.py` / `eqp.py` 搬出

## 功能

- [ ] `convert --jobs N` 并行(先保证串行正确)
- [ ] `convert` 输出 manifest.json(输入/输出/失败清单)
- [x] 目录封包 + filelist(200 KiB 分组/分片) 261107
- [x] `pack` 从根 `version.inf` 推断 filelist 版本,并复制 `etc/`、`version.inf` 到输出 261107
- [x] 输出目录中清理旧的 `filelist_*.r`,避免游戏加载旧表 261107
- [ ] `pack --replace`(只替换指定包、保持原成员顺序)
- [x] `pack` 的成员顺序/大小校验(生成后 `size` 全量自检) 261107
- [x] `unpack` 支持直接读取 APK(zip) 261107
- [ ] 失败重试/断点续跑

## 测试

- [x] `fl-verify` 636/636 261107
- [x] `.eft` 754/754 prop 261107
- [x] `.mtr` 12/12 261107
- [x] `.gui` 213/213 261107
- [x] `ams2glb` role 78/78、fx 59/59 261107
- [x] `convert` smoke test(小目录) 261107
- [x] `pack` gx_dump:成员 2515/2515、内容差异 0 261107
- [x] `prepare` PNG→TEX→PNG 像素差异 0 261107
