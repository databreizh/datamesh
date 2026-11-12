"""Differential, evidence-checked microbenchmark. Fixed topology and source-trust boundary."""
import argparse, csv, json, random, statistics, time
from dataclasses import replace
from datamesh_formal.evidence import (DataNode, EvidenceMesh, EvidenceRule,
    ScopedObligation, row, make_evidence_certificate)


def build(n):
    obligation = ScopedObligation('issuer', 'remove_email', 'source')
    rule = EvidenceRule('issuer', 'drop', 'remove_email', 'absent_field', 'email')
    source = DataNode('source', records=(row(id=1, email='private'),),
                      introduced=frozenset({obligation}))
    nodes = [source]
    prev = source
    for i in range(n - 1):
        node = DataNode(f'p{i}', (prev.id,), 'project', 'id', (row(id=1),))
        if i == 0:
            cert = make_evidence_certificate(node, obligation, rule, source.records)
            node = replace(node, certificates=(cert,))
        nodes.append(node)
        prev = node
    return nodes, rule


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--nodes', type=int, default=300)
    p.add_argument('--changes', type=int, default=40)
    p.add_argument('--seeds', type=int, default=5)
    p.add_argument('--out', default='benchmark_v11.csv')
    p.add_argument('--summary', default='benchmark_v11_summary.json')
    args = p.parse_args()
    if args.nodes < 2: p.error('nodes must be >= 2')
    records = []
    for seed in range(args.seeds):
        rng = random.Random(seed)
        nodes, rule = build(args.nodes)
        full = EvidenceMesh(nodes, [rule]); full.initialize()
        igv = EvidenceMesh(nodes, [rule]); igv.initialize()
        for i in range(args.changes):
            typ = ['revision_like_noop', 'source_edit', 'rule_edit', 'restore'][i % 4]
            changes, policies = {}, {}
            if typ == 'source_edit':
                source = full.nodes['source']
                changes = {'source':replace(source, records=(row(id=rng.randrange(2,100), email='private'),))}
            elif typ == 'rule_edit':
                policies = {('issuer','drop'):replace(full.rules[('issuer','drop')], parameter='id')}
            elif typ == 'restore':
                policies = {('issuer','drop'):rule}
            results = {}
            # Alternate timing order to mitigate systematic warm-cache bias.
            order = ('full','igv') if (i + seed) % 2 else ('igv','full')
            for strategy in order:
                m = full if strategy == 'full' else igv
                t = time.perf_counter_ns()
                recomputed = m.update(changes, policies, strategy=strategy)
                results[strategy] = ((time.perf_counter_ns()-t)/1e6, recomputed)
            assert full.cache == igv.cache == full.full(), (seed, i)
            for strategy, (ms, recomputed) in results.items():
                records.append(dict(seed=seed, edit=i, type=typ, strategy=strategy,
                                    ms=round(ms,6), recomputed=recomputed, equal=True))
    with open(args.out, 'w', newline='', encoding='utf8') as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0])); writer.writeheader(); writer.writerows(records)
    summary = {'settings':vars(args), 'all_equal':True, 'edits':args.changes*args.seeds,
               'cautions':['Synthetic finite records', 'Source records trusted',
                           'Fixed chain topology', 'Index construction excluded'], 'groups':{}}
    for typ in sorted(set(r['type'] for r in records)):
        summary['groups'][typ] = {}
        for strategy in ('full','igv'):
            subset = [r for r in records if r['type']==typ and r['strategy']==strategy]
            summary['groups'][typ][strategy] = {
                'mean_ms':statistics.mean(r['ms'] for r in subset),
                'median_ms':statistics.median(r['ms'] for r in subset),
                'mean_recomputed':statistics.mean(r['recomputed'] for r in subset)}
    with open(args.summary,'w',encoding='utf8') as f: json.dump(summary,f,indent=2)
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()
