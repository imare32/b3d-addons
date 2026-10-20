# 生成式 Zellij

Craig S. Kaplan 的 [*Generative Zellij*](https://openprocessing.org/sketch/1809458) p5.js 草图
（原文件存于仓库根的 `.ref/`）的 **1:1 算法移植**：几何全部在 Python 里算完，Blender 只负责
把它写进 Grease Pencil v3 数据块，SVG 导出交给 Blender 自带功能。

- 输出对象固定名为 `Zellij`（`GREASEPENCIL`），几何落在局部 XY 平面 `z = 0`
- 设计包围盒按 `Size` 拟合，左下角对齐对象原点
- 同一 `seed` 结果可复现，且与原草图逐块瓷砖一致（几何层用 Node 跑原版 JS 做过比对）
- 需要 Blender 4.3+ / 5.x（Grease Pencil v3）

---

## 快速开始

1. 在 VSCode 里按 `F6` 热加载插件（开发时），或正常启用 `b3d` 扩展；
2. 3D 视图按 `N` 打开侧栏，切到 **Zellij** 页签；
3. 点 **Generate**；
4. 点 Generate 右侧的小图标按钮，一键把所有 3D 视口切成 **Solid + Attribute** 着色
   （填充色来自顶点色，用 Material / Object 之类的着色模式会看不到颜色）。

---

## 面板参数

### Placement

| 参数 | 说明 |
|---|---|
| `Seed` | 随机种子，`-1` 表示每次生成都换一个新设计 |
| `Size` | 正方形画布边长（Blender 单位） |
| `Frame` | 笔划写入的关键帧号 |

### Design

| 参数 | 说明 |
|---|---|
| `Density` | 构造线密度：`Auto`（沿用原草图的随机抽取）/ 6 / 10 / 20 |
| `Focus` | 强制特征：`Auto` / `None` / `Eight`（2×2 区块）/ `Sixteen`（星形） |
| `Shimmer` | 相邻色块的亮度抖动：`Auto` / `On` / `Off` |

`Auto` 一律沿用原草图的随机行为，手动指定才会覆盖。

### Stroke（描边宽度）

三个独立滑块，单位是**相对 `Size` 的半径**（实际半径 = 值 × `Size`），默认都是 `0.001`：

| 滑块 | 作用图层 |
|---|---|
| `Fill` | `Fills_01_xxxxxx` … |
| `Tiling` | `Tiling`（红色瓷砖轮廓） |
| `Lines` | `Lines`（黑色虚线构造线） |

滑块带 `update` 回调，**拖动即改已有对象的点半径**，不需要重新 Generate。

### Opacity（不透明度）

三个滑块，默认都是 `0`，直接映射到三个 `Opacity` 修改器的 `color_factor`：

| 滑块 | 修改器 | 过滤的图层 pass |
|---|---|---|
| `Fill` | `Opacity` | 1 |
| `Tiling` | `Tiling Opacity` | 2 |
| `Lines` | `Lines Opacity` | 3 |

三个修改器的 `color_mode` 都是 `STROKE`，只影响描边、不影响填充块本身。
`0` 表示描边完全透明（靠 `radius` 归零 + `opacity` 归零实现），并不是隐藏图层。

### Output

| 控件 | 说明 |
|---|---|
| `Filled` | 导出时是否得到实心块（关掉即细线轮廓） |
| `Export SVG` | 复用 `wm.grease_pencil_export_svg`，导出前临时把视口切成俯视正交、导出后还原 |

### Layers

列出对象当前所有图层，每层一个 `hide` 勾选框，并显示对象上记录的
`seed / density / focus`。

---

## 输出结构

### 图层

| 图层 | 内容 |
|---|---|
| `Fills_NN_XXXXXX` | 成品马赛克，**按基色量化分组**，`XXXXXX` 是基色的十六进制 |
| `Tiling` | 瓷砖轮廓，顶点色固定为红色 `(1, 0, 0)` |
| `Lines` | 构造线，裁到拼贴区域的凸包内，以虚线显示 |

- 所有图层默认都不隐藏；宽度为 0 时本来就看不见，要看只需拉对应的 Stroke 滑块；
- 填充分组用 `Design.colours` 做**最近色**量化（欧氏距离），但写进 `fill_color` 的仍是
  每个形状自己的颜色——shimmer 的抖动不会被拉平，只是"归到哪一组"用了量化；
- 量化前 shimmer 打开时图层数会爆炸（实测出现过 153 个），量化后稳定在基色数量（5 个左右）+ 2。

### 修改器

| 名称 | 类型 | 过滤 | 关键参数 |
|---|---|---|---|
| `Zellij Dashes` | `GREASE_PENCIL_DASH` | layer pass 3 | `dash = 2`、`gap = 2`（单位：采样点） |
| `Opacity` | `GREASE_PENCIL_OPACITY` | layer pass 1 | `color_mode = STROKE` |
| `Tiling Opacity` | `GREASE_PENCIL_OPACITY` | layer pass 2 | 同上 |
| `Lines Opacity` | `GREASE_PENCIL_OPACITY` | layer pass 3 | 同上 |

**不使用任何材质**，只保留两套索引给修改器过滤：

| 图层 | 笔划 `material_index` | 图层 `pass_index` |
|---|---|---|
| `Fills_*` | 0 | 1 |
| `Tiling` | 1 | 2 |
| `Lines` | 2 | 3 |

### 笔划属性（GPv3）

| 属性 | 域 / 类型 | 用途 |
|---|---|---|
| `position` | POINT / FLOAT_VECTOR | 坐标（批量 `foreach_set`） |
| `radius` | POINT / FLOAT | 描边半径（宽度 × Size） |
| `opacity` | POINT / FLOAT | 0 时完全不可见，避免半径 0 仍画 1px 细线 |
| `vertex_color` | POINT / FLOAT_COLOR | 描边颜色（填充层为派生色，辅助层为红/黑） |
| `fill_color` | CURVE / FLOAT_COLOR | 填充颜色 |
| `fill_id` | CURVE / INT | 非零才参与填充，每条形状一个 ID，避免相邻形状被合并 |
| `hide_stroke` | CURVE / BOOLEAN | **恒为 `False`**（SVG 导出器会整条跳过 `True` 的笔划） |
| `cyclic` | CURVE / BOOLEAN | 是否闭合（来自 `Shape.closed`） |

颜色一律按**线性空间**存储：写入前把 8 bit sRGB 转成线性，视口与导出才是设计色。

---

## 算法要点

1. **设计空间与变换**
   构造线、瓷砖、图案匹配都在设计空间（p5 的 y 向下坐标）里算；最后统一乘一个
   变换矩阵：`fill_box` 拟合 + `y → -y` 翻转，使包围盒对齐 `[0, Size]`。

2. **各拓扑共用同一个变换矩阵**
   `build_layers()` 只算一次 `matrix`（基准是填充图案的包围盒），`Tiling` / `Lines`
   用同一个矩阵。分开各自拟合包围盒会让图层错位。

3. **构造线：凸包裁切 + 固定弧长采样**
   - 原草图把构造线两端各延长一个画布边长，靠**画布裁剪**截断；Blender 没有画布裁剪，
     按原样延长会拖出主体一大截，裁到包围盒又会留下空角，所以裁到**拼贴区域凸包**；
   - 采样间距固定为 `图案包围盒宽度 / LINE_SAMPLES_PER_WIDTH`（size=1 时约 3.1 mm），
     与线长无关。

4. **虚线靠 Dash 修改器**
   `dash` / `gap` 的单位是**采样点个数**，不是长度：
   - 点距 10 mm 时，dash2/gap2 每单位长度约 26 段；
   - 点距 3.1 mm 时约 80 段；
   - `gap = 0` 表示没有间隙，结果是实线。
   所以必须先做等距采样，否则长线点稀、短线点密，虚线疏密会随线长变化。

5. **填充去重**
   相邻瓷砖常被匹配到同一个图案簇，投影后会产生坐标只差 1e-5～1e-4 的重合形状。
   实心填充看不出来，但半透明描边会叠画两次。`_dedupe()` 按 4 位小数量化比较，
   保留最上面一份（颜色以盖在上面的为准）。

6. **描边颜色由填充色派生**
   取填充色的色相，饱和度 `s * 1.35 + 0.15`，明度朝填充色的反方向推
   `v = clamp(0.68 - 0.36v, 0.28, 0.78)`——浅底配深描边、深底配亮描边。
   直接乘系数会把深色压成一片黑、浅色压成一片灰，各层描边看起来就"统一"了。

---

## 常量表

| 常量 | 位置 | 值 | 含义 |
|---|---|---|---|
| `STROKE_SATURATION` | `gp.py` | `1.35` | 填充层描边的饱和度增益 |
| `STROKE_ALPHA` | `gp.py` | `1.0` | 顶点色 alpha（透明度交给修改器，两者相乘会过淡） |
| `LINE_SAMPLES_PER_WIDTH` | `scene.py` | `320` | 构造线沿画布宽度的采样段数 |
| `DASH_LENGTH` / `DASH_GAP` | `gp.py` | `2` / `2` | 虚线的点线长度（单位：采样点） |
| `FILL_OPACITY` | `gp.py` | `0.0` | `Opacity` 修改器默认值 |
| `TILING_OPACITY` | `gp.py` | `0.0` | `Tiling Opacity` 修改器默认值 |
| `LINES_OPACITY` | `gp.py` | `0.0` | `Lines Opacity` 修改器默认值 |
| `LAYER_PASS_FILLS/TILING/LINES` | `gp.py` | `1 / 2 / 3` | 图层 pass 过滤值 |
| `MATERIAL_INDEX_FILLS/TILING/LINES` | `gp.py` | `0 / 1 / 2` | 笔划材质索引（仅备查与过滤） |

面板默认值在 `props.py`：三个描边宽度均为 `0.001`，三个不透明度均为 `0`。

---

## 模块划分

| 文件 | 职责 |
|---|---|
| `geom.py` | 点 / 3×3 仿射矩阵 / `fillBox` / `groupTiles` |
| `design.py` | 构造线晶格、瓷砖生长、签名匹配、填充查询（纯 Python，不依赖 `bpy`） |
| `fillers.json` / `fillers.py` | 图案库数据与惰性加载 |
| `rng.py` | Mulberry32，保证同 seed 可复现 |
| `scene.py` | 与后端无关的 `Shape` 列表（点列 / 填充色 / 描边色 / 闭合），`build_layers()` |
| `gp.py` | Grease Pencil v3 写入、图层拆分、材质索引、修改器与宽度/不透明度回写 |
| `props.py` / `ui.py` / `ops.py` | 场景属性、侧栏面板、生成与导出算子 |

`scene.build_shapes(mode=...)` 仍保留（单视图、旧 API），当前生成走 `build_layers()`。

---

## 已知行为与取舍

- **构造线长度**：原 p5 靠画布裁剪，这里改为裁到拼贴区凸包，因此比原版短，但不再溢出主体；
- **近似重合的填充块**：算法固有（相邻瓷砖匹配同一图案簇），现在由 `_dedupe()` 去掉；
- **Shimmer 与图层数**：按精确色分组会得到上百层，现按基色量化分组，颜色仍保留抖动；
- **虚线疏密**：`dash` / `gap` 是采样点个数，改点距会同时改变虚线密度；
- **半透明**：GP 材质的 alpha 在 Solid 着色模式下不生效，所以改用 `Opacity` 修改器，
  现在 Solid / Material Preview 都能显示；
- **SVG 导出**：导出的是"视口看到的样子"，导出前会临时切俯视正交；`hide_stroke=True`
  的笔划会被导出器整条跳过，因此插件始终让描边可见、靠半径控制粗细。

---

## 性能

- `density 10`：约 70–180 ms；`density 20`：约 170–350 ms（含凸包与去重开销）
- 坐标、半径、颜色全部走 `foreach_set` 批量写入，不逐点调用 API
- 最密集配置约 6800 条笔划 / 4.7 万点

---

## 调试建议

- 只看构造线：把 `Fill` / `Tiling` 的不透明度调到 0，`Lines` 调到 1；
- 检查虚线密度：看 `Lines` 图层的点间距是否一致（`length / (points - 1)` 应为常数）；
- 检查重叠：统计填充形状的 `(顶点数, 面积, 质心)` 是否出现重复；
- 检查图层分组：`Fills_*` 每组内应出现多种相近色（shimmer 抖动），但组数应等于基色数量。
