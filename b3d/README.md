# b3d — Personal Blender Addon

个人 Blender 插件，集合了多个提升工作效率的小工具。支持 Blender 4.2+，通过 `auto_load` 模块自动注册所有功能。

## Shell

- **PowerShell 7.x** (`pwsh`)

## Blender

- **路径**: `C:\Programs\blender-5.2.2-windows-x64\blender.exe`
- **版本**: 5.2.2 LTS

### 环境变量

| 变量 | 值 | 说明 |
|---|---|---|
| `BLENDER_USER_RESOURCES` | `C:\git\blender\5.2` | 用户资源目录 |
| `PYDEVD_DISABLE_FILE_VALIDATION` | `1` | 禁用 pydevd 文件校验 |

### Python 环境

- **系统Python**: `C:\Program Files\Python313\python.exe` 已添加进环境变量

- **捆绑解释器**: `C:\Programs\blender-5.2.2-windows-x64\5.2\python\bin\python.exe`
- **第三方库目标目录**: `C:\git\blender\5.2\scripts\modules`

Blender 启动时会将该目录加入 `sys.path`，因此内嵌 Python 可直接 `import` 已安装的第三方库。

**安装第三方库**（不污染 Blender 本体）：

```powershell
& "C:\Programs\blender-5.2.2-windows-x64\5.2\python\bin\python.exe" -m pip install <pkg> --target "C:\git\blender\5.2\scripts\modules"
```

### VSCode 插件开发

使用 VSCode 扩展 **Blender Development**（`jacqueslucke.blender-development`）启动 Blender、附加 Python 调试器并热重载插件。

#### 目录链接

本项目通过目录联接（junction）直接链接到 Blender 的扩展目录，因此**不需要打包安装**，改完代码重载即可生效：

| 链接位置 | 目标 |
|---|---|
| `C:\git\blender\5.2\extensions\vscode_development\b3d` | `C:\git\b3d` |

```powershell
New-Item -ItemType Junction -Path "C:\git\blender\5.2\extensions\vscode_development\b3d" -Target "C:\git\b3d"
```

其中 `vscode_development` 对应扩展设置 `blender.addon.extensionsRepository` 的默认值。

#### 启动与加载

执行 `Blender: Start` 后，扩展实际运行的命令为：

```
C:\Programs\blender-5.2.2-windows-x64\blender.exe --python C:\Users\Administrator\.vscode\extensions\jacqueslucke.blender-development-0.0.31\pythonFiles\launch.py
```

待加载的插件通过启动参数传给 `launch.py`：

```json
"ADDONS_TO_LOAD": "[{\"load_dir\":\"c:\\git\\b3d\",\"module_name\":\"b3d\"}]"
```

Blender 实际加载的是链接目录下的副本，调试器通过下面的路径映射把本地源码与运行代码对应起来（由扩展自动生成，无需手动填写）：

```json
"pathMappings": [
  {
    "localRoot": "C:\\git\\b3d",
    "remoteRoot": "C:\\git\\blender\\5.2\\extensions\\vscode_development\\b3d"
  }
]
```

#### 快捷键（`keybindings.json`）

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

> 也可开启 `blender.addon.reloadOnSave`，保存文件时自动重载。

#### 通过 MCP 热重载（与启动方式无关）

`F6` 只在 VSCode 扩展自己启动的 Blender 里有效。改用 MCP（`execute_blender_code`）则无此限制 ——
只要 Blender 正在运行且 MCP 可用，无论它是怎么启动的，都能重载改动后的代码：

```python
import bpy, sys

mod = next((a.module for a in bpy.context.preferences.addons
            if a.module == "b3d" or a.module.endswith(".b3d")), None)

bpy.ops.preferences.addon_disable(module=mod)
for name in list(sys.modules):
    if name == mod or name.startswith(mod + "."):
        del sys.modules[name]
bpy.ops.preferences.addon_enable(module=mod)
```

注意：

- 必须走「禁用 → 清 `sys.modules` → 启用」。直接 `import b3d` 拿到的是旧模块，重复注册会导致类和快捷键重复。
- 模块名取决于扩展仓库：VSCode 这边是 `bl_ext.vscode_development.b3d`，其他仓库前缀不同，所以用 `.b3d` 后缀匹配而不是写死。
- 这套流程和扩展的 `blender.reloadAddons` 内部实现一致（disable → 删 `sys.modules` → enable），两条路可以混用，不会冲突。
- 若 b3d 未作为插件启用（只是被 `import`），上面取不到 `mod`，需退回手动方式：

```python
import importlib, b3d
b3d.unregister()
importlib.reload(b3d)
b3d.register()
```

## 功能概览

### 节点编辑器增强 (`my_arrange_nodes.py`)

- **一键排列节点** (`Alt+Shift+F`)：自动排列当前节点树中的所有节点，保留原有选择状态
- **节点树优化** (`Alt+Shift+Q`)：一键清理和优化节点树（按节点类型隐藏接口/选项的规则见 [`data/node_cleanup_rules.json`](data/node_cleanup_rules.json)），包括：
  - 移除 Reroute 节点（保留连接关系）
  - 替换已废弃节点为新版节点
  - 分发 Group Input 节点（减少长距离连线）
  - 分发独立输入节点（Index、Position 等）
  - 按规则隐藏未连接的输入/输出接口
  - 重置节点宽度
  - 可选移除 Frame 节点和孤立节点

### 颜色渐变工具 (`my_color_ramp.py`)

在 Color Ramp 的右键菜单中添加：
- **复制/粘贴 Color Ramp**：在多个 Color Ramp 之间快速复制色标、插值模式等全部配置
- **预设生成器**：内置 15 种常用配色预设（热力图、海洋、岩浆、Viridis、地形、落日等），一键应用

### 编辑器切换 (`my_editor_switch.py`)

在几乎所有编辑器标题栏添加 **Tab 切换按钮组**，按功能分组：
- **组 1**：几何节点、着色器(对象/世界)、图像编辑器、Dope Sheet、时间线、文本编辑器
- **组 2**：属性编辑器、Python 控制台
- **组 3**：电子表格、资产浏览器、文件浏览器

快捷键 `Alt+`` 在当前分组内循环切换编辑器类型。

### 图标查看器 (`my_icon_viewer.py`)

3D 视图侧栏 > Dev 面板中提供 Blender 全部内置图标的浏览工具：
- 按名称或视觉关键词搜索（如 "sun"、"eye"、"hand"）
- 四种分类模式：紧凑(5类) / 标准(18类) / 前缀分组 / 扁平列表
- 点击图标复制名称到剪贴板
- 可调图标大小和是否显示名称

### 命名属性菜单 (`my_named_attributes.py`)

在几何节点编辑器的菜单栏添加"命名属性"菜单：
- 列出当前活动对象的所有自定义属性（按域分类显示）
- 点击属性自动在节点树中添加对应类型的 Named Attribute 节点

### 节点信息面板 (`show_node_info.py`)

在节点编辑器的活动节点属性面板顶部添加信息摘要：
- 显示节点名称、类型、位置、尺寸
- 列出所有输入/输出接口的名称和标识符
- 可直接切换接口的隐藏状态

### 资产浏览器窗口 (`my_asset_window.py`)

在电子表格标题栏添加"Asset Browser"按钮：
- 通过 `userpref_show()` 打开新窗口并切换为资产浏览器
- 偏好设置窗口会记忆上次的位置和尺寸，因此资产浏览器窗口也会保持一致

### 通用操作符 (`my_operators.py`)

- **表达式测试** (`dev.expr_test`)：弹出对话框执行 Python 表达式，支持 `context`/`bpy`/`D` 快捷变量
- **复制文本** (`b3d.copy_text`)：将指定文本复制到剪贴板
- **切换帧结束** (`Alt+Shift+E`)：在 250 和 3000 帧之间切换场景结束帧
- **切换线框显示** (`Alt+Shift+W`)：切换活动网格对象的线框显示
- **窗口复制/全屏** (`Alt+Shift+D`)：智能处理当前区域 — 多区域时弹出独立窗口（Properties/Outliner 会自动调整尺寸），单区域时切换全屏或关闭窗口

### 跨平台窗口助手 (`platform_window.py`)

封装 Win32 (Windows) 和 X11 (Linux) 的窗口操作 API：
- 获取前台窗口、窗口 PID、屏幕尺寸、窗口矩形
- 移动和调整窗口位置/大小

## 快捷键速查

| 快捷键 | 功能 | 作用域 |
|--------|------|--------|
| `Alt+Shift+F` | 排列所有节点 | 节点编辑器 |
| `Alt+Shift+Q` | 优化节点树 | 节点编辑器 |
| `Alt+Shift+E` | 切换帧结束 (250/3000) | 3D 视图 |
| `Alt+Shift+W` | 切换线框显示 | 3D 视图 |
| `Alt+Shift+D` | 复制/全屏窗口 | 多种编辑器 |
| `Alt+`` | 循环切换编辑器 | 多种编辑器 |
