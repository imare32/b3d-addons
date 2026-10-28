# b3d-addons — Blender 内容创作插件合集

面向内容创作的 Blender 扩展合集。每个子目录是一个独立扩展（extension），仓库整体又注册为一个 Blender
**本地扩展仓库**，因此插件可以逐个启用/禁用，也可以一次注册后全部可用。

支持 Blender 4.2+，当前开发环境为 **Blender 5.2.2 LTS**。

- 插件清单见 [插件一览](#插件一览)（待补充）
- 开发环境与调试流程见 [环境](#shell) 起各节

## 目录结构

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

| 扩展 id | 名称 | 用途 | 状态 | 依赖 / 权限 | 快捷键 |
|---|---|---|---|---|---|
| [`gen_zellij`](gen_zellij/README.md) | Gen Zellij | 生成式伊斯兰几何镶嵌（zellij），渲染为分层 Grease Pencil v3 | 可用（5.2.2 实测） | 无外部依赖（图案库随包） | — |
| [`gen_rosette`](gen_rosette/README.md) | Gen Rosette | 生成式星形玫瑰线，编成上下交替的蕾丝带，渲染为分层 Grease Pencil v3 | 可用（5.2.2 实测） | 无外部依赖 | — |
| [`sim_catenary`](sim_catenary/README.md) | Sim Catenary | XPBD 铁环相扣悬链线物理模拟，逐帧驱动 Grease Pencil 笔划（可按张力着色） | 可用（5.2.2 实测） | 无外部依赖（用 Blender 自带 numpy） | — |
| [`gen_celtic`](gen_celtic/README.md) | Gen Celtic | 任意网格 → 交织绳结（编的是求值几何，输出曲线 + 几何节点扫成圆管），改源网格 / 修改器参数即重编 | 可用（5.2.2 实测） | 无外部依赖（用 Blender 自带 numpy） | — |
| [`gen_dome`](gen_dome/README.md) | Gen Dome | 生成式斐波那契穹顶：球面 Voronoi 面板，逐代重划分的生长（点数递增 → 面板变多变小），几何节点按帧挑代 + 种子上立柱 | 可用（5.2.2 实测） | 无外部依赖（用 Blender 自带 numpy） | — |
| [`sim_dendrite`](sim_dendrite/README.md) | Sim Dendrite | Warren 相场晶枝生长模拟，逐帧推进后画成 3D 高度场（相场 / 过冷度两种配色） | 可用（5.2.2 实测） | 可选 taichi（GPU 加速，250² 约 5 ms/帧）；缺失时回退纯 NumPy | — |
| [`sim_game_of_life`](sim_game_of_life/README.md) | Sim Game of Life | Conway 生命游戏跑在四边形网格的面上（面 = 细胞，要求面小且均匀、每个面 8 邻居，否则图案走样），每 N 帧推进一代并实时画成面颜色；五档 B/S 规则、生死 / 年龄两种配色 | 可用（5.2.2 实测） | 可选 taichi（GPU 加速，几千面用 NumPy 已实时）；缺失时回退纯 NumPy | — |
| [`sim_hilbert`](sim_hilbert/README.md) | Sim Hilbert | 3D 希尔伯特曲线的弹簧质点物理模拟：格子落下来撞地、坍缩、摊开，逐帧驱动一条 GP 笔划（速度 / 应变配色） | 可用（5.2.2 实测） | 可选 taichi（GPU 加速，4 阶 4.7 万弹簧 16 ms/帧）；缺失时回退纯 NumPy（3 阶 41 ms/帧） | — |

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
- `unregister()` 必须完整反向清理（类、面板、快捷键、处理器、定时器），否则重载会残留重复注册。
- **新建对象一律进「当前激活的 collection」**，不要写死 `scene.collection`：生成物、预设相机、
  补光都算。统一写法是
  `target = collection or bpy.context.collection or bpy.context.scene.collection`，
  只有后台 / handler 里取不到 `context.collection` 时才回退场景根。

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

色板落在 [`_shared/palette.py`](_shared/palette.py)（含 `hex_to_linear()` / `srgb_to_linear()`）。
Blender 扩展必须自包含，所以 `gen_zellij/palette.py`、`gen_rosette/palette.py`、
`sim_catenary/palette.py`、`gen_celtic/palette.py`、`gen_dome/palette.py`、
`sim_dendrite/palette.py`、`sim_game_of_life/palette.py` 与 `sim_hilbert/palette.py` 都是它的
**逐字副本**（随各自的 zip 打包）：改色时九处一起改，`Get-FileHash` 比对即可确认同步。

## 快捷键速查

> 占位表：按插件实际绑定补充。

| 快捷键 | 功能 | 作用域 | 来源 |
|---|---|---|---|
| — | — | — | — |