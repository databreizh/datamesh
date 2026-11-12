"""V0.25: cost-frontier audit of held-out V0.24 measurements.

No timing oracle is used for deployment: this is retrospective cost accounting.
A FULL update followed by continued IGV operation must rebuild the cache.
A stateless FULL-only engine instead pays no rebuild but forfeits IGV state.
"""
import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

FIELDS = ('nodes','rows','selectivity','topology','hidden')

def parse_bool(value):
    if isinstance(value,bool): return value
    if str(value).lower() in ('true','1'): return True
    if str(value).lower() in ('false','0'): return False
    raise ValueError(value)

def read_rows(path):
    with open(path,newline='',encoding='utf8') as f: rows=list(csv.DictReader(f))
    for r in rows:
        for k in ('nodes','rows','seed','edit','igv_governance_nodes','igv_operators'): r[k]=int(r[k])
        r['selectivity']=float(r['selectivity']);r['hidden']=parse_bool(r['hidden']);r['equal']=parse_bool(r['equal'])
        for k in ('full_total_ms','full_switch_total_ms','igv_total_ms','adaptive_total_ms','full_cache_rebuild_ms'):r[k]=float(r[k])
    if not rows or not all(r['equal'] for r in rows):raise ValueError('Missing rows or correctness failure')
    return rows

def mean(xs):return statistics.mean(xs)

def summarize(rows,horizons=(1,2,4,8,16,32,64),thresholds=(0,.25,.5,.75,.9,1)):
    groups=defaultdict(list)
    for r in rows:groups[tuple(r[f] for f in FIELDS)].append(r)
    results=[]
    for key,rr in sorted(groups.items()):
        full=mean([r['full_total_ms'] for r in rr]); igv=mean([r['igv_total_ms'] for r in rr]); rebuild=mean([r['full_cache_rebuild_ms'] for r in rr]); switch=mean([r['full_switch_total_ms'] for r in rr]); adaptive=mean([r['adaptive_total_ms'] for r in rr]);
        n=key[0]; fraction=mean([r['igv_governance_nodes']/n for r in rr]);
        # FULL-only costs h*FULL; IGV h*IGV plus a ONE-TIME initialization.
        # Initialization is taken from test seed records in the V0.24 JSON (injected below).
        entry=dict(zip(FIELDS,key));entry.update(samples=len(rr),mean_full_ms=full,mean_igv_ms=igv,mean_cache_rebuild_ms=rebuild,mean_full_switch_ms=switch,mean_adaptive_ms=adaptive,
            full_over_igv=full/igv,full_over_switch=switch/igv,mean_governance_affected_fraction=fraction,
            full_without_rebuild_faster=full<igv,full_with_rebuild_faster=switch<igv,
            # Marginal benefit of FULL over IGV; positive only if FULL alone is faster.
            potential_saving_per_full_ms=igv-full,
            # A single FULL step + rebuild is beneficial iff igv > full + rebuild.
            single_switch_saving_ms=igv-switch,
            # At most this fraction of rebuild can be charged to a single FULL step.
            rebuild_budget_ms=max(0,igv-full),
            rebuild_to_saving_ratio=(rebuild/(igv-full) if igv>full else None))
        results.append(entry)
    return results

def audit(rows,summary,horizons,thresholds):
    initial=summary.get('test_initializations',[])
    inits={tuple(r[f] for f in FIELDS)+(r['seed'],):float(r['igv_initialization_ms']) for r in initial}
    grouped=defaultdict(list)
    for r in rows:grouped[tuple(r[f] for f in FIELDS)].append(r)
    groups=summarize(rows,horizons,thresholds)
    for g in groups:
        key=tuple(g[f] for f in FIELDS); rr=grouped[key]
        available=[inits[key+(seed,)] for seed in sorted({r['seed'] for r in rr}) if key+(seed,) in inits]
        init=mean(available) if available else None
        g['mean_igv_initialization_ms']=init
        g['full_only_vs_igv_break_even_edits']=(math.floor(init/(g['mean_full_ms']-g['mean_igv_ms']))+1 if init is not None and g['mean_full_ms']>g['mean_igv_ms'] else None)
        g['horizons']=[dict(edits=h,full_only_ms=h*g['mean_full_ms'],igv_ms=(init+h*g['mean_igv_ms'] if init is not None else None),
          # FULL once then IGV for remaining steps, rebuilding once: no future cost advantage
          full_once_then_igv_ms=(init+g['mean_full_switch_ms']+(h-1)*g['mean_igv_ms'] if init is not None else None),
          full_every_edit_and_rebuild_ms=(init+h*g['mean_full_switch_ms'] if init is not None else None)) for h in horizons]
    # Structural threshold is an *audit*, not a trained policy: group means from test data.
    frontier=[]
    for t in thresholds:
        selected=[g for g in groups if g['mean_governance_affected_fraction']>=t]
        frontier.append(dict(affected_fraction_threshold=t,configurations_selected=len(selected),
            full_only_better=sum(g['full_without_rebuild_faster'] for g in selected),
            full_plus_rebuild_better=sum(g['full_with_rebuild_faster'] for g in selected),
            average_full_over_igv=(mean([g['full_over_igv'] for g in selected]) if selected else None)))
    aggregate=dict(configurations=len(groups),edits=len(rows),full_only_faster_configurations=sum(g['full_without_rebuild_faster'] for g in groups),
        full_plus_rebuild_faster_configurations=sum(g['full_with_rebuild_faster'] for g in groups),
        full_over_igv=sum(r['full_total_ms'] for r in rows)/sum(r['igv_total_ms'] for r in rows),
        full_plus_rebuild_over_igv=sum(r['full_switch_total_ms'] for r in rows)/sum(r['igv_total_ms'] for r in rows),
        adaptive_full_choices=sum(r['adaptive_choice']=='full' for r in rows),
        hindsight_full_plus_rebuild_faster_edits=sum(r['full_switch_total_ms']<r['igv_total_ms'] for r in rows))
    return dict(version='0.25',aggregate=aggregate,groups=groups,structural_frontier=frontier,
        limitations=['Retrospective cost accounting on V0.24 held-out measurements, not a new runtime experiment',
        'Horizon estimates assume stationary per-edit means; actual sequential scheduling not measured',
        'FULL-only and IGV are distinct operating modes; FULL-only cannot keep IGV state for free',
        'A FULL-to-IGV switch includes cache reconstruction; no partial cache rebuild is implemented',
        'Affected fraction is observed after IGV execution, not a free pre-execution predictor',
        'Threshold frontier is descriptive and evaluated on held-out data, not a validated predictive policy',
        'Small Python in-memory timings and fixed synthetic topology families; no distributed system'])

def main():
    p=argparse.ArgumentParser();p.add_argument('--csv',default='benchmark_v24.csv');p.add_argument('--summary',default='benchmark_v24_summary.json');p.add_argument('--out',default='benchmark_v25_cost_frontier.json');p.add_argument('--groups-csv',default='benchmark_v25_groups.csv');p.add_argument('--horizons',type=int,nargs='+',default=[1,2,4,8,16,32,64]);p.add_argument('--thresholds',type=float,nargs='+',default=[0,.25,.5,.75,.9,1]);args=p.parse_args()
    with open(args.summary,encoding='utf8') as f:summary=json.load(f)
    rows=read_rows(args.csv)
    report=audit(rows,summary,args.horizons,args.thresholds)
    Path(args.out).parent.mkdir(parents=True,exist_ok=True)
    with open(args.out,'w',encoding='utf8') as f:json.dump(report,f,indent=2)
    fields=[k for k in report['groups'][0] if k!='horizons']
    with open(args.groups_csv,'w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows({k:r[k] for k in fields} for r in report['groups'])
    print(json.dumps(report['aggregate'],indent=2))
    return report
if __name__=='__main__':main()
