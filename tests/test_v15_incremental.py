from dataclasses import replace
import random
import pytest
from datamesh_formal.evidence import DataNode,row
from datamesh_formal.lifecycle import Requirement,Mode,Attestation,recompute
from datamesh_formal.lifecycle_incremental import LifecycleIGV


def scenario():
    nodes=[DataNode('health',records=(row(key='1',patient_id='secret'),)),
           DataNode('research',records=(row(key='1',patient_id='exposed'),)),
           DataNode('clean',('health',),'project','key',(row(key='1'),)),
           DataNode('merged',('clean','research'),'join','key',(row(key='1',patient_id='exposed'),))]
    req=Requirement('health','redact','health',Mode.PERSISTENT,'absent:patient_id')
    return nodes,req

def assert_oracle(mesh):
    assert mesh.decisions()==recompute(list(mesh.nodes.values()),list(mesh.requirements),
                                       list(mesh.attestations),mesh.boundaries)

def test_counterexample_and_repair():
    nodes,req=scenario()
    mesh=LifecycleIGV(nodes,[req],boundaries=frozenset({'clean','merged'}))
    assert_oracle(mesh)
    assert mesh.decisions()['clean'].publishable
    assert not mesh.decisions()['merged'].publishable
    mesh.update(nodes=[replace(nodes[1],records=(row(key='1'),)),
                       replace(nodes[3],records=(row(key='1'),))])
    assert_oracle(mesh)
    assert mesh.decisions()['merged'].publishable

def test_origin_distinction_and_rule_revoke():
    nodes,req=scenario()
    second=Requirement('research','redact','research',Mode.PERSISTENT,'absent:patient_id')
    mesh=LifecycleIGV(nodes,[req,second],boundaries={'merged'})
    assert_oracle(mesh)
    assert mesh.decisions()['merged'].violations==frozenset({req,second})
    stats=mesh.update(requirements=[req])
    assert stats['seeds']==1
    assert_oracle(mesh)
    assert mesh.decisions()['merged'].violations==frozenset({req})

def test_once_attestation_derived_done_propagates():
    nodes,_=scenario()
    req=Requirement('health','audit','health',Mode.ONCE,'action:audited')
    mesh=LifecycleIGV(nodes,[req],boundaries={'clean','merged'})
    assert_oracle(mesh)
    assert not mesh.decisions()['merged'].publishable
    mesh.update(attestations=[Attestation(req,'clean','audited')])
    assert_oracle(mesh)
    assert mesh.decisions()['merged'].publishable
    mesh.update(attestations=[])
    assert_oracle(mesh)
    assert not mesh.decisions()['merged'].publishable

def test_boundary_only_and_noop():
    nodes,req=scenario()
    req=replace(req,mode=Mode.BOUNDARY)
    mesh=LifecycleIGV(nodes,[req],boundaries={'clean'})
    assert_oracle(mesh)
    assert not mesh.decisions()['merged'].publishable
    mesh.update(boundaries={'clean','merged'})
    assert_oracle(mesh)
    assert req in mesh.decisions()['merged'].violations
    stats=mesh.update()
    assert stats['recomputed']==0

def test_errors_are_part_of_cutoff():
    nodes,req=scenario()
    mesh=LifecycleIGV(nodes,[req],boundaries={'merged'})
    bad=replace(nodes[2],records=(row(key='1',patient_id='wrong'),))
    mesh.update(nodes=[bad])
    assert_oracle(mesh)
    assert any('output_mismatch@clean' in e for e in mesh.decisions()['merged'].errors)

def test_randomized_differential_changes():
    for seed in range(12):
        rng=random.Random(seed)
        nodes,req=scenario()
        once=Requirement('health','audit','health',Mode.ONCE,'action:audited')
        second=Requirement('research','redact','research',Mode.BOUNDARY,'absent:patient_id')
        mesh=LifecycleIGV(nodes,[req,once,second],boundaries={'clean','merged'})
        assert_oracle(mesh)
        for _ in range(45):
            k=rng.randrange(6)
            if k==0:
                mesh.update(requirements=rng.sample([req,once,second],rng.randrange(4)))
            elif k==1:
                mesh.update(attestations=[Attestation(once,'clean','audited')] if rng.randrange(2) else [])
            elif k==2:
                mesh.update(boundaries=rng.choice([frozenset(),frozenset({'merged'}),frozenset({'clean','merged'})]))
            elif k==3:
                n=mesh.nodes['research']
                mesh.update(nodes=[replace(n,records=(row(key='1'),) if rng.randrange(2) else (row(key='1',patient_id='exposed'),))])
            elif k==4:
                n=mesh.nodes['merged']
                mesh.update(nodes=[replace(n,records=(row(key='1'),) if rng.randrange(2) else (row(key='1',patient_id='exposed'),))])
            else:
                mesh.update()
            assert_oracle(mesh)

def test_fixed_graph_rejects_edge_edit():
    nodes,req=scenario()
    mesh=LifecycleIGV(nodes,[req])
    with pytest.raises(ValueError,match='fixed'):
        mesh.update(nodes=[replace(nodes[2],inputs=('research',))])
