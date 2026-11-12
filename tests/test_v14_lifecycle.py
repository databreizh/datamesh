from datamesh_formal.evidence import DataNode, row
from datamesh_formal.lifecycle import Requirement, Mode, Attestation, recompute


def scenario(mode):
    req = Requirement('health', 'no_identifier', 'health', mode, 'absent:patient_id')
    nodes = [
        DataNode('health', records=(row(key='1', patient_id='sensitive'),), introduced=frozenset()),
        DataNode('research', records=(row(key='1', patient_id='reintroduced'),)),
        DataNode('clean', ('health',), 'project', 'key', (row(key='1'),)),
        DataNode('merged', ('clean', 'research'), 'join', 'key', (row(key='1', patient_id='reintroduced'),)),
    ]
    return nodes, req


def test_persistent_reintroduced_field_is_rejected():
    nodes, req = scenario(Mode.PERSISTENT)
    states = recompute(nodes, [req], boundaries=frozenset({'clean', 'merged'}))
    assert states['clean'].publishable
    assert not states['merged'].publishable
    assert req in states['merged'].violations
    assert req in states['merged'].lineage


def test_boundary_rechecks_after_join():
    nodes, req = scenario(Mode.BOUNDARY)
    states = recompute(nodes, [req], boundaries=frozenset({'clean', 'merged'}))
    assert states['clean'].publishable
    assert not states['merged'].publishable
    assert req in states['merged'].violations


def test_once_attestation_persists_but_not_state_predicate():
    nodes, _ = scenario(Mode.ONCE)
    req = Requirement('health', 'audit', 'health', Mode.ONCE, 'action:audited')
    states = recompute(nodes, [req], [Attestation(req, 'clean', 'audited')], frozenset({'clean', 'merged'}))
    assert states['clean'].publishable
    assert states['merged'].publishable
    assert req in states['merged'].lineage
    assert not states['merged'].pending


def test_one_time_missing_attestation_blocks():
    nodes, _ = scenario(Mode.ONCE)
    req = Requirement('health', 'audit', 'health', Mode.ONCE, 'action:audited')
    states = recompute(nodes, [req], boundaries=frozenset({'merged'}))
    assert not states['merged'].publishable
    assert req in states['merged'].pending


def test_homonymous_obligations_are_distinct():
    nodes, _ = scenario(Mode.BOUNDARY)
    a = Requirement('health', 'redact', 'health', Mode.BOUNDARY, 'absent:patient_id')
    b = Requirement('research', 'redact', 'research', Mode.BOUNDARY, 'absent:patient_id')
    states = recompute(nodes, [a, b], boundaries=frozenset({'merged'}))
    assert states['merged'].violations == frozenset({a, b})


def test_bad_output_blocks_even_when_predicate_passes():
    nodes, req = scenario(Mode.BOUNDARY)
    nodes[-1] = DataNode('merged', ('clean', 'research'), 'join', 'key', (row(key='1'),))
    states = recompute(nodes, [req], boundaries=frozenset({'merged'}))
    assert not states['merged'].publishable
    assert any('output_mismatch' in x for x in states['merged'].errors)


def test_no_attestation_can_discharge_persistent():
    nodes, req = scenario(Mode.PERSISTENT)
    states = recompute(nodes, [req], [Attestation(req, 'clean', 'audited')], frozenset({'merged'}))
    assert req in states['merged'].violations


def test_cycle_rejected():
    a = DataNode('a', ('b',), 'project', 'key', ())
    b = DataNode('b', ('a',), 'project', 'key', ())
    try:
        recompute([a, b], [])
    except ValueError as exc:
        assert 'cycle' in str(exc)
    else:
        assert False
