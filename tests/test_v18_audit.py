from dataclasses import replace
import pytest
from datamesh_formal.evidence import DataNode,row
from datamesh_formal.lifecycle import Requirement,Mode,Attestation,recompute
from datamesh_formal.materialized import MaterializedMesh,full_refresh
from datamesh_formal.lifecycle_incremental import LifecycleIGV


def graph():
    s=DataNode('s',records=(row(key='a',patient_id='P'),))
    clean=DataNode('clean',('s',),'project','key',(row(key='a'),))
    t=DataNode('t',records=(row(key='a',patient_id='P'),))
    join=DataNode('join',('clean','t'),'join','key',(row(key='a',patient_id='P'),))
    end=DataNode('end',('join',),'project','key,patient_id',(row(key='a',patient_id='P'),))
    return [s,clean,t,join,end]

@pytest.mark.parametrize('mode', [Mode.PERSISTENT,Mode.BOUNDARY])
def test_reintroduced_identifier_blocks_release_and_recovery(mode):
    nodes=graph();r=Requirement('hospital','redact','s',mode,'absent:patient_id')
    m=MaterializedMesh(nodes,[r],boundaries=frozenset({'clean','end'}))
    assert m.decisions()['clean'].publishable
    assert not m.decisions()['end'].publishable
    fixed=replace(nodes[2],records=(row(key='a'),))
    m.update(sources=[fixed])
    assert m.decisions()['end'].publishable
    assert m.decisions()==recompute(full_refresh(nodes,[fixed]),[r],boundaries=frozenset({'clean','end'}))
    m.update(sources=[nodes[2]])
    assert not m.decisions()['end'].publishable


def test_once_attestation_not_equivalent_to_state_invariant():
    nodes=graph()
    once=Requirement('hospital','review','s',Mode.ONCE,'action:reviewed')
    persist=Requirement('hospital','redact','s',Mode.PERSISTENT,'absent:patient_id')
    m=MaterializedMesh(nodes,[once,persist],[Attestation(once,'clean','reviewed')],frozenset({'end'}))
    assert once not in m.decisions()['end'].pending
    assert persist in m.decisions()['end'].violations
    assert not m.decisions()['end'].publishable


def test_invalid_policy_edit_is_atomic():
    nodes=graph();req=Requirement('h','redact','s',Mode.PERSISTENT,'absent:patient_id')
    e=LifecycleIGV(nodes,[req],boundaries=frozenset({'end'}))
    before=(dict(e.nodes),e.decisions(),e.requirements,e.boundaries)
    bad=Requirement('h','missing','nonexistent',Mode.PERSISTENT,'absent:secret')
    with pytest.raises(ValueError):e.update(nodes=[replace(nodes[0],records=(row(key='a'),))],requirements=[bad])
    assert (dict(e.nodes),e.decisions(),e.requirements,e.boundaries)==before


def test_duplicate_rule_edit_is_rejected_atomically():
    nodes=graph();req=Requirement('h','redact','s',Mode.PERSISTENT,'absent:patient_id')
    e=LifecycleIGV(nodes,[req]); before=e.decisions()
    with pytest.raises(ValueError):e.update(requirements=[req,req])
    assert e.decisions()==before


def test_invalid_second_node_is_atomic():
    nodes=graph();e=LifecycleIGV(nodes,[]);before=dict(e.nodes)
    with pytest.raises(ValueError):e.update(nodes=[replace(nodes[0],records=()),DataNode('unknown')])
    assert e.nodes==before


def test_operator_edits_rejected():
    nodes=graph();e=LifecycleIGV(nodes,[])
    with pytest.raises(ValueError):e.update(nodes=[replace(nodes[1],parameter='key,patient_id')])
