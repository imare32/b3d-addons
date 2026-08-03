bl_info = {
    "name": "b3d",
    "author": "imare32",
    "description": "Personal Blender addon",
    "blender": (4, 2, 0),
    "version": (0, 0, 1),
    "category": "Generic",
}

from . import auto_load  # noqa: E402

auto_load.init()


def register():
    auto_load.register()


def unregister():
    auto_load.unregister()
