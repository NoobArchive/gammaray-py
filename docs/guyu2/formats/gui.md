# 古域2 `.gui` 0x2714

> 对照实现:`gammaray-py/src/deltaxutils/gamma_formats.py`
> `_GuiReader.s` / `parse_gui_tree`
> 二进制:`GammaGUI.dll` `sub_10021A00`

## 1. 字符串

全部是 **UTF-16LE**,长度前缀是**字符数**(不是字节数):

```text
u32 charCount
u16 chars[charCount]
```

手游 0x271B/0x271C 已经是 UTF-8,所以 `_GuiReader.s()` 按
`version <= 0x2718` 分支处理。

## 2. 窗口 item

```text
string class            // "CGWnd" / "CGButton" / ...
string name
u32    f0               // style
i32    f1..f4           // x,y,w,h(有符号)
[>=0x2712] u32 g0, g1
u32    h
string skin
u32    i0..i3           // 字号/字间距/行间距/LockFlag
string s16
string s24
string s25
string more[3]          // 0x2714 共 3 个,不是 4/6
u32    f30              // >=0x2713
u32    childCount
per child:
    [除第 1 个外] i32 rect[4]
    u32 listCount
    per list:
        u32 a,b,c
        u32 stateCount
        per state: 44B
root: u32 windowCount + (string name + item)
```

与 0x271D 的差异:

| | 0x2714 | 0x271D |
|---|---|---|
| 字符串 | UTF-16LE | UTF-8 |
| `s17`/`s26` | 无 | 有 |
| `more[]` | 3 | 4 |
| `fade` | 无 | 有 |
| state | **44B** | 54B |
| list 后 `groups` | **无** | >0x2715 有 |
| `windows` | 只在根节点 | 所有深度 |

`sub_10021A00` 里 `list` 是 28 字节结构(`a,b,c` + `vector<state>`),
`state` 的 `vector` 元素是 44 字节;`windows` 由根节点统一读。

## 3. 44B state

```text
+0x00 u32 tex         // deps["tex"] 下标
+0x04 f32 uv[4]       // 源矩形(l,t,r,b),texel
+0x14 f32 dst[4]      // 目标矩形(l,t,r,b)
+0x24 u32 k           // ARGB 染色
+0x28 u32 u0          // 样本值 0 / 15;具体位域未在二进制逐位确认
```

没有现代 state 的 `alpha`/`f`/`u[4]`/`u5`。
当前预览把 `k` 的 A 通道当透明度、`u0` 填到 `u[0]`;
`u[1]`(平铺/纯色标志)置 0。

## 4. UV 越界是常态

古域2 的 GUI 大量使用 `uv` 越界值(例如 `[-246,1424,-226,1434]`
配合 256x256 的 `login01.tex`),引擎按纹理 **repeat** 取模。
当前渲染器 `_tile_source_piece` 会自动平铺,所以 581 个 GUI 里
`wrapped=12617` 属于预期,不是解析错误。

## 5. 产物

```text
_agent-sandbox-guyu261103/out_ui/
  581 张 PNG
  states=29419 drawn=19415 solid=29 wrapped=12617 flipped=5759
  hidden=123 texts=0
```

- `texts=0`:古域2 的本地化词典在独立的 `dictionary.pkg` 里,
  本轮解包目录没有 `config/dictionary/*.txt`;
- 每个 `.gui` 的根矩形都很小(如 303x548、195x124),
  它们是**窗口片段**,整屏由 C++ 在运行时拼装,不是独立全屏界面;
- 纹理依赖里的空名字(`tex[0] == ""`)表示“无纹理”,
  旧版渲染器用它画纯色/遮罩。
