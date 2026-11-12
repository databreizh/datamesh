import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'examples'))
from benchmark_v29 import FEATURES,feature,train,choose,collect,summarize

def test_ablation_features():
    row=dict(nodes=20,rows=100,selectivity=.5,hidden=False)
    assert feature(row,'no_hidden')==(20,100,.5)
    assert feature(row,'constant')==()
    assert 'hidden' not in FEATURES['no_hidden']

def test_training_fallback():
    r=[dict(nodes=20,rows=20,selectivity=1.,hidden=False,igv_ms=2.,sync_ms=1.)]
    model=train(r,'no_size');assert choose(model,r[0],'no_size')=='sync'
    assert choose(model,dict(selectivity=0.,hidden=True),'no_size')=='igv'

def test_paired_same_edit():
    policies={'adaptive_hidden_only':({(False,):'sync'},'hidden_only')}
    rows=collect((6,4,.5,'chain',False),1234,2,policies,repeat=0)
    assert all(r['equal'] for r in rows)
    assert all(r['adaptive_hidden_only_paired_ms']==r['sync_ms'] for r in rows)
    for r in rows:r['heldout_fold']='chain'
    summary=summarize(rows,['adaptive_hidden_only'],bootstrap=20)
    assert 'adaptive_hidden_only' in summary['paired_bootstrap_95pct']
