# 相场晶枝生长（Sim Dendrite）

Warren 相场模型的晶枝（dendrite）生长**实时模拟** + 3D 高度场（扩展 id `sim_dendrite`，
Blender 5.2.2 实测）。一个过冷的液体里放一颗晶核，逐帧推进相场与温度场，六重对称的
枝晶就长出来；网格顶点的 `z = φ · 高度缩放`，剩下的 `phi` / `dT` / `interface` / `color`
属性随帧刷新。

计算后端两档，代码同一套方程：

| 后端 | 依赖 | 250² × 50 子步 | 说明 |
|---|---|---|---|
| **Taichi GPU**（默认「自动」时优先） | 需要在 Blender 的 Python 里 `import taichi` | **5.5 ms**（求解）/ **约 11 ms**（整帧） | 参考实现 `.ref/dendrite_growth.py` 的 kernel，实时档 |
| **NumPy**（回退） | 只要 Blender 自带 numpy | 79 ms（求解） | 同方程矢量化版，实测与 GPU 结果差 6e-8；小网格可用 |

## 快速开始

1. 仓库已注册为本地扩展仓库（见根目录 `README.md`），启用 **Sim Dendrite**；
2. 3D 视图侧栏出现 **Dendrite** 面板（F3 里也有 `sim_dendrite.*` 算子）；
3. 点「构建」——建 250×250 的高度场网格、求解器、斜俯视正交相机，画面停在初始晶核；
4. 播放（或点「+1 帧」）即实时演算：6 条主枝带着二次枝晶长出来；
5. 出图点「场景预设」：1600×1000 正交相机、Standard 色彩、世界背景铺项目色板的
   `BACKGROUND`。

算子：`sim_dendrite.build`、`sim_dendrite.reset`（只重铺初始场，不重建网格）、
`sim_dendrite.step`（时间轴 +1 帧）、`sim_dendrite.setup_scene`、
`sim_dendrite.fix_viewport`、`sim_dendrite.delete`。

颜色只写在 `color` 顶点属性里，而 Solid 视图默认的「着色颜色 = Material」读的是材质单色，
看不出相场色 —— 所以「构建」「场景预设」会顺手把 Solid 视图切成 **Attribute**。播放中要是
又切回 Material，面板会亮一条红色提示，点 `sim_dendrite.fix_viewport` 即可；材质预览 /
渲染模式本来就看得到颜色，不动它们。

想让枝晶长得快，**只调「子步」**：每帧推进的物理步数就是它。缺省 50（24 fps 下
1200 步/秒，约 5 秒长到画面的一小半）；GPU 上拉到 200 仍不掉帧，NumPy 后端建议 10~25。
实测（250²、τ=3e-4、dx=0.025）固相占比 vs 步数：

| 步数 | 3000 | 6000 | 12000 |
|---|---|---|---|
| 固相占比 | 5.0% | 12.2% | 34.1% |

## 面板参数

| 分组 | 参数 | 说明 |
|---|---|---|
| 初始条件 | 种子网格 | 可选参考网格：它的顶点（世界空间 XY 投影，归一化到 `[-1,1]`）各放一个晶核，用来做多晶 / 沿形状生长；留空只有中心一个核 |
| | 种子半径 / 初始过冷度 | 晶核半径（网格格数，5 = 5 格）与液体的 ΔT = T − T_m（< 0，越负长得越快） |
| 网格 / 精度 | NX / NY / DX / DY | 250² × 0.025 = 6.25 见方的场域。改分辨率会重建网格 |
| | 后端 | 「自动」= 有 taichi 就用 GPU，取不到直接回退 NumPy（原因写在读数里） |
| | 子步 | 每帧推进几个物理步 —— **唯一的"生长速度"旋钮** |
| 物理 | 对称次数 N | 6 = 雪花，4 = 十字 / 立方，3 = 三叉 |
| | 各向异性强度 c | 0 = 各向同性（长成圆团），0.01~0.03 才有明显枝晶 |
| | 热扩散 DT / 时间步 dt | 显式欧拉，`dt` 改大前先看画面有没有爆成噪点 |
| | 界面 / 耦合（折叠） | α（界面尺度）、晶向偏角、τ（弛豫时间）、κ1、κ2 |
| 外观 | 高度 Z 缩放 | `z = φ · 缩放`：0 = 纯平面着色 |
| | 配色 | 相场（绢白液相 → 石青固相 + 赭石/朱砂界面高光）/ 过冷度（热力图） |

三档参数的生效方式不同：

- **物理 / 子步**：下一帧就生效，不重建；
- **外观**：当场重刷当前帧（不推进物理）；
- **网格 / 初始条件 / 后端**：进签名，下一帧由 handler 自动重建（也可以手动点「构建」）。

右下角读数：当前帧、固相占比、ΔT 区间、后端名与回退原因、每帧耗时（指数滑动平均）。

## 物理模型

相场 `φ`（0 = 液相，1 = 固相）与无量纲过冷度 `ΔT` 耦合（Warren 模型）：

```
τ ∂φ/∂t = ∇·(D ∇φ) + φ(1−φ)·[φ − 1/2 − (κ1/π)·arctan(κ2·ΔT)]
∂ΔT/∂t  = D_T·∇²ΔT + ∂φ/∂t
```

各向异性写在扩散张量里（界面刚度对法向角取 N 次对称）：

```
ψ  = θ0 + arctan2(∂_yφ, ∂_xφ)          # 界面法向角
β  = cos(N·ψ)                          # 用 tan(Nψ/2) 的有理形式实现
M  = α²·(1 + c·β)
D  = M · [[1+cβ, −∂_ψβ], [∂_ψβ, 1+cβ]]
```

离散化与参考实现逐式一致：

- **面通量**：每个子步在 `(nx+1)×ny` 与 `nx×(ny+1)` 两个面网格上各算一次各向异性通量，
  再取散度（GPU 在 `evolve_kernel` 里逐格算四个面，NumPy 在切片上一次算完整个面网格）；
- **边界**：零通量 Neumann，等价于把越界索引钳到域内（NumPy 用 edge padding）；
- **潜热**：`∂ΔT/∂t` 里的 `∂φ/∂t` 就是结晶放热 —— 界面附近 ΔT 被拉回 0，所以晶体内部是"热"的；
- **显式欧拉**：`dt` 受热扩散限制（`D_T·dt/dx² < 0.25`，缺省 0.072，还有 3.5 倍余量）。

> **τ 不要往小改**：界面推进速度 ∝ 1/τ，但 τ=1e-4 时界面会被数值振荡糊掉（实测 59% 的格子
> 落在 0.05~0.95 之间，画面是一团橙色渐变；τ=3e-4 只有 4.7%）。要快要加「子步」。

实测（250²、缺省参数、4000 步）：半径角向谱的 6 次谐波是基频的 33 倍（4 次谐波 2.2 倍），
也就是真正的六重对称 —— 与 `N=6` 一致；`N=4` 时 4 次谐波主导。

## 数据接口

网格（`Dendrite Growth`）上的属性，几何节点 / 着色器可以直接按名字取：

| 属性 | 类型 / 域 | 含义 |
|---|---|---|
| `phi` | FLOAT / POINT | 相场 0（液相）~ 1（固相） |
| `dT` | FLOAT / POINT | 过冷度 ΔT = T − T_m |
| `interface` | FLOAT / POINT | `4φ(1−φ)`，界面指示函数（最中间为 1） |
| `color` | FLOAT_COLOR / POINT | 线性 RGBA，Solid 的 Attribute 着色与渲染共用 |

顶点编号是 `i*ny + j`（C 序），与求解器字段的展平顺序一致，所以相场数组原样写进属性、
不需要重排。颜色只存一份：材质里 `Color Attribute → Base Color`，没有第二套色。
节点的属性名**留空**（不设置），由渲染器自动取活跃色属性（`active_color_index` /
`render_color_index`）：填上 `color` / `vertex_color` 会被 Cycles 当成它自己的标准顶点色
名，取到空值、渲染成纯黑（EEVEE 不受影响，只测 EEVEE 发现不了）。

## 模块

```
solver.py        相场 + 热扩散求解器（纯 NumPy）+ 后端工厂 create()   （不 import bpy）
taichi_solver.py 同一套方程的 Taichi GPU kernel（可选后端）
colour.py        相场 / 过冷度 → 顶点色（只用项目色板）               （不 import bpy）
mesh.py          nx×ny 高度场网格、顶点属性、材质、参考网格种子取样
sim.py           frame_change_pre handler：推进相场 → 写顶点与属性
scene.py         世界底色 / 斜俯视正交相机 / 渲染设置
viewport.py      Solid 视图的着色颜色检查（颜色写在属性里，Material 着色看不出相场）
props.py         scene.sim_dendrite 参数组
ops.py           构建、重置、步进、场景预设、视口修正、删除
ui.py            View3D > 侧栏 > Dendrite
```

`solver.py` / `colour.py` 可以脱离 Blender 单独跑（系统 Python + numpy）：

```powershell
python -c "from sim_dendrite import solver; f=solver.create(96,96,0.025,0.025,backend='NUMPY'); f.initialize(); f.step(1000); print(f.phi_flat().mean())"
```

## Taichi 的装法（踩过的坑）

`taichi_solver` 只在 `import taichi` 成功时才可用，其余情况由 `solver.create` 自动回退
NumPy（面板读数里会写原因）。正常按根 README 的启动方式（带 `BLENDER_USER_RESOURCES`
环境变量）运行时，`<BLENDER_USER_RESOURCES>/scripts/modules` 就在 `sys.path` 上，
`taichi` 直接可用：

```
$ blender --background --python-expr "import taichi; print(taichi.__file__)"
Taichi ... version 1.7.4 ...
C:\git\blender\5.2\scripts\modules\taichi\__init__.py
```

**但 `--factory-startup` 例外**：它不读用户偏好，连带这个目录也不会进 `sys.path`
（`--background --factory-startup` 下打印 `sys.path` 可复现）—— 测试/CI 里这么启动就会出现
「明明装了 taichi 却回退 NumPy」。所以 `taichi_solver` 在 `import taichi` 失败时会按
`BLENDER_USER_RESOURCES` / `%APPDATA%\Blender Foundation\Blender\*\scripts\modules`
兜底补一次路径；另一条永远可用的路是把库装进 Blender 官方支持的扩展依赖目录
`<BLENDER_USER_RESOURCES>/extensions/.local/lib/python3.13/site-packages`。

同时注意 `taichi_solver.py` **不能**有 `from __future__ import annotations`：Taichi 直接读
`__annotations__` 判断 kernel 参数类型，PEP 563 的字符串注解会报
`TaichiSyntaxError: Invalid type annotation`。

## 与参考实现的差别

参考实现 `.ref/dendrite_growth.py` + `.ref/blender_dendrite_growth.py`（独立脚本、Taichi GUI
预览、几何节点着色）验证过的物理与配色照搬，差别在工程结构：

| | 参考脚本 | 本扩展 |
|---|---|---|
| 组织方式 | 两个独立脚本（solver / Blender 集成） | 扩展包：算法层与 Blender 层分开，可单独测试 |
| 后端 | 只有 Taichi | Taichi GPU 优先，**可回退 NumPy**（同方程，实测差 6e-8） |
| 结果落到场景 | 网格 + 几何节点 Store Named Attribute 上色 | 网格 + 直接写 `color` 顶点属性 + 材质，不依赖几何节点 |
| 种子 | 参考网格顶点 → 归一化点位 | 同左（物体模式取求值网格，编辑模式取 BMesh） |
| 参数 | 场景属性组 | 三档语义（物理 / 外观 / 重建）分明，并带读数 |

## 局限与下一步

- **没有烘焙**：动画靠 `frame_change_pre` 实时算，渲染动画时帧要按顺序走（Blender 默认如此），
  **回退会重置**整个模拟（`target < 当前帧` 即从初始核重算）；
- 网格是平面高度场，枝晶再长也是在 2D 场里长；要立体的枝晶得做 3D 相场（开销与内存 ×N）；
- 显式欧拉 + 固定 `dt`：改大 `dt` 会先热扩散失稳、再相场失稳，画面表现是"糊成一片橙"；
- 边界是零通量 Neumann，没有周期性；正方场域四角会先"憋热"，长时间后枝晶会贴着四边收口；
- 编辑模式下 handler 会跳过（顶点由 BMesh 掌管，`foreach_set` 会被丢掉），退出编辑即恢复。
