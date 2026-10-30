"""Scene switching must never disguise Early Access as a stable release."""
import os
from pathlib import Path
import sys
import time

import bpy

sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
import xivgate_revit_bridge as addon
from xivgate_revit_bridge import updater

addon.register()
stable = dict(version='0.3.0', channel='stable', available=False, installable=False, release_url='stable')
early = dict(version='0.3.1', channel='early', available=True, installable=True, release_url='early')
old = dict(version='0.2.0', channel='stable', available=False, installable=True, release_url='old')
scene = bpy.context.scene
props = scene.xivgate_revit_bridge
updater.start_check(fetcher=lambda _: [early, stable, old])
deadline = time.monotonic() + 5
while updater.STATE.busy and time.monotonic() < deadline:
    updater.tick(); time.sleep(.02)
assert updater.STATE.phase == 'CURRENT', updater.STATE.message
assert updater.STATE.info['version'] == '0.3.0'
assert props.release_version == '0.3.0'
props.release_channel = 'early'
assert props.release_version == '0.3.1'
assert updater.info_for_props(props)['channel'] == 'early'
other = bpy.data.scenes.new('Stable in other scene')
bpy.context.window.scene = other
second = other.xivgate_revit_bridge
assert second.release_channel == 'stable'
assert updater.info_for_props(second)['channel'] == 'stable'
assert all(info['channel'] == 'stable' for info in updater.visible_releases(second.release_channel))
second.release_version = '0.2.0'
assert updater.info_for_props(second)['installable']
assert updater.STATE.info['version'] == '0.2.0'
addon.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_RELEASE_CHANNELS passed', bpy.app.version_string)
