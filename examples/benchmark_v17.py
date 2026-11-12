"""V0.17 true materialization + governance end-to-end benchmark.

Independent full pipeline re-evaluates all operators and verifies with V0.14.
IGV variants rematerialize affected outputs before lifecycle verification.
"""
import argparse,csv,json,random,statistics,time
from dataclasses import replace
from pathlib import Path
from datamesh_formal.evidence import DataNode,row
from datamesh_formal.lifecycle import Mode,Requirement,Attestation,recompute
from datamesh_formal.materialized import MaterializedMesh,full_refresh

METHODS=('full','igv','descendants','no_policy_index')
KINDS=('source_visible','source_hidden','local_rule','shared_rule','attestation','noop')

def build(n,topology):
    nodes=[]
    for i in range(n):
        name=f'p{i}'
        if i==0 or (topology!='chain' and i%20==0):
            nodes.append(DataNode(name,records=(row(key='1', patient_id='P', secret='a') if i==0 else row(key='1',secret='a'),)))
            continue
        if topology=='chain': parents=(f'p{i-1}',)
        elif topology=='tree':parents=(f'p{(i-1)//2}',)
        elif topology=='dag':parents=(f'p{i-1}',f'p{max(0,i-3)}') if i>=3 and i%3==0 else (f'p{i-1}',)
        elif topology=='layered':
            prev=i-10 if i>=10 else 0
            parents=(f'p{prev}',f'p{max(0,i-11)}') if i>=12 and i%5==0 and prev!=max(0,i-11) else (f'p{prev}',)
        else:raise ValueError('unknown topology')
        if len(parents)==2: nodes.append(DataNode(name,parents,'join','key',()))
        else: nodes.append(DataNode(name,parents,'project','key',()))
    return full_refresh(nodes)

def run(args):
    results=[]
    for topology in args.topologies.split(','):
      for seed in range(args.seeds):
        rng=random.Random(seed+919)
        nodes=build(args.nodes,topology)
        roots=[n.id for n in nodes if not n.inputs]
        global_req=Requirement('health','redact',roots[0],Mode.PERSISTENT,'absent:patient_id')
        local_req=Requirement('research','local',roots[-1],Mode.PERSISTENT,'absent:local_secret')
        once=Requirement('health','audit',roots[0],Mode.ONCE,'action:audited')
        reqs=[global_req,local_req,once]; acts=[]; boundaries=frozenset({nodes[-1].id})
        engines={m:MaterializedMesh(nodes,reqs,boundaries=boundaries) for m in METHODS if m!='full'}
        current={n.id:n for n in nodes}
        sources=roots
        for edit in range(args.changes):
            kind=KINDS[edit%len(KINDS)]
            patch={}
            source_id='p0' if args.positions=='root' else rng.choice(sources)
            if kind in ('source_visible','source_hidden'):
                old=current[source_id]
                data=dict(old.records[0]);data['secret']=str(edit)
                if kind=='source_visible':
                    data['key']=str(1+(edit%2))
                new=replace(old,records=(row(**data),))
                current[source_id]=new
                patch['sources']=[new]
            elif kind in ('local_rule','shared_rule'):
                req=local_req if kind=='local_rule' else global_req
                reqs=[r for r in reqs if r!=req] if req in reqs else [*reqs,req]
                patch['requirements']=reqs
            elif kind=='attestation':
                acts=[] if acts else [Attestation(once,'p1','audited')]
                patch['attestations']=acts
            order=list(METHODS);rng.shuffle(order)
            times={};counts={};materialized={};decisions=None
            for method in order:
                start=time.perf_counter_ns()
                if method=='full':
                    full_nodes=full_refresh(list(current.values()))
                    decisions=recompute(full_nodes,reqs,acts,boundaries)
                    counts[method]=args.nodes
                    materialized[method]=args.nodes-len(sources)
                else:
                    stat=engines[method].update(**patch,strategy=method)
                    counts[method]=stat['recomputed']
                    materialized[method]=stat['operators_evaluated']
                times[method]=(time.perf_counter_ns()-start)/1e6
            full_records={n.id:n.records for n in full_nodes}
            for method,mesh in engines.items():
                if mesh.decisions()!=decisions or {n.id:n.records for n in mesh.nodes.values()}!=full_records:
                    raise AssertionError((topology,seed,edit,kind,method))
            for method in METHODS:
                results.append(dict(topology=topology,seed=seed,edit=edit,kind=kind,method=method,
                                    ms=times[method],recomputed=counts[method],operators_evaluated=materialized[method],equal=True))
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',newline='',encoding='utf8') as f:
        writer=csv.DictWriter(f,fieldnames=results[0]);writer.writeheader();writer.writerows(results)
    groups={}
    for t in args.topologies.split(','):
        groups[t]={}
        for k in KINDS:
            groups[t][k]={}
            for m in METHODS:
                sample=[r for r in results if r['topology']==t and r['kind']==k and r['method']==m]
                times=sorted(r['ms'] for r in sample)
                groups[t][k][m]={'mean_ms':statistics.mean(times),'median_ms':statistics.median(times),
                                 'p95_ms':times[min(len(times)-1,int(.95*len(times)))],
                                 'mean_recomputed':statistics.mean(r['recomputed'] for r in sample),
                                 'mean_operators_evaluated':statistics.mean(r['operators_evaluated'] for r in sample)}
    report={'settings':vars(args),'all_equal':True,'edits':len(results)//len(METHODS),'groups':groups,
            'limitations':['Synthetic finite records, no external IO or distributed coordination',
                           'Fixed graph; trusted source data and attestations',
                           'Full baseline includes rematerialization and independent V0.14 verification',
                           'Incremental baselines include rematerialization and V0.15 governance update',
                           'Policy-index ablation seeds all nodes on policy edits',
                           'No-op separated; source positions sampled only from source roots',
                           'No population confidence intervals; timings include Python overhead']}
    Path(args.summary).write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({'all_equal':True,'edits':report['edits'],'summary':args.summary,
                      'means':{t:{k:{m:round(groups[t][k][m]['mean_ms'],3) for m in METHODS} for k in KINDS} for t in groups}},indent=2))

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--nodes',type=int,default=300)
    p.add_argument('--changes',type=int,default=24)
    p.add_argument('--seeds',type=int,default=3)
    p.add_argument('--topologies',default='chain,tree,dag,layered')
    p.add_argument('--positions',choices=('root','random_roots'),default='random_roots')
    p.add_argument('--out',default='benchmark_v17.csv')
    p.add_argument('--summary',default='benchmark_v17_summary.json')
    args=p.parse_args()
    if args.nodes<20 or args.changes<1 or args.seeds<1:raise ValueError('invalid dimensions')
    run(args)
if __name__=='__main__':main()
