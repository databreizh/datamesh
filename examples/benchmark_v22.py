"""V0.22 paired evaluation: FULL, IGV, conservative descendants.

Per-seed resampling avoids treating sequential edits as independent experiments.
"""
import argparse
import csv
import json
import random
import statistics
import time
from dataclasses import replace
from pathlib import Path

from datamesh_formal.evidence import row
from datamesh_formal.lifecycle import recompute
from datamesh_formal.materialized import MaterializedMesh, full_refresh
from benchmark_v21 import build


def ratio(pairs):
    return sum(p[0] for p in pairs) / sum(p[1] for p in pairs)


def cluster_ci(seed_pairs, iterations=2000, rng_seed=2201):
    """Resample entire independent seeds, keeping within-seed edit correlations."""
    if len(seed_pairs) < 2:
        return None
    rng = random.Random(rng_seed)
    keys = list(seed_pairs)
    results = []
    for _ in range(iterations):
        sample = [seed_pairs[rng.choice(keys)] for _ in keys]
        full = sum(sum(a for a, b in cluster) for cluster in sample)
        incremental = sum(sum(b for a, b in cluster) for cluster in sample)
        results.append(full / incremental)
    results.sort()
    return [results[int(.025 * (len(results)-1))], results[int(.975 * (len(results)-1))]]


def mutate(mesh, edit):
    current = list(mesh.nodes['external'].records)
    if current:
        d = dict(current[0]); d['patient_id'] = 'X' if d['patient_id'] != 'X' else 'P0'
        current[0] = row(**d)
    else:
        current = [row(key='unmatched', patient_id='X' if edit % 2 else 'Y')]
    return replace(mesh.nodes['external'], records=tuple(current))


def run(args):
    if args.seeds < 2 or args.edits < 1 or args.bootstrap < 1:
        raise ValueError('at least two seeds, one edit and one bootstrap iteration required')
    records = []
    init_records = []
    for n in args.nodes:
        for count in args.rows:
            for sel in args.selectivities:
                for hidden in (False, True):
                    for seed in range(args.seeds):
                        base, reqs, boundaries = build(n, count, sel, hidden)
                        start = time.perf_counter_ns()
                        igv = MaterializedMesh(base, reqs, boundaries=boundaries)
                        init_igv = (time.perf_counter_ns()-start)/1e6
                        start = time.perf_counter_ns()
                        descendants = MaterializedMesh(base, reqs, boundaries=boundaries)
                        init_desc = (time.perf_counter_ns()-start)/1e6
                        init_records.append(dict(nodes=n, rows=count, selectivity=sel, hidden=hidden,
                                                 seed=seed, igv_initialization_ms=init_igv,
                                                 descendants_initialization_ms=init_desc))
                        for edit in range(args.edits):
                            patch = mutate(igv, edit)
                            prior = list(igv.nodes.values())
                            # Alternating evaluation order reduces systematic order effects.
                            if (seed+edit) % 2:
                                start = time.perf_counter_ns()
                                fresh = full_refresh(prior, sources=[patch])
                                fmat = (time.perf_counter_ns()-start)/1e6
                                start = time.perf_counter_ns()
                                decision = recompute(fresh, reqs, (), boundaries)
                                fgov = (time.perf_counter_ns()-start)/1e6
                                start = time.perf_counter_ns()
                                istats = igv.update(sources=[patch])
                                itotal = (time.perf_counter_ns()-start)/1e6
                                start = time.perf_counter_ns()
                                dstats = descendants.update(sources=[patch], strategy='descendants')
                                dtotal = (time.perf_counter_ns()-start)/1e6
                            else:
                                start = time.perf_counter_ns()
                                dstats = descendants.update(sources=[patch], strategy='descendants')
                                dtotal = (time.perf_counter_ns()-start)/1e6
                                start = time.perf_counter_ns()
                                istats = igv.update(sources=[patch])
                                itotal = (time.perf_counter_ns()-start)/1e6
                                start = time.perf_counter_ns()
                                fresh = full_refresh(prior, sources=[patch])
                                fmat = (time.perf_counter_ns()-start)/1e6
                                start = time.perf_counter_ns()
                                decision = recompute(fresh, reqs, (), boundaries)
                                fgov = (time.perf_counter_ns()-start)/1e6
                            target = {x.id:x.records for x in fresh}
                            assert igv.decisions() == descendants.decisions() == decision
                            assert {k:v.records for k,v in igv.nodes.items()} == target
                            assert {k:v.records for k,v in descendants.nodes.items()} == target
                            records.append(dict(nodes=n, rows=count, selectivity=sel, hidden=hidden,
                                                seed=seed, edit=edit, full_materialize_ms=fmat,
                                                full_governance_ms=fgov, full_total_ms=fmat+fgov,
                                                igv_materialize_ms=istats['materialize_ms'],
                                                igv_governance_ms=istats['governance_ms'],
                                                igv_total_ms=itotal, igv_operators=istats['operators_evaluated'],
                                                igv_governance_nodes=istats['recomputed'],
                                                descendants_materialize_ms=dstats['materialize_ms'],
                                                descendants_governance_ms=dstats['governance_ms'],
                                                descendants_total_ms=dtotal,
                                                descendants_operators=dstats['operators_evaluated'],
                                                descendants_governance_nodes=dstats['recomputed'], equal=True))
    groups=[]
    for key in sorted(set((r['nodes'],r['rows'],r['selectivity'],r['hidden']) for r in records)):
        rr=[r for r in records if (r['nodes'],r['rows'],r['selectivity'],r['hidden'])==key]
        seeds=sorted({r['seed'] for r in rr})
        pairs={s:[(r['full_total_ms'],r['igv_total_ms']) for r in rr if r['seed']==s] for s in seeds}
        comp={s:[(r['descendants_total_ms'],r['igv_total_ms']) for r in rr if r['seed']==s] for s in seeds}
        init=[r for r in init_records if (r['nodes'],r['rows'],r['selectivity'],r['hidden'])==key]
        mean_init=statistics.mean(r['igv_initialization_ms'] for r in init)
        full_mean=statistics.mean(r['full_total_ms'] for r in rr)
        igv_mean=statistics.mean(r['igv_total_ms'] for r in rr)
        delta=full_mean-igv_mean
        # FULL has no cache initialization in this in-memory benchmark. Break-even
        # is conservative and includes the full IGV initial state construction.
        break_even=None if delta <= 0 else int(__import__('math').ceil(mean_init/delta))
        groups.append(dict(nodes=key[0], rows=key[1], selectivity=key[2], hidden=key[3],
                           samples=len(rr), independent_seeds=len(seeds),
                           full_over_igv=ratio([p for v in pairs.values() for p in v]),
                           full_over_igv_seed_cluster_95_ci=cluster_ci(pairs, args.bootstrap),
                           descendants_over_igv=ratio([p for v in comp.values() for p in v]),
                           descendants_over_igv_seed_cluster_95_ci=cluster_ci(comp, args.bootstrap),
                           mean_full_ms=full_mean, mean_igv_ms=igv_mean,
                           mean_descendants_ms=statistics.mean(r['descendants_total_ms'] for r in rr),
                           mean_igv_materialize_ms=statistics.mean(r['igv_materialize_ms'] for r in rr),
                           mean_igv_governance_ms=statistics.mean(r['igv_governance_ms'] for r in rr),
                           mean_igv_initialization_ms=mean_init,
                           break_even_edits_vs_uninitialized_full=break_even,
                           mean_igv_governance_nodes=statistics.mean(r['igv_governance_nodes'] for r in rr),
                           mean_descendants_governance_nodes=statistics.mean(r['descendants_governance_nodes'] for r in rr)))
    for path in (args.out,args.summary):Path(path).parent.mkdir(parents=True,exist_ok=True)
    with open(args.out,'w',newline='',encoding='utf8') as f:
        writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    summary=dict(settings=vars(args),paired_edits=len(records),all_equal=True,groups=groups,
                 initializations=init_records,limitations=[
                     'Fixed DAG, synthetic in-memory rows, nested-loop join; no I/O or distributed system',
                     'Descendants is a conservative governance-propagation baseline sharing the same data refresh implementation',
                     'FULL starts from an existing graph and has no initialization charge; IGV break-even is deliberately conservative',
                     'Bootstrap resamples independent seed trajectories, not sequential edits; 5 seeds are insufficient for strong inference',
                     'Timed update total includes orchestration overhead; measured phase times are internal and need not sum exactly to wall time',
                     'Seed parameter does not currently randomize the generated dataset; independent seeds are timing repetitions, not independently sampled datasets',
                     'No ICWS 2024 implementation is evaluated'])
    with open(args.summary,'w',encoding='utf8') as f:json.dump(summary,f,indent=2)
    print('Validated',len(records),'paired edits; 3 evaluators agree;',len(groups),'configurations')
    for g in groups:print('n={nodes} rows={rows} sel={selectivity} hidden={hidden} FULL/IGV={full_over_igv:.2f}x DESC/IGV={descendants_over_igv:.2f}x break_even={break_even_edits_vs_uninitialized_full}'.format(**g))
    return records,groups

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--nodes',type=int,nargs='+',default=[20,100]);p.add_argument('--rows',type=int,nargs='+',default=[20,100])
    p.add_argument('--selectivities',type=float,nargs='+',default=[0,.5,1]);p.add_argument('--seeds',type=int,default=5)
    p.add_argument('--edits',type=int,default=6);p.add_argument('--bootstrap',type=int,default=2000)
    p.add_argument('--out',default='benchmark_v22.csv');p.add_argument('--summary',default='benchmark_v22_summary.json')
    run(p.parse_args())
