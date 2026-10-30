import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from xivgate_revit_bridge.core.loader import ensure_loader, launch_environment
from xivgate_revit_bridge.core.installer import manifest_plan, install_manifest, manifest_matches


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['XIVGATE_TEST_ROOT'])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'core').mkdir()
        self.data = b'MZ' + b'test-only stable loader'
        self.payload = {'schema': 1, 'abi': 1, 'loaders': {'2027': {
            'sha256': hashlib.sha256(self.data).hexdigest(),
            'data': base64.b64encode(self.data).decode()}}}

    def write_payload(self):
        path = self.root / 'core/loader_payload.py'
        path.write_text('PAYLOAD = ' + repr(self.payload), encoding='utf-8')
        (self.root / 'PACKAGE_MANIFEST.json').write_text(json.dumps(dict(schema=1,
            id='xivgate_revit_bridge', version='0.4.8', files={
                'core/loader_payload.py': hashlib.sha256(path.read_bytes()).hexdigest()},
            loader_payloads={'abi': 1, 'sha256': {k: v['sha256'] for k, v in self.payload['loaders'].items()}})))

    def worker(self, version):
        name = f'runtime/revit/2027/{version}/XIVgate.RevitBridge.Worker.dll'
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('MZ test-only worker ' + version).encode())
        (self.root / 'PACKAGE_MANIFEST.json').write_text(json.dumps(dict(schema=1,
            id='xivgate_revit_bridge', version=version, workers={'2027': name},
            files={name: hashlib.sha256(path.read_bytes()).hexdigest(),
                   'core/loader_payload.py': hashlib.sha256((self.root / 'core/loader_payload.py').read_bytes()).hexdigest()},
            loader_payloads={'abi': 1, 'sha256': {k: v['sha256'] for k, v in self.payload['loaders'].items()}})))
        return path

    def test_old_packages_have_no_loader_side_effects(self):
        self.assertIsNone(ensure_loader(self.root, '2027'))
        self.assertEqual(launch_environment(self.root, '2027', '0.4.7'), {})
        self.assertFalse((self.root / 'runtime').exists())

    def test_worker_updates_reuse_identical_loader_without_rewriting(self):
        self.write_payload()
        self.worker('0.4.8')
        first = ensure_loader(self.root, '2027')
        before = first.stat().st_mtime_ns
        with patch('os.link', side_effect=AssertionError('existing loader must not be rewritten')):
            self.worker('0.4.9')
            self.assertEqual(first, ensure_loader(self.root, '2027'))
        self.assertEqual(first.read_bytes(), self.data)
        self.assertEqual(before, first.stat().st_mtime_ns)
        env = launch_environment(self.root, '2027', '0.4.9')
        self.assertEqual(env['XIVGATE_RBB_WORKER_VERSION'], '0.4.9')
        self.assertEqual(env['XIVGATE_RBB_WORKER_SHA256'], hashlib.sha256(self.worker_bytes('0.4.9')).hexdigest())

    @staticmethod
    def worker_bytes(version):
        return ('MZ test-only worker ' + version).encode()

    def test_bad_payload_never_creates_loader(self):
        self.payload['loaders']['2027']['sha256'] = '0' * 64
        self.write_payload()
        with self.assertRaises(ValueError): ensure_loader(self.root, '2027')
        self.assertFalse((self.root / 'runtime').exists())

    def test_changed_resource_is_rejected_before_any_extraction(self):
        self.write_payload()
        path = self.root / 'core/loader_payload.py'
        path.write_text(path.read_text() + '\n# changed after installation', encoding='utf-8')
        with self.assertRaises(ValueError): ensure_loader(self.root, '2027')
        self.assertFalse((self.root / 'runtime').exists())

    def test_missing_declared_payload_cannot_fall_back_to_direct_worker(self):
        self.write_payload()
        self.worker('0.4.8')
        (self.root / 'core/loader_payload.py').unlink()
        with self.assertRaises(ValueError): ensure_loader(self.root, '2027')
        with self.assertRaises(ValueError): launch_environment(self.root, '2027', '0.4.8')

    def test_legacy_manifest_without_payload_keeps_old_route(self):
        (self.root / 'PACKAGE_MANIFEST.json').write_text(json.dumps(dict(
            schema=1, id='xivgate_revit_bridge', version='0.4.7', files={})))
        self.assertIsNone(ensure_loader(self.root, '2027'))
        self.assertEqual(launch_environment(self.root, '2027', '0.4.7'), {})

    def test_existing_loader_is_never_silently_replaced(self):
        self.write_payload()
        target = ensure_loader(self.root, '2027')
        target.write_bytes(b'foreign bytes')
        with self.assertRaises(ValueError): ensure_loader(self.root, '2027')
        self.assertEqual(target.read_bytes(), b'foreign bytes')

    def test_same_loader_bytes_at_another_location_are_not_a_matching_entry(self):
        self.write_payload()
        first = ensure_loader(self.root, '2027')
        other = self.root / 'other/XIVgate.RevitBridge.Loader.dll'
        other.parent.mkdir()
        other.write_bytes(first.read_bytes())
        plan = manifest_plan('', first, appdata_root=self.root / 'profile')
        install_manifest(plan)
        self.assertTrue(manifest_matches(plan))
        different_root = manifest_plan('', other, appdata_root=self.root / 'profile')
        self.assertFalse(manifest_matches(different_root), 'Loader resolves workers relative to its own location')

    def test_corrupted_worker_and_wrong_version_are_rejected(self):
        self.write_payload()
        worker = self.worker('0.4.8')
        worker.write_bytes(b'changed after install')
        with self.assertRaises(ValueError): launch_environment(self.root, '2027', '0.4.8')
        with self.assertRaises(ValueError): launch_environment(self.root, '2027', '../0.4.8')
        with self.assertRaises(ValueError): launch_environment(self.root, '2027', '0.4.9')

    def test_payload_is_data_and_never_executed(self):
        (self.root / 'core/loader_payload.py').write_text("raise RuntimeError('must never execute')", encoding='utf-8')
        with self.assertRaises(ValueError): ensure_loader(self.root, '2027')

    def test_unsupported_key_and_abi_are_rejected(self):
        self.write_payload()
        with self.assertRaises(ValueError): ensure_loader(self.root, '../2027')
        with self.assertRaises(ValueError): ensure_loader(self.root, '2026')
        self.payload['abi'] = 999
        self.write_payload()
        with self.assertRaises(ValueError): ensure_loader(self.root, '2027')

    def test_publish_race_preserves_other_file(self):
        self.write_payload()
        def race(source, target):
            Path(target).write_bytes(b'racing foreign file')
            raise FileExistsError()
        with patch('os.link', side_effect=race):
            with self.assertRaises(ValueError): ensure_loader(self.root, '2027')
        target = self.root / 'runtime/revit/2027/loader/1/XIVgate.RevitBridge.Loader.dll'
        self.assertEqual(target.read_bytes(), b'racing foreign file')
        self.assertFalse(list(target.parent.glob('*.pending')))
