"""V0.7: finite, proof-rule checked discharge and differential incremental verification.

This is a *symbolic policy calculus*, not a privacy or legal compliance oracle.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from collections import defaultdict
from heapq import heappush, heappop

@dataclass(frozen=True, order=True)
class Obligation:
    issuer: str
    name: str

@dataclass(frozen=True)
class Rule:
    obligation: Obligation
    kind: str
    required_fact: str

@dataclass(frozen=True)
class Policy:
    issuer: str
    version: int
    rules: tuple[Rule, ...] = ()

@dataclass(frozen=True)
class Certificate:
    obligation: Obligation
    issuer: str
    version: int
    rule_kind: str
    witness_fact: str

@dataclass(frozen=True)
class Node:
    id: str
    inputs: tuple[str, ...] = ()
    kind: str = 'source'
    introduced: frozenset[Obligation] = frozenset()
    facts: frozenset[str] = frozenset()
    certificates: tuple[Certificate, ...] = ()

@dataclass(frozen=True)
class Result:
    residual: frozenset[Obligation]
    valid: bool
    errors: tuple[str, ...]
    discharged: tuple[Obligation, ...]

@dataclass(frozen=True)
class Stats:
    recomputed: int
    visited: int
    stopped: int

class Mesh:
    def __init__(self, nodes: list[Node], policies: list[Policy]):
        self.nodes = {n.id:n for n in nodes}
        self.policies = {p.issuer:p for p in policies}
        if len(self.nodes)!=len(nodes) or len(self.policies)!=len(policies):
            raise ValueError('duplicate node/policy')
        self.children = defaultdict(set)
        indeg = {n:0 for n in self.nodes}
        for n in nodes:
            if len(n.inputs)!=len(set(n.inputs)): raise ValueError('duplicate inputs')
            if (not n.inputs)!=(n.kind=='source'): raise ValueError('source/input mismatch')
            for p in n.inputs:
                if p not in self.nodes: raise ValueError('unknown input')
                self.children[p].add(n.id); indeg[n.id]+=1
        ready = sorted(n for n in indeg if indeg[n]==0)
        order=[]
        while ready:
            n=ready.pop(0); order.append(n)
            for c in sorted(self.children[n]):
                indeg[c]-=1
                if indeg[c]==0: ready.append(c); ready.sort()
        if len(order)!=len(nodes): raise ValueError('cycle')
        self.order=tuple(order); self.rank={n:i for i,n in enumerate(order)}
        self.cache={}
        self._index()

    def _index(self):
        self.policy_users=defaultdict(set)
        for n in self.nodes.values():
            for c in n.certificates: self.policy_users[c.issuer].add(n.id)

    def _check(self, n: Node, c: Certificate) -> bool:
        # Rules are trusted policy declarations. Facts are trusted *only as
        # symbolic assertions*, not as independently verified real-world facts.
        pol=self.policies.get(c.issuer)
        if pol is None or c.issuer!=c.obligation.issuer or pol.version!=c.version:
            return False
        return (c.witness_fact in n.facts and
                any(r.obligation==c.obligation and r.kind==n.kind and
                    r.kind==c.rule_kind and r.required_fact==c.witness_fact
                    for r in pol.rules))

    def _compute(self, name, results):
        n=self.nodes[name]
        inherited=frozenset().union(*(results[p].residual for p in n.inputs))
        incoming=inherited | n.introduced
        errors=[]; discharged=set()
        for c in n.certificates:
            if c.obligation not in incoming: continue
            if self._check(n,c): discharged.add(c.obligation)
            else: errors.append(f'invalid:{c.obligation.issuer}/{c.obligation.name}@{n.id}')
        # If any attempted certificate for an obligation is invalid, retain it;
        # do not let a second valid certificate mask a failed proof obligation.
        invalid_obligations={c.obligation for c in n.certificates
                             if c.obligation in incoming and not self._check(n,c)}
        discharged-=invalid_obligations
        valid=not errors and all(results[p].valid for p in n.inputs)
        return Result(frozenset(incoming-discharged), valid,
                      tuple(sorted(errors)), tuple(sorted(discharged)))

    def full(self):
        results={}
        for n in self.order: results[n]=self._compute(n,results)
        return results

    def initialize(self):
        self.cache=self.full()

    def _apply(self, changes, policies):
        for name,n in changes.items():
            if name not in self.nodes or n.id!=name or n.inputs!=self.nodes[name].inputs:
                raise ValueError('topology change requires rebuild')
            if (not n.inputs)!=(n.kind=='source'): raise ValueError('source/input mismatch')
        for issuer,p in policies.items():
            if issuer not in self.policies or p.issuer!=issuer:
                raise ValueError('new policy requires rebuild')
        seeds=set(changes)
        for issuer in policies: seeds.update(self.policy_users[issuer])
        self.nodes.update(changes); self.policies.update(policies)
        self._index()
        for issuer in policies: seeds.update(self.policy_users[issuer])
        return seeds

    def update(self, node_changes=None, policy_changes=None, strategy='igv'):
        if len(self.cache)!=len(self.nodes): raise ValueError('initialize first')
        changes=node_changes or {}; policies=policy_changes or {}
        seeds=self._apply(changes,policies)
        if strategy not in ('igv','desc','full'): raise ValueError('unknown strategy')
        if strategy=='full':
            self.cache=self.full(); return Stats(len(self.nodes),len(self.nodes),0)
        if strategy=='desc':
            affected=set(seeds); stack=list(seeds)
            while stack:
                for child in self.children[stack.pop()]:
                    if child not in affected: affected.add(child); stack.append(child)
            for name in self.order:
                if name in affected: self.cache[name]=self._compute(name,self.cache)
            return Stats(len(affected),len(affected),0)
        heap=[(self.rank[n],n) for n in seeds]
        import heapq
        heapq.heapify(heap); scheduled=set(seeds)
        recomputed=visited=stopped=0
        while heap:
            _,name=heappop(heap); scheduled.remove(name); visited+=1
            old=self.cache[name]; new=self._compute(name,self.cache)
            recomputed+=1; self.cache[name]=new
            if old==new:
                stopped+=1; continue
            for child in self.children[name]:
                if child not in scheduled:
                    heappush(heap,(self.rank[child],child)); scheduled.add(child)
        return Stats(recomputed,visited,stopped)
