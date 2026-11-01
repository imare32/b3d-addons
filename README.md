# b3d-addons — Blender 内容创作插件合集

面向内容创作的 Blender 扩展合集。每个子目录是一个独立扩展（extension），仓库整体又注册为一个 Blender
**本地扩展仓库**，因此插件可以逐个启用/禁用，也可以一次注册后全部可用。

支持 Blender 4.2+，当前开发环境为 **Blender 5.2.2 LTS**。

- 插件清单见 [插件一览](#插件一览)（待补充）
- 开发环境与调试流程见 [环境](#shell) 起各节

## 目录结构

临时文件放到 .temp/ 目录下

```text
b3d-addons/
├─ <ext_id>/                 # 一个子目录 = 一个扩展，目录名必须与 manifest 的 id 一致
│  ├─ blender_manifest.toml  # 扩展清单（必填）
│  ├─ __init__.py            # 入口，提供 register() / unregister()
│  ├─ *.py                   # 功能模块
│  └─ README.md              # 可选，单插件说明
├─ _shared/                  # 可选，多个插件共用的 Python 包（链接进 scripts/modules）
├─ .vscode/settings.json     # 可选，开发机配置（见 VSCode 插件开发）
├─ pyproject.toml            # 可选，ruff 格式化 / uv 索引配置
├─ GEN_TECH.md               # 生成式插件技术报告（GP 底座 / 各插件算法 / 交织专题）
├─ SIM_TECH.md               # 模拟类插件技术报告（状态契约 / 各插件算法 / 后端与性能）
├─ .gitignore
└─ README.md
```

> 仓库根目录下的 `README.md`、`pyproject.toml`、`_shared/` 等非扩展内容不会被识别为插件（Blender 只扫描
> 含 `blender_manifest.toml` 的子目录和 `.zip` 包），可放心与其他文件混放。

## Shell

- **PowerShell 7.x** (`pwsh`)

## Blender

- **路径**: `C:\Programs\blender-5.2.2-windows-x64\blender.exe`
- **版本**: 5.2.2 LTS（便携版 zip 安装，`C:\Programs\blender-<版本>-windows-x64\`）

### 环境变量

| 变量 | 值 | 说明 |
|---|---|---|
| `BLENDER_USER_RESOURCES` | `C:\git\blender\5.2` | 用户资源目录（`config/`、`extensions/`、`scripts/`） |
| `PYDEVD_DISABLE_FILE_VALIDATION` | `1` | 禁用 pydevd 文件校验，避免调试时断点失效 |

`BLENDER_USER_RESOURCES` 把用户数据从便携版安装目录里剥离出来，升级/重装 Blender 不会丢插件和配置。
手动启动 Blender 时也要带上它：

```powershell
$env:BLENDER_USER_RESOURCES = "C:\git\blender\5.2"
& "C:\Programs\blender-5.2.2-windows-x64\blender.exe"
```

## 扩展仓库（多扩展）

本仓库的子目录都是扩展，按下面任一方式让 Blender 能加载。

### 方式 A：整仓注册为本地扩展仓库（推荐）

本地仓库的 `directory` 就是源码目录，扩展**在原地加载**，不产生符号链接或拷贝，改完代码重载即可生效。

```powershell
# 注册（一次性；仓库 id = b3d_addons，界面显示名 = b3d-addons）
blender --command extension repo-add b3d_addons --name "b3d-addons" --directory "C:\git\b3d-addons"

# 校验注册结果
blender --command extension repo-list      # 应包含 b3d_addons
blender --command extension list           # 应列出仓库内的各个扩展

# 卸载仓库（不影响源码）
blender --command extension repo-remove b3d_addons
```

要点：

- 仓库 id（`b3d_addons`）决定模块前缀，扩展被启用后的模块名是 `bl_ext.b3d_addons.<ext_id>`。
- 注册后 `Preferences > Get Extensions` 的仓库下拉里能看到它，扩展按普通扩展启用/禁用。
- 加 `--no-prefs` 可只读偏好设置（不改配置文件），适合临时验证或脚本化流程。
- 同一扩展不要同时出现在多个仓库里（例如既在 `b3d_addons` 又在 `vscode_development`），否则会出现重名条目。
- 用这种方式的话，VSCode 里必须把 `blender.addon.extensionsRepository` 也设成 `b3d_addons`：
  模块名是 `bl_ext.<仓库 id>.<目录名>`，不设就会去找 `bl_ext.vscode_development.<ext_id>` 而失败。

### 方式 B：junction 链接到 `vscode_development`（与 b3d 相同）

沿用 VSCode 扩展默认仓库，逐个扩展做目录联接：

| 链接位置 | 目标 |
|---|---|
| `C:\git\blender\5.2\extensions\vscode_development\<ext_id>` | `C:\git\b3d-addons\<ext_id>` |

```powershell
New-Item -ItemType Junction -Path "C:\git\blender\5.2\extensions\vscode_development\<ext_id>" -Target "C:\git\b3d-addons\<ext_id>"
```

此方式下模块名为 `bl_ext.vscode_development.<ext_id>`（仓库目录固定在
`C:\git\blender\5.2\extensions\vscode_development`）。注意 Blender Development 扩展在需要时也会自己建立这类
junction（Windows 下为 junction，Linux 为 symlink），重复建立会被它自动清理。

## Python 环境

- **系统 Python**: `C:\Program Files\Python313\python.exe`（已加入 PATH）
- **捆绑解释器**: `C:\Programs\blender-5.2.2-windows-x64\5.2\python\bin\python.exe`
- **第三方库目标目录**: `C:\git\blender\5.2\scripts\modules`

Blender 启动时会将该目录加入 `sys.path`，因此内嵌 Python 可直接 `import` 已安装的第三方库（当前已含
numpy / scipy / opencv / pillow / matplotlib / taichi / flask / debugpy 等）。

> **注意 `--factory-startup`**：该参数不读用户偏好，`scripts/modules` 也就不会进 `sys.path`
> （带 `BLENDER_USER_RESOURCES` 正常启动时是在的）。测试 / 后台渲染若加了它，扩展就会
> `import taichi` 失败并回退后备实现 —— 排查「装了库却用不上」时先看这个。
> 另一条永远可用的路径是 Blender 官方支持的扩展依赖目录
> `<BLENDER_USER_RESOURCES>/extensions/.local/lib/python3.13/site-packages`（该目录不受此影响）。

**安装第三方库**（不污染 Blender 本体，也不进入 `blender_manifest.toml`）：

```powershell
& "C:\Programs\blender-5.2.2-windows-x64\5.2\python\bin\python.exe" -m pip install <pkg> --target "C:\git\blender\5.2\scripts\modules"
```

**可选：共享本地包**。多个扩展共用的纯 Python 包（如 `_shared`）可同样用 junction 挂进 modules 目录：

```powershell
New-Item -ItemType Junction -Path "C:\git\blender\5.2\scripts\modules\b3d_shared" -Target "C:\git\b3d-addons\_shared"
```

**代码风格**：如需沿用 b3d 的做法，仓库根放 `pyproject.toml`（ruff 配置 + `[tool.uv]` 镜像源）、
`.python-version` 写 `3.13`，用 `uv run ruff format .` 统一格式。

## VSCode 插件开发

使用 VSCode 扩展 **Blender Development**（`jacqueslucke.blender-development`）启动 Blender、附加 Python
调试器并热重载插件。

### 工作区配置（`.vscode/settings.json`）

```json
{
  "blender.executables": [
    {
      "isDefault": true,
      "name": "Blender 5.2.2 LTS",
      "path": "C:\\Programs\\blender-5.2.2-windows-x64\\blender.exe"
    }
  ],
  "blender.environmentVariables": {
    "BLENDER_USER_RESOURCES": "C:\\git\\blender\\5.2",
    "PYDEVD_DISABLE_FILE_VALIDATION": 1
  },
  "blender.addon.extensionsRepository": "b3d_addons",
  "blender.addonFolders": ["./<ext_id_a>", "./<ext_id_b>"],
  "blender.addon.justMyCode": false,
  "blender.addon.reloadOnSave": false
}
```

| 设置 | 作用 |
|---|---|
| `blender.executables` | 可选的多版本 Blender 列表，`isDefault` 指定启动用哪一个 |
| `blender.environmentVariables` | 启动 Blender 前注入的环境变量（就是上文那两个） |
| `blender.addon.extensionsRepository` | 扩展所在仓库 id；默认值 `vscode_development`，用方式 A 时改为 `b3d_addons` |
| `blender.addonFolders` | **多扩展仓库的关键**：要加载的扩展目录列表，相对路径基于工作区根；留空则只用工作区根目录 |
| `blender.addon.sourceDirectory` / `loadDirectory` / `moduleName` | 默认 `auto`：源码目录即加载目录，模块名取目录名（即 `<ext_id>`） |
| `blender.addon.justMyCode` | `false` 时允许步入第三方库代码，便于排查依赖问题 |
| `blender.addon.reloadOnSave` | 保存文件时自动重载插件 |

> **踩过的坑**：`blender.addon.extensionsRepository` 没设成 `b3d_addons` 时，
> `Blender: Start` 会失败并报
> `ModuleNotFoundError: Add-on not loaded: "bl_ext.vscode_development.<ext_id>"`。
> 原因是扩展按 `bl_ext.<extensionsRepository>.<目录名>` 拼模块名：本仓库的扩展已经在
> `b3d_addons` 仓库目录里，扩展不会再去建 junction，于是 `vscode_development` 下根本
> 没有这个模块。改完设置需要 `Blender: Stop` → `Blender: Start`（必要时重载 VSCode
> 窗口）才生效。

### 启动与加载

执行 `Blender: Start` 后，扩展实际运行的命令为：

```text
C:\Programs\blender-5.2.2-windows-x64\blender.exe --python C:\Users\Administrator\.vscode\extensions\jacqueslucke.blender-development-0.0.31\pythonFiles\launch.py
```

待加载的插件通过启动参数 `ADDONS_TO_LOAD` 传给 `launch.py`，多扩展就是数组里的多条：

```json
"ADDONS_TO_LOAD": "[{\"load_dir\":\"C:\\\\git\\\\b3d-addons\\\\<ext_id_a>\",\"module_name\":\"<ext_id_a>\"},{\"load_dir\":\"C:\\\\git\\\\b3d-addons\\\\<ext_id_b>\",\"module_name\":\"<ext_id_b>\"}]"
```

调试器的路径映射由扩展自动生成，通常本地源码与运行代码是同一目录（方式 A 原地加载），无需手填：

```json
"pathMappings": [
  {
    "localRoot": "C:\\git\\b3d-addons\\<ext_id>",
    "remoteRoot": "C:\\git\\b3d-addons\\<ext_id>"
  }
]
```

### 快捷键（`keybindings.json`）

```json
[
  { "key": "f6", "command": "blender.reloadAddons" },
  { "key": "f8", "command": "blender.runScript" }
]
```

| 命令 | 说明 |
|---|---|
| `blender.start` | 启动 Blender 并附加调试器 |
| `blender.stop` | 关闭 Blender |
| `blender.reloadAddons` | 热重载插件（绑定 `F6`） |
| `blender.runScript` | 在 Blender 中运行当前脚本（绑定 `F8`） |
| `blender.newAddon` / `blender.newOperator` | 按模板创建扩展 / 操作符 |

> 也可开启 `blender.addon.reloadOnSave`，保存文件时自动重载。

## 通过 MCP 热重载（与启动方式无关）

`F6` 只在 VSCode 扩展自己启动的 Blender 里有效。改用 MCP（`execute_blender_code`）则无此限制 ——
只要 Blender 正在运行且 MCP 可用，无论它是怎么启动的，都能重载改动后的代码：

```python
import bpy, sys

MODULES = [
    "bl_ext.b3d_addons.<ext_id>",  # 按需列出要重载的扩展
]

for mod in MODULES:
    bpy.ops.preferences.addon_disable(module=mod)
    for name in list(sys.modules):
        if name == mod or name.startswith(mod + "."):
            del sys.modules[name]
    bpy.ops.preferences.addon_enable(module=mod)
```

注意：

- 必须走「禁用 → 清 `sys.modules` → 启用」。直接 `import` 拿到的是旧模块，重复注册会导致类和快捷键重复。
- 模块名前缀取决于扩展所在仓库：方式 A 是 `bl_ext.b3d_addons.`，方式 B 是 `bl_ext.vscode_development.`。
  不确定时可在 Blender 里查当前已启用列表，再决定重载哪些：

```python
print([m for m in bpy.context.preferences.addons.keys() if m.startswith("bl_ext.")])
```

- 这套流程和扩展的 `blender.reloadAddons` 内部实现一致（disable → 删 `sys.modules` → enable），两条路可以混用。
- 若扩展未被启用（只是被 `import`），需退回手动方式逐个模块 `unregister()` → `importlib.reload()` → `register()`。

## 校验与打包

```powershell
# 校验单个扩展的清单与文件完整性（路径可为目录或 zip）
blender --command extension validate "C:\git\b3d-addons\<ext_id>"

# 打包为可分发 zip（产物默认 <id>-<version>.zip；--output-dir 不会自建目录，先建出来）
New-Item -ItemType Directory -Force "C:\git\b3d-addons\dist" | Out-Null
blender --command extension build --source-dir "C:\git\b3d-addons\<ext_id>" --output-dir "C:\git\b3d-addons\dist"
```

相关子命令：`validate`（校验）、`build`（打包，可用 `--split-platforms` 按平台拆分）、`install`
（安装仓库内包）、`install-file`（从文件安装）、`remove`、`sync` / `update`（远程仓库）、`server-generate`
（生成本地仓库索引）。

日常开发不需要打包：源码目录就是加载目录，改完重载即可生效。

## 插件一览

分两类：**生成式**（`gen_*`，一次算好结构 / 图案，参数一改就重编）与
**模拟**（`sim_*`，逐帧推进状态，靠时间轴播放）。前缀与类别一致，新增扩展也照此命名。

### 生成式（`gen_*`）

| 扩展 id | 名称 | 用途 | 状态 | 依赖 / 权限 | 快捷键 |
|---|---|---|---|---|---|
| [`gen_zellij`](gen_zellij/README.md) | Gen Zellij | 生成式伊斯兰几何镶嵌（zellij），渲染为分层 Grease Pencil v3 | 可用（5.2.2 实测） | 无外部依赖（图案库随包） | — |
| [`gen_rosette`](gen_rosette/README.md) | Gen Rosette | 生成式星形玫瑰线，编成上下交替的蕾丝带，渲染为分层 Grease Pencil v3 | 可用（5.2.2 实测） | 无外部依赖 | — |
| [`gen_celtic`](gen_celtic/README.md) | Gen Celtic | 任意网格 → 交织绳结（编的是求值几何，输出曲线 + 几何节点扫成圆管），改源网格 / 修改器参数即重编 | 可用（5.2.2 实测） | 无外部依赖（用 Blender 自带 numpy） | — |
| [`gen_dome`](gen_dome/README.md) | Gen Dome | 生成式斐波那契穹顶：球面 Voronoi 面板，逐代重划分的生长（点数递增 → 面板变多变小），几何节点按帧挑代 + 种子上立柱 | 可用（5.2.2 实测） | 无外部依赖（用 Blender 自带 numpy） | — |
| [`gen_interlace`](gen_interlace/README.md) | Gen Interlace | 生成式交错编织（纯 2D）：曲线样条或 Grease Pencil 笔划当股线，沿股线交替一上一下（奇偶求解）；被压住的那一条「平贴端头」——退到对方身外，缺口用只填不描的补片沿对方边缘裁平，或用零重叠切法；实时输出 GP 笔划（逐点 `strand_index`、逐笔划 `piece_index` / `fill_id`） | 可用（5.2.2 实测） | 无外部依赖（用 Blender 自带 numpy） | — |
| [`gen_girih`](gen_girih/README.md) | Gen Girih | 生成式 girih 星形纹样：10 种阿基米德镶嵌 + Hankin 射线（接触角 / 双线间隙），追出长股线后按「一上一下」真切缺口（切口 + 端头补片 + 蕾丝边），三种样式 / 星形填充（可按边数分色）/ 七套配色 / 接触角扫掠；几何移植自 islamic-patterns（MIT） | 可用（5.2.2 实测） | 无外部依赖（用 Blender 自带 numpy） | — |
| [`gen_tsubayaki`](gen_tsubayaki/README.md) | Gen Tsubayaki | 生成式弦线族（两条轨道之间的弦）：单轨星图 / 按格子裁边的分块开片，`n` / `t` 由帧号闭式生长 + 逐弦墨色淡出，渲染为 Grease Pencil v3（默认深底反白 + GP 效果器辉光） | 可用（5.2.2 实测） | 无外部依赖（用 Blender 自带 numpy） | — |
| [`gen_torus_knot`](gen_torus_knot/README.md) | Gen Torus Knot | 参数化环面 (p, q) 结：默认输出 Grease Pencil 笔划（每股一条闭合笔划，几何核心带 POINT 域的 `u_map` / `v_map` 两条属性），可选几何节点把笔划扫成实体管（管半径实时可调） | 可用（5.2.2 实测） | 无外部依赖（用 Blender 自带 numpy） | — |
| [`gen_voronoi`](gen_voronoi/README.md) | Gen Voronoi | 生成式平面 Voronoi：网格顶点或 Grease Pencil 的点当种子，实时重划成 GP 笔划（气泡 / 边界裁剪、内缩、逐格填充，逐点带 `cell_index`）；在 GP 编辑模式里画点、拖点即长 | 可用（5.2.2 实测） | 无外部依赖（用 Blender 自带 numpy） | — |
| [`gen_fractal_family`](gen_fractal_family/README.md) | Gen Fractal Family | 复整数「基因」生成分形曲线家族（G 高斯 / E 艾森斯坦点阵，96 条经典预设）：视口 **HUD** 里直接拖格点上的节点、点 R/M 切标记、滚轮换等级；预览（默认关闭）是 `POST_VIEW` 立即模式绘制（**不建任何临时对象**），输出曲线 / 每级对象 / 形态键生长动画 | 可用（5.2.2 实测） | 剪贴板权限（粘贴基因）；无外部依赖（用 Blender 自带 numpy） | — |
| [`gen_lsystem`](gen_lsystem/README.md) | Gen L-System | L-system（公理 + 产生式 + 条件 + 参数表达式）→ turtle 解释成折线，输出 Grease Pencil 笔划：每条分支一笔、**粗细逐点变化**（`!` / `#` / `¤`），可选按粗细 / 按深度 / 按进度三档渐变（进度按弧长逐笔归一）；十套 ABOP 预设、2D 俯视 / 3D 四分之三视角两档场景预设、参数一改自动重算 | 可用（5.2.2 实测） | 无外部依赖 | — |

### 模拟（`sim_*`）

| 扩展 id | 名称 | 用途 | 状态 | 依赖 / 权限 | 快捷键 |
|---|---|---|---|---|---|
| [`sim_catenary`](sim_catenary/README.md) | Sim Catenary | XPBD 铁环相扣悬链线物理模拟，逐帧驱动 Grease Pencil 笔划（可按张力着色） | 可用（5.2.2 实测） | 无外部依赖（用 Blender 自带 numpy） | — |
| [`sim_dendrite`](sim_dendrite/README.md) | Sim Dendrite | Warren 相场晶枝生长模拟，逐帧推进后画成 3D 高度场（相场 / 过冷度两种配色） | 可用（5.2.2 实测） | 可选 taichi（GPU 加速，250² 约 5 ms/帧）；缺失时回退纯 NumPy | — |
| [`sim_differential`](sim_differential/README.md) | Sim Differential | 差分生长：一条闭合折线在斥力 + 培养皿边界下不断分裂打褶，逐帧写成一笔闭合 GP 笔划（曲率 / 拉伸 / 单色三档配色） | 可用（5.2.2 实测） | 可选 taichi（GPU 加速，1000 节点约 8 ms/帧）；缺失时回退纯 NumPy（空间哈希，约 14 ms/帧） | — |
| [`sim_flocking`](sim_flocking/README.md) | Sim Flocking | Reynolds boids 群集模拟（XY 平面）：分离 / 对齐 / 聚集三条规则逐帧推进，每只鸟画成一个朝前的三角箭头，可直接在视口里按左键撒鸟；环绕 / 软边界 / 反弹三种边界、速度 / 朝向两种配色 | 可用（5.2.2 实测） | 可选 taichi（GPU 加速，5000 只约 1 ms/子步）；缺失时回退纯 NumPy（300 只约 1.5 ms/子步） | — |
| [`sim_game_of_life`](sim_game_of_life/README.md) | Sim Game of Life | Conway 生命游戏跑在四边形网格的面上（面 = 细胞，要求面小且均匀、每个面 8 邻居，否则图案走样），每 N 帧推进一代并实时画成面颜色；五档 B/S 规则、生死 / 年龄两种配色 | 可用（5.2.2 实测） | 可选 taichi（GPU 加速，几千面用 NumPy 已实时）；缺失时回退纯 NumPy | — |
| [`sim_hilbert`](sim_hilbert/README.md) | Sim Hilbert | 3D 希尔伯特曲线的弹簧质点物理模拟：格子落下来撞地、坍缩、摊开，逐帧驱动一条 GP 笔划（速度 / 应变配色） | 可用（5.2.2 实测） | 可选 taichi（GPU 加速，4 阶 4.7 万弹簧 16 ms/帧）；缺失时回退纯 NumPy（3 阶 41 ms/帧） | — |
| [`sim_jacobi`](sim_jacobi/README.md) | Sim Jacobi | 环面 Jacobi theta 除数模拟：零点小球在环面上滚动互撞，逐帧把除数写成顶点属性（光晕 / 相角 / 势井 / 相位格纹），环面按零点凹陷 + 涟漪变形 | 可用（5.2.2 实测） | 可选 taichi（GPU 加速，150×100 约 3 ms/帧）；缺失时回退纯 NumPy（8 零点约 4 ms/帧） | — |
| [`sim_pendulum`](sim_pendulum/README.md) | Sim Pendulum | 双摆相空间阵列：N×N 个理想双摆逐帧 RK4 推进，每个摆一抹 3 点 GP 折线（逐点半径 + 角速度 / 相角配色），两档初值（θ1–θ2 摆角平面 / θ1–dθ1 相空间切片）；可切「网格 + 几何节点」后端做同步开销对照 | 可用（5.2.2 实测） | 无必需依赖（NumPy 版零依赖；可选 taichi GPU：1 万摆 8.2 → 3.4 ms/帧） | — |
| [`sim_reaction_diffusion`](sim_reaction_diffusion/README.md) | Sim Reaction Diffusion | Gray-Scott 反应扩散：8 套经典 (F, k) 预设长成迷宫 / 斑点 / 蠕虫 / 孔洞，逐帧推进后画成 3D 高度场（图案 / 水墨 / 轮廓三种配色） | 可用（5.2.2 实测） | 可选 taichi（GPU 加速，200² × 32 子步约 3.4 ms/帧）；缺失时回退纯 NumPy（约 6.8 ms/帧） | — |
| [`sim_space_colonization`](sim_space_colonization/README.md) | Sim Space Colonization | 太空殖民生长：任意输入网格表面按面积撒吸收点，枝条逐帧长过去（贴表面 = 藤蔓 / 自由空间 = 树枝），每个分叉到枝尖画成一笔 GP（逐点半径 + 根→梢的逐点配色）；不选目标会自动建缺省输入网格 | 可用（5.2.2 实测） | 可选 scipy（KD 树，约 1.9 ms/子步）或 taichi（GPU 暴力，1.4 ms）；都缺时回退纯 NumPy（18.6 ms/子步） | — |

新增一个扩展的最小步骤：

1. 建目录 `b3d-addons/<ext_id>/`，写 `blender_manifest.toml`（`id` 与目录名一致）和 `__init__.py`
   （提供 `register()` / `unregister()`）。
2. 在 `.vscode/settings.json` 的 `blender.addonFolders` 里加上该目录（方式 A 只需保证仓库已注册）。
3. `Blender: Start` 启动并启用扩展；之后改代码用 `F6` 或 MCP 重载。
4. 补上本 README 的插件一览行与（如有）快捷键。

## 插件开发约定

- 清单必填项（`blender_manifest.toml`）：`schema_version`、`id`、`version`、`name`、`tagline`、
  `maintainer`、`type = "add-on"`、`license`、`blender_version_min`（建议 `4.2.0`）。
- **目录名 == manifest 的 `id`**：仓库内模块名按目录名解析，不一致会加载失败。
- 需要联网、读写文件、剪贴板等能力时，在 manifest 里声明 `[permissions]` 并写明原因；用到网络时同时检查
  `bpy.app.online_access`。
- 依赖第三方库优先随扩展自带（`wheels`）或用 [Python 环境](#python-环境) 中的 modules 目录方式；
  避免隐式依赖系统 Python。
- 写 **Taichi 后端**（参考 `sim_dendrite` / `sim_jacobi` / `sim_reaction_diffusion`）时有两个
  固定约定：`taichi_solver.py` **不要**写 `from __future__ import annotations`（Taichi 直接读
  `__annotations__` 判断 kernel 参数类型，字符串注解会报
  `TaichiSyntaxError: Invalid type annotation`）；`import taichi` 失败时按
  [Python 环境](#python-环境) 说的补一次 `scripts/modules` 路径，兜住 `--factory-startup`。
- 逐顶点配色：网格属性叫 `color` 就行，但材质里 `Color Attribute` 节点的 `layer_name` 要
  **留空**（建好节点就别动它，默认就是空的；由渲染器自动取活跃色属性），并把
  `color_attributes.active_color_index` / `render_color_index` 指过去。填上 `color` 或
  `vertex_color` 会被 Cycles 当成自己的标准顶点色名、取到空值渲染成纯黑（**只有 Cycles**，
  EEVEE 正常，只测 EEVEE 发现不了）。
- `unregister()` 必须完整反向清理（类、面板、快捷键、处理器、定时器），否则重载会残留重复注册。
- **新建对象一律进「当前激活的 collection」**，不要写死 `scene.collection`：生成物、预设相机、
  补光都算。统一写法是
  `target = collection or bpy.context.collection or bpy.context.scene.collection`，
  只有后台 / handler 里取不到 `context.collection` 时才回退场景根。

### frame handler：按「模块命名空间」清理，不要按函数名

`bpy.app.handlers` 是一串**平铺的函数对象**，Blender 没有给 handler 分区/命名空间的东西
（`bpy.app` 里唯一带 namespace 的是 `bpy.app.driver_namespace`，那是**驱动表达式**的求值命名
空间：它适合存 GPU draw handler 的**不透明句柄**（见下一节），但存不了「哪个 app handler 是
我的」—— app handler 本身就是函数对象，没有句柄可存）。于是各插件只有 `frame_handler` 一个
名字可用，「按名字清场」就会把别家的 handler 一起摘掉 —— 两个 sim 插件同时启用时，后启用的
那个会把先启用的停掉，画面直接不动。

正确判据是 **`handler.__module__`**：扩展装在 `bl_ext.<repo>.<id>` 下，热重载后模块名不变、
函数对象换新，所以模块名是唯一稳定的归属依据；顺带还能一次清掉本包注册在其它子模块里的
handler。所有 sim 插件都已按这个写法清理：

```python
def purge_handlers() -> None:
    package = __package__ or __name__

    def ours(handler) -> bool:
        module = getattr(handler, "__module__", "")
        return module == package or module.startswith(package + ".")

    bpy.app.handlers.frame_change_pre[:] = [
        handler for handler in bpy.app.handlers.frame_change_pre if not ours(handler)
    ]
```

新插件照抄这一段即可，函数名继续叫 `frame_handler`（想再加一层保险就给函数名带插件前缀，
防住那些还没改成模块判据的旧版本，例如 `sim_flocking` 的 `flocking_frame_handler`）。

### GPU draw handler：句柄存两处（模块变量 + `driver_namespace`）

要在 3D 视图里直接画东西时用 `SpaceView3D.draw_handler_add(callback, args, region_type,
draw_type)`，它返回一个**不透明句柄**（5.2.2 里是 `PyCapsule`，`RNA_HANDLE`），只能拿这个
句柄去 `draw_handler_remove(handle, region_type)`，否则永远删不掉。四个实测出来、必须照顾到
的行为：

1. **重复 add 会堆叠，而且句柄互不相等**：同一个函数 add 两次得到两个句柄，回调被调两次。
   所以注册前先自愈地清一遍（和 app handler 一个道理）。
2. **`region_type` 必须和 add 时一致**：`'WINDOW'` 的句柄用 `'HEADER'` 去删**不会当场报错**，
   但句柄会被消耗掉，随后再删就抛
   `ValueError: callback_remove(handler): null handler given, invalid or already removed`。
   所以句柄要**和 region 一起存**（一个插件在多个 region 各挂一个时尤其要注意）。
3. **`bpy.app.driver_namespace` 在文件加载时会被清空**（`read_homefile` /
   `read_factory_settings` 之后 key 就没了，实测），而 **draw handler 在文件加载后还活着**
   （加载后仍能成功 remove）。只把句柄存 namespace 的话，用户 File > Open 之后就再也删不掉
   那个 handler 了 —— 所以**模块级变量才是主，namespace 只是跨「重跑脚本 / 热重载」的兜底**
   （重跑脚本时模块变量会被重新初始化，而 namespace 还在）。
4. namespace 是驱动表达式的求值空间，key 对驱动**可见**，所以键名一律带插件前缀。

```python
_DRAW_KEY = "sim_flocking_draw_handle"  # 键名带插件前缀
_DRAW_REGION = "WINDOW"

#: 主副本：模块变量。文件加载后仍在（namespace 会被清空）
_HANDLE = None


def _paint(*args) -> None:
    ...  # 用 gpu / blf 画；回调每个 3D 视图都会被调用，别在这里做重活


def install_draw_handler() -> None:
    global _HANDLE
    remove_draw_handler()  # 自愈：先清掉上一次留下的
    _HANDLE = bpy.types.SpaceView3D.draw_handler_add(
        _paint, (), _DRAW_REGION, "POST_PIXEL"
    )
    bpy.app.driver_namespace[_DRAW_KEY] = (_HANDLE, _DRAW_REGION)


def remove_draw_handler() -> None:
    global _HANDLE
    stored = bpy.app.driver_namespace.get(_DRAW_KEY)
    candidates = [_HANDLE]
    if isinstance(stored, tuple):
        candidates.append(stored[0])
    elif stored is not None:
        candidates.append(stored)
    for handle in candidates:
        if handle is None:
            continue
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, _DRAW_REGION)
        except (ValueError, TypeError):
            pass  # 已经删过了 / 句柄已被文件加载作废
    _HANDLE = None
    bpy.app.driver_namespace.pop(_DRAW_KEY, None)
```

调用时机：`register()` 里 `install_draw_handler()`，`unregister()` 里
`remove_draw_handler()`（`unregister` 里的清理要能容忍二次删除，所以上面套了 `try`）。

`draw_type` 按要画的东西选：`POST_PIXEL` 是 2D 叠层（像素坐标，配 `blf` 写字 / 画 HUD），
`POST_VIEW` 是 3D 视图空间（画立体几何，要自己做 depth 处理）。两种都在**每个 3D 视图**各调
一次，回调里别假设只有一个视图、也别做重活。

## 颜色偏好

全项目共用一套色板：各扩展从这里取色，不要各自另定一套（尤其是画布底色）。

| 常量 | 色值 | 名称 | 用途 |
|---|---|---|---|
| `INK` | `#2B2B2B` | 墨玄 | 主线 / 文字，最深的强调色 |
| `CINNABAR` | `#C85A4B` | 朱砂 | 强调、焦点、标记 |
| `AZURITE` | `#3E5C6B` | 石青 | 冷色主调、副线 |
| `OCHRE` | `#C49A45` | 赭石 | 暖色点缀、高亮 |
| `MOSS` | `#5A7052` | 苔绿 | 中性过渡、次要形状 |
| `SILK` | `#F4F0EA` | 绢白 | 浅色底 / 反白文字 |
| `BACKGROUND` | `#E4D9D0` | 画布底色 | 作品统一使用，避免每个后端各写一个白 |

```python
INK = "#2B2B2B"  # 墨玄
CINNABAR = "#C85A4B"  # 朱砂
AZURITE = "#3E5C6B"  # 石青
OCHRE = "#C49A45"  # 赭石
MOSS = "#5A7052"  # 苔绿
SILK = "#F4F0EA"  # 绢白

#: 画布底色 —— 作品统一用它，避免每个后端各写一个白。
BACKGROUND = "#E4D9D0"
```

规则：

- 色值一律写 sRGB 十六进制；写进 Blender 的颜色属性（`vertex_color`、材质、世界背景）
  前先转线性，否则整体发白（换算见 [gen_zellij 的颜色约定](gen_zellij/README.md)）。
- 画布底色只有一个 `BACKGROUND`；插件需要"白"时用 `SILK`，不要另写纯白。
  唯一的例外是线稿插件 `gen_lsystem`：它全程不出填色块，画布默认就用绢白 `SILK`（见其 README）。

色板落在 [`_shared/palette.py`](_shared/palette.py)（含 `hex_to_linear()` / `srgb_to_linear()`）。
Blender 扩展必须自包含，所以每个插件目录下都有一份 `palette.py`（当前 **21 份**，含 `gen_girih`
与全部 `sim_*`），都是它的**逐字副本**（随各自的 zip 打包）：改色时二十一处一起改，
`Get-FileHash` 比对即可确认同步。

视口着色检查（`viewport.py`）也一样：`gen_*` 与 `sim_*` 共 **19 份**逐字副本，同样整批同步。

技术细节见两份报告：生成式（GP v3 属性表与实测行为、各插件算法、交织专题）见
[`GEN_TECH.md`](GEN_TECH.md)；模拟（`STATE` 契约、逐帧链路、双后端与数值、性能与调试清单）见
[`SIM_TECH.md`](SIM_TECH.md)。

## 快捷键速查

> 占位表：按插件实际绑定补充。

| 快捷键 | 功能 | 作用域 | 来源 |
|---|---|---|---|
| — | — | — | — |