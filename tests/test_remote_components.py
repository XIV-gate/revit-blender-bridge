import unittest
from unittest.mock import patch

import numpy as np

from xivgate_revit_bridge.core.remote_components import filter_remote_components


DTYPE = np.dtype([('vertices', '<f4', (3, 8)), ('material', '<i8')])
IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]


def triangle(offset=0):
    result = np.zeros(1, dtype=DTYPE)
    result['vertices'][0, :, :3] = [[offset, 0, 0], [offset + 1, 0, 0], [offset, 1, 0]]
    result['vertices'][:, :, 3:6] = [-0., 0, 1]
    result['vertices'][:, :, 6:8] = [[-.0, .125], [.5, .25], [1, 1]]
    return result


def floor_records(remote=1600):
    records = np.concatenate((triangle(remote), triangle(), triangle()))
    records['vertices'][2, :, :3] = [[1, 0, 0], [1, 1, 0], [0, 1, 0]]
    records['material'] = [41, -1, 2**55 + 7]
    return records


def fixture(records=None):
    arrays = {1: triangle(), 2: floor_records() if records is None else records}
    occurrences = [dict(id='anchor-' + str(i), prototype=1, category='Windows', transform=IDENTITY[:])
                   for i in range(20)]
    occurrences.append(dict(id='floor', prototype=2, category='Перекрытия', transform=IDENTITY[:]))
    return occurrences, arrays


class RemoteComponentsTests(unittest.TestCase):
    def test_removes_whole_remote_component_preserving_source_and_kept_record_bits(self):
        rows, arrays = fixture()
        before = arrays[2].tobytes()
        arrays[2].flags.writeable = False
        result, report = filter_remote_components(rows, arrays)
        self.assertIsNot(result, arrays)
        self.assertIs(result[1], arrays[1])
        self.assertEqual(result[2].tobytes(), arrays[2][[1, 2]].tobytes())
        self.assertEqual(arrays[2].tobytes(), before)
        self.assertEqual(report['removed_triangles'], 1)
        self.assertEqual(report['removed_components'], 1)
        self.assertEqual(report['filtered_elements'], 1)
        detail = report['elements'][0]
        self.assertEqual((detail['id'], detail['prototype']), ('floor', 2))
        self.assertEqual(detail['kept_triangles'], 2)
        np.testing.assert_array_equal(detail['bbox_min'], [0, 0, 0])
        np.testing.assert_array_equal(detail['bbox_max'], [1, 1, 0])
        self.assertEqual(detail['distance'], 1599)

    def test_missing_anchors_all_far_near_parts_and_other_categories_do_not_change_mapping(self):
        for mode in ('missing', 'all-far', 'near', 'wall', 'mass', 'connected'):
            with self.subTest(mode=mode):
                rows, arrays = fixture()
                if mode == 'missing': rows.pop(0)
                if mode == 'all-far': arrays[2] = np.concatenate((triangle(1500), triangle(1600)))
                if mode == 'near': arrays[2] = floor_records(50)
                if mode == 'wall': rows[-1]['category'] = 'Стены'
                if mode == 'mass': rows[-1]['category'] = 'Mass'
                if mode == 'connected':
                    # A long branch joins the far face; never clip a connected object.
                    arrays[2]['vertices'][1, 1, :3] = arrays[2]['vertices'][0, 0, :3]
                result, report = filter_remote_components(rows, arrays)
                self.assertIs(result, arrays)
                self.assertEqual(report['elements'], [])

    def test_union_of_buildings_and_shared_prototype_keep_geometry(self):
        rows, arrays = fixture()
        other_building = dict(id='building-2', prototype=1, category='Curtain Wall Mullions',
                              transform=IDENTITY[:9] + [1600, 0, 0])
        result, report = filter_remote_components(rows + [other_building], arrays)
        self.assertIs(result, arrays)
        shared = dict(id='other-floor', prototype=2, category='Floors', transform=IDENTITY[:])
        result, report = filter_remote_components(rows + [shared], arrays)
        self.assertIs(result, arrays)
        self.assertGreater(report['skipped_shared'], 0)

    def test_world_transform_cap_and_invalid_geometry_are_conservative(self):
        rows, arrays = fixture()
        rows[-1]['category'] = 'Floor'
        rows[-1]['transform'] = [0, -2, 0, 3, 0, 0, 0, 0, -1, 0, 0, 0]
        result, report = filter_remote_components(rows, arrays)
        self.assertEqual(len(result[2]), 2)
        np.testing.assert_array_equal(report['elements'][0]['bbox_min'], [0, -2, 0])
        np.testing.assert_array_equal(report['elements'][0]['bbox_max'], [3, 0, 0])
        with patch('xivgate_revit_bridge.core.remote_components.MAX_FLOOR_TRIANGLES', 2):
            result, report = filter_remote_components(rows, arrays)
        self.assertIs(result, arrays)
        self.assertEqual(report['skipped_large'], 1)
        rows[-1]['transform'][-1] = float('nan')
        result, report = filter_remote_components(rows, arrays)
        self.assertIs(result, arrays)
        self.assertGreater(report['skipped_invalid'], 0)

    def test_micron_weld_keeps_remote_face_connected_to_nearby_branch(self):
        rows, arrays = fixture()
        arrays[2] = np.concatenate((triangle(), triangle(-1600)))
        arrays[2]['vertices'][1, 0, :3] = [1e-7, 0, 0]
        rows[-1]['transform'] = IDENTITY[:9] + [1600, 0, 0]
        result, report = filter_remote_components(rows, arrays)
        self.assertIs(result, arrays)
        self.assertEqual(report['elements'], [])

    def test_invalid_anchor_bounds_do_not_count_towards_minimum(self):
        rows, arrays = fixture()
        rows[0]['transform'][-1] = float('nan')
        result, report = filter_remote_components(rows, arrays)
        self.assertIs(result, arrays)
        self.assertEqual(report['elements'], [])


if __name__ == '__main__':
    unittest.main()
