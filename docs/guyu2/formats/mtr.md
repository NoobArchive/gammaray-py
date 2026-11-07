# 古域2 旧版 `.mtr`(0x2712 / 0x2713)

> 对照实现:`gammaray-py/src/deltaxutils/gamma_formats.py` `parse_mtr_extra`
> 参考:1.3.3 的 0x2716/0x2717 布局(`docs/formats/mtr.md`)

## 1. 总布局

```text
0x2712:
  u8   shaderPlaceholder(恒 0;旧版没有着色器名字符串)
  u8   flags[11]
  u32  old[6]              // 24 字节
  [u32 factor]             // 部分文件多 4 字节

0x2713:
  u32  shaderLen + ASCII   // 例如 7 + "Default"
  u8   flags[11]
  u32  old[7]              // 28 字节
```

67/67 个古域2 `.mtr` 按这个布局**恰好读完**。

`flags[11]` 与 0x2716/0x2717 完全同序:

```text
0 alphaBlendEnable
1 srcBlend
2 destBlend
3 f157   (旧版重复的 zWrite)
4 f158
5 alpharef
6 zTestEnable
7 zWrite
8 zTestFunc
9 cull   (1=NONE -> doubleSided)
10 f160
```

## 2. 旧版多出来的 6/7 个 dword

0x2716+ 在 `diffuse` 之后只保留 `specularSharp(1B) + factor(4B)`;
0x2712/0x2713 则直接存放整套材质:

| 下标 | 含义 | 典型值 |
|---:|---|---|
| 0 | 颜色/光照标志 | `0x01010002`,带高光贴图时 `0x0101FC02` |
| 1 | ambient | `0xFFFFFFFF` |
| 2 | diffuse | `0xFFFFFFFF`(或水面的彩色) |
| 3 | specular | `0xFF000000`;金属高光为 `0x616161FF` |
| 4 | emissive | `0xFF000000`;自发光为彩色 |
| 5 | specularPower(f32) | `0x3F800000` = 1.0,`0x407CCCA1` = 3.95 |
| 6 | factor(可选) | ARGB,多数 `0xFFFFFFFF` |

`factor` 只在 0x2713(全部)和部分 0x2712(40 字节而不是 36 字节)出现。
当前实现按 `len(extra)` 决定读几个,因此两种长度都精确闭合。

## 3. 与 0x2716/0x2717 的差异

| | 0x2712/0x2713 | 0x2716/0x2717 |
|---|---|---|
| 着色器名 | 0x2712 无;0x2713 有 | 有 |
| 材质颜色 | ambient/diffuse/specular/emissive 全存 | 只存 diffuse |
| 高光强度 | f32 | 1 字节 `specularSharp` |
| factor | 可选 dword | 固定 4 字节 |
| 总长度 | 36/40/50/54/58 | 24+slen / 26+slen |

## 4. 对 GLB 的影响

`ams2glb` 只使用 `alphaMode` 与 `doubleSided`,它们完全由
`flags[0..2]`/`flags[9]` 决定,所以旧版材质现在和手游一样可用。
ambient/specular/emissive/power 暂未映射到 glTF 扩展。

## 5. 产物

`.mtr` 本身没有单独导出;GLB 导出时自动读取并写入
`alphaMode`/`doubleSided`。古域2 的 5117 个 GLB 已覆盖
`OPAQUE` / `BLEND` / `ADDITIVE` 三种。
