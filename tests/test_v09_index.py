from dataclasses import replace
import random
from datamesh_formal.certified import Mesh, Node, Policy, Rule, Obligation, Certificate
from datamesh_formal.indexed import IndexedMesh


def setup():
    o1, o2 = Obligation('sales','raw'), Obligation('sales','commercial')
    p = Policy('sales',1,(Rule(o1,'aggregate','safe'), Rule(o2,'aggregate','safe')))
    nodes = [Node('root',introduced=frozenset({o1,o2}))]
    for i in range(1,25):
        o = o1 if i % 2 else o2
        nodes.append(Node(f'n{i}',('root',),'aggregate',facts=frozenset({'safe'}),
                          certificates=(Certificate(o,'sales',1,'aggregate','safe'),)))
    return nodes,p,o1,o2


def test_rule_specific_invalidation():
    nodes,p,o1,o2 = setup()
    base,idx = Mesh(nodes,[p]),IndexedMesh(nodes,[p])
    base.initialize();idx.initialize()
    new = replace(p,rules=(Rule(o2,'aggregate','safe'),))
    sb=base.update(policy_changes={'sales':new},strategy='igv')
    si=idx.update(policy_changes={'sales':new},strategy='igv')
    assert base.cache==idx.cache
    assert si.recomputed < sb.recomputed
    assert si.recomputed == 12


def test_version_bump_cannot_skip_unmodified_rules():
    nodes,p,o1,o2 = setup()
    base,idx = Mesh(nodes,[p]),IndexedMesh(nodes,[p])
    base.initialize();idx.initialize()
    new=replace(p,version=2)
    base.update(policy_changes={'sales':new},strategy='igv')
    stats=idx.update(policy_changes={'sales':new},strategy='igv')
    assert base.cache==idx.cache
    assert stats.recomputed==24
    assert all(not idx.cache[f'n{i}'].valid for i in range(1,25))


def test_rule_addition_revalidates_previously_invalid_certificates():
    nodes,p,o1,o2=setup()
    p=replace(p,rules=())
    base,idx=Mesh(nodes,[p]),IndexedMesh(nodes,[p])
    base.initialize();idx.initialize()
    new=replace(p,rules=(Rule(o1,'aggregate','safe'),))
    base.update(policy_changes={'sales':new},strategy='igv')
    idx.update(policy_changes={'sales':new},strategy='igv')
    assert base.cache==idx.cache
    assert idx.cache['n1'].valid
    assert not idx.cache['n2'].valid


def test_randomized_rule_edits_and_node_changes():
    nodes,p,o1,o2=setup()
    base,idx=Mesh(nodes,[p]),IndexedMesh(nodes,[p])
    base.initialize();idx.initialize()
    rng=random.Random(7)
    for j in range(100):
        if j % 4 == 0:
            pol=base.policies['sales']
            new=replace(pol,version=pol.version+1)
            changes={}; policies={'sales':new}
        elif j % 4 == 1:
            pol=base.policies['sales']
            rules=tuple(r for r in pol.rules if r.obligation != o1) if any(r.obligation==o1 for r in pol.rules) else pol.rules+(Rule(o1,'aggregate','safe'),)
            changes={};policies={'sales':replace(pol,rules=rules)}
        else:
            name=f'n{rng.randrange(1,25)}'
            node=base.nodes[name]
            changes={name:replace(node,facts=node.facts ^ frozenset({'safe'}))};policies={}
        base.update(changes,policies,strategy='igv')
        idx.update(changes,policies,strategy='igv')
        assert base.cache==idx.cache, j
        assert idx.cache==idx.full(), j
