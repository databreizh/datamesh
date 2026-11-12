"""V0.31: operational feature analysis and conservative OOD fallback.

Train on regular generator; test on irregular generator. Gate is trained ONLY
on regular graph signatures; no irregular timing information is used.
"""
import argparse, csv, json, random, statistics, time
from collections import defaultdict
from pathlib import Path
from benchmark_v23 import build_instance, mutate
from benchmark_v30 import irregular_instance
from datamesh_formal.materialized import MaterializedMesh, full_refresh
from datamesh_formal.lifecycle import recompute


def graph_features(nodes, requirements, boundaries, edited_source='external'):
    """Static pre-edit graph measurements, never post-edit oracle statistics."""
    byid={x.id:x for x in nodes}
    children=defaultdict(list)
    for x in nodes:
        for parent in x.inputs:children[parent].append(x.id)
    reachable={edited_source};stack=[edited_source]
    while stack:
        for child in children[stack.pop()]:
            if child not in reachable:reachable.add(child);stack.append(child)
    derived=[x for x in nodes if x.operation!='source']
    project=[x for x in nodes if x.operation=='project']
    visible=sum('patient_id' in x.parameter.split(',') for x in project)
    degrees=[len(children[x.id]) for x in nodes]
    rowvol=sum(len(x.records) for x in nodes)
    touched=sum(len(byid[k].records) for k in reachable)
    # Signature is invariant to graph size but discriminates regular vs irregular DAGs.
    # No performance observations from held-out generator enter the gate.
    parent_hist=tuple(sorted((len(children[x.id]) for x in nodes if x.operation!='source'),reverse=True))
    return dict(reachable_ratio=round(len(reachable)/len(nodes),2),
       visible_ratio=round(visible/max(1,len(project)),2),
       volume_ratio=round(touched/max(1,rowvol),2),
       branching_ratio=round(sum(d>1 for d in degrees)/len(nodes),2),
       max_fanout=max(degrees), boundary_count=len(boundaries),
       requirement_count=len(requirements),
       mixed_visibility=bool(project and 0<visible<len(project)),
       parent_hist=parent_hist)


def signature(f):
    return (f['boundary_count'],f['requirement_count'],f['mixed_visibility'],
            f['parent_hist'])


def bucket(f):
    return (round(f['reachable_ratio']*4)/4, round(f['visible_ratio']*4)/4,
            round(f['volume_ratio']*4)/4, round(f['branching_ratio']*4)/4)


def fit(records, margin=.03, min_support=6):
    aggregates=defaultdict(lambda:[0.,0.,0]);signatures=set()
    for r in records:
        signatures.add(r['signature'])
        a=aggregates[r['bucket']];a[0]+=r['igv_ms'];a[1]+=r['sync_ms'];a[2]+=1
    choices={k:('sync' if n>=min_support and sync < igv*(1-margin) else 'igv')
             for k,(igv,sync,n) in aggregates.items()}
    return choices,signatures


def decision(f,model,gate=True):
    choices,known=model
    if gate and signature(f) not in known:return 'igv','ood'
    b=bucket(f)
    if b not in choices:return 'igv','unsupported_bucket'
    return choices[b], 'trained'


def run_case(cfg,seed,edits,repeats,model=None,irregular=False,order_seed=31):
    n,count,sel,topology,hidden=cfg
    builder=irregular_instance if irregular else build_instance
    base,reqs,boundaries=builder(n,count,sel,hidden,topology,seed)
    f=graph_features(base,reqs,boundaries)
    # Two policies: operational selector without OOD gate, and gated selector.
    policies={} if model is None else {'operational':False,'safe':True}
    out=[]
    for rep in range(repeats):
        methods=['igv','sync',*policies]
        engines={m:MaterializedMesh(base,reqs,boundaries=boundaries) for m in methods}
        for edit in range(edits):
            patch=mutate(engines['igv'],seed,edit)
            previous=list(engines['igv'].nodes.values())
            r=dict(nodes=n,rows=count,selectivity=sel,topology=topology,hidden=hidden,
                   seed=seed,repeat=rep,edit=edit,generator='irregular' if irregular else 'regular',
                   signature=signature(f),bucket=bucket(f),**{k:v for k,v in f.items() if k!='parent_hist'})
            order=methods[:]
            random.Random(order_seed+seed*1009+edit*13+rep*101).shuffle(order)
            for m in order:
                if m in policies:
                    choice,reason=decision(f,model,policies[m]);r[m+'_choice']=choice;r[m+'_reason']=reason
                else:choice=m
                start=time.perf_counter_ns()
                if choice=='sync':stats=engines[m].full_sync(sources=[patch])
                else:stats=engines[m].update(sources=[patch])
                r[m+'_ms']=(time.perf_counter_ns()-start)/1e6
                r[m+'_operators']=stats.get('operators_evaluated',0)
                r[m+'_governance_recomputed']=stats.get('recomputed',0)
            expected=full_refresh(previous,sources=[patch]);oracle=recompute(expected,reqs,(),boundaries)
            target={x.id:x.records for x in expected}
            for m,e in engines.items():
                if e.decisions()!=oracle or {k:v.records for k,v in e.nodes.items()}!=target:
                    raise AssertionError((cfg,seed,edit,m))
            for m in policies:r[m+'_paired_ms']=r[r[m+'_choice']+'_ms']
            r['equal']=True;out.append(r)
    return out


def summarize(rows):
    total={m:sum(r[m+'_ms'] for r in rows) for m in ('igv','sync','operational','safe')}
    paired={m:sum(r[m+'_paired_ms'] for r in rows) for m in ('operational','safe')}
    return dict(count=len(rows),all_equal=all(r['equal'] for r in rows),
      totals_ms=total,paired_totals_ms=paired,
      actual_gain_pct={m:100*(1-total[m]/total['igv']) for m in ('operational','safe')},
      paired_gain_pct={m:100*(1-paired[m]/total['igv']) for m in ('operational','safe')},
      choices={m:{'sync':sum(r[m+'_choice']=='sync' for r in rows),
                   'ood':sum(r[m+'_reason']=='ood' for r in rows)} for m in ('operational','safe')})


def run(args):
    if min(args.train_seeds,args.test_seeds,args.edits,args.repeats) < 1:
        raise ValueError('counts must be positive')
    train=[];test=[]
    configs=[(n,rows,sel,t,hidden) for n in args.nodes for rows in args.rows
             for sel in args.selectivities for t in args.topologies for hidden in (False,True)]
    for cfg in configs:
        for j in range(args.train_seeds):
            train+=run_case(cfg,args.seed_offset+j,args.edits,args.repeats)
    model=fit(train,args.min_gain,args.min_support)
    for cfg in configs:
        for j in range(args.test_seeds):
            test+=run_case(cfg,args.seed_offset+args.train_seeds+j,args.edits,args.repeats,model,True,args.order_seed)
    for name,rows in ((args.training_out,train),(args.out,test)):
        p=Path(name);p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('w',newline='',encoding='utf8') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader()
            for r in rows:
                x=dict(r);x['signature']=repr(x['signature']);x['bucket']=repr(x['bucket']);writer.writerow(x)
    result=dict(protocol='regular-train irregular-test operational features and OOD safety',
       train_edits=len(train),test_edits=len(test),train_signatures=len(model[1]),
       learned_buckets=len(model[0]),sync_buckets=sum(x=='sync' for x in model[0].values()),
       by_topology={t:summarize([r for r in test if r['topology']==t]) for t in args.topologies},
       **summarize(test),
       caveats=['OOD signature uses graph shape and governance configuration before any edit',
        'Strict exact signature can abstain on most irregular graphs; safety is not guaranteed on unseen matching signatures',
        'Fallback does not establish a universal runtime performance guarantee',
        'Independent execution times are noisy; paired counterfactual is an estimator',
        'Features and initialization are excluded from timed update; include them in deployment costs',
        'All data are synthetic and all operators use the same Python engine'])
    Path(args.summary).write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result,indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--nodes',nargs='+',type=int,default=[20,100]);p.add_argument('--rows',nargs='+',type=int,default=[20,100])
    p.add_argument('--selectivities',nargs='+',type=float,default=[0,.5,1]);p.add_argument('--topologies',nargs='+',default=['chain','fanout','layered'])
    p.add_argument('--train-seeds',type=int,default=10);p.add_argument('--test-seeds',type=int,default=10)
    p.add_argument('--seed-offset',type=int,default=6000);p.add_argument('--edits',type=int,default=8)
    p.add_argument('--repeats',type=int,default=3);p.add_argument('--order-seed',type=int,default=31)
    p.add_argument('--min-gain',type=float,default=.03);p.add_argument('--min-support',type=int,default=6)
    p.add_argument('--training-out',default='benchmark_v31_training.csv');p.add_argument('--out',default='benchmark_v31.csv')
    p.add_argument('--summary',default='benchmark_v31_summary.json');run(p.parse_args())
