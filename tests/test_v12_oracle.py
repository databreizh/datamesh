from dataclasses import replace
import random
import pytest
from datamesh_formal.evidence import (row, DataNode, ScopedObligation,
    EvidenceRule, EvidenceMesh, make_evidence_certificate)
from datamesh_formal.oracle_v12 import verify


def compare(mesh):
    expected = verify(list(mesh.nodes.values()), list(mesh.rules.values()))
    actual = mesh.full()
    for name, (valid, residual, discharged, digest) in expected.items():
        assert actual[name].valid == valid, name
        assert actual[name].residual == residual, name
        assert set(actual[name].discharged) == discharged, name
        assert actual[name].output_digest == digest, name
    assert mesh.cache == actual


def graph(topology):
    a = ScopedObligation('A', 'remove_email', 'a')
    b = ScopedObligation('A', 'remove_email', 'b')
    rule = EvidenceRule('A', 'r', 'remove_email', 'absent_field', 'email')
    nodes = [DataNode('a', records=(row(id=1, group='x', email='secret'),),
                      introduced=frozenset({a})),
             DataNode('b', records=(row(id=1, group='x', email='other'),),
                      introduced=frozenset({b}))]
    if topology == 'chain':
        nodes += [DataNode('c', ('a',), 'project', 'id,group', (row(id=1, group='x'),)),
                  DataNode('d', ('c',), 'filter_eq', 'group=x', (row(id=1, group='x'),))]
    elif topology == 'tree':
        nodes += [DataNode('c', ('a',), 'project', 'id,group', (row(id=1, group='x'),)),
                  DataNode('d', ('a',), 'filter_eq', 'group=x', nodes[0].records)]
    elif topology == 'dag':
        nodes += [DataNode('c', ('a',), 'project', 'id,group', (row(id=1, group='x'),)),
                  DataNode('d', ('c','b'), 'join', 'id', (row(id=1, group='x', email='other'),))]
    elif topology == 'layered':
        nodes += [DataNode('c', ('a','b'), 'project', 'id,group', (row(id=1, group='x'),)*2),
                  DataNode('d', ('c',), 'group_count', 'group', (row(group='x', count=2),))]
    return EvidenceMesh(nodes, [rule])


@pytest.mark.parametrize('topology', ['chain','tree','dag','layered'])
def test_independent_oracle_across_topologies(topology):
    mesh = graph(topology)
    mesh.initialize()
    compare(mesh)
    rng = random.Random(117)
    for i in range(50):
        if i % 4 == 0:
            current = mesh.nodes['a']
            changed = replace(current, records=(row(id=rng.choice([1,2]), group='x', email='secret'),))
            mesh.update(node_changes={'a': changed})
        elif i % 4 == 1:
            current = mesh.nodes['c']
            changed = replace(current, records=(row(id=rng.choice([1,2]), group='x'),))
            mesh.update(node_changes={'c': changed})
        elif i % 4 == 2:
            mesh.update(rule_changes={('A','r'):replace(mesh.rules[('A','r')], parameter='id')})
        else:
            mesh.update(rule_changes={('A','r'):replace(mesh.rules[('A','r')], parameter='email')})
        compare(mesh)


def test_provenance_cannot_be_laundered():
    mesh = graph('layered')
    o1, o2 = next(iter(mesh.nodes['a'].introduced)), next(iter(mesh.nodes['b'].introduced))
    node = mesh.nodes['c']
    cert = make_evidence_certificate(node, o1, mesh.rules[('A','r')],
                                     mesh.nodes['a'].records + mesh.nodes['b'].records)
    mesh.update(node_changes={'c':replace(node, certificates=(cert,))}) if mesh.cache else None
    mesh.initialize()
    mesh.update(node_changes={'c':replace(node, certificates=(cert,))})
    compare(mesh)
    assert o2 in mesh.cache['c'].residual
    assert o1 not in mesh.cache['c'].residual


def test_tampered_certificate_fails_independent_oracle():
    mesh = graph('chain')
    o = next(iter(mesh.nodes['a'].introduced))
    node = mesh.nodes['c']
    cert = make_evidence_certificate(node, o, mesh.rules[('A','r')], mesh.nodes['a'].records)
    mesh.initialize()
    mesh.update(node_changes={'c':replace(node, certificates=(replace(cert, input_digest='forged'),))})
    compare(mesh)
    assert not mesh.cache['c'].valid
    assert o in mesh.cache['c'].residual


def test_join_rejects_conflicting_shared_fields():
    mesh = graph('dag')
    mesh.initialize()
    compare(mesh)
    assert mesh.cache['d'].valid
    mesh.update(node_changes={'b':replace(mesh.nodes['b'], records=(row(id=1, group='y', email='other'),))})
    compare(mesh)
    assert not mesh.cache['d'].valid


def test_invalid_filter_and_group_are_rejected():
    mesh = graph('layered')
    mesh.initialize()
    mesh.update(node_changes={'d':replace(mesh.nodes['d'], parameter='missing')})
    compare(mesh)
    assert not mesh.cache['d'].valid
