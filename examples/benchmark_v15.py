"""Paired benchmark with independent V0.14 oracle validation (not timed)."""
import argparse,csv,json,random,statistics,time
from dataclasses import replace
from datamesh_formal.evidence import DataNode,row
from datamesh_formal.lifecycle import Mode,Requirement,Attestation,recompute
from datamesh_formal.lifecycle_incremental import LifecycleIGV

def build(n):
    nodes=[DataNode('source',records=(row(key='1',patient_id='secret'),))]
    nodes.append(DataNode('p1',('source',),'project','key',(row(key='1'),)))
    for i in range(2,n):
        nodes.append(DataNode(f'p{i}',(f'p{i-1}',),'project','key',(row(key='1'),)))
    req=Requirement('health','redact','source',Mode.PERSISTENT,'absent:patient_id')
    return nodes,req

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--nodes',type=int,default=300)
    p.add_argument('--changes',type=int,default=40)
    p.add_argument('--seeds',type=int,default=5)
    p.add_argument('--out',default='benchmark_v15.csv')
    p.add_argument('--summary',default='benchmark_v15_summary.json')
    a=p.parse_args()
    if a.nodes<3:raise ValueError('nodes >= 3')
    rows=[]
    for seed in range(a.seeds):
        nodes,req=build(a.nodes)
        once=Requirement('health','audit','source',Mode.ONCE,'action:audited')
        mesh=LifecycleIGV(nodes,[req,once],boundaries={f'p{a.nodes-1}'})
        for edit in range(a.changes):
            kind=['rule','attestation','source','noop'][edit%4]
            update={}
            if kind=='rule':
                update['requirements']=[once] if req in mesh.requirements else [req,once]
            elif kind=='attestation':
                update['attestations']=[] if mesh.attestations else [Attestation(once,'p1','audited')]
            elif kind=='source':
                src=mesh.nodes['source']
                update['nodes']=[replace(src,records=(row(key='1'),) if 'patient_id' in dict(src.records[0]) else (row(key='1',patient_id='secret'),))]
            start=time.perf_counter_ns();stats=mesh.update(**update);igv_ms=(time.perf_counter_ns()-start)/1e6
            start=time.perf_counter_ns();full=recompute(list(mesh.nodes.values()),list(mesh.requirements),list(mesh.attestations),mesh.boundaries);full_ms=(time.perf_counter_ns()-start)/1e6
            if mesh.decisions()!=full:raise AssertionError(f'failure {seed=} {edit=}')
            rows.append(dict(seed=seed,edit=edit,kind=kind,igv_ms=igv_ms,full_ms=full_ms,
                             recomputed=stats['recomputed'],equal=True))
    with open(a.out,'w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
    groups={}
    for kind in ('rule','attestation','source','noop'):
        group=[r for r in rows if r['kind']==kind]
        groups[kind]={key:statistics.mean(r[key] for r in group) for key in ('igv_ms','full_ms','recomputed')}
    summary={'settings':vars(a),'all_equal':True,'edits':len(rows),'groups':groups,
             'cautions':['fixed chain; synthetic records','timing is sequential, no true order randomization',
                         'initialization excluded','oracle full evaluator timed; no separate validation cost']}
    with open(a.summary,'w',encoding='utf8') as f:json.dump(summary,f,indent=2)
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
