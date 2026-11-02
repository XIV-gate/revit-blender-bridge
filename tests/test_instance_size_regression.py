import unittest
import numpy as np

from test_affine_instances import box, occurrence
from xivgate_revit_bridge.core.affine_instances import plan_affine_instances


def subdivide(records):
    result = np.repeat(records, 3).copy()
    for index, triangle in enumerate(records['vertices']):
        middle = triangle.mean(axis=0)
        for corner in range(3):
            result['vertices'][index * 3 + corner] = [triangle[corner], triangle[(corner+1)%3], middle]
    return result


class InstanceSizeRegression(unittest.TestCase):
    def plan(self, arrays, materials=None):
        return plan_affine_instances([occurrence(key,key) for key in arrays], arrays,
            materials or {7:{'uv_sensitive':False}})

    def test_different_width_or_height_never_share_by_stretching(self):
        for dimensions in ((2,.035,4.925),(2.2,.035,3.19)):
            self.assertEqual(self.plan({1:box((2,.035,3.19)),2:box(dimensions)})['redirects'],{})

    def test_same_box_with_extra_coplanar_edges_does_share(self):
        first = box((2,.035,4.925))
        other = subdivide(first)
        other['vertices'][:,:,:3] += [3,4,5]
        result = self.plan({1:first,2:other})
        self.assertEqual(result['redirects'],{'1':1,'2':1})
        self.assertEqual(result['scales']['2'],[1,1,1])

    def test_hole_or_duplicate_overlapping_faces_cannot_pass_box_proof(self):
        first=box((2,.035,4.925))
        missing=first[:-1].copy()
        overlap=first.copy(); overlap[1]=overlap[0]
        for other in (missing,overlap):
            self.assertEqual(self.plan({1:first,2:other})['redirects'],{})

    def test_textured_subdivision_is_not_discarded(self):
        first=box((2,.035,4.925)); other=subdivide(first)
        self.assertEqual(self.plan({1:first,2:other},{7:{'uv_sensitive':True}})['redirects'],{})

    def test_rotation_preserves_size_and_maps_original_vertices(self):
        first=box((2,.035,4.925))
        other=subdivide(first)
        angle=.731
        rotation=np.array([[np.cos(angle),-np.sin(angle),0],[np.sin(angle),np.cos(angle),0],[0,0,1]])
        other['vertices'][:,:,:3]=other['vertices'][:,:,:3]@rotation.T+[4,5,6]
        other['vertices'][:,:,3:6]=other['vertices'][:,:,3:6]@rotation.T
        result=self.plan({1:first,2:other})
        self.assertEqual(result['redirects'],{'1':1,'2':1})
        actual=np.asarray(result['rotations']['2'])
        np.testing.assert_allclose(actual.T@actual,np.eye(3),atol=1e-7)
        self.assertAlmostEqual(abs(np.linalg.det(actual)),1,places=7)
        anchor=first['vertices'][:,:,:3].reshape(-1,3)
        source=other['vertices'][:,:,:3].reshape(-1,3)
        source_center=(source.min(0).astype(float)+source.max(0))/2
        mapped=anchor@actual.T+source_center
        for point in mapped:
            self.assertLess(np.linalg.norm(source-point,axis=1).min(),1e-5)

    def test_large_face_small_hole_is_not_a_complete_box(self):
        first=box((100,100,1))
        other=np.concatenate((first[:1],first[1:2],first[1:])).copy()
        # Replace the first bottom triangle by two triangles which leave a
        # 20 mm corner open while all five other faces remain complete.
        t=first['vertices'][0].copy(); corner=t[0].copy()
        a=corner.copy();a[:3]=corner[:3]+(t[1,:3]-corner[:3])*.0002
        b=corner.copy();b[:3]=corner[:3]+(t[2,:3]-corner[:3])*.0002
        other['vertices'][0]=[a,t[1],t[2]]
        other['vertices'][1]=[a,t[2],b]
        self.assertEqual(self.plan({1:first,2:other})['redirects'],{})

    def test_world_placement_scale_cannot_amplify_beyond_tolerance(self):
        arrays={1:box((1,2,3)),2:subdivide(box((1.00009,2.00009,3.00009)))}
        rows=[occurrence(1,1),occurrence(2,2)]
        rows[1]['transform']=[2,0,0,0,3,0,0,0,-.5,0,0,0]
        result=plan_affine_instances(rows,arrays,{7:{'uv_sensitive':False}},tolerance_mm=.1)
        self.assertEqual(result['redirects'],{})

    def test_valid_thirteen_triangle_box_with_shared_edge_t_junction(self):
        first=box((2,.035,4.925))
        split=np.repeat(first[:1],2).copy()
        triangle=first['vertices'][0]; middle=(triangle[0]+triangle[2])/2
        split['vertices'][0]=[triangle[0],triangle[1],middle]
        split['vertices'][1]=[triangle[1],triangle[2],middle]
        other=np.concatenate((split,first[1:]))
        self.assertEqual(self.plan({1:first,2:other})['redirects'],{'1':1,'2':1})


if __name__=='__main__': unittest.main()
