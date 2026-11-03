"""Keep grouped source identities explicit without creating per-element objects."""
import json

SOURCE_INDEX='revit_source_index'


def attach_family_provenance(obj, placement, mesh_prim):
    import numpy as np
    from pxr import UsdGeom
    mode=placement.GetAttribute('revit:import_mode')
    if not mode or mode.Get()!='FAMILY': return False
    table_raw=placement.GetAttribute('revit:source_table').Get()
    ranges_raw=placement.GetAttribute('revit:source_ranges').Get()
    table=json.loads(table_raw); ranges=json.loads(ranges_raw)
    if not isinstance(table,list) or not table or not isinstance(ranges,list):
        raise ValueError('Некорректная таблица исходных элементов семейства')
    for row in table:
        if (not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id']
                or any(not isinstance(row.get(field),list) or any(not isinstance(value,str)for value in row[field])
                       for field in ('source_element_ids','selection_parent_ids'))):
            raise ValueError('Некорректные ID исходного элемента семейства')
    values=np.asarray(UsdGeom.PrimvarsAPI(mesh_prim).GetPrimvar(SOURCE_INDEX).Get(),dtype=np.int64)
    if values.shape!=(len(obj.data.polygons),) or np.any(values<0) or np.any(values>=len(table)):
        raise ValueError('Исходные элементы не соответствуют граням семейства')
    cursor=0
    for row in ranges:
        if not isinstance(row,dict) or any(type(row.get(key))is not int for key in ('source_index','start','count')):
            raise ValueError('Некорректный диапазон граней исходного элемента')
        index,start,count=(row[key]for key in ('source_index','start','count'))
        if start!=cursor or count<=0 or index<0 or index>=len(table) or start+count>len(values):
            raise ValueError('Неполный диапазон граней исходного элемента')
        if not np.all(values[start:start+count]==index):
            raise ValueError('Таблица исходных элементов расходится с гранями')
        cursor+=count
    if cursor!=len(values):raise ValueError('Не все грани семейства имеют исходный элемент')
    layer=obj.data.attributes.get(SOURCE_INDEX)
    if layer is not None and (layer.data_type!='INT' or layer.domain!='FACE'):
        obj.data.attributes.remove(layer);layer=None
    if layer is None:layer=obj.data.attributes.new(name=SOURCE_INDEX,type='INT',domain='FACE')
    layer.data.foreach_set('value',np.ascontiguousarray(values,dtype=np.int32))
    for target in (obj,obj.data):
        target['revit:import_mode']='FAMILY'
        target['revit:source_table']=table_raw
        target['revit:source_ranges']=ranges_raw
        key=placement.GetAttribute('revit:family_key')
        if key and key.Get() is not None:target['revit:family_key']=str(key.Get())
    return True


def select_source_faces(obj):
    import bmesh
    mesh=obj.data;bm=bmesh.from_edit_mesh(mesh)
    layer=bm.faces.layers.int.get(SOURCE_INDEX)
    face=bm.faces.active
    if layer is None or face is None:
        raise ValueError('В режиме редактирования выберите грань нужного элемента')
    index=int(face[layer]);table=json.loads(mesh['revit:source_table'])
    if not 0<=index<len(table):raise ValueError('У грани отсутствует исходный элемент')
    bm.select_mode={'FACE'}
    for item in bm.verts:item.select_set(False)
    for item in bm.edges:item.select_set(False)
    count=0
    for item in bm.faces:
        selected=int(item[layer])==index
        item.select_set(selected);count+=selected
    bm.select_flush_mode()
    bmesh.update_edit_mesh(mesh,loop_triangles=False,destructive=False)
    return dict(element_id=table[index]['id'],faces=count)
