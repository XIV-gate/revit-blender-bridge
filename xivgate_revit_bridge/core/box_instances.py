"""Prove full cuboid surfaces independently of coplanar triangle subdivision.

Only explicitly untextured, flat-normal, closed boxes qualify. Face area plus
pairwise non-overlap proves complete rectangle coverage; holes, duplicated
faces and internal geometry cannot be hidden by matching bounding boxes.
"""
from collections import defaultdict
import itertools
import math

MAX_BOX_COMPARISONS = 64
MAX_TOTAL_BOX_COMPARISONS = 50_000


def _boundary_complete(triangles, dimensions, precision):
    """Every interior directed sub-edge cancels; only the rectangle remains.

    Split collinear edges at T-junctions before counting. Area alone cannot
    distinguish a tiny hole in a large face from floating-point roundoff.
    """
    import numpy as np
    values=np.asarray(triangles)
    points,indices=np.unique(values.reshape(-1,2),axis=0,return_inverse=True)
    for x,y in itertools.product((-1,1),repeat=2):
        corner=np.array([x,y])*dimensions/2
        if np.linalg.norm(points-corner,axis=1).min()>precision: return False
    counts=defaultdict(int)
    for triangle in indices.reshape(-1,3):
        for u,v in zip(triangle,np.roll(triangle,-1)):
            edge=points[v]-points[u]; squared=float(edge@edge)
            if squared<=1e-24: return False
            delta=points-points[u]
            parameters=delta@edge/squared
            distances=abs(delta[:,0]*edge[1]-delta[:,1]*edge[0])/math.sqrt(squared)
            cuts=np.flatnonzero((parameters>1e-10)&(parameters<1-1e-10)&(distances<=precision))
            sequence=[int(u),*sorted(map(int,cuts),key=lambda k:parameters[k]),int(v)]
            for a,b in zip(sequence,sequence[1:]):
                if a==b: continue
                counts[min(a,b),max(a,b)]+=1 if a<b else -1
    for (u,v),count in counts.items():
        if count==0: continue
        if abs(count)!=1: return False
        a,b=(points[u],points[v])if count>0 else(points[v],points[u])
        # The remaining boundary must run counterclockwise around the outside.
        valid=False
        for axis in range(2):
            other=1-axis
            for sign in (-1,1):
                bound=sign*dimensions[axis]/2
                if max(abs(a[axis]-bound),abs(b[axis]-bound))<=precision:
                    direction=sign if axis==0 else -sign
                    if direction*(b[other]-a[other])>0: valid=True
        if not valid: return False
    return True


def _cross(a,b):
    return float(a[0]*b[1]-a[1]*b[0])


def _intersection_area(first, second):
    import numpy as np
    polygon=[point.copy() for point in first]
    for index in range(3):
        start,end=second[index],second[(index+1)%3]
        edge=end-start
        old,polygon=polygon,[]
        if not old: return 0.
        previous=old[-1]; previous_side=_cross(edge,previous-start)
        for point in old:
            side=_cross(edge,point-start)
            if (side>=0)!=(previous_side>=0):
                weight=previous_side/(previous_side-side)
                polygon.append(previous+(point-previous)*weight)
            if side>=0: polygon.append(point)
            previous,previous_side=point,side
    if len(polygon)<3: return 0.
    p=np.asarray(polygon)
    return abs(sum(_cross(p[index],p[(index+1)%len(p)]) for index in range(len(p))))/2


def _prove(records, materials, precision):
    import numpy as np
    if not 12<=len(records)<=128: return None
    if any(materials.get(str(int(key)),{}).get('uv_sensitive') is not False
           for key in np.unique(records['material'])): return None
    v=records['vertices'].astype(np.float64)
    xyz=v[:,:,:3]
    edges=xyz[:,1]-xyz[:,0]; other=xyz[:,2]-xyz[:,0]
    cross=np.cross(edges,other); lengths=np.linalg.norm(cross,axis=1)
    if np.any(lengths<=1e-12): return None
    geometric_normals=cross/lengths[:,None]
    supplied=v[:,:,3:6]; magnitude=np.linalg.norm(supplied,axis=2)
    if np.any(magnitude<=1e-12): return None
    flat=supplied/magnitude[:,:,None]
    normals=flat[:,0]
    if np.max(np.abs(flat-normals[:,None,:]))>1e-6: return None
    # Short thin triangles amplify float32 coordinate noise in a cross product.
    # Flat supplied normals define the frame; the vertices must independently
    # pass all six plane, coverage and non-overlap checks below.
    if np.min(np.sum(geometric_normals*normals,axis=1))<1-1e-8: return None
    axes=[]
    for normal in normals:
        axis=normal.copy()
        if axis[np.argmax(abs(axis))]<0: axis=-axis
        if any(abs(float(axis@existing))>1-1e-8 for existing in axes): continue
        if len(axes)==3 or any(abs(float(axis@existing))>1e-7 for existing in axes): return None
        axes.append(axis)
    if len(axes)!=3: return None
    frame=np.asarray(axes).T
    u,_,vt=np.linalg.svd(frame)
    frame=u@vt
    projected=xyz@frame
    low,high=projected.min(axis=(0,1)),projected.max(axis=(0,1))
    dims=high-low
    if np.any(dims<=1e-7): return None
    # Stable length order permits source-local rotations and axis permutations.
    order=np.argsort(dims,kind='stable'); frame=frame[:,order]
    projected=projected[:,:,order]; low=low[order]; high=high[order]; dims=dims[order]
    centre=frame@((low+high)/2)
    coords=projected-(low+high)/2
    directions=normals@frame
    faces=defaultdict(list)
    face_materials={}; plane_deviation=0.
    for index,triangle in enumerate(coords):
        axis=int(np.argmax(abs(directions[index]))); sign=1 if directions[index,axis]>0 else -1
        expected=sign*dims[axis]/2
        deviation=float(np.max(abs(triangle[:,axis]-expected)))
        if deviation>precision: return None
        plane_deviation=max(plane_deviation,deviation)
        plane=[i for i in range(3) if i!=axis]
        polygon=triangle[:,plane].copy()
        signed=_cross(polygon[1]-polygon[0],polygon[2]-polygon[0])
        if signed<0: polygon=polygon[[0,2,1]]
        key=(axis,sign); material=int(records['material'][index])
        if key in face_materials and face_materials[key]!=material: return None
        face_materials[key]=material
        faces[key].append(polygon)
    if len(faces)!=6: return None
    for (axis,sign),triangles in faces.items():
        if not _boundary_complete(triangles,np.delete(dims,axis),precision): return None
        expected=float(np.prod(np.delete(dims,axis)))
        area_precision=max(1e-12,expected*1e-7,plane_deviation*float(np.delete(dims,axis).sum())*4)
        area=sum(abs(_cross(t[1]-t[0],t[2]-t[0]))/2 for t in triangles)
        if abs(area-expected)>area_precision: return None
        for first,second in itertools.combinations(triangles,2):
            if _intersection_area(first,second)>area_precision: return None
    return dict(frame=frame,centre=centre,dims=dims,deviation=plane_deviation,
                materials=tuple(face_materials[(axis,sign)]for axis in range(3)for sign in (-1,1)))


def plan_box_instances(occurrences, records_by_id, materials, tolerance_mm):
    import numpy as np
    from .affine_instances import _order
    metadata={str(key):value for key,value in materials.items()}
    used=defaultdict(list)
    for row in occurrences: used[int(row['prototype'])].append(row)
    tolerance=float(tolerance_mm)/1000
    gains={}
    for key,rows in used.items():
        gain=1.
        for row in rows:
            if 'transform' in row:
                basis=np.asarray(row['transform'],dtype=np.float64)
                if basis.shape!=(12,) or not np.isfinite(basis).all(): raise ValueError('Некорректная матрица размещения')
                gain=max(gain,float(np.linalg.norm(basis[:9].reshape(3,3),ord=2)))
        gains[key]=gain
    info={key:value for key in sorted(used,key=_order)
          if (value:=_prove(records_by_id[key],metadata,min(tolerance/gains[key],1e-5)+1e-12)) is not None}
    buckets=defaultdict(list); groups=defaultdict(list)
    comparisons=0; limit_hits=0
    for key,data in info.items():
        representative=key
        # Material correspondence is geometric, so equal geometry in different
        # Revit families may share; a family name alone never proves equality.
        exact=tolerance<1e-12
        cell=tuple(map(float,data['dims']))if exact else tuple(math.floor(float(v)/tolerance)for v in data['dims'])
        candidates=[]
        for delta in ((0,0,0),)if exact else itertools.product((-1,0,1),repeat=3):
            neighbour=cell if exact else tuple(a+b for a,b in zip(cell,delta))
            candidates.extend(buckets[data['materials'],neighbour])
        count=0
        for anchor in sorted(candidates,key=_order):
            if count>=MAX_BOX_COMPARISONS or comparisons>=MAX_TOTAL_BOX_COMPARISONS:
                limit_hits+=1; break
            count+=1
            comparisons+=1
            difference=abs(data['dims']-info[anchor]['dims'])
            if np.max(difference)*gains[key]>tolerance+1e-12: continue
            error=(np.linalg.norm(difference)/2+4*(data['deviation']+info[anchor]['deviation']))*gains[key]
            if error>tolerance+1e-12: continue
            representative=anchor; break
        if representative==key: buckets[data['materials'],cell].append(key)
        groups[representative].append(key)
    redirects,rotations={},{}
    for anchor,members in groups.items():
        if len(members)<2: continue
        for key in members:
            rotation=info[key]['frame']@info[anchor]['frame'].T
            for row in used[key]:
                redirects[str(row['id'])]=anchor
                rotations[str(row['id'])]=rotation.tolist()
    return dict(redirects=redirects,rotations=rotations,
                statistics=dict(proven_boxes=len(info),groups=sum(len(v)>1 for v in groups.values()),
                                grouped_occurrences=len(redirects),comparisons=comparisons,comparison_limit_hits=limit_hits))
