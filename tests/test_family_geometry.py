import copy
import unittest

import numpy as np

from xivgate_revit_bridge.core.family_geometry import plan_family_geometry


DTYPE = np.dtype([('vertices','<f4',(3,8)),('material','<i8')])


def triangles(count=2):
    records = np.zeros(count,dtype=DTYPE)
    records['vertices'][:,:,:3] = [[0,0,0],[1,0,0],[0,1,1]]
    records['vertices'][:,:,3:6] = [0,-2**-.5,2**-.5]
    records['vertices'][:,:,6:8] = [[0,0],[1,0],[0,1]]
    records['material'] = np.arange(count)%2+7
    return records


def occurrence(identifier, prototype=0, **updates):
    row = dict(id=str(identifier),prototype=prototype,category_id='windows',category='Окна',
               family_name='Блок',type_name='Тип A',source_element_ids=[str(identifier)],
               selection_parent_ids=[],transform=[1,0,0,0,1,0,0,0,1,100000000,20,30])
    row.update(updates)
    return row


class FamilyGeometryTests(unittest.TestCase):
    def test_category_family_grouping_and_type_fallback(self):
        rows=[occurrence(1),occurrence(2,type_name='Тип B'),occurrence(3,category_id='doors'),
              occurrence(4,family_name='',type_name='A'),occurrence(5,family_name='',type_name='B')]
        plan=plan_family_geometry(rows,{0:triangles()})
        chunks=list(plan)
        self.assertEqual(len(chunks),4)
        self.assertEqual(plan.statistics['source_placements'],5)
        self.assertEqual(plan.statistics['expanded_triangles'],10)
        self.assertEqual(len({chunk['occurrence']['id']for chunk in chunks}),4)
        self.assertTrue(all(chunk['occurrence']['id'].startswith('family_')for chunk in chunks))
        self.assertEqual(sorted(len(chunk['source_table'])for chunk in chunks),[1,1,1,2])
        other=list(plan_family_geometry(list(reversed(rows)),{0:triangles()}))
        self.assertEqual([chunk['occurrence']['id']for chunk in chunks],
                         [chunk['occurrence']['id']for chunk in other])

    def test_world_bake_rebase_and_mirror_preserve_paired_normal_uv_material(self):
        raw=triangles()
        before=raw.tobytes()
        row=occurrence(1,transform=[0,3,0,-2,0,0,0,0,-.5,100000000,20,30])
        original=copy.deepcopy(row)
        raw.flags.writeable=False
        chunk=next(iter(plan_family_geometry([row],{0:raw})))
        out=chunk['records']
        basis=np.array(row['transform'][:9],dtype=float).reshape(3,3).T
        # Negative determinant reverses corners 1/2 together with their UVs.
        order=[0,2,1]
        expected=raw['vertices'][:,order,:3].astype(float)@basis.T+row['transform'][9:]
        origin=chunk['occurrence']['transform'][9:]
        np.testing.assert_allclose(out['vertices'][:,:,:3].astype(float)+origin,expected,atol=1e-8,rtol=0)
        expected_normal=raw['vertices'][:,order,3:6].astype(float)@np.linalg.inv(basis)
        expected_normal/=np.linalg.norm(expected_normal,axis=2,keepdims=True)
        np.testing.assert_allclose(out['vertices'][:,:,3:6],expected_normal,atol=1e-7,rtol=0)
        np.testing.assert_array_equal(out['vertices'][:,:,6:8],raw['vertices'][:,order,6:8])
        np.testing.assert_array_equal(out['material'],raw['material'])
        cross=np.cross(out['vertices'][:,1,:3]-out['vertices'][:,0,:3],
                       out['vertices'][:,2,:3]-out['vertices'][:,0,:3])
        self.assertTrue(np.all(np.sum(cross*out['vertices'][:,0,3:6],axis=1)>0))
        self.assertEqual(row,original)
        self.assertEqual(raw.tobytes(),before)

    def test_chunks_split_oversized_occurrences_and_keep_owner_ranges_exact(self):
        rows=[occurrence('9223372036854775807'),occurrence('9223372036854775808',1)]
        plan=plan_family_geometry(rows,{0:triangles(8),1:triangles(2)},max_triangles=3)
        chunks=list(plan)
        self.assertEqual([len(chunk['records'])for chunk in chunks],[3,3,3,1])
        self.assertEqual(plan.statistics['expanded_triangles'],10)
        self.assertEqual(plan.statistics['split_placements'],2)
        for chunk in chunks:
            labels=chunk['source_index']
            self.assertEqual(labels.dtype,np.dtype('int32'))
            self.assertEqual(len(labels),len(chunk['records']))
            for segment in chunk['source_ranges']:
                self.assertTrue(np.all(labels[segment['start']:segment['start']+segment['count']]==segment['source_index']))
            self.assertTrue(all(isinstance(source['id'],str)for source in chunk['source_table']))
        self.assertEqual(chunks[2]['source_index'].tolist(),[0,0,1])
        self.assertEqual([source['id']for source in chunks[2]['source_table']],
                         ['9223372036854775807','9223372036854775808'])

    def test_compound_owner_metadata_is_truthful_union_without_child_face_claims(self):
        rows=[occurrence('owner-A',source_element_ids=['child-A','child-B'],selection_parent_ids=['parent-A']),
              occurrence('owner-B',source_element_ids=['child-B','child-C'],parent_id='parent-B')]
        del rows[1]['selection_parent_ids']
        chunk=next(iter(plan_family_geometry(rows,{0:triangles()})))
        self.assertEqual(chunk['occurrence']['source_element_ids'],['child-A','child-B','child-C'])
        self.assertEqual(chunk['occurrence']['selection_parent_ids'],['parent-A','parent-B'])
        self.assertEqual(chunk['source_table'][0]['id'],'owner-A')
        self.assertEqual(chunk['source_table'][0]['source_element_ids'],['child-A','child-B'])
        self.assertEqual(chunk['source_index'].tolist(),[0,0,1,1])

    def test_invalid_last_transform_or_nonfinite_geometry_fails_during_preflight(self):
        for transform in ([0]*12,[1]*11,[1,0,0,0,1,0,0,0,1,float('nan'),0,0]):
            with self.assertRaises(ValueError):
                plan_family_geometry([occurrence(1),occurrence(2,transform=transform)],{0:triangles()})
        for column in (0,3,6):
            raw=triangles();raw['vertices'][0,0,column]=float('nan')
            with self.assertRaises(ValueError):plan_family_geometry([occurrence(1)],{0:raw})
        with self.assertRaises(ValueError):
            plan_family_geometry([occurrence(1),occurrence(1)],{0:triangles()})

    def test_expansion_limit_checks_shared_geometry_real_cost_before_iteration(self):
        rows=[occurrence(i)for i in range(5)]
        with self.assertRaises(ValueError):
            plan_family_geometry(rows,{0:triangles(3)},max_expanded_triangles=14)
        plan=plan_family_geometry(rows,{0:triangles(3)},max_expanded_triangles=15)
        self.assertEqual(plan.statistics['expanded_record_bytes'],15*104)

    def test_exact_family_names_and_real_source_ids_cannot_collide(self):
        chunks=list(plan_family_geometry([occurrence(1,family_name='A'),occurrence(2,family_name='A ')],
                                         {0:triangles()}))
        self.assertEqual(len(chunks),2)
        generated=next(iter(plan_family_geometry([occurrence(1)],{0:triangles()})))['occurrence']['id']
        with self.assertRaises(ValueError):
            plan_family_geometry([occurrence(1),occurrence(generated,category_id='other')],{0:triangles()})

    def test_zero_normals_and_world_overflow_fail_before_iteration(self):
        raw=triangles();raw['vertices'][:,:,3:6]=0
        with self.assertRaises(ValueError):plan_family_geometry([occurrence(1)],{0:raw})
        with self.assertRaises(ValueError):
            plan_family_geometry([occurrence(1,transform=[1e308,0,0,0,1,0,0,0,1,1e308,0,0])],{0:triangles()})

    def test_missing_family_uses_native_type_id_without_grouping_equal_labels(self):
        rows=[occurrence(1,family_name='',type_name='Same',type_id='10'),
              occurrence(2,family_name='',type_name='Same',type_id='11')]
        self.assertEqual(len(list(plan_family_geometry(rows,{0:triangles()}))),2)
        first=next(iter(plan_family_geometry(rows[:1],{0:triangles()})))
        renamed=copy.deepcopy(rows[:1]);renamed[0]['type_name']='Renamed'
        second=next(iter(plan_family_geometry(renamed,{0:triangles()})))
        self.assertEqual(first['occurrence']['id'],second['occurrence']['id'])
        self.assertEqual(second['occurrence']['type_name'],'Renamed')
        for type_id in (None,'','-1',-1):
            unknown=[occurrence(1,family_name='',type_name='Same',type_id=type_id),
                     occurrence(2,family_name='',type_name='Same',type_id=type_id)]
            self.assertEqual(len(list(plan_family_geometry(unknown,{0:triangles()}))),2)


if __name__=='__main__':unittest.main()
