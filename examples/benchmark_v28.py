"""V0.28: leave-one-topology-out (LOTO) structural transfer experiment.

Train and test instances are generated on disjoint topologies AND seeds.
The policy never sees held-out topology timings. A simple pooled estimator
transfers across topologies, with a conservative fallback.
"""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from benchmark_v27 import key
from benchmark_v23 import build_instance, mutate
from datamesh_formal.materialized import MaterializedMesh, full_refresh
from datamesh_formal.lifecycle import recompute
import time


def train_transfer_policy(records, min_gain=0.03, feature_set='full'):
    """Pooled timing estimator without topology as a feature.

    full: (nodes,rows,selectivity,hidden)
    coarse: (selectivity,hidden)
    rule: hand-specified, never trained; sync iff visible and selectivity>0.
    """
    if feature_set == 'rule':
        return {}
    sums=defaultdict(lambda:[0.,0.])
    for r in records:
        k=features(r,feature_set)
        sums[k][0]+=float(r['igv_ms']); sums[k][1]+=float(r.get('full_sync_ms',r.get('sync_ms',0.)))
    return {k: ('sync' if s < i*(1-min_gain) else 'igv') for k,(i,s) in sums.items()}


def features(r, feature_set):
    if feature_set=='full': return (int(r['nodes']),int(r['rows']),float(r['selectivity']),bool(r['hidden']))
    if feature_set=='coarse': return (float(r['selectivity']),bool(r['hidden']))
    raise ValueError(feature_set)


def decide(policy, r, feature_set):
    if feature_set=='rule':
        return 'sync' if not r['hidden'] and r['selectivity']>0 else 'igv'
    return policy.get(features(r,feature_set),'igv')


def collect(config,seed,edits,policies=None):
    n,count,sel,topology,hidden=config
    base,reqs,boundaries=build_instance(n,count,sel,hidden,topology,seed)
    engines={m:MaterializedMesh(base,reqs,boundaries=boundaries) for m in ('igv','sync')}
    if policies:
        for name in policies: engines[name]=MaterializedMesh(base,reqs,boundaries=boundaries)
    rows=[]
    for edit in range(edits):
        patch=mutate(engines['igv'],seed,edit)
        prior=list(engines['igv'].nodes.values())
        row=dict(nodes=n,rows=count,selectivity=sel,topology=topology,hidden=hidden,seed=seed,edit=edit,equal=True)
        methods=list(engines)
        shift=(seed+edit)%len(methods);methods=methods[shift:]+methods[:shift]
        for m in methods:
            choice=m if m in ('igv','sync') else decide(policies[m][0],row,policies[m][1])
            start=time.perf_counter_ns()
            if choice=='sync': engines[m].full_sync(sources=[patch])
            else: engines[m].update(sources=[patch])
            row[m+'_ms']=(time.perf_counter_ns()-start)/1e6
            if m not in ('igv','sync'):row[m+'_choice']=choice
        expected=full_refresh(prior,sources=[patch]); oracle=recompute(expected,reqs,(),boundaries)
        target={x.id:x.records for x in expected}
        for m,engine in engines.items():
            if engine.decisions()!=oracle or {k:v.records for k,v in engine.nodes.items()}!=target:
                raise AssertionError((config,seed,edit,m))
        rows.append(row)
    return rows


def run(args):
    if len(set(args.topologies))<2:raise ValueError('LOTO requires at least two distinct topologies')
    if min(args.train_seeds,args.test_seeds,args.edits)<1 or not 0<=args.min_gain<1:raise ValueError('invalid arguments')
    all_train=[];all_test=[];folds=[]
    for fold,heldout in enumerate(args.topologies):
        train=[];test=[]
        for topology in args.topologies:
            if topology==heldout:continue
            for n in args.nodes:
                for count in args.rows:
                    for sel in args.selectivities:
                        for hidden in (False,True):
                            c=key(n,count,sel,topology,hidden)
                            for j in range(args.train_seeds):
                                train.extend(collect(c,args.seed_offset+j,args.edits))
        models={f:train_transfer_policy(train,args.min_gain,f) for f in ('full','coarse','rule')}
        policies={f'adaptive_{f}':(models[f],f) for f in models}
        for n in args.nodes:
            for count in args.rows:
                for sel in args.selectivities:
                    for hidden in (False,True):
                        c=key(n,count,sel,heldout,hidden)
                        for j in range(args.test_seeds):
                            test.extend(collect(c,args.seed_offset+args.train_seeds+j,args.edits,policies))
        assert not ({r['topology'] for r in train}&{r['topology'] for r in test})
        totals={name:sum(r[name+'_ms'] for r in test) for name in ('igv','sync',*policies)}
        fold_summary=dict(heldout_topology=heldout,train_topologies=sorted({r['topology'] for r in train}),
                          train_edits=len(train),test_edits=len(test),all_equal=all(r['equal'] for r in test),
                          totals_ms=totals,ratios_to_igv={name:val/totals['igv'] for name,val in totals.items() if name!='igv'},
                          sync_choices={name:sum(r[name+'_choice']=='sync' for r in test) for name in policies})
        folds.append(fold_summary)
        for r in train:r['heldout_fold']=heldout
        for r in test:r['heldout_fold']=heldout
        all_train.extend(train);all_test.extend(test)
    for path,records in ((args.training_out,all_train),(args.out,all_test)):
        p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    totals={name:sum(f['totals_ms'][name] for f in folds) for name in folds[0]['totals_ms']}
    summary=dict(protocol='leave-one-topology-out',topologies=args.topologies,train_seed_range=[args.seed_offset,args.seed_offset+args.train_seeds-1],
                 test_seed_range=[args.seed_offset+args.train_seeds,args.seed_offset+args.train_seeds+args.test_seeds-1],
                 min_gain=args.min_gain,folds=folds,totals_ms=totals,
                 ratios_to_igv={k:v/totals['igv'] for k,v in totals.items() if k!='igv'},
                 all_equal=all(f['all_equal'] for f in folds),
                 caveats=['Held-out topology families, but same generator and size/selectivity regimes',
                          'Each fold has its own training data; folds are not independent',
                          'Timings exclude initialization; independent engines may introduce measurement noise',
                          'No training or hyperparameter tuning on held-out folds; min_gain fixed a priori',
                          'Rule baseline is intentionally simple and not optimized on test data'])
    p=Path(args.summary);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(summary,indent=2))
    print(json.dumps({k:v for k,v in summary.items() if k!='folds'},indent=2))
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--nodes',nargs='+',type=int,default=[20,100]);p.add_argument('--rows',nargs='+',type=int,default=[20,100])
    p.add_argument('--selectivities',nargs='+',type=float,default=[0,.5,1]);p.add_argument('--topologies',nargs='+',default=['chain','fanout','layered'])
    p.add_argument('--train-seeds',type=int,default=10);p.add_argument('--test-seeds',type=int,default=10)
    p.add_argument('--seed-offset',type=int,default=3000);p.add_argument('--edits',type=int,default=8)
    p.add_argument('--min-gain',type=float,default=.03)
    p.add_argument('--training-out',default='benchmark_v28_training.csv');p.add_argument('--out',default='benchmark_v28.csv');p.add_argument('--summary',default='benchmark_v28_summary.json')
    run(p.parse_args())
