"""Category expansion, selection, tooltips and a constant panel structure."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import bpy
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
import xivgate_revit_bridge as addon
addon.register()
from xivgate_revit_bridge import ui

props = bpy.context.scene.xivgate_revit_bridge
ui._SYNCING = True
for identifier, category, name in [('1', 'walls', 'Фасад'), ('2', 'zones', 'Граница зоны'), ('3', 'walls', 'Внутренняя стена')]:
    item = props.elements.add()
    item.element_id, item.category_id, item.name = identifier, category, name
    item.reason = 'Очень длинное пояснение ' * 100
    item.selected = identifier == '1'
for identifier, name in [('zones', 'Границы зон'), ('walls', 'Стены')]:
    group = props.categories.add()
    group.group_kind, group.group_id, group.name = 'category', identifier, name
ui._SYNCING = False
ui.sync_groups(props)
assert [(row.kind, row.item_index) for row in props.tree_rows] == [('category', 0), ('category', 1)]

class Layout:
    def __init__(self, calls=None):
        self.calls = [] if calls is None else calls
    def column(self, **kw): return Layout(self.calls)
    row = column
    box = column
    def label(self, **kw): self.calls.append(('label', kw.get('text', '')))
    def prop(self, data, field, **kw): self.calls.append(('prop', field))
    def template_list(self, name, *args, **kw): self.calls.append(('list', name, kw['rows']))
    def operator(self, name, **kw):
        self.calls.append(('operator', name, kw.get('text', '')))
        return SimpleNamespace()

def panel():
    layout = Layout()
    ui.RBB_PT_panel.draw(SimpleNamespace(layout=layout), bpy.context)
    return layout.calls

before = panel()
props.tree_index = 1
assert panel() == before, 'Changing active category must not rebuild or expand the panel'
props.categories[1].expanded = True
assert [(row.kind, row.item_index) for row in props.tree_rows] == [('category', 0), ('category', 1), ('element', 0), ('element', 2)]
assert panel() == before, 'Expansion belongs inside the fixed-height list'
props.categories[0].expanded = True
assert len(props.tree_rows) == 5
props.elements[2].selected = True
assert props.categories[1].selected_count == 2
assert bpy.ops.xivgate_rbb.toggle_group(kind='category', group_id='walls') == {'FINISHED'}
assert not props.elements[0].selected and not props.elements[2].selected
props.search = 'внутренняя'
assert [(row.kind, row.item_index) for row in props.tree_rows] == [('category', 1), ('element', 2)]
assert props.selected_count == 0
tooltip = ui.RBB_OT_element_info.description(bpy.context, SimpleNamespace(element_index=2))
assert 'Очень длинное' in tooltip and 'ID 3' in tooltip
props.search = ''
for entry in panel():
    assert 'Это выбор' not in str(entry) and 'Галочки задают' not in str(entry)
assert not any(entry[:2] == ('prop', 'list_mode') for entry in panel())
for tree_row in props.tree_rows:
    ui.RBB_UL_catalog_tree.draw_item(None, bpy.context, Layout(), props, tree_row, 0, props, 'tree_index', 0)
fixture = Path(os.environ['RBB_TEST_ROOT']) / ('tree_linked_scene_' + str(os.getpid()) + '.blend')
bpy.data.libraries.write(str(fixture), {bpy.context.scene})
with bpy.data.libraries.load(str(fixture), link=True) as (reader, writer):
    writer.scenes = [reader.scenes[0]]
linked = writer.scenes[0]
assert linked.library is not None
with patch.object(ui, 'sync_groups', wraps=ui.sync_groups) as sync:
    ui._load_post()
    assert all(call.args[0].id_data != linked for call in sync.call_args_list)
bpy.data.scenes.remove(linked)
addon.unregister()
assert ui._load_post not in bpy.app.handlers.load_post
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_CATEGORY_TREE passed', bpy.app.version_string)
