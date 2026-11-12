"""V0.9: exact policy-rule dependency indexing over V0.7 symbolic certificates.

Important: a policy *version bump* invalidates every pinned certificate of that
issuer, even if its rule is unchanged. No unsafe optimization is claimed there.
"""
from collections import defaultdict
from .certified import Mesh


def rule_key(rule):
    return (rule.obligation, rule.kind, rule.required_fact)


def certificate_key(cert):
    return (cert.obligation, cert.rule_kind, cert.witness_fact)


class IndexedMesh(Mesh):
    def _index(self):
        super()._index()
        self.rule_users = defaultdict(set)
        for node in self.nodes.values():
            for cert in node.certificates:
                self.rule_users[(cert.issuer, certificate_key(cert))].add(node.id)

    def _apply(self, changes, policies):
        # Match base-class topology and issuer checks BEFORE mutating state.
        for name, node in changes.items():
            if name not in self.nodes or node.id != name or node.inputs != self.nodes[name].inputs:
                raise ValueError('topology change requires rebuild')
            if (not node.inputs) != (node.kind == 'source'):
                raise ValueError('source/input mismatch')
        for issuer, pol in policies.items():
            if issuer not in self.policies or pol.issuer != issuer:
                raise ValueError('new policy requires rebuild')

        seeds = set(changes)
        for issuer, new in policies.items():
            old = self.policies[issuer]
            if old.version != new.version:
                # Version-pinned certificates: all issuer users may change status.
                seeds.update(self.policy_users[issuer])
            else:
                changed = {rule_key(r) for r in old.rules} ^ {rule_key(r) for r in new.rules}
                for key in changed:
                    seeds.update(self.rule_users[(issuer, key)])

        self.nodes.update(changes)
        self.policies.update(policies)
        self._index()
        # Node edits are always seeded, including changes to certificate bindings.
        return seeds
