"""几何节点组 ``Phyllotaxis Dome``：挑代（生长）+ 立柱。

mesh 里烘的是**每一代**完整的 Voronoi 划分（逐面属性 ``gen`` 记代号），所以这里的“生长”
只是一次筛选：把不属于当前帧那一代的面删掉。播放 / 拖动时间轴都在切代，不重算几何。

```text
Group Input(Geometry, Grow, Animate, Start Frame, Length, Last Gen, Studs, Stud Radius)
  ├─ (帧 - 起始帧) / 时长 × Last Gen  ┐
  ├─ Grow × Last Gen                 ┴→ Switch(Animate) → 取整 → 当前代
  └─ gen 属性 → |gen - 当前代| > 0.5 → Delete Geometry(FACE)   只留当前这一代
       ├→ Set Material(面板) ────────────────────────────┐
       └→ Mesh to Points(FACES, 位置 = seed_pos 属性)     │
            → Ico Sphere(立柱) → Set Material ─┐         │
                 → Instance on Points → Switch(Studs) ────┤
  └────────────────────────────────────→ Join Geometry ───┴→ Group Output
```

**不做平滑着色**：面板就是它自己的平面法线（平直的多边形面），想要圆润的用户自己加修改器
或在物体上 Shade Smooth —— 插件只负责几何、材质与生长。

代的来源有两种，``Animate`` 开关选一种：

* 关 —— 直接用 ``Grow`` 输入（0 = 第一代，1 = 最后一代），拖滑块是实时的；
* 开 —— 用场景时间算：``(当前帧 - Start Frame) / Length``，夹到 0..1 再乘上 ``Last Gen``。
  于是一帧一代（或几帧一代，烘的代少时），播放就是“点数变多、单元变小”的连续生长，
  倒着播会自己退回去。

``Last Gen`` 是**烘进网格的**代数（由生成算子写在对象上），不是面板上的帧数 ——
后者只决定这段动画占多少帧，改它不用重新生成。

**Blender 5.x**：接口输入要按名字回 ``node_group.interface.items_tree`` 里查 identifier
（``Socket_2`` 这种），写的时候走 ``modifier.properties.inputs.<identifier>.value``；
老的 ``modifier[identifier]`` 在 5.x 上直接抛 "id properties not supported"（见
:func:`mesh.set_mod_input`）。
"""

from __future__ import annotations

import bpy

__all__ = [
    "GEN_ATTR",
    "GROUP_NAME",
    "GROUP_VERSION",
    "PANEL_MATERIAL_NODE",
    "SEED_INDEX_ATTR",
    "SEED_POS_ATTR",
    "STUD_SUBDIVISIONS",
    "STUD_MATERIAL_NODE",
    "dome_group",
    "material_node",
]

#: 节点组名（只建一次，之后所有生成对象共用）。
GROUP_NAME = "Phyllotaxis Dome"
#: 节点组版本；不匹配就地重搭（老文件里的组可能缺插口，直接复用会静默失效）。
#: 2 = 生长改成“按代筛选”（1 是更早的“按出生时刻删面”，几何完全不同，必须重搭）。
#: 3 = 代先取整再比较（2 里进度正好落在两代中间时两代会同时显示、互相穿插）。
#: 4 = 去掉两处 Set Shade Smooth（平滑着色交给用户自己加）。
#: 5 = 重排版面：三条横带、单一 Group Input、运算节点加中文标签。
GROUP_VERSION = 5
_VERSION_KEY = "gen_dome_api"

#: 立柱球的细分级别（2 已经圆得看不出来，再高只是多面数）。
STUD_SUBDIVISIONS = 2

#: 逐面属性名 —— 与 ``dome.build_generations`` 写进网格的那三个一一对应。
SEED_INDEX_ATTR = "seed_idx"
SEED_POS_ATTR = "seed_pos"
GEN_ATTR = "gen"

#: 两个 ``Set Material`` 节点的名字（材质由 ``mesh`` 按名字写进去）。
PANEL_MATERIAL_NODE = "Panel Material"
STUD_MATERIAL_NODE = "Stud Material"


def _new(nodes, bl_idname, location, name: str = "", label: str = ""):
    """建节点、摆好位置、可选加标签。

    ``location`` 一律给坐标：不给的话所有节点都叠在原点，打开编辑器就是一团；
    版面本身怎么分带见 :func:`dome_group` 里的说明。``name`` 是查节点用的固定名
    （材质靠它找节点），``label`` 只是显示用的中文说明，不影响查找。
    """
    node = nodes.new(bl_idname)
    node.location = location
    if name:
        node.name = name
    if label:
        node.label = label
    return node


def _socket(node, *names):
    """按名字取输入插口（跨版本按名字找，不认死序号）。"""
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            return socket
    raise KeyError(f"{node.bl_idname} missing input {names[0]!r}")


def _math(nodes, operation, location, *, clamp: bool = False, label: str = ""):
    node = _new(nodes, "ShaderNodeMath", location, label=label)
    node.operation = operation
    node.use_clamp = clamp
    return node


def _cleared(group):
    """把已有的旧节点组清空（就地重搭，对象上的修改器引用因此不会断）。"""
    for node in list(group.nodes):
        group.nodes.remove(node)
    interface = group.interface
    if hasattr(interface, "clear"):
        interface.clear()
    else:  # pragma: no cover - 兼容老接口
        for item in list(interface.items_tree):
            interface.remove(item)


def _interface(group):
    """接口：几何进出 + 七个驱动输入。"""
    interface = group.interface
    interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )
    interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )

    grow = interface.new_socket(
        name="Grow", in_out="INPUT", socket_type="NodeSocketFloat"
    )
    grow.default_value = 1.0
    grow.min_value = 0.0
    grow.max_value = 1.0
    grow.description = (
        "生长进度：0 = 第一代（点数最少），1 = 最后一代（关掉 Animate 时用它）"
    )

    animate = interface.new_socket(
        name="Animate", in_out="INPUT", socket_type="NodeSocketBool"
    )
    animate.default_value = False
    animate.description = "生长进度改由场景时间算，时间轴一播放就长出来"

    start = interface.new_socket(
        name="Start Frame", in_out="INPUT", socket_type="NodeSocketInt"
    )
    start.default_value = 1
    start.description = "从哪一帧开始长（Animate 打开时生效）"

    length = interface.new_socket(
        name="Length", in_out="INPUT", socket_type="NodeSocketInt"
    )
    length.default_value = 72
    length.min_value = 1
    length.description = "长满用多少帧（Animate 打开时生效；改它不用重新生成）"

    last = interface.new_socket(
        name="Last Gen", in_out="INPUT", socket_type="NodeSocketInt"
    )
    last.default_value = 0
    last.min_value = 0
    last.description = "网格里烘了几代（最后一层的代号，0 = 只有一代不生长）"

    studs = interface.new_socket(
        name="Studs", in_out="INPUT", socket_type="NodeSocketBool"
    )
    studs.default_value = True
    studs.description = "在每块面板的种子上放一个小球"

    radius = interface.new_socket(
        name="Stud Radius", in_out="INPUT", socket_type="NodeSocketFloat"
    )
    # 接口插口只认基础类型，DISTANCE 是拿 subtype 标的（不是 NodeSocketFloatDistance）
    radius.subtype = "DISTANCE"
    radius.default_value = 0.012
    radius.min_value = 0.0
    radius.description = "立柱小球的半径"
    return interface


def dome_group():
    """取（必要时建）穹顶节点组。"""
    group = bpy.data.node_groups.get(GROUP_NAME)
    if group is not None and group.get(_VERSION_KEY) == GROUP_VERSION:
        return group
    if group is None:
        group = bpy.data.node_groups.new(GROUP_NAME, "GeometryNodeTree")
    else:
        _cleared(group)

    group.is_modifier = True
    _interface(group)

    nodes, links = group.nodes, group.links

    # 版面分三条横带，从左到右单向流动，线不回头：
    #
    #   ① y≈ 100  几何主线：Group Input → Delete Geometry → Set Material → Join → Output
    #   ② y≈-400  当前代：Scene Time → 帧-起始帧 → ÷时长 → ×代数 → Switch → 取整 → 比对
    #   ③ y≈-700  立柱：种子属性 → Mesh to Points → 实例化 → Switch(立柱) → 汇入 Join
    #
    # 插口一律从**同一个** Group Input 取（Blender 允许一个节点连多条线）；长线尽量压短，
    # 免得外部「节点整理」类插件为了缩短连线再复制出一堆 Group Input。
    source = _new(nodes, "NodeGroupInput", (-1500.0, 100.0))
    output = _new(nodes, "NodeGroupOutput", (1560.0, 80.0))

    # --- ② 当前是第几代 ---------------------------------------------------
    clock = _new(nodes, "GeometryNodeInputSceneTime", (-1500.0, -360.0))
    shift = _math(nodes, "SUBTRACT", (-1300.0, -400.0), label="帧 - 起始帧")
    links.new(clock.outputs["Frame"], shift.inputs[0])
    links.new(source.outputs["Start Frame"], shift.inputs[1])

    # 夹在 0..1：提前就是第一代，超时就是最后一代
    span = _math(
        nodes, "DIVIDE", (-1120.0, -400.0), clamp=True, label="÷ 时长（夹 0..1）"
    )
    links.new(shift.outputs[0], span.inputs[0])
    links.new(source.outputs["Length"], span.inputs[1])

    timed = _math(nodes, "MULTIPLY", (-940.0, -320.0), label="× 代数")
    links.new(span.outputs[0], timed.inputs[0])
    links.new(source.outputs["Last Gen"], timed.inputs[1])

    manual = _math(nodes, "MULTIPLY", (-940.0, -470.0), label="× 代数（手动）")
    links.new(source.outputs["Grow"], manual.inputs[0])
    links.new(source.outputs["Last Gen"], manual.inputs[1])

    progress = _new(
        nodes, "GeometryNodeSwitch", (-740.0, -395.0), label="跟时间轴 / 手动"
    )
    progress.input_type = "FLOAT"
    links.new(source.outputs["Animate"], _socket(progress, "Switch"))
    links.new(manual.outputs[0], _socket(progress, "False"))
    links.new(timed.outputs[0], _socket(progress, "True"))

    # 进度先取整再比较：正好落在两代中间时（0.5）不能两代都留，否则两层几何互相穿插
    step = _math(nodes, "FLOOR", (-540.0, -395.0), label="取整")
    links.new(progress.outputs["Output"], step.inputs[0])

    # 只留 gen 与当前代相等的那些面（两边都是整数，差值只会是 0 / ±1 / …）
    gen = _new(nodes, "GeometryNodeInputNamedAttribute", (-360.0, -620.0))
    gen.data_type = "INT"
    gen.inputs["Name"].default_value = GEN_ATTR
    distance = _math(nodes, "SUBTRACT", (-360.0, -395.0), label="gen - 代数")
    links.new(gen.outputs["Attribute"], distance.inputs[0])
    links.new(step.outputs[0], distance.inputs[1])

    distance_abs = _math(nodes, "ABSOLUTE", (-180.0, -395.0), label="取绝对值")
    links.new(distance.outputs[0], distance_abs.inputs[0])
    stale = _math(nodes, "GREATER_THAN", (0.0, -395.0), label="> 0.5")
    links.new(distance_abs.outputs[0], stale.inputs[0])
    stale.inputs[1].default_value = 0.5

    # --- ① 几何主线 -------------------------------------------------------
    delete = _new(
        nodes, "GeometryNodeDeleteGeometry", (420.0, 80.0), label="只留当前代"
    )
    delete.domain = "FACE"
    delete.mode = "ALL"
    links.new(source.outputs["Geometry"], _socket(delete, "Geometry"))
    links.new(stale.outputs[0], _socket(delete, "Selection"))

    # 不做平滑着色：面板保持自己的平面法线，想要圆润的用户自己加
    # 「着色平滑」修改器或在物体上右键 Shade Smooth
    panels = _new(
        nodes,
        "GeometryNodeSetMaterial",
        (660.0, 160.0),
        name=PANEL_MATERIAL_NODE,
    )
    links.new(delete.outputs["Geometry"], _socket(panels, "Geometry"))

    # --- ③ 立柱 -----------------------------------------------------------
    # 从「删完之后」的面取点：面板没长出来，它的立柱也不该在
    points = _new(nodes, "GeometryNodeMeshToPoints", (240.0, -700.0), label="种子 → 点")
    points.mode = "FACES"
    links.new(delete.outputs["Geometry"], _socket(points, "Mesh"))

    seed_pos = _new(nodes, "GeometryNodeInputNamedAttribute", (-360.0, -1020.0))
    seed_pos.data_type = "FLOAT_VECTOR"
    seed_pos.inputs["Name"].default_value = SEED_POS_ATTR
    links.new(seed_pos.outputs["Attribute"], _socket(points, "Position"))

    # 底板那张面不是种子的面板（seed_idx = -1），不该在它上面立一颗球
    seed_index = _new(nodes, "GeometryNodeInputNamedAttribute", (-360.0, -860.0))
    seed_index.data_type = "INT"
    seed_index.inputs["Name"].default_value = SEED_INDEX_ATTR
    real = _new(nodes, "FunctionNodeCompare", (-160.0, -1020.0), label="排除底板")
    real.data_type = "INT"
    real.operation = "GREATER_THAN"
    links.new(seed_index.outputs["Attribute"], _socket(real, "A"))
    _socket(real, "B").default_value = -1
    links.new(real.outputs["Result"], _socket(points, "Selection"))

    ico = _new(nodes, "GeometryNodeMeshIcoSphere", (480.0, -1000.0))
    _socket(ico, "Subdivisions").default_value = STUD_SUBDIVISIONS
    links.new(source.outputs["Stud Radius"], _socket(ico, "Radius"))

    studs_mat = _new(
        nodes,
        "GeometryNodeSetMaterial",
        (700.0, -1000.0),
        name=STUD_MATERIAL_NODE,
    )
    links.new(ico.outputs["Mesh"], _socket(studs_mat, "Geometry"))

    instance = _new(nodes, "GeometryNodeInstanceOnPoints", (920.0, -780.0))
    links.new(points.outputs["Points"], _socket(instance, "Points"))
    links.new(studs_mat.outputs["Geometry"], _socket(instance, "Instance"))

    studs_on = _new(nodes, "GeometryNodeSwitch", (1120.0, -700.0), label="立柱开关")
    studs_on.input_type = "GEOMETRY"
    links.new(source.outputs["Studs"], _socket(studs_on, "Switch"))
    # False 那一头不接：空几何，等于没有立柱
    links.new(instance.outputs["Instances"], _socket(studs_on, "True"))

    # --- 汇合 -------------------------------------------------------------
    join = _new(nodes, "GeometryNodeJoinGeometry", (1300.0, 80.0), label="面板 + 立柱")
    links.new(panels.outputs["Geometry"], _socket(join, "Geometry"))
    links.new(studs_on.outputs["Output"], _socket(join, "Geometry"))
    links.new(join.outputs["Geometry"], output.inputs["Geometry"])

    group[_VERSION_KEY] = GROUP_VERSION
    return group


def material_node(group, name: str):
    """取组里某个 ``Set Material`` 节点（写材质用）；没有就返回 ``None``。"""
    return group.nodes.get(name) if group is not None else None
