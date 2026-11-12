from dataclasses import replace
import random
import pytest
from datamesh_formal.evidence import DataNode, row
from datamesh_formal.lifecycle import Requirement, Mode, Attestation, recompute
from datamesh_formal.materialized import MaterializedMesh, full_refresh, full_verify


def fixture():
    nodes = [DataNode('health', records=(row(key='1', patient_id='P'),)),
             DataNode('clean', ('health',), 'project', 'key', (row(key='1'),)),
             DataNode('research', records=(row(key='1', patient_id='P'),)),
             DataNode('merged', ('clean','research'), 'join', 'key', (row(key='1',patient_id='P'),))]
    req = Requirement('health', 'redact', 'health', Mode.PERSISTENT, 'absent:patient_id')
    return nodes, req


def test_reintroduction_and_recovery():
    nodes, req = fixture()
    mesh = MaterializedMesh(nodes, [req], boundaries=frozenset({'clean','merged'}))
    assert mesh.decisions()['clean'].publishable
    assert not mesh.decisions()['merged'].publishable
    edit = replace(nodes[2], records=(row(key='1'),))
    stats = mesh.update(sources=[edit])
    assert stats['materialized_changed'] == 2
    assert mesh.decisions()['merged'].publishable
    expected = full_refresh(nodes, [edit])
    assert mesh.decisions() == recompute(expected,[req],boundaries=frozenset({'clean','merged'}))
    mesh.update(sources=[nodes[2]])
    assert not mesh.decisions()['merged'].publishable


def test_parent_data_changes_but_governance_state_same():
    nodes = [DataNode('s', records=(row(key='1', v='a'),)),
             DataNode('f', ('s',), 'project', 'key,v', (row(key='1',v='a'),)),
             DataNode('g', ('f',), 'project', 'key,v', (row(key='1',v='a'),))]
    req = Requirement('owner','absent','s',Mode.PERSISTENT,'absent:secret')
    mesh = MaterializedMesh(nodes,[req],boundaries=frozenset({'g'}))
    changed = replace(nodes[0],records=(row(key='1',v='b'),))
    stats=mesh.update(sources=[changed])
    assert stats['materialized_changed']==3
    assert mesh.nodes['g'].records == (row(key='1',v='b'),)
    assert mesh.decisions() == recompute(full_refresh(nodes,[changed]),[req],boundaries=frozenset({'g'}))


def test_project_cutoff_prevents_downstream_operator():
    nodes=[DataNode('s',records=(row(key='1',secret='a'),)),
           DataNode('p',('s',),'project','key',(row(key='1'),)),
           DataNode('q',('p',),'project','key',(row(key='1'),))]
    mesh=MaterializedMesh(nodes,[])
    stats=mesh.update(sources=[replace(nodes[0],records=(row(key='1',secret='b'),))])
    assert stats['operators_evaluated']==1
    assert stats['materialized_changed']==1
    assert mesh.nodes['q'].records==nodes[2].records


def test_invalid_inputs():
    nodes, req=fixture(); mesh=MaterializedMesh(nodes,[req])
    with pytest.raises(ValueError):mesh.update(sources=[nodes[1]])
    with pytest.raises(ValueError):mesh.update(sources=[nodes[0],nodes[0]])
    with pytest.raises(ValueError):MaterializedMesh([replace(nodes[1],records=())]+[n for n in nodes if n.id!='clean'],[req])


@pytest.mark.parametrize('seed',range(10))
def test_random_end_to_end(seed):
    rng=random.Random(seed)
    nodes=[DataNode('s',records=(row(key='1',x='a'),)),DataNode('t',records=(row(key='1',y='a'),)),
           DataNode('p',('s',),'project','key,x',(row(key='1',x='a'),)),
           DataNode('j',('p','t'),'join','key',(row(key='1',x='a',y='a'),)),
           DataNode('q',('j',),'project','key,x,y',(row(key='1',x='a',y='a'),))]
    req=Requirement('h','absence','s',Mode.PERSISTENT,'absent:secret')
    once=Requirement('h','audit','s',Mode.ONCE,'action:audited')
    reqs=[req,once]; acts=[]; boundaries=frozenset({'q'})
    mesh=MaterializedMesh(nodes,reqs,acts,boundaries)
    current={n.id:n for n in nodes}
    for i in range(54):
        patch={}
        kind=rng.choice(('source','rule','attest','noop'))
        if kind=='source':
            src=rng.choice(('s','t'))
            field='x' if src=='s' else 'y'
            value=rng.choice(('a','b','c'))
            extra={'secret':'private'} if src=='s' and rng.random()<.3 else {}
            new=replace(current[src],records=(row(key='1',**{field:value},**extra),))
            current[src]=new
            patch['sources']=[new]
        elif kind=='rule':
            reqs=[] if req in reqs else [req,once]
            patch['requirements']=reqs
        elif kind=='attest':
            acts=[] if acts else [Attestation(once,'p','audited')]
            patch['attestations']=acts
        mesh.update(**patch)
        oracle_nodes=full_refresh(list(current.values()))
        assert {n.id:n.records for n in oracle_nodes} == {n.id:n.records for n in mesh.nodes.values()}
        assert mesh.decisions()==recompute(oracle_nodes,reqs,acts,boundaries)
