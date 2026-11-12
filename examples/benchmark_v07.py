"""Differential benchmark: identical changes and meshes, FULL / DESC / IGV.
Run: python examples/benchmark_v07.py --nodes 1000 --changes 40 --seeds 3
"""
import argparse, csv, json, random, statistics, time
from dataclasses import replace
from datamesh_formal.certified import Mesh, Node, Policy, Obligation, Rule, Certificate

def build(n, seed, topology):
    rng=random.Random(seed)
    o=Obligation('sales','no-raw-export')
    pol=Policy('sales',1,(Rule(o,'aggregate','aggregated'),))
    nodes=[Node('n0',introduced=frozenset({o}))]
    for i in range(1,n):
        if topology=='chain': parents=(f'n{i-1}',)
        elif topology=='tree': parents=(f'n{(i-1)//2}',)
        elif topology=='layered':
            parents=tuple(f'n{j}' for j in sorted(rng.sample(range(max(0,i-30),i),min(i,2))))
        else: parents=tuple(f'n{j}' for j in sorted(rng.sample(range(i),min(i,2))))
        kind='aggregate' if i%17==0 else 'copy'
        cert=(Certificate(o,'sales',1,'aggregate','aggregated'),) if kind=='aggregate' else ()
        facts=frozenset({'aggregated'}) if cert else frozenset()
        nodes.append(Node(f'n{i}',parents,kind,facts=facts,certificates=cert))
    return nodes,[pol],o

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--nodes',type=int,default=1000)
    ap.add_argument('--changes',type=int,default=40)
    ap.add_argument('--seeds',type=int,default=3)
    ap.add_argument('--topology',choices=['dag','chain','tree','layered'],default='dag')
    ap.add_argument('--out',default='benchmark_v07.csv')
    args=ap.parse_args()
    rows=[]
    for seed in range(args.seeds):
        nodes,policies,o=build(args.nodes,seed,args.topology)
        meshes={s:Mesh(nodes,policies) for s in ('full','desc','igv')}
        for m in meshes.values():m.initialize()
        rng=random.Random(seed+9000)
        for step in range(args.changes):
            i=rng.randrange(args.nodes)
            base=meshes['full'].nodes[f'n{i}']
            # Every 7th change is a policy revision, otherwise edit a node.
            if step%7==0:
                p=meshes['full'].policies['sales']
                changes={}; policy_changes={'sales':replace(p,version=p.version+1)}
                change_type='policy'
            else:
                # Toggle an obligation to guarantee meaningful changes in many trials.
                new_ob=frozenset() if o in base.introduced else frozenset({o})
                changes={base.id:replace(base,introduced=new_ob)}
                policy_changes={}; change_type='node'
            outputs={}
            for strategy,m in meshes.items():
                start=time.perf_counter_ns()
                stats=m.update(node_changes=changes,policy_changes=policy_changes,strategy=strategy)
                elapsed=time.perf_counter_ns()-start
                outputs[strategy]=(stats,elapsed)
            assert meshes['full'].cache==meshes['desc'].cache==meshes['igv'].cache, (seed,step)
            for strategy,(stats,elapsed) in outputs.items():
                rows.append(dict(seed=seed,step=step,nodes=args.nodes,topology=args.topology,
                    change_type=change_type,strategy=strategy,elapsed_ns=elapsed,
                    recomputed=stats.recomputed,visited=stats.visited,stopped=stats.stopped,equal_full=True))
    with open(args.out,'w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    summary={}
    for s in ('full','desc','igv'):
        x=[r for r in rows if r['strategy']==s]
        summary[s]={'mean_ms':round(statistics.mean(r['elapsed_ns'] for r in x)/1e6,5),
                    'median_ms':round(statistics.median(r['elapsed_ns'] for r in x)/1e6,5),
                    'mean_recomputed':round(statistics.mean(r['recomputed'] for r in x),2)}
    print(json.dumps({'settings':vars(args),'samples_per_strategy':args.seeds*args.changes,
                      'summary':summary,'all_equal':True},indent=2))

if __name__=='__main__':main()
