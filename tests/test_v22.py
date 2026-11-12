import importlib.util
from pathlib import Path
from dataclasses import replace

from datamesh_formal.evidence import DataNode, row
from datamesh_formal.materialized import MaterializedMesh, full_refresh
from datamesh_formal.lifecycle import recompute


def test_instrumented_phases_and_conservative_baseline():
    nodes=[DataNode('s',records=(row(key='1',patient_id='a'),)),
           DataNode('v',('s',),'project','key',())]
    nodes=full_refresh(nodes)
    a=MaterializedMesh(nodes,[],boundaries=frozenset({'v'}))
    b=MaterializedMesh(nodes,[],boundaries=frozenset({'v'}))
    patch=replace(a.nodes['s'],records=(row(key='1',patient_id='b'),))
    ia=a.update(sources=[patch]);ib=b.update(sources=[patch],strategy='descendants')
    assert a.decisions()==b.decisions()==recompute(full_refresh(nodes,sources=[patch]),[],(),frozenset({'v'}))
    assert ia['operators_evaluated']==ib['operators_evaluated']==1
    assert ib['recomputed']>=ia['recomputed']
    for s in (ia,ib):
        assert s['materialize_ms']>=0 and s['governance_ms']>=0
        assert abs(s['measured_total_ms']-s['materialize_ms']-s['governance_ms'])<1e-9


def test_seed_cluster_bootstrap_constant_ratios():
    path=Path(__file__).resolve().parents[1]/'examples'/'benchmark_v22.py'
    spec=importlib.util.spec_from_file_location('bench_v22_test',path)
    # benchmark_v22 imports sibling benchmark_v21, already covered by the pilot CLI.
    import sys
    sys.path.insert(0,str(path.parent))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    ci=module.cluster_ci({0:[(4,2),(8,4)],1:[(6,3),(10,5)]},iterations=50)
    assert ci==[2,2]
    assert module.cluster_ci({0:[(4,2)]}) is None
