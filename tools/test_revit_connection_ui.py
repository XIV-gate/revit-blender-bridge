"""Per-year registration status is read-only; selection does not register hosts."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import bpy
sys.dont_write_bytecode=True
sys.path.insert(0,os.environ['RBB_SOURCE_DIR'])
import xivgate_revit_bridge as addon
addon.register()
from xivgate_revit_bridge import runtime,ui
from xivgate_revit_bridge.core.installer import install_manifest
root=Path(os.environ['RBB_TEST_ROOT'])/('connection_'+str(os.getpid()));root.mkdir()
props=bpy.context.scene.xivgate_revit_bridge
assert props.show_import_settings and not props.show_program_settings

for year in (2024,2027):
 folder=root/str(year);folder.mkdir()
 (folder/'Revit.exe').write_bytes(b'fixture exe')
 (folder/'worker.dll').write_bytes(str(year).encode())

def inspect(exe):
 path=Path(exe);year=int(path.parent.name)
 return dict(path=str(path),year=year,key=str(year),version=str(year-2000)+'.2.0.0')

with patch.object(runtime,'inspect_revit',side_effect=inspect),patch.object(runtime,'worker_path',side_effect=lambda p:Path(p.revit_exe).parent/'worker.dll'),patch('xivgate_revit_bridge.core.loader._payload',return_value=None),patch.object(runtime,'setup_plan',side_effect=AssertionError('Status must not install/extract loaders')):
 for year in (2024,2027):
  props.revit_exe=str(root/str(year)/'Revit.exe')
  props.addin_folder=str(root/'Addins'/str(year))
  state=runtime.connection_info(props)
  assert state['year']==year and not state['connected']
  assert not Path(state['plan']['path']).exists()
  install_manifest(state['plan'])
  assert runtime.connection_info(props)['connected']
  if year==2024:first=Path(state['plan']['path']).read_bytes()
  else:assert (root/'Addins/2024/XIVgate.RevitBridge.addin').read_bytes()==first
 props.revit_exe=str(root/'2027/Revit.exe')
 props.addin_folder=str(root/'Addins/2024')
 state=runtime.connection_info(props)
 assert Path(state['plan']['path']).parent.name=='2027'
 assert (root/'Addins/2024/XIVgate.RevitBridge.addin').read_bytes()==first
addon.unregister()
assert not (Path(os.environ['BLENDER_USER_CONFIG'])/'userpref.blend').exists()
print('REVIT_CONNECTION_UI_PASS',bpy.app.version_string)
