"""Read existing CSVs without deleting outliers. Output distribution and anomaly audit."""
import argparse,csv,json,math,statistics
from collections import defaultdict
from pathlib import Path

def quantile(values,p):
    if not values:return None
    a=sorted(values);pos=(len(a)-1)*p;lo=int(pos);hi=min(lo+1,len(a)-1)
    return a[lo]+(a[hi]-a[lo])*(pos-lo)

def inspect(path):
    by=defaultdict(list); bad=[];count=0
    with open(path,newline='',encoding='utf-8') as f:
        for r in csv.DictReader(f):
            count+=1
            gen=r.get('generator','training');top=r.get('topology','unknown')
            for method in ('igv','sync','cost_policy'):
                k=method+'_ms'
                if not r.get(k):continue
                try:v=float(r[k])
                except ValueError:bad.append({'row':count,'column':k,'value':r[k]});continue
                if not math.isfinite(v) or v<0:bad.append({'row':count,'column':k,'value':r[k]});continue
                by[(gen,top,method)].append((v,count,r.get('seed'),r.get('repeat'),r.get('edit')))
    report={'file':str(path),'rows':count,'invalid':bad[:100],'groups':{}}
    for (gen,top,method),vals in sorted(by.items()):
        times=[x[0] for x in vals]; med=statistics.median(times);p99=quantile(times,.99);p999=quantile(times,.999)
        # Flag only; never trim or substitute observations.
        flagged=[dict(ms=round(v,4),csv_row=i+1,seed=seed,repeat=rep,edit=edit) for v,i,seed,rep,edit in vals if v>max(1000,100*med)]
        report['groups'][f'{gen}/{top}/{method}']={'n':len(times),'median_ms':med,'p95_ms':quantile(times,.95),'p99_ms':p99,'p999_ms':p999,'max_ms':max(times),'sum_ms':sum(times),'extreme_flag_count':len(flagged),'extreme_examples':sorted(flagged,key=lambda x:-x['ms'])[:12]}
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('csv',nargs='+');p.add_argument('--out',default='v32_1_diagnostics.json');args=p.parse_args()
    data=[inspect(x) for x in args.csv];Path(args.out).write_text(json.dumps(data,indent=2),encoding='utf8')
    print('Wrote',args.out)
    for f in data:
        print(f['file'],f['rows'],'rows')
        for key,g in f['groups'].items():
            if g['extreme_flag_count']:print('FLAG',key,'count',g['extreme_flag_count'],'max_ms',round(g['max_ms'],2))
