# gammaray-py TODO

> 最新条目在最上面。状态记号：`[ ]` 未做 ｜ `[~]` 进行中/部分 ｜ `[x]` 已完成。
> 请登记新条目的时间和状态发生变化的条目相应时间及其更改哈希

> 不执行 Markdown 严格换行：在编辑器下排版易用即可，当前不追求在要求严格的渲染器中完美渲染


## 已完成(第一版)

- [x] `gammaray unpack`(复用 `gamma_formats.cmd_unpack`)
- [x] `gammaray convert`(tex / models / ui / mat / fx)
- [x] `gammaray pack`(记录级 `.r`)
- [x] `gammaray info`
- [x] legacy 定位/导入(`src/gammaray/legacy.py`)

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
- [ ] `pack --replace`(保持成员顺序 + 更新 filelist + 200KiB 分片)
- [ ] `pack` 的成员顺序/大小校验
- [ ] `unpack` 支持直接读取 APK(zip)而不只是已解压 assets
- [ ] 失败重试/断点续跑

## 测试

- [ ] `fl-verify` 636/636
- [ ] `.eft` 754/754 prop
- [ ] `.mtr` 12/12
- [ ] `.gui` 213/213
- [ ] `ams2glb` role 78/78、fx 59/59
- [ ] `convert` smoke test(小目录)
