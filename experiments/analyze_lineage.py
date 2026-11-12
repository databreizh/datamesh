import csv,random,collections
from pathlib import Path
src=Path(__file__).resolve().parents[1] / 'results' / 'e1_reproduced.csv'
rows=list(csv.DictReader(src.open()))
for r in rows:
 n=int(r['n']); shape=r['topology']; origin_i={'source':0,'middle':n//2,'leaf':n-1}[r['origin']]
 rng=random.Random(1000+int(r['seed']))
 for k in range(int(r['edit'])+1): target=rng.randrange(1,n)
 r['target']=f'n{target}'
 if shape=='chain': in_lineage=target>=origin_i
 elif shape=='fanout': in_lineage=origin_i==0 or target==origin_i
 else:
  j=target
  while j>origin_i: j=(j-1)//2
  in_lineage=j==origin_i
 r['lineage']='in' if in_lineage else 'out'
 r['origin_node']=f'n{origin_i}'
assert len(rows)==2700 and all(r['equal']=='True' for r in rows)
assert all(r['kind']==('boundary','attestation','requirement')[int(r['edit'])%3] for r in rows)
out=Path(__file__).resolve().parents[1] / 'results' / 'e1_lineage_reproduced.csv'
with out.open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
for origin in ['source','middle','leaf']:
 for line in ['in','out']:
  a=[r for r in rows if r['origin']==origin and r['kind']=='attestation' and r['lineage']==line]
  i=sum(int(r['igv_recomputed']) for r in a);d=sum(int(r['desc_recomputed']) for r in a)
  print(origin,line,len(a),i,d,round(100*(d-i)/d,1) if d else '-')
