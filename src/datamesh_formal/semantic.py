"""V0.10: content-addressed symbolic governance rules and incremental validation.

Fingerprints are SHA-256 over canonical JSON, not proofs of real-world compliance.
Rule identity is issuer + stable rule_id; semantic fingerprint includes transitive
rule dependencies. Revocation is an explicit policy decision.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import defaultdict
import hashlib
import json
from .certified import Mesh, Node, Obligation, Result


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class SemanticRule:
    rule_id: str
    obligation: Obligation
    kind: str
    required_fact: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticPolicy:
    issuer: str
    revision: int
    rules: tuple[SemanticRule, ...] = ()
    revoked: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SemanticCertificate:
    obligation: Obligation
    issuer: str
    rule_id: str
    fingerprint: str
    witness_fact: str


def fingerprints(policy: SemanticPolicy) -> dict[str, str]:
    rules = {}
    for r in policy.rules:
        if not r.rule_id or r.rule_id in rules:
            raise ValueError('empty or duplicate rule ID')
        if r.obligation.issuer != policy.issuer:
            raise ValueError('rule obligation issuer mismatch')
        if len(r.depends_on) != len(set(r.depends_on)):
            raise ValueError('duplicate rule dependencies')
        rules[r.rule_id] = r
    if not policy.revoked <= rules.keys():
        raise ValueError('revoked rule not declared')
    memo, visiting = {}, set()
    def visit(rule_id):
        if rule_id not in rules:
            raise ValueError('unknown rule dependency')
        if rule_id in visiting:
            raise ValueError('cyclic rule dependencies')
        if rule_id in memo:
            return memo[rule_id]
        visiting.add(rule_id)
        r = rules[rule_id]
        deps = [(d, visit(d)) for d in sorted(r.depends_on)]
        value = digest([policy.issuer, r.rule_id, r.obligation.issuer,
                        r.obligation.name, r.kind, r.required_fact, deps])
        visiting.remove(rule_id)
        memo[rule_id] = value
        return value
    for rid in sorted(rules):
        visit(rid)
    return memo


class SemanticMesh(Mesh):
    """Uses the existing DAG/incremental engine with semantic policy checking.

    Cache equality with full() is checked by the benchmark and tests.
    Policy changes invalidate only certificates whose validity can change.
    """
    def __init__(self, nodes: list[Node], policies: list[SemanticPolicy]):
        for p in policies:
            fingerprints(p)
        super().__init__(nodes, policies)

    def _index(self):
        super()._index()
        self.rule_users = defaultdict(set)
        for node in self.nodes.values():
            for c in node.certificates:
                self.rule_users[(c.issuer, c.rule_id)].add(node.id)

    def _check(self, n: Node, c: SemanticCertificate) -> bool:
        p = self.policies.get(c.issuer)
        if p is None or c.issuer != c.obligation.issuer:
            return False
        rules = {r.rule_id:r for r in p.rules}
        r = rules.get(c.rule_id)
        if r is None or c.rule_id in p.revoked:
            return False
        # Dependencies of a revoked rule cannot support a live certificate.
        if not self._active(c.rule_id, rules, p.revoked, set()):
            return False
        return (r.obligation == c.obligation and r.kind == n.kind and
                r.required_fact == c.witness_fact and c.witness_fact in n.facts and
                self._fingerprints[c.issuer][c.rule_id] == c.fingerprint)

    @staticmethod
    def _active(rid, rules, revoked, seen):
        if rid in revoked or rid not in rules:
            return False
        if rid in seen:
            return False
        return all(SemanticMesh._active(dep, rules, revoked, seen | {rid})
                   for dep in rules[rid].depends_on)

    def initialize(self):
        self._fingerprints = {issuer:fingerprints(p) for issuer,p in self.policies.items()}
        super().initialize()

    def full(self):
        self._fingerprints = {issuer:fingerprints(p) for issuer,p in self.policies.items()}
        return super().full()

    def _apply(self, changes, policies):
        for name, node in changes.items():
            if name not in self.nodes or node.id != name or node.inputs != self.nodes[name].inputs:
                raise ValueError('topology change requires rebuild')
            if (not node.inputs) != (node.kind == 'source'):
                raise ValueError('source/input mismatch')
        for issuer, p in policies.items():
            if issuer not in self.policies or p.issuer != issuer:
                raise ValueError('new policy requires rebuild')
            fingerprints(p)
        seeds = set(changes)
        for issuer, new in policies.items():
            old = self.policies[issuer]
            old_f = fingerprints(old); new_f = fingerprints(new)
            old_rules = {r.rule_id:r for r in old.rules}
            new_rules = {r.rule_id:r for r in new.rules}
            ids = old_f.keys() | new_f.keys()
            def active(rid, rules, revoked):
                return self._active(rid, rules, revoked, set())
            affected = {rid for rid in ids if old_f.get(rid) != new_f.get(rid) or
                        active(rid, old_rules, old.revoked) != active(rid, new_rules, new.revoked)}
            for rid in affected:
                seeds.update(self.rule_users[(issuer,rid)])
        self.nodes.update(changes)
        self.policies.update(policies)
        self._fingerprints = {issuer:fingerprints(p) for issuer,p in self.policies.items()}
        self._index()
        return seeds


def make_certificate(policy: SemanticPolicy, rule_id: str) -> SemanticCertificate:
    fp = fingerprints(policy)
    r = next((r for r in policy.rules if r.rule_id == rule_id), None)
    if r is None:
        raise ValueError('unknown rule')
    if not SemanticMesh._active(rule_id, {r.rule_id:r for r in policy.rules}, policy.revoked, set()):
        raise ValueError('cannot issue certificate for revoked rule/dependency')
    return SemanticCertificate(r.obligation, policy.issuer, rule_id,
                               fp[rule_id], r.required_fact)
