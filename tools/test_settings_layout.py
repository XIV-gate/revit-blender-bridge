"""Check the user-visible groups with real Blender properties and panel drawing."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
import xivgate_revit_bridge as addon
addon.register()
from xivgate_revit_bridge import ui

class Layout:
    def __init__(self, calls=None, section='main'):
        self.calls = [] if calls is None else calls
        self.section = section
    def column(self, **kwargs):
        return Layout(self.calls, self.section)
    row = column
    box = column
    def label(self, *, text='', **kwargs):
        if text in ('Подключение Revit', 'Настройки импорта', 'Рабочие данные'):
            self.section = text
    def prop(self, data, field, **kwargs):
        self.calls.append((self.section, 'prop', field))
    def operator(self, name, **kwargs):
        self.calls.append((self.section, 'operator', name))
        return SimpleNamespace()

props = bpy.context.scene.xivgate_revit_bridge
props.show_advanced = True
props.import_id = 'fixture-import'
ui.runtime.STATE.worker = SimpleNamespace()
layout = Layout()
ui.RBB_PT_panel.draw(SimpleNamespace(layout=layout), bpy.context)
ui.runtime.STATE.worker = None
def locations(kind, name):
    return [section for section, category, value in layout.calls if category == kind and value == name]
assert locations('prop', 'revit_exe') == ['Подключение Revit']
assert locations('operator', 'xivgate_rbb.choose_revit') == ['Подключение Revit']
assert locations('operator', 'xivgate_rbb.setup_revit') == ['Подключение Revit']
assert locations('operator', 'xivgate_rbb.cancel') == ['Подключение Revit']
assert not locations('prop', 'addin_folder')
for field in ('preserve_instances', 'use_revit_instances', 'instance_tolerance_mm', 'import_materials', 'import_textures', 'pack_images', 'center_model', 'detail_level'):
    assert locations('prop', field) == ['Настройки импорта'], (field, layout.calls)
for field in ('cache_directory', 'reuse_export_cache'):
    assert locations('prop', field) == ['Рабочие данные']
assert locations('prop', 'source_rvt') == ['main']
assert locations('operator', 'xivgate_rbb.import_selected') == ['Настройки импорта', 'main']
props.show_advanced = False
layout = Layout()
ui.RBB_PT_panel.draw(SimpleNamespace(layout=layout), bpy.context)
assert not any(field == 'revit_exe' for _, kind, field in layout.calls if kind == 'prop')
addon.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_SETTINGS_LAYOUT passed', bpy.app.version_string)
