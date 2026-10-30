import copy
import math
import subprocess
import sys
import unittest
from unittest.mock import patch

import numpy as np

from xivgate_revit_bridge.core.instance_matching import plan_instances


DTYPE = np.dtype([('vertices', '<f4', (3, 8)), ('material', '<i8')])


def mesh(delta=0, triangles=1, material=7):
    records = np.zeros(triangles, dtype=DTYPE)
    records['vertices'][:, :, :3] = [[0, 0, 0], [1, 0, 0], [0, 1, 1]]
    records['vertices'][:, :, 0] += delta
    records['vertices'][:, :, 5] = 1
    records['vertices'][:, :, 6:8] = [[0, 0], [1, 0], [0, 1]]
    records['material'] = material
    return records


def occurrence(identifier, prototype, **values):
    return dict(id=str(identifier), prototype=prototype, type_id='chair',
                native_instance_key='symbol:chair', native_instance_safe=True, **values)


class InstanceMatchingTests(unittest.TestCase):
    def plan(self, records, occurrences=None, materials=None, **options):
        return plan_instances(occurrences or [occurrence(key, key) for key in records], records,
                              {7: {'uv_sensitive': False}} if materials is None else materials,
                              **options)

    def test_native_identity_allows_tessellation_and_uv_difference(self):
        first, second = mesh(), mesh(0.00005, triangles=2)
        second['vertices'][:, :, 6:8] += 0.2
        result = self.plan({2: second, 1: first})
        self.assertEqual(result['redirects'], {'1': 1, '2': 1})
        self.assertEqual(result['representatives'], {1})
        self.assertEqual(result['statistics']['native_merges'], 1)
        self.assertEqual(self.plan({1: first, 2: second}, use_revit=False)['representatives'], {1, 2})

    def test_native_requires_safe_key_type_material_and_bounds(self):
        for change in ({'native_instance_key': 'different'}, {'native_instance_safe': False},
                       {'native_instance_safe': 'true'}, {'native_instance_key': ''}, {'type_id': 'other'}):
            with self.subTest(change=change):
                a, b = occurrence(1, 1), occurrence(2, 2)
                b.update(change)
                self.assertEqual(self.plan({1: mesh(), 2: mesh(triangles=2)}, [a, b])['representatives'], {1, 2})
        self.assertEqual(self.plan({1: mesh(), 2: mesh(0.001, triangles=2)})['representatives'], {1, 2})
        self.assertEqual(self.plan({1: mesh(), 2: mesh(triangles=2, material=8)},
                                  materials={7: {}, 8: {}})['representatives'], {1, 2})
        for axis in (1, 2):
            different = mesh(triangles=2)
            different['vertices'][0, 2, axis] += 0.001
            self.assertEqual(self.plan({1: mesh(), 2: different})['representatives'], {1, 2})

    def test_textured_missing_texture_and_multi_material_never_native_merge(self):
        for material in ({'uv_sensitive': True}, {'base_texture': 'textures/a.png'},
                         {'textures': [{'path': 'missing.png', 'found': False}]}):
            self.assertEqual(self.plan({1: mesh(), 2: mesh(triangles=2)},
                                      materials={'7': material})['representatives'], {1, 2})
        a, b = mesh(triangles=2), mesh(triangles=3)
        a['material'][1] = b['material'][1] = 8
        self.assertEqual(self.plan({1: a, 2: b}, materials={7: {}, 8: {}})['representatives'], {1, 2})

    def test_fallback_uses_coordinates_normals_uv_and_order(self):
        original = mesh()
        for part, difference, merged in ((0, 0.00005, True), (0, 0.0002, False),
                                         (3, 0.0000005, True), (3, 0.000002, False),
                                         (6, 0.000000005, True), (6, 0.00000002, False)):
            with self.subTest(part=part, difference=difference):
                other = original.copy()
                other['vertices'][0, 0, part] += difference
                result = self.plan({1: original, 2: other}, use_revit=False)
                self.assertEqual(len(result['representatives']), 1 if merged else 2)
        permuted = original.copy()
        permuted['vertices'] = original['vertices'][:, ::-1]
        self.assertEqual(self.plan({1: original, 2: permuted}, use_revit=False)['representatives'], {1, 2})

    def test_fallback_material_sequence_is_exact(self):
        a, b = mesh(triangles=2), mesh(triangles=2)
        a['material'] = [7, 8]
        b['material'] = [8, 7]
        self.assertEqual(self.plan({1: a, 2: b}, use_revit=False)['representatives'], {1, 2})

    def test_safe_false_can_still_match_real_arrays(self):
        occurrences = [occurrence(1, 1), occurrence(2, 2)]
        occurrences[1]['native_instance_safe'] = False
        result = self.plan({1: mesh(), 2: mesh(0.00001)}, occurrences)
        self.assertEqual(result['representatives'], {1})
        self.assertEqual(result['statistics']['native_merges'], 0)
        self.assertEqual(result['statistics']['tolerant_merges'], 1)

    def test_missing_or_invalid_type_never_groups_distinct_prototypes(self):
        for type_id in (None, '', '-1', -1, '   '):
            rows = [dict(occurrence(1, 1), type_id=type_id), dict(occurrence(2, 2), type_id=type_id)]
            self.assertEqual(self.plan({1: mesh(), 2: mesh()}, rows)['representatives'], {1, 2})

    def test_distinct_valid_types_do_not_match_even_identical_arrays(self):
        rows = [occurrence(1, 1), dict(occurrence(2, 2), type_id='other')]
        self.assertEqual(self.plan({1: mesh(), 2: mesh()}, rows)['representatives'], {1, 2})

    def test_missing_material_metadata_disables_native_shortcut(self):
        self.assertEqual(self.plan({1: mesh(), 2: mesh(triangles=2)}, materials={})['representatives'], {1, 2})

    def test_old_material_list_supported(self):
        self.assertEqual(self.plan({1: mesh(), 2: mesh(triangles=2)},
                                  materials=[{'id': 7}])['representatives'], {1})

    def test_zero_is_exact_fallback_but_native_can_remain_authoritative(self):
        self.assertEqual(self.plan({1: mesh(), 2: mesh(0.000001)}, tolerance_mm=0,
                                  use_revit=False)['representatives'], {1, 2})
        self.assertEqual(self.plan({1: mesh(), 2: mesh()}, tolerance_mm=0,
                                  use_revit=False)['representatives'], {1})
        self.assertEqual(self.plan({1: mesh(), 2: mesh(triangles=2)}, tolerance_mm=0)['representatives'], {1})

    def test_anchor_prevents_transitive_chain(self):
        for native in (True, False):
            result = self.plan({3: mesh(0.00018), 2: mesh(0.00009), 1: mesh()}, use_revit=native)
            self.assertEqual(result['redirects'], {'1': 1, '2': 1, '3': 3})

    def test_lower_tolerance_restores_groups_without_mutating_input_or_mirror(self):
        records = {1: mesh(), 2: mesh(0.00005)}
        occurrences = [occurrence(2, 2), occurrence(1, 1)]
        occurrences[0]['transform'] = [-1, 0, 0, 0, 1, 0, 0, 0, 1, 5, 0, 0]
        before = copy.deepcopy((records, occurrences))
        self.assertEqual(len(self.plan(records, occurrences)['representatives']), 1)
        self.assertEqual(len(self.plan(records, occurrences, tolerance_mm=0.01)['representatives']), 2)
        self.assertEqual(occurrences, before[1])
        for key in records:
            np.testing.assert_array_equal(records[key], before[0][key])

    def test_raw_sharing_across_types_and_deterministic_numeric_representatives(self):
        occurrences = [occurrence('z', 10), occurrence('a', 2)]
        occurrences += [dict(occurrence('b', 2), type_id='other')]
        records = {10: mesh(), 2: mesh()}
        result = self.plan(records, occurrences)
        self.assertEqual(result['redirects'], {'a': 2, 'b': 2, 'z': 2})
        self.assertEqual(result['statistics']['exact_reuses'], 1)
        self.assertEqual(result, self.plan(dict(reversed(list(records.items()))), list(reversed(occurrences))))

    def test_bounds_prune_large_unrelated_same_type_group(self):
        records = {key: mesh(key * 0.01) for key in range(400)}
        result = self.plan(records)
        self.assertEqual(len(result['representatives']), 400)
        self.assertEqual(result['statistics']['native_comparisons'], 0)
        self.assertEqual(result['statistics']['fallback_comparisons'], 0)

    def test_same_bounds_uv_variants_have_bounded_comparisons(self):
        records = {}
        for key in range(520):
            variant = mesh()
            variant['vertices'][:, :, 6] += key * 0.001
            records[key] = variant
        result = self.plan(records, use_revit=False)
        self.assertEqual(len(result['representatives']), len(records))
        self.assertLessEqual(result['statistics']['fallback_comparisons'], len(records) * 64)
        self.assertGreater(result['statistics']['comparison_limit_hits'], 0)

    def test_matching_attributes_are_prioritized_past_unrelated_variants(self):
        records = {}
        for key in range(520):
            variant = mesh()
            variant['vertices'][:, :, 6] += key * 0.001
            records[key] = variant
        records[520] = records[400].copy()
        result = self.plan(records, use_revit=False)
        self.assertEqual(result['redirects']['520'], 400)
        self.assertEqual(len(result['representatives']), 520)
        self.assertLessEqual(result['statistics']['fallback_comparisons'], len(records) * 64)

    def test_surface_rotated_safe_family_reports_world_adjustment(self):
        first = mesh()
        first['vertices'][0, :, :3] = [[-.5, -1, 0], [.5, -1, 0], [0, 0, 0]]
        second = first.copy()
        second['vertices'][:, :, :3] *= [-1, -1, 1]
        second['vertices'][:, :, 6:8] += .2
        rows = [dict(occurrence(1, 1), family_name='Chair'),
                dict(occurrence(2, 2), family_name='Chair', native_instance_key='other')]
        before = second.copy()
        result = self.plan({1: first, 2: second}, rows)
        self.assertEqual(result['redirects']['2'], 1)
        self.assertEqual(result['adjustments']['2'], [-1, -1, 1])
        np.testing.assert_allclose(first['vertices'][0, :, :3] * result['adjustments']['2'],
                                   second['vertices'][0, :, :3])
        np.testing.assert_array_equal(second, before)
        # Later occurrences of the second native symbol must not inherit an
        # unrotated redirect from a polluted native-key index.
        third = second.copy()
        third['vertices'][:, :, 6:8] += .1
        rows.append(dict(occurrence(3, 3), family_name='Chair', native_instance_key='other'))
        result = self.plan({1: first, 2: second, 3: third}, rows)
        self.assertEqual(result['adjustments']['3'], [-1, -1, 1])

    def test_surface_tessellation_and_tolerance_remain_guarded(self):
        first = mesh()
        first['vertices'][0, :, :3] = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        second = mesh(triangles=2)
        second['vertices'][0, :, :3] = [[0, 0, .00005], [1, 0, .00005], [0, .5, .00005]]
        second['vertices'][1, :, :3] = [[0, .5, .00005], [1, 0, .00005], [0, 1, .00005]]
        rows = [dict(occurrence(1, 1), family_name='Chair'),
                dict(occurrence(2, 2), family_name='Chair', native_instance_key='other')]
        self.assertEqual(self.plan({1: first, 2: second}, rows)['representatives'], {1})
        for tolerance in (0, .01):
            self.assertEqual(self.plan({1: first, 2: second}, rows, tolerance_mm=tolerance)['representatives'], {1, 2})
        for change in ({'family_name': ''}, {'family_name': 'Different'}, {'native_instance_safe': False}):
            guarded = copy.deepcopy(rows)
            guarded[1].update(change)
            self.assertEqual(self.plan({1: first, 2: second}, guarded)['representatives'], {1, 2})
        self.assertEqual(self.plan({1: first, 2: second}, rows, materials={7: {'base_texture': 'a.png'}})['representatives'], {1, 2})
        opposite = second.copy()
        opposite['vertices'][:, :, 5] = -1
        self.assertEqual(self.plan({1: first, 2: opposite}, rows)['representatives'], {1, 2})
        self.assertEqual(self.plan({1: first, 2: second}, rows, use_revit=False)['representatives'], {1, 2})

    def test_surface_check_is_bidirectional_and_does_not_fill_missing_faces(self):
        square = mesh(triangles=2)
        square['vertices'][0, :, :3] = [[0, 0, 0], [1, 0, 0], [1, 1, 0]]
        square['vertices'][1, :, :3] = [[0, 0, 0], [1, 1, 0], [0, 1, 0]]
        half = square[:1].copy()
        rows = [dict(occurrence(1, 1), family_name='Panel'),
                dict(occurrence(2, 2), family_name='Panel', native_instance_key='other')]
        self.assertEqual(self.plan({1: square, 2: half}, rows)['representatives'], {1, 2})

    def test_surface_hard_edge_uses_nearby_matching_normal_not_nearest_other_face(self):
        from xivgate_revit_bridge.core.instance_surface import surface_matches
        first = mesh(triangles=3)['vertices'].copy()
        first[0, :, :3] = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        first[1, :, :3] = [[0, 0, -1], [0, 1, -1], [0, 1, 1]]
        first[2, :, :3] = [[0, 0, -1], [0, 1, 1], [0, 0, 1]]
        first[1:, :, 3:6] = [1, 0, 0]
        second = first.copy()
        second[0, :, 2] += .00005
        before = second.copy()
        # At the horizontal edge the perpendicular face is distance zero,
        # while the corresponding horizontal surface is only 50 microns away.
        self.assertTrue(surface_matches(first, second, (1, 1, 1), .0001))
        self.assertFalse(surface_matches(first, second, (1, 1, 1), .00001))
        opposite = second.copy()
        opposite[0, :, 3:6] *= -1
        self.assertFalse(surface_matches(first, opposite, (1, 1, 1), .0001))
        np.testing.assert_array_equal(second, before)

    def test_surface_work_is_bounded_and_multi_material_and_large_meshes_skip(self):
        records, rows = {}, []
        for key in range(7):
            records[key] = mesh()
            angle = math.radians(key * 20)
            records[key]['vertices'][:, :, 3:6] = [math.sin(angle), 0, math.cos(angle)]
            rows.append(dict(occurrence(key, key), family_name='Chair', native_instance_key='symbol:' + str(key)))
        result = self.plan(records, rows)
        self.assertEqual(len(result['representatives']), 7)
        self.assertLessEqual(result['statistics']['surface_comparisons'], len(records) * 4)
        self.assertGreater(result['statistics']['surface_limit_hits'], 0)
        first, second = mesh(triangles=2049), mesh(triangles=2049)
        second['vertices'][:, :, 3] += .001
        rows = [dict(occurrence(1, 1), family_name='Chair'),
                dict(occurrence(2, 2), family_name='Chair', native_instance_key='other')]
        with patch('xivgate_revit_bridge.core.instance_surface.surface_matches', side_effect=AssertionError('Must skip')):
            self.assertEqual(self.plan({1: first, 2: second}, rows)['representatives'], {1, 2})
            first, second = mesh(triangles=2), mesh(triangles=3)
            first['material'][1] = second['material'][1] = 8
            self.assertEqual(self.plan({1: first, 2: second}, rows, materials={7: {}, 8: {}})['representatives'], {1, 2})

    def test_invalid_tolerances_and_empty_input(self):
        for tolerance in (-1, math.nan, math.inf, -math.inf, 1001, True, None):
            with self.subTest(tolerance=tolerance), self.assertRaises(ValueError):
                self.plan({}, tolerance_mm=tolerance)
        result = plan_instances([], {}, {}, tolerance_mm=0)
        self.assertEqual(result['redirects'], {})
        self.assertEqual(result['representatives'], set())

    def test_module_import_does_not_import_numpy(self):
        result = subprocess.run([sys.executable, '-B', '-c',
                                 'import sys; import xivgate_revit_bridge.core.instance_matching; '
                                 'assert "numpy" not in sys.modules'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
