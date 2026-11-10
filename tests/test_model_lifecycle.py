import itertools
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from xivgate_revit_bridge.core.model_lifecycle import preparation_options, preparation_key, retain_texture
from xivgate_revit_bridge.core.jobs import JobSession

class ModelLifecycleTests(unittest.TestCase):
    def test_all_four_flags_remain_independent(self):
        keys = set()
        for overwrite, reuse, keep, pack in itertools.product((False, True), repeat=4):
            props = SimpleNamespace(overwrite_source=overwrite, reuse_prepared_model=reuse,
                                    keep_temporary_data=keep, pack_images=pack)
            self.assertEqual(preparation_options(props), dict(overwrite_source=overwrite,
                reuse_prepared_model=reuse, keep_temporary_data=keep))
            keys.add(preparation_key(props))
        self.assertEqual(len(keys), 8)

    def test_unpacked_texture_is_retained_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            job = JobSession.create(root)
            texture = job.directory / 'output' / 'wall.png'
            texture.write_bytes(b'texture bytes')
            target = retain_texture(root, texture)
            self.assertEqual(target.parent, root / 'textures')
            self.assertEqual(target.read_bytes(), b'texture bytes')
            self.assertEqual(texture.read_bytes(), b'texture bytes')
            self.assertEqual(retain_texture(root, texture), target)
            foreign = root / 'foreign.png'
            foreign.write_bytes(b'user file')
            with self.assertRaises(ValueError):
                retain_texture(root, foreign)
