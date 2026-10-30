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
from xivgate_revit_bridge.core.updates import fetch_release, download_release, validate_archive

assert bpy.app.online_access, 'Pass --online to the isolated test launcher'
xivgate_revit_bridge.register()
updater.start_check()
deadline = time.monotonic() + 30
while updater.STATE.busy and time.monotonic() < deadline:
    updater.tick()
    time.sleep(.1)
assert updater.STATE.phase == 'CURRENT', updater.STATE.message
assert updater.STATE.info['version'] == updater.current_version()
root = Path(os.environ['RBB_TEST_ROOT']).resolve()
output = root / ('public_blender_check_' + str(os.getpid()))
output.mkdir()
info = fetch_release('0.1.0')
archive = download_release(info, output)
package = validate_archive(archive, info['version'], tuple(bpy.app.version))
assert package.manifest['version'] == updater.current_version()
report = dict(status='passed', blender=bpy.app.version_string, anonymous_github=True,
              ui_check_reports_current=True, live_download_verified=True,
              version=info['version'], sha256=info['sha256'])
(output / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
xivgate_revit_bridge.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG']) / 'userpref.blend').exists()
print('AGR_PUBLIC_UPDATE ' + json.dumps(report), flush=True)
