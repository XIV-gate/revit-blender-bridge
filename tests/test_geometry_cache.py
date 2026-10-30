import hashlib
import math
import struct
import unittest

from xivgate_revit_bridge.geometry_cache import validate_prototype, matrix_rows, safe_asset_path


class CacheValidationTests(unittest.TestCase):
    def test_triangle_layout_is_checked_before_import(self):
        data = struct.pack('<24fq', *range(24), 42)
        validate_prototype({'triangles': 1, 'bytes': 104, 'sha256': hashlib.sha256(data).hexdigest()}, data)

    def test_truncated_geometry_is_not_accepted(self):
        with self.assertRaises(ValueError):
            validate_prototype({'triangles': 1, 'bytes': 103}, b'\0' * 103)

    def test_corrupt_geometry_is_not_accepted(self):
        with self.assertRaises(ValueError):
            validate_prototype({'triangles': 1, 'bytes': 104, 'sha256': 'bad'}, b'\0' * 104)

    def test_transform_translation_is_not_scaled_twice(self):
        self.assertEqual(matrix_rows([1, 0, 0, 0, 1, 0, 0, 0, 1, 10, 20, 30])[3], (10, 20, 30, 1))

    def test_nonfinite_transform_is_rejected(self):
        with self.assertRaises(ValueError):
            matrix_rows([1] * 11 + [math.nan])

    def test_texture_cannot_escape_export_directory(self):
        with self.assertRaises(ValueError):
            safe_asset_path('export', '../outside.png')


if __name__ == '__main__':
    unittest.main()
