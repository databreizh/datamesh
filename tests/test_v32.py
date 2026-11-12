import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'examples'))
from benchmark_v32 import train_costs,choose,case,summary
from benchmark_v31 import bucket

def example(ratio):
    return dict(nodes=8,rows=8,selectivity=.5,topology='chain',hidden=False,
                seed=1,repeat=0,igv_ms=10.,sync_ms=10.*ratio,bucket=(1.,0.,1.,0.))

def test_cost_model_safe_margin():
    model=train_costs([dict(example(.5),seed=i) for i in range(9)],min_support=8)
    assert model[(1.,0.,1.,0.)]['choice']=='sync'

def test_cost_model_rejects_costly():
    model=train_costs([dict(example(1.2),seed=i) for i in range(9)],min_support=8)
    assert model[(1.,0.,1.,0.)]['choice']=='igv'

def test_cost_model_rejects_unsupported():
    model=train_costs([example(.5)],min_support=8)
    assert model[(1.,0.,1.,0.)]['choice']=='igv'

def test_unseen_bucket_fallback():
    f=dict(reachable_ratio=1,visible_ratio=0,volume_ratio=1,branching_ratio=0)
    assert choose(f,{})[:2]==('igv','unseen_bucket')

def test_regular_irregular_semantics_and_overhead():
    model={}
    for irregular in (False,True):
        rows=case((8,8,.5,'chain',False),1,2,1,model,irregular)
        assert all(r['equal'] and r['cost_policy_choice']=='igv' for r in rows)
        assert all(r['cost_policy_end_to_end_ms']>=r['cost_policy_ms'] for r in rows)
        assert summary(rows)['sync_choices']==0
