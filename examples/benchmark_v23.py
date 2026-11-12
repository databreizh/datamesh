"""V0.23: independently randomized instance families, paired methods, seed-cluster CIs.

Adaptive is an explicit conservative structural heuristic, not a learned or optimal policy.
The FULL branch recomputes and reinstantiates IGV so later edits retain a valid cache.
"""
import argparse, csv, json, math, random, statistics, time
from dataclasses import replace
from pathlib import Path
from datamesh_formal.evidence import DataNode, row
from datamesh_formal.lifecycle import Mode, Requirement, recompute
from datamesh_formal.materialized import MaterializedMesh, full_refresh
from benchmark_v22 import cluster_ci


def build_instance(n, count, selectivity, hidden, topology, seed):
    if n < 5 or count < 2 or not 0 <= selectivity <= 1 or topology not in ('chain','fanout','layered'):
        raise ValueError('invalid benchmark parameters')
    rng=random.Random(seed)
    keys=rng.sample(range(max(10000, count*100)),count)
    left=DataNode('customer',records=tuple(row(key=str(k),segment=rng.choice('ABC')) for k in keys))
    matched=rng.sample(keys,round(count*selectivity))
    right=DataNode('external',records=tuple(row(key=str(k),patient_id='P'+str(rng.randrange(1000000))) for k in matched))
    nodes=[left,right,DataNode('merged',('customer','external'),'join','key',())]
    for i in range(n-3):
        if topology=='chain': parent=nodes[-1].id
        elif topology=='fanout': parent='merged'
        else: parent='merged' if i<2 else nodes[3+(i-2)//2].id
        fields='key,segment' if hidden else 'key,segment,patient_id'
        nodes.append(DataNode('view'+str(i),(parent,),'project',fields,()))
    nodes=full_refresh(nodes)
    req=[Requirement('customer','redact','customer',Mode.PERSISTENT,'absent:patient_id')]
    return nodes,req,frozenset({nodes[-1].id})


def mutate(mesh, seed, edit):
    current=list(mesh.nodes['external'].records)
    if current:
        index=random.Random(seed*1000003+edit).randrange(len(current))
        d=dict(current[index]);d['patient_id']='E'+str(seed)+'_'+str(edit)
        current[index]=row(**d)
    else:
        current=[row(key='unmatched_'+str(seed),patient_id='E'+str(edit))]
    return replace(mesh.nodes['external'],records=tuple(current))


def structural_choice(n, topology, selectivity, hidden, threshold):
    # Deliberately uses only public workload metadata, never measured timings.
    # In a chain, full selectivity + visible edits imply near-global invalidation.
    risk=(topology=='chain' and not hidden and selectivity >= threshold)
    return 'full' if risk else 'igv'


def run(args):
    if args.seeds<2 or args.edits<1 or args.bootstrap<1 or not 0<=args.threshold<=1:
        raise ValueError('invalid repetitions or threshold')
    output=[]; initial=[]
    for n in args.nodes:
      for count in args.rows:
       for sel in args.selectivities:
        for topology in args.topologies:
         for hidden in (False,True):
          for seed in range(args.seeds):
            base,reqs,boundaries=build_instance(n,count,sel,hidden,topology,seed)
            meshes={}; init={}
            for method in ('igv','descendants','adaptive'):
                start=time.perf_counter_ns();meshes[method]=MaterializedMesh(base,reqs,boundaries=boundaries)
                init[method]=(time.perf_counter_ns()-start)/1e6
            initial.append(dict(nodes=n,rows=count,selectivity=sel,topology=topology,hidden=hidden,seed=seed,**{k+'_initialization_ms':v for k,v in init.items()}))
            for edit in range(args.edits):
                patch=mutate(meshes['igv'],seed,edit)
                prior=list(meshes['igv'].nodes.values())
                times={};stats={}
                # Rotate evaluation order across seeds and edits to mitigate drift.
                order=['full','igv','descendants','adaptive']; shift=(seed+edit)%4;order=order[shift:]+order[:shift]
                choice=structural_choice(n,topology,sel,hidden,args.threshold)
                for method in order:
                    if method=='full':
                        t=time.perf_counter_ns();fresh=full_refresh(prior,sources=[patch]);mid=time.perf_counter_ns()
                        decision=recompute(fresh,reqs,(),boundaries);end=time.perf_counter_ns()
                        times['full']=(end-t)/1e6;stats['full_mat']=(mid-t)/1e6;stats['full_gov']=(end-mid)/1e6
                    elif method=='adaptive' and choice=='full':
                        # Time full refresh and rebuilding incremental state, including cache maintenance.
                        t=time.perf_counter_ns();refreshed=full_refresh(prior,sources=[patch]);rebuilt=MaterializedMesh(refreshed,reqs,boundaries=boundaries)
                        times['adaptive']=(time.perf_counter_ns()-t)/1e6
                        meshes['adaptive']=rebuilt
                        stats['adaptive_recomputed']=n
                    else:
                        t=time.perf_counter_ns();s=meshes[method].update(sources=[patch],strategy='descendants' if method=='descendants' else 'igv')
                        times[method]=(time.perf_counter_ns()-t)/1e6
                        stats[method+'_recomputed']=s['recomputed'];stats[method+'_operators']=s['operators_evaluated']
                target={x.id:x.records for x in fresh}
                for method in meshes:
                    if meshes[method].decisions()!=decision or {k:v.records for k,v in meshes[method].nodes.items()}!=target:
                        raise AssertionError((n,count,sel,topology,hidden,seed,edit,method))
                output.append(dict(nodes=n,rows=count,selectivity=sel,topology=topology,hidden=hidden,seed=seed,edit=edit,
                                   instance_id=f'{n}:{count}:{sel}:{topology}:{int(hidden)}:{seed}',adaptive_choice=choice,
                                   full_total_ms=times['full'],igv_total_ms=times['igv'],descendants_total_ms=times['descendants'],adaptive_total_ms=times['adaptive'],
                                   full_materialize_ms=stats['full_mat'],full_governance_ms=stats['full_gov'],
                                   igv_governance_nodes=stats['igv_recomputed'],descendants_governance_nodes=stats['descendants_recomputed'],
                                   igv_operators=stats['igv_operators'],equal=True))
    groups=[]
    keys=sorted({(r['nodes'],r['rows'],r['selectivity'],r['topology'],r['hidden']) for r in output})
    for key in keys:
        rr=[r for r in output if (r['nodes'],r['rows'],r['selectivity'],r['topology'],r['hidden'])==key]
        seeds=sorted({r['seed'] for r in rr})
        def compare(a,b):
            clusters={s:[(r[a+'_total_ms'],r[b+'_total_ms']) for r in rr if r['seed']==s] for s in seeds}
            speed=sum(x for cl in clusters.values() for x,y in cl)/sum(y for cl in clusters.values() for x,y in cl)
            return speed,cluster_ci(clusters,args.bootstrap,rng_seed=2301)
        fi,ci=compare('full','igv');di,dc=compare('descendants','igv');fa,ca=compare('full','adaptive')
        groups.append(dict(nodes=key[0],rows=key[1],selectivity=key[2],topology=key[3],hidden=key[4],samples=len(rr),independent_instances=len(seeds),
                           full_over_igv=fi,full_over_igv_95_ci=ci,descendants_over_igv=di,descendants_over_igv_95_ci=dc,
                           full_over_adaptive=fa,full_over_adaptive_95_ci=ca,
                           mean_igv_governance_nodes=statistics.mean(r['igv_governance_nodes'] for r in rr),
                           adaptive_full_fraction=statistics.mean(r['adaptive_choice']=='full' for r in rr)))
    for path in (args.out,args.summary):Path(path).parent.mkdir(parents=True,exist_ok=True)
    with open(args.out,'w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=list(output[0]));w.writeheader();w.writerows(output)
    summary=dict(settings=vars(args),paired_edits=len(output),all_equal=True,groups=groups,initializations=initial,
        limitations=['Synthetic in-memory DAGs; no I/O or distributed execution',
                     'Random seeds independently vary keys, segments, values and match subsets, but graph shapes are fixed per topology',
                     'Confidence intervals are seed-cluster bootstrap intervals conditional on the synthetic generator, not universal performance guarantees',
                     'Adaptive heuristic uses workload selectivity and topology as known metadata; FULL branch rebuilds IGV cache and is not a pure full-refresh comparator',
                     'FULL does not pay a persistent cache initialization cost; compare amortized costs separately',
                     'Nested-loop join and Python microtimings limit external validity; no ICWS implementation evaluated'])
    with open(args.summary,'w',encoding='utf8') as f:json.dump(summary,f,indent=2)
    print(f'Validated {len(output)} paired edits across {len(groups)} groups; all 4 evaluators agree')
    return output,groups

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--nodes',type=int,nargs='+',default=[20,100]);p.add_argument('--rows',type=int,nargs='+',default=[20,100]);p.add_argument('--selectivities',type=float,nargs='+',default=[0,.5,1])
    p.add_argument('--topologies',nargs='+',choices=['chain','fanout','layered'],default=['chain','fanout','layered'])
    p.add_argument('--seeds',type=int,default=10);p.add_argument('--edits',type=int,default=8);p.add_argument('--bootstrap',type=int,default=2000)
    p.add_argument('--threshold',type=float,default=.8)
    p.add_argument('--out',default='benchmark_v23.csv');p.add_argument('--summary',default='benchmark_v23_summary.json')
    run(p.parse_args())
