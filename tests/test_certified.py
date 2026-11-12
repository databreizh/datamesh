import random
from dataclasses import replace
import pytest
from datamesh_formal.certified import Obligation, Rule, Policy, Certificate, Node, Mesh

O=Obligation('sales','no-raw-export')
P=Policy('sales',1,(Rule(O,'aggregate','aggregate-certified'),))
C=Certificate(O,'sales',1,'aggregate','aggregate-certified')

def example():
    return Mesh([Node('source',introduced=frozenset({O})),
                 Node('aggregate',('source',),'aggregate',facts=frozenset({'aggregate-certified'}),certificates=(C,)),
                 Node('report',('aggregate',),'copy')],[P])

def test_valid_discharge():
    m=example(); m.initialize(); assert m.cache['aggregate'].valid
    assert not m.cache['aggregate'].residual

def test_revocation_and_recovery():
    m=example(); m.initialize()
    m.update(policy_changes={'sales':replace(P,version=2)})
    assert not m.cache['aggregate'].valid and O in m.cache['report'].residual
    assert m.cache==m.full()
    m.update(policy_changes={'sales':P})
    assert m.cache['report'].valid and not m.cache['report'].residual

def test_wrong_issuer_cannot_discharge():
    m=example(); m.initialize()
    bad=replace(C,issuer='other')
    m.update(node_changes={'aggregate':replace(m.nodes['aggregate'],certificates=(bad,))})
    assert not m.cache['aggregate'].valid and O in m.cache['aggregate'].residual

def test_missing_fact_cannot_discharge():
    m=example(); m.initialize()
    m.update(node_changes={'aggregate':replace(m.nodes['aggregate'],facts=frozenset())})
    assert not m.cache['aggregate'].valid and O in m.cache['report'].residual

def test_duplicate_certificate_cannot_mask_invalid():
    m=example(); m.initialize()
    bad=replace(C,version=999)
    m.update(node_changes={'aggregate':replace(m.nodes['aggregate'],certificates=(C,bad))})
    assert O in m.cache['aggregate'].residual and not m.cache['aggregate'].valid

def test_cross_domain_provenance():
    other=Obligation('finance','noncommercial')
    m=example(); m.initialize()
    m.update(node_changes={'source':replace(m.nodes['source'],introduced=frozenset({O,other}))})
    assert other in m.cache['aggregate'].residual
    assert O not in m.cache['aggregate'].residual

def test_random_differential():
    for seed in range(12):
        rng=random.Random(seed)
        nodes=[Node('n0',introduced=frozenset({O}))]
        for i in range(1,75):
            parents=tuple(f'n{j}' for j in sorted(rng.sample(range(i),min(i,3))))
            nodes.append(Node(f'n{i}',parents,'copy'))
        meshes=[Mesh(nodes,[P]) for _ in range(3)]
        for m in meshes:m.initialize()
        for k in range(24):
            i=rng.randrange(75); n=meshes[0].nodes[f'n{i}']
            updated=replace(n,introduced=frozenset({O}) if rng.random()<.5 else frozenset())
            for m,strategy in zip(meshes,('igv','desc','full')):
                m.update(node_changes={updated.id:updated},strategy=strategy)
            assert meshes[0].cache==meshes[1].cache==meshes[2].cache

def test_cycle_rejected():
    with pytest.raises(ValueError): Mesh([Node('a',('b',),'copy'),Node('b',('a',),'copy')],[P])
