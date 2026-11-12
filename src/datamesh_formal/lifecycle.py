"""V0.14 research semantics: provenance-aware obligation lifecycle.

Finite, trusted sources and exact relational operators. No GDPR compliance claim.
An obligation is never deleted from lineage by a certificate. Persistent and
boundary requirements are evaluated against the CURRENT descendant output.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from collections import defaultdict
from .evidence import DataNode, Row, canonical_rows, expected_output

class Mode(str, Enum):
    ONCE = 'once'
    PERSISTENT = 'persistent'
    BOUNDARY = 'boundary'

@dataclass(frozen=True, order=True)
class Requirement:
    issuer: str
    name: str
    origin: str
    mode: Mode
    predicate: str  # absent:field, min_count:field:threshold, or action:label

@dataclass(frozen=True)
class Attestation:
    requirement: Requirement
    node_id: str
    action: str  # trusted, explicitly logged one-time action; not a cryptographic proof

@dataclass(frozen=True)
class Decision:
    node_id: str
    lineage: frozenset[Requirement]
    pending: frozenset[Requirement]
    violations: frozenset[Requirement]
    publishable: bool
    errors: tuple[str, ...]


def check_predicate(spec: str, records: tuple[Row, ...]) -> bool:
    parts = spec.split(':')
    if len(parts) == 2 and parts[0] == 'absent' and parts[1]:
        return all(parts[1] not in dict(r) for r in records)
    if len(parts) == 3 and parts[0] == 'min_count' and parts[1]:
        try:
            minimum = int(parts[2])
        except ValueError as exc:
            raise ValueError('invalid threshold') from exc
        if minimum < 1 or not records:
            return False
        # Exact groups in the current output, not an unverifiable declared count.
        groups = defaultdict(int)
        for r in records:
            values = dict(r)
            if parts[1] not in values:
                return False
            groups[values[parts[1]]] += 1
        return all(count >= minimum for count in groups.values())
    raise ValueError(f'unsupported predicate: {spec}')


def recompute(nodes: list[DataNode], requirements: list[Requirement],
              attestations: list[Attestation] = (), boundaries: frozenset[str] = frozenset()):
    """Independent full reference evaluator; fail closed on invalid operations.

    Inputs: finite DAG of asserted records. All outputs are checked against the
    specified operator. The lineage follows graph reachability, not row-level
    provenance. Empty outputs still carry lineage (conservative choice).
    """
    by_id = {n.id: n for n in nodes}
    if len(by_id) != len(nodes):
        raise ValueError('duplicate node')
    if not boundaries <= by_id.keys():
        raise ValueError('unknown boundary')
    introduced = defaultdict(set)
    for req in requirements:
        if req.origin not in by_id:
            raise ValueError('unknown obligation origin')
        if req.mode == Mode.ONCE and not req.predicate.startswith('action:'):
            raise ValueError('one-time obligations require an action label')
        if req.mode != Mode.ONCE and req.predicate.startswith('action:'):
            raise ValueError('action is not a state predicate')
        introduced[req.origin].add(req)
    if len(set(requirements)) != len(requirements):
        raise ValueError('duplicate requirement')
    acts = defaultdict(set)
    for a in attestations:
        if a.node_id not in by_id:
            raise ValueError('unknown attestation node')
        acts[a.node_id].add((a.requirement, a.action))
    results = {}
    visiting = set()
    def visit(name):
        if name in results:
            return results[name]
        if name in visiting:
            raise ValueError('cycle')
        visiting.add(name)
        node = by_id[name]
        parents = []
        for pid in node.inputs:
            if pid not in by_id:
                raise ValueError('unknown parent')
            parents.append(visit(pid))
        lineage = set(introduced[name])
        done = set()
        errors = []
        for p in parents:
            lineage.update(p['lineage'])
            done.update(p['done'])
            errors.extend(p['errors'])
        if (not node.inputs) != (node.operation == 'source'):
            errors.append(f'invalid_source_shape@{name}')
        try:
            parent_rows = tuple(r for pid in node.inputs for r in by_id[pid].records)
            if node.operation == 'join':
                if len(node.inputs) != 2 or not node.parameter:
                    raise ValueError('join requires two parents and a key')
                left, right = (by_id[pid].records for pid in node.inputs)
                expected = canonical_rows(
                    tuple(sorted(dict(a, **dict(b)).items()))
                    for a in left for b in right
                    if dict(a).get(node.parameter) is not None
                    and dict(a).get(node.parameter) == dict(b).get(node.parameter)
                    and all(k not in dict(a) or dict(a)[k] == v for k, v in b)
                )
            else:
                expected = expected_output(node, parent_rows)
            if expected != canonical_rows(node.records):
                errors.append(f'output_mismatch@{name}')
        except (ValueError, TypeError) as exc:
            errors.append(f'operation_error@{name}:{exc}')
        for req in lineage:
            if req.mode == Mode.ONCE and (req, req.predicate[7:]) in acts[name]:
                done.add(req)
        pending = {r for r in lineage if r.mode == Mode.ONCE and r not in done}
        violations = set()
        for req in lineage:
            if req.mode == Mode.PERSISTENT or (req.mode == Mode.BOUNDARY and name in boundaries):
                try:
                    if not check_predicate(req.predicate, node.records):
                        violations.add(req)
                except ValueError as exc:
                    errors.append(f'predicate_error@{name}:{exc}')
                    violations.add(req)
        # No implicit release: all active requirements apply at a boundary.
        publishable = name in boundaries and not (errors or pending or violations)
        state = dict(lineage=frozenset(lineage), done=frozenset(done),
                     pending=frozenset(pending), violations=frozenset(violations),
                     errors=tuple(sorted(set(errors))), publishable=publishable)
        visiting.remove(name)
        results[name] = state
        return state
    for name in sorted(by_id):
        visit(name)
    return {name: Decision(name, s['lineage'], s['pending'], s['violations'],
                           s['publishable'], s['errors']) for name, s in results.items()}
