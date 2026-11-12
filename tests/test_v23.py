import importlib.util
import sys
from pathlib import Path
from datamesh_formal.materialized import MaterializedMesh

def module():
    path=Path(__file__).resolve().parents[1]/'examples'/'benchmark_v23.py'
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('v23bench',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

def test_randomized_reproducible_and_distinct():
    m=module()
    a=m.build_instance(12,10,.5,False,'chain',5)
    b=m.build_instance(12,10,.5,False,'chain',5)
    c=m.build_instance(12,10,.5,False,'chain',6)
    assert a==b and a[0]!=c[0]

def test_three_topologies_and_adaptive_rule():
    m=module()
    for top in ('chain','fanout','layered'):
        nodes,req,bound=m.build_instance(12,10,1,True,top,2)
        MaterializedMesh(nodes,req,boundaries=bound)
    assert m.structural_choice(100,'chain',1,False,.8)=='full'
    assert m.structural_choice(100,'fanout',1,False,.8)=='igv'

def test_small_campaign(tmp_path):
    from argparse import Namespace
    m=module()
    args=Namespace(nodes=[8],rows=[5],selectivities=[0,1],topologies=['chain','fanout','layered'],seeds=2,edits=2,bootstrap=40,threshold=.8,out=str(tmp_path/'v.csv'),summary=str(tmp_path/'v.json'))
    records,groups=m.run(args)
    assert len(records)==48 and len(groups)==12
    assert all(r['equal'] for r in records)
    assert any(r['adaptive_choice']=='full' for r in records)
