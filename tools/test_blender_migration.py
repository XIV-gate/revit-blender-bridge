"""Exercise ordinary scan after a Blender profile upgrade; never start Revit."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import bpy
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
import xivgate_revit_bridge as addon
from xivgate_revit_bridge import runtime, updater, ui
from xivgate_revit_bridge.core.installer import manifest_plan
from xivgate_revit_bridge.core import updates
addon.register()
base = Path(os.environ['RBB_TEST_ROOT']) / ('blender_migration_' + str(os.getpid()))
relative = 'runtime/revit/2027/loader/1/XIVgate.RevitBridge.Loader.dll'
roots = [base / f'{version}/scripts/addons/xivgate_revit_bridge' for version in ('5.2', '5.3')]
for root in roots:
    dll = root / relative
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b'fixture frozen loader')
plans = [manifest_plan('', root / relative, appdata_root=base) for root in roots]
target = Path(plans[0]['path'])
target.parent.mkdir(parents=True)
target.write_text(plans[0]['content'])
backup = base / 'backup'
backup.mkdir()
props = bpy.context.scene.xivgate_revit_bridge
props.busy = False
context = SimpleNamespace(scene=bpy.context.scene, window_manager=SimpleNamespace(
    invoke_props_dialog=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Unexpected reconnect dialog'))))
operator = SimpleNamespace(execute=lambda ctx: ui.RBB_OT_scan.execute(operator, ctx),
                           report=lambda *args: (_ for _ in ()).throw(AssertionError(str(args))))
# Real ordinary-scan invoke + execute + registration transaction, with only the
# installation location and Revit launch boundary substituted by fixture paths.
with patch.object(ui.workflow, 'prepare_settings', return_value=base), \
     patch.object(runtime, 'setup_plan', return_value=plans[1]), \
     patch.object(ui, '__file__', str(roots[1] / 'ui.py')), \
     patch.object(updater, 'connect_revit', side_effect=lambda plan: updates.connect_registration(plan, roots[1], backup)), \
     patch.object(runtime, 'begin_scan') as begin:
    assert ui.RBB_OT_scan.invoke(operator, context, None) == {'FINISHED'}
    begin.assert_called_once_with(bpy.context.scene)
assert target.read_text() == plans[1]['content']
assert (roots[0] / relative).read_bytes() == b'fixture frozen loader'
addon.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_BLENDER_MIGRATION passed ' + bpy.app.version_string, flush=True)
