import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from tools.build_package import build


class PackageTests(unittest.TestCase):
    def test_package_manifest_and_versioned_worker_match_every_payload(self):
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as directory:
            root = Path(directory)
            worker = root / 'XIVgate.RevitBridge.Worker.dll'
            worker.write_bytes(b'explicit packaging fixture, not a Revit assembly')
            target = root / 'package.zip'
            build(worker, target)
            with zipfile.ZipFile(target) as archive:
                prefix = 'xivgate_revit_bridge/'
                manifest = json.loads(archive.read(prefix + 'PACKAGE_MANIFEST.json'))
                version = archive.read(prefix + 'VERSION').decode().strip()
                self.assertEqual(manifest['id'], 'xivgate_revit_bridge')
                self.assertEqual(manifest['version'], version)
                self.assertEqual(manifest['worker'], f'runtime/revit/2027/{version}/{worker.name}')
                files = {name[len(prefix):] for name in archive.namelist()}
                self.assertEqual(files, set(manifest['files']) | {'PACKAGE_MANIFEST.json'})
                for name, expected in manifest['files'].items():
                    self.assertEqual(hashlib.sha256(archive.read(prefix + name)).hexdigest(), expected)

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT']) as directory:
            root = Path(directory)
            worker = root / 'XIVgate.RevitBridge.Worker.dll'
            worker.write_bytes(b'test-only')
            target = root / 'existing.zip'
            target.write_bytes(b'preserve')
            with self.assertRaises(ValueError):
                build(worker, target)
            self.assertEqual(target.read_bytes(), b'preserve')
