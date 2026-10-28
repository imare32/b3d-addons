# 反应扩散（Sim Reaction Diffusion）

Gray-Scott 反应扩散模型的**实时模拟** + 3D 高度场（扩展 id `sim_reaction_diffusion`，
Blender 5.2.2 实测）。两种化学物质在网格上互相催化与消耗，`F`（补料）与 `k`（移除）
决定长出来的是迷宫、珊瑚纹、斑点还是蠕虫；网格顶点的 `z = v · 高度缩放`，`u` / `v` /
`edge` / `color` 属性随帧刷新。

计算后端两档，代码同一套方程：

| 后端 | 依赖 | 200² 每子步 | 缺省 64 子步的求解耗时 |
|---|---|---|---|
| **Taichi GPU**（默认「自动」时优先） | 需要在 Blender 的 Python 里 `import taichi` | **0.105 ms** | **6.7 ms/帧**（参考实现的 kernel，实时档） |
| **NumPy**（回退） | 只要 Blender 自带 numpy | 0.21 ms | 13.4 ms/帧（同方程矢量化版，实测与 GPU 结果差 2e-6） |

整帧还要加上网格写入与配色（200² 约 6 ms、400² 约 13 ms）。实测（Taichi，Blender
5.2.2）：200² × 缺省 64 子步 **13.8 ms/帧**，200² × 24 子步 8.9 ms/帧，400² × 24 子步
20.2 ms/帧 —— 24 fps 下都有余量。

## 快速开始

1. 仓库已注册为本地扩展仓库（见根目录 `README.md`），启用 **Sim Reaction Diffusion**；
2. 3D 视图侧栏出现 **Reaction** 面板（F3 里也有 `sim_reaction_diffusion.*` 算子）；
3. 点「构建」——建 200×200 的高度场网格、求解器、斜俯视正交相机，画面停在初始种子；
4. 播放（或点「+1 帧」）即实时演算：种子向四周长出图案；
5. 出图点「场景预设」：1600×1000 正交相机、Standard 色彩、世界背景铺项目色板的
   `BACKGROUND`。

算子：`sim_reaction_diffusion.build`、`sim_reaction_diffusion.reset`（只重铺初始场，
不重建网格）、`sim_reaction_diffusion.step`（时间轴 +1 帧）、
`sim_reaction_diffusion.inject`（随机位置注一颗种子）、
`sim_reaction_diffusion.setup_scene`、`sim_reaction_diffusion.fix_viewport`、
`sim_reaction_diffusion.delete`。

颜色只写在 `color` 顶点属性里，而 Solid 视图默认的「着色颜色 = Material」读的是材质
单色，看不出图案色 —— 所以「构建」「场景预设」会顺手把 Solid 视图切成 **Attribute**。
播放中要是又切回 Material，面板会亮一条红色提示，点
`sim_reaction_diffusion.fix_viewport` 即可；材质预览 / 渲染模式本来就看得到颜色，不动
它们。

想让图案长得快，**只调「子步」**：每帧推进的物理步数就是它。缺省 64（24 fps 下
1536 步/秒，约 3 秒走完 5000 步）；GPU 上拉到 150~240 仍不掉帧，NumPy 后端建议
32~64。
实测（200²、随机撒点、64 子步）成熟过程：

| 帧 | 10 | 20 | 40 | 70 |
|---|---|---|---|---|
| v 覆盖（六边形斑点） | 6.6% | 12.2% | 24.7% | 45.3% |

## 面板参数

| 分组 | 参数 | 说明 |
|---|---|---|
| 图案预设 | 8 套 | 换预设**只改 (F, k)**，当前场就地变形，不重置（见下表） |
| 初始条件 | 种子模式 | 中心方核 / 五处撒种 / 规则阵列 / 随机撒点：只决定起点，形态由 (F, k) 决定 |
| | 种子半径 | 核的半径（网格格数）。5 是实测最稳的值：太小会被扩散吃掉，太大（≥10）遇到密集撒种时并成一大团反而会整片熄灭 |
| | 随机种子 | 「随机撒点」用的随机种子，同一个值复现同一批点位 |
| | 种子网格 | 可选参考网格：它的顶点（世界空间 XY 投影，归一化到 `[-1,1]`）各放一颗种子 |
| 网格 / 精度 | NX / NY | 200² 就能画出细图案；GPU 上 400 也实时，NumPy 后端建议 ≤ 250 |
| | 场域宽度 / 高度 | 网格的物理尺寸（Blender 单位），只影响显示与取景；动力学在网格单位里算 |
| | 后端 | 「自动」= 有 taichi 就用 GPU，取不到直接回退 NumPy（原因写在读数里） |
| | 子步 | 每帧推进几个物理步 —— **唯一的"生长速度"旋钮** |
| 物理 | F / k | 补料率与移除率，决定图案形态（就是参数图上的坐标） |
| | 扩散 / 时间步（折叠） | `Du` / `Dv` / `dt`：缺省 1.0 / 0.5 / 1.0，与所有预设同一套尺度 |
| 外观 | 高度 Z 缩放 | `z = v · 缩放`：0 = 纯平面着色 |
| | 配色 | 图案（画布底色 → 石青 → 赭石 → 朱砂）/ 水墨（绢白到墨玄）/ 轮廓（`\|∇v\|` 描边） |
| | 对比度 | 配色归一化的倍数，图案"更实"或更淡 |

三档参数的生效方式不同：

- **物理 / 子步 / 预设**：下一帧就生效，不重建、不重置；
- **外观**：当场重刷当前帧（不推进物理）；
- **网格 / 初始条件 / 后端**：进签名，下一帧由 handler 自动重建（也可以手动点「构建」）。

右下角读数：当前帧、v 覆盖比例、v 均值与峰值、后端名与回退原因、每帧耗时（指数
滑动平均）。

### 8 套预设

参数都在 `Du = 1` / `Dv = 0.5` / `dt = 1` 这套标准尺度下，所以 (F, k) 直接落在经典
Gray-Scott 参数图上；名称按**实测形态**取。

| 预设 | F | k | 形态 |
|---|---|---|---|
| 迷宫通道 | 0.038 | 0.062 | 细密的迷宫通道，边界缓慢漂移、偶尔断开 |
| 珊瑚指纹 | 0.062 | 0.062 | 更粗更密的指纹条纹，很快铺满全域 |
| 六边形斑点 | 0.030 | 0.062 | 排列成六方点阵的圆斑 |
| 细胞分裂 | 0.032 | 0.064 | 稀疏圆点：长大、挨上了就合并或分裂 |
| 波前火焰 | 0.020 | 0.052 | 向外推进的波前，撞在一起就湮灭 |
| 粗大蠕虫 | 0.026 | 0.055 | 又粗又慢的蠕虫斑块 |
| 缠绕蠕虫 | 0.046 | 0.063 | 始终不稳定的缠绕丝 |
| 实底孔洞 | 0.050 | 0.060 | 负图案：v 几乎铺满全域，只剩一片孔洞 |

实测（200²、种子半径 5、4000 步）四种种子模式下的 v 覆盖率 —— 加粗的两格是唯一会
「整片熄灭」的组合：

| 预设 | 中心方核 | 五处撒种 | 规则阵列 | 随机撒点 |
|---|---|---|---|---|
| 迷宫通道 | 22.0% | 68.5% | 69.6% | 59.6% |
| 珊瑚指纹 | 13.3% | 68.5% | 77.8% | 77.7% |
| 六边形斑点 | 24.8% | 52.6% | 53.1% | 40.5% |
| 细胞分裂 | 9.6% | 39.6% | 9.6% | 11.5% |
| 波前火焰 | 67.5% | 74.0% | **0.0%** | 67.5% |
| 粗大蠕虫 | 82.5% | 82.3% | 84.2% | 82.8% |
| 缠绕蠕虫 | 13.9% | 62.9% | 69.8% | 57.1% |
| 实底孔洞 | 92.8% | 100.0% | 100.0% | 99.9% |

换预设时图案可能先「退」一阵再长回来（`缠绕蠕虫` / `实底孔洞` 尤其明显：v 会先塌到几
个百分点，几十帧后铺满），这是同一套方程换了系数的正常过渡，不是坏了。

「波前火焰」靠近存活边界（v 的均匀态在这里是稳定的），撒点方式不合适时**整片熄灭**
是模型本身的行为，不是数值问题：面板上覆盖率会变成 0%，点「注入种子」或换回中心方核
就能救回来。其余预设都稳。

## 物理模型

两种物质 `u`（基质）与 `v`（活化剂）在二维网格上互相催化与消耗：

```
∂u/∂t = Du·∇²u − u·v² + F·(1 − u)
∂v/∂t = Dv·∇²v + u·v² − (F + k)·v
```

- `u·v²` 是自催化反应：v 消耗 u、同时生成更多 v；
- `F` 持续补 u、`F + k` 持续移除 v —— 两个系数的比值决定图案是斑点（k 大）、条纹
  （k 中）、还是一潭死水（k 太大，v 撑不住）；
- 离散化与参考实现逐式一致：**九点各向同性拉普拉斯**（十字 0.2、对角 0.05、中心 −1，
  和恒为 0）、**周期性边界**（环面）、**显式欧拉**、每步夹到 `[0, 1]`。

九点 stencil 的最负本征值约 `−1.6`，所以显式欧拉要求 `dt·Du ≤ 2/1.6 ≈ 1.25`；缺省
`dt = 1`、`Du = 1` 还有余量。超过会先出现棋盘状高频噪点，再糊成一片。

> **加 dt 不会让图案长得更快**，只会失稳：生长速度请用「子步」。图案成型要几千步
> （见「快速开始」的表）。

## 数据接口

网格（`Reaction Diffusion`）上的属性，几何节点 / 着色器可以直接按名字取：

| 属性 | 类型 / 域 | 含义 |
|---|---|---|
| `u` | FLOAT / POINT | 基质浓度 0~1（图案内部低、背景高） |
| `v` | FLOAT / POINT | 活化剂浓度 0~1（图案本体） |
| `edge` | FLOAT / POINT | `\|∇v\|`，图案轮廓强度 |
| `color` | FLOAT_COLOR / POINT | 线性 RGBA，Solid 的 Attribute 着色与渲染共用 |

顶点编号是 `i*ny + j`（C 序），与求解器字段的展平顺序一致，所以浓度数组原样写进属性、
不需要重排。颜色只存一份：材质里 `Color Attribute → Base Color`，而节点的
`layer_name` **留空**，由渲染器自动取活跃色属性（见下一条），没有第二套色。

> **用 Cycles 渲染的一定要注意**：`Color Attribute` 节点的 `layer_name` 必须留空。填
> `color` 或 `vertex_color` 会被 Cycles 当成它自己的标准顶点色名，取到空值、渲染成纯黑
> （EEVEE 不受影响，所以只在 EEVEE 里测发现不了）。见下面「踩过的坑」。

## 模块

```
solver.py        Gray-Scott 求解器（纯 NumPy）+ 预设 + 后端工厂 create()   （不 import bpy）
taichi_solver.py 同一套方程的 Taichi GPU kernel（可选后端）
colour.py        浓度 / |∇v| → 顶点色（只用项目色板）                      （不 import bpy）
mesh.py          nx×ny 高度场网格、顶点属性、材质、参考网格种子取样
sim.py           frame_change_pre handler：推进浓度场 → 写顶点与属性
scene.py         世界底色 / 斜俯视正交相机 / 渲染设置
viewport.py      Solid 视图的着色颜色检查（颜色写在属性里，Material 着色看不出图案）
props.py         scene.sim_reaction_diffusion 参数组
ops.py           构建、重置、步进、注种、场景预设、视口修正、删除
ui.py            View3D > 侧栏 > Reaction
```

`solver.py` / `colour.py` 可以脱离 Blender 单独跑（系统 Python + numpy）：

```powershell
python -c "from sim_reaction_diffusion import solver; s=solver.create(200,200,backend='NUMPY',params=solver.preset_params('MAZE')); s.initialize('NOISE'); s.step(4000); print(s.v_flat().mean())"
```

## 踩过的坑

### 本扩展新发现的两条

**1. 用 Cycles 渲染时，材质里的 `Color Attribute` 节点必须把属性名留空。**
网格属性就叫 `color`（数据接口正常、EEVEE 正常、Solid 的 Attribute 着色正常），但只要在
材质节点的 `layer_name` 里**填**上 `color` 或 `vertex_color`，Cycles 就会把它当成自己的
标准顶点色名，取到空值、渲染成纯黑。留空则由渲染器自动采用**活跃颜色属性**
（`mesh.color_attributes.active_color_index` / `render_color_index`），两个引擎都对。

实测（5.2.2，同一网格、同一份数据，只换 `layer_name`）：

| `layer_name` | Cycles | EEVEE |
|---|---|---|
| `color` | 纯黑 | 正常 |
| `vertex_color` | 纯黑 | 正常 |
| 空（回退活跃色属性） | 正常 | 正常 |
| `col` / `Col` / `vcol` / `colour` / `mycolor` / `vertex_col` / `vertex_colors` | 正常 | 正常 |

所以：属性**名字**不必改，要改的是**节点里那一栏留空**。名字里带 `color` 也不一定出事
（`mycolor`、`colour`、`vertex_colors` 都没事），出事的只有 `color` 与 `vertex_color` 这
两个**精确**的保留名，而且只在 Cycles 里 —— **只在 EEVEE 里测是测不出来的**（本扩展最
初就是只拿 EEVEE 测过、还写错过这条结论）。

本扩展的 `ensure_attributes` 会把 `color` 设成活跃色 / 渲染色，`ensure_material` 建节点时
`layer_name` 留空。`sim_dendrite` / `sim_jacobi` / `sim_game_of_life` 原来写的是
`layer_name = "color"`，在 **Cycles** 下表面是材质单色（实测 `sim_dendrite` 构建后渲染：
整块平面没有任何顶点色，把那一栏清空后图案色立刻出现，其余什么都不同）—— 这三处已一并
改成留空。

**2. 种子不要太大也不要太碎。** 半径 ≥10 的种子在「规则阵列 / 随机撒点」下容易并成一
大团，而一大团 v 的中间耗尽了 u，会整片熄灭（这是模型的真实行为）。半径 5 在四种模式
下都稳（数据见上面的预设表）。

### 仓库既有的 Taichi 约定（照做即可，不用重新踩）

这两条在仓库里早有记录，**本扩展没有新增结论，只是照做**，记在这里是给下一个写
Taichi 后端的人省一步：

**3. `taichi_solver.py` 不能有 `from __future__ import annotations`。** Taichi 直接读
`__annotations__` 判断 kernel 参数类型，PEP 563 的字符串注解会报
`TaichiSyntaxError: Invalid type annotation (argument 0) of Taichi kernel: int`。
出处：`sim_dendrite/README.md`、`sim_jacobi/README.md` 的「Taichi 的装法（踩过的坑）」，
以及根 README「插件开发约定」。

**4. `--factory-startup` 会让 taichi 找不到。** 它不读用户偏好，连带
`<BLENDER_USER_RESOURCES>/scripts/modules` 也不会进 `sys.path` —— 测试 / CI 里这么启动
就会出现「明明装了 taichi 却回退 NumPy」。出处：根 README「Python 环境」一节。
本扩展的 `taichi_solver` 与 `sim_dendrite` 同一套兜底（`import taichi` 失败时按
`BLENDER_USER_RESOURCES` / `%APPDATA%\Blender Foundation\Blender\*\scripts\modules`
补一次路径），实测（`--background --factory-startup`）：

```
scripts/modules on sys.path: ['...\\5.2\\scripts\\modules', ...]   # 只有便携版自带的
import taichi FAILED: No module named 'taichi'
after fallback: cuda  C:\git\blender\5.2\scripts\modules\taichi\__init__.py
```

## 与参考实现的差别

参考实现 `ga2/common/reaction_diffusion.py` + `ga2/scripts/blender_reaction_diffusion.py`
（独立脚本、Taichi GPU、UV 采样贴到球/猴头/圆环等 3D 表面上）验证过的方程与配色照搬，
差别在工程结构与预设标定：

| | 参考脚本 | 本扩展 |
|---|---|---|
| 组织方式 | 两个独立脚本（solver / Blender 集成） | 扩展包：算法层与 Blender 层分开，可单独测试 |
| 后端 | 只有 Taichi | Taichi GPU 优先，**可回退 NumPy**（同方程，实测差 2e-6） |
| 结果落到场景 | 网格 + `substance_b` 顶点属性 + 4 套手写材质 | 网格 + `u`/`v`/`edge`/`col` 四个属性 + 3 种配色（全部由项目色板推出） |
| 载体 | 平面 / 球 / 猴头 / 圆环 / 圆柱 / 自定义网格（按 UV 采样） | 平面高度场（`z = v·缩放`），与 `sim_dendrite` 一致 |
| 预设命名 | 按印象命名 | 按**实测形态**命名并修正取值（见下） |
| 参数语义 | 场景属性组 | 三档语义（物理 / 外观 / 重建）分明，并带读数 |

预设的取值有几处与参考实现不同，因为参考里的名字与实测形态对不上（同一套方程、同一套
九点 stencil、同样的 `dt = 1`，在本扩展里逐个跑 12000 步确认）：

| 参考取值 | 参考名 | 实测形态 | 本扩展的处理 |
|---|---|---|---|
| F=0.030, k=0.062 | `MAZE` | 六边形斑点阵列 | 叫「六边形斑点」(`SPOTS`)，取值不变 |
| F=0.035, k=0.065 | `SPOTS` | 稀疏大斑，和 `CELLS` 几乎分不出来 | 舍弃 |
| F=0.060, k=0.062 | `CORAL` | 迷宫 / 指纹条纹 | 保留「珊瑚指纹」，F 微调到 0.062（条纹更密） |
| F=0.036, k=0.064 | `CELLS` | 密点阵 | 保留「细胞分裂」，F 微调到 0.032（点更稀、分裂看得清） |
| F=0.018, k=0.051 | `SPIRALS` | 波前 / 火焰条纹 | 改叫「波前火焰」(`WAVES`)，取值调到 0.020/0.052 |
| F=0.046, k=0.063 | `CHAOS` | 缠绕丝 | 保留「缠绕蠕虫」 |
| —— | —— | —— | 新增「迷宫通道」0.038/0.062、「粗大蠕虫」0.026/0.055、「实底孔洞」0.050/0.060 |

## 局限与下一步

- **没有烘焙**：动画靠 `frame_change_pre` 实时算，渲染动画时帧要按顺序走（Blender 默认
  如此），**回退会重置**整个模拟（`target < 当前帧` 即从初始种子重算）；
- 载体是平面高度场：要把图案贴到球 / 猴头上得按 UV 采样并沿法线位移（参考脚本的做法），
  本扩展没做；
- 显式欧拉 + 固定 `dt`：`dt·Du > 1.25` 会失稳，画面表现是棋盘噪点 → 糊成一片；
- 边界是周期性（环面），所以图案会跨过边界接续；要"有墙"的效果得改成零通量边界；
- 场没有缓存，秒级回拖等于从头算；长动画建议先按顺序播到目标帧再渲染；
- 编辑模式下 handler 会跳过（顶点由 BMesh 掌管，`foreach_set` 会被丢掉），退出编辑即恢复。
