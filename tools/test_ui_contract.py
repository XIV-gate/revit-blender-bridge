import ast
import os
from pathlib import Path
import sys

import bpy

root = Path(os.environ['RBB_SOURCE_DIR'])
sys.path.insert(0, str(root))
sys.dont_write_bytecode = True
import xivgate_revit_bridge as addon
addon.register()
tree = ast.parse((root / 'xivgate_revit_bridge' / 'ui.py').read_text(encoding='utf-8'))
icons = set(bpy.types.UILayout.bl_rna.functions['label'].parameters['icon'].enum_items.keys())
for node in ast.walk(tree):
    if isinstance(node, ast.keyword) and node.arg == 'icon' and isinstance(node.value, ast.Constant):
        assert node.value.value in icons, node.value.value
for cls in tree.body:
    if isinstance(cls, ast.ClassDef):
        methods = [node.name for node in cls.body if isinstance(node, ast.FunctionDef)]
        assert len(methods) == len(set(methods)), cls.name
for name in ('RBB_OT_cache', 'RBB_OT_preview', 'RBB_OT_clear_preview', 'RBB_OT_scan'):
    assert hasattr(getattr(addon.ui, name), 'execute')
addon.unregister()
print('AGR_UI_CONTRACT passed', bpy.app.version_string)
