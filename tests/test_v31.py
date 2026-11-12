import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'examples'))
from benchmark_v31 import graph_features,fit,decision,signature,run_case
from benchmark_v23 import build_instance
from benchmark_v30 import irregular_instance

def test_irregular_signature_ood():
    reg,rr,rb=build_instance(20,20,.5,False,'chain',1)
    irr,ir,ib=irregular_instance(20,20,.5,False,'chain',1)
    a=graph_features(reg,rr,rb);b=graph_features(irr,ir,ib)
    assert signature(a)!=signature(b)
    assert decision(b,({}, {signature(a)}),True)==('igv','ood')

def test_training_never_sync_without_support():
    row=dict(bucket=(1,1,1,1),signature=(1,),igv_ms=2.,sync_ms=1.)
    model=fit([row],min_support=2)
    assert model[0][row['bucket']]=='igv'

def test_actual_oracle_and_fallback():
    reg,rr,rb=build_instance(8,8,.5,False,'chain',1)
    f=graph_features(reg,rr,rb)
    model=({},{signature(f)})
    rows=run_case((8,8,.5,'chain',False),2,2,1,model,True)
    assert all(r['equal'] and r['safe_choice']=='igv' for r in rows)
