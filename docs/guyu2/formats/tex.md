# 古域2 旧版 `.tex`(0x2711..0x2713, 0x2715)

> 对照实现:`gammaray-py/src/deltaxutils/gamma_formats.py`
> `TexFile.legacy_info` / `legacy_levels` / `legacy_first_level_rgba`
> 二进制:`GammaGraphic.dll`

## 1. 0x2711..0x2713

```text
CCommonFileHead(extraSize = 0)
main:
  +0x00 u32 format      0=DXT1 1=DXT5 2=RGB24 3=RGBA32 4=RGB565 5=A4R4G4B4
  +0x04 u32 width
  +0x08 u32 height
  +0x0C u32 mipCount
  +0x10 u32 width2      (>=0x2713 才存;<=0x2712 由引擎补成 width)
  +0x14 u32 height2
  +0x18 mip[0]
  ...
```

`mip[]` 布局按版本不同:

```text
version <  0x2712: 直接是 mip 数据
version >= 0x2712: u32 compressedSize + 数据
```

`compressedSize >= 理论大小` 时按原样取**理论大小**字节;
小于理论大小时,这 `compressedSize` 字节是块 RLE 流。

理论大小 = `bpp * w * h / 8`:

| format | bpp | RLE block |
|---|---:|---:|
| 0 DXT1 | 4 | 8 |
| 1 DXT5 | 8 | 16 |
| 2 RGB24 | 24 | 3 |
| 3 RGBA32 | 32 | 4 |
| 4 RGB565 | 16 | 2 |
| 5 A4R4G4B4 | 16 | 2 |

### RLE(`sub_100501D0`)

```text
loop until 输入读完:
    cmd = u16
    n   = cmd & 0x7FFF
    if cmd & 0x8000:          # 连续 n 个块
        read n * block bytes, 原样输出
    else:                     # 同一个块重复 n 次
        read 1 * block bytes, 输出 n 份
```

`block` 就是上表的字节数(不是纹理行宽)。解出的长度必须正好等于理论大小。

## 2. 0x2715(地图大图)

古域2 只见到 1 个 `0x2715` 纹理(`map/_hash/00477af3.tex`,1024x512 DXT1)。
它的 24B 参数块不在 main 开头,而是:

```text
main:
  +0x00 u32 0
  +0x04 u32 0
  +0x08 u32 width        1024
  +0x0C u32 height       512
  +0x10 u32 format       0
  +0x14 u32 mipCount     1
  +0x18 u32 usedWidth    768   (图集实际使用宽度)
  +0x1C u32 usedHeight   512
  +0x20 u32 0
  +0x24 u32 compressedSize
  +0x28 data             RLE 流
```

样本里 `compressedSize == 文件剩余长度`,数据按上面的块 RLE 解到
`1024*512*4/8 = 262144` 字节;右侧 256px 是黑色(usedWidth=768)。
该布局只有一例,尚无第二个样本可以区分“usedWidth”与“padding”。

## 3. 与手游 0x2716/0x2717 的差异

| | 古域2 0x2711..0x2713 | 手游 0x2716/0x2717 |
|---|---|---|
| 头位置 | main 开头 24B | extra 中 9×u32 索引 |
| 字段 | format,w,h,mip,w2,h2 | format,storage,wPow2,hPow2,depth,mips,w,h,reserved |
| 每级长度 | >=0x2712 有 u32 | 有 u32 |
| 压缩 | 自定义块 RLE | ATF / ETC2 / 原样 |
| 传输格式 | DXT1/5、RGB24、RGBA32、565、4444 | ATF(ETC/ETC2/DXT/JXR) |

## 4. 产物

```text
_agent-sandbox-guyu261103/out_tex_all/
  dxt1 5400 / dxt5 3209 / rgba32 58 / rgb24 23 / a4r4g4b4 8 / rgb565 2
```

总计 8700 张 PNG,约 536 MB。
