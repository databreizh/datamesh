"""V0.26 real timed paired FULL+cache-preserving sync vs IGV vs FULL+rebuild.

All three persistent engines receive the same sequential edits. Each method
has independent state; FULL+rebuild includes constructing a new cache. No
retrospective policy or future-cost prediction is claimed.
"""
import argparse,csv,json,time
from pathlib import Path
from datamesh_formal.materialized import MaterializedMesh,full_refresh
from datamesh_formal.lifecycle import recompute
from benchmark_v23 import build_instance,mutate

def run(args):
    rows=[]
    for n in args.nodes:
      for count in args.rows:
       for sel in args.selectivities:
        for topology in args.topologies:
         for hidden in (False,True):
          for seed in range(args.seeds):
           base,reqs,boundaries=build_instance(n,count,sel,hidden,topology,seed+args.seed_offset)
           meshes={m:MaterializedMesh(base,reqs,boundaries=boundaries) for m in ('igv','sync','rebuild')}
           for edit in range(args.edits):
            patch=mutate(meshes['igv'],seed+args.seed_offset,edit)
            prior=list(meshes['igv'].nodes.values())
            timings={}; stats={}
            methods=['igv','sync','rebuild']; shift=(seed+edit)%3; methods=methods[shift:]+methods[:shift]
            for method in methods:
             t=time.perf_counter_ns()
             if method=='igv': stats[method]=meshes[method].update(sources=[patch])
             elif method=='sync': stats[method]=meshes[method].full_sync(sources=[patch])
             else:
              fresh=full_refresh(list(meshes[method].nodes.values()),sources=[patch])
              meshes[method]=MaterializedMesh(fresh,reqs,boundaries=boundaries)
             timings[method]=(time.perf_counter_ns()-t)/1e6
            expected=full_refresh(prior,sources=[patch]); oracle=recompute(expected,reqs,(),boundaries)
            target={x.id:x.records for x in expected}
            for m,mesh in meshes.items():
             if mesh.decisions()!=oracle or {k:v.records for k,v in mesh.nodes.items()}!=target:
              raise AssertionError((n,count,sel,topology,hidden,seed,edit,m))
            rows.append(dict(nodes=n,rows=count,selectivity=sel,topology=topology,hidden=hidden,
                             seed=seed,edit=edit,igv_ms=timings['igv'],full_sync_ms=timings['sync'],
                             full_rebuild_ms=timings['rebuild'],sync_governance_nodes=stats['sync']['recomputed'],
                             igv_governance_nodes=stats['igv']['recomputed'],equal=True))
    p=Path(args.out);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w',newline='') as f:
     writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    totals={m:sum(r[m] for r in rows) for m in ('igv_ms','full_sync_ms','full_rebuild_ms')}
    result={'paired_edits':len(rows),'all_equal':all(r['equal'] for r in rows),
            'totals_ms':totals,'full_sync_over_igv':totals['full_sync_ms']/totals['igv_ms'],
            'full_rebuild_over_igv':totals['full_rebuild_ms']/totals['igv_ms'],
            'sync_wins':sum(r['full_sync_ms']<r['igv_ms'] for r in rows)}
    Path(args.summary).write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2));return result

if __name__=='__main__':
 p=argparse.ArgumentParser()
 p.add_argument('--nodes',type=int,nargs='+',default=[20,100]);p.add_argument('--rows',type=int,nargs='+',default=[20,100]);p.add_argument('--selectivities',type=float,nargs='+',default=[0,.5,1]);p.add_argument('--topologies',nargs='+',default=['chain','fanout','layered']);p.add_argument('--seeds',type=int,default=10);p.add_argument('--seed-offset',type=int,default=1000);p.add_argument('--edits',type=int,default=8)
 p.add_argument('--out',default='benchmark_v26.csv');p.add_argument('--summary',default='benchmark_v26_summary.json')
 run(p.parse_args())
