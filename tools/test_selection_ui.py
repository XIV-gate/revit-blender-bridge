"""Actual Blender RNA/operators for transparent pre-import selection and reports."""
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch
from types import SimpleNamespace

import bpy

sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
import xivgate_revit_bridge as addon
addon.register()
from xivgate_revit_bridge import ui, runtime
from xivgate_revit_bridge.core.reporting import raw_values
from xivgate_revit_bridge.core.selection_view import matches

for cls in ui.CLASSES:
    if issubclass(cls, bpy.types.Operator):
        assert cls.bl_description.strip(), f'Missing button tooltip: {cls.bl_idname}'
tooltip = ui.RBB_OT_select.description(None, SimpleNamespace(mode='all', scope='visible'))
assert 'показанных строках' in tooltip and 'поиска' in tooltip

with patch.object(runtime, 'begin_scan') as scan:
    operator = SimpleNamespace(report=lambda level, text: (_ for _ in ()).throw(AssertionError(text)))
    operator.execute = lambda context: ui.RBB_OT_scan.execute(operator, context)
    assert ui.RBB_OT_scan.invoke(operator, bpy.context, None) == {'FINISHED'}
    assert bpy.ops.xivgate_rbb.scan('INVOKE_DEFAULT') == {'FINISHED'}
    assert scan.call_count == 2

props = bpy.context.scene.xivgate_revit_bridge
ui._SYNCING = True
for index in range(5380):
    row = props.elements.add()
    row.element_id = str(index)
    row.name = 'Бетонная стена'
    row.type_name = 'Фасад' if index < 4710 else 'ADSK_Внутренняя_Бетон_200'
    row.category_id, row.category_name = 'walls', 'Стены'
    row.selected = index < 4710
    row.recommendation = 'include' if row.selected else 'exclude'
    row.reason = 'Фасад' if row.selected else 'Предположительно внутренняя стена — по имени типа'
row = props.elements.add()
row.element_id, row.category_id, row.name, row.selected = 'window', 'windows', 'Окно', True
group = props.categories.add()
group.group_id, group.group_kind, group.name = 'walls', 'category', 'Стены'
ui._SYNCING = False
ui.sync_groups(props)
assert group.selected_count == 4710 and group.count == 5380

class Layout:
    def __init__(self):
        self.calls = []
    def row(self, **kwargs):
        return self
    def operator(self, op, **kwargs):
        self.calls.append((op, kwargs))
        return SimpleNamespace()
    def label(self, **kwargs):
        self.calls.append(('label', kwargs))

layout = Layout()
ui.RBB_UL_groups.draw_item(None, bpy.context, layout, props, group, 0, props, 'category_index', 0)
assert layout.calls[0][1]['icon'] == 'REMOVE'
assert 'частично' in layout.calls[-1][1]['text']
assert bpy.ops.xivgate_rbb.inspect_group() == {'FINISHED'}
assert props.list_mode == 'elements' and props.scope_id == 'walls' and props.selection_filter == 'excluded'
assert props.elements[props.element_index].selected is False
excluded = [row for row in props.elements if matches(row, scope_kind=props.scope_kind, scope_id=props.scope_id, status=props.selection_filter)]
assert len(excluded) == 670 and all('Внутренняя' in row.type_name for row in excluded)
before = time.monotonic()
assert bpy.ops.xivgate_rbb.select(mode='all', scope='visible') == {'FINISHED'}
elapsed = time.monotonic() - before
assert group.selected_count == 5380 and props.elements[-1].selected
assert all(row.recommendation == 'exclude' for row in excluded), 'User override must not rewrite recommendation'
assert bpy.ops.xivgate_rbb.toggle_group(kind='category', group_id='walls') == {'FINISHED'}
assert group.selected_count == 0 and props.elements[-1].selected
assert bpy.ops.xivgate_rbb.toggle_group(kind='category', group_id='walls') == {'FINISHED'}
assert group.selected_count == 5380
assert bpy.ops.xivgate_rbb.clear_scope() == {'FINISHED'}
assert not props.scope_kind and props.selection_filter == 'all'

prefix = 'Выбранный элемент не дал видимой геометрии (фаза, вариант, отсутствие сетки или неподдерживаемая связь): '
values = [prefix + str(index) for index in range(2210)] + ['Не найдена текстура: facade.jpg']
runtime.set_warnings(props, values)
assert props.warning_count == 2 and props.warnings[0].count == 2210
assert props.warnings[0].severity == 'INFO'
runtime.set_warnings(props, raw_values(props.warnings) + ['Ошибка Revit'])
assert props.warning_count == 3 and len(raw_values(props.warnings)) == 2212
props.warning_index = 0
assert bpy.ops.xivgate_rbb.copy_report_details() == {'FINISHED'}
# Background Blender does not provide an OS clipboard; lossless content is
# verified above before the UI-only copy action.
addon.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_SELECTION_REPORT ' + json.dumps(dict(status='passed', walls=5380, excluded_visible=670,
    recommendations_preserved=True, group_toggle_is_scoped=True, diagnostics_lossless=True,
    select_excluded_seconds=elapsed)), flush=True)
