"""Synthetic benchmark; reports wall-clock and recomputation counts, no claims of measured speedups in the paper."""
import argparse, random, time, json
from dataclasses import replace
from datamesh_formal.incremental import GovernanceMesh, Node, Policy

p=argparse.ArgumentParser(); p.add_argument('--nodes',type=int,default=1000); p.add_argument('--changes',type=int,default=100); p.add_argument('--seed',type=int,default=42); args=p.parse_args()
rng=random.Random(args.seed)
nodes=[Node('n0','d0',source_obligations=frozenset({'restricted'}))]
for i in range(1,args.nodes):
    parents=tuple(f'n{j}' for j in sorted(rng.sample(range(i), min(i,2))))
    nodes.append(Node(f'n{i}',f'd{i%25}',parents,'copy'))
mesh=GovernanceMesh(nodes,[Policy('policy',1,frozenset())]); mesh.initialize()
rows=[]
for k in range(args.changes):
    i=rng.randrange(args.nodes); n=mesh.nodes[f'n{i}']
    new=replace(n,introduced_obligations=frozenset({f'o{k%7}'}) if k%2 else frozenset())
    t=time.perf_counter(); stats=mesh.update(node_changes={n.id:new}); inc=time.perf_counter()-t
    t=time.perf_counter(); full=mesh.full_verify(); full_t=time.perf_counter()-t
    assert full==mesh.cache
    rows.append({'change':k,'node':n.id,'igv_seconds':inc,'full_seconds':full_t,'igv_recomputed':stats.recomputed,'full_recomputed':args.nodes,'equal':True})
print(json.dumps({'config':vars(args),'results':rows,'summary':{'avg_igv_seconds':sum(x['igv_seconds'] for x in rows)/len(rows),'avg_full_seconds':sum(x['full_seconds'] for x in rows)/len(rows),'avg_igv_recomputed':sum(x['igv_recomputed'] for x in rows)/len(rows)}},indent=2))
