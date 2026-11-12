"""Adversarial and metamorphic regression tests for version-bound discharge."""
from dataclasses import replace
import random

from datamesh_formal.certified import Mesh, Node, Policy, Obligation, Rule, Certificate

O = Obligation('sales', 'no-export')
RULE = Rule(O, 'aggregate', 'aggregated')


def meshes():
    cert = Certificate(O, 'sales', 1, 'aggregate', 'aggregated')
    nodes = [Node('source', introduced=frozenset({O})),
             Node('agg', ('source',), 'aggregate', facts=frozenset({'aggregated'}), certificates=(cert,)),
             Node('down', ('agg',), 'copy'), Node('independent')]
    return {s: Mesh(nodes, [Policy('sales', 1, (RULE,))]) for s in ('full', 'desc', 'igv')}


def apply_all(ms, changes=None, policies=None):
    stats = {s: m.update(changes, policies, strategy=s) for s,m in ms.items()}
    assert ms['full'].cache == ms['desc'].cache == ms['igv'].cache
    return stats


def test_policy_revocation_invalidates_discharge_and_descendants():
    ms = meshes()
    for m in ms.values(): m.initialize()
    assert O not in ms['igv'].cache['agg'].residual
    apply_all(ms, policies={'sales': Policy('sales', 2, ())})
    assert O in ms['igv'].cache['agg'].residual
    assert not ms['igv'].cache['agg'].valid
    assert not ms['igv'].cache['down'].valid


def test_fact_revocation_blocks_discharge():
    ms = meshes()
    for m in ms.values(): m.initialize()
    n = ms['full'].nodes['agg']
    apply_all(ms, changes={'agg': replace(n, facts=frozenset())})
    assert O in ms['igv'].cache['agg'].residual
    assert not ms['igv'].cache['down'].valid


def test_same_residual_but_invalidity_propagates():
    ms = meshes()
    for m in ms.values(): m.initialize()
    n = ms['full'].nodes['agg']
    # Initially valid discharge; after a mismatching certificate, residual changes.
    bad = Certificate(O, 'sales', 99, 'aggregate', 'aggregated')
    apply_all(ms, changes={'agg': replace(n, certificates=n.certificates+(bad,))})
    assert O in ms['igv'].cache['agg'].residual
    assert not ms['igv'].cache['down'].valid
    # Replace with a second bad certificate: residual remains unchanged,
    # but error message and result may change; differential equality still holds.
    n2 = ms['full'].nodes['agg']
    apply_all(ms, changes={'agg': replace(n2, certificates=(bad,))})


def test_noop_stops_before_descendants():
    ms = meshes()
    for m in ms.values(): m.initialize()
    n = ms['full'].nodes['source']
    stats = apply_all(ms, changes={'source': replace(n)})
    assert stats['igv'].recomputed == 1
    assert stats['desc'].recomputed == 3


def test_foreign_issuer_cannot_discharge():
    wrong = Certificate(O, 'marketing', 1, 'aggregate', 'aggregated')
    nodes = [Node('s', introduced=frozenset({O})),
             Node('a', ('s',), 'aggregate', facts=frozenset({'aggregated'}), certificates=(wrong,))]
    m = Mesh(nodes, [Policy('sales',1,(RULE,)), Policy('marketing',1,(RULE,))])
    result = m.full()['a']
    assert O in result.residual and not result.valid


def test_randomized_policy_and_node_edit_sequences():
    from importlib.util import spec_from_file_location, module_from_spec
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'examples' / 'benchmark_v08.py'
    spec = spec_from_file_location('benchmark_v08', path)
    mod = module_from_spec(spec); spec.loader.exec_module(mod)
    for topology in ('dag', 'tree', 'chain', 'layered'):
        nodes, policies, obligation = mod.build(60, 100, topology, .25)
        ms = {s: Mesh(nodes, policies) for s in ('full','desc','igv')}
        for m in ms.values(): m.initialize()
        rng = random.Random(123)
        for step in range(35):
            changes, pols, _ = mod.edit(ms['full'], rng, step, 'mixed', obligation)
            apply_all(ms, changes, pols)
