"""Controlled, paired, finite-record microbenchmark; NOT an ICWS implementation.

Wall-time measures include Python allocations and scheduling; separate phases for FULL,
with incremental operator/governance timing instrumented by MaterializedMesh.
"""
import argparse,csv,json,random,statistics,time,math
from dataclasses import replace
from pathlib import Path
from datamesh_formal.evidence import DataNode,row
from datamesh_formal.lifecycle import Mode,Requirement,recompute
from datamesh_formal.materialized import MaterializedMesh,full_refresh


def build(n,rows,selectivity,hidden=False):
    if n<4 or rows<2 or not 0<=selectivity<=1:raise ValueError('invalid parameters')
    left=DataNode('customer',records=tuple(row(key=str(i),segment='A') for i in range(rows)))
    match=round(rows*selectivity)
    right=DataNode('external',records=tuple(row(key=str(i),patient_id='P'+str(i)) for i in range(match)))
    join=DataNode('merged',('customer','external'),'join','key',())
    nodes=[left,right,join]
    for i in range(n-3):
        fields='key,segment' if hidden else 'key,segment,patient_id'
        nodes.append(DataNode('view'+str(i),(nodes[-1].id,),'project',fields,()))
    nodes=full_refresh(nodes)
    requirement=Requirement('customer','redact','customer',Mode.PERSISTENT,'absent:patient_id')
    return nodes,[requirement],frozenset({nodes[-1].id})


def bootstrap_ci(pairs,seed=42,iterations=1000):
    if len(pairs)<2:return None
    rng=random.Random(seed)
    ratios=[]
    for _ in range(iterations):
        s=[pairs[rng.randrange(len(pairs))] for _ in pairs]
        a=sum(x[0] for x in s);b=sum(x[1] for x in s)
        if b>0:ratios.append(a/b)
    ratios.sort()
    return [ratios[int(.025*(len(ratios)-1))],ratios[int(.975*(len(ratios)-1))]] if ratios else None


def run(args):
    records=[];initializations=[]
    for n in args.nodes:
      for count in args.rows:
       for sel in args.selectivities:
        for hidden in (False,True):
         for seed in range(args.seeds):
          base,reqs,boundaries=build(n,count,sel,hidden)
          t=time.perf_counter_ns();mesh=MaterializedMesh(base,reqs,boundaries=boundaries)
          init=(time.perf_counter_ns()-t)/1e6
          initializations.append(dict(nodes=n,rows=count,selectivity=sel,hidden=hidden,seed=seed,initialization_ms=init))
          source=next(x for x in base if x.id=='external')
          original=source.records
          for edit in range(args.edits):
           # Toggle one matching row, so the magnitude of the edit stays constant.
           # If no matching rows exist, edit an unmatched source row (no join change).
           current=list(mesh.nodes['external'].records)
           target=0 if current else None
           if target is not None:
            d=dict(current[target]);d['patient_id']='X' if d['patient_id']!='X' else 'P0'
            current[target]=row(**d)
           else:
            current=[row(key='unmatched',patient_id=('X' if edit%2 else 'Y'))]
           patch=replace(mesh.nodes['external'],records=tuple(current))
           # Alternate order of FULL and IGV, preserving paired identical input states.
           def full():
            t=time.perf_counter_ns(); refreshed=full_refresh(list(mesh.nodes.values()),sources=[patch]);material=(time.perf_counter_ns()-t)/1e6
            t=time.perf_counter_ns();decision=recompute(refreshed,reqs,(),boundaries);verify=(time.perf_counter_ns()-t)/1e6
            return refreshed,decision,material,verify
           if (edit+seed)%2:
            fresh,decision,fmat,fverify=full()
            t=time.perf_counter_ns();stats=mesh.update(sources=[patch]);igv=(time.perf_counter_ns()-t)/1e6
           else:
            # Snapshot prior state for the full evaluator before IGV mutation.
            prior=list(mesh.nodes.values())
            t=time.perf_counter_ns();stats=mesh.update(sources=[patch]);igv=(time.perf_counter_ns()-t)/1e6
            t=time.perf_counter_ns();fresh=full_refresh(prior,sources=[patch]);fmat=(time.perf_counter_ns()-t)/1e6
            t=time.perf_counter_ns();decision=recompute(fresh,reqs,(),boundaries);fverify=(time.perf_counter_ns()-t)/1e6
           equal=(mesh.decisions()==decision and {x.id:x.records for x in fresh}=={k:v.records for k,v in mesh.nodes.items()})
           if not equal:raise AssertionError((n,count,sel,hidden,seed,edit))
           records.append(dict(nodes=n,rows=count,selectivity=sel,hidden=hidden,seed=seed,edit=edit,full_materialize_ms=fmat,full_verify_ms=fverify,full_total_ms=fmat+fverify,igv_total_ms=igv,igv_operators=stats['operators_evaluated'],igv_governance=stats['recomputed'],equal=equal))
    groups=[]
    for key in sorted(set((r['nodes'],r['rows'],r['selectivity'],r['hidden']) for r in records)):
        samples=[r for r in records if (r['nodes'],r['rows'],r['selectivity'],r['hidden'])==key]
        pairs=[(r['full_total_ms'],r['igv_total_ms']) for r in samples]
        speedup=sum(a for a,b in pairs)/sum(b for a,b in pairs)
        groups.append(dict(nodes=key[0],rows=key[1],selectivity=key[2],hidden=key[3],samples=len(samples),speedup_ratio_of_sums=speedup,bootstrap_95_ci=bootstrap_ci(pairs),full_mean_ms=statistics.mean(a for a,b in pairs),igv_mean_ms=statistics.mean(b for a,b in pairs),mean_operators=statistics.mean(r['igv_operators'] for r in samples),mean_governance=statistics.mean(r['igv_governance'] for r in samples)))
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
    with open(args.summary,'w',encoding='utf8') as f:json.dump(dict(settings=vars(args),all_equal=all(r['equal'] for r in records),paired_edits=len(records),groups=groups,initializations=initializations,limitations=['Finite synthetic rows; no I/O, network, distributed coordination','No ICWS 2024 baseline; FULL is this project reference evaluator','Wall-clock microbenchmark; Python GC/OS noise; bootstrap resamples correlated edits and is descriptive, not population inference','Fixed DAG and policy; trusted source changes; one-row mutation','Join operator is nested-loop, so scaling reflects prototype implementation','IGV phase split not instrumented; only full phase split','Zero-selectivity updates may not propagate beyond external source']),f,indent=2)
    print(f'Validated {len(records)} paired edits; all equal; {len(groups)} groups')
    for g in groups:print(f"n={g['nodes']} rows={g['rows']} sel={g['selectivity']} hidden={g['hidden']} speedup={g['speedup_ratio_of_sums']:.2f}x")
    return records,groups

if __name__=='__main__':
 p=argparse.ArgumentParser()
 p.add_argument('--nodes',type=int,nargs='+',default=[20,100])
 p.add_argument('--rows',type=int,nargs='+',default=[20,100])
 p.add_argument('--selectivities',type=float,nargs='+',default=[0.0,0.5,1.0])
 p.add_argument('--seeds',type=int,default=5);p.add_argument('--edits',type=int,default=6)
 p.add_argument('--out',default='benchmark_v21.csv');p.add_argument('--summary',default='benchmark_v21_summary.json')
 a=p.parse_args();run(a)
