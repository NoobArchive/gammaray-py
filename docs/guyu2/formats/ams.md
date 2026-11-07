# 古域2 旧版 `.ams`(0x2711..0x2714)

> 对照实现:`gammaray-py/src/deltaxutils/gamma_models.py`
> `_legacy_vertex_size` / `_parse_legacy_pieces` / `parse_ams`
> 二进制:`GammaGraphic.dll`

## 1. 与 0x2715+ 的整体区别

- extra(index data)的类/名字/piece 包围盒/材质表布局基本一致;
- **piece 主数据(main)多了一个 per-piece 头**,顶点 stride 由包围盒和
  缩放值算出,不能再用“扫描 8/9/10/11/12 + 蒙皮 8/0”去猜;
- 0x2715+ 的 main 以 `u16(通常0) u16(vertexCount)` 开头,旧版不是。

## 2. piece 主数据

```text
per piece:
  u8   flags              (flags & 7) == 1 -> 顶点后追加 8B/顶点蒙皮
  u32  unknown            实测 0
  f32  scale              GetVertexSize 的第二个参数
  [f32 ?]                 version < 0x2712 才有
  u16  unknown            通常 0
  u16  vertexCount
  u8[vertexCount * stride]
  u8[vertexCount * 8]     仅 flags & 7 == 1
  u32  indexCount
  u8/u16 indices[]        vertexCount > 255 ? u16 : u8
```

`main` 是这些 piece 依次拼接,**最后一个 piece 的 index 末尾必须等于
`len(main)`**。5117 个 0x2711/0x2713/0x2714 模型全部满足。

`unknown` 在样本里恒为 0,暂未定名;`flags` 的高位(0x08/0x10)存在,
但只有低 3 位参与蒙皮判断。

## 3. 顶点 stride

`Gamma::GetVertexSize`(`sub_10035830`),输入:

```text
bits  = int(max(size.x, size.y, size.z) * 4 + 0.5)   // 来自 extra 的包围盒
scale = per-piece 的 f32
```

```text
bits < 1024 and scale <= 1.02              -> 8
bits < 4096 and scale <= 2.04              -> 9
bits < 4096 and scale <  32.700001         -> 10
bits < 0x4000 and scale < 32.700001        -> 11
否则                                        -> 12
```

然后 `(flags & 7) == 1` 时再加 8 字节蒙皮。
8/9/10/11/12 的位域与手游完全一致,仍用
`VARIANTS = {8:(10/10/10 pos,12 normal,11/11 uv), ...}` 解码。

顶点位置:

```text
pos = packed * 0.25 + (center - size * 0.5)
```

`center/size` 来自 extra,`bits` 只决定 stride,不再对坐标做额外缩放。

## 4. 0x2711 的特殊点

古域2 只有 1 个 0x2711(`role/_hash/f8134a80.ams`):

- 头比 0x2712+ 多 4 字节(在 `scale` 后、`vertexCount` 前);
- `flags = 1` 但按 8 字节蒙皮计算会超出文件,按 **无蒙皮** 才能精确闭合
  (150v/196tri)。这说明 0x2711 的 `flags` 还不是“蒙皮位”;
- 实现里用 `version >= 0x2712 and (flags & 7) == 1` 作为蒙皮条件,
  0x2711 暂按无蒙皮处理。

## 5. extra(index data)

沿用现有 `parse_ams_extra`:

```text
u16 classCount
per class: u32 nameLen, utf16 name, u16 pieceCount
per piece:
  f32x3 size
  f32x3 center
  [>0x2711] f32x3, f32x3          // 第二组 size/center
  [>0x2712] u8 matCount, per mat: u16 tag, u8 subCount,
            per sub: (u8 n if >0x2713 else 1) * u16 texIndex
```

注意 `pieceCount` 实际是 **u16**,不是 u32;后续浮点从紧邻位置开始。
古域2 样本的 size/center 都正常读出,例如 `310_1`:
size=(28.5,54,25.5)、center=(40.5,3.75,0)。

## 6. 产物

```text
_agent-sandbox-guyu261103/out_glb_all/
  5117 个 GLB,约 1.0 GB
```

同名文件(不同目录的 `_hash/*.ams`)会互相覆盖,实际 unique basename
约 5085 个;需要全量保留时应按相对路径输出。
