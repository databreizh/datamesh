from dataclasses import replace
import random
import pytest
from datamesh_formal.certified import Node, Obligation
from datamesh_formal.semantic import (SemanticRule, SemanticPolicy, SemanticMesh,
                                      make_certificate, fingerprints)


def fixture():
    a,b = Obligation('sales','privacy'), Obligation('sales','audit')
    p = SemanticPolicy('sales',1,(SemanticRule('base',a,'aggregate','k5'),
                                   SemanticRule('derived',b,'aggregate','signed',('base',))))
    nodes=[Node('root',introduced=frozenset({a,b})),
           Node('left',('root',),'aggregate',facts=frozenset({'k5'}),certificates=(make_certificate(p,'base'),)),
           Node('right',('left',),'aggregate',facts=frozenset({'signed'}),certificates=(make_certificate(p,'derived'),))]
    return nodes,p


def test_revision_only_preserves_certificates_and_no_recompute():
    nodes,p=fixture(); m=SemanticMesh(nodes,[p]);m.initialize()
    before=m.cache.copy()
    s=m.update(policy_changes={'sales':replace(p,revision=2)})
    assert s.recomputed==0 and m.cache==before==m.full()


def test_rule_semantic_change_invalidates_own_and_dependent():
    nodes,p=fixture();m=SemanticMesh(nodes,[p]);m.initialize()
    changed=replace(p,rules=(replace(p.rules[0],required_fact='k10'),p.rules[1]))
    s=m.update(policy_changes={'sales':changed})
    assert s.recomputed>=2 and not m.cache['left'].valid
    assert not m.cache['right'].valid and m.cache==m.full()
    assert fingerprints(changed)['derived']!=fingerprints(p)['derived']


def test_revocation_transitive_and_recovery():
    nodes,p=fixture();m=SemanticMesh(nodes,[p]);m.initialize()
    revoked=replace(p,revoked=frozenset({'base'}),revision=2)
    m.update(policy_changes={'sales':revoked})
    assert not m.cache['left'].valid and not m.cache['right'].valid
    assert m.cache==m.full()
    m.update(policy_changes={'sales':replace(p,revision=3)})
    assert all(r.valid for r in m.cache.values()) and m.cache==m.full()


def test_unrelated_rule_change_is_not_seeded():
    nodes,p=fixture();m=SemanticMesh(nodes,[p]);m.initialize()
    extra=SemanticRule('unused',Obligation('sales','other'),'aggregate','ok')
    s=m.update(policy_changes={'sales':replace(p,rules=p.rules+(extra,))})
    assert s.recomputed==0 and m.cache==m.full()


def test_invalid_old_fingerprint_cannot_be_reused():
    nodes,p=fixture();changed=replace(p,rules=(replace(p.rules[0],required_fact='k10'),p.rules[1]))
    m=SemanticMesh(nodes,[changed]);m.initialize()
    assert not m.cache['left'].valid


def test_invalid_rule_graphs():
    a=Obligation('sales','a')
    with pytest.raises(ValueError,match='cyclic'):
        fingerprints(SemanticPolicy('sales',1,(SemanticRule('x',a,'aggregate','ok',('y',)),SemanticRule('y',a,'aggregate','ok',('x',)))))
    with pytest.raises(ValueError,match='unknown'):
        fingerprints(SemanticPolicy('sales',1,(SemanticRule('x',a,'aggregate','ok',('missing',)),)))
    with pytest.raises(ValueError,match='duplicate'):
        fingerprints(SemanticPolicy('sales',1,(SemanticRule('x',a,'aggregate','ok'),SemanticRule('x',a,'aggregate','ok'))))


def test_randomized_differential():
    nodes,p=fixture();m=SemanticMesh(nodes,[p]);m.initialize();rng=random.Random(23)
    for i in range(200):
        pol=m.policies['sales']; op=rng.randrange(5)
        if op==0: new=replace(pol,revision=pol.revision+1)
        elif op==1: new=replace(pol,revoked=frozenset() if pol.revoked else frozenset({'base'}))
        elif op==2:
            r=pol.rules[0]; new=replace(pol,rules=(replace(r,required_fact='k10' if r.required_fact=='k5' else 'k5'),pol.rules[1]))
        elif op==3:
            r=pol.rules[1]; new=replace(pol,rules=(pol.rules[0],replace(r,required_fact='signed2' if r.required_fact=='signed' else 'signed')))
        else: new=pol
        m.update(policy_changes={'sales':new},strategy='igv')
        assert m.cache==m.full(),i
