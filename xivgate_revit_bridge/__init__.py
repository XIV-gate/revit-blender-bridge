"""AGR Revit Bridge by XIVgate: local RVT import from Blender."""

bl_info = {
    "name": "AGR Revit Bridge",
    "author": "XIVgate",
    "version": (0, 5, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > AGR > Revit",
    "description": "Scan local RVT models, select elements, and import shared geometry",
    "doc_url": "https://github.com/XIV-gate/revit-blender-bridge#readme",
    "tracker_url": "https://github.com/XIV-gate/revit-blender-bridge/issues",
    "category": "Import-Export",
}


def register():
    # Blender's manual ZIP installer can reload only this file after disabling
    # the old add-on. Importing cached children would revive its old UI/updater.
    import importlib
    from pathlib import Path
    import sys
    cached_ui = sys.modules.get(__name__ + '.ui')
    if cached_ui is not None and not any(getattr(cls, 'is_registered', False)
                                        for cls in getattr(cached_ui, 'CLASSES', ())):
        root = Path(__file__).resolve().parent
        names = sorted((name for name in sys.modules if name.startswith(__name__ + '.')),
                       key=lambda name: name.count('.'), reverse=True)
        for name in names:
            module = sys.modules.pop(name)
            cached = getattr(module, '__cached__', None)
            if cached and Path(cached).resolve().is_relative_to(root):
                Path(cached).unlink(missing_ok=True)
            parent_name, _, child = name.rpartition('.')
            parent = sys.modules.get(parent_name)
            if parent is not None and getattr(parent, child, None) is module:
                delattr(parent, child)
        importlib.invalidate_caches()
    from . import ui
    ui.register()


def unregister():
    from . import ui
    ui.unregister()
