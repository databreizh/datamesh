"""V0.15 incremental implementation of the V0.14 lifecycle semantics.

Fixed DAG. Observable cutoff includes inherited one-time completions and errors,
not just publishability. Reference oracle: lifecycle.recompute.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, replace
from heapq import heappush, heappop
from .lifecycle import Mode, Requirement, Attestation, Decision, check_predicate
from .evidence import DataNode, canonical_rows, expected_output

@dataclass(frozen=True)
class State:
    lineage: frozenset[Requirement]
    done: frozenset[Requirement]
    pending: frozenset[Requirement]
    violations: frozenset[Requirement]
    errors: tuple[str, ...]
    publishable: bool

    def decision(self, name):
        return Decision(name, self.lineage, self.pending, self.violations,
                        self.publishable, self.errors)

class LifecycleIGV:
    def __init__(self, nodes, requirements, attestations=(), boundaries=frozenset()):
        self.nodes = {n.id:n for n in nodes}
        if len(self.nodes) != len(nodes):
            raise ValueError('duplicate node')
        self.children = defaultdict(set)
        indegree = {n:0 for n in self.nodes}
        for n in nodes:
            if len(n.inputs) != len(set(n.inputs)):
                raise ValueError('duplicate parent')
            for p in n.inputs:
                if p not in self.nodes: raise ValueError('unknown parent')
                self.children[p].add(n.id)
                indegree[n.id] += 1
        ready = sorted(n for n,d in indegree.items() if not d)
        order=[]
        while ready:
            name=ready.pop(0)
            order.append(name)
            for child in sorted(self.children[name]):
                indegree[child]-=1
                if indegree[child]==0:
                    ready.append(child);ready.sort()
        if len(order)!=len(nodes): raise ValueError('cycle')
        self.order=tuple(order)
        self.rank={n:i for i,n in enumerate(order)}
        self.requirements=frozenset()
        self.attestations=frozenset()
        self.boundaries=frozenset()
        self._configure(requirements, attestations, boundaries)
        self.cache={}
        self.initialize()

    def _configure(self, requirements, attestations, boundaries):
        reqs=tuple(requirements)
        if len(reqs)!=len(set(reqs)): raise ValueError('duplicate requirement')
        if not set(boundaries)<=self.nodes.keys(): raise ValueError('unknown boundary')
        for r in reqs:
            if r.origin not in self.nodes: raise ValueError('unknown origin')
            if (r.mode==Mode.ONCE) != r.predicate.startswith('action:'):
                raise ValueError('invalid requirement mode/predicate')
        for a in attestations:
            if a.node_id not in self.nodes: raise ValueError('unknown attestation node')
        self.requirements=frozenset(reqs)
        self.attestations=frozenset(attestations)
        self.boundaries=frozenset(boundaries)
        self.introduced=defaultdict(set)
        self.actions=defaultdict(set)
        for r in self.requirements:self.introduced[r.origin].add(r)
        for a in self.attestations:self.actions[a.node_id].add((a.requirement,a.action))

    def _compute(self, name):
        n=self.nodes[name]
        lineage=set(self.introduced[name]);done=set();errors=[]
        for p in n.inputs:
            parent=self.cache[p]
            lineage.update(parent.lineage)
            done.update(parent.done)
            errors.extend(parent.errors)
        if (not n.inputs)!=(n.operation=='source'):
            errors.append(f'invalid_source_shape@{name}')
        try:
            parent_rows=tuple(r for p in n.inputs for r in self.nodes[p].records)
            if n.operation=='join':
                if len(n.inputs)!=2 or not n.parameter:
                    raise ValueError('join requires two parents and a key')
                left,right=(self.nodes[p].records for p in n.inputs)
                expected=canonical_rows(
                    tuple(sorted(dict(a, **dict(b)).items()))
                    for a in left for b in right
                    if dict(a).get(n.parameter) is not None
                    and dict(a).get(n.parameter)==dict(b).get(n.parameter)
                    and all(k not in dict(a) or dict(a)[k]==v for k,v in b)
                )
            else:
                expected=expected_output(n,parent_rows)
            if expected!=canonical_rows(n.records): errors.append(f'output_mismatch@{name}')
        except (ValueError,TypeError) as exc:
            errors.append(f'operation_error@{name}:{exc}')
        for req in lineage:
            if req.mode==Mode.ONCE and (req,req.predicate[7:]) in self.actions[name]:
                done.add(req)
        pending=frozenset(r for r in lineage if r.mode==Mode.ONCE and r not in done)
        violations=set()
        for req in lineage:
            if req.mode==Mode.PERSISTENT or (req.mode==Mode.BOUNDARY and name in self.boundaries):
                try:
                    if not check_predicate(req.predicate,n.records):violations.add(req)
                except ValueError as exc:
                    errors.append(f'predicate_error@{name}:{exc}')
                    violations.add(req)
        errors=tuple(sorted(set(errors)))
        publishable=name in self.boundaries and not (errors or pending or violations)
        return State(frozenset(lineage),frozenset(done),pending,
                     frozenset(violations),errors,publishable)

    def initialize(self):
        self.cache={}
        for name in self.order:self.cache[name]=self._compute(name)
        return self.decisions()

    def decisions(self):
        return {n:s.decision(n) for n,s in self.cache.items()}

    def update(self, *, nodes=None, requirements=None, attestations=None, boundaries=None, strategy="igv"):
        """Update existing node definitions and/or policy state; return stats.

        Node edits cannot change graph edges. All seed sources are constructed
        from exact old/new differences, before updating the configuration.
        """
        if strategy not in ('igv', 'descendants', 'no_policy_index'):
            raise ValueError('unknown strategy')
        # Validate the entire batch before any mutation. Previously an invalid
        # policy or late invalid node could leave nodes/cache out of sync.
        edits=tuple(nodes) if nodes is not None else ()
        if len({n.id for n in edits}) != len(edits):
            raise ValueError('duplicate node edit')
        for n in edits:
            if n.id not in self.nodes or n.inputs!=self.nodes[n.id].inputs or n.operation!=self.nodes[n.id].operation or n.parameter!=self.nodes[n.id].parameter:
                raise ValueError('graph topology and operator are fixed')
        new_reqs=tuple(self.requirements) if requirements is None else tuple(requirements)
        new_acts=tuple(self.attestations) if attestations is None else tuple(attestations)
        new_bound=frozenset(self.boundaries if boundaries is None else boundaries)
        if len(set(new_reqs))!=len(new_reqs):raise ValueError('duplicate requirement')
        if not new_bound<=self.nodes.keys():raise ValueError('unknown boundary')
        for r in new_reqs:
            if r.origin not in self.nodes or ((r.mode==Mode.ONCE)!=r.predicate.startswith('action:')):
                raise ValueError('invalid requirement')
        for a in new_acts:
            if a.node_id not in self.nodes:raise ValueError('unknown attestation node')
        seeds=set()
        if edits:
            for n in edits:
                if n!=self.nodes[n.id]:
                    seeds.add(n.id)
                    # A parent's asserted records are a direct input to each child
                    # even if the parent's governance state remains unchanged.
                    if n.records != self.nodes[n.id].records:
                        seeds.update(self.children[n.id])
                    self.nodes[n.id]=n
        for r in self.requirements.symmetric_difference(new_reqs):seeds.add(r.origin)
        for a in self.attestations.symmetric_difference(new_acts):seeds.add(a.node_id)
        seeds.update(self.boundaries.symmetric_difference(new_bound))
        if strategy == 'no_policy_index' and (new_reqs != self.requirements or new_acts != self.attestations):
            # Conservative full scan: do not use origin/attestation reverse indexes.
            seeds.update(self.nodes)
        self._configure(new_reqs,new_acts,new_bound)
        queue=[];queued=set()
        for n in seeds:
            heappush(queue,(self.rank[n],n));queued.add(n)
        recomputed=0;changed=0
        while queue:
            _,name=heappop(queue);queued.remove(name)
            old=self.cache[name]
            new=self._compute(name)
            recomputed+=1
            if old!=new:
                changed+=1
                self.cache[name]=new
            if old!=new or strategy == 'descendants':
                for child in self.children[name]:
                    if child not in queued:
                        heappush(queue,(self.rank[child],child));queued.add(child)
        return {'seeds':len(seeds),'recomputed':recomputed,'changed':changed}
