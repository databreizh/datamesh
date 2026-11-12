"""Finite DAG incremental governance verifier (V0.6 research prototype).

Soundness is conditional on the supplied policy/declassification semantics.
No external policy engine or cryptographic proof checking is claimed.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from collections import defaultdict
from heapq import heappush, heappop
from typing import FrozenSet


@dataclass(frozen=True)
class Certificate:
    obligation: str
    policy_id: str
    policy_version: int
    # Explicit applicability predicate: allowed transformation kinds.
    allowed_kinds: FrozenSet[str]


@dataclass(frozen=True)
class Policy:
    id: str
    version: int
    dischargeable: FrozenSet[str]


@dataclass(frozen=True)
class Node:
    id: str
    domain: str
    inputs: tuple[str, ...] = ()
    kind: str = "source"
    source_obligations: FrozenSet[str] = frozenset()
    introduced_obligations: FrozenSet[str] = frozenset()
    certificates: tuple[Certificate, ...] = ()
    policy_id: str | None = None


@dataclass(frozen=True)
class Result:
    obligations: FrozenSet[str]
    valid: bool
    # Stable semantic proof status: invalid certificate identifiers are recorded.
    invalid_certificates: tuple[str, ...]
    # Signature of relevant verification dependencies, not all policy versions.
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class Stats:
    recomputed: int
    visited: int
    stopped: int


class GovernanceMesh:
    def __init__(self, nodes: list[Node], policies: list[Policy]):
        self.nodes = {n.id: n for n in nodes}
        self.policies = {p.id: p for p in policies}
        if len(self.nodes) != len(nodes) or len(self.policies) != len(policies):
            raise ValueError("duplicate node or policy")
        self.children = defaultdict(set)
        indegree = {name: 0 for name in self.nodes}
        for n in nodes:
            if len(set(n.inputs)) != len(n.inputs):
                raise ValueError("duplicate input")
            if not n.inputs and n.kind != "source":
                raise ValueError("non-source node without inputs")
            if n.inputs and n.kind == "source":
                raise ValueError("source with inputs")
            if n.policy_id is not None and n.policy_id not in self.policies:
                raise ValueError("unknown policy")
            for cert in n.certificates:
                if cert.policy_id not in self.policies:
                    raise ValueError("unknown certificate policy")
            for parent in n.inputs:
                if parent not in self.nodes:
                    raise ValueError("unknown input")
                self.children[parent].add(n.id)
                indegree[n.id] += 1
        ready = sorted(k for k,v in indegree.items() if v == 0)
        order = []
        while ready:
            name = ready.pop(0)
            order.append(name)
            for child in sorted(self.children[name]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
                    ready.sort()
        if len(order) != len(nodes):
            raise ValueError("cyclic mesh")
        self.order = tuple(order)
        self.rank = {n:i for i,n in enumerate(order)}
        self.cache: dict[str, Result] = {}
        self.reverse_policy = defaultdict(set)
        for n in nodes:
            for cert in n.certificates:
                self.reverse_policy[cert.policy_id].add(n.id)
            if n.policy_id is not None:
                self.reverse_policy[n.policy_id].add(n.id)

    def _compute(self, name: str, results: dict[str, Result]) -> Result:
        n = self.nodes[name]
        inherited = frozenset().union(*(results[p].obligations for p in n.inputs))
        obligations = inherited | n.source_obligations | n.introduced_obligations
        invalid = []
        evidence = []
        # A certificate is valid only if its policy and version match and
        # the policy explicitly allows discharging the named obligation.
        for cert in n.certificates:
            pol = self.policies[cert.policy_id]
            active = cert.obligation in obligations
            allowed = (pol.version == cert.policy_version and
                       cert.obligation in pol.dischargeable and
                       n.kind in cert.allowed_kinds)
            if active:
                if allowed:
                    obligations = obligations - {cert.obligation}
                    evidence.append(f"discharged:{cert.obligation}:{cert.policy_id}:{pol.version}")
                else:
                    invalid.append(f"{cert.obligation}@{cert.policy_id}")
            # Inactive certificates are harmless; activation is captured
            # by an upstream obligation change.
        # Parent invalidity must propagate, even if obligations remain equal.
        valid = not invalid and all(results[p].valid for p in n.inputs)
        return Result(frozenset(obligations), valid, tuple(sorted(invalid)),
                      tuple(sorted(evidence)))

    def full_verify(self) -> dict[str, Result]:
        results = {}
        for name in self.order:
            results[name] = self._compute(name, results)
        return results

    def initialize(self):
        self.cache = self.full_verify()
        return dict(self.cache)

    def update(self, *, node_changes: dict[str, Node] | None = None,
               policy_changes: dict[str, Policy] | None = None) -> Stats:
        if not self.cache:
            raise ValueError("call initialize first")
        node_changes = node_changes or {}
        policy_changes = policy_changes or {}
        # Fixed topology; any structural change requires a rebuild/full verification.
        for name, new in node_changes.items():
            if name not in self.nodes or new.id != name or new.inputs != self.nodes[name].inputs:
                raise ValueError("topology changes require rebuild")
            if new.policy_id is not None and new.policy_id not in self.policies:
                raise ValueError("unknown policy")
            if any(c.policy_id not in self.policies for c in new.certificates):
                raise ValueError("unknown certificate policy")
        for name, pol in policy_changes.items():
            if name not in self.policies or pol.id != name:
                raise ValueError("new policy requires rebuild")
        directly = set(node_changes)
        for name in policy_changes:
            directly |= self.reverse_policy[name]
        self.nodes.update(node_changes)
        self.policies.update(policy_changes)
        # Rebuild reverse dependency index after certificate edits.
        self.reverse_policy = defaultdict(set)
        for n in self.nodes.values():
            for cert in n.certificates:
                self.reverse_policy[cert.policy_id].add(n.id)
            if n.policy_id is not None:
                self.reverse_policy[n.policy_id].add(n.id)
        heap = []
        scheduled = set()
        for name in directly:
            heappush(heap, (self.rank[name], name))
            scheduled.add(name)
        recomputed = stopped = visited = 0
        while heap:
            _, name = heappop(heap)
            scheduled.remove(name)
            visited += 1
            old = self.cache[name]
            new = self._compute(name, self.cache)
            recomputed += 1
            self.cache[name] = new
            if old == new:
                stopped += 1
                continue
            for child in self.children[name]:
                if child not in scheduled:
                    heappush(heap, (self.rank[child], child))
                    scheduled.add(child)
        return Stats(recomputed, visited, stopped)
