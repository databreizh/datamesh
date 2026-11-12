"""V0.12: multi-topology FULL/IGV + independent-oracle validation.
Run with python examples/benchmark_v12.py --nodes 200 --seeds 5 --changes 20.
"""
import argparse, csv, json, random, statistics, time
from dataclasses import replace
from datamesh_formal.evidence import DataNode, EvidenceMesh, EvidenceRule, ScopedObligation, row
from datamesh_formal.oracle_v12 import verify


def build(n, topology, seed):
    rng = random.Random(seed)
    o = ScopedObligation('issuer', 'remove_email', 's0')
    rules = [EvidenceRule('issuer', 'drop', 'remove_email', 'absent_field', 'email')]
    nodes = [DataNode('s0', records=(row(id=1, email='secret'),), introduced=frozenset({o}))]
    for i in range(1, n):
        if topology == 'chain':
            parents = (f'n{i-1}' if i > 1 else 's0',)
        elif topology == 'tree':
            parents = ('s0' if (i-1)//2 == 0 else f'n{(i-1)//2}',)
        elif topology == 'layered':
            parents = ('s0' if i < 4 else f'n{i-3}',)
        else:
            a = rng.randrange(i)
            parents = ('s0' if a == 0 else f'n{a}',)
            if i > 2 and rng.random() < .35:
                b = rng.randrange(i)
                p2 = 's0' if b == 0 else f'n{b}'
                if p2 not in parents:
                    parents += (p2,)
        # Projecting only 'id' makes all downstream rows well-defined and finite.
        records = tuple(row(id=1) for _ in parents)
        nodes.append(DataNode(f'n{i}', parents, 'project', 'id', records))
    return nodes, rules


def check_oracle(mesh):
    oracle = verify(list(mesh.nodes.values()), list(mesh.rules.values()))
    for name, (valid, residual, discharged, out_hash) in oracle.items():
        result = mesh.cache[name]
        assert (result.valid, result.residual, frozenset(result.discharged), result.output_digest) == (valid, residual, discharged, out_hash), name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--nodes', type=int, default=100)
    parser.add_argument('--changes', type=int, default=20)
    parser.add_argument('--seeds', type=int, default=3)
    parser.add_argument('--topologies', nargs='+', default=['chain','tree','dag','layered'])
    parser.add_argument('--out', default='benchmark_v12.csv')
    parser.add_argument('--summary', default='benchmark_v12_summary.json')
    args = parser.parse_args()
    if args.nodes < 5 or args.changes < 1 or args.seeds < 1:
        parser.error('nodes >= 5, changes >= 1, seeds >= 1 required')
    measurements = []
    for topology in args.topologies:
        for seed in range(args.seeds):
            nodes, rules = build(args.nodes, topology, seed)
            full = EvidenceMesh(nodes, rules); full.initialize()
            igv = EvidenceMesh(nodes, rules); igv.initialize()
            for edit in range(args.changes):
                kind = ['source','rule','restore','noop'][edit % 4]
                node_updates, rule_updates = {}, {}
                if kind == 'source':
                    source = full.nodes['s0']
                    value = 1 if (edit // 4) % 2 else 2
                    node_updates['s0'] = replace(source, records=(row(id=value, email='secret'),))
                elif kind in ('rule', 'restore'):
                    rule = full.rules[('issuer','drop')]
                    rule_updates[('issuer','drop')] = replace(rule, parameter='id' if kind == 'rule' else 'email')
                order = ['full','igv'] if (seed+edit) % 2 else ['igv','full']
                for strategy in order:
                    mesh = full if strategy == 'full' else igv
                    start = time.perf_counter_ns()
                    count = mesh.update(node_updates, rule_updates, strategy=strategy)
                    elapsed = (time.perf_counter_ns()-start)/1e6
                    measurements.append(dict(topology=topology,seed=seed,edit=edit,kind=kind,
                                             strategy=strategy,ms=round(elapsed,6),recomputed=count))
                assert full.cache == igv.cache
                check_oracle(full)
                check_oracle(igv)
    with open(args.out,'w',newline='',encoding='utf-8') as f:
        w = csv.DictWriter(f,fieldnames=list(measurements[0]));w.writeheader();w.writerows(measurements)
    groups = {}
    for topology in args.topologies:
        groups[topology] = {}
        for strategy in ('full','igv'):
            rows = [r for r in measurements if r['topology']==topology and r['strategy']==strategy]
            groups[topology][strategy] = {'mean_ms':statistics.mean(r['ms'] for r in rows),
                'median_ms':statistics.median(r['ms'] for r in rows),
                'mean_recomputed':statistics.mean(r['recomputed'] for r in rows)}
    summary = {'settings':vars(args),'all_equal':True,'oracle_equal':True,
               'edits':len(measurements)//2,'groups':groups,
               'cautions':['synthetic finite rows','trusted source records and policies',
                           'oracle is independently implemented but not a formal proof',
                           'fixed graph per run','initialization excluded from timings',
                           'oracle validation excluded from timings']}
    with open(args.summary,'w',encoding='utf-8') as f: json.dump(summary,f,indent=2)
    print(json.dumps(summary,indent=2))

if __name__ == '__main__': main()
