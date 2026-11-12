"""Reproducible descriptive audit of the V0.17 CSV; no population inference."""
import argparse,csv,statistics,json,collections
from pathlib import Path

def audit(path):
    rows=list(csv.DictReader(open(path,newline='',encoding='utf-8')))
    by=collections.defaultdict(dict)
    for r in rows:
        key=(r['topology'],r['seed'],r['edit'],r['kind'])
        if r['method'] in by[key]:raise ValueError('duplicate method row')
        by[key][r['method']]=r
    methods={'full','igv','descendants','no_policy_index'}
    if any(set(v)!=methods for v in by.values()):raise ValueError('incomplete method quartet')
    if any(r['equal'].lower()!='true' for r in rows):raise ValueError('non-equivalent output')
    groups=collections.defaultdict(list)
    for key,v in by.items():
        top,seed,edit,kind=key
        groups[(top,kind)].append(v)
        groups[('ALL',kind)].append(v)
    result={'rows':len(rows),'edits':len(by),'all_equal':True,'groups':{}}
    for (top,kind),edits in sorted(groups.items()):
        full=[float(e['full']['ms']) for e in edits]
        igv=[float(e['igv']['ms']) for e in edits]
        result['groups'][f'{top}/{kind}']={'n':len(edits),
            'full_mean_ms':statistics.mean(full),'igv_mean_ms':statistics.mean(igv),
            'ratio_of_means':statistics.mean(full)/statistics.mean(igv),
            'igv_recomputed_mean':statistics.mean(float(e['igv']['recomputed']) for e in edits),
            'igv_operators_mean':statistics.mean(float(e['igv']['operators_evaluated']) for e in edits)}
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('csv');p.add_argument('--out',default='audit_v18.json')
    args=p.parse_args();result=audit(args.csv)
    Path(args.out).write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(f"Validated {result['edits']} paired edits; {result['rows']} measurements; all equal")
