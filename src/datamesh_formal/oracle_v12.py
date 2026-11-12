"""Independent full verifier for the bounded V0.12 language.

No imports of EvidenceMesh, expected_output, canonical_rows, or semantic.digest.
Trust boundary: source records and policy definitions are trusted. This is a
second implementation for differential testing, not a formal proof.
"""
from collections import Counter, defaultdict
from hashlib import sha256
import json


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode()).hexdigest()


def _rows(rows):
    return tuple(sorted(tuple(sorted(tuple(r))) for r in rows))


def _execute(node, parents):
    if node.operation == 'source':
        return _rows(node.records)
    bags = [list(_rows(p.records)) for p in parents]
    combined = [r for bag in bags for r in bag]
    if node.operation == 'project':
        columns = set(filter(None, (x.strip() for x in node.parameter.split(','))))
        return _rows(tuple((k,v) for k,v in r if k in columns) for r in combined)
    if node.operation == 'filter_eq':
        if '=' not in node.parameter:
            raise ValueError('bad filter')
        k, v = node.parameter.split('=', 1)
        if not k:
            raise ValueError('bad filter')
        return _rows(r for r in combined if dict(r).get(k) == v)
    if node.operation == 'join':
        if len(bags) != 2 or not node.parameter:
            raise ValueError('bad join')
        result = []
        for left in bags[0]:
            for right in bags[1]:
                a, b = dict(left), dict(right)
                if node.parameter in a and a[node.parameter] == b.get(node.parameter):
                    if not any(k in a and a[k] != v for k,v in b.items()):
                        result.append(tuple(sorted({**a, **b}.items())))
        return _rows(result)
    if node.operation == 'group_count':
        counts = Counter()
        for record in combined:
            value = dict(record).get(node.parameter)
            if value is None:
                raise ValueError('missing grouping key')
            counts[value] += 1
        return _rows(tuple(sorted(((node.parameter, value), ('count', str(count)))))
                     for value, count in counts.items())
    raise ValueError('unsupported operation')


def verify(nodes, rules):
    """Return per-node (valid, residual, discharged, output_digest), independently."""
    by_id = {n.id: n for n in nodes}
    policies = {(r.issuer, r.rule_id): r for r in rules}
    remaining = set(by_id)
    answer = {}
    while remaining:
        ready = sorted(n for n in remaining if all(p in answer for p in by_id[n].inputs))
        if not ready:
            raise ValueError('cycle or missing parent')
        for name in ready:
            node = by_id[name]
            parents = [by_id[p] for p in node.inputs]
            incoming = set(node.introduced)
            for p in node.inputs:
                incoming.update(answer[p][1])
            good = all(answer[p][0] for p in node.inputs)
            try:
                if _execute(node, parents) != _rows(node.records):
                    good = False
            except (ValueError, TypeError):
                good = False
            combined = _rows(r for p in parents for r in p.records)
            input_hash = _hash([list(r) for r in combined])
            output_hash = _hash([list(r) for r in _rows(node.records)])
            discharged, invalid = set(), set()
            for cert in node.certificates:
                obligation = cert.obligation
                rule = policies.get((obligation.issuer, cert.rule_id))
                match = (obligation in incoming and rule is not None and
                         rule.obligation_name == obligation.name and
                         cert.rule_fingerprint == _hash(['evidence-v1', rule.issuer,
                           rule.rule_id, rule.obligation_name, rule.kind, rule.parameter]) and
                         cert.input_digest == input_hash and cert.output_digest == output_hash and
                         _execute(node, parents) == _rows(node.records)) if rule and good else False
                if match:
                    if rule.kind == 'absent_field':
                        match = all(rule.parameter not in dict(r) for r in node.records)
                    elif rule.kind == 'min_group_count':
                        try:
                            t = int(rule.parameter)
                            match = (node.operation == 'group_count' and t >= 1 and
                                     bool(node.records) and all(int(dict(r)['count']) >= t for r in node.records))
                        except (KeyError, ValueError):
                            match = False
                    else:
                        match = False
                if match:
                    discharged.add(obligation)
                else:
                    invalid.add(obligation)
                    good = False
            discharged -= invalid
            answer[name] = (good, frozenset(incoming - discharged),
                            frozenset(discharged), output_hash)
            remaining.remove(name)
    return answer
