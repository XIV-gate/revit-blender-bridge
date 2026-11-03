import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xivgate_revit_bridge import convert_cli


class ConversionProgressTests(unittest.TestCase):
    def test_locked_optional_progress_file_never_fails_geometry(self):
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT'])as directory:
            root=Path(directory);response=root/'response.json'
            def convert(*args,**kwargs):
                kwargs['progress_callback']({'message':'Working','progress':.5})
                return {'placements':3}
            args=['convert','--manifest',str(root/'manifest.json'),'--target',str(root/'scene.usdc'),
                  '--response',str(response),'--progress',str(root/'progress.json')]
            with patch('sys.argv',args),patch.object(convert_cli,'build_usd',side_effect=convert),\
                    patch('xivgate_revit_bridge.core.jobs.atomic_write_json',side_effect=PermissionError('Windows reader lock')):
                self.assertEqual(convert_cli.main(),0)
            self.assertTrue(json.loads(response.read_text(encoding='utf-8'))['success'])
