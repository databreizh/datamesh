"""V0.16 multi-topology, paired, independently checked benchmark.

All methods see the identical edit sequence. Timed order is randomized per edit.
Full reference recompute is an independently implemented oracle, not an optimized baseline.
"""
import argparse,csv,json,random,statistics,time
from dataclasses import replace
from pathlib import Path
from datamesh_formal.evidence import DataNode,row
from datamesh_formal.lifecycle import Mode,Requirement,Attestation,recompute
from datamesh_formal.lifecycle_incremental import LifecycleIGV

METHODS=('full','igv','descendants','no_policy_index')
KINDS=('source','local_rule','shared_rule','attestation','noop')

def build(n,topology):
    if n<12: raise ValueError('nodes must be >=12')
    nodes=[]
    # Each non-source has 1 or 2 parents; two-parent nodes use key joins.
    for i in range(n):
        name=f'p{i}'
        if i==0 or (topology!='chain' and i%20==0):
            nodes.append(DataNode(name,records=(row(key='1',patient_id='secret') if i==0 else row(key='1'),)))
            continue
        if topology=='chain': parents=(f'p{i-1}',)
        elif topology=='tree': parents=(f'p{(i-1)//2}',)
        elif topology=='dag': parents=(f'p{i-1}',f'p{max(0,i-3)}') if i>=3 and i%3==0 else (f'p{i-1}',)
        elif topology=='layered':
            # Multiple independent width-10 lanes, with occasional cross-lane joins.
            prev=i-10 if i>=10 else 0
            parents=(f'p{prev}',f'p{max(0,i-11)}') if i>=12 and i%5==0 and prev!=max(0,i-11) else (f'p{prev}',)
        else:raise ValueError('topology')
        if len(parents)==2:
            nodes.append(DataNode(name,parents,'join','key',(row(key='1'),)))
        else:
            nodes.append(DataNode(name,parents,'project','key',(row(key='1'),)))
    # Some DAG parents can have patient_id at a join; only node 0 has it, and node 0
    # cannot be directly joined with another source in our chosen construction.
    # Construct correct asserted records from parent outputs rather than assuming.
    for i,nod in enumerate(nodes):
        if nod.operation=='join':
            left,right=(dict(nodes[int(p[1:])].records[0]) for p in nod.inputs)
            if any(k in left and left[k]!=v for k,v in right.items()): records=()
            else:records=(row(**{**left,**right}),)
            nodes[i]=replace(nod,records=records)
        elif nod.operation=='project':
            parent=nodes[int(nod.inputs[0][1:])]
            nodes[i]=replace(nod,records=tuple(row(key=dict(r)['key']) for r in parent.records if 'key' in dict(r)))
    return nodes

def scenario(nodes):
    roots=[n.id for n in nodes if not n.inputs]
    global_req=Requirement('health','global_redact',roots[0],Mode.PERSISTENT,'absent:patient_id')
    local_origin=roots[-1]
    local_req=Requirement('research','local_redact',local_origin,Mode.PERSISTENT,'absent:local_secret')
    once=Requirement('health','audit',roots[0],Mode.ONCE,'action:audited')
    return global_req,local_req,once

def summary_stats(vals):
    if not vals:return {}
    return {'mean_ms':statistics.mean(vals),'median_ms':statistics.median(vals),
            'p95_ms':sorted(vals)[min(len(vals)-1,int(.95*len(vals)))],
            'min_ms':min(vals),'max_ms':max(vals)}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--nodes',type=int,default=300)
    p.add_argument('--changes',type=int,default=40)
    p.add_argument('--seeds',type=int,default=5)
    p.add_argument('--topologies',default='chain,tree,dag,layered')
    p.add_argument('--out',default='benchmark_v16.csv')
    p.add_argument('--summary',default='benchmark_v16_summary.json')
    args=p.parse_args()
    rows=[]
    for topology in args.topologies.split(','):
      for seed in range(args.seeds):
        rng=random.Random(seed+101)
        nodes=build(args.nodes,topology)
        global_req,local_req,once=scenario(nodes)
        requirements=[global_req,local_req,once]
        boundaries={nodes[-1].id}
        meshes={method:LifecycleIGV(nodes,requirements,boundaries=boundaries) for method in METHODS if method!='full'}
        current={n.id:n for n in nodes}
        acts=[]
        for edit in range(args.changes):
            kind=KINDS[edit%len(KINDS)]
            patch={}
            if kind=='source':
                old=current['p0']; has='patient_id' in dict(old.records[0])
                new=replace(old,records=(row(key='1') if has else row(key='1',patient_id='secret'),))
                current['p0']=new;patch={'nodes':[new]}
            elif kind in ('local_rule','shared_rule'):
                req=local_req if kind=='local_rule' else global_req
                requirements=[r for r in requirements if r!=req] if req in requirements else [*requirements,req]
                patch={'requirements':requirements}
            elif kind=='attestation':
                acts=[] if acts else [Attestation(once,'p1','audited')]
                patch={'attestations':acts}
            order=list(METHODS);rng.shuffle(order)
            timings={};recomputes={}
            full_decisions=None
            for method in order:
                if method=='full':
                    start=time.perf_counter_ns()
                    full_decisions=recompute(list(current.values()),requirements,acts,frozenset(boundaries))
                    timings[method]=(time.perf_counter_ns()-start)/1e6
                    recomputes[method]=args.nodes
                else:
                    start=time.perf_counter_ns()
                    stats=meshes[method].update(**patch,strategy=method)
                    timings[method]=(time.perf_counter_ns()-start)/1e6
                    recomputes[method]=stats['recomputed']
            for method in meshes:
                if meshes[method].decisions()!=full_decisions:
                    raise AssertionError(f'mismatch {topology=} {seed=} {edit=} {kind=} {method=}')
            for method in METHODS:
                rows.append(dict(topology=topology,seed=seed,edit=edit,kind=kind,
                                 method=method,ms=timings[method],recomputed=recomputes[method],equal=True))
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
    groups={}
    for topology in args.topologies.split(','):
        groups[topology]={}
        for kind in KINDS:
            groups[topology][kind]={}
            for method in METHODS:
                group=[r for r in rows if r['topology']==topology and r['kind']==kind and r['method']==method]
                groups[topology][kind][method]={**summary_stats([r['ms'] for r in group]),
                                                 'mean_recomputed':statistics.mean(r['recomputed'] for r in group)}
    report={'settings':vars(args),'all_equal':True,'edits':len(rows)//len(METHODS),
            'groups':groups,'limitations':[
                'Synthetic finite records; asserted non-source records are checked, not regenerated on edits',
                'Topology fixed; no real I/O, distributed coordination or external policies',
                'Initial construction and verification comparisons excluded from timed methods',
                'Full reference evaluator is independent, but not a separately optimized FULL engine',
                'No-policy-index ablation conservatively seeds all nodes for policy/attestation edits',
                'No-op is a separately reported category; random order per edit, not controlled for warmup',
                'Timings are raw wall-clock measurements; do not interpret as population CIs']}
    with Path(args.summary).open('w',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(json.dumps({'settings':vars(args),'all_equal':True,'edits':report['edits'],
                      'overview':{t:{k:{m:round(groups[t][k][m]['mean_ms'],4) for m in METHODS}
                                      for k in KINDS} for t in groups}},indent=2))
if __name__=='__main__':main()
