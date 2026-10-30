"""Opt-in post-publication network smoke test, no installed-code replacement."""
import json
import os
from pathlib import Path
import sys
import time

import bpy

sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['RBB_SOURCE_DIR'])
import xivgate_revit_bridge
from xivgate_revit_bridge import updater
from xivgate_revit_bridge.core.updates import fetch_release, fetch_releases, download_release, validate_archive

assert bpy.app.online_access, 'Pass --online to the isolated test launcher'
xivgate_revit_bridge.register()
updater.start_check()
deadline = time.monotonic() + 30
while updater.STATE.busy and time.monotonic() < deadline:
    updater.tick()
    time.sleep(.1)
assert not updater.STATE.busy and updater.STATE.phase != 'ERROR', updater.STATE.message
props = bpy.context.scene.xivgate_revit_bridge
assert updater.STATE.info['version'] == '0.3.0' and updater.STATE.info['channel'] == 'stable'
props.release_channel = 'early'
assert updater.STATE.info['version'] == '0.3.1' and updater.STATE.info['channel'] == 'early'
assert not updater.STATE.info['installable']  # This source is the published EA version.
root = Path(os.environ['RBB_TEST_ROOT']).resolve()
output = root / ('public_blender_check_' + str(os.getpid()))
output.mkdir()
latest = fetch_release('0.1.0')
assert latest['version'] == '0.3.0', 'Early Access must not become latest stable'
verified = []
for info in fetch_releases('0.1.0'):
    if info['version'] not in ('0.3.0', '0.3.1'):
        continue
    archive = download_release(info, output)
    package = validate_archive(archive, info['version'], tuple(bpy.app.version))
    assert package.manifest['version'] == info['version']
    verified.append(dict(version=info['version'], channel=info['channel'], sha256=info['sha256']))
assert {item['version'] for item in verified} == {'0.3.0', '0.3.1'}
report = dict(status='passed', blender=bpy.app.version_string, anonymous_github=True,
              stable_and_early_lists=True, stable_latest_preserved=True, live_download_verified=True,
              verified=verified)
(output / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
xivgate_revit_bridge.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_PUBLIC_UPDATE ' + json.dumps(report), flush=True)
