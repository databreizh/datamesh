"""V0.8 differential benchmark with paired workload and seed-cluster bootstrap.

No external dependencies. Runs FULL/DESC/IGV on independent mesh instances,
asserting equality after EVERY edit. Times include update and invalidation.
"""
import argparse
import csv
import json
import random
import statistics
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from datamesh_formal.certified import Mesh, Node, Policy, Obligation, Rule, Certificate

STRATEGIES = ('full', 'desc', 'igv')
KINDS = ('node', 'fact', 'certificate', 'policy', 'noop')


def build(n, seed, topology, discharge_rate):
    rng = random.Random(seed)
    o = Obligation('sales', 'no-raw-export')
    pol = Policy('sales', 1, (Rule(o, 'aggregate', 'aggregated'),))
    nodes = [Node('n0', introduced=frozenset({o}))]
    for i in range(1, n):
        if topology == 'chain':
            parents = (f'n{i-1}',)
        elif topology == 'tree':
            parents = (f'n{(i-1)//2}',)
        elif topology == 'layered':
            parents = tuple(f'n{j}' for j in sorted(rng.sample(range(max(0, i-30), i), min(i, 2))))
        else:
            parents = tuple(f'n{j}' for j in sorted(rng.sample(range(i), min(i, 2))))
        kind = 'aggregate' if rng.random() < discharge_rate else 'copy'
        cert = (Certificate(o, 'sales', 1, 'aggregate', 'aggregated'),) if kind == 'aggregate' else ()
        nodes.append(Node(f'n{i}', parents, kind, facts=frozenset({'aggregated'}) if cert else frozenset(), certificates=cert))
    return nodes, [pol], o


def edit(mesh, rng, step, workload, obligation):
    if workload == 'mixed':
        kind = KINDS[step % len(KINDS)]
    else:
        kind = workload
    if kind == 'policy':
        pol = mesh.policies['sales']
        # Alternate a policy revision that removes/returns the discharge rule.
        rules = () if pol.rules else (Rule(obligation, 'aggregate', 'aggregated'),)
        return {}, {'sales': replace(pol, version=pol.version+1, rules=rules)}, kind
    candidates = list(mesh.nodes)
    name = candidates[rng.randrange(len(candidates))]
    node = mesh.nodes[name]
    if kind == 'node':
        new = frozenset() if obligation in node.introduced else node.introduced | {obligation}
        return {name: replace(node, introduced=new)}, {}, kind
    if kind == 'fact':
        facts = node.facts ^ frozenset({'aggregated'})
        return {name: replace(node, facts=facts)}, {}, kind
    if kind == 'certificate':
        # Deliberately insert or remove a version-bound proof attempt.
        cert = Certificate(obligation, 'sales', mesh.policies['sales'].version, node.kind, 'aggregated')
        certs = () if node.certificates else (cert,)
        return {name: replace(node, certificates=certs)}, {}, kind
    return {name: replace(node)}, {}, kind


def benchmark(args):
    rows = []
    for topology in args.topologies:
        for rate in args.discharge_rates:
            for seed in range(args.seeds):
                nodes, policies, obligation = build(args.nodes, seed+101, topology, rate)
                meshes = {s: Mesh(nodes, policies) for s in STRATEGIES}
                for m in meshes.values():
                    m.initialize()
                rng = random.Random(seed+9901)
                for step in range(args.changes):
                    changes, policy_changes, kind = edit(meshes['full'], rng, step, args.workload, obligation)
                    for strategy, mesh in meshes.items():
                        start = time.perf_counter_ns()
                        stats = mesh.update(changes, policy_changes, strategy)
                        elapsed = time.perf_counter_ns()-start
                        rows.append(dict(topology=topology, discharge_rate=rate, seed=seed,
                                         step=step, change_type=kind, strategy=strategy,
                                         elapsed_ns=elapsed, recomputed=stats.recomputed,
                                         visited=stats.visited, stopped=stats.stopped, equal_full=True))
                    if not (meshes['full'].cache == meshes['desc'].cache == meshes['igv'].cache):
                        raise AssertionError(f'Differential failure: {topology=} {rate=} {seed=} {step=}')
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    return rows


def cluster_bootstrap(values, seed=2708, reps=2000):
    """95% percentile interval resampling independent seeds (not individual edits)."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    keys = list(values)
    draws = []
    for _ in range(reps):
        sampled = [rng.choice(keys) for _ in keys]
        draws.append(statistics.mean(statistics.mean(values[k]) for k in sampled))
    draws.sort()
    return [round(draws[int(.025*reps)], 4), round(draws[int(.975*reps)-1], 4)]


def summarize(rows, reps):
    groups = defaultdict(list)
    for r in rows:
        groups[(r['topology'], r['discharge_rate'], r['change_type'])].append(r)
    summaries = []
    for (topology, rate, change_type), subset in sorted(groups.items()):
        stats = {}
        for strategy in STRATEGIES:
            rs = [r for r in subset if r['strategy'] == strategy]
            clusters = defaultdict(list)
            for r in rs:
                clusters[r['seed']].append(r['elapsed_ns']/1e6)
            stats[strategy] = {
                'mean_ms': round(statistics.mean(r['elapsed_ns']/1e6 for r in rs), 4),
                'median_ms': round(statistics.median(r['elapsed_ns']/1e6 for r in rs), 4),
                'mean_recomputed': round(statistics.mean(r['recomputed'] for r in rs), 2),
                'seed_cluster_ci95_ms': cluster_bootstrap(clusters, reps=reps),
            }
        stats['igv_vs_desc_speedup_means'] = round(stats['desc']['mean_ms']/stats['igv']['mean_ms'], 3) if stats['igv']['mean_ms'] else None
        summaries.append(dict(topology=topology, discharge_rate=rate, change_type=change_type,
                              edits_per_strategy=len(subset)//3, strategies=stats))
    return summaries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nodes', type=int, default=500)
    ap.add_argument('--changes', type=int, default=25)
    ap.add_argument('--seeds', type=int, default=5)
    ap.add_argument('--topologies', nargs='+', choices=['dag','tree','chain','layered'], default=['dag','tree','layered'])
    ap.add_argument('--discharge-rates', nargs='+', type=float, default=[0.05, 0.3])
    ap.add_argument('--workload', choices=[*KINDS,'mixed'], default='mixed')
    ap.add_argument('--bootstrap', type=int, default=1000)
    ap.add_argument('--out', default='benchmark_v08.csv')
    ap.add_argument('--summary', default='benchmark_v08_summary.json')
    args = ap.parse_args()
    if args.nodes < 2 or args.seeds < 1 or args.changes < 1 or args.bootstrap < 100:
        ap.error('nodes >=2, seeds >=1, changes >=1, bootstrap >=100 required')
    if any(not 0 <= r <= 1 for r in args.discharge_rates):
        ap.error('discharge rates must be between 0 and 1')
    rows = benchmark(args)
    result = {'settings': vars(args), 'all_equal': True, 'samples_per_strategy': len(rows)//3,
              'groups': summarize(rows, args.bootstrap),
              'cautions': ['Synthetic graph and symbolic certificates',
                           'CI resamples seeds; measurements on one host are not independent hardware replicates',
                           'Speedup ratio is ratio of arithmetic means, not mean of paired ratios']}
    Path(args.summary).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'all_equal': True, 'samples_per_strategy': len(rows)//3,
                      'groups': len(result['groups']), 'csv': args.out, 'summary': args.summary}, indent=2))


if __name__ == '__main__':
    main()
