# 古域2 资源探测记录(0.9.12.31)

> 数据源:`_agent-sandbox-guyu261103/guyu2_pkg_unpack`
> 产物:`_agent-sandbox-guyu261103/out_tex_all`、`out_glb_all`、`out_ui`
> 二进制:`D:\game\guyu2\bin\release\ShellClient.exe`、
> `GammaGraphic.dll`、`GammaGUI.dll`
> 格式细节见 [`formats/version-matrix.md`](formats/version-matrix.md)。

## 1. 目录规模

```text
91,260 个文件,约 1.00 GB
```

主要扩展名:

```text
.bin  66,868   (未定名/未定类型)
.tex   8,701
.ams   5,118
.anf   4,474
.eft   2,207
.ans     863
.lua     782
.gui     581
.map     576
.txt     458
.mtr      67
```

## 2. 版本分布

| 类型 | 版本 | 数量 |
|---|---|---:|
| `.tex` | 0x2711 / 0x2712 / 0x2713 | 657 / 2961 / 5081 |
| `.tex` | 0x2715 | 1 |
| `.ams` | 0x2711 / 0x2713 / 0x2714 | 1 / 2409 / 2707 |
| `.ans` | 0x2711..0x2714 | 863 |
| `.anf` | 0x2711 / 0x2712 / 0x2714 | 4474 |
| `.gui` | 0x2714 | 581 |
| `.eft` | 0x2711..0x2718 | 2207 |
| `.mtr` | 0x2712 / 0x2713 | 20 / 47 |

另有 2 个 `0x7000` 文件(`e_c007_01_t02.tex`、`l_f021_16.ams`)首 dword 为
`0x0C`,不是 `CCommonFileHead`,属地图/地形专用块,本轮不处理。

## 3. 已打通

### `.tex` → PNG

- 旧版头在 **main 开头 24 字节**,`extraSize=0`;
- `>=0x2712` 每级 mip 前有 `u32 compressedSize`;
- 小于理论大小时是自定义**块 RLE**(16-bit 命令,`bit15` 区分
  “重复一块”和“连续 n 块”);
- 8700/8700 成功:5400 DXT1 + 3209 DXT5 + 58 RGBA32 + 23 RGB24
  + 8 A4R4G4B4 + 2 RGB565。

### `.ams` → GLB

- 旧版 piece 主数据有 per-piece 头:`u8 flags / u32 / f32 scale /
  u16 / u16 vertexCount`;
- 顶点 stride 由 `bits = max(size)*4` 与 `scale` 通过 `GetVertexSize` 算出;
- `(flags & 7) == 1` 时追加 8B 蒙皮;
- 5117/5117 成功(2409 个 0x2713、2707 个 0x2714、1 个 0x2711),
  8889 个 piece。

### `.gui` → PNG

- 0x2714 字符串是 UTF-16LE(长度=字符数);
- state 是 **44B**:`tex + uv[4] + dst[4] + k + u0`;
- list 后没有 groups,`windows` 只在根节点读;
- 581/581 精确闭合,581 张预览。

### `.mtr`

- 0x2712:`1B 占位 + 11B flags + 6 dwords`;
- 0x2713:`u32+ASCII 着色器名 + 11B flags + 7 dwords`;
- 67/67 精确闭合,`alphaMode`/`doubleSided` 可用于 GLB。

### `.ans` / `.anf` / `.eft`

- 863/863、4474/4474、2207/2207 全部可解析;
- 旧版 `.anf`/`.eft` 没有新增字段,现有分支直接覆盖。

## 4. 产物

| 产物 | 内容 | 数量 | 体积 |
|---|---|---:|---:|
| `out_tex_all/` | PNG | 8700 | ~536 MB |
| `out_glb_all/` | GLB | 5117 | ~1.0 GB |
| `out_ui/` | PNG | 581 | ~28 MB |

GLB 按 basename 输出,不同目录的同名 `_hash/*.ams` 会覆盖,
unique 名字约 5085 个。

UI 统计:

```text
states=29419 drawn=19415 solid=29
wrapped=12617 flipped=5759 tiled=0 hidden=123 texts=0
```

`wrapped` 高是古域2 的正常现象:UV 经常越界,引擎依赖纹理 repeat。

## 5. 仍未覆盖

- `.map`(576)、`.rgn`(9687+)、`.skc`(743)、`.tsk`(225)、
  `.sce`(103):专属结构,没有转换器;
- `.ans/.anf/.eft` 只验证“可解析”,未做逐文件语义导出;
- `.gui` 的 44B state 末 4 字节位域未逐位确认;
- 0x2715 `.tex` 只有 1 例;
- 本地化词典在独立 `dictionary.pkg`,本轮没有 `config/dictionary/*.txt`,
  所以 UI 预览 `texts=0`;
- 2 个 `0x7000` 地图块未解析。
