"""V0.27: train-only cost selection; held-out persistent trajectory evaluation.

The policy is a per-configuration empirical estimator, not generalization to unseen
configurations. Test timings and oracle choices are NEVER used to train the policy.
"""
import argparse, csv, json, time
from collections import defaultdict
from pathlib import Path
from datamesh_formal.materialized import MaterializedMesh, full_refresh
from datamesh_formal.lifecycle import recompute
from benchmark_v23 import build_instance, mutate


def key(n, rows, selectivity, topology, hidden):
    return (n, rows, float(selectivity), topology, bool(hidden))


def train_policy(records, min_gain=0.03):
    """Choose SYNC only when its TRAINING aggregate time is >= min_gain faster."""
    sums=defaultdict(lambda: [0.,0.])
    for r in records:
        v=sums[key(r['nodes'],r['rows'],r['selectivity'],r['topology'],r['hidden'])]
        v[0]+=r['igv_ms'];v[1]+=r['full_sync_ms']
    return {k: ('sync' if s < i*(1-min_gain) else 'igv') for k,(i,s) in sums.items()}


def run(args):
    if args.train_seeds<1 or args.test_seeds<1 or args.edits<1 or not 0<=args.min_gain<1:
        raise ValueError('invalid benchmark arguments')
    train=[];test=[]
    configs=[key(n,r,s,t,h) for n in args.nodes for r in args.rows for s in args.selectivities for t in args.topologies for h in (False,True)]
    def instance(config,seed,policy=None):
        n,count,sel,topology,hidden=config
        base,reqs,boundaries=build_instance(n,count,sel,hidden,topology,seed)
        engines={m:MaterializedMesh(base,reqs,boundaries=boundaries) for m in ('igv','sync')}
        if policy is not None: engines['adaptive']=MaterializedMesh(base,reqs,boundaries=boundaries)
        out=[]
        for edit in range(args.edits):
            patch=mutate(engines['igv'],seed,edit)
            prior=list(engines['igv'].nodes.values())
            timings={}; stats={}
            methods=list(engines)
            shift=(seed+edit)%len(methods);methods=methods[shift:]+methods[:shift]
            for m in methods:
                choice=policy[config] if m=='adaptive' else m
                start=time.perf_counter_ns()
                stats[m]=engines[m].full_sync(sources=[patch]) if choice=='sync' else engines[m].update(sources=[patch])
                timings[m]=(time.perf_counter_ns()-start)/1e6
            expected=full_refresh(prior,sources=[patch]); oracle=recompute(expected,reqs,(),boundaries)
            target={x.id:x.records for x in expected}
            for m,engine in engines.items():
                if engine.decisions()!=oracle or {k:v.records for k,v in engine.nodes.items()}!=target:
                    raise AssertionError((config,seed,edit,m))
            out.append(dict(nodes=n,rows=count,selectivity=sel,topology=topology,hidden=hidden,seed=seed,edit=edit,
                            igv_ms=timings['igv'],full_sync_ms=timings['sync'],
                            adaptive_ms=timings.get('adaptive',''),choice=policy[config] if policy is not None else '',
                            igv_governance_nodes=stats['igv']['recomputed'],
                            sync_governance_nodes=stats['sync']['recomputed'],equal=True))
        return out
    for c in configs:
        for i in range(args.train_seeds): train.extend(instance(c,args.seed_offset+i))
    policy=train_policy(train,args.min_gain)
    for c in configs:
        for i in range(args.test_seeds): test.extend(instance(c,args.seed_offset+args.train_seeds+i,policy))
    for path,rows in ((args.training_out,train),(args.out,test)):
        p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    totals={k:sum(float(r[k]) for r in test) for k in ('igv_ms','full_sync_ms','adaptive_ms')}
    hindsight=sum(min(float(r['igv_ms']),float(r['full_sync_ms'])) for r in test)
    summary={'train_edits':len(train),'test_edits':len(test),'all_equal':all(r['equal'] for r in test),
             'train_seed_range':[args.seed_offset,args.seed_offset+args.train_seeds-1],
             'test_seed_range':[args.seed_offset+args.train_seeds,args.seed_offset+args.train_seeds+args.test_seeds-1],
             'min_gain':args.min_gain,'configurations':len(configs),
             'sync_configurations':sum(x=='sync' for x in policy.values()),
             'sync_test_edits':sum(r['choice']=='sync' for r in test),
             'totals_ms':totals,'adaptive_over_igv':totals['adaptive_ms']/totals['igv_ms'],
             'hindsight_per_edit_lower_bound_ms':hindsight,
             'hindsight_caveat':'Infeasible post-hoc bound; timings from independent engines, not an executable strategy',
             'policy':[{'nodes':c[0],'rows':c[1],'selectivity':c[2],'topology':c[3],'hidden':c[4],'choice':v} for c,v in sorted(policy.items())]}
    Path(args.summary).write_text(json.dumps(summary,indent=2))
    print(json.dumps({k:v for k,v in summary.items() if k!='policy'},indent=2))
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--nodes',nargs='+',type=int,default=[20,100]);p.add_argument('--rows',nargs='+',type=int,default=[20,100])
    p.add_argument('--selectivities',nargs='+',type=float,default=[0,.5,1]);p.add_argument('--topologies',nargs='+',default=['chain','fanout','layered'])
    p.add_argument('--train-seeds',type=int,default=10);p.add_argument('--test-seeds',type=int,default=10)
    p.add_argument('--seed-offset',type=int,default=2000);p.add_argument('--edits',type=int,default=8)
    p.add_argument('--min-gain',type=float,default=.03)
    p.add_argument('--training-out',default='benchmark_v27_training.csv');p.add_argument('--out',default='benchmark_v27.csv')
    p.add_argument('--summary',default='benchmark_v27_summary.json')
    run(p.parse_args())
