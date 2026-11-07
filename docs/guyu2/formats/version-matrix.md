# 古域2 旧版资源格式(0.9.12.31)

> 样本:`D:\game\guyu2`、`_agent-sandbox-guyu261103/guyu2_pkg_unpack`
> 二进制:`bin/release/ShellClient.exe`、`GammaGraphic.dll`、`GammaGUI.dll`
> **古域2 的版本号比手游低(0x2711..0x2714)**,这里记录的是它与
> 1.3.3(0x2716/0x2717)不同的地方;通用字段仍见 `docs/formats/*.md`。

## 1. 版本矩阵

| 类型 | 版本 | 数量 | 结果 |
|---|---|---:|---|
| `.tex` | 0x2711 / 0x2712 / 0x2713 | 657 / 2961 / 5081 | 8700/8700 可转 PNG |
| `.tex` | 0x2715 | 1 | 地图大图,单独布局,已支持 |
| `.ams` | 0x2711 / 0x2713 / 0x2714 | 1 / 2409 / 2707 | 5117/5117 可转 GLB |
| `.gui` | 0x2714 | 581 | 581/581 精确闭合 + 可渲染 |
| `.mtr` | 0x2712 / 0x2713 | 20 / 47 | 67/67 精确闭合 |
| `.ans` | 0x2711..0x2714 | 863 | 863/863 |
| `.anf` | 0x2711 / 0x2712 / 0x2714 | 4474 | 4474/4474 |
| `.eft` | 0x2711..0x2718 | 2207 | 2207/2207 |
| `.map` / `.rgn` / `.skc` / `.tsk` / `.sce` | — | 576 / 9687+ / 743 / 225 / 103 | 未做转换器 |

两个 `0x7000` 文件(`map/e_c007_01_t02.tex`、`map/五龙/五龙植物/model/l_f021_16.ams`)
不是 `CCommonFileHead`,首 dword 为 `0x0C`,内容为地图编辑器/地形专用块,
本轮不按 `.tex`/`.ams` 处理。

## 2. 公共头

`CCommonFileHead` 与手游一致:4CC + version + depCount + deps + extraSize。
古域2 的 `.tex` 把纹理参数放在 **main** 里(extraSize=0),不是 extra;
`.ams`/`.gui`/`.mtr` 的参数仍在 extra。

## 3. 各格式

- [`tex.md`](tex.md):旧纹理头、逐级 u32 长度、块 RLE。
- [`ams.md`](ams.md):piece 主数据头、按包围盒算顶点 stride、蒙皮。
- [`gui.md`](gui.md):0x2714 的 UTF-16 字符串、44B state、无 group/window 递归。
- [`mtr.md`](mtr.md):0x2712/0x2713 的着色器占位和 6/7 个旧材质 dword。

## 4. 验证方法

1. **恰好读完**:`.gui` 581/581 要求 `r.p == len(extra)`;
   `.mtr` 67/67 要求 `r.p == len(extra)`;
   `.ams` 要求 `pos == len(main)` 且所有 index < vertexCount;
   `.tex` 要求每级 mip 解出的字节数等于理论大小。
2. **二进制对照**:
   - `.tex` 旧头/RLE 来自 `GammaGraphic.dll` 的 `sub_1004BCE0`、
     `sub_1004CF50`、`sub_100501D0`、`sub_10015FB0`、`sub_10035830`;
   - `.ams` piece 主数据来自 `CPiece::ReadMainData`(vtable
     `0x100B6C50` slot 10 `sub_10035C10`)与 `Gamma::LoadVertex`
     (`sub_10038A60`);
   - `.gui` 0x2714 来自 `GammaGUI.dll` `sub_10021A00`;
   - `.mtr` 旧字段由 0x2716/0x2717 已知布局 + 逐样本长度对拍。
3. **产物抽检**:`out_tex_all`、`out_glb_all`、`out_ui` 已人工看过
   `main23`、`a_m039_t01`、`01`、`map0x2715`、`310_1`、`d2d0238a` 等样本。

## 5. 剩余缺口

- 旧版 `.map/.rgn/.skc/.tsk/.sce` 没有公开的字段映射,未转;
- `.ams` 0x2711 只有 1 个样本,`flags & 7` 在 0x2711 里**不是**蒙皮位,
  已按“0x2712+ 才加 8B 蒙皮”处理,更多样本出现时需要复核;
- `.gui` 0x2714 的 44B state 末 4 字节只确定到 `u32`(样本值 0/15),
  具体位域(平铺/纯色)未在二进制里逐位确认;预览按现代 `u[0]` 处理;
- 0x2715 `.tex` 只有 1 个地图样本,头部布局(8B 前缀 + `[w,h,format,mip]`)
  属于经验解,未见第二例;
- `.tex` 逐级 mip 只用于校验和缩略图,产物只导出第 0 级。
