# Jacobi theta 除数环面（Sim Jacobi）

环面上 Jacobi theta 除数 **实时模拟** + 顶点着色（扩展 id `sim_jacobi`，Blender 5.2.2 实测）。

```
Θ(z, t) = Π_j θ1(z − c_j(t) | τ)
```

环面 `T²` 上放 8 个（最多 64 个）**会互相弹开的小球**当零点，它们的 `(u, v)` 位置就是除数
的零点位置；每帧在 150×100 的网格上算一遍 Θ，把

* 零点光晕 `Σ exp(−120·d²)`、
* 复相角 `arg Θ`、
* 势井 `log|Θ|`、
* 高频相位干涉格纹

写进顶点属性，环面顶点按 `r = R_major + r_minor + h` 变形（零点凹陷 + 相位涟漪），
颜色直接写在 `color` 顶点属性里（项目色板，转线性）。

计算后端两档，公式同一套：

| 后端 | 依赖 | 150×100 / 8 零点 | 64 零点 | 说明 |
|---|---|---|---|---|
| **Taichi GPU**（默认「自动」时优先） | 需要在 Blender 的 Python 里 `import taichi` | **3.2 ms/帧** | 2.2 ms/帧 | 参考实现的 kernel，实时档 |
| **NumPy**（回退） | 只要 Blender 自带 numpy | 3.6 ms/帧 | 26.6 ms/帧 | 同公式矢量化版，实测与 GPU 差 1e-5 |

零点运动本身（碰撞、反弹）两个后端共用同一份 numpy 实现，所以换后端**不会改变运动**。

## 快速开始

1. 仓库已注册为本地扩展仓库（见根目录 `README.md`），启用 **Sim Jacobi**；
2. 3D 视图侧栏出现 **Jacobi** 面板（F3 里也有 `sim_jacobi.*` 算子）；
3. 点「构建」——建环面网格、求解器、零点小球、3/4 视角透视相机，画面停在初始布局；
4. 播放（或点「+1 帧」）即实时演算：零点在环面上滚动、互撞反弹，格纹跟着它们的相位打旋；
5. 出图点「场景预设」：1280×1280 正方形、Standard 色彩、世界背景铺项目色板的
   `BACKGROUND`。

算子：`sim_jacobi.build`、`sim_jacobi.reset`（零点回初始布局，不重建网格）、
`sim_jacobi.step`（时间轴 +1 帧）、`sim_jacobi.setup_scene`、`sim_jacobi.fix_viewport`、
`sim_jacobi.delete`。

颜色只写在 `color` 顶点属性里，而 Solid 视图默认的「着色颜色 = Material」读的是材质单色，
看不出配色 —— 所以「构建」「场景预设」会顺手把 Solid 视图切成 **Attribute**。播放中要是
又切回 Material，面板会亮一条红色提示，点 `sim_jacobi.fix_viewport` 即可；材质预览 /
渲染模式本来就看得到颜色，不动它们。

## 面板参数

| 分组 | 参数 | 说明 |
|---|---|---|
| 网格 / 后端 | NU / NV | 环面网格：u 方向（绕主环）与 v 方向（绕管）。150×100 = 15000 顶点 |
| | 后端 | 「自动」= 有 taichi 就用 GPU，取不到直接回退 NumPy（原因写在读数里） |
| 零点 / 物理 | 零点个数 | 1~64。**个数一变会重铺初值**（与参考实现一致：运动从头开始） |
| | 球半径 | 零点的显示半径，同时是两球的三维接触距离（`2×半径`）——越大越挤、撞得越勤 |
| | 速度 / 子步 | 每个物理步的 `dt = 速度×1.5`；子步 = 每帧走几个物理步。缺省 0.01 / 1，约 110 帧绕环一圈 |
| | 凹陷深度 | 零点下方环面被压出的凹陷；0 = 只留光斑不改几何 |
| | 涟漪幅度 | 相位格纹在环面上的位移幅度：(`phase_grid − 0.5`)×幅度。0 = 环面光滑 |
| 环面 / τ | 主半径 / 管半径 | 环面尺寸（缺省 3.0 / 1.2） |
| | τ 实部 / 虚部 | 模参数 τ：实部让格纹打旋，虚部越接近 0，θ1 的高阶项越强、格纹越碎 |
| 外观 | 配色 | 相位网格（金线菱格）/ 相角（arg Θ 循环取色）/ 势井（log\|Θ\| 热力图） |

两档参数的生效方式不同：

- **物理 / 外观**：下一帧就生效（外观是当场重刷），不重建；零点个数变了会重铺初值；
- **网格 / 后端**：进签名，下一帧由 handler 自动重建（也可以手动点「构建」）。

右下角读数：当前帧、零点个数、后端名与回退原因、每帧耗时（指数滑动平均）。

## 数学与实现

θ1 用 6 项 Fourier 级数（与参考实现同样截断，`q = exp(iπτ)`）：

```
θ1(z | τ) = 2·Σ_{n=0..5} (−1)^n · q^{(n+½)²} · sin((2n+1)·π·z)
```

除数在网格点上的自变量是 `z = du + τ·dv`（`du`/`dv` 是参数域上卷到 `(−0.5, 0.5]` 的
偏移），于是每一格要算 N 次 θ1。三个实现细节值得记下来：

- **可分离快路径**：`sin((2n+1)π(a+b))` 与 `sin(x+iy)` 都能拆成只依赖 `du` 或只依赖 `dv`
  的一维项，所以每项只算 `nu+nv` 个 `sin`/`cos`/`sinh`/`cosh` 再做两次外积，比直接对
  复数数组调 `np.sin` 快一个量级（8 零点：24.9 ms → 2.4 ms）。零点光晕同理，
  `exp(−120(du²+dv²))` 就是两个一维因子的外积。
- **卷绕要跟 Taichi 一致**：numpy 的 `np.round` 是 .5 取偶，`ti.round` 是 .5 远离零；
  在 `u − c` 恰为 ±0.5 的网格线上两边会差 1，而 `θ1(z+1) = −θ1(z)`，整列顶点的相位就
  差 π。`solver.wrap_unit()` 统一成"远离零"，且 `±0.5` 的加法放在 float64 里做（float32
  下 `−0.49999997 − 0.5` 会舍成 `−1.0`，差一个 ulp 的网格线被误判成平局）。
- **零点处相位本就不定**：`|Θ| → 0` 的格点上 `arg Θ` 是噪声，两个后端在那几个点会差任意
  角度（实测 4800 个点里 3 个）。势井与几何不受影响。

零点运动是小球在环面参数域上的弹性碰撞：位置 `(u, v)` 自由滚动，两球在**三维**环面流形
上的距离小于 `2×球半径` 时交换法向动量（恢复系数 0.95）并把重叠推开；每个物理步再切 4 个
亚步防穿透。成对处理用 `np.add.at` 向量化（64 个零点也只有 2016 对），随机速度用固定种子，
所以同一份参数的结果可复现。

## 数据接口

环面对象（`Jacobi Theta Torus`）上的属性，几何节点 / 着色器可以直接按名字取：

| 属性 | 类型 / 域 | 含义 |
|---|---|---|
| `amplitude` | FLOAT / POINT | 零点光晕 `Σ exp(−120·d²)`：零点处 1、远处 0，两个零点挨得近时会叠加超过 1（配色里会夹到 1） |
| `phase` | FLOAT / POINT | `arg Θ`（各零点相角之和，未归一化） |
| `phase_grid` | FLOAT / POINT | 高频相位干涉格纹，[0, 1] |
| `potential` | FLOAT / POINT | `log\|Θ\|`，零点处 → −∞ |
| `color` | FLOAT_COLOR / POINT | 线性 RGBA，Solid 的 Attribute 着色与渲染共用 |

顶点编号是 `i*nv + j`（C 序），与求解器场的展平顺序一致，所以场原样写进属性、不需要重排。
颜色只存一份：材质里 `Color Attribute → Base Color`（另接一路 Emission，强度 0.15，让金线
在渲染里有"发亮"的观感）。节点的属性名**留空**（不设置），由渲染器自动取活跃色属性
（`active_color_index` / `render_color_index`）：填上 `color` / `vertex_color` 会被 Cycles
当成它自己的标准顶点色名，取到空值、渲染成纯黑（EEVEE 不受影响，只测 EEVEE 发现不了）。

零点小球是独立对象（`Jacobi Zero Points`）：每个零点一份二十面体球（细分 1 级 = 42 顶点 /
80 三角面）合并在一个网格里，每帧只改顶点位置。**不用几何节点** —— 球就是普通网格，
导出、渲染、另存都不会缺依赖。

## 模块

```
solver.py        theta 除子 + 零点弹性碰撞（纯 NumPy）+ 后端工厂 create()   （不 import bpy）
taichi_solver.py 同一套公式的 Taichi GPU kernel（可选后端）
colour.py        四张标量场 → 顶点色（只用项目色板）                       （不 import bpy）
mesh.py          nu×nv 环面网格、四张属性 + 顶点色、材质、零点小球网格
sim.py           frame_change_pre handler：推进零点 → 写顶点与属性
scene.py         世界底色 / 3/4 视角透视相机 / 渲染设置
viewport.py      Solid 视图的着色颜色检查（颜色写在属性里，Material 着色看不出来）
props.py         scene.sim_jacobi 参数组
ops.py           构建、重置、步进、场景预设、视口修正、删除
ui.py            View3D > 侧栏 > Jacobi
```

`solver.py` / `colour.py` 可以脱离 Blender 单独跑（系统 Python + numpy）：

```powershell
# θ1 的两条实现互为对照：级数 vs 可分离快路径
python -c "import numpy as np; from sim_jacobi import solver; du=np.array([0.13]); dv=np.array([-0.42]); re,im=solver.theta1_separable(du,dv,0.3,1.2); z=(du[0]+0.3*dv[0])+1j*1.2*dv[0]; print(re[0,0]+1j*im[0,0], solver.theta1(np.array([z]),0.3,1.2)[0])"

# 跑一帧：形状 + 光晕、势井的范围
python -c "from sim_jacobi import solver; s=solver.create(150,100,backend='NUMPY'); s.step(20); out=s.evaluate(); print([x.shape for x in out], float(out[1].max()))"
```

## Taichi 的装法（踩过的坑）

`taichi_solver` 只在 `import taichi` 成功时才可用，其余情况由 `solver.create` 自动回退
NumPy（面板读数里会写原因）。按根 README 的启动方式（带 `BLENDER_USER_RESOURCES`）运行时
`<BLENDER_USER_RESOURCES>/scripts/modules` 就在 `sys.path` 上，`taichi` 直接可用；但
`--factory-startup` 不读用户偏好，那个目录也不会进 `sys.path` —— 所以 `taichi_solver` 在
`import taichi` 失败时会按 `BLENDER_USER_RESOURCES` /
`%APPDATA%\Blender Foundation\Blender\*\scripts\modules` 兜底补一次路径（与
`sim_dendrite` 同一套做法）。

同时注意 `taichi_solver.py` **不能**有 `from __future__ import annotations`：Taichi 直接读
`__annotations__` 判断 kernel 参数类型，PEP 563 的字符串注解会报
`TaichiSyntaxError: Invalid type annotation`。

## 与参考实现的差别

参考实现是 `ga2/common/jacobi_theta.py` + `ga2/scripts/blender_jacobi_theta_torus.py`
（独立脚本、Taichi GUI 预览、几何节点上色）。数学与物理照搬，差别在工程结构：

| | 参考脚本 | 本扩展 |
|---|---|---|
| 组织方式 | 两个独立脚本（solver / Blender 集成） | 扩展包：算法层与 Blender 层分开，可单独测试 |
| 后端 | 只有 Taichi | Taichi GPU 优先，**可回退 NumPy**（同公式，实测差 1e-5） |
| 结果落到场景 | 网格 + 几何节点 Store Named Attribute 上色 + 几何节点 Instance on Points 小球 | 直接写 `color` 顶点属性 + 材质；小球是普通网格对象，不依赖几何节点 |
| 零点排序 | 成对循环逐对处理（顺序相关） | 向量化批量处理，结果与顺序无关；固定种子可复现 |
| 参数 | 场景属性组 | 两档语义（物理 / 重建）分明，并带读数 |

## 局限与下一步

- **没有烘焙**：动画靠 `frame_change_pre` 实时算，渲染动画时帧要按顺序走（Blender 默认如此），
  **回退会重置**零点布局（`target < 当前帧` 即从初始布局重算）；
- 分辨率再往上（比如 400×300）NumPy 后端会明显掉帧，64 零点时更是如此 —— 这时用 Taichi；
- 零点是"参数域上的球"，碰撞距离按三维环面流形算，但**没有**自转/摩擦，只有法向弹性碰撞；
- 网格是 `nu×nv` 的四边形环面，凹陷与涟漪都是沿法向的径向位移，凹陷太深（> 0.5×管半径）
  会自交；
- 编辑模式下 handler 会跳过（顶点由 BMesh 掌管，`foreach_set` 会被丢掉），退出编辑即恢复。
