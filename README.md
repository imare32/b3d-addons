# B3D - Blender 个人工作流与高效开发插件

![Blender Version](https://img.shields.io/badge/Blender-4.2%2B-orange.svg)
![License](https://img.shields.io/badge/License-GPL--3.0-blue.svg)
![Maintainer](https://img.shields.io/badge/Maintainer-imare32-brightgreen.svg)

**B3D** 是一款专为 Blender 4.2+ 打造的高效工作流增强与开发者辅助扩展插件。它集成了窗口无缝全屏/独立弹窗管理、节点自动布局与调试、快捷编辑器切换、渐变色带预设管理、内置图标检索等实用功能，旨在提升日常创作与脚本开发效率。

---

## 🌟 核心功能一览

- **窗口与独立界面管理**：一键将 3D 视图、节点编辑器等区域复制并**自动最大化/全屏弹窗**，或快速关闭独立窗口。支持无边框模式下的快捷窗口控制。
- **节点树增强与排版**：一键排列节点树（基于配置规则），支持直接查看并复制节点的 `bl_idname` 与 Socket 标识。
- **快捷编辑器切换**：提供分组式的编辑器快速切换面板与快捷键，覆盖 Geometry Nodes、Shader Nodes、Asset Browser、Spreadsheet 等高频编辑器。
- **资产浏览器独立窗口**：可将任意面板一键复制并调整为居中摆放的资产浏览器独立窗口。
- **渐变色带 (ColorRamp) 增强**：支持色彩渐变预设的复制、粘贴、翻转与管理。
- **Blender 图标浏览器**：实时预览 Blender 所有内置 UI 图标与映射符号，搜索并快速复制图标名称。
- **命名属性 (Named Attributes) 节点快投**：在 Geometry Nodes 中快速创建带类型与域 (Domain) 映射的 Named Attribute 节点。

---

## ⌨️ 常用快捷键 (Keymaps)

| 快捷键 | 作用区域 | 功能描述 |
| :--- | :--- | :--- |
| <kbd>Alt</kbd> + <kbd>Shift</kbd> + <kbd>D</kbd> | 3D 视图 / 节点编辑器 / 控制台 | **复制区域并自动最大化/全屏**：在主窗口触发时弹出新窗口并自动全屏；在独立窗口中按下则直接**关闭窗口** |
| <kbd>Alt</kbd> + <kbd>Shift</kbd> + <kbd>D</kbd> | 偏好设置 (Preferences) / 属性 / 大纲 | 一键**关闭独立窗口** |
| <kbd>Alt</kbd> + <kbd>Shift</kbd> + <kbd>E</kbd> | 3D 视图 (VIEW_3D) | 快速切换场景结束帧（在 `3000` 与 `250` 帧之间交替） |
| <kbd>Alt</kbd> + <kbd>Shift</kbd> + <kbd>W</kbd> | 3D 视图 (VIEW_3D) | 快速开启/关闭当前选中网格物体的**线框显示 (Wireframe)** |

---

## 🛠️ 模块功能详解

### 1. 窗口独立与操作系统支持 (`platform_window.py` & `my_operators.py`)
- 利用 Win32 与 Linux X11 原生 API 接口，智能识别外部窗口句柄与进程 PID。
- **无边框启动兼容**：针对带 `--no-window-frame` 参数启动的 Blender，即便缺乏系统标题栏，也能通过快捷键平滑关闭或调整新弹出的偏好设置/属性窗口。

### 2. 节点树排版与调试 (`my_arrange_nodes.py` & `show_node_info.py`)
- **自动对齐**：使用 `node.my_arrange_all_nodes` 自动清理整洁节点图。
- **调试面板**：在节点编辑器的侧边栏直接读取选中节点的真实 `bl_idname` 与端口列表，单击按钮即可复制文本到剪贴板，非常适合开发节点插件与 Python 脚本。

### 3. 编辑器分组切换 (`my_editor_switch.py` & `my_asset_window.py`)
- 提供简易高效的组别导航：
  - **Group 1**: Geometry Nodes, Shader Nodes (Object/World), Image Editor, Dope Sheet, Timeline, Text Editor.
  - **Group 2**: Properties, Python Console.
  - **Group 3**: Spreadsheet, Asset Browser, File Browser.
- 提供居中悬浮式 Asset Browser 独立窗口弹出功能。

### 4. 内置图标预览器 (`my_icon_viewer.py`)
- 内置丰富的 Blender 图标映射字典，支持模糊检索、分类查看及图标标识快速复制，提升开发 UI 面板时的效率。

### 5. 渐变色带增强 (`my_color_ramp.py`)
- 方便对界面中的 `ColorRamp` 控件进行深层读写操作，轻松复制/粘贴色带数据或应用调色模板。

---

## 📦 安装与使用方法

1. **打包/下载插件**：
   将本仓库代码压缩为 `.zip` 文件（或直接保留目录文件夹）。

2. **在 Blender 中安装**：
   - 打开 Blender (4.2+)。
   - 进入 `Edit (编辑)` -> `Preferences (偏好设置)` -> `Add-ons (插件)`。
   - 点击右上角箭头菜单选择 `Install from Disk... (从磁盘安装)`，选择本插件包。
   - 勾选启用 **b3d** 插件。

---

## 📁 目录结构

```text
b3d/
├── __init__.py                # 插件主入口及自动注册机制
├── auto_load.py               # 自动模块加载工具
├── blender_manifest.toml      # Blender 4.2+ Extension 清单配置
├── my_arrange_nodes.py        # 节点自动布局与规则定义
├── my_asset_window.py         # 居中资产浏览器窗口生成器
├── my_color_ramp.py           # ColorRamp 渐变色带实用工具
├── my_editor_switch.py        # 编辑器快速分组切换器
├── my_icon_viewer.py          # Blender 内置图标预览与检索面板
├── my_named_attributes.py     # 命名属性节点快捷添加
├── my_operators.py            # 核心快捷键算子与窗口控制逻辑
├── platform_window.py         # 跨平台 (Linux/Win32) 原生窗口底层控制
├── show_node_info.py          # 节点调试信息与名称复制面板
├── rules.json                 # 节点排版规则配置文件
└── pyproject.toml             # Python 项目元数据配置
```

---

## 📄 开源协议

本项目遵循 **GPL-3.0-or-later** 开源协议。
