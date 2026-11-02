import copy
import unittest
from unittest.mock import patch

import numpy as np

from xivgate_revit_bridge.core.affine_instances import plan_affine_instances
from xivgate_revit_bridge.core.instance_frames import compensated_transform


DTYPE = np.dtype([('vertices', '<f4', (3, 8)), ('material', '<i8')])


def box(dimensions=(1, 1, 1), center=(0, 0, 0)):
    corners = np.array([[-1,-1,-1], [1,-1,-1], [1,1,-1], [-1,1,-1],
                        [-1,-1,1], [1,-1,1], [1,1,1], [-1,1,1]], dtype=np.float64) / 2
    faces = ((0,3,2,1), (4,5,6,7), (0,1,5,4), (3,7,6,2), (0,4,7,3), (1,2,6,5))
    result = np.zeros(12, dtype=DTYPE)
    for index, (a,b,c,d) in enumerate(faces):
        normal = np.cross(corners[b]-corners[a], corners[c]-corners[a])
        normal /= np.linalg.norm(normal)
        for offset, triangle in enumerate(((a,b,c), (a,c,d))):
            result['vertices'][index*2+offset,:,:3] = corners[list(triangle)] * dimensions + center
            result['vertices'][index*2+offset,:,3:6] = normal
            result['vertices'][index*2+offset,:,6:8] = [[0,0], [1,0], [1,1]]
    result['material'] = 7
    return result


def occurrence(identifier, prototype, type_id='panel'):
    return dict(id=str(identifier), prototype=prototype, type_id=type_id)


class AffineInstanceTests(unittest.TestCase):
    def plan(self, arrays, rows=None, materials=None, **options):
        return plan_affine_instances(rows or [occurrence(key,key) for key in arrays], arrays,
                                     {7: {'uv_sensitive': False}} if materials is None else materials, **options)

    def test_different_dimensions_share_anchor_with_all_member_occurrences(self):
        arrays = {10: box((2, .035, 3.19), (1,2,3)), 2: box((4,.035,2))}
        rows = [occurrence(20,10), occurrence(21,10), occurrence(1,2)]
        result = self.plan(arrays, rows)
        self.assertEqual(result['redirects'], {'1':2,'20':2,'21':2})
        np.testing.assert_allclose(result['scales']['20'], [.5,1,1.595], rtol=1e-6)
        self.assertEqual(result['scales']['1'], [1.,1.,1.])
        self.assertEqual(result['statistics']['groups'], 1)
        self.assertEqual(result, self.plan(dict(reversed(list(arrays.items()))), list(reversed(rows))))

    def test_only_repeated_occurrences_of_one_raw_prototype_are_not_an_affine_group(self):
        result = self.plan({2:box()}, [occurrence(1,2),occurrence(2,2)])
        self.assertEqual(result['redirects'], {})
        self.assertEqual(result['scales'], {})

    def test_type_material_winding_and_cuts_protect_distinct_geometry(self):
        first = box()
        variants = [box((2,1,1)), box((2,1,1)), box((2,1,1))]
        variants[0]['material'][1] = 8
        variants[1]['vertices'] = variants[1]['vertices'][:, [0,2,1]]
        variants[2]['vertices'][0,0,0] += .01  # A corner cut, not an affine box.
        for variant in variants:
            self.assertEqual(self.plan({1:first,2:variant})['redirects'], {})
        for kind in (None,'','-1',-1):
            self.assertEqual(self.plan({1:first,2:box()}, [occurrence(1,1,kind),occurrence(2,2,kind)])['redirects'], {})
        self.assertEqual(self.plan({1:first,2:box()}, [occurrence(1,1,'A'),occurrence(2,2,'B')])['redirects'], {})

    def test_cyclic_corner_and_triangle_reordering_preserves_topology_and_paired_attributes(self):
        first, second = box(), box((2,1,3))
        second['vertices'] = second['vertices'][:,[1,2,0]]
        second = second[::-1].copy()
        self.assertEqual(self.plan({1:first,2:second},materials={7:{'uv_sensitive':True}})['redirects'], {'1':1,'2':1})

    def test_uv_strict_for_textured_unknown_or_nonboolean_material_metadata(self):
        first, second = box(), box((2,1,3))
        second['vertices'][:,:,6] += .25
        for materials in ({7:{'uv_sensitive':True}}, {7:{}}, {}, {7:{'uv_sensitive':0}}):
            self.assertEqual(self.plan({1:first,2:second}, materials=materials)['redirects'], {})
        self.assertEqual(self.plan({1:first,2:second})['redirects'], {'1':1,'2':1})
        self.assertEqual(self.plan({1:first,2:box((2,1,3))}, materials={7:{}})['redirects'], {'1':1,'2':1})

    def test_normals_must_reconstruct_after_inverse_transpose(self):
        first, second = box(), box((2,1,3))
        second['vertices'][0,0,3:6] = [1,0,0]
        self.assertEqual(self.plan({1:first,2:second})['redirects'], {})

    def test_slanted_normals_use_inverse_transpose_instead_of_forward_scale(self):
        first = np.zeros(1,dtype=DTYPE)
        first['vertices'][0,:,:3] = [[0,0,0],[1,1,0],[0,0,1]]
        normal = np.array([1.,-1.,0.])/np.sqrt(2)
        first['vertices'][0,:,3:6] = normal
        first['material'] = 7
        second = first.copy()
        scale = np.array([2.,.5,3.])
        second['vertices'][0,:,:3] *= scale
        expected_normal = normal/scale
        expected_normal /= np.linalg.norm(expected_normal)
        second['vertices'][0,:,3:6] = expected_normal
        result = self.plan({1:first,2:second})
        self.assertEqual(result['redirects'],{'1':1,'2':1})
        np.testing.assert_allclose(result['scales']['2'],scale)
        second['vertices'][0,:,3:6] = normal
        self.assertEqual(self.plan({1:first,2:second})['redirects'],{})

    def test_material_labels_remain_tied_to_faces_after_canonical_sort(self):
        first, second = box(), box((2,1,3))
        first['material'][6:] = second['material'][6:] = 8
        materials={7:{'uv_sensitive':False},8:{'uv_sensitive':False}}
        reordered=second[::-1].copy()
        self.assertEqual(self.plan({1:first,2:reordered},materials=materials)['redirects'],{'1':1,'2':1})
        second['material'][0],second['material'][6]=second['material'][6],second['material'][0]
        self.assertEqual(self.plan({1:first,2:second},materials=materials)['redirects'],{})

    def test_large_user_tolerance_never_changes_shape_by_more_than_ten_micrometres(self):
        first, second = box(), box()
        second['vertices'][0,0,0] += .00003
        self.assertEqual(self.plan({1:first,2:second}, tolerance_mm=5)['redirects'], {})
        second = box()
        second['vertices'][0,0,0] += .000002
        self.assertEqual(self.plan({1:first,2:second}, tolerance_mm=.1)['redirects'], {'1':1,'2':1})
        self.assertEqual(self.plan({1:first,2:second}, tolerance_mm=0)['redirects'], {})
        self.assertEqual(self.plan({1:first,2:box((2,.25,3))}, tolerance_mm=0)['redirects'], {'1':1,'2':1})

    def test_anchor_prevents_error_chain_and_placement_scale_limits_world_error(self):
        arrays={index:box()for index in (1,2,3)}
        arrays[2]['vertices'][0,0,0] += .000006
        arrays[3]['vertices'][0,0,0] += .000012
        self.assertEqual(self.plan(arrays)['redirects'],{'1':1,'2':1})
        rows=[occurrence(1,1),occurrence(2,2)]
        rows[1]['transform']=[10,0,0,0,10,0,0,0,10,0,0,0]
        self.assertEqual(self.plan({1:arrays[1],2:arrays[2]},rows)['redirects'],{})

    def test_degenerate_shared_axis_gets_scale_one_but_mismatched_axis_is_rejected(self):
        first, second = box(), box((2,3,1))
        first['vertices'][:,:,2] = 2
        second['vertices'][:,:,2] = 5
        result = self.plan({1:first,2:second})
        self.assertEqual(result['scales']['2'], [2.,3.,1.])
        self.assertEqual(self.plan({1:first,2:box((2,3,1))})['redirects'], {})

    def test_world_mapping_rotation_mirror_nonuniform_placement_and_no_input_mutation(self):
        arrays = {1:box(),2:box((2,.25,3),(3.9,.2,-1))}
        before = {key:value.tobytes()for key,value in arrays.items()}
        rows = [occurrence(1,1),occurrence(2,2)]
        rows[1]['transform'] = [0,-2,0,-3,0,0,0,0,.5,100,-200,30]
        old = copy.deepcopy(rows)
        for value in arrays.values():value.flags.writeable=False
        result = self.plan(arrays, rows)
        scale = result['scales']['2']
        centers = [((value['vertices'][:,:,:3].min(axis=(0,1)).astype(float)
                     + value['vertices'][:,:,:3].max(axis=(0,1)).astype(float))/2)for value in arrays.values()]
        transformed = compensated_transform(rows[1]['transform'],centers[1],centers[0],[1,1,1],scales=scale)
        old_basis=np.array(rows[1]['transform'][:9]).reshape(3,3).T
        new_basis=np.array(transformed[:9]).reshape(3,3).T
        source_world=arrays[2]['vertices'][:,:,:3].astype(float)@old_basis.T+rows[1]['transform'][9:]
        anchor_world=arrays[1]['vertices'][:,:,:3].astype(float)@new_basis.T+transformed[9:]
        np.testing.assert_allclose(anchor_world,source_world,atol=1e-12,rtol=0)
        self.assertEqual(rows,old)
        self.assertEqual({key:value.tobytes()for key,value in arrays.items()},before)

    def test_invalid_tolerance_and_nonfinite_records_rejected(self):
        for tolerance in (-1,float('nan'),float('inf'),1001,None,True):
            with self.assertRaises(ValueError):self.plan({}, tolerance_mm=tolerance)
        invalid=box();invalid['vertices'][0,0,7]=float('nan')
        with self.assertRaises(ValueError):self.plan({1:invalid})

    def test_comparison_limit_is_reported_and_never_forces_a_merge(self):
        arrays={index:box()for index in range(3)}
        arrays[1]['vertices'][0,0,0] += .00002
        arrays[2]['vertices'][0,1,0] -= .00002
        with patch('xivgate_revit_bridge.core.affine_instances._MAX_TOTAL_COMPARISONS',1):
            result=self.plan(arrays)
        self.assertLessEqual(result['statistics']['comparisons'],1)
        self.assertGreaterEqual(result['statistics']['comparison_limit_hits'],1)
        self.assertEqual(result['redirects'],{})


if __name__ == '__main__':unittest.main()
