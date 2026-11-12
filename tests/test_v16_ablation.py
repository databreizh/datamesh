from dataclasses import replace
import pytest
from datamesh_formal.evidence import DataNode,row
from datamesh_formal.lifecycle import Mode,Requirement,Attestation,recompute
from datamesh_formal.lifecycle_incremental import LifecycleIGV
from examples.benchmark_v16 import build,scenario

@pytest.mark.parametrize('topology',['chain','tree','dag','layered'])
def test_multi_topology_and_ablation(topology):
    nodes=build(60,topology)
    global_req,local_req,once=scenario(nodes)
    meshes={m:LifecycleIGV(nodes,[global_req,local_req,once],boundaries={'p59'}) for m in ('igv','descendants','no_policy_index')}
    requirements=[global_req,local_req,once]
    attestations=[]
    by_id={n.id:n for n in nodes}
    edits=[{'nodes':[replace(by_id['p0'],records=(row(key='1'),))]},
           {'requirements':[local_req,once]},
           {'attestations':[Attestation(once,'p1','audited')]},
           {'requirements':[once]},
           {},
           {'requirements':[global_req,local_req,once]}]
    for patch in edits:
        if 'nodes' in patch:
            for n in patch['nodes']:by_id[n.id]=n
        requirements=patch.get('requirements',requirements)
        attestations=patch.get('attestations',attestations)
        oracle=recompute(list(by_id.values()),requirements,attestations,frozenset({'p59'}))
        for strategy,mesh in meshes.items():
            mesh.update(**patch,strategy=strategy)
            assert mesh.decisions()==oracle

def test_descendants_does_more_work_when_observables_unchanged():
    nodes=build(30,'chain')
    igv=LifecycleIGV(nodes,[])
    desc=LifecycleIGV(nodes,[])
    new=replace(nodes[0],records=(row(key='1',patient_id='changed'),))
    a=igv.update(nodes=[new],strategy='igv')
    b=desc.update(nodes=[new],strategy='descendants')
    assert a['recomputed']<b['recomputed']
    assert igv.decisions()==desc.decisions()

def test_unknown_strategy_rejected():
    mesh=LifecycleIGV(build(12,'chain'),[])
    with pytest.raises(ValueError):mesh.update(strategy='unknown')
