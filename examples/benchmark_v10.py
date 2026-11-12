"""Compare semantic IGV with full semantic recomputation on identical edits."""
import argparse,csv,json,random,statistics,time
from dataclasses import replace
from datamesh_formal.certified import Node, Obligation
from datamesh_formal.semantic import SemanticPolicy,SemanticRule,SemanticMesh,make_certificate


def run(nodes_count,rule_count,changes,seeds):
    rows=[]
    for seed in range(seeds):
        rng=random.Random(seed)
        obs=[Obligation('domain',f'o{i}') for i in range(rule_count)]
        rules=tuple(SemanticRule(f'r{i}',obs[i],'aggregate',f'f{i}',(f'r{i-1}',) if i>0 and i%5==0 else ()) for i in range(rule_count))
        pol=SemanticPolicy('domain',1,rules)
        nodes=[Node('root',introduced=frozenset(obs))]
        for i in range(1,nodes_count):
            r=rules[i%rule_count]
            parent='root' if i%3 else f'n{i-1}'
            nodes.append(Node(f'n{i}',(parent,),'aggregate',facts=frozenset({r.required_fact}),certificates=(make_certificate(pol,r.rule_id),)))
        m=SemanticMesh(nodes,[pol]);m.initialize()
        for j in range(changes):
            old=m.policies['domain']; typ=['revision','rule_edit','revoke','restore'][j%4]
            if typ=='revision': new=replace(old,revision=old.revision+1)
            elif typ=='rule_edit':
                ix=rng.randrange(rule_count);rr=list(old.rules);r=rr[ix]
                rr[ix]=replace(r,required_fact=f'f{ix}' if r.required_fact.endswith('_changed') else f'f{ix}_changed')
                new=replace(old,rules=tuple(rr),revision=old.revision+1)
            elif typ=='revoke': new=replace(old,revoked=frozenset({f'r{rng.randrange(rule_count)}'}),revision=old.revision+1)
            else: new=replace(old,revoked=frozenset(),revision=old.revision+1)
            start=time.perf_counter_ns();stats=m.update(policy_changes={'domain':new});igv_ms=(time.perf_counter_ns()-start)/1e6
            start=time.perf_counter_ns();reference=m.full();full_ms=(time.perf_counter_ns()-start)/1e6
            assert m.cache==reference,(seed,j)
            rows.append(dict(seed=seed,step=j,change_type=typ,igv_ms=igv_ms,full_ms=full_ms,recomputed=stats.recomputed,all_equal=True))
    return rows


def main():
    p=argparse.ArgumentParser();p.add_argument('--nodes',type=int,default=500);p.add_argument('--rules',type=int,default=40);p.add_argument('--changes',type=int,default=40);p.add_argument('--seeds',type=int,default=5);p.add_argument('--out',default='benchmark_v10.csv');p.add_argument('--summary',default='benchmark_v10_summary.json');a=p.parse_args()
    if min(a.nodes,a.rules,a.changes,a.seeds)<1: p.error('all numeric parameters must be positive')
    rows=run(a.nodes,a.rules,a.changes,a.seeds)
    with open(a.out,'w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    groups={}
    for typ in sorted({r['change_type'] for r in rows}):
        part=[r for r in rows if r['change_type']==typ]
        groups[typ]={'edits':len(part),'igv_mean_ms':statistics.mean(r['igv_ms'] for r in part),
                     'full_mean_ms':statistics.mean(r['full_ms'] for r in part),
                     'mean_recomputed':statistics.mean(r['recomputed'] for r in part)}
        groups[typ]['speedup_full_vs_igv']=groups[typ]['full_mean_ms']/groups[typ]['igv_mean_ms'] if groups[typ]['igv_mean_ms'] else None
    summary={'settings':vars(a),'all_equal':all(r['all_equal'] for r in rows),'edits':len(rows),'groups':groups,
             'cautions':['Symbolic facts are self-asserted','Same-process sequential timing is order-biased',
                         'Not a direct speed comparison with V0.9: certificate semantics differ',
                         'Index initialization is excluded from timing']}
    with open(a.summary,'w',encoding='utf-8') as f:json.dump(summary,f,indent=2)
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
