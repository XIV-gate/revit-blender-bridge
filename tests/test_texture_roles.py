import unittest
from xivgate_revit_bridge.core.texture_roles import height_map_descriptor


class HeightTextureTests(unittest.TestCase):
    def material(self, amount=1):
        return {'textures': {'concrete_bump_map/0/unifiedbitmap_Bitmap': 'textures/height.png'},
                'properties': {'concrete_bump_amount': amount,
                               'concrete_bump_map/0/unifiedbitmap_Invert': False,
                               'concrete_bump_map/0/unifiedbitmap_RGBAmount': 0.865}}

    def test_active_height_uses_explicit_source_amount(self):
        descriptor = height_map_descriptor(self.material())
        self.assertTrue(descriptor['active'])
        self.assertEqual(descriptor['amount'], 1)
        self.assertEqual(descriptor['path'], 'textures/height.png')
        self.assertEqual(descriptor['rgb_amount'], 0.865)

    def test_zero_disabled_unknown_or_unsupported_amount_never_assumed(self):
        for amount in (0, -1, 2, None, float('nan'), True):
            with self.subTest(amount=amount):
                self.assertFalse(height_map_descriptor(self.material(amount))['active'])
        material = self.material()
        material['properties']['concrete_bump_toggle'] = False
        self.assertFalse(height_map_descriptor(material)['active'])

    def test_normal_map_and_ambiguous_height_maps_are_not_guessed(self):
        self.assertIsNone(height_map_descriptor({'textures': {'normal_map/0/unifiedbitmap_Bitmap': 'normal.png'}}))
        material = self.material()
        material['textures']['generic_bump_map/0/unifiedbitmap_Bitmap'] = 'other.png'
        self.assertFalse(height_map_descriptor(material)['active'])

    def test_explicit_bitmap_modes_are_conservatively_not_interpreted_as_height(self):
        for mode in (0, 1, 'normal', None):
            material = self.material()
            material['properties']['concrete_bump_map/0/unifiedbitmap_BumpmapType'] = mode
            with self.subTest(mode=mode):
                descriptor = height_map_descriptor(material)
                self.assertFalse(descriptor['active'])
                self.assertEqual(descriptor['reason'], 'unsupported_explicit_map_type')


if __name__ == '__main__':
    unittest.main()
