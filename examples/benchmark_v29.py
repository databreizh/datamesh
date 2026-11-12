"""V0.29: LOTO ablations, balanced method order and paired counterfactuals.

The counterfactual cost uses IGV/SYNC baselines measured on the SAME edit;
actual controller times are reported separately, never treated as policy gain.
"""
import argparse,csv,json,random,time,statistics
from pathlib import Path
from collections import defaultdict
from benchmark_v23 import build_instance,mutate
from benchmark_v27 import key
from datamesh_formal.materialized import MaterializedMesh,full_refresh
from datamesh_formal.lifecycle import recompute

FEATURES={
 'full':('nodes','rows','selectivity','hidden'),
 'no_hidden':('nodes','rows','selectivity'),
 'no_selectivity':('nodes','rows','hidden'),
 'no_size':('selectivity','hidden'),
 'hidden_only':('hidden',),
 'selectivity_only':('selectivity',),
 'size_only':('nodes','rows'),
 'constant':(),
}

def feature(row,variant):return tuple(row[k] for k in FEATURES[variant])

def train(records,variant,margin=.03):
    sums=defaultdict(lambda:[0.,0.])
    for r in records:
        s=sums[feature(r,variant)];s[0]+=float(r['igv_ms']);s[1]+=float(r['sync_ms'])
    return {k:('sync' if v[1]<v[0]*(1-margin) else 'igv') for k,v in sums.items()}

def choose(policy,row,variant):return policy.get(feature(row,variant),'igv')

def collect(config,seed,edits,policies=None,repeat=0,order_seed=0,builder=build_instance):
    n,count,sel,topology,hidden=config
    base,reqs,boundaries=builder(n,count,sel,hidden,topology,seed)
    names=['igv','sync']+list(policies or {})
    engines={m:MaterializedMesh(base,reqs,boundaries=boundaries) for m in names}
    rows=[]
    for edit in range(edits):
        patch=mutate(engines['igv'],seed,edit)
        prior=list(engines['igv'].nodes.values())
        row=dict(nodes=n,rows=count,selectivity=sel,topology=topology,hidden=hidden,seed=seed,edit=edit,repeat=repeat,equal=True)
        methods=names[:]
        # Deterministic balanced rotations across repeats; random permutation within blocks.
        rng=random.Random((order_seed+seed*10007+edit*1009)//max(1,len(methods)))
        rng.shuffle(methods)
        shift=(repeat+edit)%len(methods)
        methods=methods[shift:]+methods[:shift]
        for m in methods:
            decision=m if m in ('igv','sync') else choose(policies[m][0],row,policies[m][1])
            start=time.perf_counter_ns()
            if decision=='sync':engines[m].full_sync(sources=[patch])
            else:engines[m].update(sources=[patch])
            row[m+'_ms']=(time.perf_counter_ns()-start)/1e6
            if m not in ('igv','sync'):row[m+'_choice']=decision
        expected=full_refresh(prior,sources=[patch]);oracle=recompute(expected,reqs,(),boundaries)
        target={x.id:x.records for x in expected}
        for m,engine in engines.items():
            if engine.decisions()!=oracle or {k:v.records for k,v in engine.nodes.items()}!=target:
                raise AssertionError((config,seed,edit,m))
        for m in (policies or {}):
            # Same-edit measured baseline cost, unlike independently timed controller.
            row[m+'_paired_ms']=row['sync_ms'] if row[m+'_choice']=='sync' else row['igv_ms']
        rows.append(row)
    return rows

def summarize(records,names,bootstrap=500,bootstrap_seed=29):
    totals={m:sum(float(r[m+'_ms']) for r in records) for m in ('igv','sync',*names)}
    paired={m:sum(float(r[m+'_paired_ms']) for r in records) for m in names}
    # Cluster bootstrap by independent (heldout topology, seed, repeat) trajectory.
    clusters=defaultdict(list)
    for r in records:clusters[(r['heldout_fold'],r['seed'],r['repeat'])].append(r)
    values=[]
    for group in clusters.values():
        igv=sum(r['igv_ms'] for r in group)
        values.append((igv,{m:sum(r[m+'_paired_ms'] for r in group) for m in names}))
    rng=random.Random(bootstrap_seed);intervals={}
    for m in names:
        samples=[]
        for _ in range(bootstrap):
            picks=[values[rng.randrange(len(values))] for _ in values]
            a=sum(v[0] for v in picks);b=sum(v[1][m] for v in picks)
            samples.append(100*(a-b)/a)
        samples.sort();intervals[m]=[samples[int(.025*(bootstrap-1))],samples[int(.975*(bootstrap-1))]]
    return dict(totals_ms=totals,paired_totals_ms=paired,
        actual_gains_pct={m:100*(1-totals[m]/totals['igv']) for m in names},
        paired_gains_pct={m:100*(1-paired[m]/totals['igv']) for m in names},
        paired_bootstrap_95pct=intervals,
        sync_choices={m:sum(r[m+'_choice']=='sync' for r in records) for m in names})

def run(args):
    if len(set(args.topologies))<2:raise ValueError('Need >=2 topologies')
    if min(args.train_seeds,args.test_seeds,args.edits,args.repeats)<1:raise ValueError('Invalid counts')
    if not 0<=args.min_gain<1:raise ValueError('Invalid margin')
    all_train=[];all_test=[];folds=[]
    names=['adaptive_'+v for v in FEATURES]
    for heldout in args.topologies:
        train_rows=[];test_rows=[]
        for topology in args.topologies:
            if topology==heldout:continue
            for n in args.nodes:
                for count in args.rows:
                    for sel in args.selectivities:
                        for hidden in (False,True):
                            config=key(n,count,sel,topology,hidden)
                            for j in range(args.train_seeds):
                                for rep in range(args.repeats):
                                    train_rows+=collect(config,args.seed_offset+j,args.edits,repeat=rep,order_seed=args.order_seed)
        policies={'adaptive_'+v:(train(train_rows,v,args.min_gain),v) for v in FEATURES}
        for n in args.nodes:
            for count in args.rows:
                for sel in args.selectivities:
                    for hidden in (False,True):
                        config=key(n,count,sel,heldout,hidden)
                        for j in range(args.test_seeds):
                            for rep in range(args.repeats):
                                test_rows+=collect(config,args.seed_offset+args.train_seeds+j,args.edits,policies,repeat=rep,order_seed=args.order_seed)
        for r in train_rows:r['heldout_fold']=heldout
        for r in test_rows:r['heldout_fold']=heldout
        all_train+=train_rows;all_test+=test_rows
        s=summarize(test_rows,names,args.bootstrap,args.order_seed)
        folds.append(dict(heldout_topology=heldout,train_edits=len(train_rows),test_edits=len(test_rows),all_equal=all(r['equal'] for r in test_rows),**s))
    for path,records in ((args.training_out,all_train),(args.out,all_test)):
        p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
    s=summarize(all_test,names,args.bootstrap,args.order_seed)
    result=dict(protocol='LOTO ablation with repeated randomized order',train_edits=len(all_train),test_edits=len(all_test),repeats=args.repeats,
        train_seed_range=[args.seed_offset,args.seed_offset+args.train_seeds-1],test_seed_range=[args.seed_offset+args.train_seeds,args.seed_offset+args.train_seeds+args.test_seeds-1],
        min_gain=args.min_gain,feature_sets={k:list(v) for k,v in FEATURES.items()},folds=folds,all_equal=all(r['equal'] for r in all_test),**s,
        caveats=['Paired counterfactual is a timing-based estimator, not an executable engine or oracle',
        'Bootstrap clusters by (fold,seed,repeat), but repeats of a seed are correlated',
        'Timing excludes initialization; random ordering reduces but does not eliminate system noise',
        'All topologies come from one synthetic generator; no out-of-generator generalization',
        'Do not compare confidence intervals as a significance test for ablation differences'])
    p=Path(args.summary);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('folds','feature_sets')},indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--nodes',nargs='+',type=int,default=[20,100]);p.add_argument('--rows',nargs='+',type=int,default=[20,100]);p.add_argument('--selectivities',nargs='+',type=float,default=[0,.5,1]);p.add_argument('--topologies',nargs='+',default=['chain','fanout','layered'])
    p.add_argument('--train-seeds',type=int,default=10);p.add_argument('--test-seeds',type=int,default=10);p.add_argument('--seed-offset',type=int,default=4000);p.add_argument('--edits',type=int,default=8);p.add_argument('--repeats',type=int,default=3);p.add_argument('--order-seed',type=int,default=29);p.add_argument('--min-gain',type=float,default=.03);p.add_argument('--bootstrap',type=int,default=500)
    p.add_argument('--training-out',default='benchmark_v29_training.csv');p.add_argument('--out',default='benchmark_v29.csv');p.add_argument('--summary',default='benchmark_v29_summary.json')
    run(p.parse_args())
