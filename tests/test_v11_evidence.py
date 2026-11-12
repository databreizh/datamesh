from dataclasses import replace
import random
import pytest
from datamesh_formal.evidence import (row, DataNode, ScopedObligation, EvidenceRule,
    EvidenceMesh, make_evidence_certificate)


def example():
    a, b = ScopedObligation('A', 'remove_email', 'a'), ScopedObligation('A', 'remove_email', 'b')
    rule = EvidenceRule('A', 'r', 'remove_email', 'absent_field', 'email')
    s1 = DataNode('a', records=(row(id=1, email='x'),), introduced=frozenset({a}))
    s2 = DataNode('b', records=(row(id=2, email='y'),), introduced=frozenset({b}))
    output = DataNode('c', ('a', 'b'), 'project', 'id', (row(id=1), row(id=2)))
    cert = make_evidence_certificate(output, a, rule, s1.records + s2.records)
    return [s1, s2, replace(output, certificates=(cert,))], rule, a, b


def test_origin_isolation():
    nodes, rule, a, b = example()
    result = EvidenceMesh(nodes, [rule]).full()['c']
    assert result.valid and result.residual == frozenset({b})
    assert result.discharged == (a,)


def test_both_scoped_certificates():
    nodes, rule, a, b = example()
    cert2 = make_evidence_certificate(nodes[-1], b, rule, nodes[0].records + nodes[1].records)
    nodes[-1] = replace(nodes[-1], certificates=nodes[-1].certificates + (cert2,))
    result = EvidenceMesh(nodes, [rule]).full()['c']
    assert result.valid and not result.residual


def test_forged_output_rejected():
    nodes, rule, _, _ = example()
    nodes[-1] = replace(nodes[-1], records=(row(id=999),))
    result = EvidenceMesh(nodes, [rule]).full()['c']
    assert not result.valid and 'output_mismatch@c' in result.errors


def test_stale_data_digest_rejected():
    nodes, rule, a, _ = example()
    nodes[0] = replace(nodes[0], records=(row(id=3, email='z'),))
    result = EvidenceMesh(nodes, [rule]).full()['c']
    assert not result.valid and a in result.residual


def test_rule_mutation_rejected():
    nodes, rule, a, _ = example()
    changed = replace(rule, parameter='id')
    result = EvidenceMesh(nodes, [changed]).full()['c']
    assert not result.valid and a in result.residual


def test_invalid_duplicate_does_not_mask():
    nodes, rule, a, _ = example()
    bad = replace(nodes[-1].certificates[0], output_digest='forged')
    nodes[-1] = replace(nodes[-1], certificates=nodes[-1].certificates + (bad,))
    result = EvidenceMesh(nodes, [rule]).full()['c']
    assert not result.valid and a in result.residual


def test_group_count_threshold():
    obligation = ScopedObligation('A', 'minimum_2', 's')
    rule = EvidenceRule('A', 'r', 'minimum_2', 'min_group_count', '2')
    s = DataNode('s', records=(row(g='x'), row(g='x'), row(g='y')),
                 introduced=frozenset({obligation}))
    g = DataNode('g', ('s',), 'group_count', 'g', (row(g='x', count=2), row(g='y', count=1)))
    c = make_evidence_certificate(g, obligation, rule, s.records)
    result = EvidenceMesh([s, replace(g, certificates=(c,))], [rule]).full()['g']
    assert not result.valid and obligation in result.residual


def test_valid_group_count():
    obligation = ScopedObligation('A', 'minimum_2', 's')
    rule = EvidenceRule('A', 'r', 'minimum_2', 'min_group_count', '2')
    s = DataNode('s', records=(row(g='x'), row(g='x')),
                 introduced=frozenset({obligation}))
    g = DataNode('g', ('s',), 'group_count', 'g', (row(g='x', count=2),))
    c = make_evidence_certificate(g, obligation, rule, s.records)
    result = EvidenceMesh([s, replace(g, certificates=(c,))], [rule]).full()['g']
    assert result.valid and not result.residual


def test_random_differential():
    for seed in range(8):
        rng = random.Random(seed)
        nodes, rule, _, _ = example()
        m = EvidenceMesh(nodes, [rule]); m.initialize()
        for i in range(30):
            if i % 4 == 0:
                new_rule = replace(m.rules[('A','r')], parameter='email' if i % 8 else 'id')
                m.update(rule_changes={('A','r'):new_rule})
            elif i % 4 == 1:
                source = m.nodes['a']
                m.update(node_changes={'a':replace(source, records=(row(id=rng.randrange(10), email='x'),))})
            elif i % 4 == 2:
                c = m.nodes['c']
                m.update(node_changes={'c':replace(c, records=(row(id=rng.randrange(10)), row(id=2)))})
            else:
                c = m.nodes['c']
                m.update(node_changes={'c':replace(c, certificates=())})
            assert m.cache == m.full()


def test_reject_invalid_origin():
    with pytest.raises(ValueError):
        EvidenceMesh([DataNode('s', introduced=frozenset({ScopedObligation('A','x','other')}))], [])
