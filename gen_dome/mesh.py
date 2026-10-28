"""几何后端：穹顶对象（网格 + 逐面属性 + 修改器 + 材质）。

本模块只管「写 Blender」，一个坐标都不算 —— 多边形来自 :mod:`dome`。分工：

1. **网格** —— ``dome.build_generations`` 的顶点 / 面写进 ``Fibonacci Dome`` 数据块；
   逐面属性 ``seed_idx``（INT）/ ``seed_pos``（FLOAT_VECTOR）/ ``gen``（INT）跟着写。
   这就是本插件对外的**数据接口**（几何节点靠它挑代、摆立柱），``seed_idx`` /
   ``seed_pos`` 与参考实现 ``blender_fibonacci_dome.py`` 的属性名一致。
   网格里烘着**每一代**完整的 Voronoi 划分，所以顶点数会明显多于当前帧看到的面板
   （生长期望上限见 ``dome.PANEL_BUDGET``）。
2. **修改器** —— 只加一个 ``Phyllotaxis`` 几何节点修改器（:mod:`gn`）：挑出当前这一代、
   放立柱。**不加实体化**（厚度）、也**不做平滑着色** —— 那是用户自己的事，往修改器栈后面
   接就行，插件不去猜他要多厚、要不要圆润。
3. **材质** —— 面板一个、立柱一个；颜色只改材质基色，几何一点不动。

**Blender 5.x**：修改器上的节点组输入要走 ``modifier.properties.inputs.<identifier>.value``
（identifier 是 ``Socket_2`` 这种，名字得回节点组接口里查，见 :func:`set_mod_input`）；
老的 ``modifier[identifier]`` 在 5.x 上直接抛 "id properties not supported"（修改器不是 ID）。
而且**写它不会自动标记对象重求值** —— 改完得自己 ``obj.update_tag()``，
不然拖滑块时视口停在旧几何上。
"""

from __future__ import annotations

import bpy
import numpy as np

from . import gn
from .palette import OCHRE, SILK, hex_to_linear

__all__ = [
    "GENERATIONS_KEY",
    "MARKER",
    "MODIFIER_NAME",
    "OBJECT_NAME",
    "PANEL_MATERIAL",
    "STUD_MATERIAL",
    "apply_growth",
    "apply_studs",
    "build_object",
    "find_object",
    "material",
    "remove_object",
    "set_colors",
    "set_mod_input",
    "stored_generations",
    "write_mesh",
]

#: 生成对象的名字（固定名，方便找回来）。
OBJECT_NAME = "Fibonacci Dome"
#: 生成对象上的标记：有它就是本插件生成的（``find_object`` 据此找对象）。
MARKER = "gen_dome"
#: 对象上的几何节点修改器名（插件只加这一个修改器）。
MODIFIER_NAME = "Phyllotaxis"
#: 面板材质名。
PANEL_MATERIAL = "Dome Panel"
#: 立柱材质名。
STUD_MATERIAL = "Dome Stud"
#: 生成对象上记的「烘了几代」：几何节点要按它把 0..1 的进度换算成代号。
#: 这是**网格**的属性（改帧数不会变），所以存在对象上，不进 ``props.DESIGN_KEYS``。
GENERATIONS_KEY = "gen_dome_generations"

#: 面板默认色：色板的绢白（3D 物体上一片干净的白，靠缝和立柱吃光）。
DEFAULT_PANEL = hex_to_linear(SILK)
#: 立柱默认色：色板的赭石（暖色小点，与冷白面板拉开）。
DEFAULT_STUD = hex_to_linear(OCHRE)


def find_object():
    """按标记找回生成对象（没有就返回 ``None``）。"""
    for obj in bpy.data.objects:
        if MARKER in obj and obj.type == "MESH":
            return obj
    return None


def remove_object() -> bool:
    """删掉生成对象（连带它独占的数据块），没找到返回 ``False``。"""
    obj = find_object()
    if obj is None:
        return False
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and not mesh.users:
        bpy.data.meshes.remove(mesh)
    return True


# --- 网格 -----------------------------------------------------------------


def _attribute(mesh, name: str, kind: str, domain: str = "FACE"):
    """取（必要时建）一个网格属性；同名但类型 / 域不对的就地换掉。"""
    attr = mesh.attributes.get(name)
    if attr is not None and attr.data_type == kind and attr.domain == domain:
        return attr
    if attr is not None:
        mesh.attributes.remove(attr)
    return mesh.attributes.new(name=name, type=kind, domain=domain)


def write_mesh(mesh, panels) -> None:
    """把 :class:`dome.Panels` 写进网格数据块（原地重建，材质槽留着）。"""
    mesh.clear_geometry()
    mesh.from_pydata(panels.verts.tolist(), [], panels.faces)
    mesh.update()
    if not panels.faces:
        return

    _attribute(mesh, gn.SEED_INDEX_ATTR, "INT").data.foreach_set(
        "value", panels.seed_index.astype(np.int32)
    )
    _attribute(mesh, gn.SEED_POS_ATTR, "FLOAT_VECTOR").data.foreach_set(
        "vector", panels.seed_pos.astype(np.float32).ravel()
    )
    _attribute(mesh, gn.GEN_ATTR, "INT").data.foreach_set(
        "value", panels.gen.astype(np.int32)
    )
    # 不给平滑着色：面板就是各自平面的法线，要圆润的用户自己 Shade Smooth / 加修改器
    mesh.update()


# --- 修改器 ---------------------------------------------------------------


def _interface_identifier(modifier, name: str):
    """节点组接口里叫 ``name`` 的输入插口的 identifier（``Socket_8`` 这种）。"""
    group = modifier.node_group
    if group is None:
        return None
    for item in group.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name == name:
            return item.identifier
    return None


def _mod_inputs(modifier):
    """5.x 的接口输入集合（``modifier.properties.inputs``）；老版本没有，返回 ``None``。"""
    return getattr(getattr(modifier, "properties", None), "inputs", None)


def _input_socket(modifier, name: str):
    """5.x：按插口名取修改器上的输入对象（有 ``value`` 的那个）；拿不到返回 ``None``。"""
    inputs = _mod_inputs(modifier)
    if inputs is None:
        return None
    identifier = _interface_identifier(modifier, name)
    socket = getattr(inputs, identifier, None) if identifier is not None else None
    if socket is not None and hasattr(socket, "value"):
        return socket
    return None


def set_mod_input(modifier, name: str, value) -> None:
    """按插口名写节点组修改器的输入。

    Blender 5.x 把接口输入挪到了 ``modifier.properties.inputs.<identifier>.value``；
    老的 ``modifier[identifier]`` 在 5.x 上直接抛 "id properties not supported"
    （修改器不是 ID），所以优先走新接口，老版本退回下标写法。
    """
    socket = _input_socket(modifier, name)
    if socket is not None:
        socket.value = value
        return
    if _mod_inputs(modifier) is not None:  # 5.x：接口在、插口没值（几何插口）
        raise ValueError(f"input socket {name!r} has no value to write")
    identifier = _interface_identifier(modifier, name)
    if identifier is None:
        raise ValueError(f"node group has no input socket named {name!r}")
    modifier[identifier] = value  # 4.x：下标就是 identifier


def _tag(obj) -> None:
    """改完修改器输入得给对象打 tag，视口才会重求值（5.2 实测，只有它管用）。"""
    obj.update_tag()


def _nodes_modifier(obj):
    modifier = obj.modifiers.get(MODIFIER_NAME)
    if modifier is None or modifier.type != "NODES":
        if modifier is not None:
            obj.modifiers.remove(modifier)
        modifier = obj.modifiers.new(MODIFIER_NAME, "NODES")
    group = gn.dome_group()
    if modifier.node_group is not group:
        modifier.node_group = group
    return modifier


def apply_studs(settings) -> None:
    """立柱开关与半径只进几何节点输入，改它不用重新生成。"""
    obj = find_object()
    if obj is None:
        return
    modifier = obj.modifiers.get(MODIFIER_NAME)
    if modifier is None or modifier.node_group is None:
        return
    set_mod_input(modifier, "Studs", bool(settings.studs))
    set_mod_input(modifier, "Stud Radius", float(settings.stud_radius))
    _tag(obj)


def stored_generations(obj) -> int:
    """对象上记的「烘了几代」（0 = 只有一代，不生长）。"""
    return int(obj.get(GENERATIONS_KEY, 0)) if obj is not None else 0


def apply_growth(settings) -> None:
    """生长进度 / 时间轴区间只进几何节点输入（不碰几何）。

    ``Last Gen`` 取的是**网格里烘的**代数（存在对象上），不是面板上的帧数 ——
    所以改「时长」是实时的，改「点数 / 生成方式」才要重新生成。
    """
    obj = find_object()
    if obj is None:
        return
    modifier = obj.modifiers.get(MODIFIER_NAME)
    if modifier is None or modifier.node_group is None:
        return
    set_mod_input(modifier, "Grow", float(settings.grow))
    set_mod_input(modifier, "Animate", bool(settings.animate))
    set_mod_input(modifier, "Start Frame", int(settings.start_frame))
    set_mod_input(modifier, "Length", int(settings.length))
    set_mod_input(modifier, "Last Gen", max(0, stored_generations(obj) - 1))
    _tag(obj)


# --- 材质 -----------------------------------------------------------------


def material(name: str, color, roughness: float = 0.5, metallic: float = 0.0):
    """取（必要时建 / 改色）材质；``color`` 是**线性** RGB。"""
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    bsdf = tree.nodes.get("Principled BSDF") if tree is not None else None
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
    mat.diffuse_color = (color[0], color[1], color[2], 1.0)
    return mat


def _assign(mesh, materials) -> None:
    """把材质按顺序摆进数据块的材质槽（多出来的槽不动，少了就补）。"""
    for index, mat in enumerate(materials):
        if index < len(mesh.materials):
            mesh.materials[index] = mat
        else:
            mesh.materials.append(mat)


def set_colors(settings) -> None:
    """面板 / 立柱颜色只改材质基色，几何一点不动。"""
    material(PANEL_MATERIAL, tuple(settings.panel_color))
    material(STUD_MATERIAL, tuple(settings.stud_color))


# --- 对象 -----------------------------------------------------------------


def build_object(panels, settings, collection=None):
    """写（或更新）生成对象：网格 + 属性 + 几何节点修改器 + 两个材质。

    对象已经存在就地更新：数据块不变、修改器与材质槽不变，改的只是顶点 / 面 / 属性 ——
    编辑模式里反复更新也不会把用户挪过的对象位置、改过的显示方式抖掉。
    插件只加**一个**几何节点修改器：厚度、平滑之类的效果用户自己往后面接。
    """
    obj = find_object()
    target = collection or bpy.context.collection or bpy.context.scene.collection
    if obj is None:
        mesh = bpy.data.meshes.new(OBJECT_NAME)
        obj = bpy.data.objects.new(OBJECT_NAME, mesh)
        target.objects.link(obj)
    elif bpy.context.view_layer.objects.get(obj.name) is None:
        # 对象留在别的场景 / 集合里：补一条链接进当前集合，否则它不在当前视图层，
        # 修改器根本不会被求值（看起来就是「生成了但不动、也不生长」）
        target.objects.link(obj)

    write_mesh(obj.data, panels)
    obj[MARKER] = True
    obj[GENERATIONS_KEY] = int(panels.generations)

    panel = material(PANEL_MATERIAL, tuple(settings.panel_color))
    stud = material(
        STUD_MATERIAL, tuple(settings.stud_color), roughness=0.35, metallic=0.2
    )
    _assign(obj.data, (panel, stud))

    nodes = _nodes_modifier(obj)

    # 几何节点里的两处 Set Material：输出几何自带材质才会上色
    group = nodes.node_group
    for node_name, mat in (
        (gn.PANEL_MATERIAL_NODE, panel),
        (gn.STUD_MATERIAL_NODE, stud),
    ):
        node = gn.material_node(group, node_name)
        if node is not None:
            node.inputs["Material"].default_value = mat

    apply_growth(settings)
    apply_studs(settings)
    return obj
