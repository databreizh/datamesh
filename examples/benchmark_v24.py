"""V0.24 held-out cost-policy evaluation.

Training and evaluation use DISJOINT random instance seeds. The trained policy
uses only workload metadata and training timings, never evaluation timings.
The adaptive FULL path includes cache reconstruction. Oracle is hindsight only.
"""
import argparse, csv, json, math, statistics, time
from dataclasses import replace
from pathlib import Path
from datamesh_formal.materialized import MaterializedMesh, full_refresh
from datamesh_formal.lifecycle import recompute
from benchmark_v23 import build_instance, mutate
from benchmark_v22 import cluster_ci

METHODS=('full','igv','descendants','adaptive')

def key_of(r):
    return (r['nodes'],r['rows'],r['selectivity'],r['topology'],r['hidden'])

def train_policy(rows, initializations, margin=0.05):
    """Learn a per-workload empirical cost table, penalizing FULL by cache rebuild.

    Uses only training observations. A fixed positive margin avoids unstable
    switches when predicted times are close. This is a lookup policy, not ML.
    """
    init={ (key_of(r),r['seed']):r['adaptive_initialization_ms'] for r in initializations }
    by={}
    for r in rows:
        k=key_of(r)
        by.setdefault(k,[]).append((float(r['igv_total_ms']),
                                   float(r['full_total_ms'])+init[(k,r['seed'])]))
    policy={}
    for k,obs in by.items():
        igv=statistics.median(x for x,y in obs)
        full_rebuild=statistics.median(y for x,y in obs)
        policy[k]=dict(choice='full' if full_rebuild < igv*(1-margin) else 'igv',
                       predicted_igv_ms=igv,predicted_full_rebuild_ms=full_rebuild,
                       training_edits=len(obs))
    return policy

def evaluate(args, policy):
    rows=[]; initial=[]
    for n in args.nodes:
      for count in args.rows:
       for sel in args.selectivities:
        for topology in args.topologies:
         for hidden in (False,True):
          k=(n,count,sel,topology,hidden)
          for j in range(args.test_seeds):
            seed=args.seed_offset+args.train_seeds+j
            base,reqs,boundaries=build_instance(n,count,sel,hidden,topology,seed)
            meshes={}; init={}
            for method in ('igv','descendants','adaptive'):
                t=time.perf_counter_ns(); meshes[method]=MaterializedMesh(base,reqs,boundaries=boundaries)
                init[method]=(time.perf_counter_ns()-t)/1e6
            initial.append(dict(nodes=n,rows=count,selectivity=sel,topology=topology,hidden=hidden,seed=seed,**{m+'_initialization_ms':v for m,v in init.items()}))
            for edit in range(args.edits):
                patch=mutate(meshes['igv'],seed,edit)
                prior=list(meshes['igv'].nodes.values())
                choice=policy[k]['choice']; times={}; stats={}
                order=list(METHODS); shift=(seed+edit)%4; order=order[shift:]+order[:shift]
                for method in order:
                    if method=='full':
                        t=time.perf_counter_ns(); fresh=full_refresh(prior,sources=[patch]); mid=time.perf_counter_ns()
                        decision=recompute(fresh,reqs,(),boundaries); end=time.perf_counter_ns()
                        times['full']=(end-t)/1e6
                        stats['full_materialize_ms']=(mid-t)/1e6
                        stats['full_governance_ms']=(end-mid)/1e6
                        # FULL plus cache reconstruction is the fair alternative
                        # for a switchable incremental engine; measure separately.
                        t=time.perf_counter_ns(); rebuilt=MaterializedMesh(fresh,reqs,boundaries=boundaries)
                        stats['full_cache_rebuild_ms']=(time.perf_counter_ns()-t)/1e6
                        if rebuilt.decisions()!=decision: raise AssertionError('rebuilt cache disagreement')
                    elif method=='adaptive' and choice=='full':
                        t=time.perf_counter_ns(); refreshed=full_refresh(prior,sources=[patch]); rebuilt=MaterializedMesh(refreshed,reqs,boundaries=boundaries)
                        times['adaptive']=(time.perf_counter_ns()-t)/1e6
                        meshes['adaptive']=rebuilt
                    else:
                        t=time.perf_counter_ns(); s=meshes[method].update(sources=[patch],strategy='descendants' if method=='descendants' else 'igv')
                        times[method]=(time.perf_counter_ns()-t)/1e6
                        stats[method+'_governance_nodes']=s['recomputed']
                        stats[method+'_operators']=s['operators_evaluated']
                target={x.id:x.records for x in fresh}
                for method,mesh in meshes.items():
                    if mesh.decisions()!=decision or {x:v.records for x,v in mesh.nodes.items()}!=target:
                        raise AssertionError((k,seed,edit,method))
                full_switch=times['full']+stats['full_cache_rebuild_ms']
                oracle_choice='full' if full_switch < times['igv'] else 'igv'
                rows.append(dict(nodes=n,rows=count,selectivity=sel,topology=topology,hidden=hidden,
                    seed=seed,edit=edit,instance_id=f'{n}:{count}:{sel}:{topology}:{int(hidden)}:{seed}',
                    adaptive_choice=choice,oracle_choice=oracle_choice,
                    full_total_ms=times['full'],full_switch_total_ms=full_switch,
                    igv_total_ms=times['igv'],descendants_total_ms=times['descendants'],
                    adaptive_total_ms=times['adaptive'],oracle_hindsight_ms=min(full_switch,times['igv']),
                    full_cache_rebuild_ms=stats['full_cache_rebuild_ms'],
                    igv_governance_nodes=stats['igv_governance_nodes'],igv_operators=stats['igv_operators'],equal=True))
    return rows,initial

def run(args):
    if args.train_seeds<2 or args.test_seeds<2 or args.edits<1 or args.bootstrap<1 or not 0<=args.margin<1:
        raise ValueError('need >=2 disjoint train/test seeds, >=1 edit/bootstrap, margin in [0,1)')
    # Collect training data through V0.23's exact paired benchmark. Its old
    # adaptive heuristic is irrelevant; only FULL, IGV and initialization used.
    from benchmark_v23 import run as train_run
    from argparse import Namespace
    training_csv=Path(args.out).with_name(Path(args.out).stem+'_training.csv')
    training_json=Path(args.out).with_name(Path(args.out).stem+'_training_summary.json')
    train_args=Namespace(nodes=args.nodes,rows=args.rows,selectivities=args.selectivities,
        topologies=args.topologies,seeds=args.train_seeds,edits=args.edits,bootstrap=args.bootstrap,
        threshold=.8,out=str(training_csv),summary=str(training_json))
    training,groups=train_run(train_args)
    with open(training_json,encoding='utf8') as f: training_info=json.load(f)
    policy=train_policy(training,training_info['initializations'],args.margin)
    evaluation,initial=evaluate(args,policy)
    Path(args.out).parent.mkdir(parents=True,exist_ok=True)
    with open(args.out,'w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=list(evaluation[0]));w.writeheader();w.writerows(evaluation)
    summaries=[]
    for k in sorted(policy):
        rr=[r for r in evaluation if key_of(r)==k]
        clusters={s:[(r['full_total_ms'],r['igv_total_ms']) for r in rr if r['seed']==s] for s in sorted({r['seed'] for r in rr})}
        ci=cluster_ci(clusters,args.bootstrap,rng_seed=2401)
        sums=lambda col:sum(r[col] for r in rr)
        summaries.append(dict(nodes=k[0],rows=k[1],selectivity=k[2],topology=k[3],hidden=k[4],
            test_edits=len(rr),test_instances=args.test_seeds,trained_choice=policy[k]['choice'],
            full_over_igv=sums('full_total_ms')/sums('igv_total_ms'),full_over_igv_95_ci=ci,
            full_over_adaptive=sums('full_total_ms')/sums('adaptive_total_ms'),
            adaptive_over_igv=sums('adaptive_total_ms')/sums('igv_total_ms'),
            adaptive_oracle_agreement=sum(r['adaptive_choice']==r['oracle_choice'] for r in rr)/len(rr),
            oracle_hindsight_over_adaptive=sums('adaptive_total_ms')/sums('oracle_hindsight_ms')))
    sums=lambda col:sum(r[col] for r in evaluation)
    report=dict(settings=vars(args),training_instances=len({r['instance_id'] for r in training}),
        test_instances=len({r['instance_id'] for r in evaluation}),training_edits=len(training),
        test_edits=len(evaluation),all_equal=all(r['equal'] for r in evaluation),
        aggregate=dict(full_over_igv=sums('full_total_ms')/sums('igv_total_ms'),
                       full_over_adaptive=sums('full_total_ms')/sums('adaptive_total_ms'),
                       adaptive_over_igv=sums('adaptive_total_ms')/sums('igv_total_ms'),
                       adaptive_oracle_agreement=sum(r['adaptive_choice']==r['oracle_choice'] for r in evaluation)/len(evaluation),
                       oracle_hindsight_over_adaptive=sums('adaptive_total_ms')/sums('oracle_hindsight_ms')),
        policy=[dict(nodes=k[0],rows=k[1],selectivity=k[2],topology=k[3],hidden=k[4],**v) for k,v in sorted(policy.items())],
        groups=summaries,test_initializations=initial,
        limitations=['Cost policy is a per-configuration lookup trained on measured timings, not a predictive parametric model',
                     'Training and test have disjoint random seeds but fixed graph shapes and same generator',
                     'FULL baseline excludes cache rebuild; switchable FULL includes cache rebuild and is compared separately',
                     'Oracle is a retrospective lower bound from paired measured times, not an implementable strategy',
                     'Python in-memory timings, no I/O/distributed systems, no external competing implementation',
                     'Per-edit hindsight oracle is optimistically biased by measurement noise',
                     'CIs are test-seed cluster bootstrap conditional on the generator'])
    with open(args.summary,'w',encoding='utf8') as f:json.dump(report,f,indent=2)
    print(f'V0.24: {len(training)} training edits, {len(evaluation)} held-out edits; all methods equivalent')
    print('Held-out aggregate:',report['aggregate'])
    return evaluation,report

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--nodes',type=int,nargs='+',default=[20,100]);p.add_argument('--rows',type=int,nargs='+',default=[20,100])
    p.add_argument('--selectivities',type=float,nargs='+',default=[0,.5,1]);p.add_argument('--topologies',nargs='+',choices=['chain','fanout','layered'],default=['chain','fanout','layered'])
    p.add_argument('--train-seeds',type=int,default=10);p.add_argument('--test-seeds',type=int,default=10)
    p.add_argument('--seed-offset',type=int,default=100000);p.add_argument('--edits',type=int,default=8)
    p.add_argument('--bootstrap',type=int,default=2000);p.add_argument('--margin',type=float,default=.05)
    p.add_argument('--out',default='benchmark_v24.csv');p.add_argument('--summary',default='benchmark_v24_summary.json')
    run(p.parse_args())
