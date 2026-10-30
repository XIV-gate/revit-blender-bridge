"""Install/enable the handoff ZIP in the isolated test profile, without saving preferences."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import bpy
import addon_utils

sys.dont_write_bytecode = True
root = Path(os.environ['RBB_TEST_ROOT']).resolve()
for key in ('BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_DATAFILES', 'BLENDER_USER_EXTENSIONS'):
    assert Path(os.environ[key]).resolve().is_relative_to(root)
package = root / os.environ['RBB_PACKAGE_NAME']
result = bpy.ops.preferences.addon_install(filepath=str(package), overwrite=True)
assert result == {'FINISHED'}, result
addon_utils.modules_refresh()
module = addon_utils.enable('xivgate_revit_bridge', default_set=False, persistent=False)
assert module is not None
assert hasattr(bpy.context.scene, 'xivgate_revit_bridge')
if hasattr(module.ui, '_initialize_tree'):
    assert module.ui._initialize_tree() is None
from xivgate_revit_bridge.runtime import worker_path
assert worker_path().is_file()
with patch.object(module.ui.workflow, 'prepare_settings', return_value=root), \
        patch.object(module.runtime, 'setup_plan', return_value={'path': str(root / 'fixture.addin')}), \
        patch.object(module.runtime, 'manifest_matches', return_value=True), \
        patch.object(module.runtime, 'begin_scan') as scan:
    operator = SimpleNamespace(report=lambda level, text: (_ for _ in ()).throw(AssertionError(text)))
    operator.execute = lambda context: module.ui.RBB_OT_scan.execute(operator, context)
    assert module.ui.RBB_OT_scan.invoke(operator, bpy.context, None) == {'FINISHED'}
    assert bpy.ops.xivgate_rbb.scan('INVOKE_DEFAULT') == {'FINISHED'}
    assert scan.call_count == 2
assert Path(module.__file__).resolve().is_relative_to(Path(os.environ['BLENDER_USER_SCRIPTS']).resolve())
addon_utils.disable('xivgate_revit_bridge', default_set=False)
assert not hasattr(bpy.context.scene, 'xivgate_revit_bridge')
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('XIVGATE_PACKAGE ' + json.dumps(dict(status='passed', blender=bpy.app.version_string, installed=package.name,
    isolated_profile=True, enabled=True, bundled_worker=True, preferences_not_saved=True)))
