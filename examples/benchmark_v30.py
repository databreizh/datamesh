"""V0.30 out-of-generator test: legacy regular DAGs -> irregular heterogeneous DAGs.

No model fitting, margin tuning or policy inspection on the held-out generator.
The test generator changes DAG structure, data skew, projection visibility, and
adds mixed governance obligations. All outcomes are verified against the oracle.
"""
import argparse, csv, json, random
from pathlib import Path
from dataclasses import replace
from benchmark_v23 import build_instance
from benchmark_v29 import collect, train, summarize, FEATURES
from datamesh_formal.evidence import DataNode, row
from datamesh_formal.lifecycle import Requirement, Mode
from datamesh_formal.materialized import full_refresh


def irregular_instance(n, count, selectivity, hidden, topology, seed):
    if n < 5 or count < 2 or not 0 <= selectivity <= 1:
        raise ValueError('invalid benchmark parameters')
    if topology not in ('chain','fanout','layered'):
        raise ValueError('invalid nominal topology')
    # Distinct RNG and graph construction from benchmark_v23.build_instance.
    rng = random.Random(seed * 104729 + 30007 + {'chain':11,'fanout':17,'layered':23}[topology])
    keys = rng.sample(range(max(10000,count*100)),count)
    # Skewed segment values, unlike uniform ABC in the original generator.
    customer = DataNode('customer',records=tuple(row(key=str(k),segment=rng.choices('ABC',weights=[85,10,5])[0]) for k in keys))
    matched = rng.sample(keys,round(count*selectivity))
    external = DataNode('external',records=tuple(row(key=str(k),patient_id='P'+str(rng.randrange(1000000))) for k in matched))
    nodes=[customer,external,DataNode('merged',('customer','external'),'join','key',())]
    # A DAG with irregular parent degrees and depth, rather than fixed shapes.
    # Every derived node has one parent; joins are inherited from the root.
    # The topology label biases depth, but does not determine exact edges.
    for i in range(n-3):
        if i==0:parent='merged'
        elif topology=='chain':parent=nodes[-1].id if rng.random()<.70 else rng.choice(nodes[2:]).id
        elif topology=='fanout':parent='merged' if rng.random()<.60 else rng.choice(nodes[2:]).id
        else:parent=nodes[max(2,len(nodes)-rng.randint(1,min(8,len(nodes)-2)))].id
        # Mixed visibility: local projections can differ even when global flag is fixed.
        visible = rng.random() < (.70 if not hidden else .18)
        fields='key,segment,patient_id' if visible else 'key,segment'
        nodes.append(DataNode('view'+str(i),(parent,),'project',fields,()))
    nodes=full_refresh(nodes)
    reqs=[Requirement('customer','redact','customer',Mode.PERSISTENT,'absent:patient_id'),
          Requirement('external','external-redact','external',Mode.PERSISTENT,'absent:patient_id')]
    # Multiple boundary sinks with irregular positions, unlike a single final sink.
    boundaries=frozenset({nodes[-1].id,nodes[max(3,len(nodes)//2)].id})
    return nodes,reqs,boundaries


def run(args):
    if min(args.train_seeds,args.test_seeds,args.edits,args.repeats,args.bootstrap)<1:
        raise ValueError('positive seed, edit, repeat, bootstrap counts required')
    if not 0 <= args.min_gain < 1:raise ValueError('invalid min gain')
    if set(args.topologies)-{'chain','fanout','layered'}:raise ValueError('invalid topology')
    train_rows=[];test_rows=[]
    for topology in args.topologies:
      for n in args.nodes:
       for count in args.rows:
        for sel in args.selectivities:
         for hidden in (False,True):
          cfg=(n,count,sel,topology,hidden)
          for j in range(args.train_seeds):
           for rep in range(args.repeats):
            train_rows.extend(collect(cfg,args.seed_offset+j,args.edits,repeat=rep,order_seed=args.order_seed))
    policies={'adaptive_'+v:(train(train_rows,v,args.min_gain),v) for v in FEATURES}
    for topology in args.topologies:
      for n in args.nodes:
       for count in args.rows:
        for sel in args.selectivities:
         for hidden in (False,True):
          cfg=(n,count,sel,topology,hidden)
          for j in range(args.test_seeds):
           for rep in range(args.repeats):
            rr=collect(cfg,args.seed_offset+args.train_seeds+j,args.edits,policies,repeat=rep,order_seed=args.order_seed,builder=irregular_instance)
            for r in rr:r['heldout_fold']='irregular_generator';r['generator']='irregular'
            test_rows.extend(rr)
    for r in train_rows:r['heldout_fold']='legacy_generator';r['generator']='legacy'
    for path,records in ((args.training_out,train_rows),(args.out,test_rows)):
        p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('w',newline='',encoding='utf8') as f:
            w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
    names=list(policies)
    overall=summarize(test_rows,names,args.bootstrap,args.order_seed)
    by_topology={}
    for t in args.topologies:
        subset=[r for r in test_rows if r['topology']==t]
        by_topology[t]=summarize(subset,names,args.bootstrap,args.order_seed)
    result=dict(protocol='train regular synthetic generator, test disjoint irregular generator',
        train_edits=len(train_rows),test_edits=len(test_rows),all_equal=all(r['equal'] for r in test_rows),
        train_seed_range=[args.seed_offset,args.seed_offset+args.train_seeds-1],
        test_seed_range=[args.seed_offset+args.train_seeds,args.seed_offset+args.train_seeds+args.test_seeds-1],
        feature_sets={k:list(v) for k,v in FEATURES.items()},min_gain=args.min_gain,
        generator_changes=['irregular DAG parent selection','skewed segment frequencies',
            'mixed local visibility in projections','two source-origin persistent requirements','two publication boundaries'],
        by_topology=by_topology,**overall,
        caveats=['Both generators share the same operators and Python in-memory engine',
          'The held-out generator is a synthetic stress test, not an external real-world dataset',
          'Generator label biases structure and is known at train and test time; policies do not use topology',
          'Paired counterfactual is not a measured executable policy',
          'Bootstrap resamples seed-repeat trajectories; repeated seeds are correlated',
          'Initialization and feature-extraction costs are not included'])
    p=Path(args.summary);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps({k:result[k] for k in ('train_edits','test_edits','all_equal','actual_gains_pct','paired_gains_pct','sync_choices')},indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--nodes',nargs='+',type=int,default=[20,100]);p.add_argument('--rows',nargs='+',type=int,default=[20,100]);p.add_argument('--selectivities',nargs='+',type=float,default=[0,.5,1]);p.add_argument('--topologies',nargs='+',default=['chain','fanout','layered'])
    p.add_argument('--train-seeds',type=int,default=10);p.add_argument('--test-seeds',type=int,default=10);p.add_argument('--seed-offset',type=int,default=5000);p.add_argument('--edits',type=int,default=8);p.add_argument('--repeats',type=int,default=3);p.add_argument('--order-seed',type=int,default=30);p.add_argument('--min-gain',type=float,default=.03);p.add_argument('--bootstrap',type=int,default=1000)
    p.add_argument('--training-out',default='benchmark_v30_training.csv');p.add_argument('--out',default='benchmark_v30.csv');p.add_argument('--summary',default='benchmark_v30_summary.json')
    run(p.parse_args())
