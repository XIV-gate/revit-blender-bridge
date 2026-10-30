"""XIVgate Revit Blender Bridge: local RVT import with a Blender-only workflow."""

bl_info = {
    "name": "XIVgate Revit Bridge",
    "author": "XIVgate",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > XIVgate > Revit",
    "description": "Scan local RVT models, select elements, and import shared geometry",
    "category": "Import-Export",
}


def register():
    from . import ui
    ui.register()


def unregister():
    from . import ui
    ui.unregister()
