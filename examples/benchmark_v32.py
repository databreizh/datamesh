"""V0.32: pre-edit cost prediction with conservative uncertainty and measured decision overhead.

Training on regular graphs; held-out regular and irregular evaluation. No test timing
used to fit or tune models. The counterfactual estimator is not an executable engine.
"""
import argparse, csv, json, random, statistics, time, math
from collections import defaultdict
from pathlib import Path
from benchmark_v23 import build_instance, mutate
from benchmark_v30 import irregular_instance
from benchmark_v31 import graph_features, bucket, signature
from datamesh_formal.materialized import MaterializedMesh, full_refresh
from datamesh_formal.lifecycle import recompute

FEATURES=('reachable_ratio','visible_ratio','volume_ratio','branching_ratio','max_fanout','boundary_count','requirement_count')

def train_costs(rows, margin=.03, min_support=8, uncertainty=1.0):
    """Per-bucket median relative-cost estimates, conservative empirical upper bound.

    One observation per seed/configuration/repeat (median of edits), preventing
    repeated edits from masquerading as independent evidence.
    """
    groups=defaultdict(list)
    for r in rows:
        key=(r['nodes'],r['rows'],r['selectivity'],r['topology'],r['hidden'],r['seed'],r['repeat'])
        groups[(r['bucket'],key)].append(r)
    by_bucket=defaultdict(list)
    for (b,_),sample in groups.items():
        igv=sum(x['igv_ms'] for x in sample)
        sync=sum(x['sync_ms'] for x in sample)
        if igv>0:by_bucket[b].append(sync/igv)
    model={}
    for b,ratios in by_bucket.items():
        ratios.sort()
        median=statistics.median(ratios)
        # Conservative upper quartile; scale its distance from median.
        q75=ratios[math.ceil(.75*len(ratios))-1]
        upper=median+uncertainty*max(0,q75-median)
        model[b]=dict(n=len(ratios),ratio=median,upper=upper,
                      choice='sync' if len(ratios)>=min_support and upper < 1-margin else 'igv')
    return model

def choose(f,model,margin=.03,min_support=8,uncertainty=1.0):
    entry=model.get(bucket(f))
    if entry is None:return 'igv','unseen_bucket',float('nan'),float('nan')
    if entry['n']<min_support:return 'igv','low_support',entry['ratio'],entry['upper']
    if entry['upper'] >= 1-margin:return 'igv','uncertain_or_costly',entry['ratio'],entry['upper']
    return 'sync','cost_advantage',entry['ratio'],entry['upper']

def case(cfg,seed,edits,repeats,model=None,irregular=False,order_seed=32,margin=.03,min_support=8,uncertainty=1.0):
    n,count,sel,topology,hidden=cfg
    builder=irregular_instance if irregular else build_instance
    base,reqs,boundaries=builder(n,count,sel,hidden,topology,seed)
    out=[]
    for rep in range(repeats):
        names=['igv','sync']+(['cost_policy'] if model is not None else [])
        engines={name:MaterializedMesh(base,reqs,boundaries=boundaries) for name in names}
        for edit in range(edits):
            patch=mutate(engines['igv'],seed,edit)
            previous=list(engines['igv'].nodes.values())
            row=dict(nodes=n,rows=count,selectivity=sel,topology=topology,hidden=hidden,
                     seed=seed,repeat=rep,edit=edit,generator='irregular' if irregular else 'regular')
            # Feature and decision costs are timed, unlike V0.31.
            t0=time.perf_counter_ns()
            f=graph_features(list(engines['igv'].nodes.values()),reqs,boundaries)
            row['feature_ms']=(time.perf_counter_ns()-t0)/1e6
            row['bucket']=bucket(f)  # Canonical tuple: same type in training and inference
            row.update({k:f[k] for k in FEATURES})
            if model is not None:
                t0=time.perf_counter_ns()
                choice,reason,pred,upper=choose(f,model,margin,min_support,uncertainty)
                row['decision_ms']=(time.perf_counter_ns()-t0)/1e6
                row.update(cost_policy_choice=choice,cost_policy_reason=reason,predicted_ratio=pred,upper_ratio=upper)
            order=names[:]
            random.Random(order_seed+seed*1009+edit*13+rep*101).shuffle(order)
            for name in order:
                strategy=choice if name=='cost_policy' else name
                start=time.perf_counter_ns()
                if strategy=='sync':stats=engines[name].full_sync(sources=[patch])
                else:stats=engines[name].update(sources=[patch])
                row[name+'_ms']=(time.perf_counter_ns()-start)/1e6
                row[name+'_operators']=stats.get('operators_evaluated',0)
                row[name+'_governance_recomputed']=stats.get('recomputed',0)
            expected=full_refresh(previous,sources=[patch])
            oracle=recompute(expected,reqs,(),boundaries)
            target={x.id:x.records for x in expected}
            for name,engine in engines.items():
                if engine.decisions()!=oracle or {k:v.records for k,v in engine.nodes.items()}!=target:
                    raise AssertionError((cfg,seed,edit,name))
            if model is not None:
                row['cost_policy_paired_ms']=row[choice+'_ms']
                row['cost_policy_end_to_end_ms']=row['cost_policy_ms']+row['feature_ms']+row['decision_ms']
                row['oracle_best_ms']=min(row['igv_ms'],row['sync_ms'])
                row['paired_regret_ms']=row['cost_policy_paired_ms']-row['oracle_best_ms']
                row['harmful_sync']=int(choice=='sync' and row['sync_ms']>row['igv_ms'])
            row['equal']=True
            out.append(row)
    return out

def summary(rows):
    igv=sum(r['igv_ms'] for r in rows)
    sync=sum(r['sync_ms'] for r in rows)
    actual=sum(r['cost_policy_ms'] for r in rows)
    paired=sum(r['cost_policy_paired_ms'] for r in rows)
    end=sum(r['cost_policy_end_to_end_ms'] for r in rows)
    oracle=sum(r['oracle_best_ms'] for r in rows)
    return dict(count=len(rows),all_equal=all(r['equal'] for r in rows),
                igv_ms=igv,sync_ms=sync,cost_policy_ms=actual,
                paired_ms=paired,end_to_end_ms=end,
                feature_overhead_ms=sum(r['feature_ms'] for r in rows),
                decision_overhead_ms=sum(r['decision_ms'] for r in rows),
                gain_actual_pct=100*(1-actual/igv),gain_paired_pct=100*(1-paired/igv),
                gain_end_to_end_pct=100*(1-end/igv),
                oracle_best_ms=oracle,paired_regret_ms=paired-oracle,
                sync_choices=sum(r['cost_policy_choice']=='sync' for r in rows),
                harmful_sync=sum(r['harmful_sync'] for r in rows),
                reasons=dict((reason,sum(r['cost_policy_reason']==reason for r in rows))
                             for reason in sorted({r['cost_policy_reason'] for r in rows})))

def write_csv(path,rows):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w',newline='',encoding='utf8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)

def run(args):
    if min(args.train_seeds,args.test_seeds,args.edits,args.repeats)>0:pass
    else:raise ValueError('counts must be positive')
    configs=[(n,r,s,t,h) for n in args.nodes for r in args.rows for s in args.selectivities
             for t in args.topologies for h in (False,True)]
    train=[]
    for cfg in configs:
        for i in range(args.train_seeds):
            train.extend(case(cfg,args.seed_offset+i,args.edits,args.repeats))
    model=train_costs(train,args.min_gain,args.min_support,args.uncertainty)
    assert all(isinstance(k,tuple) for k in model), 'Noncanonical training buckets'
    assert set(r['bucket'] for r in train)==set(model), 'Training buckets are not recognized'
    write_csv(args.training_out,train)
    results={};all_test=[]
    for generator in ('regular','irregular'):
        rows=[]
        for cfg in configs:
            for i in range(args.test_seeds):
                rows.extend(case(cfg,args.seed_offset+args.train_seeds+i,args.edits,args.repeats,
                                 model,irregular=generator=='irregular',order_seed=args.order_seed,
                                 margin=args.min_gain,min_support=args.min_support,uncertainty=args.uncertainty))
        results[generator]=dict(**summary(rows),by_topology={t:summary([r for r in rows if r['topology']==t]) for t in args.topologies})
        all_test.extend(rows)
    write_csv(args.out,all_test)
    for gen in ('regular','irregular'):
        matches=sum(r['cost_policy_reason']!='unseen_bucket' for r in all_test if r['generator']==gen)
        print(f'Bucket coverage {gen}: {matches}/{sum(r["generator"]==gen for r in all_test)}',flush=True)
    result=dict(bucket_format='canonical tuple (CSV repr only)',protocol='train regular / test disjoint regular and irregular; conservative per-bucket cost ratio',
                train_edits=len(train),test_edits=len(all_test),
                model_buckets=len(model),sync_buckets=sum(v['choice']=='sync' for v in model.values()),
                settings=dict(min_gain=args.min_gain,min_support=args.min_support,uncertainty=args.uncertainty,
                              repeats=args.repeats,train_seeds=args.train_seeds,test_seeds=args.test_seeds),
                results=results,
                caveats=['Model predicts ratio from per-bucket aggregate training costs, not a calibrated probabilistic bound',
                         'Upper quartile is a heuristic uncertainty margin, not a statistical guarantee',
                         'Oracle-best uses per-edit measured times and is a hindsight lower bound',
                         'End-to-end includes pre-edit feature and decision cost, but excludes engine initialization and offline training',
                         'All workloads synthetic and share the same Python in-memory implementation',
                         'Timing order randomized, but cache/system noise remains; independent timings differ from paired estimates'])
    Path(args.summary).write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result,indent=2));return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--nodes',nargs='+',type=int,default=[20,100]);p.add_argument('--rows',nargs='+',type=int,default=[20,100])
    p.add_argument('--selectivities',nargs='+',type=float,default=[0,.5,1]);p.add_argument('--topologies',nargs='+',default=['chain','fanout','layered'])
    p.add_argument('--train-seeds',type=int,default=10);p.add_argument('--test-seeds',type=int,default=10)
    p.add_argument('--seed-offset',type=int,default=7000);p.add_argument('--edits',type=int,default=8)
    p.add_argument('--repeats',type=int,default=3);p.add_argument('--order-seed',type=int,default=32)
    p.add_argument('--min-gain',type=float,default=.03);p.add_argument('--min-support',type=int,default=8)
    p.add_argument('--uncertainty',type=float,default=1.0)
    p.add_argument('--training-out',default='benchmark_v32_training.csv');p.add_argument('--out',default='benchmark_v32.csv')
    p.add_argument('--summary',default='benchmark_v32_summary.json')
    run(p.parse_args())
