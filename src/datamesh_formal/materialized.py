"""V0.17 end-to-end finite-record rematerialization, fixed DAG.

Trusted source updates trigger actual operator evaluation. The materialized
outputs are then checked by V0.14 or V0.15. This is NOT a distributed engine.
"""
from dataclasses import replace
from time import perf_counter_ns
from .evidence import canonical_rows, expected_output
from .lifecycle import recompute
from .lifecycle_incremental import LifecycleIGV


def derive(node, by_id):
    """Compute actual output from materialized parent records (including join)."""
    if node.operation == 'source':
        return canonical_rows(node.records)
    if node.operation == 'join':
        if len(node.inputs) != 2 or not node.parameter:
            raise ValueError('join requires two parents and a key')
        left, right = (by_id[p].records for p in node.inputs)
        return canonical_rows(
            tuple(sorted({**dict(a), **dict(b)}.items()))
            for a in left for b in right
            if dict(a).get(node.parameter) is not None
            and dict(a).get(node.parameter) == dict(b).get(node.parameter)
            and all(k not in dict(a) or dict(a)[k] == v for k, v in b)
        )
    parents = tuple(r for p in node.inputs for r in by_id[p].records)
    return expected_output(node, parents)


class MaterializedMesh:
    """V0.15 lifecycle engine with real source-to-descendant data refresh.

    Incremental refresh visits only reachable descendants of changed sources;
    it evaluates operators when parent rows change. Governance propagation is
    delegated to the unchanged V0.15 engine. The V0.14 oracle is independent.
    """
    def __init__(self, nodes, requirements, attestations=(), boundaries=frozenset()):
        self.engine = LifecycleIGV(nodes, requirements, attestations, boundaries)
        self.nodes = self.engine.nodes
        # Reject stale initial derived records instead of silently repairing them.
        for name in self.engine.order:
            if self.nodes[name].operation != 'source':
                if canonical_rows(self.nodes[name].records) != derive(self.nodes[name], self.nodes):
                    raise ValueError('initial derived output mismatch at '+name)

    def full_sync(self, *, sources=()):
        """V0.26 full data refresh with *preserved* governance cache.

        Full materialization is computed independently, then only changed node
        records are patched into the existing LifecycleIGV engine. Unaffected
        governance states are retained. Unlike reconstructing MaterializedMesh,
        this does not rebuild the graph, policy indexes or all cached states.
        The complete data refresh remains O(full graph); this is NOT a partial
        data rematerialization or a free switch.

        Fixed source-only edits; no policy or graph changes.
        """
        start = perf_counter_ns()
        edits = tuple(sources)
        if len({s.id for s in edits}) != len(edits):
            raise ValueError('duplicate source edit')
        for s in edits:
            if s.id not in self.nodes or self.nodes[s.id].operation != 'source':
                raise ValueError('source edits only')
            if s.inputs != self.nodes[s.id].inputs or s.operation != 'source':
                raise ValueError('fixed source structure')
        fresh = full_refresh(tuple(self.nodes.values()), sources=edits)
        materialize_ms = (perf_counter_ns() - start) / 1e6
        patches = [n for n in fresh if n != self.nodes[n.id]]
        gov_start = perf_counter_ns()
        stats = self.engine.update(nodes=patches if patches else None)
        governance_ms = (perf_counter_ns() - gov_start) / 1e6
        # self.nodes and self.engine.nodes are the same dictionary.
        return {**stats, 'materialize_ms': materialize_ms,
                'governance_ms': governance_ms,
                'measured_total_ms': materialize_ms + governance_ms,
                'materialized_changed': len(patches),
                'operators_evaluated': sum(n.operation != 'source' for n in fresh),
                'cache_rebuilt': False}

    def decisions(self):
        return self.engine.decisions()

    def update(self, *, sources=(), requirements=None, attestations=None,
               boundaries=None, strategy='igv'):
        """Atomic trusted source patches; no graph edits or derived-row assertions.

        All updated source records are installed together. A topological walk
        computes actual descendant outputs. Identical outputs are not patched.
        Returns both materialization and governance recomputation counts.
        """
        start_ns = perf_counter_ns()
        edits = tuple(sources)
        if len({s.id for s in edits}) != len(edits):
            raise ValueError('duplicate source edit')
        for s in edits:
            if s.id not in self.nodes or self.nodes[s.id].operation != 'source':
                raise ValueError('source edits only')
            if s.inputs != self.nodes[s.id].inputs or s.operation != 'source':
                raise ValueError('fixed source structure')
        changed = set()
        patch = {}
        working = dict(self.nodes)
        for s in edits:
            if s != self.nodes[s.id]:
                changed.add(s.id)
                patch[s.id] = s
                working[s.id] = s
        visited = 0
        evaluated = 0
        for name in self.engine.order:
            node = working[name]
            if node.operation == 'source' or not any(p in changed for p in node.inputs):
                continue
            visited += 1
            output = derive(node, working)
            evaluated += 1
            if canonical_rows(node.records) != output:
                patch[name] = replace(node, records=output)
                working[name] = patch[name]
                changed.add(name)
        materialize_ms = (perf_counter_ns()-start_ns)/1e6
        governance_start_ns = perf_counter_ns()
        stats = self.engine.update(nodes=list(patch.values()) if patch else None,
                                   requirements=requirements, attestations=attestations,
                                   boundaries=boundaries, strategy=strategy)
        governance_ms = (perf_counter_ns()-governance_start_ns)/1e6
        return {**stats, 'materialize_ms':materialize_ms,
                'governance_ms':governance_ms,
                'measured_total_ms':materialize_ms+governance_ms,
                'materialized_changed':len(changed),
                'operators_evaluated':evaluated, 'derived_visited':visited}


def full_refresh(nodes, sources=()):
    """Independent full materialization from trusted source records."""
    by_id = {n.id:n for n in nodes}
    for s in sources:
        if s.id not in by_id or by_id[s.id].operation != 'source':
            raise ValueError('source edits only')
        if s.inputs != by_id[s.id].inputs:
            raise ValueError('fixed graph')
        by_id[s.id] = s
    # Construct order independently; detect missing inputs/cycles.
    result = {}; visiting = set()
    def visit(name):
        if name in result: return
        if name in visiting: raise ValueError('cycle')
        if name not in by_id: raise ValueError('missing parent')
        visiting.add(name)
        node = by_id[name]
        for p in node.inputs: visit(p)
        result[name] = node if node.operation == 'source' else replace(
            node, records=derive(node, result))
        visiting.remove(name)
    for name in sorted(by_id): visit(name)
    return list(result.values())


def full_verify(nodes, requirements, attestations=(), boundaries=frozenset()):
    refreshed = full_refresh(nodes)
    return refreshed, recompute(refreshed, requirements, attestations, boundaries)
