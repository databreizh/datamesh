"""Paired comparison of V0.8 IGV and V0.9 indexed IGV.

Same-version edits model fine-grained rule changes within a policy snapshot.
Version-bump edits model actual V0.8 pinned certificate revocation.
"""
import argparse, csv, json, random, statistics, time
from dataclasses import replace
from pathlib import Path
from datamesh_formal.certified import Mesh, Node, Policy, Obligation, Rule, Certificate
from datamesh_formal.indexed import IndexedMesh


def build(n, n_rules, rate, seed):
    rng=random.Random(seed)
    obligations=[Obligation('issuer',f'o{i}') for i in range(n_rules)]
    pol=Policy('issuer',1,tuple(Rule(o,'aggregate','safe') for o in obligations))
    nodes=[Node('root',introduced=frozenset(obligations))]
    for i in range(1,n):
        parents=(('root' if (j := rng.randrange(max(0,i-20),i)) == 0 else f'n{j}'),)
        # Each certificate references one independent rule.
        certs=()
        if rng.random()<rate:
            o=obligations[rng.randrange(n_rules)]
            certs=(Certificate(o,'issuer',1,'aggregate','safe'),)
        nodes.append(Node(f'n{i}',parents,'aggregate',facts=frozenset({'safe'}),certificates=certs))
    return nodes,pol,obligations


def run(args):
    rows=[]
    for seed in range(args.seeds):
        for scenario in ('rule_edit','version_bump'):
            nodes,pol,obligations=build(args.nodes,args.rules,args.discharge_rate,seed+100)
            old=Mesh(nodes,[pol]);new=IndexedMesh(nodes,[pol]);old.initialize();new.initialize()
            rng=random.Random(seed+500)
            for step in range(args.changes):
                oldpol=old.policies['issuer']
                if scenario=='rule_edit':
                    target=obligations[rng.randrange(len(obligations))]
                    rule=Rule(target,'aggregate','safe')
                    rules=tuple(r for r in oldpol.rules if r!=rule)
                    if len(rules)==len(oldpol.rules): rules+=(rule,)
                    updated=replace(oldpol,rules=rules)
                else:
                    updated=replace(oldpol,version=oldpol.version+1)
                for strategy,mesh in (('igv_v08',old),('indexed_v09',new)):
                    start=time.perf_counter_ns()
                    stats=mesh.update(policy_changes={'issuer':updated},strategy='igv')
                    elapsed=time.perf_counter_ns()-start
                    rows.append(dict(seed=seed,scenario=scenario,step=step,strategy=strategy,
                                     elapsed_ns=elapsed,recomputed=stats.recomputed))
                if old.cache!=new.cache or new.cache!=new.full():
                    raise AssertionError(f'differential mismatch: {seed=} {scenario=} {step=}')
    with open(args.out,'w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=rows[0].keys());writer.writeheader();writer.writerows(rows)
    summary={}
    for scenario in ('rule_edit','version_bump'):
        summary[scenario]={}
        for strategy in ('igv_v08','indexed_v09'):
            vals=[r for r in rows if r['scenario']==scenario and r['strategy']==strategy]
            summary[scenario][strategy]={'mean_ms':round(statistics.mean(r['elapsed_ns'] for r in vals)/1e6,4),
                                         'mean_recomputed':round(statistics.mean(r['recomputed'] for r in vals),2)}
        a=summary[scenario]['igv_v08']['mean_ms'];b=summary[scenario]['indexed_v09']['mean_ms']
        summary[scenario]['speedup_means']=round(a/b,3) if b else None
    result={'settings':vars(args),'all_equal':True,'samples_per_strategy':args.seeds*args.changes*2,
            'summary':summary,'cautions':['Synthetic symbolic workload; same-version rule edits are not version-bump revocations',
                                           'Times include update/invalidation, not initial index construction',
                                           'Benchmark not a proof of semantic certificate validity']}
    Path(args.summary).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--nodes',type=int,default=1000)
    p.add_argument('--rules',type=int,default=40)
    p.add_argument('--discharge-rate',type=float,default=.6)
    p.add_argument('--changes',type=int,default=30)
    p.add_argument('--seeds',type=int,default=5)
    p.add_argument('--out',default='benchmark_v09.csv')
    p.add_argument('--summary',default='benchmark_v09_summary.json')
    args=p.parse_args()
    if args.nodes<2 or args.rules<2 or args.seeds<1 or args.changes<1 or not 0<=args.discharge_rate<=1:p.error('invalid settings')
    run(args)
