"""V0.11 finite-data evidence checker. Illustrative properties, not legal/privacy compliance.

Source records are trusted inputs. Non-source output is recomputed independently;
certificates are scoped to the obligation's originating node and pinned to data
and rule content. No claim of cryptographic authentication or general soundness.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from collections import defaultdict
from heapq import heappush, heappop
from .semantic import digest

Row = tuple[tuple[str, str], ...]


def row(**values) -> Row:
    return tuple(sorted((str(k), str(v)) for k, v in values.items()))


def canonical_rows(rows):
    return tuple(sorted(tuple(sorted(r)) for r in rows))


@dataclass(frozen=True, order=True)
class ScopedObligation:
    issuer: str
    name: str
    origin: str


@dataclass(frozen=True)
class EvidenceRule:
    issuer: str
    rule_id: str
    obligation_name: str
    kind: str  # absent_field or min_group_count
    parameter: str

    @property
    def fingerprint(self):
        return digest(['evidence-v1', self.issuer, self.rule_id,
                       self.obligation_name, self.kind, self.parameter])


@dataclass(frozen=True)
class EvidenceCertificate:
    obligation: ScopedObligation
    rule_id: str
    rule_fingerprint: str
    input_digest: str
    output_digest: str


@dataclass(frozen=True)
class DataNode:
    id: str
    inputs: tuple[str, ...] = ()
    operation: str = 'source'  # source, project, filter_eq, join, group_count
    parameter: str = ''  # comma-separated projected fields, or group field
    records: tuple[Row, ...] = ()  # asserted output; source records are trusted
    introduced: frozenset[ScopedObligation] = frozenset()
    certificates: tuple[EvidenceCertificate, ...] = ()


@dataclass(frozen=True)
class EvidenceResult:
    residual: frozenset[ScopedObligation]
    valid: bool
    errors: tuple[str, ...]
    discharged: tuple[ScopedObligation, ...]
    output_digest: str


def expected_output(node: DataNode, parent_rows: tuple[Row, ...]):
    if node.operation == 'source':
        return canonical_rows(node.records)
    if node.operation == 'project':
        keep = frozenset(x.strip() for x in node.parameter.split(',') if x.strip())
        return canonical_rows(tuple((k, v) for k, v in r if k in keep) for r in parent_rows)
    if node.operation == 'filter_eq':
        if '=' not in node.parameter:
            raise ValueError('filter requires field=value')
        field, value = node.parameter.split('=', 1)
        if not field:
            raise ValueError('empty filter field')
        return canonical_rows(r for r in parent_rows if dict(r).get(field) == value)
    if node.operation == 'join':
        if len(node.inputs) != 2:
            raise ValueError('join requires exactly two parents')
        # The two parent bags are kept separate; shared columns must agree.
        # Namespace collisions with conflicting values do not produce a join row.
        raise ValueError('join requires partitioned parent inputs')
    if node.operation == 'group_count':
        counts = defaultdict(int)
        for r in parent_rows:
            fields = dict(r)
            if node.parameter not in fields:
                raise ValueError('group field missing')
            counts[fields[node.parameter]] += 1
        return canonical_rows(row(**{node.parameter: key, 'count': count})
                              for key, count in counts.items())
    raise ValueError('unknown operation')


class EvidenceMesh:
    def __init__(self, nodes: list[DataNode], rules: list[EvidenceRule]):
        self.nodes = {n.id: n for n in nodes}
        self.rules = {(r.issuer, r.rule_id): r for r in rules}
        if len(self.nodes) != len(nodes) or len(self.rules) != len(rules):
            raise ValueError('duplicate node or rule')
        self.children = defaultdict(set)
        indeg = {n: 0 for n in self.nodes}
        for n in nodes:
            if (not n.inputs) != (n.operation == 'source'):
                raise ValueError('source/input mismatch')
            if len(n.inputs) != len(set(n.inputs)):
                raise ValueError('duplicate inputs')
            for parent in n.inputs:
                if parent not in self.nodes:
                    raise ValueError('unknown parent')
                self.children[parent].add(n.id)
                indeg[n.id] += 1
            for o in n.introduced:
                if o.origin != n.id:
                    raise ValueError('introduced obligation must identify its source')
        ready = sorted(k for k, d in indeg.items() if d == 0)
        order = []
        while ready:
            name = ready.pop(0)
            order.append(name)
            for child in sorted(self.children[name]):
                indeg[child] -= 1
                if indeg[child] == 0:
                    ready.append(child)
                    ready.sort()
        if len(order) != len(nodes):
            raise ValueError('cycle')
        self.order = tuple(order)
        self.rank = {name: i for i, name in enumerate(order)}
        self.cache = {}
        self._index()

    def _index(self):
        self.rule_users = defaultdict(set)
        for n in self.nodes.values():
            for c in n.certificates:
                self.rule_users[(c.obligation.issuer, c.rule_id)].add(n.id)

    def _compute(self, name, results):
        n = self.nodes[name]
        incoming = set(n.introduced)
        for p in n.inputs:
            incoming.update(results[p].residual)
        parent_rows = tuple(r for p in n.inputs for r in self.nodes[p].records)
        errors = []
        try:
            if n.operation == 'join':
                if len(n.inputs) != 2 or not n.parameter:
                    raise ValueError('join requires two parents and a key')
                left, right = (self.nodes[p].records for p in n.inputs)
                expected = canonical_rows(
                    tuple(sorted(dict(a, **dict(b)).items()))
                    for a in left for b in right
                    if dict(a).get(n.parameter) is not None
                    and dict(a).get(n.parameter) == dict(b).get(n.parameter)
                    and all(k not in dict(a) or dict(a)[k] == v for k, v in b)
                )
            else:
                expected = expected_output(n, parent_rows)
            if expected != canonical_rows(n.records):
                errors.append(f'output_mismatch@{name}')
        except ValueError as e:
            errors.append(f'operation_error@{name}:{e}')
        input_hash = digest([list(r) for r in canonical_rows(parent_rows)])
        output_hash = digest([list(r) for r in canonical_rows(n.records)])
        discharged = set()
        invalid = set()
        for c in n.certificates:
            o = c.obligation
            if o not in incoming:
                errors.append(f'certificate_without_obligation@{name}:{o.origin}/{o.name}')
                continue
            rule = self.rules.get((o.issuer, c.rule_id))
            ok = (rule is not None and rule.obligation_name == o.name and
                  c.rule_fingerprint == rule.fingerprint and
                  c.input_digest == input_hash and c.output_digest == output_hash and
                  not any(e.startswith(('output_mismatch', 'operation_error')) for e in errors))
            if ok:
                if rule.kind == 'absent_field':
                    ok = all(rule.parameter not in dict(r) for r in n.records)
                elif rule.kind == 'min_group_count':
                    try:
                        threshold = int(rule.parameter)
                        ok = (n.operation == 'group_count' and threshold >= 1 and
                              bool(n.records) and all(int(dict(r)['count']) >= threshold
                                                      for r in n.records))
                    except (ValueError, KeyError):
                        ok = False
                else:
                    ok = False
            if ok:
                discharged.add(o)
            else:
                invalid.add(o)
                errors.append(f'invalid_certificate@{name}:{o.origin}/{o.name}')
        discharged -= invalid
        return EvidenceResult(frozenset(incoming - discharged),
                              not errors and all(results[p].valid for p in n.inputs),
                              tuple(sorted(errors)), tuple(sorted(discharged)), output_hash)

    def full(self):
        results = {}
        for n in self.order:
            results[n] = self._compute(n, results)
        return results

    def initialize(self):
        self.cache = self.full()

    def update(self, node_changes=None, rule_changes=None, strategy='igv'):
        if len(self.cache) != len(self.nodes):
            raise ValueError('initialize first')
        changes = node_changes or {}
        updates = rule_changes or {}
        for name, n in changes.items():
            if name not in self.nodes or n.id != name or n.inputs != self.nodes[name].inputs:
                raise ValueError('topology change requires rebuild')
            if (not n.inputs) != (n.operation == 'source'):
                raise ValueError('source/input mismatch')
            if any(o.origin != name for o in n.introduced):
                raise ValueError('invalid obligation origin')
        for key, r in updates.items():
            if key not in self.rules or (r.issuer, r.rule_id) != key:
                raise ValueError('rule identity change requires rebuild')
        seeds = set(changes)
        for key, r in updates.items():
            if self.rules[key] != r:
                seeds.update(self.rule_users[key])
        self.nodes.update(changes)
        self.rules.update(updates)
        self._index()
        if strategy == 'full':
            self.cache = self.full()
            return len(self.nodes)
        if strategy not in ('igv', 'desc'):
            raise ValueError('unknown strategy')
        if strategy == 'desc':
            affected = set(seeds)
            stack = list(seeds)
            while stack:
                for child in self.children[stack.pop()]:
                    if child not in affected:
                        affected.add(child)
                        stack.append(child)
            for n in self.order:
                if n in affected:
                    self.cache[n] = self._compute(n, self.cache)
            return len(affected)
        heap = [(self.rank[n], n) for n in seeds]
        import heapq
        heapq.heapify(heap)
        scheduled = set(seeds)
        recomputed = 0
        while heap:
            _, name = heappop(heap)
            scheduled.remove(name)
            old = self.cache[name]
            new = self._compute(name, self.cache)
            recomputed += 1
            self.cache[name] = new
            if old != new:
                for child in self.children[name]:
                    if child not in scheduled:
                        heappush(heap, (self.rank[child], child))
                        scheduled.add(child)
        return recomputed


def make_evidence_certificate(node: DataNode, obligation: ScopedObligation,
                              rule: EvidenceRule, parent_rows=()):
    """Creates a candidate certificate; does not establish its validity."""
    if rule.issuer != obligation.issuer or rule.obligation_name != obligation.name:
        raise ValueError('rule/obligation mismatch')
    return EvidenceCertificate(obligation, rule.rule_id, rule.fingerprint,
                               digest([list(r) for r in canonical_rows(parent_rows)]),
                               digest([list(r) for r in canonical_rows(node.records)]))
