"""Actual host font checks; no windows, user input or profile modifications."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
import bpy
from xivgate_revit_bridge import ui
from xivgate_revit_bridge.core.text_layout import pixel_wrap


class Layout:
    def __init__(self):
        self.lines = []
    def column(self, align=False):
        assert align
        return self
    def label(self, **values):
        self.lines.append(values)


text = 'Отсоединение RVT в локальной рабочей копии с закрытыми рабочими наборами'
path = 'C:\\Users\\Имя Пользователя\\ОченьДлинноеНазваниеФасаднойМоделиБезПробелов.rvt'
records = []
for scale in (1.0, 1.5, 2.0):
    preferences = SimpleNamespace(system=SimpleNamespace(ui_scale=scale), ui_styles=bpy.context.preferences.ui_styles)
    for zoom in (0.8, 1.0, 1.4):
        for width in (240, 350, 500, 800):
            # Actual font API with deterministic sidebar coordinates. UI scale
            # and sidebar zoom are independent inputs, not character counts.
            view = SimpleNamespace(region_to_view=lambda x, y, s=scale, z=zoom: (x / (s*z), y))
            region = SimpleNamespace(width=width * scale, type='UI', view2d=view)
            context = SimpleNamespace(preferences=preferences, region=region)
            budget, measure = ui._text_metrics(context, inset=20)
            for message in (text, path, 'Ш' * 300):
                target = Layout()
                ui._wrapped(target, message, context, icon='INFO')
                assert target.lines[0]['icon'] == 'INFO'
                assert all(row['icon'] == 'BLANK1' for row in target.lines[1:])
                assert all(measure(row['text']) <= budget + 0.01 for row in target.lines)
                assert ''.join(row['text'] for row in target.lines).replace(' ', '') == message.replace(' ', '')
            lines = pixel_wrap(text, budget, measure)
            records.append(dict(scale=scale, zoom=zoom, width=width, budget=budget, lines=lines))

# In the real background context no UI region exists; declared dialog width
# remains valid and doesn't accidentally use the full application window.
budget, measure = ui._text_metrics(bpy.context, width=650)
assert budget > 0 and measure('ШШШШ') > measure('iiii')
small = pixel_wrap(text, 200, measure)
large = pixel_wrap(text, 600, measure)
assert len(small) > len(large)
style = bpy.context.preferences.ui_styles[0]
report = {'host': bpy.app.version_string, 'ui_font_points': getattr(style, 'widget_label', style.widget).points,
          'actual_context_region': str(bpy.context.region), 'cases': len(records), 'measurements': records}
output = Path(os.environ['RBB_TEST_ROOT']) / ('ui_wrapping_' + str(os.getpid()) + '.json')
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('UI_WRAPPING_PASS', bpy.app.version_string, len(records), str(output))
