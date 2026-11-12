import sys,csv,time,random
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'src'))
from datamesh_formal.evidence import DataNode,row
from datamesh_formal.lifecycle import Requirement,Mode,Attestation,recompute
from datamesh_formal.lifecycle_incremental import LifecycleIGV

def nodes_for(n,shape):
    nodes=[DataNode('n0',records=(row(key='1',email='x'),))]
    for i in range(1,n):
        p=i-1 if shape=='chain' else 0 if shape=='fanout' else (i-1)//2
        nodes.append(DataNode(f'n{i}',(f'n{p}',),'project','key',(row(key='1'),)))
    return nodes
rows=[]
for n in (20,100):
 for shape in ('chain','fanout','layered'):
  nodes=nodes_for(n,shape)
  for origin_label,origin_i in [('source',0),('middle',n//2),('leaf',n-1)]:
   for seed in range(5):
    rng=random.Random(1000+seed)
    req=Requirement('domain','review',f'n{origin_i}',Mode.ONCE,'action:reviewed')
    a=LifecycleIGV(nodes,[req],boundaries={f'n{n-1}'})
    b=LifecycleIGV(nodes,[req],boundaries={f'n{n-1}'})
    for k in range(30):
     typ=('boundary','attestation','requirement')[k%3]
     v=f'n{rng.randrange(1,n)}'
     if typ=='boundary':
      bounds=set(a.boundaries);bounds.symmetric_difference_update({v}); kw={'boundaries':frozenset(bounds)}
     elif typ=='attestation':
      at=Attestation(req,v,'reviewed');acts=set(a.attestations);acts.symmetric_difference_update({at});kw={'attestations':tuple(acts)}
     else:
      kw={'requirements':[] if a.requirements else [req]}
     x=a.update(strategy='igv',**kw)
     y=b.update(strategy='descendants',**kw)
     oracle=recompute(list(a.nodes.values()),list(a.requirements),list(a.attestations),a.boundaries)
     ok=a.cache==b.cache and a.decisions()==oracle and b.decisions()==oracle
     rows.append(dict(n=n,topology=shape,origin=origin_label,seed=seed,edit=k,kind=typ,igv_recomputed=x['recomputed'],desc_recomputed=y['recomputed'],equal=ok))
p=Path(str(Path(__file__).resolve().parents[1] / 'results' / 'e1_reproduced.csv'))
with p.open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
print('ROWS',len(rows),'PASS',sum(r['equal'] for r in rows))
for origin in ('source','middle','leaf'):
 for kind in ('boundary','attestation','requirement'):
  rs=[r for r in rows if r['origin']==origin and r['kind']==kind]
  i=sum(r['igv_recomputed'] for r in rs);d=sum(r['desc_recomputed'] for r in rs)
  print(origin,kind,len(rs),i,d,round(100*(1-i/d),2) if d else 0)
