import unittest

import numpy as np

from xivgate_revit_bridge.core.instance_frames import center_prototypes, compensated_transform


DTYPE = np.dtype([('vertices', '<f4', (3, 8)), ('material', '<i8')])


def records(offset=(0, 0, 0)):
    result = np.zeros(2, dtype=DTYPE)
    result['vertices'][:, :, :3] = [[-.125, -.25, -.5], [.125, .25, .5], [0, .25, -.5]]
    result['vertices'][:, :, :3] += offset
    result['vertices'][:, :, 3:6] = [-0., 1., .25]
    result['vertices'][:, :, 6:8] = [[.125, -.0], [.5, .25], [1, 1]]
    result['material'] = [2**55 + 7, -1]
    return result


class InstanceFramesTests(unittest.TestCase):
    def test_center_translated_columns_and_doors_without_touching_other_fields(self):
        arrays = {1: records((3.900000572, 0, 0)), 'door': records((.2, 1.25, -2))}
        before = {key: value.tobytes() for key, value in arrays.items()}
        for value in arrays.values():
            value.flags.writeable = False
        centered, centers = center_prototypes(arrays)
        self.assertEqual(set(centered), set(arrays))
        for key, original in arrays.items():
            source = original['vertices'][:, :, :3].astype(np.float64)
            expected_center = (source.min(axis=(0, 1)) + source.max(axis=(0, 1))) / 2
            np.testing.assert_array_equal(centers[key], expected_center)
            self.assertEqual(centers[key].dtype, np.dtype('float64'))
            expected = (source - expected_center).astype('<f4')
            np.testing.assert_array_equal(centered[key]['vertices'][:, :, :3], expected)
            self.assertFalse(np.shares_memory(centered[key], original))
            self.assertEqual(original.tobytes(), before[key])
            for field, values in (('normals', (3, 6)), ('uv', (6, 8))):
                self.assertEqual(centered[key]['vertices'][:, :, values[0]:values[1]].tobytes(),
                                 original['vertices'][:, :, values[0]:values[1]].tobytes(), field)
            self.assertEqual(centered[key]['material'].tobytes(), original['material'].tobytes())

    def test_centered_or_empty_arrays_are_shared_without_allocation(self):
        arrays = {1: records(), 2: np.empty(0, dtype=DTYPE)}
        result, centers = center_prototypes(arrays)
        for key in arrays:
            self.assertIs(result[key], arrays[key])
            np.testing.assert_array_equal(centers[key], np.zeros(3))
        self.assertEqual(center_prototypes({}), ({}, {}))

    def test_compensation_preserves_world_mapping_rotation_mirror_and_nonuniform_scale(self):
        basis = np.array([[0., -3., 0.], [2., 0., 0.], [0., 0., -.5]])
        original = basis.T.reshape(-1).tolist() + [100., -200., 30.]
        source_center, anchor_center = [3.900000572, .2, -5.], [.125, 1., 2.]
        for signs in ([1, 1, 1], [-1, 1, 1], [-1, -1, -1]):
            original_before = original[:]
            source_before, anchor_before, signs_before = source_center[:], anchor_center[:], signs[:]
            result = compensated_transform(original, source_center, anchor_center, signs)
            new_basis = np.array(result[:9]).reshape(3, 3).T
            for anchor_point in ([0., 0., 0.], [1., 2., 3.], [-5., .25, -9.]):
                source_point = np.array(source_center) + np.array(signs) * (np.array(anchor_point) - anchor_center)
                expected = basis @ source_point + original[9:]
                actual = new_basis @ anchor_point + result[9:]
                np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)
            np.testing.assert_array_equal(new_basis, basis @ np.diag(signs))
            self.assertEqual(original, original_before)
            self.assertEqual((source_center, anchor_center, signs), (source_before, anchor_before, signs_before))

    def test_identity_compensation_keeps_original_transform_values(self):
        original = [1., 0., 0., 0., 1., 0., 0., 0., 1., 10., 20., 30.]
        self.assertEqual(compensated_transform(original, [.2, 3, -1], [.2, 3, -1], [1, 1, 1]), original)

    def test_optional_positive_scales_compose_with_mirror_without_changing_old_default(self):
        original = [1.,0,0,0,2.,0,0,0,3.,10.,20.,30.]
        result = compensated_transform(original,[1,2,3],[.5,1,1],[-1,1,1],scales=[2,3,.5])
        self.assertEqual(result[:9],[-2.,0,0,0,6.,0,0,0,1.5])
        self.assertEqual(result[9:],[12.,18.,37.5])
        for scale in ([0,1,1],[-1,1,1],[1,float('nan'),1],[1,1]):
            with self.assertRaises(ValueError):compensated_transform(original,[0]*3,[0]*3,[1]*3,scales=scale)

    def test_nonfinite_vertices_normals_or_uv_are_rejected(self):
        for field in (0, 3, 6):
            for value in (np.nan, np.inf, -np.inf):
                array = records()
                array['vertices'][0, 0, field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    center_prototypes({1: array})

    def test_compensation_rejects_nonfinite_wrong_shape_and_invalid_signs(self):
        identity = [1., 0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0.]
        valid = [identity, [0, 0, 0], [0, 0, 0], [1, 1, 1]]
        for index, value in ((0, [1]*11), (0, identity[:11] + [np.inf]),
                             (1, [0, np.nan, 0]), (2, [0, 0]), (3, [1, 0, 1]),
                             (3, [1, -2, 1]), (3, [1, np.inf, 1])):
            args = valid[:]
            args[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                compensated_transform(*args)

    def test_invalid_record_shapes_and_dtypes_are_rejected(self):
        invalid = (np.empty((1, 1), dtype=DTYPE), np.empty(1, dtype='<f4'),
                   np.zeros(1, dtype=[('vertices', '<f8', (3, 8)), ('material', '<i8')]),
                   np.zeros(1, dtype=[('vertices', '<f4', (3, 8)), ('material', '<i4')]))
        for array in invalid:
            with self.subTest(dtype=array.dtype), self.assertRaises(ValueError):
                center_prototypes({1: array})

    def test_compensation_accepts_readonly_arrays_and_rejects_result_overflow(self):
        inputs = [np.array(value, dtype=np.float64) for value in
                  ([1, 0, 0, 0, 1, 0, 0, 0, 1, 10, 20, 30], [.2, 0, 0], [0, 0, 0], [-1, 1, 1])]
        before = [array.tobytes() for array in inputs]
        for array in inputs:
            array.flags.writeable = False
        result = compensated_transform(*inputs)
        self.assertEqual(result[0], -1)
        self.assertAlmostEqual(result[9], 10.2)
        self.assertEqual([array.tobytes() for array in inputs], before)
        with self.assertRaises(ValueError):
            compensated_transform([1e308, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0],
                                  [1e308, 0, 0], [0, 0, 0], [1, 1, 1])


if __name__ == '__main__':
    unittest.main()
