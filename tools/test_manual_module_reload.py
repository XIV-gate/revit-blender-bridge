"""Manual ZIP installs reload the package root; stale disabled children must go."""
import importlib
import os
from pathlib import Path
import sys

import bpy

sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
import xivgate_revit_bridge as addon

addon.register()
previous_ui = addon.ui
previous_core = addon.updater.updates
previous_ui._test_stale_marker = True
addon.unregister()
addon = importlib.reload(addon)
addon.register()
assert addon.ui is not previous_ui, 'Old UI was reused after manual installation'
assert addon.updater.updates is not previous_core, 'Old updater validation was reused'
assert not hasattr(addon.ui, '_test_stale_marker')
assert hasattr(bpy.context.scene, 'xivgate_revit_bridge')
addon.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_MANUAL_MODULE_RELOAD passed', bpy.app.version_string)
