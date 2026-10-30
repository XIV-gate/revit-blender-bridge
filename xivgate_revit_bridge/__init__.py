"""AGR Revit Bridge by XIVgate: local RVT import from Blender."""

bl_info = {
    "name": "AGR Revit Bridge",
    "author": "XIVgate",
    "version": (0, 3, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > AGR > Revit",
    "description": "Scan local RVT models, select elements, and import shared geometry",
    "doc_url": "https://github.com/XIV-gate/revit-blender-bridge#readme",
    "tracker_url": "https://github.com/XIV-gate/revit-blender-bridge/issues",
    "category": "Import-Export",
}


def register():
    from . import ui
    ui.register()


def unregister():
    from . import ui
    ui.unregister()
